"""Versioned acquisition and output contracts for diagnosis investigations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from ops_agent.diagnosis_models import (
    DiagnosisEvidenceStatus,
    DiagnosisFreshness,
    DiagnosisStep,
    DiagnosisStopReason,
    DiagnosisUsage,
    DiagnosisValidation,
    DiagnosisValidationAttempt,
    HypothesisSupportStatus,
)
from ops_agent.evaluation_models import ConditionState, FrozenModel, canonical_sha256


class DiagnosisAcquisitionMode(str, Enum):
    FROZEN_PROJECTED = "FROZEN_PROJECTED"
    CONTROLLED_SCENARIO = "CONTROLLED_SCENARIO"
    LIVE_READ_ONLY = "LIVE_READ_ONLY"


class HypothesisNameV2(str, Enum):
    HOT_KEY_SUSPECTED = "HOT_KEY_SUSPECTED"
    WORKER_PATH_PRESSURE_SUSPECTED = "WORKER_PATH_PRESSURE_SUSPECTED"
    WORKER_CAPACITY_SHORTFALL_SUSPECTED = "WORKER_CAPACITY_SHORTFALL_SUSPECTED"
    POSTGRES_PATH_DEGRADED_SUSPECTED = "POSTGRES_PATH_DEGRADED_SUSPECTED"
    POISON_RECORD_RETRY_SUSPECTED = "POISON_RECORD_RETRY_SUSPECTED"
    SEQUENCE_CONTENTION_SUSPECTED = "SEQUENCE_CONTENTION_SUSPECTED"
    CONSUMER_REBALANCE_SUSPECTED = "CONSUMER_REBALANCE_SUSPECTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class FrozenProjectedProvenance(FrozenModel):
    acquisition_mode: Literal[DiagnosisAcquisitionMode.FROZEN_PROJECTED]
    source_bundle_digests: list[str] = Field(min_length=1, max_length=256)
    source_evidence_ids: list[str] = Field(default_factory=list, max_length=512)


class ControlledScenarioProvenance(FrozenModel):
    acquisition_mode: Literal[DiagnosisAcquisitionMode.CONTROLLED_SCENARIO]
    fixture_id: str = Field(min_length=1, max_length=128)
    fixture_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario_contract_version: Literal["ops.diagnosis.scenario.v1"] = (
        "ops.diagnosis.scenario.v1"
    )


class LiveReadOnlyProvenance(FrozenModel):
    acquisition_mode: Literal[DiagnosisAcquisitionMode.LIVE_READ_ONLY]
    source_identity: str = Field(min_length=1, max_length=256)
    query_contract_version: str = Field(min_length=1, max_length=128)
    requested_at: datetime
    source_timestamp: datetime | None = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> "LiveReadOnlyProvenance":
        if self.requested_at.utcoffset() is None:
            raise ValueError("live acquisition requested_at must be timezone-aware")
        if self.source_timestamp is not None and self.source_timestamp.utcoffset() is None:
            raise ValueError("live acquisition source_timestamp must be timezone-aware")
        return self


DiagnosisEvidenceProvenance = Annotated[
    FrozenProjectedProvenance
    | ControlledScenarioProvenance
    | LiveReadOnlyProvenance,
    Field(discriminator="acquisition_mode"),
]


class DiagnosisEvidenceV2(FrozenModel):
    evidence_id: str = Field(min_length=1, max_length=256)
    tool_id: str = Field(min_length=1, max_length=128)
    status: DiagnosisEvidenceStatus
    observed_at: datetime
    freshness: DiagnosisFreshness
    semantic_type: str = Field(min_length=1, max_length=256)
    summary: dict[str, Any]
    provenance: DiagnosisEvidenceProvenance
    error_code: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_evidence(self) -> "DiagnosisEvidenceV2":
        if self.observed_at.utcoffset() is None:
            raise ValueError("diagnosis evidence timestamps must be timezone-aware")
        failed = self.status in {
            DiagnosisEvidenceStatus.ERROR,
            DiagnosisEvidenceStatus.UNAVAILABLE,
        }
        if failed != (self.error_code is not None):
            raise ValueError("ERROR or UNAVAILABLE evidence requires only an error_code")
        return self


class HypothesisResultV2(FrozenModel):
    hypothesis: HypothesisNameV2
    support_status: HypothesisSupportStatus
    reason_codes: list[str] = Field(min_length=1, max_length=16)
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=128)
    conflicting_evidence_ids: list[str] = Field(default_factory=list, max_length=128)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=32)


class AgentDecisionV2(FrozenModel):
    hypotheses: list[HypothesisResultV2] = Field(min_length=1, max_length=8)
    stop_reason: DiagnosisStopReason

    @model_validator(mode="after")
    def unique_hypotheses(self) -> "AgentDecisionV2":
        names = [item.hypothesis for item in self.hypotheses]
        if len(names) != len(set(names)):
            raise ValueError("hypothesis names must be unique")
        return self


class DiagnosisPolicyV2(FrozenModel):
    policy_version: Literal["local-ha.diagnosis.v2"] = "local-ha.diagnosis.v2"
    required_condition_schema: Literal["ops.conditions.v2"] = "ops.conditions.v2"
    required_condition: Literal["CORE_BACKLOG_PRESSURE"] = "CORE_BACKLOG_PRESSURE"
    required_state: Literal["PRESENT"] = "PRESENT"
    tool_registry_version: Literal["ops.diagnosis.tools.v2"] = "ops.diagnosis.tools.v2"
    agent_version: Literal["ops.diagnosis.agent.v2"] = "ops.diagnosis.agent.v2"
    model: str = Field(min_length=1, max_length=128)
    model_mode: Literal["recorded", "live"] = "recorded"
    reasoning_effort: Literal["low"] = "low"
    max_steps: int = Field(default=4, ge=1, le=8)
    max_tool_calls: int = Field(default=4, ge=1, le=8)
    max_output_tokens: int = Field(default=1600, ge=256, le=4096)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=60)
    max_retries: int = Field(default=1, ge=0, le=2)
    max_output_repairs: int = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def validate_budgets(self) -> "DiagnosisPolicyV2":
        if self.max_tool_calls > self.max_steps:
            raise ValueError("max_tool_calls must not exceed max_steps")
        return self


class DiagnosisContextV2(FrozenModel):
    profile: str = Field(min_length=1, max_length=64)
    cluster_context: str = Field(min_length=1, max_length=256)
    namespace: str = Field(min_length=1, max_length=256)
    topic: str = Field(min_length=1, max_length=256)
    consumer_group: str = Field(min_length=1, max_length=256)
    activation_source_bundle_digests: list[str] = Field(min_length=1, max_length=256)


class BranchEvaluation(FrozenModel):
    after_tool_id: str = Field(min_length=1, max_length=128)
    expected_next_tools: list[str] = Field(default_factory=list, max_length=16)
    selected_next_tool: str | None = Field(default=None, max_length=128)
    result: Literal["PASS", "FAIL", "NOT_EVALUATED"]


class DiagnosisRunV2(FrozenModel):
    schema_version: Literal["ops.diagnosis.v2"] = "ops.diagnosis.v2"
    diagnosis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    incident_id: str = Field(min_length=1, max_length=256)
    condition_evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    investigation_session_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    acquisition_mode: DiagnosisAcquisitionMode
    context: DiagnosisContextV2
    policy: DiagnosisPolicyV2
    input_conditions: dict[str, ConditionState]
    initial_evidence_ids: list[str] = Field(default_factory=list, max_length=1024)
    additional_evidence: list[DiagnosisEvidenceV2] = Field(default_factory=list, max_length=16)
    steps: list[DiagnosisStep] = Field(default_factory=list, max_length=8)
    hypotheses: list[HypothesisResultV2] = Field(min_length=1, max_length=8)
    stop_reason: DiagnosisStopReason
    steps_used: int = Field(ge=0, le=8)
    started_at: datetime
    completed_at: datetime
    model_response_ids: list[str] = Field(default_factory=list, max_length=10)
    model_turns: int = Field(ge=1, le=10)
    usage: DiagnosisUsage
    validation: DiagnosisValidation
    output_repairs_used: int = Field(ge=0, le=1)
    validation_attempts: list[DiagnosisValidationAttempt] = Field(min_length=1, max_length=2)
    branch_evaluations: list[BranchEvaluation] = Field(default_factory=list, max_length=8)

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "condition_evaluation_id": self.condition_evaluation_id,
            "investigation_session_id": self.investigation_session_id,
            "acquisition_mode": self.acquisition_mode.value,
            "context": self.context.model_dump(mode="json"),
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
            "branch_evaluations": [
                item.model_dump(mode="json") for item in self.branch_evaluations
            ],
        }

    def verify_integrity(self) -> None:
        if self.diagnosis_id != canonical_sha256(self._identity_payload()):
            raise ValueError("diagnosis_id does not match the diagnosis payload")

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.verify_integrity()
        return super().model_dump(*args, **kwargs)

    @model_validator(mode="after")
    def validate_run(self) -> "DiagnosisRunV2":
        if self.started_at.utcoffset() is None or self.completed_at.utcoffset() is None:
            raise ValueError("diagnosis timestamps must be timezone-aware")
        if self.started_at > self.completed_at:
            raise ValueError("diagnosis start must not exceed completion")
        if self.steps_used != len(self.steps):
            raise ValueError("steps_used must match recorded steps")
        if self.steps_used > self.policy.max_steps or len(self.steps) > self.policy.max_tool_calls:
            raise ValueError("diagnosis exceeded the configured budget")
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
        if len(self.model_response_ids) != self.model_turns:
            raise ValueError("model response IDs must match model turns")
        expected_api_requests = self.model_turns if self.policy.model_mode == "live" else 0
        if self.usage.api_requests != expected_api_requests:
            raise ValueError("API request count does not match model mode")
        if self.input_conditions != {"CORE_BACKLOG_PRESSURE": ConditionState.PRESENT}:
            raise ValueError("diagnosis must preserve the deterministic PRESENT input")
        if [item.step for item in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("diagnosis steps must be consecutive")
        returned = [value for step in self.steps for value in step.returned_evidence_ids]
        if returned != [item.evidence_id for item in self.additional_evidence]:
            raise ValueError("diagnosis step trace must match additional evidence")
        if any(
            item.provenance.acquisition_mode != self.acquisition_mode
            for item in self.additional_evidence
        ):
            raise ValueError("diagnosis evidence acquisition mode mismatch")
        self.verify_integrity()
        return self


__all__ = [
    "AgentDecisionV2",
    "BranchEvaluation",
    "ControlledScenarioProvenance",
    "DiagnosisAcquisitionMode",
    "DiagnosisContextV2",
    "DiagnosisEvidenceProvenance",
    "DiagnosisEvidenceV2",
    "DiagnosisPolicyV2",
    "DiagnosisRunV2",
    "FrozenProjectedProvenance",
    "HypothesisNameV2",
    "HypothesisResultV2",
    "LiveReadOnlyProvenance",
]
