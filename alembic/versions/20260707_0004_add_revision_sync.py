"""add revision-based sync tables and columns

Revision ID: 20260707_0004
Revises: 20260701_0003
Create Date: 2026-07-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from obsidian_sync.db.base import DB_SCHEMA

revision: str = '20260707_0004'
down_revision: str | None = '20260701_0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'vaults',
        sa.Column(
            'current_revision',
            sa.BigInteger(),
            nullable=False,
            server_default='0',
        ),
        schema=DB_SCHEMA,
    )
    op.add_column(
        'vault_files',
        sa.Column('revision', sa.BigInteger(), nullable=False, server_default='0'),
        schema=DB_SCHEMA,
    )
    op.add_column(
        'vault_files',
        sa.Column('deleted', sa.Boolean(), nullable=False, server_default='false'),
        schema=DB_SCHEMA,
    )
    op.add_column(
        'vault_files',
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        schema=DB_SCHEMA,
    )
    op.add_column(
        'vault_files',
        sa.Column('updated_by_device_id', sa.Text(), nullable=True),
        schema=DB_SCHEMA,
    )

    op.create_table(
        'sync_devices',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('vault_pk', sa.BigInteger(), nullable=False),
        sa.Column('vault_id', sa.Text(), nullable=False),
        sa.Column('device_id', sa.Text(), nullable=False),
        sa.Column('device_name', sa.Text(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column(
            'last_seen_revision',
            sa.BigInteger(),
            nullable=False,
            server_default='0',
        ),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.ForeignKeyConstraint(
            ['vault_pk'],
            [f'{DB_SCHEMA}.vaults.id'],
            name='fk_sync_devices_vault_pk_vaults',
            ondelete='CASCADE',
        ),
        sa.UniqueConstraint(
            'vault_id',
            'device_id',
            name='uq_sync_devices_vault_device',
        ),
        schema=DB_SCHEMA,
    )

    op.create_table(
        'vault_file_versions',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('vault_pk', sa.BigInteger(), nullable=False),
        sa.Column('vault_id', sa.Text(), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=False),
        sa.Column('revision', sa.BigInteger(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column('created_by_device_id', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ['vault_pk'],
            [f'{DB_SCHEMA}.vaults.id'],
            name='fk_vault_file_versions_vault_pk_vaults',
            ondelete='CASCADE',
        ),
        sa.UniqueConstraint(
            'vault_id',
            'revision',
            name='uq_vault_file_versions_vault_revision',
        ),
        schema=DB_SCHEMA,
    )
    op.create_index(
        'ix_vault_file_versions_vault_path_revision',
        'vault_file_versions',
        ['vault_id', 'source_path', 'revision'],
        schema=DB_SCHEMA,
    )

    op.create_table(
        'sync_events',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('vault_pk', sa.BigInteger(), nullable=False),
        sa.Column('vault_id', sa.Text(), nullable=False),
        sa.Column('revision', sa.BigInteger(), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=True),
        sa.Column('deleted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column('created_by_device_id', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ['vault_pk'],
            [f'{DB_SCHEMA}.vaults.id'],
            name='fk_sync_events_vault_pk_vaults',
            ondelete='CASCADE',
        ),
        sa.UniqueConstraint(
            'vault_id',
            'revision',
            name='uq_sync_events_vault_revision',
        ),
        sa.CheckConstraint(
            "event_type IN ('CREATE', 'UPDATE', 'DELETE', 'RESTORE')",
            name='ck_sync_events_event_type',
        ),
        schema=DB_SCHEMA,
    )
    op.create_index(
        'ix_sync_events_vault_revision',
        'sync_events',
        ['vault_id', 'revision'],
        schema=DB_SCHEMA,
    )

    op.create_table(
        'sync_conflicts',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('vault_pk', sa.BigInteger(), nullable=False),
        sa.Column('vault_id', sa.Text(), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=False),
        sa.Column('server_revision', sa.BigInteger(), nullable=False),
        sa.Column('client_base_revision', sa.BigInteger(), nullable=False),
        sa.Column('device_id', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=False, server_default='OPEN'),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['vault_pk'],
            [f'{DB_SCHEMA}.vaults.id'],
            name='fk_sync_conflicts_vault_pk_vaults',
            ondelete='CASCADE',
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'RESOLVED', 'IGNORED')",
            name='ck_sync_conflicts_status',
        ),
        schema=DB_SCHEMA,
    )
    op.create_index(
        'ix_sync_conflicts_vault_status',
        'sync_conflicts',
        ['vault_id', 'status'],
        schema=DB_SCHEMA,
    )

    _backfill_existing_files()


def _backfill_existing_files() -> None:
    """Make pre-existing vault files syncable.

    Rows created before revision sync sit at revision 0, which can never be
    updated (base_revision 0 means create) and never surface in /sync/changes.
    Assign each existing non-deleted file a distinct revision per vault, bump
    the vault counter, and emit a CREATE sync event so clients can pull them.
    Content is served from disk on GET, so no vault_file_versions rows are
    backfilled (their content is not available in the database).
    """
    op.execute(
        f"""
        WITH ranked AS (
            SELECT
                vf.id,
                v.current_revision
                    + ROW_NUMBER() OVER (
                        PARTITION BY vf.vault_id ORDER BY vf.id
                    ) AS new_revision
            FROM {DB_SCHEMA}.vault_files vf
            JOIN {DB_SCHEMA}.vaults v ON v.id = vf.vault_pk
            WHERE vf.deleted = false
        )
        UPDATE {DB_SCHEMA}.vault_files vf
        SET revision = ranked.new_revision
        FROM ranked
        WHERE vf.id = ranked.id
        """
    )
    op.execute(
        f"""
        UPDATE {DB_SCHEMA}.vaults v
        SET current_revision = sub.max_revision
        FROM (
            SELECT vault_pk, MAX(revision) AS max_revision
            FROM {DB_SCHEMA}.vault_files
            WHERE deleted = false
            GROUP BY vault_pk
        ) sub
        WHERE v.id = sub.vault_pk
        """
    )
    op.execute(
        f"""
        INSERT INTO {DB_SCHEMA}.sync_events (
            vault_pk,
            vault_id,
            revision,
            source_path,
            event_type,
            content_hash,
            deleted,
            created_at,
            created_by_device_id
        )
        SELECT
            vault_pk,
            vault_id,
            revision,
            source_path,
            'CREATE',
            content_hash,
            false,
            now(),
            NULL
        FROM {DB_SCHEMA}.vault_files
        WHERE deleted = false AND revision > 0
        """
    )


def downgrade() -> None:
    op.drop_index(
        'ix_sync_conflicts_vault_status',
        table_name='sync_conflicts',
        schema=DB_SCHEMA,
    )
    op.drop_table('sync_conflicts', schema=DB_SCHEMA)
    op.drop_index(
        'ix_sync_events_vault_revision',
        table_name='sync_events',
        schema=DB_SCHEMA,
    )
    op.drop_table('sync_events', schema=DB_SCHEMA)
    op.drop_index(
        'ix_vault_file_versions_vault_path_revision',
        table_name='vault_file_versions',
        schema=DB_SCHEMA,
    )
    op.drop_table('vault_file_versions', schema=DB_SCHEMA)
    op.drop_table('sync_devices', schema=DB_SCHEMA)
    op.drop_column('vault_files', 'updated_by_device_id', schema=DB_SCHEMA)
    op.drop_column('vault_files', 'deleted_at', schema=DB_SCHEMA)
    op.drop_column('vault_files', 'deleted', schema=DB_SCHEMA)
    op.drop_column('vault_files', 'revision', schema=DB_SCHEMA)
    op.drop_column('vaults', 'current_revision', schema=DB_SCHEMA)
