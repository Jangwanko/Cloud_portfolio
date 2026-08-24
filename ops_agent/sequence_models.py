"""Versioned models for ordered evidence-sequence evaluation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ops_agent.evaluation_models import (
    AssessmentName,
    AssessmentResult,
    ConditionName,
    ConditionResult,
    ConditionState,
    EvaluationPolicy,
    EvaluationStatus,
    FrozenModel,
    canonical_sha256,
)
from ops_agent.models import CollectionStatus


class SequenceEvaluationPolicy(FrozenModel):
    """Immutable local-ha activation contract calibrated in Phases 2.5/2.6."""

    cluster_profile: Literal["local-ha"] = "local-ha"
    source_policy_version: Literal["local-ha.evidence.v1"] = (
        "local-ha.evidence.v1"
    )
    policy_version: Literal["local-ha.conditions.v2"] = "local-ha.conditions.v2"
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
    worker_deployment_name: Literal["worker"] = "worker"
    worker_unavailable_grace_seconds: Literal[120] = 120
    local_ha_min_ready_standbys: Literal[2] = 2
    local_ha_min_sync_standbys: Literal[1] = 1
    local_ha_max_replication_delay_bytes: Literal[1048576] = 1048576
    activation_consecutive_capture_count: Literal[3] = 3
    activation_total_lag_floor_records: Literal[7000] = 7000
    activation_lag_slope_floor_records_per_second: Literal[100.0] = 100.0

    def single_bundle_policy(self) -> EvaluationPolicy:
        values = self.model_dump(mode="python")
        values["policy_version"] = "local-ha.conditions.v1"
        return EvaluationPolicy.model_validate(
            {
                name: values[name]
                for name in EvaluationPolicy.model_fields
            }
        )


class SequenceSourceBundleReference(FrozenModel):
    sequence_index: int = Field(ge=0)
    schema_version: Literal["ops.evidence.v1"]
    bundle_id: str
    incident_id: str
    cluster_profile: str
    collection_status: CollectionStatus
    collection_started_at: datetime
    collection_completed_at: datetime
    kafka_source_timestamp: datetime | None = None
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_timestamps(self) -> "SequenceSourceBundleReference":
        if self.collection_started_at.utcoffset() is None:
            raise ValueError("sequence collection timestamps must be timezone-aware")
        if self.collection_completed_at.utcoffset() is None:
            raise ValueError("sequence collection timestamps must be timezone-aware")
        if self.collection_started_at > self.collection_completed_at:
            raise ValueError("sequence collection start must not exceed completion")
        if (
            self.kafka_source_timestamp is not None
            and self.kafka_source_timestamp.utcoffset() is None
        ):
            raise ValueError("Kafka source timestamp must be timezone-aware")
        return self


class SequenceCaptureObservation(FrozenModel):
    sequence_index: int = Field(ge=0)
    bundle_id: str
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_evidence_usable: bool
    core_single_bundle_state: ConditionState | None = None
    reason_codes: list[str] = Field(default_factory=list)
    source_identity_sha256: str | None = Field(
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
    meets_lag_floor: bool | None = None
    meets_slope_floor: bool | None = None
    worker_context: dict[str, Any] = Field(default_factory=dict)
    keda_context: dict[str, Any] = Field(default_factory=dict)
    worker_stage_latency_context: dict[str, Any] = Field(default_factory=dict)


def sequence_evaluation_id(
    *,
    evaluator_version: str,
    ruleset_version: str,
    policy: SequenceEvaluationPolicy | dict[str, Any],
    source_bundles: list[SequenceSourceBundleReference] | list[dict[str, Any]],
    capture_observations: list[SequenceCaptureObservation] | list[dict[str, Any]],
    conditions: dict[ConditionName, ConditionResult] | dict[str, Any],
    assessments: dict[AssessmentName, AssessmentResult] | dict[str, Any],
) -> str:
    def dumped(value: Any) -> Any:
        return value.model_dump(mode="json") if isinstance(value, BaseModel) else value

    return canonical_sha256(
        {
            "evaluator_version": evaluator_version,
            "ruleset_version": ruleset_version,
            "policy": dumped(policy),
            "source_bundles": [dumped(value) for value in source_bundles],
            "capture_observations": [
                dumped(value) for value in capture_observations
            ],
            "conditions": {
                (key.value if isinstance(key, Enum) else key): dumped(value)
                for key, value in conditions.items()
            },
            "assessments": {
                (key.value if isinstance(key, Enum) else key): dumped(value)
                for key, value in assessments.items()
            },
        }
    )


class SequenceConditionEvaluation(FrozenModel):
    schema_version: Literal["ops.conditions.v2"] = "ops.conditions.v2"
    evaluator_version: Literal["ops.evaluator.v2"] = "ops.evaluator.v2"
    ruleset_version: Literal["ops.conditions.rules.v2"] = (
        "ops.conditions.rules.v2"
    )
    evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_status: EvaluationStatus
    policy: SequenceEvaluationPolicy
    source_bundles: list[SequenceSourceBundleReference] = Field(
        min_length=1,
        max_length=256,
    )
    capture_observations: list[SequenceCaptureObservation] = Field(
        min_length=1,
        max_length=256,
    )
    conditions: dict[ConditionName, ConditionResult]
    assessments: dict[AssessmentName, AssessmentResult]

    def _computed_evaluation_id(self) -> str:
        return sequence_evaluation_id(
            evaluator_version=self.evaluator_version,
            ruleset_version=self.ruleset_version,
            policy=self.policy,
            source_bundles=self.source_bundles,
            capture_observations=self.capture_observations,
            conditions=self.conditions,
            assessments=self.assessments,
        )

    def verify_integrity(self) -> None:
        if self.evaluation_id != self._computed_evaluation_id():
            raise ValueError("evaluation_id does not match the sequence payload")

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.verify_integrity()
        return super().model_dump(*args, **kwargs)

    @model_validator(mode="after")
    def validate_output_contract(self) -> "SequenceConditionEvaluation":
        expected_indexes = list(range(len(self.source_bundles)))
        if [item.sequence_index for item in self.source_bundles] != expected_indexes:
            raise ValueError("source bundles must retain their ordered sequence indexes")
        if [item.sequence_index for item in self.capture_observations] != expected_indexes:
            raise ValueError("capture observations must retain source sequence order")
        for source, capture in zip(
            self.source_bundles,
            self.capture_observations,
        ):
            if source.bundle_id != capture.bundle_id:
                raise ValueError("capture observation bundle identity mismatch")
            if source.source_bundle_sha256 != capture.source_bundle_sha256:
                raise ValueError("capture observation bundle digest mismatch")

        if set(self.conditions) != set(ConditionName):
            raise ValueError("condition map must contain the exact condition set")
        if set(self.assessments) != set(AssessmentName):
            raise ValueError("assessment map must contain the exact assessment set")
        if any(key != value.condition for key, value in self.conditions.items()):
            raise ValueError("condition map keys must match condition payload names")
        if any(key != value.assessment for key, value in self.assessments.items()):
            raise ValueError("assessment map keys must match assessment payload names")

        has_unknown = any(
            result.state == ConditionState.UNKNOWN
            for result in [*self.conditions.values(), *self.assessments.values()]
        )
        expected_status = (
            EvaluationStatus.PARTIAL if has_unknown else EvaluationStatus.COMPLETE
        )
        if self.evaluation_status != expected_status:
            raise ValueError(
                "evaluation status must be PARTIAL exactly when a result is UNKNOWN"
            )

        assessment = self.assessments[
            AssessmentName.NO_BACKLOG_PRESSURE_DETECTED
        ]
        dependencies = {
            dependency.condition: dependency
            for dependency in assessment.condition_dependencies
        }
        expected_dependencies = {
            ConditionName.CORE_BACKLOG_PRESSURE,
            ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED,
        }
        if (
            len(dependencies) != len(assessment.condition_dependencies)
            or set(dependencies) != expected_dependencies
        ):
            raise ValueError("assessment must contain its exact condition dependencies")
        for condition_name, dependency in dependencies.items():
            condition = self.conditions[condition_name]
            if dependency.state != condition.state:
                raise ValueError("assessment dependency state must match condition state")
            if dependency.reason_codes != condition.reason_codes:
                raise ValueError("assessment dependency reasons must match condition reasons")
        dependency_states = {item.state for item in dependencies.values()}
        if dependency_states == {ConditionState.ABSENT}:
            expected_assessment = ConditionState.PRESENT
        elif ConditionState.PRESENT in dependency_states:
            expected_assessment = ConditionState.ABSENT
        else:
            expected_assessment = ConditionState.UNKNOWN
        if assessment.state != expected_assessment:
            raise ValueError("assessment state must match deterministic condition logic")
        self.verify_integrity()
        return self


__all__ = [
    "SequenceCaptureObservation",
    "SequenceConditionEvaluation",
    "SequenceEvaluationPolicy",
    "SequenceSourceBundleReference",
    "sequence_evaluation_id",
]
