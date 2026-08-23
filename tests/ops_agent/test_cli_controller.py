from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from ops_agent import cli, controller
from ops_agent.evaluation_models import ConditionState, EvaluationStatus
from ops_agent.models import CollectionStatus, EvidenceStatus
from ops_agent.policies import load_policy


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "ops_agent"
    / "fixtures"
    / "all_sources_available.json"
)


def _results():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FakePrometheusCollector:
    result = None

    def __init__(self, base_url, **options):
        assert base_url == "http://127.0.0.1/prometheus"
        assert options["host_header"] == "localhost"
        assert options["expected_partition_ids"] == list(range(8))

    def collect(self):
        return deepcopy(self.result)


def test_collect_bundle_connects_all_read_only_collectors(monkeypatch, tmp_path) -> None:
    results = _results()
    FakePrometheusCollector.result = results["prometheus"]
    calls = []
    monkeypatch.setattr(controller, "PrometheusCollector", FakePrometheusCollector)
    monkeypatch.setattr(
        controller,
        "get_current_context",
        lambda **_kwargs: {"status": "OK", "context": "kind-messaging-ha", "error": None},
    )
    monkeypatch.setattr(
        controller,
        "collect_application",
        lambda base_url, **_kwargs: (calls.append(("application", base_url)) or deepcopy(results["application"])),
    )
    monkeypatch.setattr(
        controller,
        "collect_kubernetes",
        lambda **kwargs: (calls.append(("kubernetes", kwargs["context"])) or deepcopy(results["kubernetes"])),
    )
    monkeypatch.setattr(
        controller,
        "collect_argocd",
        lambda **kwargs: (calls.append(("argocd", kwargs["context"])) or deepcopy(results["argocd"])),
    )
    monkeypatch.setattr(controller, "_source_revision", lambda: "source-sha")
    monkeypatch.setattr(controller, "_source_dirty", lambda: False)

    bundle = controller.collect_bundle(
        policy=load_policy("local-ha"),
        incident_id="fixture-incident",
        artifact_root=tmp_path / "raw",
    )

    assert bundle.collection.status == CollectionStatus.COMPLETE
    assert bundle.scope.context == "kind-messaging-ha"
    assert bundle.context.source_sha == "source-sha"
    assert isinstance(bundle.context.source_dirty, bool)
    assert len(bundle.context.collector_tree_sha256) == 64
    endpoint_identities = {
        item.labels["collector_source"]: item
        for item in bundle.evidence
        if item.metric.name == "source_endpoint_identity"
    }
    assert set(endpoint_identities) == {"application", "prometheus"}
    application_identity = endpoint_identities["application"].metric.value
    assert application_identity["base_url"] == "http://127.0.0.1"
    assert application_identity["host_header"] == "localhost"
    assert application_identity["configuration_source"] == "policy"
    assert len(application_identity["identity_sha256"]) == 64
    prometheus_identity = endpoint_identities["prometheus"].metric.value
    assert prometheus_identity["base_url"] == "http://127.0.0.1/prometheus"
    assert prometheus_identity["host_header"] == "localhost"
    assert prometheus_identity["configuration_source"] == "policy"
    assert calls == [
        ("application", "http://127.0.0.1"),
        ("kubernetes", "kind-messaging-ha"),
        ("argocd", "kind-messaging-ha"),
    ]


def test_collect_bundle_records_effective_endpoint_overrides(monkeypatch, tmp_path) -> None:
    results = _results()
    calls = []

    class OverridePrometheusCollector:
        def __init__(self, base_url, **options):
            calls.append(("prometheus", base_url, options["host_header"]))

        def collect(self):
            return deepcopy(results["prometheus"])

    monkeypatch.setattr(controller, "PrometheusCollector", OverridePrometheusCollector)
    monkeypatch.setattr(
        controller,
        "get_current_context",
        lambda **_kwargs: {"status": "OK", "context": "kind-messaging-ha", "error": None},
    )
    monkeypatch.setattr(
        controller,
        "collect_application",
        lambda base_url, **kwargs: (
            calls.append(("application", base_url, kwargs["host_header"]))
            or deepcopy(results["application"])
        ),
    )
    monkeypatch.setattr(
        controller,
        "collect_kubernetes",
        lambda **_kwargs: deepcopy(results["kubernetes"]),
    )
    monkeypatch.setattr(
        controller,
        "collect_argocd",
        lambda **_kwargs: deepcopy(results["argocd"]),
    )

    bundle = controller.collect_bundle(
        policy=load_policy("local-ha"),
        incident_id="override-incident",
        artifact_root=tmp_path / "raw",
        application_url="http://127.0.0.1:18080",
        prometheus_url="http://127.0.0.1:19090/prometheus",
    )

    assert ("application", "http://127.0.0.1:18080", None) in calls
    assert ("prometheus", "http://127.0.0.1:19090/prometheus", None) in calls
    identities = {
        item.labels["collector_source"]: item.metric.value
        for item in bundle.evidence
        if item.metric.name == "source_endpoint_identity"
    }
    assert identities["application"]["base_url"] == "http://127.0.0.1:18080"
    assert identities["prometheus"]["base_url"] == (
        "http://127.0.0.1:19090/prometheus"
    )
    assert identities["application"]["host_header"] is None
    assert identities["prometheus"]["host_header"] is None
    assert identities["application"]["configuration_source"] == "operator_override"
    assert identities["prometheus"]["configuration_source"] == "operator_override"


def test_source_revision_is_resolved_from_the_collector_repository(monkeypatch) -> None:
    calls = []

    class Result:
        returncode = 0
        stdout = "abc123\n"

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    monkeypatch.setattr(controller.subprocess, "run", run)

    assert controller._source_revision() == "abc123"
    command, kwargs = calls[0]
    assert command[:3] == ["git", "-C", str(controller._REPO_ROOT)]
    assert command[3:] == ["rev-parse", "HEAD"]
    assert kwargs["shell"] is False


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (0, "", False),
        (0, " M README.md\n", True),
        (1, "", None),
    ],
)
def test_source_dirty_is_resolved_from_the_collector_repository(
    monkeypatch, returncode, stdout, expected
) -> None:
    calls = []

    class Result:
        pass

    result = Result()
    result.returncode = returncode
    result.stdout = stdout

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return result

    monkeypatch.setattr(controller.subprocess, "run", run)

    assert controller._source_dirty() is expected
    command, kwargs = calls[0]
    assert command[:3] == ["git", "-C", str(controller._REPO_ROOT)]
    assert command[3:] == ["status", "--porcelain=v1", "--untracked-files=all"]
    assert kwargs["shell"] is False


def test_context_failure_skips_argocd_without_default_context_fallback(
    monkeypatch, tmp_path
) -> None:
    results = _results()
    FakePrometheusCollector.result = results["prometheus"]
    monkeypatch.setattr(controller, "PrometheusCollector", FakePrometheusCollector)
    monkeypatch.setattr(
        controller,
        "get_current_context",
        lambda **_kwargs: {
            "status": "ERROR",
            "context": None,
            "error": {"type": "ContextMissing", "message": "no context"},
        },
    )
    monkeypatch.setattr(controller, "collect_application", lambda *_args, **_kwargs: results["application"])
    monkeypatch.setattr(
        controller,
        "collect_kubernetes",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Kubernetes GET must be skipped")),
    )
    monkeypatch.setattr(
        controller,
        "collect_argocd",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Argo GET must be skipped")),
    )

    bundle = controller.collect_bundle(
        policy=load_policy("local-ha"),
        incident_id="fixture-incident",
        artifact_root=tmp_path / "raw",
    )

    assert bundle.scope.context is None
    argocd = next(item for item in bundle.evidence if item.source == "argocd")
    assert argocd.status == EvidenceStatus.ERROR
    assert argocd.error.type == "ContextUnavailable"
    assert bundle.collection.status == CollectionStatus.PARTIAL


def test_cli_collect_writes_atomic_schema_v1_output(monkeypatch, tmp_path, capsys) -> None:
    results = _results()
    bundle = controller.build_bundle_from_results(
        policy=load_policy("local-ha"),
        collector_results=results,
        incident_id="fixture-incident",
        cluster_context="kind-messaging-ha",
        artifact_root=tmp_path / "fixture-raw",
        bundle_id="fixture-bundle",
    )
    monkeypatch.setattr(cli, "collect_bundle", lambda **_kwargs: bundle)
    output = tmp_path / "nested" / "evidence.json"

    exit_code = cli.main(
        [
            "collect",
            "--profile",
            "local-ha",
            "--incident-id",
            "fixture-incident",
            "--output",
            str(output),
        ]
    )

    stdout_value = capsys.readouterr().out.strip()
    file_payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert stdout_value == output.as_posix()
    assert file_payload["schema_version"] == "ops.evidence.v1"
    assert not (output.parent / ".evidence.json.tmp").exists()


def test_cli_evaluate_writes_deterministic_conditions_output(tmp_path, capsys) -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "results"
        / "ops-agent"
        / "live-baseline"
        / "no-backlog-20260812.json"
    )
    output = tmp_path / "nested" / "conditions.json"

    exit_code = cli.main(
        [
            "evaluate",
            "--input",
            str(source),
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == output.as_posix()
    assert payload["schema_version"] == "ops.conditions.v1"
    assert payload["evaluation_status"] == EvaluationStatus.COMPLETE.value
    assert payload["source_bundle"]["collection_status"] == "PARTIAL"
    assert payload["conditions"]["CORE_BACKLOG_PRESSURE"]["state"] == (
        ConditionState.ABSENT.value
    )
    assert payload["assessments"]["NO_BACKLOG_PRESSURE_DETECTED"]["state"] == (
        ConditionState.PRESENT.value
    )
    assert not (output.parent / ".conditions.json.tmp").exists()


def test_cli_evaluate_rejects_oversized_input(monkeypatch, tmp_path) -> None:
    source = tmp_path / "too-large.json"
    source.write_bytes(b"{}")
    monkeypatch.setattr(cli, "_MAX_EVIDENCE_INPUT_BYTES", 1)

    with pytest.raises(ValueError, match="16 MiB"):
        cli.main(["evaluate", "--input", str(source)])


def test_cli_evaluate_does_not_overwrite_source_bundle(tmp_path) -> None:
    source = tmp_path / "evidence.json"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="must not overwrite"):
        cli.main(
            [
                "evaluate",
                "--input",
                str(source),
                "--output",
                str(source),
            ]
        )
    assert source.read_text(encoding="utf-8") == "{}"
