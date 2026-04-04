"""init schema

Revision ID: 0001_init_schema
Revises:
Create Date: 2026-04-04
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0001_init_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS rooms (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS room_members (
            room_id BIGINT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(room_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id BIGSERIAL PRIMARY KEY,
            room_id BIGINT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        ALTER TABLE messages ADD COLUMN IF NOT EXISTS request_id TEXT;

        CREATE INDEX IF NOT EXISTS idx_messages_room_id_id_desc ON messages(room_id, id DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_request_id_unique ON messages(request_id)
        WHERE request_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS read_receipts (
            message_id BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            read_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(message_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS idempotency_keys (
            route TEXT NOT NULL,
            idem_key TEXT NOT NULL,
            response_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(route, idem_key)
        );

        CREATE TABLE IF NOT EXISTS notification_attempts (
            id BIGSERIAL PRIMARY KEY,
            message_id BIGINT NOT NULL,
            room_id BIGINT NOT NULL,
            payload JSONB NOT NULL,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS notification_attempts;
        DROP TABLE IF EXISTS idempotency_keys;
        DROP TABLE IF EXISTS read_receipts;
        DROP TABLE IF EXISTS messages;
        DROP TABLE IF EXISTS room_members;
        DROP TABLE IF EXISTS rooms;
        DROP TABLE IF EXISTS users;
        """
    )
