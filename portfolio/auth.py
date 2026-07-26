import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from functools import lru_cache

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from portfolio.db import get_conn, get_cursor
from portfolio.event_envelope import validate_json_structure

_TOKEN_TTL_SECONDS = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "3600"))
_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "dev-secret-change-me")
_bearer_scheme = HTTPBearer(auto_error=False)
_DEFAULT_SECRET_KEY = "dev-secret-change-me"
_KNOWN_PLACEHOLDER_SECRET_KEYS = {
    _DEFAULT_SECRET_KEY,
    "replace-with-a-random-local-secret",
}
_LEGACY_PASSWORD_ITERATIONS = 120_000
_PASSWORD_ITERATIONS = int(os.getenv("PASSWORD_HASH_ITERATIONS", "600000"))
_MAX_PASSWORD_ITERATIONS = 2_000_000
_MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807
_MAX_UNIX_TIMESTAMP = 253_402_300_799
_MAX_ACCESS_TOKEN_LENGTH = 4096
_B64URL_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_TOKEN_TTL_SECONDS = 2_592_000

if not (_LEGACY_PASSWORD_ITERATIONS <= _PASSWORD_ITERATIONS <= _MAX_PASSWORD_ITERATIONS):
    raise RuntimeError(
        "PASSWORD_HASH_ITERATIONS must be between "
        f"{_LEGACY_PASSWORD_ITERATIONS} and {_MAX_PASSWORD_ITERATIONS}"
    )
if not 1 <= _TOKEN_TTL_SECONDS <= _MAX_TOKEN_TTL_SECONDS:
    raise RuntimeError(
        f"ACCESS_TOKEN_TTL_SECONDS must be between 1 and {_MAX_TOKEN_TTL_SECONDS}"
    )


def is_default_auth_secret() -> bool:
    return _SECRET_KEY == _DEFAULT_SECRET_KEY


def is_unsafe_auth_secret() -> bool:
    try:
        encoded = _SECRET_KEY.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return _SECRET_KEY in _KNOWN_PLACEHOLDER_SECRET_KEYS or len(encoded) < 32


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.b64decode(data + padding, altchars=b"-_", validate=True)


def hash_password(password: str) -> str:
    if not isinstance(password, str):
        raise ValueError("Password must be a string")
    validate_json_structure(password)
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS
    )
    return (
        f"pbkdf2_sha256${_PASSWORD_ITERATIONS}"
        f"${_b64url_encode(salt)}${_b64url_encode(derived)}"
    )


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not isinstance(stored_hash, str) or not stored_hash:
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) == 3:
            algorithm, salt_raw, digest_raw = parts
            iterations = _LEGACY_PASSWORD_ITERATIONS
        elif len(parts) == 4:
            algorithm, iterations_raw, salt_raw, digest_raw = parts
            iterations = int(iterations_raw)
        else:
            return False
        if algorithm != "pbkdf2_sha256":
            return False
        if not (_LEGACY_PASSWORD_ITERATIONS <= iterations <= _MAX_PASSWORD_ITERATIONS):
            return False
        salt = _b64url_decode(salt_raw)
        expected = _b64url_decode(digest_raw)
    except (ValueError, TypeError, binascii.Error):
        return False
    if len(salt) != 16 or len(expected) != hashlib.sha256().digest_size:
        return False
    try:
        validate_json_structure(password)
        password_bytes = password.encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations)
    return hmac.compare_digest(actual, expected)


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    salt = b"portfolio-dummy!"
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        b"invalid-password",
        salt,
        _PASSWORD_ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${_PASSWORD_ITERATIONS}"
        f"${_b64url_encode(salt)}${_b64url_encode(digest)}"
    )


def create_access_token(user_id: int, username: str) -> str:
    if type(user_id) is not int or not 1 <= user_id <= _MAX_POSTGRES_BIGINT:
        raise ValueError("Invalid token subject")
    if not isinstance(username, str) or not 1 <= len(username) <= 30:
        raise ValueError("Invalid token username")
    validate_json_structure(username)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": int(time.time()) + _TOKEN_TTL_SECONDS,
    }
    header_segment = _b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    payload_segment = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = hmac.new(_SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_segment}.{payload_segment}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict:
    if not isinstance(token, str) or not token or len(token) > _MAX_ACCESS_TOKEN_LENGTH:
        raise HTTPException(status_code=401, detail="Invalid access token")
    try:
        header_segment, payload_segment, signature_segment = token.split(".")
        if not all(
            segment and _B64URL_SEGMENT_RE.fullmatch(segment)
            for segment in (header_segment, payload_segment, signature_segment)
        ):
            raise ValueError("Invalid token encoding")
        header = json.loads(_b64url_decode(header_segment).decode("utf-8"))
        if (
            not isinstance(header, dict)
            or header.get("alg") != "HS256"
            or header.get("typ") != "JWT"
        ):
            raise ValueError("Unsupported token header")
        validate_json_structure(header)
        actual_signature = _b64url_decode(signature_segment)
        if len(actual_signature) != hashlib.sha256().digest_size:
            raise ValueError("Invalid token signature")
    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
        RecursionError,
    ) as exc:
        raise HTTPException(status_code=401, detail="Invalid access token") from exc

    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    expected_signature = hmac.new(
        _SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected_signature, actual_signature):
        raise HTTPException(status_code=401, detail="Invalid access token")

    try:
        payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Token payload must be an object")
        validate_json_structure(payload)
        expires_at = payload["exp"]
        subject = payload["sub"]
        username = payload["username"]
        if (
            type(expires_at) is not int
            or expires_at <= 0
            or expires_at > _MAX_UNIX_TIMESTAMP
            or not isinstance(subject, str)
            or not subject
            or not subject.isascii()
            or not subject.isdecimal()
        ):
            raise ValueError("Invalid token claims")
        user_id = int(subject)
        if subject != str(user_id) or not 1 <= user_id <= _MAX_POSTGRES_BIGINT:
            raise ValueError("Invalid token subject")
        if not isinstance(username, str) or not 1 <= len(username) <= 30:
            raise ValueError("Invalid token subject")
    except (
        KeyError,
        ValueError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
        RecursionError,
    ) as exc:
        raise HTTPException(status_code=401, detail="Invalid access token") from exc
    if expires_at <= int(time.time()):
        raise HTTPException(status_code=401, detail="Access token expired")
    return payload


def authenticate_user(username: str, password: str) -> dict | None:
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                "/*NO LOAD BALANCE*/ SELECT id, username, password_hash FROM users WHERE username=%s",
                (username,),
            )
            user = cur.fetchone()
    stored_hash = user.get("password_hash") if user else _dummy_password_hash()
    password_valid = verify_password(password, stored_hash)
    if not user or not password_valid:
        return None
    return {"id": int(user["id"]), "username": user["username"]}


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authentication scheme")

    payload = decode_access_token(credentials.credentials)
    return {"id": int(payload["sub"]), "username": payload["username"]}
