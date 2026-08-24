from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


_SENSITIVE_KEY_MARKERS = {
    "apikey",
    "authorization",
    "clientcertificatedata",
    "clientkeydata",
    "cookie",
    "credential",
    "kubeconfig",
    "password",
    "privatekey",
    "secret",
    "token",
    "accesskeyid",
    "secretaccesskey",
}
_BEARER_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[^\s,;]+")
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:access_token|api_key|password|secret|token)=)[^&#\s]+"
)
_URL_CREDENTIAL_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)[^@/\s]+(@)")
_OPAQUE_SECRET_RE = re.compile(
    r"(?i)\b(?:"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}"
    r")\b"
)
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_SLACK_WEBHOOK_RE = re.compile(
    r"(?i)https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+"
)
_INLINE_SECRET_RE = re.compile(
    r"(?ix)\b("
    r"(?:x[-_]?)?api[-_]?key|"
    r"(?:aws[-_]?)?access[-_]?key(?:[-_]?id)?|"
    r"secret[-_]?access[-_]?key|"
    r"password|secret|token"
    r")\s*(=|:)\s*(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
)


def _canonical_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def sanitize_text(value: str) -> str:
    value = _BEARER_RE.sub(lambda match: f"{match.group(1)} [REDACTED]", value)
    value = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", value)
    value = _URL_CREDENTIAL_RE.sub(r"\1[REDACTED]\2", value)
    value = _OPAQUE_SECRET_RE.sub("[REDACTED]", value)
    value = _JWT_RE.sub("[REDACTED]", value)
    value = _SLACK_WEBHOOK_RE.sub("[REDACTED]", value)
    return _INLINE_SECRET_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        value,
    )


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = _canonical_key(key)
            if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def write_raw_artifact(
    artifact_root: Path,
    bundle_id: str,
    source: str,
    payload: Any,
) -> tuple[str, str]:
    target = artifact_root / bundle_id / f"{source}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        redact(payload),
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    encoded += b"\n"
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, target)
    digest = hashlib.sha256(encoded).hexdigest()
    return target.as_posix(), digest
