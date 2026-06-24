"""initial obsidian schema

Revision ID: 20260624_0001
Revises:
Create Date: 2026-06-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from obsidian_sync.db.base import DB_SCHEMA
from obsidian_sync.db.types import Vector

revision: str = '20260624_0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}')
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    op.create_table(
        'vaults',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('vault_id', sa.Text(), nullable=False, unique=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column(
            'default_visibility',
            sa.Text(),
            nullable=False,
            server_default='personal',
        ),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.CheckConstraint(
            "default_visibility IN ('personal', 'company', 'confidential', 'public')",
            name=op.f('ck_vaults_default_visibility'),
        ),
        schema=DB_SCHEMA,
    )

    op.create_table(
        'vault_files',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('vault_pk', sa.BigInteger(), nullable=False),
        sa.Column('vault_id', sa.Text(), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=False),
        sa.Column('size_bytes', sa.BigInteger()),
        sa.Column('mime_type', sa.Text()),
        sa.Column('file_type', sa.Text()),
        sa.Column('vectorize', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('status', sa.Text(), nullable=False, server_default='current'),
        sa.Column('index_status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('index_error', sa.Text()),
        sa.Column('last_synced_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('last_indexed_at', sa.DateTime()),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.CheckConstraint(
            "status IN ('current', 'draft', 'deprecated', 'archived')",
            name=op.f('ck_vault_files_status'),
        ),
        sa.CheckConstraint(
            "index_status IN ('pending', 'indexed', 'failed', 'skipped', 'archived')",
            name=op.f('ck_vault_files_index_status'),
        ),
        sa.ForeignKeyConstraint(
            ['vault_pk'],
            [f'{DB_SCHEMA}.vaults.id'],
            ondelete='CASCADE',
            name=op.f('fk_vault_files_vault_pk_vaults'),
        ),
        sa.UniqueConstraint(
            'vault_id',
            'source_path',
            name='uq_vault_files_vault_path',
        ),
        schema=DB_SCHEMA,
    )

    op.create_table(
        'knowledge_chunks',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('vault_pk', sa.BigInteger(), nullable=False),
        sa.Column('vault_id', sa.Text(), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('title', sa.Text()),
        sa.Column('heading', sa.Text()),
        sa.Column('heading_path', postgresql.ARRAY(sa.Text())),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('agent_hint', sa.Text()),
        sa.Column('project', sa.Text()),
        sa.Column('domain', sa.Text()),
        sa.Column('type', sa.Text()),
        sa.Column('status', sa.Text(), nullable=False, server_default='current'),
        sa.Column('priority', sa.Text(), nullable=False, server_default='medium'),
        sa.Column('visibility', sa.Text(), nullable=False, server_default='personal'),
        sa.Column('tags', postgresql.ARRAY(sa.Text())),
        sa.Column('content_hash', sa.Text(), nullable=False),
        sa.Column(
            'embedding_model', sa.Text(), nullable=False, server_default='bge-m3'
        ),
        sa.Column('embedding', Vector(1024)),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.CheckConstraint(
            (
                "type IN ('reference', 'rule', 'decision', 'issue-solution', "
                "'study-note', 'prompt', 'command', 'checklist')"
            ),
            name=op.f('ck_knowledge_chunks_type'),
        ),
        sa.CheckConstraint(
            "status IN ('current', 'draft', 'deprecated', 'archived')",
            name=op.f('ck_knowledge_chunks_status'),
        ),
        sa.CheckConstraint(
            "priority IN ('high', 'medium', 'low')",
            name=op.f('ck_knowledge_chunks_priority'),
        ),
        sa.CheckConstraint(
            "visibility IN ('personal', 'company', 'confidential', 'public')",
            name=op.f('ck_knowledge_chunks_visibility'),
        ),
        sa.ForeignKeyConstraint(
            ['vault_pk'],
            [f'{DB_SCHEMA}.vaults.id'],
            ondelete='CASCADE',
            name=op.f('fk_knowledge_chunks_vault_pk_vaults'),
        ),
        sa.UniqueConstraint(
            'vault_id',
            'source_path',
            'chunk_index',
            name='uq_knowledge_chunks_vault_path_chunk',
        ),
        schema=DB_SCHEMA,
    )

    op.create_table(
        'archived_vault_files',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('original_id', sa.BigInteger()),
        sa.Column('vault_pk', sa.BigInteger()),
        sa.Column('vault_id', sa.Text(), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=False),
        sa.Column('size_bytes', sa.BigInteger()),
        sa.Column('mime_type', sa.Text()),
        sa.Column('file_type', sa.Text()),
        sa.Column('archived_reason', sa.Text(), nullable=False),
        sa.Column(
            'archived_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column('archived_by', sa.Text(), server_default='system'),
        sa.Column('raw_record', postgresql.JSONB()),
        schema=DB_SCHEMA,
    )

    op.create_table(
        'archived_knowledge_chunks',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('original_id', sa.BigInteger()),
        sa.Column('vault_pk', sa.BigInteger()),
        sa.Column('vault_id', sa.Text(), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('title', sa.Text()),
        sa.Column('heading', sa.Text()),
        sa.Column('heading_path', postgresql.ARRAY(sa.Text())),
        sa.Column('content', sa.Text()),
        sa.Column('agent_hint', sa.Text()),
        sa.Column('project', sa.Text()),
        sa.Column('domain', sa.Text()),
        sa.Column('type', sa.Text()),
        sa.Column('status', sa.Text()),
        sa.Column('priority', sa.Text()),
        sa.Column('visibility', sa.Text()),
        sa.Column('tags', postgresql.ARRAY(sa.Text())),
        sa.Column('content_hash', sa.Text()),
        sa.Column('embedding_model', sa.Text()),
        sa.Column('embedding', Vector(1024)),
        sa.Column('archived_reason', sa.Text(), nullable=False),
        sa.Column(
            'archived_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column('archived_by', sa.Text(), server_default='system'),
        sa.Column('raw_record', postgresql.JSONB()),
        schema=DB_SCHEMA,
    )

    op.create_table(
        'search_logs',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('request_id', sa.Text(), nullable=False),
        sa.Column('token_id', sa.Text()),
        sa.Column('vault_pk', sa.BigInteger()),
        sa.Column('vault_id', sa.Text(), nullable=False),
        sa.Column('client_ip', sa.Text()),
        sa.Column('user_agent', sa.Text()),
        sa.Column('query', sa.Text(), nullable=False),
        sa.Column('filters', postgresql.JSONB()),
        sa.Column('top_k', sa.Integer()),
        sa.Column('result_count', sa.Integer()),
        sa.Column('latency_ms', sa.Integer()),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.ForeignKeyConstraint(
            ['vault_pk'],
            [f'{DB_SCHEMA}.vaults.id'],
            ondelete='SET NULL',
            name=op.f('fk_search_logs_vault_pk_vaults'),
        ),
        schema=DB_SCHEMA,
    )

    op.create_table(
        'index_failure_logs',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('vault_pk', sa.BigInteger()),
        sa.Column('vault_id', sa.Text(), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.Text()),
        sa.Column('phase', sa.Text(), nullable=False),
        sa.Column('error_code', sa.Text(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=False),
        sa.Column('error_details', postgresql.JSONB()),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.CheckConstraint(
            "phase IN ('frontmatter', 'chunking', 'embedding', 'database', 'unknown')",
            name=op.f('ck_index_failure_logs_phase'),
        ),
        sa.ForeignKeyConstraint(
            ['vault_pk'],
            [f'{DB_SCHEMA}.vaults.id'],
            ondelete='SET NULL',
            name=op.f('fk_index_failure_logs_vault_pk_vaults'),
        ),
        schema=DB_SCHEMA,
    )

    op.create_index(
        'idx_vault_files_vault_path',
        'vault_files',
        ['vault_id', 'source_path'],
        schema=DB_SCHEMA,
    )
    op.create_index(
        'idx_vault_files_index_status',
        'vault_files',
        ['vault_id', 'index_status'],
        schema=DB_SCHEMA,
    )
    op.create_index(
        'idx_chunks_vault_project_status',
        'knowledge_chunks',
        ['vault_id', 'project', 'status'],
        schema=DB_SCHEMA,
    )
    op.create_index('idx_chunks_type', 'knowledge_chunks', ['type'], schema=DB_SCHEMA)
    op.create_index(
        'idx_chunks_priority',
        'knowledge_chunks',
        ['priority'],
        schema=DB_SCHEMA,
    )
    op.create_index(
        'idx_chunks_tags',
        'knowledge_chunks',
        ['tags'],
        schema=DB_SCHEMA,
        postgresql_using='gin',
    )
    op.create_index(
        'idx_search_logs_vault_created',
        'search_logs',
        ['vault_id', sa.text('created_at DESC')],
        schema=DB_SCHEMA,
    )
    op.create_index(
        'idx_index_failure_logs_vault_created',
        'index_failure_logs',
        ['vault_id', sa.text('created_at DESC')],
        schema=DB_SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        'idx_index_failure_logs_vault_created',
        table_name='index_failure_logs',
        schema=DB_SCHEMA,
    )
    op.drop_index(
        'idx_search_logs_vault_created',
        table_name='search_logs',
        schema=DB_SCHEMA,
    )
    op.drop_index('idx_chunks_tags', table_name='knowledge_chunks', schema=DB_SCHEMA)
    op.drop_index(
        'idx_chunks_priority', table_name='knowledge_chunks', schema=DB_SCHEMA
    )
    op.drop_index('idx_chunks_type', table_name='knowledge_chunks', schema=DB_SCHEMA)
    op.drop_index(
        'idx_chunks_vault_project_status',
        table_name='knowledge_chunks',
        schema=DB_SCHEMA,
    )
    op.drop_index(
        'idx_vault_files_index_status',
        table_name='vault_files',
        schema=DB_SCHEMA,
    )
    op.drop_index(
        'idx_vault_files_vault_path',
        table_name='vault_files',
        schema=DB_SCHEMA,
    )
    op.drop_table('index_failure_logs', schema=DB_SCHEMA)
    op.drop_table('search_logs', schema=DB_SCHEMA)
    op.drop_table('archived_knowledge_chunks', schema=DB_SCHEMA)
    op.drop_table('archived_vault_files', schema=DB_SCHEMA)
    op.drop_table('knowledge_chunks', schema=DB_SCHEMA)
    op.drop_table('vault_files', schema=DB_SCHEMA)
    op.drop_table('vaults', schema=DB_SCHEMA)
