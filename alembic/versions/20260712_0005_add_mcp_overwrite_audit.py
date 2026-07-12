"""add mcp overwrite audit columns

Revision ID: 20260712_0005
Revises: 20260707_0004
Create Date: 2026-07-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from obsidian_sync.db.base import DB_SCHEMA

revision: str = '20260712_0005'
down_revision: str | None = '20260707_0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'sync_events',
        sa.Column('origin', sa.Text(), nullable=True),
        schema=DB_SCHEMA,
    )
    op.add_column(
        'api_tokens',
        sa.Column(
            'allow_overwrite',
            sa.Boolean(),
            nullable=False,
            server_default='false',
        ),
        schema=DB_SCHEMA,
    )
    _backfill_existing_tokens()


def _backfill_existing_tokens() -> None:
    """Preserve pre-existing tokens' ability to force-overwrite via MCP.

    `allow_overwrite` defaults to false for newly created tokens, but
    tokens that were already deployed before this column existed relied on
    `McpSyncFileRequest.overwrite=True` working unconditionally. Grant
    them the permission explicitly so this migration does not silently
    revoke a capability already in use.
    """
    op.execute(f'UPDATE {DB_SCHEMA}.api_tokens SET allow_overwrite = true')


def downgrade() -> None:
    op.drop_column('api_tokens', 'allow_overwrite', schema=DB_SCHEMA)
    op.drop_column('sync_events', 'origin', schema=DB_SCHEMA)
