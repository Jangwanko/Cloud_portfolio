"""make notification attempts idempotent by message

Revision ID: 0006_notification_attempt_idempotency
Revises: 0005_order_event_columns
Create Date: 2026-07-14
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0006_notification_attempt_idempotency"
down_revision: Union[str, None] = "0005_order_event_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM notification_attempts newer
        USING notification_attempts older
        WHERE newer.message_id = older.message_id
          AND newer.id > older.id;

        CREATE UNIQUE INDEX IF NOT EXISTS uq_notification_attempts_message_id
        ON notification_attempts(message_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS uq_notification_attempts_message_id;
        """
    )
