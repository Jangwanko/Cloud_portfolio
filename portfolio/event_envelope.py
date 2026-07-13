import math
import re
from typing import Any


GENERIC_EVENT_TYPE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_GENERIC_EVENT_TYPE_RE = re.compile(GENERIC_EVENT_TYPE_PATTERN)
MAX_JSON_NESTING_DEPTH = 64
MAX_JSON_WIRE_NESTING_DEPTH = MAX_JSON_NESTING_DEPTH + 1


def is_generic_event_type(value: str) -> bool:
    return bool(_GENERIC_EVENT_TYPE_RE.fullmatch(value))


def _contains_utf16_surrogate(value: str) -> bool:
    return any("\ud800" <= character <= "\udfff" for character in value)


def contains_nul(value: Any) -> bool:
    stack = [value]
    visited_containers: set[int] = set()
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            if "\x00" in current:
                return True
            continue
        if isinstance(current, dict):
            container_id = id(current)
            if container_id in visited_containers:
                continue
            visited_containers.add(container_id)
            stack.extend(current.keys())
            stack.extend(current.values())
            continue
        if isinstance(current, (list, tuple)):
            container_id = id(current)
            if container_id in visited_containers:
                continue
            visited_containers.add(container_id)
            stack.extend(current)
    return False


def validate_json_structure(value: Any, *, max_depth: int = MAX_JSON_NESTING_DEPTH) -> None:
    """Reject invalid Unicode/NUL, non-finite numbers, cycles and excessive depth."""

    stack: list[tuple[Any, int, bool]] = [(value, 0, False)]
    active_containers: set[int] = set()
    while stack:
        current, parent_depth, exiting = stack.pop()
        if isinstance(current, str):
            if "\x00" in current:
                raise ValueError("JSON strings and object keys must not contain NUL characters")
            if _contains_utf16_surrogate(current):
                raise ValueError("JSON strings and object keys must contain valid Unicode scalars")
            continue
        if isinstance(current, float) and not math.isfinite(current):
            raise ValueError("JSON numbers must be finite")
        if not isinstance(current, (dict, list, tuple)):
            continue

        container_id = id(current)
        if exiting:
            active_containers.remove(container_id)
            continue

        depth = parent_depth + 1
        if depth > max_depth:
            raise ValueError(f"JSON nesting must not exceed {max_depth} container levels")
        if container_id in active_containers:
            raise ValueError("JSON values must not contain circular references")
        active_containers.add(container_id)
        stack.append((current, parent_depth, True))

        if isinstance(current, dict):
            for key, item in current.items():
                if not isinstance(key, str):
                    raise ValueError("JSON object keys must be strings")
                if "\x00" in key:
                    raise ValueError(
                        "JSON strings and object keys must not contain NUL characters"
                    )
                if _contains_utf16_surrogate(key):
                    raise ValueError(
                        "JSON strings and object keys must contain valid Unicode scalars"
                    )
                stack.append((item, depth, False))
        else:
            for item in current:
                stack.append((item, depth, False))
