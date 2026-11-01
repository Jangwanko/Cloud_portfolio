"""Prefix-only, fail-closed follow-up projection candidate (no model or source I/O)."""

from copy import deepcopy
import json

from ops_agent.evaluation_models import canonical_sha256
from ops_agent.evaluator import evaluate_bundle
from ops_agent.models import EvidenceBundle
from ops_agent.recovery_evaluator import evaluate_recovery
from ops_agent.recovery_policies import load_recovery_policy
from ops_agent.runtime_evidence_v3 import FrozenRuntimeRegistry, verify_runtime_inputs
from ops_agent.sequence_evaluator import evaluate_bundle_sequence

VERSION = "ops.diagnosis.followup-replay.v1"


def _offset_heads(bundle):
    names = {"kafka_topic_partition_current_offset", "kafka_consumergroup_current_offset"}
    return {(item.metric.name, item.labels.get("partition")): int(item.metric.value[-1]["value"])
        for item in bundle.evidence if item.metric.name in names}


class FollowupReplay:
    """One verified activation followed by one capture at a time.

    UNKNOWN permanently blocks this replay session. CLOSED is sticky. A new
    session must never be used to discard a known history gap or prior closure.
    This candidate does not attach diagnoses to ops.incident.v1 records.
    """

    def __init__(self, *, activation_bundles, activation):
        self._activation_bundles, self._activation = verify_runtime_inputs(activation_bundles, activation)
        core = self._activation.conditions["CORE_BACKLOG_PRESSURE"]
        if len(self._activation_bundles) != 3 or core.state.value != "PRESENT":
            raise ValueError("exactly one verified three-capture activation required")
        ids = [b.incident_id for b in self._activation_bundles]
        parts = [s.rsplit("-", 1) for s in ids]
        if len(set(ids)) == 1:
            self._incident_id = ids[0]
        elif all(len(p) == 2 and p[1].isdigit() for p in parts) and len({p[0] for p in parts}) == 1:
            self._incident_id = parts[0][0]
        else:
            raise ValueError("activation incident identity is ambiguous")
        self._post = []
        self._status = "AWAITING_FOLLOWUP"
        self._last = None
        self._boundary = None

    def append(self, document):
        """Consume only the current document. Invalid input poisons the session."""
        try:
            if len(self._post) >= 253:
                raise ValueError("256 total capture limit exceeded")
            bundle = EvidenceBundle.model_validate_json(
                document.model_dump_json() if isinstance(document, EvidenceBundle)
                else json.dumps(document))
            suffix = bundle.incident_id.removeprefix(self._incident_id + "-")
            if bundle.incident_id != self._incident_id and (
                not bundle.incident_id.startswith(self._incident_id + "-") or not suffix.isdigit()
            ):
                raise ValueError("follow-up incident identity mismatch")
            if self._status not in ("BLOCKED", "CLOSED"):
                previous = _offset_heads(self._post[-1] if self._post else self._activation_bundles[-1])
                current = _offset_heads(bundle)
                if previous.keys() != current.keys() or any(current[k] < v for k, v in previous.items()):
                    raise ValueError("follow-up offset reset or partition change")
            self._post.append(bundle)
            digests = [canonical_sha256(b.model_dump(mode="json")) for b in self._post]
            recovery = None
            if self._status not in ("BLOCKED", "CLOSED"):
                recovery = evaluate_recovery(incident_id=self._incident_id,
                    activation_evaluation=self._activation, bundles=self._post,
                    source_bundle_digests=digests, policy=load_recovery_policy(version="v2"))
                state = recovery.state.value
                self._status = ("BLOCKED" if state == "WORKER_BACKLOG_UNKNOWN" else
                    "CLOSED" if state == "WORKER_BACKLOG_RECOVERED" else "ELIGIBLE")
                if self._status != "ELIGIBLE":
                    self._boundary = {"post_activation_index": len(self._post) - 1,
                        "state": state, "reason_codes": recovery.reason_codes}
            record = {"version": VERSION, "post_activation_index": len(self._post) - 1,
                "status": self._status, "observed_at": bundle.collection.completed_at.isoformat(),
                "activation_evaluation_id": self._activation.evaluation_id,
                "activation_bundle_digests": [b.source_bundle_sha256 for b in self._activation.source_bundles],
                "followup_bundle_digests": digests, "projection_bundle_sha256": digests[-1],
                "current_recovery_state": recovery.state.value if recovery else None,
                "recovery_evaluation_id": recovery.recovery_evaluation_id if recovery else None,
                "first_boundary": deepcopy(self._boundary),
                "previous_step_sha256": self._last["step_sha256"] if self._last else None}
            record["step_sha256"] = canonical_sha256(record)
            self._last = record
            return deepcopy(record)
        except (ValueError, TypeError, KeyError, IndexError):
            if self._status != "CLOSED":
                self._status = "BLOCKED"
                self._boundary = {"state": "INVALID_INPUT", "reason_codes": ["FOLLOWUP_INPUT_REJECTED"]}
            raise

    def registry(self):
        if self._status != "ELIGIBLE":
            raise ValueError("follow-up projection requires uninterrupted open history")
        return _FollowupRegistry(self)


class _FollowupRegistry(FrozenRuntimeRegistry):
    """Reuse frozen v1 projection semantics; bind output to the verified prefix."""

    def __init__(self, replay):
        super().__init__(bundles=replay._activation_bundles, evaluation=replay._activation)
        self._step = deepcopy(replay._last)
        self._bundle = EvidenceBundle.model_validate_json(replay._post[-1].model_dump_json())
        self._single = evaluate_bundle(self._bundle)
        self._capture = evaluate_bundle_sequence([self._bundle]).capture_observations[0]
        self._index = 3 + self._step["post_activation_index"]
        self._activation.update(projection_sequence_index=self._index,
            projection_bundle_sha256=self._step["projection_bundle_sha256"],
            activation_observed_at=replay._activation_bundles[-1].collection.completed_at.isoformat(),
            diagnosis_observed_at=self._step["observed_at"],
            followup=self._step, input_condition_scope="HISTORICAL_ACTIVATION_ONLY")

    def execute(self, tool):
        evidence = super().execute(tool).model_dump(mode="json")
        evidence["summary"].update(adapter_version=VERSION,
            followup_step_sha256=self._step["step_sha256"],
            current_recovery_state=self._step["current_recovery_state"])
        evidence["evidence_id"] = "pending"
        evidence["evidence_id"] = f"diagnosis.{tool}.{canonical_sha256(evidence)[:24]}"
        from ops_agent.diagnosis_v2_models import DiagnosisEvidenceV2
        return DiagnosisEvidenceV2.model_validate(evidence)
