from collections.abc import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.db.models import (
    KnowledgeChunk,
    SyncConflict,
    SyncDevice,
    SyncEvent,
    Vault,
    VaultFile,
    VaultFileVersion,
)


class SyncRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_vault(self, vault_id: str) -> Vault | None:
        result = await self._session.execute(
            select(Vault).where(Vault.vault_id == vault_id)
        )
        return result.scalar_one_or_none()

    async def next_revision(self, vault_pk: int) -> int:
        result = await self._session.execute(
            update(Vault)
            .where(Vault.id == vault_pk)
            .values(current_revision=Vault.current_revision + 1)
            .returning(Vault.current_revision)
        )
        return int(result.scalar_one())

    async def get_current_revision(self, vault_pk: int) -> int:
        result = await self._session.execute(
            select(Vault.current_revision).where(Vault.id == vault_pk)
        )
        return int(result.scalar_one())

    async def get_device(self, vault_id: str, device_id: str) -> SyncDevice | None:
        result = await self._session.execute(
            select(SyncDevice).where(
                SyncDevice.vault_id == vault_id,
                SyncDevice.device_id == device_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_device(
        self,
        *,
        vault: Vault,
        device_id: str,
        device_name: str | None,
    ) -> SyncDevice:
        device = await self.get_device(vault.vault_id, device_id)
        if device is None:
            device = SyncDevice(
                vault_pk=vault.id,
                vault_id=vault.vault_id,
                device_id=device_id,
                device_name=device_name,
                last_seen_at=func.now(),
            )
            self._session.add(device)
            return device
        if device_name is not None:
            device.device_name = device_name
        device.last_seen_at = func.now()
        return device

    async def touch_device(
        self,
        *,
        vault: Vault,
        device_id: str,
        last_seen_revision: int,
    ) -> None:
        device = await self.upsert_device(
            vault=vault,
            device_id=device_id,
            device_name=None,
        )
        device.last_seen_revision = last_seen_revision

    async def get_file(self, vault_id: str, source_path: str) -> VaultFile | None:
        result = await self._session.execute(
            select(VaultFile).where(
                VaultFile.vault_id == vault_id,
                VaultFile.source_path == source_path,
            )
        )
        return result.scalar_one_or_none()

    async def get_file_for_update(
        self, vault_id: str, source_path: str
    ) -> VaultFile | None:
        """Read a vault file row and hold a row lock for the transaction.

        Serializes concurrent write paths (put/delete/restore) on the same
        path so the base_revision check observes the latest committed
        revision instead of a stale snapshot under READ COMMITTED.
        """
        result = await self._session.execute(
            select(VaultFile)
            .where(
                VaultFile.vault_id == vault_id,
                VaultFile.source_path == source_path,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_events_since(
        self,
        *,
        vault_id: str,
        since: int,
        limit: int,
    ) -> Sequence[SyncEvent]:
        result = await self._session.execute(
            select(SyncEvent)
            .where(SyncEvent.vault_id == vault_id, SyncEvent.revision > since)
            .order_by(SyncEvent.revision)
            .limit(limit)
        )
        return result.scalars().all()

    async def count_events_since(self, *, vault_id: str, since: int) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(SyncEvent)
            .where(SyncEvent.vault_id == vault_id, SyncEvent.revision > since)
        )
        return int(result.scalar_one())

    def add_event(
        self,
        *,
        vault: Vault,
        revision: int,
        source_path: str,
        event_type: str,
        content_hash: str | None,
        deleted: bool,
        device_id: str | None,
        origin: str | None = None,
    ) -> SyncEvent:
        event = SyncEvent(
            vault_pk=vault.id,
            vault_id=vault.vault_id,
            revision=revision,
            source_path=source_path,
            event_type=event_type,
            content_hash=content_hash,
            deleted=deleted,
            created_by_device_id=device_id,
            origin=origin,
        )
        self._session.add(event)
        return event

    def add_version(
        self,
        *,
        vault: Vault,
        source_path: str,
        revision: int,
        content_hash: str,
        content: str,
        size_bytes: int,
        event_type: str,
        device_id: str | None,
    ) -> VaultFileVersion:
        version = VaultFileVersion(
            vault_pk=vault.id,
            vault_id=vault.vault_id,
            source_path=source_path,
            revision=revision,
            content_hash=content_hash,
            content=content,
            size_bytes=size_bytes,
            event_type=event_type,
            created_by_device_id=device_id,
        )
        self._session.add(version)
        return version

    def add_conflict(
        self,
        *,
        vault: Vault,
        source_path: str,
        server_revision: int,
        client_base_revision: int,
        device_id: str | None,
    ) -> SyncConflict:
        conflict = SyncConflict(
            vault_pk=vault.id,
            vault_id=vault.vault_id,
            source_path=source_path,
            server_revision=server_revision,
            client_base_revision=client_base_revision,
            device_id=device_id,
            status='OPEN',
        )
        self._session.add(conflict)
        return conflict

    async def get_version(
        self,
        *,
        vault_id: str,
        source_path: str,
        revision: int | None,
    ) -> VaultFileVersion | None:
        statement = select(VaultFileVersion).where(
            VaultFileVersion.vault_id == vault_id,
            VaultFileVersion.source_path == source_path,
        )
        if revision is not None:
            statement = statement.where(VaultFileVersion.revision == revision)
        result = await self._session.execute(
            statement.order_by(VaultFileVersion.revision.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def count_open_conflicts(self, vault_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(SyncConflict)
            .where(
                SyncConflict.vault_id == vault_id,
                SyncConflict.status == 'OPEN',
            )
        )
        return int(result.scalar_one())

    async def count_pending_vectorizing(self, vault_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(VaultFile)
            .where(
                VaultFile.vault_id == vault_id,
                VaultFile.index_status == 'pending',
                VaultFile.vectorize.is_(True),
                VaultFile.deleted.is_(False),
            )
        )
        return int(result.scalar_one())

    async def delete_chunks(self, vault_id: str, source_path: str) -> int:
        count_result = await self._session.execute(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.vault_id == vault_id,
                KnowledgeChunk.source_path == source_path,
            )
        )
        deleted = int(count_result.scalar_one())
        await self._session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.vault_id == vault_id,
                KnowledgeChunk.source_path == source_path,
            )
        )
        return deleted
