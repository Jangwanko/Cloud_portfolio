"""Candidate decision contract: measured signals, candidate causes and context.

This is an offline candidate, not a replacement for ops.diagnosis.v2 runs.
"""

from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ops_agent.diagnosis_models import HypothesisSupportStatus
from ops_agent.evaluation_models import FrozenModel

EvidenceId = Annotated[str, Field(min_length=1, max_length=256)]
Code = Annotated[str, Field(min_length=1, max_length=128)]


class SignalNameV3(str, Enum):
    WORKER_STAGE_ELEVATED = "WORKER_STAGE_ELEVATED"
    WORKER_REPLICA_SHORTFALL = "WORKER_REPLICA_SHORTFALL"
    POSTGRES_GUARDRAIL_DEGRADED = "POSTGRES_GUARDRAIL_DEGRADED"


class CauseNameV3(str, Enum):
    WORKER_CAPACITY_SHORTFALL_SUSPECTED = "WORKER_CAPACITY_SHORTFALL_SUSPECTED"
    POSTGRES_GUARDRAIL_PRESSURE_SUSPECTED = "POSTGRES_GUARDRAIL_PRESSURE_SUSPECTED"
    HOT_KEY_SUSPECTED = "HOT_KEY_SUSPECTED"
    POISON_RECORD_RETRY_SUSPECTED = "POISON_RECORD_RETRY_SUSPECTED"
    SEQUENCE_CONTENTION_SUSPECTED = "SEQUENCE_CONTENTION_SUSPECTED"
    CONSUMER_REBALANCE_SUSPECTED = "CONSUMER_REBALANCE_SUSPECTED"


class ObservedSignalV3(FrozenModel):
    signal: SignalNameV3
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=16)


class CandidateCauseV3(FrozenModel):
    hypothesis: CauseNameV3
    support_status: HypothesisSupportStatus
    supporting_evidence_ids: list[EvidenceId] = Field(max_length=32)
    conflicting_evidence_ids: list[EvidenceId] = Field(max_length=32)
    evidence_gaps: list[Code] = Field(max_length=16)


class AgentDecisionV3(FrozenModel):
    # All fields are required, including explicitly empty lists.
    schema_version: Literal["ops.diagnosis.decision.v3"]
    input_condition: Literal["CORE_BACKLOG_PRESSURE=PRESENT"]
    observations: list[ObservedSignalV3] = Field(max_length=8)
    hypotheses: list[CandidateCauseV3] = Field(max_length=8)
    context_evidence_ids: list[EvidenceId] = Field(max_length=64)
    investigation_gaps: list[Code] = Field(max_length=32)
    conclusion: Literal["ABSTAIN", "SUPPORTED_CANDIDATES"]

    @model_validator(mode="after")
    def unique_roles(self):
        for values in (
            [item.signal for item in self.observations],
            [item.hypothesis for item in self.hypotheses],
            self.context_evidence_ids,
            *(item.evidence_ids for item in self.observations),
            *(item.supporting_evidence_ids for item in self.hypotheses),
            *(item.conflicting_evidence_ids for item in self.hypotheses),
        ):
            if len(values) != len(set(values)):
                raise ValueError("duplicate signals, hypotheses or citation IDs")
        signed = set()
        for item in self.hypotheses:
            if set(item.supporting_evidence_ids) & set(item.conflicting_evidence_ids):
                raise ValueError("same citation cannot support and conflict with one hypothesis")
            signed.update(item.supporting_evidence_ids)
            signed.update(item.conflicting_evidence_ids)
        if signed & set(self.context_evidence_ids):
            raise ValueError("context and signed causal citations must be separate")
        return self
