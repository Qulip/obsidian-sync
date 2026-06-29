"""add api_tokens table

Revision ID: 20260626_0002
Revises: 20260624_0001
Create Date: 2026-06-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from obsidian_sync.db.base import DB_SCHEMA

revision: str = '20260626_0002'
down_revision: str | None = '20260624_0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'api_tokens',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('token_hash', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint('token_hash', name='uq_api_tokens_token_hash'),
        schema=DB_SCHEMA,
    )
    op.create_index(
        'ix_api_tokens_token_hash',
        'api_tokens',
        ['token_hash'],
        schema=DB_SCHEMA,
    )


def downgrade() -> None:
    op.drop_index('ix_api_tokens_token_hash', table_name='api_tokens', schema=DB_SCHEMA)
    op.drop_table('api_tokens', schema=DB_SCHEMA)
