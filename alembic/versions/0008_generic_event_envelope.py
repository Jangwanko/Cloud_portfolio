"""add a domain-neutral event envelope

Revision ID: 0008_generic_event_envelope
Revises: 0007_drop_legacy_room_sequence_allocations
Create Date: 2026-07-14
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0008_generic_event_envelope"
down_revision: Union[str, None] = "0007_drop_legacy_room_sequence_allocations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE messages ADD COLUMN IF NOT EXISTS schema_version SMALLINT NOT NULL DEFAULT 1;
        ALTER TABLE messages ADD COLUMN IF NOT EXISTS payload JSONB;
        ALTER TABLE messages ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

        UPDATE messages
        SET payload = jsonb_build_object('text', body)
        WHERE payload IS NULL;

        UPDATE messages
        SET metadata = metadata || jsonb_strip_nulls(
            jsonb_build_object(
                'classification', category,
                'external_references',
                CASE
                    WHEN payment_id IS NULL THEN NULL
                    ELSE jsonb_build_object('payment', payment_id)
                END
            )
        );

        ALTER TABLE messages
        ADD CONSTRAINT chk_messages_payload_object
        CHECK (payload IS NULL OR jsonb_typeof(payload) = 'object') NOT VALID;

        ALTER TABLE messages
        ADD CONSTRAINT chk_messages_metadata_object
        CHECK (jsonb_typeof(metadata) = 'object') NOT VALID;

        ALTER TABLE messages
        ADD CONSTRAINT chk_messages_schema_version_positive
        CHECK (schema_version BETWEEN 1 AND 32767) NOT VALID;

        ALTER TABLE messages
        ADD CONSTRAINT chk_messages_generic_envelope
        CHECK (
            schema_version < 2
            OR (
                event_type IS NOT NULL
                AND char_length(event_type) BETWEEN 1 AND 50
                AND event_type ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
                AND jsonb_typeof(payload) = 'object'
            )
        ) NOT VALID;

        ALTER TABLE messages VALIDATE CONSTRAINT chk_messages_payload_object;
        ALTER TABLE messages VALIDATE CONSTRAINT chk_messages_metadata_object;
        ALTER TABLE messages VALIDATE CONSTRAINT chk_messages_schema_version_positive;
        ALTER TABLE messages VALIDATE CONSTRAINT chk_messages_generic_envelope;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM messages
                WHERE schema_version >= 2
                   OR payload IS DISTINCT FROM jsonb_build_object('text', body)
                   OR metadata IS DISTINCT FROM jsonb_strip_nulls(
                        jsonb_build_object(
                            'classification', category,
                            'external_references',
                            CASE
                                WHEN payment_id IS NULL THEN NULL
                                ELSE jsonb_build_object('payment', payment_id)
                            END
                        )
                   )
            ) THEN
                RAISE EXCEPTION
                    'refusing lossy downgrade: messages contain non-reconstructable structured payload/metadata';
            END IF;
        END
        $$;

        ALTER TABLE messages DROP CONSTRAINT IF EXISTS chk_messages_generic_envelope;
        ALTER TABLE messages DROP CONSTRAINT IF EXISTS chk_messages_schema_version_positive;
        ALTER TABLE messages DROP CONSTRAINT IF EXISTS chk_messages_metadata_object;
        ALTER TABLE messages DROP CONSTRAINT IF EXISTS chk_messages_payload_object;
        ALTER TABLE messages DROP COLUMN IF EXISTS metadata;
        ALTER TABLE messages DROP COLUMN IF EXISTS payload;
        ALTER TABLE messages DROP COLUMN IF EXISTS schema_version;
        """
    )
