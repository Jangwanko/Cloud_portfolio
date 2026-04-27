"""add kafka mode state tables

Revision ID: 0004_kafka_state_backend
Revises: 0003_auth_and_password_hash
Create Date: 2026-04-26
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0004_kafka_state_backend"
down_revision: Union[str, None] = "0003_auth_and_password_hash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS request_statuses (
            request_id TEXT PRIMARY KEY,
            user_id BIGINT,
            status_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_request_statuses_user_id
        ON request_statuses(user_id);

        CREATE TABLE IF NOT EXISTS intake_idempotency_keys (
            route TEXT NOT NULL,
            idem_key TEXT NOT NULL,
            request_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(route, idem_key)
        );

        CREATE TABLE IF NOT EXISTS room_sequence_allocations (
            room_id BIGINT PRIMARY KEY REFERENCES rooms(id) ON DELETE CASCADE,
            last_seq BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS room_sequence_allocations;
        DROP TABLE IF EXISTS intake_idempotency_keys;
        DROP TABLE IF EXISTS request_statuses;
        """
    )
