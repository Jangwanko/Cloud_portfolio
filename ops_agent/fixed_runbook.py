"""Fixed, four-call offline checklist. No scenario answers, model, or runtime I/O."""

from datetime import datetime, timezone

from ops_agent.diagnosis_models import (
    DiagnosisStep, DiagnosisUsage, DiagnosisValidationAttempt,
)
from ops_agent.diagnosis_scenarios import DiagnosisEvidenceRegistry
from ops_agent.diagnosis_tools import TOOL_BY_ID
from ops_agent.diagnosis_v2_models import (
    AgentDecisionV2, DiagnosisContextV2, DiagnosisPolicyV2, DiagnosisRunV2,
)
from ops_agent.diagnosis_v2_validator import DiagnosisOutputValidatorV2
from ops_agent.evaluation_models import canonical_sha256


RUNBOOK_VERSION = "fixed-checklist.v1"
TOOL_ORDER = (
    "get_worker_stage_latency", "get_postgres_health",
    "get_worker_replica_status", "get_keda_status",
)


def decide(evidence):
    """Rules consume only acquired normalized evidence, never an expected label."""
    by_tool = {item.tool_id: item for item in evidence}

    def usable(tool):
        item = by_tool.get(tool)
        return item is not None and item.status.value == "OK" and item.freshness.status.value == "FRESH"

    def flag(tool):
        return by_tool[tool].summary.get("semantic_flag")

    hypothesis = None
    supporting = []
    if usable("get_worker_stage_latency") and usable("get_postgres_health"):
        stage = flag("get_worker_stage_latency")
        postgres = flag("get_postgres_health")
        if stage == "ABOVE_SCENARIO_BASELINE" and postgres == "POSTGRES_PATH_DEGRADED":
            hypothesis = "POSTGRES_PATH_DEGRADED_SUSPECTED"
            supporting = ["get_worker_stage_latency", "get_postgres_health"]
        elif usable("get_worker_replica_status"):
            replicas = flag("get_worker_replica_status")
            if (stage == "WITHIN_SCENARIO_BASELINE"
                and postgres == "POSTGRES_PATH_HEALTHY"
                and replicas == "WORKER_CAPACITY_SHORTFALL"
                and usable("get_keda_status")
                and by_tool["get_keda_status"].summary.get("active") is True):
                hypothesis = "WORKER_CAPACITY_SHORTFALL_SUSPECTED"
                supporting = ["get_worker_replica_status", "get_keda_status"]
            elif (stage == "ABOVE_SCENARIO_BASELINE"
                  and postgres == "POSTGRES_PATH_HEALTHY"
                  and replicas == "WORKER_CAPACITY_AVAILABLE"):
                hypothesis = "WORKER_PATH_PRESSURE_SUSPECTED"
                supporting = ["get_worker_stage_latency"]
    if hypothesis is None:
        return AgentDecisionV2.model_validate({
            "hypotheses": [{
                "hypothesis": "INSUFFICIENT_EVIDENCE", "support_status": "INSUFFICIENT",
                "reason_codes": ["FIXED_CHECKLIST_NO_SUFFICIENT_RULE"],
                "evidence_gaps": ["MISSING_OR_CONFLICTING_REQUIRED_OBSERVATIONS"],
            }],
            "stop_reason": "insufficient_evidence",
        })
    return AgentDecisionV2.model_validate({
        "hypotheses": [{
            "hypothesis": hypothesis, "support_status": "SUPPORTED",
            "reason_codes": ["FIXED_CHECKLIST_OBSERVATION_RULE"],
            "supporting_evidence_ids": [by_tool[tool].evidence_id for tool in supporting],
        }],
        "stop_reason": "sufficient_evidence",
    })


def run_fixed_runbook(*, activation: dict, registry: DiagnosisEvidenceRegistry,
                      max_tool_calls: int = 4) -> DiagnosisRunV2:
    if max_tool_calls != len(TOOL_ORDER):
        raise ValueError("fixed-checklist.v1 requires the shared four-call budget")
    started = datetime.now(timezone.utc)
    evidence, steps = [], []
    for tool in TOOL_ORDER:
        requested = datetime.now(timezone.utc)
        item = registry.execute(tool)
        evidence.append(item)
        steps.append(DiagnosisStep(
            step=len(steps) + 1, tool_id=tool, reason_code=TOOL_BY_ID[tool].reason_code,
            requested_at=requested, returned_evidence_ids=[item.evidence_id],
        ))
    decision = decide(evidence)
    policy = DiagnosisPolicyV2(
        model=RUNBOOK_VERSION, model_mode="recorded", max_steps=4,
        max_tool_calls=4, max_retries=0, max_output_repairs=0,
    )
    validation = DiagnosisOutputValidatorV2(allowlisted_tool_ids=set(registry.tool_ids)).validate(
        decision=decision, policy=policy, initial_evidence_ids=activation["evidence_ids"],
        additional_evidence=evidence, steps=steps,
    )
    context = DiagnosisContextV2(
        **{key: activation[key] for key in (
            "profile", "cluster_context", "namespace", "topic", "consumer_group"
        )}, activation_source_bundle_digests=activation["source_bundle_digests"],
    )
    # Adapt the deterministic checklist to the existing recorded-run contract.
    # One synthetic decision turn is not a model invocation; all usage stays zero.
    response_id = "fixed-checklist-decision"
    payload = {
        "condition_evaluation_id": activation["condition_evaluation_id"],
        "investigation_session_id": canonical_sha256({
            "runbook": RUNBOOK_VERSION, "started_at": started.isoformat(),
            "evidence_ids": [item.evidence_id for item in evidence],
        }),
        "acquisition_mode": "CONTROLLED_SCENARIO",
        "context": context.model_dump(mode="json"), "policy": policy.model_dump(mode="json"),
        "initial_evidence_ids": activation["evidence_ids"],
        "additional_evidence": [item.model_dump(mode="json") for item in evidence],
        "steps": [{key: step.model_dump(mode="json")[key] for key in (
            "step", "tool_id", "reason_code", "returned_evidence_ids"
        )} for step in steps],
        "hypotheses": [item.model_dump(mode="json") for item in decision.hypotheses],
        "stop_reason": decision.stop_reason.value, "branch_evaluations": [],
    }
    return DiagnosisRunV2(
        **{key: value for key, value in payload.items() if key != "steps"},
        diagnosis_id=canonical_sha256(payload), incident_id=activation["incident_id"],
        input_conditions={"CORE_BACKLOG_PRESSURE": "PRESENT"}, steps=steps,
        steps_used=len(steps), started_at=started, completed_at=datetime.now(timezone.utc),
        model_response_ids=[response_id], model_turns=1, usage=DiagnosisUsage(),
        validation=validation, output_repairs_used=0,
        validation_attempts=[DiagnosisValidationAttempt(
            attempt=1, phase="initial", result="VALID", response_id=response_id,
        )],
    )
