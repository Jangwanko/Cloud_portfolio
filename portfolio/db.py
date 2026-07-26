from contextlib import contextmanager
from pathlib import Path
import threading
import time
from urllib.parse import quote

from alembic import command
from alembic.config import Config
from psycopg2 import InterfaceError, OperationalError
from psycopg2.extras import RealDictCursor
from psycopg2.pool import PoolError, ThreadedConnectionPool

from portfolio.config import settings
from portfolio.metrics import (
    db_failure_total,
    db_pool_in_use,
    db_reconnect_total,
    health_status,
    postgres_is_primary,
    postgres_replication_delay_bytes_max,
    postgres_replication_state_count,
    postgres_replication_sync_state_count,
    postgres_standby_count,
    postgres_sync_standby_count,
)

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.RLock()
_pool_active: dict[int, int] = {}
_retired_pools: dict[int, ThreadedConnectionPool] = {}


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


def _create_pool() -> ThreadedConnectionPool:
    return ThreadedConnectionPool(
        minconn=settings.db_pool_minconn,
        maxconn=settings.db_pool_maxconn,
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=settings.db_connect_timeout,
    )


def _install_pool(new_pool: ThreadedConnectionPool) -> None:
    """Atomically replace the pool without closing checked-out connections."""
    global _pool

    with _pool_lock:
        old_pool = _pool
        _pool = new_pool
        _pool_active.setdefault(id(new_pool), 0)
        if old_pool is None or old_pool is new_pool:
            return
        old_id = id(old_pool)
        if _pool_active.get(old_id, 0) == 0:
            old_pool.closeall()
            _pool_active.pop(old_id, None)
        else:
            _retired_pools[old_id] = old_pool


def _checkout_connection() -> tuple[ThreadedConnectionPool, object]:
    with _pool_lock:
        pool = _pool
        if pool is None:
            raise PoolError("DB pool is not initialized")
        conn = pool.getconn()
        _pool_active[id(pool)] = _pool_active.get(id(pool), 0) + 1
    db_pool_in_use.inc()
    return pool, conn


def _return_connection(pool: ThreadedConnectionPool, conn, *, close: bool) -> None:
    pool_id = id(pool)
    try:
        with _pool_lock:
            try:
                pool.putconn(conn, close=close)
            finally:
                remaining = max(0, _pool_active.get(pool_id, 1) - 1)
                _pool_active[pool_id] = remaining
                if remaining == 0 and pool_id in _retired_pools:
                    retired = _retired_pools.pop(pool_id)
                    retired.closeall()
                    _pool_active.pop(pool_id, None)
    finally:
        db_pool_in_use.dec()


def init_pool_with_retry(retries: int, delay_sec: float) -> None:
    last_error = None
    for _ in range(retries):
        try:
            _install_pool(_create_pool())
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
    with _pool_lock:
        pools = list(_retired_pools.values())
        _retired_pools.clear()
        if _pool is not None:
            pools.append(_pool)
        _pool = None
        _pool_active.clear()
    for pool in pools:
        pool.closeall()


def reconnect_pool() -> None:
    try:
        _install_pool(_create_pool())
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
        origin_pool, conn = _checkout_connection()
    except Exception as exc:
        db_failure_total.labels(reason="pool_getconn_failure").inc()
        health_status.labels(component="db").set(0)
        raise exc

    discard = False
    try:
        if conn.closed:
            discard = True
            raise InterfaceError("DB pool returned a closed connection")
        yield conn
    except (OperationalError, InterfaceError) as exc:
        discard = True
        db_failure_total.labels(reason=classify_db_error(exc)).inc()
        health_status.labels(component="db").set(0)
        raise
    finally:
        try:
            if not conn.closed:
                # psycopg2 starts a transaction for SELECT statements too.
                # Returning an idle transaction to the pool pins snapshots and
                # can break later callers, so every checkout is reset.
                conn.rollback()
            else:
                discard = True
        except Exception:  # noqa: BLE001
            discard = True
        finally:
            _return_connection(origin_pool, conn, close=discard)


@contextmanager
def get_cursor(conn):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
    finally:
        cur.close()


def run_alembic_migrations() -> None:
    project_root = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(project_root / "alembic"))
    database_url = (
        f"postgresql+psycopg2://{quote(settings.db_user, safe='')}:"
        f"{quote(settings.db_password, safe='')}@{settings.db_host}:{settings.db_port}/"
        f"{settings.db_name}"
    )
    alembic_cfg.set_main_option(
        "sqlalchemy.url",
        database_url.replace("%", "%%"),
    )
    command.upgrade(alembic_cfg, "head")


def run_schema_migrations() -> None:
    # Backward compatibility for old imports.
    run_alembic_migrations()


def _count_replication_rows(rows) -> tuple[dict[str, int], dict[str, int], int, int]:
    state_counts: dict[str, int] = {}
    sync_state_counts: dict[str, int] = {}
    sync_standby_count = 0
    max_replication_delay_bytes = 0

    for row in rows:
        replication_state = str(row.get("state", "unknown")).lower() or "unknown"
        sync_state = str(row.get("sync_state", "unknown")).lower() or "unknown"
        state_counts[replication_state] = state_counts.get(replication_state, 0) + 1
        sync_state_counts[sync_state] = sync_state_counts.get(sync_state, 0) + 1

        if sync_state in {"sync", "quorum"}:
            sync_standby_count += 1

        raw_delay = row.get("replication_delay_bytes", 0)
        try:
            delay_bytes = int(raw_delay)
        except (TypeError, ValueError):
            delay_bytes = 0
        max_replication_delay_bytes = max(max_replication_delay_bytes, delay_bytes)

    return state_counts, sync_state_counts, sync_standby_count, max_replication_delay_bytes


def _read_pg_stat_replication(cur) -> list[dict]:
    cur.execute(
        """
        /*NO LOAD BALANCE*/
        SELECT
            application_name,
            state,
            sync_state,
            COALESCE(pg_wal_lsn_diff(sent_lsn, replay_lsn), 0)::bigint AS replication_delay_bytes
        FROM pg_stat_replication
        """
    )
    return list(cur.fetchall())


def get_postgres_runtime_status() -> dict:
    status = {
        "ha_mode": False,
        "primary_reachable": False,
        "write_available": False,
        "standby_count": 0,
        "sync_standby_count": 0,
        "state_counts": {},
        "sync_state_counts": {},
        "max_replication_delay_bytes": 0,
        "reasons": [],
    }

    postgres_replication_state_count.clear()
    postgres_replication_sync_state_count.clear()

    try:
        with get_conn() as conn:
            with get_cursor(conn) as cur:
                try:
                    cur.execute("SHOW pool_nodes")
                    rows = cur.fetchall()
                    status["ha_mode"] = True
                except Exception:
                    conn.rollback()
                    cur.execute("SELECT NOT pg_is_in_recovery() AS is_primary")
                    row = cur.fetchone()
                    primary_reachable = bool(row and row["is_primary"])
                    postgres_is_primary.set(1 if primary_reachable else 0)
                    postgres_standby_count.set(0)
                    postgres_sync_standby_count.set(0)
                    postgres_replication_delay_bytes_max.set(0)
                    status["primary_reachable"] = primary_reachable
                    status["write_available"] = primary_reachable
                    if primary_reachable:
                        health_status.labels(component="db").set(1)
                    else:
                        health_status.labels(component="db").set(0)
                        status["reasons"].append("postgres_primary_unreachable")
                    return status
    except Exception as exc:  # noqa: BLE001
        db_failure_total.labels(reason=classify_db_error(exc)).inc()
        health_status.labels(component="db").set(0)
        postgres_is_primary.set(0)
        postgres_standby_count.set(0)
        postgres_sync_standby_count.set(0)
        postgres_replication_delay_bytes_max.set(0)
        status["reasons"].append(f"postgres_pool_nodes_error:{type(exc).__name__}")
        return status

    state_counts: dict[str, int] = {}
    sync_state_counts: dict[str, int] = {}
    standby_count = 0
    sync_standby_count = 0
    max_replication_delay_bytes = 0
    primary_reachable = False

    for row in rows:
        node_status = str(row.get("status", "")).lower()
        pg_status = str(row.get("pg_status", node_status)).lower()
        role = str(row.get("role", row.get("pg_role", ""))).lower()
        if role == "primary" and node_status == "up" and pg_status == "up":
            primary_reachable = True
            continue

        if role not in {"standby", "secondary"}:
            continue
        if node_status != "up" or pg_status != "up":
            continue

        standby_count += 1
        replication_state = str(row.get("replication_state", "unknown")).lower() or "unknown"
        sync_state = str(row.get("replication_sync_state", "unknown")).lower() or "unknown"
        state_counts[replication_state] = state_counts.get(replication_state, 0) + 1
        sync_state_counts[sync_state] = sync_state_counts.get(sync_state, 0) + 1

        if sync_state in {"sync", "quorum"}:
            sync_standby_count += 1

        raw_delay = row.get("replication_delay", 0)
        try:
            delay_bytes = int(raw_delay)
        except (TypeError, ValueError):
            delay_bytes = 0
        max_replication_delay_bytes = max(max_replication_delay_bytes, delay_bytes)

    if primary_reachable:
        try:
            with get_conn() as conn:
                with get_cursor(conn) as cur:
                    replication_rows = _read_pg_stat_replication(cur)
            if replication_rows:
                (
                    state_counts,
                    sync_state_counts,
                    sync_standby_count,
                    max_replication_delay_bytes,
                ) = _count_replication_rows(replication_rows)
                standby_count = max(standby_count, len(replication_rows))
        except Exception as exc:  # noqa: BLE001
            status["reasons"].append(f"postgres_replication_stats_error:{type(exc).__name__}")

    for replication_state, count in state_counts.items():
        postgres_replication_state_count.labels(state=replication_state).set(count)
    for sync_state, count in sync_state_counts.items():
        postgres_replication_sync_state_count.labels(sync_state=sync_state).set(count)

    postgres_is_primary.set(1 if primary_reachable else 0)
    postgres_standby_count.set(standby_count)
    postgres_sync_standby_count.set(sync_standby_count)
    postgres_replication_delay_bytes_max.set(max_replication_delay_bytes)

    status["primary_reachable"] = primary_reachable
    status["write_available"] = primary_reachable
    status["standby_count"] = standby_count
    status["sync_standby_count"] = sync_standby_count
    status["state_counts"] = state_counts
    status["sync_state_counts"] = sync_state_counts
    status["max_replication_delay_bytes"] = max_replication_delay_bytes

    if primary_reachable:
        health_status.labels(component="db").set(1)
    else:
        health_status.labels(component="db").set(0)
        status["reasons"].append("postgres_primary_unreachable")

    return status


def ping_db() -> bool:
    status = get_postgres_runtime_status()
    return bool(status["primary_reachable"])
