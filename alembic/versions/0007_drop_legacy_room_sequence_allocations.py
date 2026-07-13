"""drop unused legacy room sequence allocation table

Revision ID: 0007_drop_legacy_room_sequence_allocations
Revises: 0006_notification_attempt_idempotency
Create Date: 2026-07-14
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0007_drop_legacy_room_sequence_allocations"
down_revision: Union[str, None] = "0006_notification_attempt_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS room_sequence_allocations;")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS room_sequence_allocations (
            room_id BIGINT PRIMARY KEY REFERENCES rooms(id) ON DELETE CASCADE,
            last_seq BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
