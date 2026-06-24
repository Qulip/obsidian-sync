from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.db.models import (
    ArchivedKnowledgeChunk,
    ArchivedVaultFile,
    KnowledgeChunk,
    Vault,
    VaultFile,
)


class VaultRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_vault(self, vault_id: str) -> Vault | None:
        result = await self._session.execute(
            select(Vault).where(Vault.vault_id == vault_id)
        )
        return result.scalar_one_or_none()

    async def list_vaults(self) -> Sequence[Vault]:
        result = await self._session.execute(
            select(Vault).order_by(Vault.created_at.desc(), Vault.vault_id)
        )
        return result.scalars().all()

    def add_vault(
        self,
        *,
        vault_id: str,
        name: str,
        description: str | None,
        default_visibility: str,
    ) -> Vault:
        vault = Vault(
            vault_id=vault_id,
            name=name,
            description=description,
            default_visibility=default_visibility,
        )
        self._session.add(vault)
        return vault

    async def list_files(self, vault_id: str) -> Sequence[VaultFile]:
        result = await self._session.execute(
            select(VaultFile).where(VaultFile.vault_id == vault_id)
        )
        return result.scalars().all()

    async def get_file(self, vault_id: str, source_path: str) -> VaultFile | None:
        result = await self._session.execute(
            select(VaultFile).where(
                VaultFile.vault_id == vault_id,
                VaultFile.source_path == source_path,
            )
        )
        return result.scalar_one_or_none()

    def add_file(
        self,
        *,
        vault: Vault,
        source_path: str,
        content_hash: str,
        size_bytes: int,
        mime_type: str | None,
        file_type: str,
        vectorize: bool,
    ) -> VaultFile:
        row = VaultFile(
            vault_pk=vault.id,
            vault_id=vault.vault_id,
            source_path=source_path,
            content_hash=content_hash,
            size_bytes=size_bytes,
            mime_type=mime_type,
            file_type=file_type,
            vectorize=vectorize,
            status='current',
            index_status='pending',
            index_error=None,
            last_synced_at=func.now(),
            updated_at=func.now(),
        )
        self._session.add(row)
        return row

    def update_file(
        self,
        row: VaultFile,
        *,
        content_hash: str,
        size_bytes: int,
        mime_type: str | None,
        file_type: str,
        vectorize: bool,
    ) -> None:
        row.content_hash = content_hash
        row.size_bytes = size_bytes
        row.mime_type = mime_type
        row.file_type = file_type
        row.vectorize = vectorize
        row.status = 'current'
        row.index_status = 'pending'
        row.index_error = None
        row.last_synced_at = func.now()
        row.updated_at = func.now()

    async def list_chunks_for_paths(
        self,
        vault_id: str,
        paths: Sequence[str],
    ) -> Sequence[KnowledgeChunk]:
        result = await self._session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.vault_id == vault_id,
                KnowledgeChunk.source_path.in_(paths),
            )
        )
        return result.scalars().all()

    def archive_file(
        self,
        row: VaultFile,
        *,
        reason: str,
        archived_by: str,
    ) -> None:
        self._session.add(
            ArchivedVaultFile(
                original_id=row.id,
                vault_pk=row.vault_pk,
                vault_id=row.vault_id,
                source_path=row.source_path,
                content_hash=row.content_hash,
                size_bytes=row.size_bytes,
                mime_type=row.mime_type,
                file_type=row.file_type,
                archived_reason=reason,
                archived_by=archived_by,
                raw_record=_raw_vault_file(row),
            )
        )

    def archive_chunk(
        self,
        row: KnowledgeChunk,
        *,
        reason: str,
        archived_by: str,
    ) -> None:
        self._session.add(
            ArchivedKnowledgeChunk(
                original_id=row.id,
                vault_pk=row.vault_pk,
                vault_id=row.vault_id,
                source_path=row.source_path,
                chunk_index=row.chunk_index,
                title=row.title,
                heading=row.heading,
                heading_path=row.heading_path,
                content=row.content,
                agent_hint=row.agent_hint,
                project=row.project,
                domain=row.domain,
                type=row.type,
                status=row.status,
                priority=row.priority,
                visibility=row.visibility,
                tags=row.tags,
                content_hash=row.content_hash,
                embedding_model=row.embedding_model,
                embedding=row.embedding,
                archived_reason=reason,
                archived_by=archived_by,
                raw_record=_raw_chunk(row),
            )
        )

    async def delete_files(self, vault_id: str, paths: Sequence[str]) -> None:
        await self._session.execute(
            delete(VaultFile).where(
                VaultFile.vault_id == vault_id,
                VaultFile.source_path.in_(paths),
            )
        )

    async def delete_chunks(self, vault_id: str, paths: Sequence[str]) -> None:
        await self._session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.vault_id == vault_id,
                KnowledgeChunk.source_path.in_(paths),
            )
        )


def _raw_vault_file(row: VaultFile) -> dict[str, Any]:
    return {
        'id': row.id,
        'vault_pk': row.vault_pk,
        'vault_id': row.vault_id,
        'source_path': row.source_path,
        'content_hash': row.content_hash,
        'size_bytes': row.size_bytes,
        'mime_type': row.mime_type,
        'file_type': row.file_type,
        'vectorize': row.vectorize,
        'status': row.status,
        'index_status': row.index_status,
        'index_error': row.index_error,
        'last_synced_at': row.last_synced_at.isoformat()
        if row.last_synced_at
        else None,
        'last_indexed_at': row.last_indexed_at.isoformat()
        if row.last_indexed_at
        else None,
    }


def _raw_chunk(row: KnowledgeChunk) -> dict[str, Any]:
    return {
        'id': row.id,
        'vault_pk': row.vault_pk,
        'vault_id': row.vault_id,
        'source_path': row.source_path,
        'chunk_index': row.chunk_index,
        'title': row.title,
        'heading': row.heading,
        'heading_path': row.heading_path,
        'content': row.content,
        'agent_hint': row.agent_hint,
        'project': row.project,
        'domain': row.domain,
        'type': row.type,
        'status': row.status,
        'priority': row.priority,
        'visibility': row.visibility,
        'tags': row.tags,
        'content_hash': row.content_hash,
        'embedding_model': row.embedding_model,
    }
