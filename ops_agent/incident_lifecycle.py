"""Pure incident lifecycle transitions over immutable Ops Agent artifacts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel

from ops_agent.diagnosis_models import DiagnosisRun
from ops_agent.evaluation_models import ConditionName, ConditionState, canonical_sha256
from ops_agent.incident_models import (
    CurrentIncidentObservation,
    IncidentDetection,
    IncidentDiagnosis,
    IncidentEventType,
    IncidentLifecycleState,
    IncidentOutcome,
    IncidentProvenance,
    IncidentRecord,
    IncidentRecovery,
    IncidentRecoveryEvaluation,
    IncidentTimelineEvent,
    ObservationQuality,
    incident_identity_sha256,
)
from ops_agent.recovery_models import RecoveryEvaluation, RecoveryState
from ops_agent.sequence_models import SequenceConditionEvaluation


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _artifact_digest(value: BaseModel) -> str:
    return canonical_sha256(value.model_dump(mode="json"))


def _source_incident_id(evaluation: SequenceConditionEvaluation) -> str:
    values = [item.incident_id for item in evaluation.source_bundles]
    if len(set(values)) == 1:
        return values[0]
    parts = [value.rsplit("-", 1) for value in values]
    if all(len(part) == 2 and part[1].isdigit() for part in parts):
        parents = {part[0] for part in parts}
        if len(parents) == 1:
            return next(iter(parents))
    raise ValueError("incident activation requires one logical source incident ID")


def _event(
    *,
    event_type: IncidentEventType,
    observed_at: datetime,
    artifact_id: str,
    lifecycle_state: IncidentLifecycleState,
    reason_codes: list[str],
) -> dict[str, Any]:
    payload = {
        "event_type": event_type.value,
        "observed_at": observed_at.isoformat(),
        "artifact_id": artifact_id,
        "lifecycle_state": lifecycle_state.value,
        "reason_codes": reason_codes,
    }
    return {"event_id": canonical_sha256(payload), **payload}


def _materialize(payload: Mapping[str, Any]) -> IncidentRecord:
    value = _json_value(dict(payload))
    value["incident_record_sha256"] = "0" * 64
    draft = IncidentRecord.model_validate(
        value,
        context={"skip_record_integrity": True},
    )
    normalized = draft._record_payload()
    normalized["incident_record_sha256"] = canonical_sha256(normalized)
    return IncidentRecord.model_validate(normalized)


def _parse_activation(
    value: SequenceConditionEvaluation | Mapping[str, Any] | str | bytes,
) -> SequenceConditionEvaluation:
    if isinstance(value, SequenceConditionEvaluation):
        value.verify_integrity()
        return value
    if isinstance(value, (str, bytes)):
        return SequenceConditionEvaluation.model_validate_json(value)
    return SequenceConditionEvaluation.model_validate(value)


def _parse_diagnosis(value: DiagnosisRun | Mapping[str, Any] | str | bytes) -> DiagnosisRun:
    if isinstance(value, DiagnosisRun):
        value.verify_integrity()
        return value
    if isinstance(value, (str, bytes)):
        return DiagnosisRun.model_validate_json(value)
    return DiagnosisRun.model_validate(value)


def _parse_recovery(
    value: RecoveryEvaluation | Mapping[str, Any] | str | bytes,
) -> RecoveryEvaluation:
    if isinstance(value, RecoveryEvaluation):
        value.verify_integrity()
        return value
    if isinstance(value, (str, bytes)):
        return RecoveryEvaluation.model_validate_json(value)
    return RecoveryEvaluation.model_validate(value)


def create_incident(
    *,
    activation: SequenceConditionEvaluation | Mapping[str, Any] | str | bytes,
    provenance: IncidentProvenance | Mapping[str, Any],
    activation_artifact_ref: str | None = None,
) -> IncidentRecord:
    condition_evaluation = _parse_activation(activation)
    condition = condition_evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    if condition.state != ConditionState.PRESENT:
        raise ValueError("incident creation requires CORE_BACKLOG_PRESSURE=PRESENT")
    source_identities = {
        item.source_identity_sha256
        for item in condition_evaluation.capture_observations
        if item.source_identity_sha256 is not None
    }
    if len(source_identities) != 1:
        raise ValueError("incident activation requires one complete source identity")
    source_digests = [
        item.source_bundle_sha256 for item in condition_evaluation.source_bundles
    ]
    detection = IncidentDetection(
        condition_evaluation_id=condition_evaluation.evaluation_id,
        policy_version=condition_evaluation.policy.policy_version,
        evaluator_version=condition_evaluation.evaluator_version,
        ruleset_version=condition_evaluation.ruleset_version,
        source_bundle_digests=source_digests,
        source_identity_sha256=next(iter(source_identities)),
        source_incident_id=_source_incident_id(condition_evaluation),
        artifact_sha256=_artifact_digest(condition_evaluation),
        artifact_ref=activation_artifact_ref,
    )
    identity = incident_identity_sha256(detection)
    detected_at = condition_evaluation.source_bundles[-1].collection_completed_at
    incident_id = f"inc-{identity[:24]}"
    timeline = [
        _event(
            event_type=IncidentEventType.DETECTED,
            observed_at=detected_at,
            artifact_id=condition_evaluation.evaluation_id,
            lifecycle_state=IncidentLifecycleState.DETECTED,
            reason_codes=["DETERMINISTIC_CONDITION_PRESENT"],
        ),
        _event(
            event_type=IncidentEventType.ACTIVE,
            observed_at=detected_at,
            artifact_id=condition_evaluation.evaluation_id,
            lifecycle_state=IncidentLifecycleState.ACTIVE,
            reason_codes=["WORKER_BACKLOG_INCIDENT_ACTIVATED"],
        ),
    ]
    return _materialize(
        {
            "schema_version": "ops.incident.v1",
            "incident_id": incident_id,
            "incident_identity_sha256": identity,
            "incident_type": "WORKER_BACKLOG_PRESSURE",
            "profile": condition_evaluation.policy.cluster_profile,
            "created_at": detected_at,
            "detected_at": detected_at,
            "closed_at": None,
            "lifecycle_state": IncidentLifecycleState.ACTIVE,
            "outcome": None,
            "detection": detection,
            "diagnosis": None,
            "recovery": IncidentRecovery(),
            "current_observation": None,
            "timeline": timeline,
            "provenance": IncidentProvenance.model_validate(provenance),
        }
    )


def attach_diagnosis(
    incident: IncidentRecord | Mapping[str, Any] | str | bytes,
    diagnosis: DiagnosisRun | Mapping[str, Any] | str | bytes,
    *,
    artifact_ref: str | None = None,
) -> IncidentRecord:
    record = (
        incident
        if isinstance(incident, IncidentRecord)
        else IncidentRecord.model_validate_json(incident)
        if isinstance(incident, (str, bytes))
        else IncidentRecord.model_validate(incident)
    )
    record.verify_integrity()
    run = _parse_diagnosis(diagnosis)
    if record.diagnosis is not None:
        if record.diagnosis.diagnosis_id == run.diagnosis_id:
            if record.diagnosis.artifact_sha256 != _artifact_digest(run):
                raise ValueError("duplicate diagnosis ID has a different digest")
            return record
        raise ValueError("incident already has a different diagnosis")
    if record.lifecycle_state == IncidentLifecycleState.CLOSED:
        raise ValueError("new diagnosis cannot be attached after incident closure")
    if run.condition_evaluation_id != record.detection.condition_evaluation_id:
        raise ValueError("diagnosis condition evaluation does not match incident")
    if run.context.profile != record.profile:
        raise ValueError("diagnosis profile does not match incident")
    if run.context.cluster_context != "kind-messaging-ha":
        raise ValueError("diagnosis cluster context does not match local-ha incident")
    if run.context.source_bundle_digests != record.detection.source_bundle_digests:
        raise ValueError("diagnosis source bundle digests do not match detection")
    if not all(run.validation.model_dump(mode="python").values()):
        raise ValueError("diagnosis validator result must be fully valid")
    if run.completed_at < record.timeline[-1].observed_at:
        raise ValueError("diagnosis would reorder the incident timeline")
    diagnosis_ref = IncidentDiagnosis(
        diagnosis_id=run.diagnosis_id,
        model=run.policy.model,
        stop_reason=run.stop_reason.value,
        tool_names=[item.tool_id for item in run.steps],
        output_repairs_used=run.output_repairs_used,
        completed_at=run.completed_at,
        artifact_sha256=_artifact_digest(run),
        artifact_ref=artifact_ref,
    )
    payload = record.model_dump(mode="json")
    payload["diagnosis"] = diagnosis_ref.model_dump(mode="json")
    payload["timeline"].append(
        _event(
            event_type=IncidentEventType.DIAGNOSIS_ATTACHED,
            observed_at=run.completed_at,
            artifact_id=run.diagnosis_id,
            lifecycle_state=record.lifecycle_state,
            reason_codes=["READ_ONLY_DIAGNOSIS_VALIDATED"],
        )
    )
    return _materialize(payload)


def attach_recovery_evaluation(
    incident: IncidentRecord | Mapping[str, Any] | str | bytes,
    recovery: RecoveryEvaluation | Mapping[str, Any] | str | bytes,
    *,
    artifact_ref: str | None = None,
) -> IncidentRecord:
    record = (
        incident
        if isinstance(incident, IncidentRecord)
        else IncidentRecord.model_validate_json(incident)
        if isinstance(incident, (str, bytes))
        else IncidentRecord.model_validate(incident)
    )
    record.verify_integrity()
    evaluation = _parse_recovery(recovery)
    artifact_digest = _artifact_digest(evaluation)
    existing = {
        item.evaluation_id: item for item in record.recovery.evaluations
    }.get(evaluation.recovery_evaluation_id)
    if existing is not None:
        if existing.artifact_sha256 != artifact_digest:
            raise ValueError("duplicate recovery ID has a different digest")
        return record
    if (
        record.detection.source_incident_id is None
        or evaluation.incident_id != record.detection.source_incident_id
    ):
        raise ValueError("recovery incident identity does not match")
    if evaluation.policy.profile != record.profile:
        raise ValueError("recovery profile does not match incident")
    if evaluation.policy.context != "kind-messaging-ha":
        raise ValueError("recovery context does not match local-ha incident")
    if evaluation.activation.evaluation_id != record.detection.condition_evaluation_id:
        raise ValueError("recovery activation does not match incident detection")
    if evaluation.activation.source_bundle_digests != record.detection.source_bundle_digests:
        raise ValueError("recovery activation digests do not match incident detection")
    observed_at = evaluation.source_bundles[-1].collection_completed_at
    if record.recovery.evaluations and observed_at < record.recovery.evaluations[-1].observed_at:
        raise ValueError("recovery evaluation would reorder recovery history")
    if observed_at < record.timeline[-1].observed_at:
        raise ValueError("recovery evaluation would reorder the incident timeline")
    source_digests = [
        item.actual_source_bundle_sha256 for item in evaluation.source_bundles
    ]
    recovery_ref = IncidentRecoveryEvaluation(
        evaluation_id=evaluation.recovery_evaluation_id,
        state=evaluation.state,
        observed_at=observed_at,
        policy_version=evaluation.policy.policy_version,
        evaluator_version=evaluation.evaluator_version,
        ruleset_version=evaluation.ruleset_version,
        source_bundle_digests=source_digests,
        artifact_sha256=artifact_digest,
        artifact_ref=artifact_ref,
    )
    latest_digest = source_digests[-1]
    current_observation = CurrentIncidentObservation(
        evaluation_state=evaluation.state,
        evidence_quality=(
            ObservationQuality.UNKNOWN
            if evaluation.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
            else ObservationQuality.USABLE
        ),
        reason_codes=evaluation.reason_codes,
        source_bundle_digest=latest_digest,
        observed_at=observed_at,
    )
    payload = record.model_dump(mode="json")
    payload["recovery"]["evaluations"].append(recovery_ref.model_dump(mode="json"))
    payload["recovery"]["policy_version"] = evaluation.policy.policy_version
    payload["current_observation"] = current_observation.model_dump(mode="json")
    closed = record.lifecycle_state == IncidentLifecycleState.CLOSED
    if closed:
        event_type = IncidentEventType.OBSERVATION_UPDATED
        next_state = IncidentLifecycleState.CLOSED
        reason_codes = ["CLOSED_LIFECYCLE_PRESERVED", *evaluation.reason_codes]
    elif evaluation.state == RecoveryState.WORKER_BACKLOG_ACTIVE:
        event_type = IncidentEventType.RECOVERY_OBSERVED
        next_state = IncidentLifecycleState.ACTIVE
        reason_codes = evaluation.reason_codes
    elif evaluation.state == RecoveryState.WORKER_BACKLOG_RECOVERING:
        event_type = IncidentEventType.RECOVERING
        next_state = IncidentLifecycleState.RECOVERING
        reason_codes = evaluation.reason_codes
    elif evaluation.state == RecoveryState.WORKER_BACKLOG_UNKNOWN:
        event_type = IncidentEventType.OBSERVATION_UPDATED
        next_state = record.lifecycle_state
        reason_codes = ["OBSERVATION_QUALITY_UNKNOWN", *evaluation.reason_codes]
    else:
        if not evaluation.policy.policy_version.endswith(".v2"):
            raise ValueError("only recovery v2 can close an incident")
        event_type = IncidentEventType.RECOVERED
        next_state = IncidentLifecycleState.RECOVERED
        reason_codes = evaluation.reason_codes
    payload["lifecycle_state"] = next_state.value
    payload["timeline"].append(
        _event(
            event_type=event_type,
            observed_at=observed_at,
            artifact_id=evaluation.recovery_evaluation_id,
            lifecycle_state=next_state,
            reason_codes=reason_codes,
        )
    )
    if next_state == IncidentLifecycleState.RECOVERED:
        payload["timeline"].append(
            _event(
                event_type=IncidentEventType.CLOSED,
                observed_at=observed_at,
                artifact_id=evaluation.recovery_evaluation_id,
                lifecycle_state=IncidentLifecycleState.CLOSED,
                reason_codes=["DETERMINISTIC_RECOVERY_V2_COMPLETE"],
            )
        )
        payload["lifecycle_state"] = IncidentLifecycleState.CLOSED.value
        payload["outcome"] = IncidentOutcome.RECOVERED.value
        payload["closed_at"] = observed_at.isoformat()
    return _materialize(payload)


__all__ = [
    "attach_diagnosis",
    "attach_recovery_evaluation",
    "create_incident",
]
