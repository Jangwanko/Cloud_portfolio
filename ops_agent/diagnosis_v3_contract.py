"""Reviewable instructions/schema for the isolated v3 decision candidate."""

from ops_agent.diagnosis_v3_models import AgentDecisionV3
from ops_agent.evaluation_models import canonical_sha256

PROMPT_VERSION_V3 = "ops.diagnosis.prompt.v3-candidate"
INSTRUCTIONS_V3 = """Report only the required decision JSON for the established
CORE_BACKLOG_PRESSURE=PRESENT incident. The incident condition cannot be changed.
Separate observations directly measured by fresh OK tool evidence from hypotheses
about possible contributing causes. WORKER_STAGE_ELEVATED is an observation, never
a second causal hypothesis. PostgreSQL guardrail observations describe reachability,
replication and HA guardrails, not query or commit latency.
Use context_evidence_ids for general lag, KEDA state and other background information.
Context never substitutes for direct supporting/conflicting evidence of a candidate.
The same measured observation may anchor a relevant candidate, but context and signed
causal citation lists must be separate. A replica shortfall candidate needs the actual
replica deficit; a PostgreSQL guardrail candidate needs the actual guardrail signal.
Supported means evidence-grounded suspicion, not proven root cause. Other causal
telemetry is unavailable: keep those hypotheses INSUFFICIENT with explicit gaps and
no signed citations. Rebalance requires CONSUMER_REBALANCE_TELEMETRY_UNAVAILABLE.
CONFLICTED requires both supporting and conflicting citations. INSUFFICIENT requires
a gap. When no causal candidate has direct support, return ABSTAIN and preserve the
observations and investigation gaps. Do not manufacture a cause to accompany a symptom.
Use only supplied IDs; do not infer missing telemetry is healthy. Do not request actions,
change runtime resources, declare recovery, or include tool commands in this output.
"""


def decision_v3_contract():
    schema = AgentDecisionV3.model_json_schema()
    content = {"instructions": INSTRUCTIONS_V3, "schema": schema}
    return {"classification": "OFFLINE_CONTRACT_CANDIDATE",
            "identity": {"version": PROMPT_VERSION_V3, "sha256": canonical_sha256(content)},
            **content}
