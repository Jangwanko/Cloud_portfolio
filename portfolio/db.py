from contextlib import contextmanager
import time

from psycopg2 import InterfaceError, OperationalError
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool

from portfolio.config import settings
from portfolio.metrics import db_failure_total, db_pool_in_use, db_reconnect_total, health_status

_pool: SimpleConnectionPool | None = None


def classify_db_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "could not translate host name" in message or "name or service not known" in message:
        return "dns_resolution"
    if "connection refused" in message:
        return "connection_refused"
    if "server closed the connection unexpectedly" in message:
        return "server_closed_connection"
    if "timeout expired" in message or "timed out" in message:
        return "timeout"
    if "terminating connection due to administrator command" in message:
        return "admin_termination"
    if isinstance(exc, InterfaceError):
        return "interface_error"
    if isinstance(exc, OperationalError):
        return "operational_error"
    return "unknown"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rooms (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS room_members (
    room_id BIGINT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(room_id, user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    room_id BIGINT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE messages ADD COLUMN IF NOT EXISTS request_id TEXT;

CREATE INDEX IF NOT EXISTS idx_messages_room_id_id_desc ON messages(room_id, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_request_id_unique ON messages(request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS read_receipts (
    message_id BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    read_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(message_id, user_id)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    route TEXT NOT NULL,
    idem_key TEXT NOT NULL,
    response_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(route, idem_key)
);

CREATE TABLE IF NOT EXISTS notification_attempts (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL,
    room_id BIGINT NOT NULL,
    payload JSONB NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _create_pool() -> SimpleConnectionPool:
    return SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
    )


def init_pool_with_retry(retries: int, delay_sec: float) -> None:
    global _pool
    last_error = None
    for _ in range(retries):
        try:
            _pool = _create_pool()
            health_status.labels(component="db").set(1)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            db_failure_total.labels(reason=classify_db_error(exc)).inc()
            health_status.labels(component="db").set(0)
            time.sleep(delay_sec)
    raise RuntimeError(f"DB pool init failed: {last_error}")


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


def reconnect_pool() -> None:
    global _pool
    try:
        close_pool()
        _pool = _create_pool()
    except Exception as exc:  # noqa: BLE001
        db_reconnect_total.labels(result="failure").inc()
        health_status.labels(component="db").set(0)
        db_failure_total.labels(reason=classify_db_error(exc)).inc()
        raise
    db_reconnect_total.labels(result="success").inc()
    health_status.labels(component="db").set(1)


@contextmanager
def get_conn():
    if _pool is None:
        reconnect_pool()

    try:
        conn = _pool.getconn()
        db_pool_in_use.inc()
    except Exception:
        db_failure_total.labels(reason="pool_getconn_failure").inc()
        reconnect_pool()
        conn = _pool.getconn()
        db_pool_in_use.inc()

    try:
        if conn.closed:
            if _pool is not None:
                _pool.putconn(conn, close=True)
            db_pool_in_use.dec()
            reconnect_pool()
            conn = _pool.getconn()
            db_pool_in_use.inc()
        yield conn
    except (OperationalError, InterfaceError) as exc:
        db_failure_total.labels(reason=classify_db_error(exc)).inc()
        reconnect_pool()
        raise
    finally:
        try:
            if _pool is not None and not conn.closed:
                _pool.putconn(conn)
        finally:
            db_pool_in_use.dec()


@contextmanager
def get_cursor(conn):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
    finally:
        cur.close()


def run_schema_migrations() -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()


def ping_db() -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        health_status.labels(component="db").set(1)
        return True
    except Exception as exc:
        db_failure_total.labels(reason=classify_db_error(exc)).inc()
        health_status.labels(component="db").set(0)
        return False
