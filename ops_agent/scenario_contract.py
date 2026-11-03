"""Shared, scenario-independent output and capability instructions for v2 diagnosis."""

SCENARIO_PROMPT_VERSION = "ops.diagnosis.prompt.v2"

OUTPUT_CONTRACT = {
    "version": SCENARIO_PROMPT_VERSION,
    "support_status": {
        "SUPPORTED": "Requires acquired evidence that directly supports the causal hypothesis and at least one supporting_evidence_id.",
        "CONFLICTED": "Requires both supporting_evidence_ids and conflicting_evidence_ids. This means mixed evidence, not merely evidence against a hypothesis.",
        "INSUFFICIENT": "Requires at least one nonempty evidence_gap. Use when support is absent, required observations are missing, or only evidence against a hypothesis is available. Report observed counterevidence separately; never invent supporting evidence.",
    },
    "citations": [
        "Use only supplied evidence IDs. An existing ID is not automatically relevant to a claim.",
        "Fresh OK evidence can support only what it actually measures. PARTIAL, MISSING, ERROR, UNAVAILABLE or stale observations cannot establish a cause.",
        "Do not turn a general backlog observation into supporting evidence for a specific cause.",
    ],
    "capabilities": {
        "CONSUMER_REBALANCE_SUSPECTED": {
            "support_status": "INSUFFICIENT",
            "supporting_evidence_ids": [],
            "conflicting_evidence_ids": [],
            "required_evidence_gap": "CONSUMER_REBALANCE_TELEMETRY_UNAVAILABLE",
            "explanation": "Dedicated rebalance telemetry is unavailable. If this hypothesis is included, use this exact gap code. Do not infer or exclude rebalance from lag or replica snapshots.",
        },
        "worker_stage_latency": "A stage observation does not measure transaction commit latency or prove the root cause of a database path delay.",
        "postgres_health": "HA/reachability/replication health does not measure query latency or prove the absence of a database performance bottleneck.",
    },
    "investigation": [
        "CORE_BACKLOG_PRESSURE=PRESENT establishes an incident, not its cause.",
        "Choose useful tools within the supplied budget. Do not spend a tool call merely repeating an activation fact when missing diagnostic evidence could distinguish causes.",
        "Distinguish directly observed symptoms from competing causal explanations. Do not promote a symptom to a separate supported cause when it only repeats the effect of another explanation.",
        "When missing observations prevent distinguishing causal explanations, keep the affected candidates INSUFFICIENT and name the missing information. Do not treat missing telemetry as a healthy result.",
        "Include only hypotheses relevant to the investigation; the schema vocabulary does not require an exhaustive list.",
    ],
    "stop_reason": {
        "sufficient_evidence": "Requires at least one supported causal hypothesis.",
        "insufficient_evidence": "Requires no supported causal hypothesis. Use an INSUFFICIENT_EVIDENCE hypothesis with an explicit gap when abstaining.",
        "step_budget_exhausted": "Allowed only after all configured tool steps have been used; it does not establish a cause.",
        "no_useful_tool_remaining": "No remaining useful investigation can close the observed evidence gaps; preserve the gaps in the result.",
    },
}

REPAIR_INSTRUCTIONS = """Repair the supplied structured result against ALL output contract rules.
The reported validation_error is the first detected error, not a complete error list.
Check every included hypothesis, citation side, evidence gap and stop reason before
returning the single repaired JSON result. Do not diagnose again, add hypotheses,
invent observations, fabricate IDs, request tools, change the deterministic condition,
declare recovery or recommend actions. Do not fabricate a supporting citation to fill
an empty CONFLICTED side. Preserve valid supported causal conclusions and their grounded
citations. If support was absent, use INSUFFICIENT with the actual evidence gap.
Keep unavailable rebalance telemetry unavailable and preserve its exact required gap.
"""
