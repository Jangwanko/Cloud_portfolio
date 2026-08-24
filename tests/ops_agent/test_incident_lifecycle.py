from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ops_agent import cli
from ops_agent.diagnosis_models import DiagnosisRun, diagnosis_id
from ops_agent.evaluation_models import (
    AssessmentName,
    AssessmentResult,
    ConditionDependencyTrace,
    ConditionName,
    ConditionResult,
    ConditionState,
    EvaluationStatus,
    canonical_sha256,
)
from ops_agent.incident_lifecycle import (
    attach_diagnosis,
    attach_recovery_evaluation,
    create_incident,
)
from ops_agent.incident_models import (
    IncidentLifecycleState,
    IncidentOutcome,
    IncidentProvenance,
    IncidentRecord,
    ObservationQuality,
)
from ops_agent.recovery_models import (
    RecoveryActivationReference,
    RecoveryCaptureObservation,
    RecoveryCompletion,
    RecoveryCompletionStatus,
    RecoveryEvaluation,
    RecoveryQuality,
    RecoverySourceBundleReference,
    RecoveryState,
    RecoveryWindow,
    recovery_evaluation_id,
)
from ops_agent.recovery_policies import load_recovery_policy
from ops_agent.sequence_models import (
    SequenceCaptureObservation,
    SequenceConditionEvaluation,
    SequenceEvaluationPolicy,
    SequenceSourceBundleReference,
    sequence_evaluation_id,
)


BASE = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)
SOURCE_IDENTITY = "1" * 64


def _condition_result(name: ConditionName, state: ConditionState) -> ConditionResult:
    return ConditionResult(
        condition=name,
        state=state,
        reason_codes=[f"{name.value}_{state.value}"],
        required_evidence=[],
        optional_evidence=[],
    )


def _activation(*, salt: str = "a") -> SequenceConditionEvaluation:
    policy = SequenceEvaluationPolicy()
    source_bundles = []
    observations = []
    for index in range(3):
        digest = canonical_sha256({"salt": salt, "index": index})
        source_bundles.append(
            {
                "sequence_index": index,
                "schema_version": "ops.evidence.v1",
                "bundle_id": f"bundle-{salt}-{index}",
                "incident_id": f"sample-{salt}-{index}",
                "cluster_profile": "local-ha",
                "collection_status": "COMPLETE",
                "collection_started_at": BASE + timedelta(seconds=index * 15),
                "collection_completed_at": BASE + timedelta(seconds=index * 15 + 1),
                "kafka_source_timestamp": BASE + timedelta(seconds=index * 15),
                "source_bundle_sha256": digest,
            }
        )
        observations.append(
            {
                "sequence_index": index,
                "bundle_id": f"bundle-{salt}-{index}",
                "source_bundle_sha256": digest,
                "required_evidence_usable": True,
                "core_single_bundle_state": "UNKNOWN",
                "reason_codes": ["PRESSURE_SEQUENCE_QUALIFIED"],
                "source_identity_sha256": SOURCE_IDENTITY,
                "kafka_source_timestamp": BASE + timedelta(seconds=index * 15),
                "partition_set": [str(value) for value in range(8)],
                "total_lag_records": 8000 + index * 2000,
                "lag_slope_60s_records_per_second": 100.0,
                "produce_rate_60s_records_per_second": 200.0,
                "committed_offset_rate_60s_records_per_second": 100.0,
                "rate_arithmetic_consistent": True,
                "meets_lag_floor": True,
                "meets_slope_floor": True,
            }
        )
    source_bundles = [
        SequenceSourceBundleReference.model_validate(value)
        for value in source_bundles
    ]
    observations = [
        SequenceCaptureObservation.model_validate(value)
        for value in observations
    ]
    conditions = {
        ConditionName.CORE_BACKLOG_PRESSURE: _condition_result(
            ConditionName.CORE_BACKLOG_PRESSURE,
            ConditionState.PRESENT,
        ),
        ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED: _condition_result(
            ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED,
            ConditionState.ABSENT,
        ),
        ConditionName.DB_DEGRADED: _condition_result(
            ConditionName.DB_DEGRADED,
            ConditionState.ABSENT,
        ),
        ConditionName.WORKER_REPLICA_UNAVAILABLE: _condition_result(
            ConditionName.WORKER_REPLICA_UNAVAILABLE,
            ConditionState.ABSENT,
        ),
    }
    assessment = AssessmentResult(
        assessment=AssessmentName.NO_BACKLOG_PRESSURE_DETECTED,
        state=ConditionState.ABSENT,
        reason_codes=["BACKLOG_PRESSURE_PRESENT"],
        condition_dependencies=[
            ConditionDependencyTrace(
                condition=name,
                state=conditions[name].state,
                reason_codes=conditions[name].reason_codes,
            )
            for name in (
                ConditionName.CORE_BACKLOG_PRESSURE,
                ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED,
            )
        ],
    )
    evaluation_id = sequence_evaluation_id(
        evaluator_version="ops.evaluator.v2",
        ruleset_version="ops.conditions.rules.v2",
        policy=policy,
        source_bundles=source_bundles,
        capture_observations=observations,
        conditions=conditions,
        assessments={AssessmentName.NO_BACKLOG_PRESSURE_DETECTED: assessment},
    )
    return SequenceConditionEvaluation.model_validate(
        {
            "evaluation_id": evaluation_id,
            "evaluation_status": EvaluationStatus.COMPLETE,
            "policy": policy,
            "source_bundles": source_bundles,
            "capture_observations": observations,
            "conditions": conditions,
            "assessments": {
                AssessmentName.NO_BACKLOG_PRESSURE_DETECTED: assessment,
            },
        }
    )


def _provenance() -> IncidentProvenance:
    return IncidentProvenance(
        source_sha="abcdef1234567",
        source_tree_sha256="2" * 64,
        runtime_image="ghcr.io/example/cloud_portfolio:test",
        argocd_revision="1234567890abcdef",
    )


def _diagnosis(activation: SequenceConditionEvaluation, *, seconds: int = 50) -> DiagnosisRun:
    policy = {
        "policy_version": "local-ha.diagnosis.v1",
        "required_condition_schema": "ops.conditions.v2",
        "required_condition": "CORE_BACKLOG_PRESSURE",
        "required_state": "PRESENT",
        "tool_registry_version": "ops.diagnosis.tools.v1",
        "agent_version": "ops.diagnosis.agent.v1",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "max_steps": 4,
        "max_tool_calls": 4,
        "max_output_tokens": 1600,
        "request_timeout_seconds": 30.0,
        "max_retries": 1,
        "max_output_repairs": 1,
    }
    hypothesis = {
        "hypothesis": "INSUFFICIENT_EVIDENCE",
        "support_status": "SUPPORTED",
        "reason_codes": ["GROUNDED_ABSTENTION"],
        "supporting_evidence_ids": ["initial.evidence"],
        "conflicting_evidence_ids": [],
        "evidence_gaps": ["CAUSAL_SIGNAL_NOT_AVAILABLE"],
    }
    identity = {
        "condition_evaluation_id": activation.evaluation_id,
        "source_bundle_digests": [
            item.source_bundle_sha256 for item in activation.source_bundles
        ],
        "policy": policy,
        "initial_evidence_ids": ["initial.evidence"],
        "additional_evidence": [],
        "steps": [],
        "hypotheses": [hypothesis],
        "stop_reason": "insufficient_evidence",
    }
    completed_at = BASE + timedelta(seconds=seconds)
    return DiagnosisRun.model_validate(
        {
            "diagnosis_id": diagnosis_id(identity),
            "incident_id": "source-sample-id",
            "condition_evaluation_id": activation.evaluation_id,
            "context": {
                "profile": "local-ha",
                "cluster_context": "kind-messaging-ha",
                "namespace": "messaging-app",
                "topic": "message-ingress",
                "consumer_group": "message-worker",
                "source_bundle_digests": identity["source_bundle_digests"],
            },
            "policy": policy,
            "input_conditions": {"CORE_BACKLOG_PRESSURE": "PRESENT"},
            "initial_evidence_ids": ["initial.evidence"],
            "additional_evidence": [],
            "steps": [],
            "hypotheses": [hypothesis],
            "stop_reason": "insufficient_evidence",
            "steps_used": 0,
            "started_at": completed_at - timedelta(seconds=1),
            "completed_at": completed_at,
            "model_response_ids": ["response-1"],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
                "api_requests": 1,
            },
            "validation": {
                "schema_valid": True,
                "citations_valid": True,
                "tool_calls_valid": True,
                "step_budget_valid": True,
                "stop_valid": True,
                "forbidden_claims_absent": True,
            },
            "output_repairs_used": 0,
            "validation_attempts": [
                {
                    "attempt": 1,
                    "phase": "initial",
                    "result": "VALID",
                    "response_id": "response-1",
                }
            ],
        }
    )


def _recovery(
    incident_id: str,
    activation: SequenceConditionEvaluation,
    *,
    state: RecoveryState,
    seconds: int,
    version: str = "v2",
    salt: str = "r",
) -> RecoveryEvaluation:
    policy = load_recovery_policy(version=version)
    digest = canonical_sha256({"recovery": salt, "seconds": seconds})
    observed_at = BASE + timedelta(seconds=seconds)
    completion = (
        RecoveryCompletionStatus.CALIBRATION_PENDING
        if version == "v1"
        else RecoveryCompletionStatus.COMPLETE
        if state == RecoveryState.WORKER_BACKLOG_RECOVERED
        else RecoveryCompletionStatus.IN_PROGRESS
    )
    status = "PARTIAL" if state == RecoveryState.WORKER_BACKLOG_UNKNOWN else "COMPLETE"
    payload = {
        "schema_version": "ops.recovery.v1",
        "evaluator_version": f"ops.recovery.evaluator.{version}",
        "ruleset_version": f"ops.recovery.rules.{version}",
        "evaluation_status": status,
        "incident_id": incident_id,
        "activation": {
            "schema_version": activation.schema_version,
            "evaluation_id": activation.evaluation_id,
            "evaluator_version": activation.evaluator_version,
            "ruleset_version": activation.ruleset_version,
            "policy_version": activation.policy.policy_version,
            "condition": "CORE_BACKLOG_PRESSURE",
            "condition_state": "PRESENT",
            "source_bundle_digests": [
                item.source_bundle_sha256 for item in activation.source_bundles
            ],
            "last_collection_completed_at": activation.source_bundles[-1].collection_completed_at,
            "last_kafka_source_timestamp": activation.source_bundles[-1].kafka_source_timestamp,
            "source_identity_sha256": SOURCE_IDENTITY,
        },
        "policy": policy.model_dump(mode="json"),
        "state": state.value,
        "reason_codes": [f"TEST_{state.value}"],
        "source_bundles": [
            {
                "sequence_index": 0,
                "bundle_id": f"recovery-{salt}",
                "source_incident_id": "source-recovery-sample",
                "expected_source_bundle_sha256": digest,
                "actual_source_bundle_sha256": digest,
                "digest_matches": True,
                "collection_started_at": observed_at - timedelta(seconds=1),
                "collection_completed_at": observed_at,
                "kafka_source_timestamp": observed_at - timedelta(seconds=1),
            }
        ],
        "observations": [
            {
                "sequence_index": 0,
                "bundle_id": f"recovery-{salt}",
                "source_bundle_sha256": digest,
                "usable": state != RecoveryState.WORKER_BACKLOG_UNKNOWN,
                "issue_codes": (
                    ["INVALID_NEGATIVE_EXPORTER_LAG"]
                    if state == RecoveryState.WORKER_BACKLOG_UNKNOWN
                    else []
                ),
                "condition_source_identity_sha256": SOURCE_IDENTITY,
                "recovery_source_identity_sha256": SOURCE_IDENTITY,
                "kafka_source_timestamp": observed_at - timedelta(seconds=1),
                "partition_set": [str(value) for value in range(8)],
                "total_lag_records": 10,
                "lag_slope_60s_records_per_second": -1.0,
                "produce_rate_60s_records_per_second": 75.0,
                "committed_offset_rate_60s_records_per_second": 76.0,
                "rate_arithmetic_consistent": True,
                "postgres_ready": True,
                "required_evidence_ids": ["kafka.evidence", "postgres.evidence"],
                "negative_exporter_lag": [],
                "derived_lag_evidence_ids": [],
                "state_after_capture": state.value,
                "state_reason_codes": [f"TEST_{state.value}"],
            }
        ],
        "window": {
            "required_capture_count": 3,
            "evaluated_sequence_indexes": [0],
            "matched_recovering_windows": [],
            "first_observed_at": observed_at,
            "last_observed_at": observed_at,
            "capture_count": 1,
        },
        "evidence_ids": ["kafka.evidence", "postgres.evidence"],
        "quality": {
            "required_evidence_names": ["kafka", "postgres"],
            "expected_partition_count": 8,
            "low_lag_evidence_policy": "INVALID_ONLY",
            "exporter_negative_lag_preserved": True,
            "negative_lag_clamped_to_zero": False,
            "derived_lag_created": False,
            "timestamp_coherence_contract": "test coherent timestamps",
            "source_identity_required": True,
        },
        "recovery_completion": {
            "status": completion.value,
            "reason_codes": ["TEST_COMPLETION"],
        },
    }
    payload["activation"] = RecoveryActivationReference.model_validate(
        payload["activation"]
    ).model_dump(mode="json")
    payload["source_bundles"] = [
        RecoverySourceBundleReference.model_validate(value).model_dump(mode="json")
        for value in payload["source_bundles"]
    ]
    payload["observations"] = [
        RecoveryCaptureObservation.model_validate(value).model_dump(mode="json")
        for value in payload["observations"]
    ]
    payload["window"] = RecoveryWindow.model_validate(payload["window"]).model_dump(
        mode="json"
    )
    payload["quality"] = RecoveryQuality.model_validate(payload["quality"]).model_dump(
        mode="json"
    )
    payload["recovery_completion"] = RecoveryCompletion.model_validate(
        payload["recovery_completion"]
    ).model_dump(mode="json")
    payload["recovery_evaluation_id"] = recovery_evaluation_id(payload)
    return RecoveryEvaluation.model_validate(payload)


def _incident() -> tuple[SequenceConditionEvaluation, IncidentRecord]:
    activation = _activation()
    return activation, create_incident(
        activation=activation,
        provenance=_provenance(),
        activation_artifact_ref="results/conditions.json",
    )


def test_activation_creates_deterministic_active_incident() -> None:
    activation = _activation()
    first = create_incident(activation=activation, provenance=_provenance())
    second = create_incident(activation=activation, provenance=_provenance())

    assert first.incident_id == second.incident_id
    assert first.detection.source_incident_id == "sample-a"
    assert first.lifecycle_state == IncidentLifecycleState.ACTIVE
    assert [item.event_type.value for item in first.timeline] == ["DETECTED", "ACTIVE"]


def test_diagnosis_attachment_is_valid_and_idempotent() -> None:
    activation, incident = _incident()
    diagnosis = _diagnosis(activation)

    attached = attach_diagnosis(incident, diagnosis)
    duplicate = attach_diagnosis(attached, diagnosis)

    assert attached == duplicate
    assert attached.lifecycle_state == IncidentLifecycleState.ACTIVE
    assert attached.diagnosis is not None
    assert attached.diagnosis.validation_status == "VALID"


def test_active_recovering_recovered_closes_only_with_v2() -> None:
    activation, incident = _incident()
    incident = attach_diagnosis(incident, _diagnosis(activation))
    active = _recovery(
        incident.detection.source_incident_id,
        activation,
        state=RecoveryState.WORKER_BACKLOG_ACTIVE,
        seconds=60,
        version="v1",
        salt="active",
    )
    recovering = _recovery(
        incident.detection.source_incident_id,
        activation,
        state=RecoveryState.WORKER_BACKLOG_RECOVERING,
        seconds=70,
        version="v2",
        salt="recovering",
    )
    recovered = _recovery(
        incident.detection.source_incident_id,
        activation,
        state=RecoveryState.WORKER_BACKLOG_RECOVERED,
        seconds=80,
        version="v2",
        salt="recovered",
    )

    incident = attach_recovery_evaluation(incident, active)
    assert incident.lifecycle_state == IncidentLifecycleState.ACTIVE
    assert incident.closed_at is None
    incident = attach_recovery_evaluation(incident, recovering)
    assert incident.lifecycle_state == IncidentLifecycleState.RECOVERING
    incident = attach_recovery_evaluation(incident, recovered)

    assert incident.lifecycle_state == IncidentLifecycleState.CLOSED
    assert incident.outcome == IncidentOutcome.RECOVERED
    assert incident.closed_at == BASE + timedelta(seconds=80)


def test_unknown_after_closed_updates_observation_without_reopening() -> None:
    activation, incident = _incident()
    incident = attach_recovery_evaluation(
        incident,
        _recovery(
            incident.detection.source_incident_id,
            activation,
            state=RecoveryState.WORKER_BACKLOG_RECOVERING,
            seconds=60,
            salt="recovering",
        ),
    )
    incident = attach_recovery_evaluation(
        incident,
        _recovery(
            incident.detection.source_incident_id,
            activation,
            state=RecoveryState.WORKER_BACKLOG_RECOVERED,
            seconds=70,
            salt="recovered",
        ),
    )
    closed_at = incident.closed_at
    incident = attach_recovery_evaluation(
        incident,
        _recovery(
            incident.detection.source_incident_id,
            activation,
            state=RecoveryState.WORKER_BACKLOG_UNKNOWN,
            seconds=80,
            salt="unknown",
        ),
    )

    assert incident.lifecycle_state == IncidentLifecycleState.CLOSED
    assert incident.closed_at == closed_at
    assert incident.current_observation is not None
    assert incident.current_observation.evidence_quality == ObservationQuality.UNKNOWN


def test_duplicate_recovery_attachment_is_idempotent() -> None:
    activation, incident = _incident()
    recovery = _recovery(
        incident.detection.source_incident_id,
        activation,
        state=RecoveryState.WORKER_BACKLOG_ACTIVE,
        seconds=60,
    )
    first = attach_recovery_evaluation(incident, recovery)
    second = attach_recovery_evaluation(first, recovery)

    assert first == second
    assert len(first.recovery.evaluations) == 1


def test_mismatched_incident_and_diagnosis_scope_are_rejected() -> None:
    activation, incident = _incident()
    wrong_incident = _recovery(
        "inc-" + "f" * 24,
        activation,
        state=RecoveryState.WORKER_BACKLOG_ACTIVE,
        seconds=60,
    )
    with pytest.raises(ValueError, match="identity"):
        attach_recovery_evaluation(incident, wrong_incident)

    diagnosis_payload = _diagnosis(activation).model_dump(mode="json")
    diagnosis_payload["context"]["profile"] = "demo-lite"
    diagnosis_payload["diagnosis_id"] = diagnosis_id(
        {
            "condition_evaluation_id": diagnosis_payload["condition_evaluation_id"],
            "source_bundle_digests": diagnosis_payload["context"]["source_bundle_digests"],
            "policy": diagnosis_payload["policy"],
            "initial_evidence_ids": diagnosis_payload["initial_evidence_ids"],
            "additional_evidence": diagnosis_payload["additional_evidence"],
            "steps": [],
            "hypotheses": diagnosis_payload["hypotheses"],
            "stop_reason": diagnosis_payload["stop_reason"],
        }
    )
    with pytest.raises(ValueError, match="profile"):
        attach_diagnosis(incident, DiagnosisRun.model_validate(diagnosis_payload))

    diagnosis_payload = _diagnosis(activation).model_dump(mode="json")
    diagnosis_payload["context"]["cluster_context"] = "kind-unrelated"
    diagnosis_payload["diagnosis_id"] = diagnosis_id(
        {
            "condition_evaluation_id": diagnosis_payload["condition_evaluation_id"],
            "source_bundle_digests": diagnosis_payload["context"]["source_bundle_digests"],
            "policy": diagnosis_payload["policy"],
            "initial_evidence_ids": diagnosis_payload["initial_evidence_ids"],
            "additional_evidence": diagnosis_payload["additional_evidence"],
            "steps": [],
            "hypotheses": diagnosis_payload["hypotheses"],
            "stop_reason": diagnosis_payload["stop_reason"],
        }
    )
    with pytest.raises(ValueError, match="cluster context"):
        attach_diagnosis(incident, DiagnosisRun.model_validate(diagnosis_payload))


def test_reordered_timeline_and_digest_mutation_are_rejected() -> None:
    _, incident = _incident()
    payload = incident.model_dump(mode="json")
    payload["timeline"] = list(reversed(payload["timeline"]))
    with pytest.raises(ValueError, match="begin with DETECTED"):
        IncidentRecord.model_validate(payload)

    payload = incident.model_dump(mode="json")
    payload["provenance"]["runtime_image"] = "changed"
    with pytest.raises(ValueError, match="record digest"):
        IncidentRecord.model_validate(payload)


def test_new_activation_creates_a_distinct_incident_candidate() -> None:
    first = create_incident(activation=_activation(salt="a"), provenance=_provenance())
    second = create_incident(activation=_activation(salt="b"), provenance=_provenance())

    assert first.incident_id != second.incident_id
    assert first.incident_identity_sha256 != second.incident_identity_sha256


def test_build_incident_cli_is_offline_and_writes_valid_artifact(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    activation_path = tmp_path / "activation.json"
    output_path = tmp_path / "incident.json"
    activation_path.write_text(
        _activation().model_dump_json(indent=2),
        encoding="utf-8",
    )

    exit_code = cli.main(
        [
            "build-incident",
            "--activation",
            str(activation_path),
            "--source-sha",
            "abcdef1234567",
            "--source-tree-sha256",
            "2" * 64,
            "--runtime-image",
            "ghcr.io/example/cloud_portfolio:test",
            "--argocd-revision",
            "1234567890abcdef",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    incident = IncidentRecord.model_validate_json(output_path.read_bytes())
    assert incident.lifecycle_state == IncidentLifecycleState.ACTIVE
    assert incident.detection.artifact_ref == "activation.json"
