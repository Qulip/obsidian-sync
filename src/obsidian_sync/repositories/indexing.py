from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.db.models import (
    IndexFailureLog,
    KnowledgeChunk,
    Vault,
    VaultFile,
)


@dataclass(frozen=True, slots=True)
class VaultRecord:
    id: int
    vault_id: str
    default_visibility: str


@dataclass(frozen=True, slots=True)
class VaultFileRecord:
    id: int
    vault_pk: int
    vault_id: str
    source_path: str
    content_hash: str
    vectorize: bool
    status: str
    index_status: str


@dataclass(frozen=True, slots=True)
class ChunkWrite:
    vault_pk: int
    vault_id: str
    source_path: str
    chunk_index: int
    title: str
    heading: str | None
    heading_path: list[str]
    content: str
    agent_hint: str | None
    project: str
    domain: str
    document_type: str
    status: str
    priority: str
    visibility: str
    tags: list[str]
    content_hash: str
    embedding_model: str
    embedding: list[float]


class IndexingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_vault(self, vault_id: str) -> VaultRecord | None:
        result = await self.session.execute(
            select(Vault.id, Vault.vault_id, Vault.default_visibility).where(
                Vault.vault_id == vault_id,
                Vault.is_active.is_(True),
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return VaultRecord(
            id=row.id,
            vault_id=row.vault_id,
            default_visibility=row.default_visibility,
        )

    async def get_file(
        self,
        *,
        vault_id: str,
        source_path: str,
    ) -> VaultFileRecord | None:
        result = await self.session.execute(
            select(VaultFile).where(
                VaultFile.vault_id == vault_id,
                VaultFile.source_path == source_path,
            )
        )
        model = result.scalar_one_or_none()
        return _file_record(model) if model is not None else None

    async def list_reindex_files(
        self,
        *,
        vault_id: str,
        changed_only: bool,
    ) -> list[VaultFileRecord]:
        statement = select(VaultFile).where(
            VaultFile.vault_id == vault_id,
            VaultFile.status != 'archived',
            VaultFile.source_path.endswith('.md'),
        )
        if changed_only:
            statement = statement.where(VaultFile.index_status != 'indexed')
        result = await self.session.execute(statement.order_by(VaultFile.source_path))
        return [_file_record(model) for model in result.scalars()]

    async def replace_chunks(self, chunks: list[ChunkWrite]) -> tuple[int, int, int]:
        if not chunks:
            return (0, 0, 0)

        first = chunks[0]
        deleted = await self.delete_chunks(
            vault_id=first.vault_id,
            source_path=first.source_path,
        )
        self.session.add_all([_chunk_model(chunk) for chunk in chunks])
        return (len(chunks), 0, deleted)

    async def delete_chunks(self, *, vault_id: str, source_path: str) -> int:
        count_result = await self.session.execute(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.vault_id == vault_id,
                KnowledgeChunk.source_path == source_path,
            )
        )
        deleted = int(count_result.scalar_one())
        await self.session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.vault_id == vault_id,
                KnowledgeChunk.source_path == source_path,
            )
        )
        return deleted

    async def set_file_index_status(
        self,
        *,
        file_id: int,
        index_status: str,
        index_error: str | None,
        indexed: bool,
    ) -> None:
        values: dict[str, Any] = {
            'index_status': index_status,
            'index_error': index_error,
            'updated_at': func.now(),
        }
        if indexed:
            values['last_indexed_at'] = func.now()
        await self.session.execute(
            update(VaultFile).where(VaultFile.id == file_id).values(**values)
        )

    async def log_failure(
        self,
        *,
        vault_pk: int | None,
        vault_id: str,
        source_path: str,
        content_hash: str | None,
        phase: str,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            IndexFailureLog(
                vault_pk=vault_pk,
                vault_id=vault_id,
                source_path=source_path,
                content_hash=content_hash,
                phase=phase,
                error_code=error_code,
                error_message=error_message,
                error_details=error_details,
                created_at=datetime.utcnow(),
            )
        )


def _file_record(model: VaultFile) -> VaultFileRecord:
    return VaultFileRecord(
        id=model.id,
        vault_pk=model.vault_pk,
        vault_id=model.vault_id,
        source_path=model.source_path,
        content_hash=model.content_hash,
        vectorize=model.vectorize,
        status=model.status,
        index_status=model.index_status,
    )


def _chunk_model(chunk: ChunkWrite) -> KnowledgeChunk:
    return KnowledgeChunk(
        vault_pk=chunk.vault_pk,
        vault_id=chunk.vault_id,
        source_path=chunk.source_path,
        chunk_index=chunk.chunk_index,
        title=chunk.title,
        heading=chunk.heading,
        heading_path=chunk.heading_path,
        content=chunk.content,
        agent_hint=chunk.agent_hint,
        project=chunk.project,
        domain=chunk.domain,
        type=chunk.document_type,
        status=chunk.status,
        priority=chunk.priority,
        visibility=chunk.visibility,
        tags=chunk.tags,
        content_hash=chunk.content_hash,
        embedding_model=chunk.embedding_model,
        embedding=chunk.embedding,
    )
