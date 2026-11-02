"""Bounded historical code facts. No model summaries or implicit current evidence."""
from copy import deepcopy
from datetime import datetime

from ops_agent.evaluation_models import canonical_sha256

VERSION = "ops.observation-history.v1"
MAX_ENTRIES = 8
IDENTITY = ("incident_id", "scope", "cluster_profile", "provenance")


def _at(value):
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("history timestamp requires timezone")
    return result


def extend_history(previous):
    if previous is None:
        return {"version": VERSION, "max_entries": MAX_ENTRIES, "dropped_entries": 0, "entries": []}
    history = deepcopy(previous["input"]["observation_history"])
    obs = deepcopy(previous["observations"])
    digest = canonical_sha256(obs)
    history["entries"].append({"history_id": "history:" + digest,
        "source_record_sha256": previous["record_sha256"], "observations_sha256": digest,
        "identity": {key: deepcopy(previous["input"][key]) for key in IDENTITY},
        "observations": obs})
    if len(history["entries"]) > MAX_ENTRIES:
        history["entries"] = history["entries"][-MAX_ENTRIES:]
        history["dropped_entries"] += 1
    return history


def validate_history(context):
    history = context["observation_history"]
    if (set(history) != {"version", "max_entries", "dropped_entries", "entries"}
            or history["version"] != VERSION or history["max_entries"] != MAX_ENTRIES
            or type(history["dropped_entries"]) is not int or history["dropped_entries"] < 0
            or not isinstance(history["entries"], list) or len(history["entries"]) > MAX_ENTRIES):
        raise ValueError("invalid observation history envelope")
    if history["dropped_entries"] and len(history["entries"]) != MAX_ENTRIES:
        raise ValueError("truncated history must retain the bounded tail")
    seen = set()
    earlier = None
    for entry in history["entries"]:
        if set(entry) != {"history_id", "source_record_sha256", "observations_sha256", "identity", "observations"}:
            raise ValueError("invalid history entry fields")
        if entry["identity"] != {key: context[key] for key in IDENTITY}:
            raise ValueError("historical observation belongs to a different incident/source scope")
        obs = entry["observations"]
        digest = canonical_sha256(obs)
        if entry["observations_sha256"] != digest or entry["history_id"] != "history:" + digest:
            raise ValueError("historical observation digest mismatch")
        stamp = _at(obs["observed_at"])
        if (entry["history_id"] in seen or stamp >= _at(context["observed_at"])
                or (earlier is not None and stamp <= earlier)):
            raise ValueError("historical observations must be unique, strictly ordered and earlier")
        source_hash = entry["source_record_sha256"]
        if not isinstance(source_hash, str) or len(source_hash) != 64 or any(c not in '0123456789abcdef' for c in source_hash):
            raise ValueError("invalid history source record digest")
        seen.add(entry["history_id"])
        earlier = stamp
    previous = context["previous"]
    if previous is None:
        if history["entries"] or history["dropped_entries"]:
            raise ValueError("history requires a preceding record")
    elif (not history["entries"]
          or history["entries"][-1]["source_record_sha256"] != previous["record_sha256"]
          or history["entries"][-1]["observations"]["observed_at"] != previous["observed_at"]):
        raise ValueError("history tail does not match preceding record")


def unknown_catalog(facts, history):
    """Allowed missing-information topics, with deterministic wording and request scope."""
    entries = []
    def add(identifier, kind, text, request_id=None):
        entries.append({"id": identifier, "kind": kind, "text": text, "request_id": request_id})
    add("gap:causal-link", "CAUSAL_LINK", "관측된 증상과 원인 후보의 인과관계는 별도 확인이 필요합니다.")
    add("gap:processing-details", "PROCESSING_DETAILS", "요청별 소비·재시도·저장 작업의 연결을 추가 확인해야 합니다.")
    add("gap:exact-commit-time", "EXACT_COMMIT_TIME", "상태 조회 시각만으로 실제 commit 시각·지연은 알 수 없습니다.")
    add("gap:global-sustained-health", "GLOBAL_SUSTAINED_HEALTH", "관측 표본만으로 전체 트래픽의 지속 정상·회복은 확인할 수 없습니다.")
    request_fact = facts["facts"]["requests"]
    if request_fact["status"] != "KNOWN":
        add("gap:request-observations", "REQUEST_OBSERVATIONS", "유효한 요청별 수락·상태 관측을 확보하지 못했습니다.")
    else:
        for identifier, state in request_fact["value"]["per_request"].items():
            suffix = canonical_sha256(identifier)[:16]
            if state["persistence"] == "UNKNOWN":
                add("gap:persistence:" + suffix, "REQUEST_PERSISTENCE",
                    "append 수락은 확인됐지만 저장 상태는 조회 불가로 미확인입니다.", identifier)
                add("gap:read-failure:" + suffix, "REQUEST_READ_FAILURE_REASON",
                    "상태 조회 실패의 원인과 조회 가시성 복구 방법을 확인해야 합니다.", identifier)
            elif state["persistence"] == "NOT_YET_OBSERVED":
                add("gap:progress:" + suffix, "REQUEST_FUTURE_PROGRESS",
                    "관측 시점에는 저장이 확인되지 않았으며 이후 처리 진행은 추가 관측이 필요합니다.", identifier)
    for name in ("lag", "database_waits"):
        if facts["facts"][name]["status"] == "UNKNOWN":
            add("gap:observation:" + name, "OBSERVATION_COVERAGE", name + "의 유효한 현재 관측을 확보하지 못했습니다.")
    if history["dropped_entries"]:
        add("gap:history-retention", "HISTORY_RETENTION", "보존 한도 이전의 관측이 입력에서 생략됐습니다. 과거 증거 부재로 해석할 수 없습니다.")
    return entries
