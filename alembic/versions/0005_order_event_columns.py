"""add order event analytics columns

Revision ID: 0005_order_event_columns
Revises: 0004_kafka_state_backend
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0005_order_event_columns"
down_revision: Union[str, None] = "0004_kafka_state_backend"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE messages ADD COLUMN IF NOT EXISTS event_type TEXT;
        ALTER TABLE messages ADD COLUMN IF NOT EXISTS category TEXT;
        ALTER TABLE messages ADD COLUMN IF NOT EXISTS payment_id TEXT;

        CREATE INDEX IF NOT EXISTS idx_messages_event_type_created_at
        ON messages(event_type, created_at DESC)
        WHERE event_type IS NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_messages_category_created_at
        ON messages(category, created_at DESC)
        WHERE category IS NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_messages_payment_id
        ON messages(payment_id)
        WHERE payment_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS idx_messages_payment_id;
        DROP INDEX IF EXISTS idx_messages_category_created_at;
        DROP INDEX IF EXISTS idx_messages_event_type_created_at;

        ALTER TABLE messages DROP COLUMN IF EXISTS payment_id;
        ALTER TABLE messages DROP COLUMN IF EXISTS category;
        ALTER TABLE messages DROP COLUMN IF EXISTS event_type;
        """
    )
