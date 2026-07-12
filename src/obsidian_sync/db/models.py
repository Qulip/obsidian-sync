from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from obsidian_sync.db.base import DB_SCHEMA, Base
from obsidian_sync.db.types import Vector

DOCUMENT_TYPES = (
    'reference',
    'rule',
    'decision',
    'issue-solution',
    'study-note',
    'prompt',
    'command',
    'checklist',
)
DOCUMENT_STATUSES = ('current', 'draft', 'deprecated', 'archived')
PRIORITIES = ('high', 'medium', 'low')
VISIBILITIES = ('personal', 'company', 'confidential', 'public')
INDEX_STATUSES = ('pending', 'indexed', 'failed', 'skipped', 'archived')
INDEX_FAILURE_PHASES = ('frontmatter', 'chunking', 'embedding', 'database', 'unknown')
SYNC_EVENT_TYPES = ('CREATE', 'UPDATE', 'DELETE', 'RESTORE')
SYNC_CONFLICT_STATUSES = ('OPEN', 'RESOLVED', 'IGNORED')


def _check_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted_values = ', '.join(f"'{value}'" for value in values)
    return f'{column_name} IN ({quoted_values})'


class Vault(Base):
    __tablename__ = 'vaults'
    __table_args__ = (
        CheckConstraint(
            _check_values('default_visibility', VISIBILITIES),
            name='default_visibility',
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vault_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    default_visibility: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default='personal',
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default='true',
    )
    current_revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default='0',
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class VaultFile(Base):
    __tablename__ = 'vault_files'
    __table_args__ = (
        UniqueConstraint('vault_id', 'source_path', name='uq_vault_files_vault_path'),
        CheckConstraint(_check_values('status', DOCUMENT_STATUSES), name='status'),
        CheckConstraint(
            _check_values('index_status', INDEX_STATUSES),
            name='index_status',
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vault_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f'{DB_SCHEMA}.vaults.id', ondelete='CASCADE'),
        nullable=False,
    )
    vault_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(Text)
    file_type: Mapped[str | None] = mapped_column(Text)
    vectorize: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default='false',
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default='current')
    index_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default='pending',
    )
    index_error: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default='0',
    )
    deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default='false',
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_by_device_id: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=func.now(),
    )
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class KnowledgeChunk(Base):
    __tablename__ = 'knowledge_chunks'
    __table_args__ = (
        UniqueConstraint(
            'vault_id',
            'source_path',
            'chunk_index',
            name='uq_knowledge_chunks_vault_path_chunk',
        ),
        CheckConstraint(_check_values('type', DOCUMENT_TYPES), name='type'),
        CheckConstraint(_check_values('status', DOCUMENT_STATUSES), name='status'),
        CheckConstraint(_check_values('priority', PRIORITIES), name='priority'),
        CheckConstraint(_check_values('visibility', VISIBILITIES), name='visibility'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vault_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f'{DB_SCHEMA}.vaults.id', ondelete='CASCADE'),
        nullable=False,
    )
    vault_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    heading: Mapped[str | None] = mapped_column(Text)
    heading_path: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    agent_hint: Mapped[str | None] = mapped_column(Text)
    project: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default='current')
    priority: Mapped[str] = mapped_column(Text, nullable=False, server_default='medium')
    visibility: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default='personal',
    )
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default='bge-m3',
    )
    embedding: Mapped[Any | None] = mapped_column(Vector(1024))
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(title, '') || ' ' || content)",
            persisted=True,
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class SyncDevice(Base):
    __tablename__ = 'sync_devices'
    __table_args__ = (
        UniqueConstraint('vault_id', 'device_id', name='uq_sync_devices_vault_device'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vault_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f'{DB_SCHEMA}.vaults.id', ondelete='CASCADE'),
        nullable=False,
    )
    vault_id: Mapped[str] = mapped_column(Text, nullable=False)
    device_id: Mapped[str] = mapped_column(Text, nullable=False)
    device_name: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen_revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default='0',
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class VaultFileVersion(Base):
    __tablename__ = 'vault_file_versions'
    __table_args__ = (
        UniqueConstraint(
            'vault_id',
            'revision',
            name='uq_vault_file_versions_vault_revision',
        ),
        Index(
            'ix_vault_file_versions_vault_path_revision',
            'vault_id',
            'source_path',
            'revision',
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vault_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f'{DB_SCHEMA}.vaults.id', ondelete='CASCADE'),
        nullable=False,
    )
    vault_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    created_by_device_id: Mapped[str | None] = mapped_column(Text)


class SyncEvent(Base):
    __tablename__ = 'sync_events'
    __table_args__ = (
        UniqueConstraint('vault_id', 'revision', name='uq_sync_events_vault_revision'),
        Index('ix_sync_events_vault_revision', 'vault_id', 'revision'),
        CheckConstraint(
            _check_values('event_type', SYNC_EVENT_TYPES),
            name='event_type',
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vault_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f'{DB_SCHEMA}.vaults.id', ondelete='CASCADE'),
        nullable=False,
    )
    vault_id: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text)
    deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default='false',
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    created_by_device_id: Mapped[str | None] = mapped_column(Text)
    # Which write path produced this event: 'mcp' for a normal MCP
    # sync_file upload, 'mcp_overwrite' for an explicit overwrite=True
    # forced replace. NULL for events recorded through the bidirectional
    # (base_revision) revision sync API, preserving its existing behavior.
    origin: Mapped[str | None] = mapped_column(Text)


class SyncConflict(Base):
    __tablename__ = 'sync_conflicts'
    __table_args__ = (
        CheckConstraint(
            _check_values('status', SYNC_CONFLICT_STATUSES),
            name='status',
        ),
        Index('ix_sync_conflicts_vault_status', 'vault_id', 'status'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vault_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f'{DB_SCHEMA}.vaults.id', ondelete='CASCADE'),
        nullable=False,
    )
    vault_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    server_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    client_base_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    device_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default='OPEN')
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class ArchivedVaultFile(Base):
    __tablename__ = 'archived_vault_files'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    original_id: Mapped[int | None] = mapped_column(BigInteger)
    vault_pk: Mapped[int | None] = mapped_column(BigInteger)
    vault_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(Text)
    file_type: Mapped[str | None] = mapped_column(Text)
    archived_reason: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    archived_by: Mapped[str | None] = mapped_column(Text, server_default='system')
    raw_record: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ArchivedKnowledgeChunk(Base):
    __tablename__ = 'archived_knowledge_chunks'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    original_id: Mapped[int | None] = mapped_column(BigInteger)
    vault_pk: Mapped[int | None] = mapped_column(BigInteger)
    vault_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    heading: Mapped[str | None] = mapped_column(Text)
    heading_path: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    content: Mapped[str | None] = mapped_column(Text)
    agent_hint: Mapped[str | None] = mapped_column(Text)
    project: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    content_hash: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[Any | None] = mapped_column(Vector(1024))
    archived_reason: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    archived_by: Mapped[str | None] = mapped_column(Text, server_default='system')
    raw_record: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ApiToken(Base):
    __tablename__ = 'api_tokens'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default='true',
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    # Fail-closed: new tokens cannot force-overwrite existing MCP content
    # (McpSyncFileRequest.overwrite=True) unless explicitly granted. Tokens
    # that existed before this column was introduced are backfilled to
    # true by the migration that adds it, preserving their prior behavior.
    allow_overwrite: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default='false',
    )


class SearchLog(Base):
    __tablename__ = 'search_logs'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    token_id: Mapped[str | None] = mapped_column(Text)
    vault_pk: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(f'{DB_SCHEMA}.vaults.id', ondelete='SET NULL'),
    )
    vault_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    top_k: Mapped[int | None] = mapped_column(Integer)
    result_count: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class IndexFailureLog(Base):
    __tablename__ = 'index_failure_logs'
    __table_args__ = (
        CheckConstraint(_check_values('phase', INDEX_FAILURE_PHASES), name='phase'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vault_pk: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(f'{DB_SCHEMA}.vaults.id', ondelete='SET NULL'),
    )
    vault_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
