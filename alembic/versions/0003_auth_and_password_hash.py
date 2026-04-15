"""auth and password hash

Revision ID: 0003_auth_and_password_hash
Revises: 0002_room_seq_and_dlq_replay
Create Date: 2026-04-16
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0003_auth_and_password_hash"
down_revision: Union[str, None] = "0002_room_seq_and_dlq_replay"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE users DROP COLUMN IF EXISTS password_hash;
        """
    )
