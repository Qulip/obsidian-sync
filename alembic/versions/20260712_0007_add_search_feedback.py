"""add search feedback columns

Revision ID: 20260712_0007
Revises: 20260712_0006
Create Date: 2026-07-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from obsidian_sync.db.base import DB_SCHEMA

revision: str = '20260712_0007'
down_revision: str | None = '20260712_0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'search_logs',
        sa.Column('feedback_helpful', sa.Boolean(), nullable=True),
        schema=DB_SCHEMA,
    )
    op.add_column(
        'search_logs',
        sa.Column('feedback_selected_source_path', sa.Text(), nullable=True),
        schema=DB_SCHEMA,
    )
    op.add_column(
        'search_logs',
        sa.Column('feedback_selected_chunk_rank', sa.Integer(), nullable=True),
        schema=DB_SCHEMA,
    )
    op.add_column(
        'search_logs',
        sa.Column('feedback_expected_missing', sa.Boolean(), nullable=True),
        schema=DB_SCHEMA,
    )
    op.add_column(
        'search_logs',
        sa.Column('feedback_comment', sa.Text(), nullable=True),
        schema=DB_SCHEMA,
    )
    op.add_column(
        'search_logs',
        sa.Column('feedback_at', sa.DateTime(), nullable=True),
        schema=DB_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column('search_logs', 'feedback_at', schema=DB_SCHEMA)
    op.drop_column('search_logs', 'feedback_comment', schema=DB_SCHEMA)
    op.drop_column('search_logs', 'feedback_expected_missing', schema=DB_SCHEMA)
    op.drop_column('search_logs', 'feedback_selected_chunk_rank', schema=DB_SCHEMA)
    op.drop_column('search_logs', 'feedback_selected_source_path', schema=DB_SCHEMA)
    op.drop_column('search_logs', 'feedback_helpful', schema=DB_SCHEMA)
