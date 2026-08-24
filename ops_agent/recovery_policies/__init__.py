from __future__ import annotations

import json
from pathlib import Path

from ops_agent.recovery_models import RecoveryEvaluationPolicy


def load_recovery_policy(
    profile: str = "local-ha",
    version: str = "v1",
) -> RecoveryEvaluationPolicy:
    if version not in {"v1", "v2"}:
        raise ValueError(f"unknown recovery policy version: {version}")
    path = Path(__file__).with_name(f"worker-backlog-{profile}-{version}.yaml")
    if not path.is_file():
        raise ValueError(f"unknown recovery policy profile: {profile}")
    return RecoveryEvaluationPolicy.model_validate_json(
        path.read_text(encoding="utf-8")
    )


__all__ = ["load_recovery_policy"]
