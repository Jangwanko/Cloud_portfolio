from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_policy(profile: str) -> dict[str, Any]:
    policy_path = Path(__file__).with_name(f"{profile}.yaml")
    if not policy_path.is_file():
        raise ValueError(f"unknown cluster profile: {profile}")
    # JSON is a strict YAML subset, avoiding a runtime YAML dependency for one policy.
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy.get("profile") != profile:
        raise ValueError(f"policy profile mismatch: expected {profile}")
    return policy
