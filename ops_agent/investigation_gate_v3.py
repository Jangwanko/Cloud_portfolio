"""Fixture-independent minimum coverage before a model may return a decision."""

PRIMARY_TOOLS = ("get_worker_stage_latency", "get_postgres_health", "get_worker_replica_status")


def investigation_policy_v3():
    return {"version": "ops.diagnosis.investigation.v1", "primary_tools": list(PRIMARY_TOOLS),
            "conditional_tool": "get_keda_status",
            "condition": "Fresh OK get_worker_replica_status has semantic_flag WORKER_CAPACITY_SHORTFALL",
            "failed_queries": "A completed UNAVAILABLE/ERROR query counts as attempted coverage, never as healthy evidence",
            "selection": "While required tools remain, offer only those tools with tool_choice required; model chooses order",
            "max_tools": 4, "early_decision": "Reject locally without extra requests or output repair"}


def pending_investigation(acquired_evidence):
    by_tool = {item.tool_id: item for item in acquired_evidence}
    if len(by_tool) != len(acquired_evidence):
        raise ValueError("duplicate acquisition tool in investigation gate")
    required = list(PRIMARY_TOOLS)
    replica = by_tool.get("get_worker_replica_status")
    if (replica is not None and replica.status.value == "OK"
            and replica.freshness.status.value == "FRESH"
            and replica.summary.get("semantic_flag") == "WORKER_CAPACITY_SHORTFALL"):
        required.append("get_keda_status")
    return [tool for tool in required if tool not in by_tool]
