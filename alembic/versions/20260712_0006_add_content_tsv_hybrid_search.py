"""add generated tsvector column and gin index for hybrid search

Revision ID: 20260712_0006
Revises: 20260712_0005
Create Date: 2026-07-12 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
from obsidian_sync.db.base import DB_SCHEMA

revision: str = '20260712_0006'
down_revision: str | None = '20260712_0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = 'idx_knowledge_chunks_content_tsv'


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {DB_SCHEMA}.knowledge_chunks
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('simple', coalesce(title, '') || ' ' || content)
        ) STORED
        """
    )
    op.execute(
        f"""
        CREATE INDEX {_INDEX_NAME}
        ON {DB_SCHEMA}.knowledge_chunks
        USING gin (content_tsv)
        """
    )


def downgrade() -> None:
    op.execute(f'DROP INDEX IF EXISTS {DB_SCHEMA}.{_INDEX_NAME}')
    op.execute(
        f"""
        ALTER TABLE {DB_SCHEMA}.knowledge_chunks
        DROP COLUMN IF EXISTS content_tsv
        """
    )
