from pathlib import Path
from typing import Any

from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.config import Settings
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.domain.agent_hint import build_agent_hint
from obsidian_sync.domain.chunking import chunk_markdown
from obsidian_sync.domain.embeddings import format_chunk_embedding_input
from obsidian_sync.domain.errors import DomainValidationError
from obsidian_sync.domain.frontmatter import parse_frontmatter
from obsidian_sync.domain.hashing import sha256_text
from obsidian_sync.domain.paths import normalize_source_path, safe_vault_destination
from obsidian_sync.domain.sync_rules import is_vectorizable_path
from obsidian_sync.repositories.indexing import (
    ChunkWrite,
    IndexingRepository,
    VaultFileRecord,
)
from obsidian_sync.schemas.indexing import ReindexMode, ReindexResult


class ReindexService:
    def __init__(
        self,
        *,
        repository: IndexingRepository,
        ollama_client: OllamaClient,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.ollama_client = ollama_client
        self.settings = settings

    async def reindex_vault(
        self,
        *,
        vault_id: str,
        mode: ReindexMode,
    ) -> ReindexResult:
        vault = await self.repository.get_vault(vault_id)
        if vault is None:
            raise AppError(ErrorCode.NOT_FOUND, 'Vault was not found.', status_code=404)

        records = await self.repository.list_reindex_files(
            vault_id=vault_id,
            changed_only=mode is ReindexMode.CHANGED_ONLY,
        )
        result = ReindexResult(vault_id=vault_id, mode=mode.value)
        for record in records:
            try:
                markdown = await self._read_vault_file(vault_id, record.source_path)
            except Exception as exc:
                await self._record_failure(record, exc)
                result.failed_files += 1
                result.failures.append(record.source_path)
                continue
            await self._index_record(record, markdown, result)
        return result

    async def reindex_file(
        self,
        *,
        vault_id: str,
        source_path: str,
        content: str | None,
    ) -> ReindexResult:
        path = normalize_source_path(source_path)
        vault = await self.repository.get_vault(vault_id)
        if vault is None:
            raise AppError(ErrorCode.NOT_FOUND, 'Vault was not found.', status_code=404)

        record = await self.repository.get_file(vault_id=vault_id, source_path=path)
        if record is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                'Vault file was not found.',
                status_code=404,
                details={'source_path': path},
            )

        markdown = (
            content
            if content is not None
            else await self._read_vault_file(
                vault_id,
                path,
            )
        )
        result = ReindexResult(vault_id=vault_id, mode='file')
        await self._index_record(record, markdown, result)
        return result

    async def _read_vault_file(self, vault_id: str, source_path: str) -> str:
        vault_root = (Path(self.settings.vault_storage_root) / vault_id).resolve(
            strict=False
        )
        path = safe_vault_destination(vault_root, source_path)
        try:
            return path.read_text(encoding='utf-8')
        except OSError as exc:
            raise AppError(
                ErrorCode.INDEXING_FAILED,
                'Vault file could not be read.',
                status_code=500,
                details={'source_path': source_path, 'error': str(exc)},
            ) from exc

    async def _index_record(
        self,
        record: VaultFileRecord,
        markdown: str,
        result: ReindexResult,
    ) -> None:
        if (
            not record.vectorize
            or record.deleted
            or not is_vectorizable_path(record.source_path)
        ):
            deleted = await self.repository.delete_chunks(
                vault_id=record.vault_id,
                source_path=record.source_path,
            )
            await self.repository.set_file_index_status(
                file_id=record.id,
                index_status='skipped',
                index_error=None,
                indexed=False,
            )
            result.deleted_chunks += deleted
            result.skipped_files += 1
            return

        try:
            frontmatter = parse_frontmatter(markdown)
            if not frontmatter.vectorize or frontmatter.status != 'current':
                deleted = await self.repository.delete_chunks(
                    vault_id=record.vault_id,
                    source_path=record.source_path,
                )
                await self.repository.set_file_index_status(
                    file_id=record.id,
                    index_status='skipped',
                    index_error=None,
                    indexed=False,
                )
                result.deleted_chunks += deleted
                result.skipped_files += 1
                return

            chunks = chunk_markdown(markdown)
            if not chunks:
                raise DomainValidationError('markdown produced no indexable chunks')

            fresh_content_hash = sha256_text(markdown)
            writes: list[ChunkWrite] = []
            for chunk in chunks:
                embedding_input = format_chunk_embedding_input(frontmatter, chunk)
                embedding = await self.ollama_client.embed(embedding_input)
                if len(embedding) != self.settings.embedding_dimension:
                    raise AppError(
                        ErrorCode.EMBEDDING_FAILED,
                        'Embedding dimension did not match configuration.',
                        status_code=502,
                        details={
                            'expected': self.settings.embedding_dimension,
                            'actual': len(embedding),
                        },
                    )
                writes.append(
                    ChunkWrite(
                        vault_pk=record.vault_pk,
                        vault_id=record.vault_id,
                        source_path=record.source_path,
                        chunk_index=chunk.chunk_index,
                        title=frontmatter.title,
                        heading=chunk.heading,
                        heading_path=list(chunk.heading_path),
                        content=chunk.content,
                        agent_hint=build_agent_hint(frontmatter, chunk),
                        project=frontmatter.project,
                        domain=frontmatter.domain,
                        document_type=frontmatter.document_type.value,
                        status=frontmatter.status.value,
                        priority=frontmatter.priority.value,
                        visibility=frontmatter.visibility.value,
                        tags=list(frontmatter.tags),
                        content_hash=fresh_content_hash,
                        embedding_model=self.settings.embedding_model,
                        embedding=embedding,
                    )
                )

            created, updated, deleted = await self.repository.replace_chunks(writes)
            current_record = await self.repository.get_file(
                vault_id=record.vault_id,
                source_path=record.source_path,
            )
            if (
                current_record is None
                or current_record.deleted
                or current_record.content_hash != fresh_content_hash
            ):
                await self.repository.set_file_index_status(
                    file_id=record.id,
                    index_status='pending',
                    index_error=None,
                    indexed=False,
                )
                result.created_chunks += created
                result.updated_chunks += updated
                result.deleted_chunks += deleted
                return
            await self.repository.set_file_index_status(
                file_id=record.id,
                index_status='indexed',
                index_error=None,
                indexed=True,
            )
            result.indexed_files += 1
            result.created_chunks += created
            result.updated_chunks += updated
            result.deleted_chunks += deleted
        except Exception as exc:
            await self._record_failure(record, exc)
            result.failed_files += 1
            result.failures.append(record.source_path)

    async def _record_failure(self, record: VaultFileRecord, exc: Exception) -> None:
        message = str(exc) or exc.__class__.__name__
        await self.repository.set_file_index_status(
            file_id=record.id,
            index_status='failed',
            index_error=message,
            indexed=False,
        )
        await self.repository.log_failure(
            vault_pk=record.vault_pk,
            vault_id=record.vault_id,
            source_path=record.source_path,
            content_hash=record.content_hash,
            phase=_failure_phase(exc),
            error_code=_failure_code(exc),
            error_message=message,
            error_details=_failure_details(exc),
        )


def _failure_phase(exc: Exception) -> str:
    if isinstance(exc, DomainValidationError):
        return 'frontmatter'
    if isinstance(exc, AppError) and exc.code is ErrorCode.EMBEDDING_FAILED:
        return 'embedding'
    return 'unknown'


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.code.value
    if isinstance(exc, DomainValidationError):
        return ErrorCode.VALIDATION_ERROR.value
    return ErrorCode.INDEXING_FAILED.value


def _failure_details(exc: Exception) -> dict[str, Any] | None:
    if isinstance(exc, AppError):
        return exc.details
    if isinstance(exc, DomainValidationError):
        return exc.details
    return None
