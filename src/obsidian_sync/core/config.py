from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_async_database_url(value: str) -> str:
    if value.startswith('postgresql://'):
        return value.replace('postgresql://', 'postgresql+asyncpg://', 1)
    return value


class Settings(BaseSettings):
    app_name: str = 'Obsidian Sync'
    app_version: str = '0.1.0'
    environment: str = 'local'
    api_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            'OBSIDIAN_SYNC_API_TOKEN',
            'KNOWLEDGE_API_TOKEN',
        ),
    )
    embedding_model: str = 'bge-m3'
    embedding_dimension: int = 1024
    ollama_base_url: str = 'http://localhost:11434'
    ollama_timeout_seconds: float = 30.0
    vault_storage_root: Path = Path('vaults')
    vault_archive_root: Path = Path('archives')
    sync_max_content_bytes: int = 10 * 1024 * 1024
    sync_changes_default_limit: int = 500
    sync_soft_delete_retention_days: int = 7
    sync_version_retention_days: int = 90
    search_min_score: float = 0.0
    search_hybrid_enabled: bool = True
    search_candidate_limit: int = 50
    search_rerank_enabled: bool = False
    search_rerank_model: str = ''
    search_rerank_candidates: int = 15
    search_per_source_limit: int = 2
    post_sync_indexing_enabled: bool = True
    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            'OBSIDIAN_SYNC_DATABASE_URL',
            'OBSIDIAN_POSTGRESQL_URL',
            'DATABASE_URL',
        ),
    )

    @field_validator('database_url')
    @classmethod
    def normalize_database_url(cls, value: str | None) -> str | None:
        if value is not None:
            return normalize_async_database_url(value)
        return value

    model_config = SettingsConfigDict(
        env_file='.env',
        env_prefix='OBSIDIAN_SYNC_',
        extra='ignore',
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
