"""add room sequence support

Revision ID: 0002_room_seq_and_dlq_replay
Revises: 0001_init_schema
Create Date: 2026-04-04
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0002_room_seq_and_dlq_replay"
down_revision: Union[str, None] = "0001_init_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE messages ADD COLUMN IF NOT EXISTS room_seq BIGINT;

        WITH ordered AS (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY room_id ORDER BY id) AS rn
            FROM messages
            WHERE room_seq IS NULL
        )
        UPDATE messages m
        SET room_seq = ordered.rn
        FROM ordered
        WHERE m.id = ordered.id;

        CREATE TABLE IF NOT EXISTS room_sequences (
            room_id BIGINT PRIMARY KEY REFERENCES rooms(id) ON DELETE CASCADE,
            last_seq BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        INSERT INTO room_sequences (room_id, last_seq)
        SELECT room_id, COALESCE(MAX(room_seq), 0)
        FROM messages
        GROUP BY room_id
        ON CONFLICT (room_id) DO UPDATE SET
            last_seq = EXCLUDED.last_seq,
            updated_at = NOW();

        ALTER TABLE messages ALTER COLUMN room_seq SET NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_room_id_room_seq_unique
        ON messages(room_id, room_seq);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS idx_messages_room_id_room_seq_unique;
        ALTER TABLE messages ALTER COLUMN room_seq DROP NOT NULL;
        DROP TABLE IF EXISTS room_sequences;
        ALTER TABLE messages DROP COLUMN IF EXISTS room_seq;
        """
    )

