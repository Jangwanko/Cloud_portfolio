"""At-least-once relay; one Kafka send per locked row, concurrent relays skip locks.

ACK then process death may republish. notification_attempts deduplicates message_id.
"""
import json
import logging
import time

from portfolio.db import get_conn, get_cursor
from portfolio.kafka_client import publish_notification_job
from portfolio.metrics import (
    health_status, notification_publish_failures_total, outbox_pending,
    outbox_oldest_pending_seconds, outbox_published_total,
)


def enqueue_notification(cur, payload):
    cur.execute("""
        INSERT INTO notification_outbox (message_id, room_id, payload)
        VALUES (%s, %s, %s::jsonb)
        ON CONFLICT (message_id) DO NOTHING
    """, (payload['event_id'], payload['stream_id'], json.dumps(payload, allow_nan=False)))


def relay_once():
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("""
                /*NO LOAD BALANCE*/
                SELECT message_id, room_id, payload, attempts
                FROM notification_outbox
                WHERE published_at IS NULL AND next_attempt_at <= clock_timestamp()
                ORDER BY next_attempt_at, message_id
                LIMIT 1 FOR UPDATE SKIP LOCKED
            """)
            row = cur.fetchone()
            if row is None:
                return False
            try:
                publish_notification_job(row['room_id'], row['payload'])
            except Exception as exc:
                notification_publish_failures_total.inc()
                cur.execute("""
                    UPDATE notification_outbox
                    SET attempts=attempts+1, last_error=%s,
                        next_attempt_at=clock_timestamp() + (%s * interval '1 second')
                    WHERE message_id=%s
                """, (type(exc).__name__, min(60, 2 ** min(row['attempts'], 6)), row['message_id']))
                conn.commit()
                return True
            cur.execute("""
                UPDATE notification_outbox
                SET published_at=clock_timestamp(), attempts=attempts+1, last_error=NULL
                WHERE message_id=%s
            """, (row['message_id'],))
        conn.commit()
    outbox_published_total.inc()
    return True


def observe_backlog():
    with get_conn() as conn, get_cursor(conn) as cur:
        cur.execute("""
            /*NO LOAD BALANCE*/
            SELECT count(*) AS pending,
                   coalesce(extract(epoch FROM clock_timestamp()-min(created_at)), 0) AS oldest
            FROM notification_outbox WHERE published_at IS NULL
        """)
        row = cur.fetchone()
        outbox_pending.set(row['pending'])
        outbox_oldest_pending_seconds.set(max(0, float(row['oldest'])))


def run_outbox_loop():
    next_observation = 0
    while True:
        try:
            if time.monotonic() >= next_observation:
                observe_backlog()
                next_observation = time.monotonic() + 10
            worked = relay_once()
            health_status.labels(component='outbox').set(1)
            if not worked:
                time.sleep(0.5)
        except Exception as exc:
            health_status.labels(component='outbox').set(0)
            logging.error('Outbox transaction failed; retained for retry error_type=%s', type(exc).__name__)
            time.sleep(2)
