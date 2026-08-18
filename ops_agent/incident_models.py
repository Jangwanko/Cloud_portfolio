"""Versioned models for deterministic incident lifecycle artifacts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, ValidationInfo, model_validator

from ops_agent.evaluation_models import FrozenModel, canonical_sha256
from ops_agent.recovery_models import RecoveryState


class IncidentLifecycleState(str, Enum):
    DETECTED = "DETECTED"
    ACTIVE = "ACTIVE"
    RECOVERING = "RECOVERING"
    RECOVERED = "RECOVERED"
    CLOSED = "CLOSED"


class IncidentOutcome(str, Enum):
    RECOVERED = "RECOVERED"


class ObservationQuality(str, Enum):
    USABLE = "USABLE"
    UNKNOWN = "UNKNOWN"


class IncidentEventType(str, Enum):
    DETECTED = "DETECTED"
    ACTIVE = "ACTIVE"
    DIAGNOSIS_ATTACHED = "DIAGNOSIS_ATTACHED"
    RECOVERY_OBSERVED = "RECOVERY_OBSERVED"
    RECOVERING = "RECOVERING"
    RECOVERED = "RECOVERED"
    CLOSED = "CLOSED"
    OBSERVATION_UPDATED = "OBSERVATION_UPDATED"


def _validate_reference(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ":" in path.parts[0] or ".." in path.parts:
        raise ValueError("artifact references must be repository-relative")
    return normalized


class IncidentDetection(FrozenModel):
    condition_evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    condition_name: Literal["CORE_BACKLOG_PRESSURE"] = "CORE_BACKLOG_PRESSURE"
    condition_state: Literal["PRESENT"] = "PRESENT"
    policy_version: Literal["local-ha.conditions.v2"] = "local-ha.conditions.v2"
    evaluator_version: Literal["ops.evaluator.v2"] = "ops.evaluator.v2"
    ruleset_version: Literal["ops.conditions.rules.v2"] = "ops.conditions.rules.v2"
    source_bundle_digests: list[str] = Field(min_length=1, max_length=256)
    source_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_incident_id: str | None = Field(default=None, min_length=1, max_length=256)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_ref: str | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> "IncidentDetection":
        object.__setattr__(self, "artifact_ref", _validate_reference(self.artifact_ref))
        return self


class IncidentDiagnosis(FrozenModel):
    diagnosis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: str = Field(min_length=1, max_length=128)
    status: Literal["COMPLETED"] = "COMPLETED"
    validation_status: Literal["VALID"] = "VALID"
    stop_reason: str = Field(min_length=1, max_length=128)
    tool_names: list[str] = Field(default_factory=list, max_length=8)
    output_repairs_used: int = Field(ge=0, le=1)
    completed_at: datetime
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_ref: str | None = None

    @model_validator(mode="after")
    def validate_diagnosis(self) -> "IncidentDiagnosis":
        if self.completed_at.utcoffset() is None:
            raise ValueError("diagnosis completed_at must be timezone-aware")
        object.__setattr__(self, "artifact_ref", _validate_reference(self.artifact_ref))
        return self


class IncidentRecoveryEvaluation(FrozenModel):
    evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: RecoveryState
    observed_at: datetime
    policy_version: str = Field(min_length=1, max_length=128)
    evaluator_version: str = Field(min_length=1, max_length=128)
    ruleset_version: str = Field(min_length=1, max_length=128)
    source_bundle_digests: list[str] = Field(min_length=1, max_length=256)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_ref: str | None = None

    @model_validator(mode="after")
    def validate_recovery(self) -> "IncidentRecoveryEvaluation":
        if self.observed_at.utcoffset() is None:
            raise ValueError("recovery observed_at must be timezone-aware")
        object.__setattr__(self, "artifact_ref", _validate_reference(self.artifact_ref))
        return self


class IncidentRecovery(FrozenModel):
    policy_version: str | None = None
    evaluations: list[IncidentRecoveryEvaluation] = Field(
        default_factory=list,
        max_length=256,
    )


class CurrentIncidentObservation(FrozenModel):
    evaluation_state: RecoveryState
    evidence_quality: ObservationQuality
    reason_codes: list[str] = Field(default_factory=list, max_length=64)
    source_bundle_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime

    @model_validator(mode="after")
    def validate_observation(self) -> "CurrentIncidentObservation":
        if self.observed_at.utcoffset() is None:
            raise ValueError("current observation timestamp must be timezone-aware")
        expected = (
            ObservationQuality.UNKNOWN
            if self.evaluation_state == RecoveryState.WORKER_BACKLOG_UNKNOWN
            else ObservationQuality.USABLE
        )
        if self.evidence_quality != expected:
            raise ValueError("observation quality must match recovery evaluation state")
        return self


class IncidentTimelineEvent(FrozenModel):
    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_type: IncidentEventType
    observed_at: datetime
    artifact_id: str = Field(min_length=1, max_length=256)
    lifecycle_state: IncidentLifecycleState
    reason_codes: list[str] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_timestamp(self) -> "IncidentTimelineEvent":
        if self.observed_at.utcoffset() is None:
            raise ValueError("timeline timestamps must be timezone-aware")
        expected = canonical_sha256(
            {
                "event_type": self.event_type.value,
                "observed_at": self.observed_at.isoformat(),
                "artifact_id": self.artifact_id,
                "lifecycle_state": self.lifecycle_state.value,
                "reason_codes": self.reason_codes,
            }
        )
        if self.event_id != expected:
            raise ValueError("timeline event_id does not match event payload")
        return self


class IncidentProvenance(FrozenModel):
    source_sha: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    source_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_image: str = Field(min_length=1, max_length=512)
    argocd_revision: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    builder_version: Literal["ops.incident.builder.v1"] = "ops.incident.builder.v1"


def incident_identity_sha256(detection: IncidentDetection | dict) -> str:
    value = (
        detection.model_dump(mode="json")
        if isinstance(detection, IncidentDetection)
        else detection
    )
    payload = {
        "incident_type": "WORKER_BACKLOG_PRESSURE",
        "profile": "local-ha",
        "condition_evaluation_id": value["condition_evaluation_id"],
        "source_bundle_digests": value["source_bundle_digests"],
        "source_identity_sha256": value["source_identity_sha256"],
    }
    if value.get("source_incident_id") is not None:
        payload["source_incident_id"] = value["source_incident_id"]
    return canonical_sha256(payload)


class IncidentRecord(FrozenModel):
    schema_version: Literal["ops.incident.v1"] = "ops.incident.v1"
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    incident_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    incident_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    incident_type: Literal["WORKER_BACKLOG_PRESSURE"] = "WORKER_BACKLOG_PRESSURE"
    profile: Literal["local-ha"] = "local-ha"
    created_at: datetime
    detected_at: datetime
    closed_at: datetime | None = None
    lifecycle_state: IncidentLifecycleState
    outcome: IncidentOutcome | None = None
    detection: IncidentDetection
    diagnosis: IncidentDiagnosis | None = None
    recovery: IncidentRecovery
    current_observation: CurrentIncidentObservation | None = None
    timeline: list[IncidentTimelineEvent] = Field(min_length=2, max_length=1024)
    provenance: IncidentProvenance

    def _record_payload(self) -> dict:
        payload = super().model_dump(mode="json")
        payload.pop("incident_record_sha256", None)
        if payload["detection"].get("source_incident_id") is None:
            payload["detection"].pop("source_incident_id", None)
        return payload

    def verify_integrity(self) -> None:
        identity = incident_identity_sha256(self.detection)
        if self.incident_identity_sha256 != identity:
            raise ValueError("incident identity digest does not match detection")
        if self.incident_id != f"inc-{identity[:24]}":
            raise ValueError("incident ID does not match deterministic identity")
        if self.incident_record_sha256 != canonical_sha256(self._record_payload()):
            raise ValueError("incident record digest does not match payload")

    def model_dump(self, *args, **kwargs) -> dict:
        self.verify_integrity()
        return super().model_dump(*args, **kwargs)

    @model_validator(mode="after")
    def validate_incident(self, info: ValidationInfo) -> "IncidentRecord":
        for value in (self.created_at, self.detected_at):
            if value.utcoffset() is None:
                raise ValueError("incident timestamps must be timezone-aware")
        if self.created_at > self.detected_at:
            raise ValueError("incident creation must not follow detection")
        times = [item.observed_at for item in self.timeline]
        if times != sorted(times):
            raise ValueError("incident timeline must be chronological")
        event_ids = [item.event_id for item in self.timeline]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("incident timeline event IDs must be unique")
        if [item.event_type for item in self.timeline[:2]] != [
            IncidentEventType.DETECTED,
            IncidentEventType.ACTIVE,
        ]:
            raise ValueError("incident timeline must begin with DETECTED then ACTIVE")
        recovery_ids = [item.evaluation_id for item in self.recovery.evaluations]
        if len(recovery_ids) != len(set(recovery_ids)):
            raise ValueError("recovery evaluations must be idempotently unique")
        recovery_times = [item.observed_at for item in self.recovery.evaluations]
        if recovery_times != sorted(recovery_times):
            raise ValueError("recovery evaluations must be chronological")
        is_closed = self.lifecycle_state == IncidentLifecycleState.CLOSED
        if is_closed:
            if self.outcome != IncidentOutcome.RECOVERED or self.closed_at is None:
                raise ValueError("closed incident requires recovered outcome and closed_at")
            if self.closed_at.utcoffset() is None:
                raise ValueError("closed_at must be timezone-aware")
            event_types = [item.event_type for item in self.timeline]
            if IncidentEventType.RECOVERED not in event_types or event_types.count(
                IncidentEventType.CLOSED
            ) != 1:
                raise ValueError("closed incident requires one recovered and closed event")
            closed_event = next(
                item for item in self.timeline if item.event_type == IncidentEventType.CLOSED
            )
            if closed_event.observed_at != self.closed_at:
                raise ValueError("closed_at must match the immutable CLOSED event")
        elif self.outcome is not None or self.closed_at is not None:
            raise ValueError("open incident must not have outcome or closed_at")
        identity = incident_identity_sha256(self.detection)
        if self.incident_identity_sha256 != identity:
            raise ValueError("incident identity digest does not match detection")
        if self.incident_id != f"inc-{identity[:24]}":
            raise ValueError("incident ID does not match deterministic identity")
        if not (info.context or {}).get("skip_record_integrity"):
            self.verify_integrity()
        return self


__all__ = [
    "CurrentIncidentObservation",
    "IncidentDetection",
    "IncidentDiagnosis",
    "IncidentEventType",
    "IncidentLifecycleState",
    "IncidentOutcome",
    "IncidentProvenance",
    "IncidentRecord",
    "IncidentRecovery",
    "IncidentRecoveryEvaluation",
    "IncidentTimelineEvent",
    "ObservationQuality",
    "incident_identity_sha256",
]
