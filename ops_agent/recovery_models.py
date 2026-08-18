"""Versioned models for deterministic Worker backlog recovery evaluation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ops_agent.evaluation_models import (
    ConditionState,
    EvaluationStatus,
    FrozenModel,
    canonical_sha256,
)


class RecoveryState(str, Enum):
    WORKER_BACKLOG_ACTIVE = "WORKER_BACKLOG_ACTIVE"
    WORKER_BACKLOG_RECOVERING = "WORKER_BACKLOG_RECOVERING"
    WORKER_BACKLOG_UNKNOWN = "WORKER_BACKLOG_UNKNOWN"
    WORKER_BACKLOG_RECOVERED = "WORKER_BACKLOG_RECOVERED"


class LowLagEvidencePolicy(str, Enum):
    INVALID_ONLY = "INVALID_ONLY"
    INVALID_PLUS_DERIVED = "INVALID_PLUS_DERIVED"


class RecoveryCompletionStatus(str, Enum):
    CALIBRATION_PENDING = "CALIBRATION_PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"


class RecoveredPolicyStatus(str, Enum):
    CALIBRATION_PENDING = "CALIBRATION_PENDING"
    PROMOTED = "PROMOTED"


class RecoveryEvaluationPolicy(FrozenModel):
    """Immutable local-ha recovery policy calibrated by Phase 4."""

    profile: Literal["local-ha"] = "local-ha"
    policy_version: Literal[
        "worker-backlog-local-ha.recovery.v1",
        "worker-backlog-local-ha.recovery.v2",
    ] = (
        "worker-backlog-local-ha.recovery.v1"
    )
    source_evidence_policy_version: Literal["local-ha.evidence.v1"] = (
        "local-ha.evidence.v1"
    )
    activation_schema_version: Literal["ops.conditions.v2"] = "ops.conditions.v2"
    activation_policy_version: Literal["local-ha.conditions.v2"] = (
        "local-ha.conditions.v2"
    )
    activation_evaluator_version: Literal["ops.evaluator.v2"] = "ops.evaluator.v2"
    activation_ruleset_version: Literal["ops.conditions.rules.v2"] = (
        "ops.conditions.rules.v2"
    )
    context: Literal["kind-messaging-ha"] = "kind-messaging-ha"
    namespace: Literal["messaging-app"] = "messaging-app"
    topic: Literal["message-ingress"] = "message-ingress"
    consumer_group: Literal["message-worker"] = "message-worker"
    expected_partition_count: Literal[8] = 8
    kafka_range_window_seconds: Literal[60] = 60
    kafka_range_step_seconds: Literal[5] = 5
    kafka_expected_sample_count: Literal[13] = 13
    kafka_range_collection_skew_seconds: Literal[1] = 1
    kafka_source_to_range_end_max_seconds: Literal[5] = 5
    freshness_age_tolerance_seconds: Literal[0.01] = 0.01
    configured_capture_interval_seconds: Literal[15] = 15
    capture_interval_min_seconds: Literal[9.0] = 9.0
    capture_interval_max_seconds: Literal[21.0] = 21.0
    recovering_consecutive_capture_count: Literal[3] = 3
    low_lag_evidence_policy: Literal[LowLagEvidencePolicy.INVALID_ONLY] = (
        LowLagEvidencePolicy.INVALID_ONLY
    )
    derived_lag_enabled: Literal[False] = False
    postgres_required_body_status: Literal["ready"] = "ready"
    postgres_require_ha_mode: Literal[True] = True
    postgres_require_primary_reachable: Literal[True] = True
    recovered_policy_status: RecoveredPolicyStatus = (
        RecoveredPolicyStatus.CALIBRATION_PENDING
    )
    recovered_contract_version: Literal[
        "local-ha.medium-reentry-candidate.v1"
    ] | None = None
    recovered_workload_profile: Literal["MEDIUM"] | None = None
    recovered_target_arrival_rate_records_per_second: Literal[75] | None = None
    recovered_actual_produce_rate_minimum: Literal[74.98333333333333] | None = None
    recovered_actual_produce_rate_maximum: Literal[77.08333333333333] | None = None
    recovered_total_lag_maximum: Literal[22] | None = None
    recovered_lag_slope_maximum: Literal[0.0] | None = None
    recovered_consecutive_capture_count: Literal[3] | None = None
    calibration_experiment_id: Literal[
        "20260816T100600Z",
        "20260816T100600Z+20260816T194023Z",
    ] = "20260816T100600Z"
    calibration_provenance: str = (
        "results/ops-agent/recovery-calibration/20260816T100600Z/analysis.json"
    )

    @model_validator(mode="after")
    def validate_versioned_recovered_policy(self) -> "RecoveryEvaluationPolicy":
        recovered_values = (
            self.recovered_contract_version,
            self.recovered_workload_profile,
            self.recovered_target_arrival_rate_records_per_second,
            self.recovered_actual_produce_rate_minimum,
            self.recovered_actual_produce_rate_maximum,
            self.recovered_total_lag_maximum,
            self.recovered_lag_slope_maximum,
            self.recovered_consecutive_capture_count,
        )
        if self.policy_version.endswith(".v1"):
            if self.recovered_policy_status != RecoveredPolicyStatus.CALIBRATION_PENDING:
                raise ValueError("recovery v1 must keep RECOVERED calibration pending")
            if any(value is not None for value in recovered_values):
                raise ValueError("recovery v1 must not define RECOVERED thresholds")
            if self.calibration_experiment_id != "20260816T100600Z":
                raise ValueError("recovery v1 calibration provenance changed")
            if self.calibration_provenance != (
                "results/ops-agent/recovery-calibration/20260816T100600Z/analysis.json"
            ):
                raise ValueError("recovery v1 calibration path changed")
        else:
            if self.recovered_policy_status != RecoveredPolicyStatus.PROMOTED:
                raise ValueError("recovery v2 requires a promoted RECOVERED policy")
            if any(value is None for value in recovered_values):
                raise ValueError("recovery v2 RECOVERED contract is incomplete")
            if self.calibration_experiment_id != (
                "20260816T100600Z+20260816T194023Z"
            ):
                raise ValueError("recovery v2 calibration provenance changed")
            if self.calibration_provenance != (
                "results/ops-agent/recovered-calibration/20260816T194023Z/analysis.json"
            ):
                raise ValueError("recovery v2 calibration path changed")
        return self


class RecoveryActivationReference(FrozenModel):
    schema_version: Literal["ops.conditions.v2"]
    evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluator_version: str
    ruleset_version: str
    policy_version: str
    condition: Literal["CORE_BACKLOG_PRESSURE"] = "CORE_BACKLOG_PRESSURE"
    condition_state: ConditionState
    source_bundle_digests: list[str] = Field(min_length=1, max_length=256)
    last_collection_completed_at: datetime
    last_kafka_source_timestamp: datetime | None = None
    source_identity_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class RecoverySourceBundleReference(FrozenModel):
    sequence_index: int = Field(ge=0)
    bundle_id: str
    source_incident_id: str
    expected_source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actual_source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    digest_matches: bool
    collection_started_at: datetime
    collection_completed_at: datetime
    kafka_source_timestamp: datetime | None = None


class NegativeLagPoint(FrozenModel):
    evidence_id: str
    partition: str
    sample_index: int = Field(ge=0)
    range_evaluation_timestamp: float
    exporter_lag_records: int = Field(lt=0)
    end_offset_records: int | None = None
    committed_offset_records: int | None = None


class RecoveryCaptureObservation(FrozenModel):
    sequence_index: int = Field(ge=0)
    bundle_id: str
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    usable: bool
    issue_codes: list[str] = Field(default_factory=list)
    condition_source_identity_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    recovery_source_identity_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    kafka_source_timestamp: datetime | None = None
    partition_set: list[str] = Field(default_factory=list)
    total_lag_records: int | None = Field(default=None, ge=0)
    lag_slope_60s_records_per_second: float | None = None
    produce_rate_60s_records_per_second: float | None = None
    committed_offset_rate_60s_records_per_second: float | None = None
    rate_arithmetic_consistent: bool | None = None
    partition_offsets: dict[str, dict[str, int]] = Field(default_factory=dict)
    postgres_ready: bool | None = None
    postgres_context: dict[str, Any] = Field(default_factory=dict)
    worker_context: dict[str, Any] = Field(default_factory=dict)
    keda_context: dict[str, Any] = Field(default_factory=dict)
    worker_stage_latency_context: dict[str, Any] = Field(default_factory=dict)
    required_evidence_ids: list[str] = Field(default_factory=list)
    negative_exporter_lag: list[NegativeLagPoint] = Field(default_factory=list)
    derived_lag_evidence_ids: list[str] = Field(default_factory=list)
    state_after_capture: RecoveryState
    state_reason_codes: list[str]


class RecoveryWindow(FrozenModel):
    required_capture_count: int = Field(ge=1)
    evaluated_sequence_indexes: list[int] = Field(default_factory=list)
    matched_recovering_windows: list[list[int]] = Field(default_factory=list)
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    capture_count: int = Field(ge=0)


class RecoveryQuality(FrozenModel):
    required_evidence_names: list[str]
    expected_partition_count: int = Field(ge=1)
    low_lag_evidence_policy: LowLagEvidencePolicy
    exporter_negative_lag_preserved: bool
    negative_lag_clamped_to_zero: Literal[False] = False
    derived_lag_created: Literal[False] = False
    timestamp_coherence_contract: str
    source_identity_required: bool = True


class RecoveryCompletion(FrozenModel):
    status: RecoveryCompletionStatus
    reason_codes: list[str]


def recovery_evaluation_id(payload: dict[str, Any]) -> str:
    return canonical_sha256(payload)


class RecoveryEvaluation(FrozenModel):
    schema_version: Literal["ops.recovery.v1"] = "ops.recovery.v1"
    evaluator_version: Literal[
        "ops.recovery.evaluator.v1",
        "ops.recovery.evaluator.v2",
    ] = (
        "ops.recovery.evaluator.v1"
    )
    ruleset_version: Literal[
        "ops.recovery.rules.v1",
        "ops.recovery.rules.v2",
    ] = "ops.recovery.rules.v1"
    recovery_evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_status: EvaluationStatus
    incident_id: str
    activation: RecoveryActivationReference
    policy: RecoveryEvaluationPolicy
    state: RecoveryState
    reason_codes: list[str]
    source_bundles: list[RecoverySourceBundleReference] = Field(
        min_length=1,
        max_length=256,
    )
    observations: list[RecoveryCaptureObservation] = Field(
        min_length=1,
        max_length=256,
    )
    window: RecoveryWindow
    evidence_ids: list[str] = Field(default_factory=list)
    quality: RecoveryQuality
    recovery_completion: RecoveryCompletion

    def _id_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluator_version": self.evaluator_version,
            "ruleset_version": self.ruleset_version,
            "evaluation_status": self.evaluation_status.value,
            "incident_id": self.incident_id,
            "activation": self.activation.model_dump(mode="json"),
            "policy": self.policy.model_dump(mode="json"),
            "state": self.state.value,
            "reason_codes": self.reason_codes,
            "source_bundles": [
                item.model_dump(mode="json") for item in self.source_bundles
            ],
            "observations": [
                item.model_dump(mode="json") for item in self.observations
            ],
            "window": self.window.model_dump(mode="json"),
            "evidence_ids": self.evidence_ids,
            "quality": self.quality.model_dump(mode="json"),
            "recovery_completion": self.recovery_completion.model_dump(mode="json"),
        }

    def verify_integrity(self) -> None:
        if self.recovery_evaluation_id != recovery_evaluation_id(self._id_payload()):
            raise ValueError("recovery_evaluation_id does not match the payload")

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.verify_integrity()
        return super().model_dump(*args, **kwargs)

    @model_validator(mode="after")
    def validate_output_contract(self) -> "RecoveryEvaluation":
        indexes = list(range(len(self.source_bundles)))
        if [item.sequence_index for item in self.source_bundles] != indexes:
            raise ValueError("source bundles must retain ordered sequence indexes")
        if [item.sequence_index for item in self.observations] != indexes:
            raise ValueError("observations must retain ordered sequence indexes")
        for source, observation in zip(self.source_bundles, self.observations):
            if source.bundle_id != observation.bundle_id:
                raise ValueError("recovery source and observation bundle mismatch")
            if source.actual_source_bundle_sha256 != observation.source_bundle_sha256:
                raise ValueError("recovery source and observation digest mismatch")
        is_v2 = self.policy.policy_version.endswith(".v2")
        expected_evaluator = (
            "ops.recovery.evaluator.v2" if is_v2 else "ops.recovery.evaluator.v1"
        )
        expected_ruleset = (
            "ops.recovery.rules.v2" if is_v2 else "ops.recovery.rules.v1"
        )
        if self.evaluator_version != expected_evaluator:
            raise ValueError("recovery evaluator version does not match policy")
        if self.ruleset_version != expected_ruleset:
            raise ValueError("recovery ruleset version does not match policy")
        if self.state == RecoveryState.WORKER_BACKLOG_RECOVERED and not is_v2:
            raise ValueError("recovery v1 must not emit RECOVERED")
        expected_status = (
            EvaluationStatus.PARTIAL
            if self.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
            else EvaluationStatus.COMPLETE
        )
        if self.evaluation_status != expected_status:
            raise ValueError("UNKNOWN must map to PARTIAL; determinate states to COMPLETE")
        expected_completion = (
            RecoveryCompletionStatus.CALIBRATION_PENDING
            if not is_v2
            else (
                RecoveryCompletionStatus.COMPLETE
                if self.state == RecoveryState.WORKER_BACKLOG_RECOVERED
                else RecoveryCompletionStatus.IN_PROGRESS
            )
        )
        if self.recovery_completion.status != expected_completion:
            raise ValueError("recovery completion status does not match state and policy")
        if self.quality.negative_lag_clamped_to_zero:
            raise ValueError("negative exporter lag must never be clamped")
        if self.quality.derived_lag_created:
            raise ValueError("INVALID_ONLY policy must not create derived lag")
        if self.evidence_ids != sorted(set(self.evidence_ids)):
            raise ValueError("recovery evidence IDs must be sorted and unique")
        self.verify_integrity()
        return self


__all__ = [
    "LowLagEvidencePolicy",
    "NegativeLagPoint",
    "RecoveryActivationReference",
    "RecoveryCaptureObservation",
    "RecoveryCompletion",
    "RecoveryCompletionStatus",
    "RecoveryEvaluation",
    "RecoveryEvaluationPolicy",
    "RecoveryQuality",
    "RecoverySourceBundleReference",
    "RecoveryState",
    "RecoveryWindow",
    "RecoveredPolicyStatus",
    "recovery_evaluation_id",
]
