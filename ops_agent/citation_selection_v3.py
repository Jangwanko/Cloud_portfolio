"""Request-time finite citation choices from supplied evidence, independent of answers."""

from copy import deepcopy

from ops_agent.diagnosis_v3_contract import decision_v3_contract
from ops_agent.diagnosis_validator import DiagnosisValidationError


def citation_selection_policy_v3():
    return {"version": "ops.diagnosis.citation-selection.v1",
            "initial_universe": "Only initial activation IDs plus evidence acquired before this request",
            "repair_universe": "Intersect supplied IDs with IDs present in the original decision; no new citations",
            "fields": ["observations.evidence_ids", "hypotheses.supporting_evidence_ids",
                       "hypotheses.conflicting_evidence_ids", "context_evidence_ids"],
            "schema": "Shared string enum via $defs/ProvidedEvidenceIdV3; arrays empty-only if universe empty",
            "empty_universe": "Observations forbidden; other citation arrays maxItems zero",
            "max_enum_values": 256, "semantic_validation": "Existing tool, flag, freshness and evidence-role checks unchanged"}


def decision_citations(raw):
    def items(value):
        return value if isinstance(value, list) else []
    def strings(value):
        return {item for item in items(value) if isinstance(item, str)}
    values = strings(raw.get("context_evidence_ids"))
    for item in items(raw.get("observations")):
        if isinstance(item, dict):
            values.update(strings(item.get("evidence_ids")))
    for item in items(raw.get("hypotheses")):
        if isinstance(item, dict):
            values.update(strings(item.get("supporting_evidence_ids")))
            values.update(strings(item.get("conflicting_evidence_ids")))
    return values


def selectable_evidence_ids(*, initial_evidence_ids, acquired_evidence, original=None):
    values = [*initial_evidence_ids, *(item.evidence_id for item in acquired_evidence)]
    if any(not isinstance(value, str) or not 1 <= len(value) <= 256 for value in values):
        raise ValueError("invalid supplied evidence ID")
    allowed = set(values)
    if original is not None:
        allowed &= decision_citations(original)
    if len(allowed) > 256:
        raise ValueError("citation selection universe exceeds 256 IDs")
    return sorted(allowed)


def citation_schema_v3(allowed_evidence_ids):
    schema = deepcopy(decision_v3_contract()["schema"])
    values = list(allowed_evidence_ids)
    if any(not isinstance(value, str) or not 1 <= len(value) <= 256 for value in values):
        raise ValueError("invalid citation enum")
    allowed = sorted(set(values))
    if len(allowed) > 256:
        raise ValueError("invalid citation enum")
    observed = schema["$defs"]["ObservedSignalV3"]["properties"]["evidence_ids"]
    candidate = schema["$defs"]["CandidateCauseV3"]["properties"]
    fields = [observed, candidate["supporting_evidence_ids"], candidate["conflicting_evidence_ids"],
              schema["properties"]["context_evidence_ids"]]
    if allowed:
        schema["$defs"]["ProvidedEvidenceIdV3"] = {"type": "string", "minLength": 1, "maxLength": 256, "enum": allowed}
        for field in fields:
            field["items"] = {"$ref": "#/$defs/ProvidedEvidenceIdV3"}
    else:
        # JSON Schema disallows an empty enum. No observation can have its required
        # citation, so forbid observation objects and make the other arrays empty.
        schema["properties"]["observations"]["maxItems"] = 0
        for field in fields[1:]:
            field["maxItems"] = 0
    return schema


def validate_citation_selection(decision, allowed_evidence_ids):
    if not decision_citations(decision.model_dump(mode="json")) <= set(allowed_evidence_ids):
        raise DiagnosisValidationError("V3_CITATION_SELECTION_OUTSIDE_ENUM", "citation is outside the supplied request-time selection universe")
