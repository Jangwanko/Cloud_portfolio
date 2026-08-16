from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
from typing import Any

import pytest

from ops_agent.collectors.application import collect_application
from ops_agent.collectors import application as application_collector
from ops_agent.collectors.argocd import collect_argocd
from ops_agent.collectors.kubernetes import (
    collect_kubernetes,
    get_current_context,
    resolve_kubectl_path,
    run_kubectl_get_json,
)


COLLECTED_AT = "2026-08-12T01:02:03Z"
KUBECTL = "kubectl.exe"


def _deployment(*, include_unavailable: bool = True) -> dict[str, Any]:
    status: dict[str, Any] = {
        "observedGeneration": 7,
        "replicas": 4,
        "updatedReplicas": 2,
        "readyReplicas": 2,
        "availableReplicas": 2,
        "conditions": [
            {
                "type": "Progressing",
                "status": "True",
                "reason": "ReplicaSetUpdated",
                "lastTransitionTime": "2026-08-12T01:01:30Z",
            }
        ],
    }
    if include_unavailable:
        status["unavailableReplicas"] = 2
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "worker",
            "namespace": "messaging-app",
            "uid": "deployment-uid",
            "resourceVersion": "100",
            "generation": 7,
        },
        "spec": {
            "replicas": 4,
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "worker",
                            "image": "ghcr.io/example/app:abc123",
                        }
                    ]
                }
            },
        },
        "status": status,
    }


def _pods() -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {
                "metadata": {
                    "name": "worker-abc",
                    "namespace": "messaging-app",
                    "uid": "pod-uid",
                    "resourceVersion": "101",
                },
                "status": {
                    "phase": "Running",
                    "startTime": "2026-08-12T01:00:00Z",
                    "conditions": [
                        {
                            "type": "Ready",
                            "status": "True",
                            "lastTransitionTime": "2026-08-12T01:00:20Z",
                        }
                    ],
                    "containerStatuses": [
                        {
                            "name": "worker",
                            "ready": True,
                            "started": True,
                            "restartCount": 1,
                            "image": "ghcr.io/example/app:abc123",
                            "imageID": "ghcr.io/example/app@sha256:deadbeef",
                            "state": {
                                "running": {"startedAt": "2026-08-12T01:00:10Z"}
                            },
                            "lastState": {
                                "terminated": {
                                    "reason": "OOMKilled",
                                    "exitCode": 137,
                                    "signal": 0,
                                    "startedAt": "2026-08-12T00:59:00Z",
                                    "finishedAt": "2026-08-12T00:59:30Z",
                                }
                            },
                        }
                    ],
                },
            }
        ],
    }


def _scaled_object() -> dict[str, Any]:
    return {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "ScaledObject",
        "metadata": {
            "name": "worker-keda",
            "namespace": "messaging-app",
            "resourceVersion": "102",
            "generation": 3,
        },
        "spec": {
            "scaleTargetRef": {"name": "worker"},
            "pollingInterval": 5,
            "cooldownPeriod": 120,
            "minReplicaCount": 2,
            "maxReplicaCount": 4,
            "triggers": [
                {
                    "type": "kafka",
                    "metadata": {
                        "bootstrapServers": "not-copied-to-normalized-evidence",
                        "topic": "message-ingress",
                    },
                }
            ],
        },
        "status": {
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True",
                    "reason": "ScaledObjectReady",
                    "lastTransitionTime": "2026-08-12T01:00:00Z",
                },
                {
                    "type": "Active",
                    "status": "True",
                    "reason": "ScalerActive",
                    "lastTransitionTime": "2026-08-12T01:01:00Z",
                },
            ],
            "hpaName": "worker-keda-hpa",
            "lastActiveTime": "2026-08-12T01:01:50Z",
            "originalReplicaCount": 2,
            "health": {"s0-kafka": {"status": "Happy", "numberOfFailures": 0}},
        },
    }


class FakeKubectl:
    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        self.documents = documents
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        resource = command[command.index("get") + 1]
        document = self.documents[resource]
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(document),
            stderr="",
        )


def test_application_preserves_http_status_and_body_state_separately() -> None:
    def fake_http_get(url: str, timeout_seconds: float) -> tuple[int, bytes]:
        assert timeout_seconds == 2.0
        if url.endswith("/health/ready"):
            return 503, json.dumps(
                {
                    "status": "not_ready",
                    "reason": ["kafka_unreachable"],
                    "postgres": {"primary_reachable": True},
                }
            ).encode()
        return 200, json.dumps(
            {
                "app_version": "2.1.0",
                "worker": {"source": "prometheus", "desired_replicas": 4},
            }
        ).encode()

    result = collect_application(
        "http://127.0.0.1",
        timeout_seconds=2.0,
        http_get=fake_http_get,
        collected_at=COLLECTED_AT,
    )

    assert result["status"] == "OK"
    readiness = result["data"]["readiness"]
    assert readiness["status"] == "OK"
    assert readiness["http_status"] == 503
    assert readiness["body_status"] == "not_ready"
    assert readiness["body"]["postgres"]["primary_reachable"] is True
    ops_summary = result["data"]["ops_summary"]
    assert ops_summary["http_status"] == 200
    assert ops_summary["body_status"] is None
    assert ops_summary["semantic"] == {
        "type": "application_ops_summary",
        "advisory": True,
        "cache_max_age_seconds": 15,
        "notes": "advisory application summary with an in-process 15 second cache",
    }


def test_application_partial_failure_keeps_the_successful_endpoint() -> None:
    def partially_unreachable(url: str, _timeout: float) -> tuple[int, bytes]:
        if url.endswith("/health/ready"):
            raise ConnectionError("connection refused")
        return 200, b'{"worker":{"source":"unavailable"}}'

    result = collect_application(
        "http://127.0.0.1",
        http_get=partially_unreachable,
        collected_at=COLLECTED_AT,
    )

    assert result["status"] == "ERROR"
    assert result["partial"] is True
    assert result["data"]["readiness"]["http_status"] is None
    assert result["data"]["readiness"]["error"]["type"] == "ConnectionError"
    assert result["data"]["ops_summary"]["status"] == "OK"
    assert "connection refused" not in json.dumps(result)


def test_application_rejects_credentials_in_the_base_url() -> None:
    called = False

    def should_not_run(_url: str, _timeout: float) -> tuple[int, bytes]:
        nonlocal called
        called = True
        return 200, b"{}"

    result = collect_application(
        "http://operator:secret@127.0.0.1",
        http_get=should_not_run,
        collected_at=COLLECTED_AT,
    )

    assert result["status"] == "ERROR"
    assert result["data"] == {}
    assert called is False
    assert "operator" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "base_url",
    (
        "http://127.0.0.1/private",
        "http://127.0.0.1?next=private",
        "http://127.0.0.1#private",
    ),
)
def test_application_rejects_non_root_or_ambiguous_base_urls(base_url: str) -> None:
    called = False

    def should_not_run(_url: str, _timeout: float) -> tuple[int, bytes]:
        nonlocal called
        called = True
        return 200, b"{}"

    result = collect_application(
        base_url,
        http_get=should_not_run,
        collected_at=COLLECTED_AT,
    )

    assert result["status"] == "ERROR"
    assert result["data"] == {}
    assert called is False


def test_application_projects_known_fields_before_normalized_evidence() -> None:
    def response(url: str, _timeout: float) -> tuple[int, bytes]:
        if url.endswith("/health/ready"):
            return 200, b'{"status":"ready","apiKey":"must-not-normalize"}'
        return 200, b'{"worker":{"source":"prometheus"},"AccessKeyId":"nope"}'

    result = collect_application(
        "http://127.0.0.1",
        http_get=response,
        collected_at=COLLECTED_AT,
    )

    assert result["data"]["readiness"]["body"]["status"] == "ready"
    assert "apiKey" not in result["data"]["readiness"]["body"]
    assert "AccessKeyId" not in result["data"]["ops_summary"]["body"]


def test_application_rejects_invalid_reason_shapes_and_opaque_tokens() -> None:
    github_token = "ghp_012345678901234567890123456789012345"
    slack_hook = "https://hooks.slack.com/services/T/B/SECRET"

    def response(url: str, _timeout: float) -> tuple[int, bytes]:
        if url.endswith("/health/ready"):
            return 200, json.dumps(
                {
                    "status": "ready",
                    "reason": {"webhook_url": slack_hook, "value": github_token},
                }
            ).encode()
        return 200, b'{"worker":{"source":"prometheus"}}'

    result = collect_application(
        "http://127.0.0.1",
        http_get=response,
        collected_at=COLLECTED_AT,
    )
    encoded = json.dumps(result)

    assert result["data"]["readiness"]["body"]["reason"] is None
    assert github_token not in encoded
    assert slack_hook not in encoded


def test_application_response_size_and_wall_deadline_are_bounded(monkeypatch) -> None:
    class LargeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            return b"x" * size

    class LargeOpener:
        def open(self, _request, timeout):
            assert timeout == 1
            return LargeResponse()

    monkeypatch.setattr(application_collector, "_HTTP_OPENER", LargeOpener())
    too_large = collect_application(
        "http://127.0.0.1",
        timeout_seconds=1,
        collected_at=COLLECTED_AT,
    )
    assert too_large["data"]["readiness"]["error"]["type"] == (
        "ApplicationCollectionError"
    )

    release = threading.Event()

    def slow_response(_url: str, _timeout: float) -> tuple[int, bytes]:
        release.wait(1)
        return 200, b"{}"

    timed_out = collect_application(
        "http://127.0.0.1",
        timeout_seconds=0.01,
        http_get=slow_response,
        collected_at=COLLECTED_AT,
    )
    release.set()
    assert timed_out["data"]["readiness"]["error"]["type"] == "TimeoutError"
    assert timed_out["data"]["ops_summary"]["error"]["type"] == "TimeoutError"


def test_default_application_http_handler_does_not_follow_redirects() -> None:
    handler = application_collector._NoRedirectHandler()

    assert handler.redirect_request(None, None, 302, "Found", {}, "http://invalid") is None


def test_default_application_http_handler_disables_ambient_proxies() -> None:
    from urllib.request import ProxyHandler

    assert not any(
        isinstance(handler, ProxyHandler)
        for handler in application_collector._HTTP_OPENER.handlers
    )


def test_kubernetes_collects_raw_replica_pod_image_and_keda_observations() -> None:
    runner = FakeKubectl(
        {
            "deployment": _deployment(),
            "pods": _pods(),
            "scaledobject.keda.sh": _scaled_object(),
        }
    )

    result = collect_kubernetes(
        namespace="messaging-app",
        context="kind-messaging-ha",
        kubectl_path=KUBECTL,
        runner=runner,
        collected_at=COLLECTED_AT,
    )

    assert result["status"] == "OK"
    deployment = result["data"]["deployment"]["data"]
    assert deployment["desired_replicas"] == 4
    assert deployment["current_replicas"] == 4
    assert deployment["available_replicas"] == 2
    assert deployment["unavailable_replicas"] == 2
    assert deployment["desired_containers"] == [
        {"name": "worker", "image": "ghcr.io/example/app:abc123"}
    ]

    pod = result["data"]["pods"]["data"][0]
    assert pod["phase"] == "Running"
    assert pod["ready_condition_status"] == "True"
    assert pod["containers"][0]["restart_count"] == 1
    assert pod["containers"][0]["image"] == "ghcr.io/example/app:abc123"
    assert pod["containers"][0]["image_id"].endswith("sha256:deadbeef")
    assert pod["containers"][0]["last_termination"] == {
        "reason": "OOMKilled",
        "exit_code": 137,
        "signal": 0,
        "started_at": "2026-08-12T00:59:00Z",
        "finished_at": "2026-08-12T00:59:30Z",
    }

    scaled_object = result["data"]["scaled_object"]["data"]
    assert scaled_object["scale_target_name"] == "worker"
    assert scaled_object["min_replicas"] == 2
    assert scaled_object["max_replicas"] == 4
    assert scaled_object["conditions"][0]["type"] == "Ready"
    assert scaled_object["trigger_types"] == ["kafka"]
    assert "bootstrapServers" not in json.dumps(scaled_object)
    assert "no incident or grace-period evaluation" in result["semantic"]["notes"]

    assert len(runner.calls) == 3
    for command, kwargs in runner.calls:
        assert kwargs["shell"] is False
        assert kwargs["check"] is False
        assert command[0] == KUBECTL
        assert "get" in command
        assert "--output=json" in command
        assert not {"apply", "delete", "patch", "scale", "rollout", "exec"}.intersection(
            command
        )
        assert "secret" not in command


def test_kubernetes_preserves_missing_replica_field_and_partial_failure() -> None:
    class PartialRunner(FakeKubectl):
        def __call__(
            self, command: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            resource = command[command.index("get") + 1]
            if resource == "pods":
                self.calls.append((command, kwargs))
                return subprocess.CompletedProcess(
                    command,
                    returncode=1,
                    stdout="",
                    stderr="unable to connect to the server",
                )
            return super().__call__(command, **kwargs)

    runner = PartialRunner(
        {
            "deployment": _deployment(include_unavailable=False),
            "scaledobject.keda.sh": _scaled_object(),
        }
    )
    result = collect_kubernetes(
        context="kind-messaging-ha",
        kubectl_path=KUBECTL,
        runner=runner,
        collected_at=COLLECTED_AT,
    )

    assert result["status"] == "ERROR"
    assert result["partial"] is True
    assert result["data"]["pods"]["status"] == "ERROR"
    assert result["data"]["pods"]["data"] is None
    assert result["data"]["deployment"]["status"] == "OK"
    assert result["data"]["deployment"]["data"]["unavailable_replicas"] is None


def test_kubectl_helper_rejects_non_allowlisted_resources_without_running() -> None:
    called = False

    def should_not_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError("must not run")

    try:
        run_kubectl_get_json(
            resource="secret",
            namespace="messaging-app",
            name="messaging-env",
            runner=should_not_run,
        )
    except ValueError as exc:
        assert "not allowlisted" in str(exc)
    else:
        raise AssertionError("secret collection must be rejected")
    assert called is False


def test_windows_kubectl_resolution_and_current_context_are_fixed_reads(
    tmp_path: Path,
) -> None:
    repository_kubectl = tmp_path / "tools" / "kubectl.exe"
    repository_kubectl.parent.mkdir()
    repository_kubectl.write_bytes(b"test executable placeholder")
    assert resolve_kubectl_path(
        repo_root=tmp_path,
        platform_name="nt",
    ) == str(repository_kubectl)
    assert resolve_kubectl_path(
        explicit="C:/approved/kubectl.exe",
        repo_root=tmp_path,
        platform_name="nt",
    ) == "C:/approved/kubectl.exe"

    calls: list[tuple[list[str], dict[str, Any]]] = []

    def context_runner(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout="kind-messaging-ha\n",
            stderr="",
        )

    result = get_current_context(kubectl_path=KUBECTL, runner=context_runner)

    assert result == {"status": "OK", "context": "kind-messaging-ha", "error": None}
    command, kwargs = calls[0]
    assert command == [KUBECTL, "config", "current-context"]
    assert kwargs["shell"] is False
    assert kwargs["check"] is False


def test_windows_kubectl_rejects_batch_launchers_and_unsafe_contexts() -> None:
    with pytest.raises(ValueError, match=r"\.exe"):
        resolve_kubectl_path(
            explicit="C:/tools/kubectl.cmd",
            platform_name="nt",
        )

    called = False

    def should_not_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError("unsafe context must be rejected before execution")

    with pytest.raises(ValueError, match="invalid Kubernetes context"):
        run_kubectl_get_json(
            resource="deployment",
            namespace="messaging-app",
            name="worker",
            context="kind-messaging-ha&whoami",
            kubectl_path=KUBECTL,
            runner=should_not_run,
        )
    assert called is False


def test_argocd_collects_status_without_evaluating_sync_or_health() -> None:
    application = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": "messaging-portfolio-local-ha",
            "namespace": "argocd",
            "resourceVersion": "200",
            "generation": 9,
        },
        "spec": {
            "source": {
                "repoURL": "https://example.invalid/private.git",
                "targetRevision": "master",
                "path": "k8s/gitops/overlays/local-ha",
            },
            "destination": {"namespace": "messaging-app"},
        },
        "status": {
            "sync": {"status": "OutOfSync", "revision": "abc123"},
            "health": {"status": "Progressing"},
            "reconciledAt": "2026-08-12T01:01:55Z",
        },
    }
    runner = FakeKubectl({"applications.argoproj.io": application})

    result = collect_argocd(
        applicable=True,
        context="kind-messaging-ha",
        kubectl_path=KUBECTL,
        runner=runner,
        collected_at=COLLECTED_AT,
    )

    assert result["status"] == "OK"
    assert result["source_timestamp"] == "2026-08-12T01:01:55Z"
    data = result["data"]["application"]
    assert data["sync_status"] == "OutOfSync"
    assert data["health_status"] == "Progressing"
    assert data["revision"] == "abc123"
    assert data["target_revision"] == "master"
    assert "repoURL" not in json.dumps(result)
    command, kwargs = runner.calls[0]
    assert command == [
        KUBECTL,
        "--context=kind-messaging-ha",
        "--namespace=argocd",
        "get",
        "applications.argoproj.io",
        "messaging-portfolio-local-ha",
        "--output=json",
    ]
    assert kwargs["shell"] is False


def test_argocd_not_applicable_performs_no_kubernetes_call() -> None:
    called = False

    def should_not_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError("must not run")

    result = collect_argocd(
        applicable=False,
        runner=should_not_run,
        collected_at=COLLECTED_AT,
    )

    assert result["status"] == "NOT_APPLICABLE"
    assert result["data"] == {
        "applicability": "NOT_APPLICABLE",
        "application": None,
    }
    assert result["error"] is None
    assert called is False


def test_argocd_missing_application_is_not_treated_as_not_applicable() -> None:
    def not_found(
        command: list[str], **_kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            returncode=1,
            stdout="",
            stderr='Error from server (NotFound): applications.argoproj.io "missing" not found',
        )

    result = collect_argocd(
        applicable=True,
        application_name="missing",
        kubectl_path=KUBECTL,
        runner=not_found,
        collected_at=COLLECTED_AT,
    )

    assert result["status"] == "MISSING"
    assert result["data"]["applicability"] == "APPLICABLE"
    assert result["error"]["type"] == "ResourceNotFound"
