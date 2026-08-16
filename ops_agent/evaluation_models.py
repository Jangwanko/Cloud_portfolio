"""Versioned models for deterministic condition evaluation."""

from __future__ import annotations

from enum import Enum
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ops_agent.models import CollectionStatus, EvidenceStatus, FreshnessStatus


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConditionState(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class EvaluationStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"


class DependencyRequirement(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


class ConditionName(str, Enum):
    CORE_BACKLOG_PRESSURE = "CORE_BACKLOG_PRESSURE"
    PARTITION_LAG_CONCENTRATION_OBSERVED = (
        "PARTITION_LAG_CONCENTRATION_OBSERVED"
    )
    DB_DEGRADED = "DB_DEGRADED"
    WORKER_REPLICA_UNAVAILABLE = "WORKER_REPLICA_UNAVAILABLE"


class AssessmentName(str, Enum):
    NO_BACKLOG_PRESSURE_DETECTED = "NO_BACKLOG_PRESSURE_DETECTED"


class EvaluationPolicy(FrozenModel):
    """Immutable evidence contract used by the local-ha v1 ruleset."""

    cluster_profile: Literal["local-ha"] = "local-ha"
    source_policy_version: Literal["local-ha.evidence.v1"] = (
        "local-ha.evidence.v1"
    )
    policy_version: Literal["local-ha.conditions.v1"] = "local-ha.conditions.v1"
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


class EvidenceIssue(FrozenModel):
    code: str
    evidence_id: str | None = None
    detail: str | None = None


class EvidenceDependencyTrace(FrozenModel):
    dependency: str
    requirement: DependencyRequirement
    accepted_metric_names: list[str]
    evidence_ids: list[str] = Field(default_factory=list)
    usable_evidence_ids: list[str] = Field(default_factory=list)
    evidence_statuses: dict[str, EvidenceStatus] = Field(default_factory=dict)
    freshness_statuses: dict[str, FreshnessStatus] = Field(default_factory=dict)
    missing: bool = False
    missing_evidence_ids: list[str] = Field(default_factory=list)
    stale_evidence_ids: list[str] = Field(default_factory=list)
    unknown_freshness_evidence_ids: list[str] = Field(default_factory=list)
    coverage_incomplete_evidence_ids: list[str] = Field(default_factory=list)
    semantic_anomaly_evidence_ids: list[str] = Field(default_factory=list)
    issues: list[EvidenceIssue] = Field(default_factory=list)


class ConditionResult(FrozenModel):
    condition: ConditionName
    state: ConditionState
    reason_codes: list[str]
    evidence_ids: list[str] = Field(default_factory=list)
    required_evidence: list[EvidenceDependencyTrace]
    optional_evidence: list[EvidenceDependencyTrace]
    missing_required_dependencies: list[str] = Field(default_factory=list)
    stale_required_evidence_ids: list[str] = Field(default_factory=list)
    unknown_freshness_required_evidence_ids: list[str] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)


class ConditionDependencyTrace(FrozenModel):
    condition: ConditionName
    state: ConditionState
    reason_codes: list[str]


class AssessmentResult(FrozenModel):
    assessment: AssessmentName
    state: ConditionState
    reason_codes: list[str]
    condition_dependencies: list[ConditionDependencyTrace]


class SourceBundleReference(FrozenModel):
    schema_version: Literal["ops.evidence.v1"]
    bundle_id: str
    incident_id: str
    cluster_profile: str
    collection_status: CollectionStatus
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def canonical_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def condition_evaluation_id(
    *,
    evaluator_version: str,
    ruleset_version: str,
    policy: EvaluationPolicy | dict[str, Any],
    source_bundle: SourceBundleReference | dict[str, Any],
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
            "source_bundle": dumped(source_bundle),
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


class ConditionEvaluation(FrozenModel):
    schema_version: Literal["ops.conditions.v1"] = "ops.conditions.v1"
    evaluator_version: Literal["ops.evaluator.v1"] = "ops.evaluator.v1"
    ruleset_version: Literal["ops.conditions.rules.v1"] = (
        "ops.conditions.rules.v1"
    )
    evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_status: EvaluationStatus
    policy: EvaluationPolicy
    source_bundle: SourceBundleReference
    conditions: dict[ConditionName, ConditionResult]
    assessments: dict[AssessmentName, AssessmentResult]

    def _computed_evaluation_id(self) -> str:
        return condition_evaluation_id(
            evaluator_version=self.evaluator_version,
            ruleset_version=self.ruleset_version,
            policy=self.policy,
            source_bundle=self.source_bundle,
            conditions=self.conditions,
            assessments=self.assessments,
        )

    def verify_integrity(self) -> None:
        if self.evaluation_id != self._computed_evaluation_id():
            raise ValueError("evaluation_id does not match the evaluation payload")

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.verify_integrity()
        return super().model_dump(*args, **kwargs)

    @model_validator(mode="after")
    def validate_output_contract(self) -> "ConditionEvaluation":
        if set(self.conditions) != set(ConditionName):
            raise ValueError("condition map must contain the exact condition set")
        if set(self.assessments) != set(AssessmentName):
            raise ValueError("assessment map must contain the exact assessment set")
        if any(key != result.condition for key, result in self.conditions.items()):
            raise ValueError("condition map keys must match condition payload names")
        if any(key != result.assessment for key, result in self.assessments.items()):
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

        expected_dependencies = {
            AssessmentName.NO_BACKLOG_PRESSURE_DETECTED: {
                ConditionName.CORE_BACKLOG_PRESSURE,
                ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED,
            }
        }
        for assessment_name, assessment in self.assessments.items():
            dependencies = {
                dependency.condition: dependency
                for dependency in assessment.condition_dependencies
            }
            if len(dependencies) != len(assessment.condition_dependencies):
                raise ValueError("assessment condition dependencies must be unique")
            if set(dependencies) != expected_dependencies[assessment_name]:
                raise ValueError(
                    "assessment must contain the exact condition dependency set"
                )
            for condition_name, dependency in dependencies.items():
                condition = self.conditions[condition_name]
                if dependency.state != condition.state:
                    raise ValueError(
                        "assessment dependency state must match condition state"
                    )
                if dependency.reason_codes != condition.reason_codes:
                    raise ValueError(
                        "assessment dependency reasons must match condition reasons"
                    )
            dependency_states = {
                dependency.state for dependency in dependencies.values()
            }
            if dependency_states == {ConditionState.ABSENT}:
                expected_assessment_state = ConditionState.PRESENT
            elif ConditionState.PRESENT in dependency_states:
                expected_assessment_state = ConditionState.ABSENT
            else:
                expected_assessment_state = ConditionState.UNKNOWN
            if assessment.state != expected_assessment_state:
                raise ValueError(
                    "assessment state must match its deterministic condition logic"
                )
        self.verify_integrity()
        return self
