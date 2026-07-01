"""add hnsw index for knowledge chunk embeddings

Revision ID: 20260701_0003
Revises: 20260626_0002
Create Date: 2026-07-01 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
from obsidian_sync.db.base import DB_SCHEMA

revision: str = '20260701_0003'
down_revision: str | None = '20260626_0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = 'idx_knowledge_chunks_embedding_hnsw'


def upgrade() -> None:
    op.execute(
        f"""
        CREATE INDEX {_INDEX_NAME}
        ON {DB_SCHEMA}.knowledge_chunks
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f'DROP INDEX IF EXISTS {DB_SCHEMA}.{_INDEX_NAME}')
