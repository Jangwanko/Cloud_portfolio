"""Strongly typed Phase 3 diagnosis artifacts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from ops_agent.evaluation_models import ConditionState, FrozenModel, canonical_sha256
from ops_agent.models import EvidenceStatus, FreshnessStatus


class HypothesisName(str, Enum):
    HOT_KEY_SUSPECTED = "HOT_KEY_SUSPECTED"
    WORKER_PATH_PRESSURE_SUSPECTED = "WORKER_PATH_PRESSURE_SUSPECTED"
    POISON_RECORD_RETRY_SUSPECTED = "POISON_RECORD_RETRY_SUSPECTED"
    SEQUENCE_CONTENTION_SUSPECTED = "SEQUENCE_CONTENTION_SUSPECTED"
    CONSUMER_REBALANCE_SUSPECTED = "CONSUMER_REBALANCE_SUSPECTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class HypothesisSupportStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTED = "CONFLICTED"


class DiagnosisStopReason(str, Enum):
    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_USEFUL_TOOL_REMAINING = "no_useful_tool_remaining"
    STEP_BUDGET_EXHAUSTED = "step_budget_exhausted"
    TOOL_ERROR = "tool_error"
    VALIDATION_FAILURE = "validation_failure"


class DiagnosisEvidenceStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    ERROR = "ERROR"
    UNAVAILABLE = "UNAVAILABLE"


class DiagnosisPolicy(FrozenModel):
    policy_version: Literal["local-ha.diagnosis.v1"] = "local-ha.diagnosis.v1"
    required_condition_schema: Literal["ops.conditions.v2"] = "ops.conditions.v2"
    required_condition: Literal["CORE_BACKLOG_PRESSURE"] = "CORE_BACKLOG_PRESSURE"
    required_state: Literal["PRESENT"] = "PRESENT"
    tool_registry_version: Literal["ops.diagnosis.tools.v1"] = "ops.diagnosis.tools.v1"
    agent_version: Literal["ops.diagnosis.agent.v1"] = "ops.diagnosis.agent.v1"
    model: str = Field(min_length=1, max_length=128)
    reasoning_effort: Literal["low"] = "low"
    max_steps: int = Field(default=4, ge=1, le=8)
    max_tool_calls: int = Field(default=4, ge=1, le=8)
    max_output_tokens: int = Field(default=1600, ge=256, le=4096)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=60)
    max_retries: int = Field(default=1, ge=0, le=2)
    max_output_repairs: int = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def validate_budgets(self) -> "DiagnosisPolicy":
        if self.max_tool_calls > self.max_steps:
            raise ValueError("max_tool_calls must not exceed max_steps")
        return self


class DiagnosisContext(FrozenModel):
    profile: str = Field(min_length=1, max_length=64)
    cluster_context: str = Field(min_length=1, max_length=256)
    namespace: str = Field(min_length=1, max_length=256)
    topic: str = Field(min_length=1, max_length=256)
    consumer_group: str = Field(min_length=1, max_length=256)
    source_bundle_digests: list[str] = Field(min_length=1, max_length=256)


class DiagnosisFreshness(FrozenModel):
    status: FreshnessStatus
    oldest_source_timestamp: datetime | None = None
    newest_source_timestamp: datetime | None = None
    max_age_seconds: float | None = Field(default=None, gt=0)


class DiagnosisEvidence(FrozenModel):
    evidence_id: str = Field(min_length=1, max_length=256)
    tool_id: str = Field(min_length=1, max_length=128)
    status: DiagnosisEvidenceStatus
    observed_at: datetime
    freshness: DiagnosisFreshness
    source_evidence_ids: list[str] = Field(default_factory=list, max_length=512)
    source_bundle_digests: list[str] = Field(min_length=1, max_length=256)
    semantic_type: str = Field(min_length=1, max_length=256)
    summary: dict[str, Any]
    error_code: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_time(self) -> "DiagnosisEvidence":
        if self.observed_at.utcoffset() is None:
            raise ValueError("diagnosis evidence timestamps must be timezone-aware")
        return self


class DiagnosisStep(FrozenModel):
    step: int = Field(ge=1, le=8)
    tool_id: str = Field(min_length=1, max_length=128)
    reason_code: str = Field(min_length=1, max_length=128)
    requested_at: datetime
    returned_evidence_ids: list[str] = Field(min_length=1, max_length=16)


class HypothesisResult(FrozenModel):
    hypothesis: HypothesisName
    support_status: HypothesisSupportStatus
    reason_codes: list[str] = Field(min_length=1, max_length=16)
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=128)
    conflicting_evidence_ids: list[str] = Field(default_factory=list, max_length=128)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=32)


class AgentDecision(FrozenModel):
    hypotheses: list[HypothesisResult] = Field(min_length=1, max_length=6)
    stop_reason: DiagnosisStopReason

    @model_validator(mode="after")
    def unique_hypotheses(self) -> "AgentDecision":
        names = [item.hypothesis for item in self.hypotheses]
        if len(names) != len(set(names)):
            raise ValueError("hypothesis names must be unique")
        return self


class DiagnosisUsage(FrozenModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    api_requests: int = Field(default=0, ge=0)


class DiagnosisValidation(FrozenModel):
    schema_valid: bool
    citations_valid: bool
    tool_calls_valid: bool
    step_budget_valid: bool
    stop_valid: bool
    forbidden_claims_absent: bool


class DiagnosisValidationAttempt(FrozenModel):
    attempt: int = Field(ge=1, le=2)
    phase: Literal["initial", "repair"]
    result: Literal["VALID", "INVALID"]
    response_id: str = Field(min_length=1, max_length=256)
    error_code: str | None = Field(default=None, min_length=1, max_length=128)
    error_message: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_error(self) -> "DiagnosisValidationAttempt":
        has_error = self.error_code is not None or self.error_message is not None
        if self.result == "VALID" and has_error:
            raise ValueError("valid validation attempts cannot contain an error")
        if self.result == "INVALID" and not (
            self.error_code is not None and self.error_message is not None
        ):
            raise ValueError("invalid validation attempts require an error")
        return self


def diagnosis_id(payload: dict[str, Any]) -> str:
    return canonical_sha256(payload)


class DiagnosisRun(FrozenModel):
    schema_version: Literal["ops.diagnosis.v1"] = "ops.diagnosis.v1"
    diagnosis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    incident_id: str = Field(min_length=1, max_length=256)
    condition_evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    context: DiagnosisContext
    policy: DiagnosisPolicy
    input_conditions: dict[str, ConditionState]
    initial_evidence_ids: list[str] = Field(default_factory=list, max_length=1024)
    additional_evidence: list[DiagnosisEvidence] = Field(default_factory=list, max_length=16)
    steps: list[DiagnosisStep] = Field(default_factory=list, max_length=8)
    hypotheses: list[HypothesisResult] = Field(min_length=1, max_length=6)
    stop_reason: DiagnosisStopReason
    steps_used: int = Field(ge=0, le=8)
    started_at: datetime
    completed_at: datetime
    model_response_ids: list[str] = Field(default_factory=list, max_length=10)
    usage: DiagnosisUsage
    validation: DiagnosisValidation
    output_repairs_used: int = Field(ge=0, le=1)
    validation_attempts: list[DiagnosisValidationAttempt] = Field(
        min_length=1,
        max_length=2,
    )

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "condition_evaluation_id": self.condition_evaluation_id,
            "source_bundle_digests": self.context.source_bundle_digests,
            "policy": self.policy.model_dump(mode="json"),
            "initial_evidence_ids": self.initial_evidence_ids,
            "additional_evidence": [
                item.model_dump(mode="json") for item in self.additional_evidence
            ],
            "steps": [
                {
                    "step": item.step,
                    "tool_id": item.tool_id,
                    "reason_code": item.reason_code,
                    "returned_evidence_ids": item.returned_evidence_ids,
                }
                for item in self.steps
            ],
            "hypotheses": [item.model_dump(mode="json") for item in self.hypotheses],
            "stop_reason": self.stop_reason.value,
        }

    def verify_integrity(self) -> None:
        if self.diagnosis_id != diagnosis_id(self._identity_payload()):
            raise ValueError("diagnosis_id does not match the diagnosis payload")

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.verify_integrity()
        return super().model_dump(*args, **kwargs)

    @model_validator(mode="after")
    def validate_run(self) -> "DiagnosisRun":
        if self.started_at.utcoffset() is None or self.completed_at.utcoffset() is None:
            raise ValueError("diagnosis timestamps must be timezone-aware")
        if self.started_at > self.completed_at:
            raise ValueError("diagnosis start must not exceed completion")
        if self.steps_used != len(self.steps):
            raise ValueError("steps_used must match recorded steps")
        if self.steps_used > self.policy.max_steps:
            raise ValueError("diagnosis exceeded max_steps")
        if len(self.steps) > self.policy.max_tool_calls:
            raise ValueError("diagnosis exceeded max_tool_calls")
        if self.output_repairs_used > self.policy.max_output_repairs:
            raise ValueError("diagnosis exceeded max_output_repairs")
        if len(self.validation_attempts) != self.output_repairs_used + 1:
            raise ValueError("validation attempt count does not match output repairs")
        if [item.attempt for item in self.validation_attempts] != list(
            range(1, len(self.validation_attempts) + 1)
        ):
            raise ValueError("validation attempts must be consecutive")
        expected_phases = ["initial"] + (["repair"] if self.output_repairs_used else [])
        if [item.phase for item in self.validation_attempts] != expected_phases:
            raise ValueError("validation attempt phases do not match output repairs")
        if self.validation_attempts[-1].result != "VALID":
            raise ValueError("completed diagnosis requires final valid output")
        if self.output_repairs_used and self.validation_attempts[0].result != "INVALID":
            raise ValueError("output repair requires an invalid initial result")
        if len(self.model_response_ids) != self.usage.api_requests:
            raise ValueError("model response IDs must match API request count")
        if self.input_conditions != {
            "CORE_BACKLOG_PRESSURE": ConditionState.PRESENT
        }:
            raise ValueError("diagnosis must preserve the deterministic PRESENT input")
        if [item.step for item in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("diagnosis steps must be consecutive")
        returned = [
            evidence_id
            for item in self.steps
            for evidence_id in item.returned_evidence_ids
        ]
        if returned != [item.evidence_id for item in self.additional_evidence]:
            raise ValueError("diagnosis step trace must match additional evidence")
        self.verify_integrity()
        return self


def evidence_status_summary(items: list[EvidenceStatus]) -> DiagnosisEvidenceStatus:
    if not items:
        return DiagnosisEvidenceStatus.MISSING
    if all(item == EvidenceStatus.UNAVAILABLE for item in items):
        return DiagnosisEvidenceStatus.UNAVAILABLE
    if all(item == EvidenceStatus.ERROR for item in items):
        return DiagnosisEvidenceStatus.ERROR
    if all(item == EvidenceStatus.MISSING for item in items):
        return DiagnosisEvidenceStatus.MISSING
    if all(item == EvidenceStatus.OK for item in items):
        return DiagnosisEvidenceStatus.OK
    return DiagnosisEvidenceStatus.PARTIAL


__all__ = [
    "AgentDecision",
    "DiagnosisContext",
    "DiagnosisEvidence",
    "DiagnosisEvidenceStatus",
    "DiagnosisFreshness",
    "DiagnosisPolicy",
    "DiagnosisRun",
    "DiagnosisStep",
    "DiagnosisStopReason",
    "DiagnosisUsage",
    "DiagnosisValidation",
    "DiagnosisValidationAttempt",
    "HypothesisName",
    "HypothesisResult",
    "HypothesisSupportStatus",
    "diagnosis_id",
    "evidence_status_summary",
]
