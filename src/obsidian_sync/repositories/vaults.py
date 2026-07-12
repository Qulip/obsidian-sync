from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.db.models import Vault, VaultFile


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

    async def get_file(self, vault_id: str, source_path: str) -> VaultFile | None:
        result = await self._session.execute(
            select(VaultFile).where(
                VaultFile.vault_id == vault_id,
                VaultFile.source_path == source_path,
            )
        )
        return result.scalar_one_or_none()
