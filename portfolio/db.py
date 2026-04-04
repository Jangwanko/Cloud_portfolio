from contextlib import contextmanager
import time

from alembic import command
from alembic.config import Config
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


def _create_pool() -> SimpleConnectionPool:
    return SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=3,
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


def run_alembic_migrations() -> None:
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option(
        "sqlalchemy.url",
        (
            f"postgresql+psycopg2://{settings.db_user}:{settings.db_password}"
            f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
        ),
    )
    command.upgrade(alembic_cfg, "head")


def run_schema_migrations() -> None:
    # Backward compatibility for old imports.
    run_alembic_migrations()


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
