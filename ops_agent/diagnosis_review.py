"""Post-hoc review dimensions alongside the unchanged v1 quality gate."""

from collections import Counter

from ops_agent.diagnosis_quality import score_attempt
from ops_agent.diagnosis_v2_models import DiagnosisRunV2


REVIEW_VERSION = "ops.diagnosis.review.v1"


def review_attempt(attempt, *, catalog, rubric):
    legacy = score_attempt(attempt, catalog=catalog, rubric=rubric)
    result = {
        "attempt_id": attempt.attempt_id, "fixture_id": attempt.fixture_id,
        "legacy_score": legacy,
        "scope": "CONTROLLED_FIXTURE_LABEL_ALIGNMENT_NOT_CAUSAL_TRUTH",
        "dimensions": {key: "NOT_EVALUATED" for key in (
            "fixture_label_alignment", "investigation_completeness",
            "abstention", "citation_relations",
        )},
        "citation_reviews": [], "review_flags": [],
    }
    if not legacy["contract_pass"]:
        result["review_flags"] = ["OUTPUT_CONTRACT_FAILED"]
        return result
    checks = legacy["checks"]
    result["dimensions"].update({
        "fixture_label_alignment": "PASS" if checks["supported_hypotheses_match"] else "FAIL",
        "investigation_completeness": "PASS" if checks["required_evidence_acquired"] else "FAIL",
        "abstention": "PASS" if checks["abstention_correct"] and checks["abstention_gap_present"] else "FAIL",
    })
    run = DiagnosisRunV2.model_validate(attempt.run)
    by_id = {item.evidence_id: item for item in run.additional_evidence}
    for edge in legacy["citation_edges"]:
        evidence = by_id.get(edge["evidence_id"])
        reason = None
        if edge["rubric_relation"] is None:
            status = "REVIEW_REQUIRED"
            reason = "ACTIVATION_CONTEXT_WITHOUT_SIGNED_CAUSAL_RULE" if evidence is None else "SIGNED_RELATION_UNDEFINED"
        elif (evidence is not None and evidence.tool_id == "get_keda_status"
              and edge["hypothesis"] == "WORKER_CAPACITY_SHORTFALL_SUSPECTED"):
            # Active scaling alone is not proof of a replica availability deficit.
            # Preserve v1's verdict and flag the relation for review on either side.
            status, reason = "REVIEW_REQUIRED", "KEDA_STATE_REQUIRES_REPLICA_AND_LAG_CONTEXT"
        else:
            status = "MATCH" if edge["pass"] else "MISMATCH"
        result["citation_reviews"].append({**edge, "review_status": status, "review_reason": reason})
    statuses = {edge["review_status"] for edge in result["citation_reviews"]}
    result["dimensions"]["citation_relations"] = (
        "FAIL" if "MISMATCH" in statuses else
        "REVIEW_REQUIRED" if "REVIEW_REQUIRED" in statuses else
        "PASS" if statuses else "NOT_APPLICABLE"
    )
    supported = set(legacy["supported_hypotheses"])
    if {"WORKER_PATH_PRESSURE_SUSPECTED", "POSTGRES_PATH_DEGRADED_SUSPECTED"} <= supported:
        result["review_flags"].append("SYMPTOM_AND_EXPLANATION_BOTH_SUPPORTED")
    postgres = next((item for item in run.additional_evidence if item.tool_id == "get_postgres_health"), None)
    if "WORKER_PATH_PRESSURE_SUSPECTED" in supported and (
        postgres is None or postgres.status.value != "OK" or postgres.freshness.status.value != "FRESH"
    ):
        result["review_flags"].append("SUPPORTED_PATH_WITH_MISSING_POSTGRES_DISCRIMINATOR")
    if "get_keda_status" in legacy["missing_required_tools"]:
        result["review_flags"].append("KEDA_INVESTIGATION_INCOMPLETE_NOT_LABEL_ERROR")
    if any(edge["review_status"] == "REVIEW_REQUIRED" for edge in result["citation_reviews"]):
        result["review_flags"].append("CITATION_RULE_REVIEW_NEEDED")
    return result


def summarize_reviews(reviews):
    if not reviews or len({item["attempt_id"] for item in reviews}) != len(reviews):
        raise ValueError("review attempts must be nonempty with unique IDs")
    return {
        "review_version": REVIEW_VERSION, "attempt_count": len(reviews),
        "legacy_quality_pass_count": sum(item["legacy_score"]["quality_pass"] for item in reviews),
        "dimensions": {
            name: dict(Counter(item["dimensions"][name] for item in reviews))
            for name in reviews[0]["dimensions"]
        },
        "review_flag_counts": dict(Counter(flag for item in reviews for flag in item["review_flags"])),
        "new_integrated_pass_rate": None,
    }
