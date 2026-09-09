"""Durable notification intent; no implicit historical backfill."""
from alembic import op

revision = "0009_notification_outbox"
down_revision = "0008_generic_event_envelope"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE notification_outbox (
            message_id BIGINT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
            room_id BIGINT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            published_at TIMESTAMPTZ
        );
        CREATE INDEX notification_outbox_pending_idx
            ON notification_outbox (next_attempt_at, message_id)
            WHERE published_at IS NULL;
    """)


def downgrade():
    raise RuntimeError("Outbox downgrade requires an explicit drain/archive plan")
