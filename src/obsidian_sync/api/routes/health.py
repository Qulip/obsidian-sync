import asyncio
import json
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter
from pydantic import BaseModel

from obsidian_sync.api.deps import SettingsDependency
from obsidian_sync.core.responses import ResponseEnvelope, ok

router = APIRouter(tags=['health'])


class OllamaHealth(BaseModel):
    status: Literal['ok', 'unreachable', 'model_missing']
    base_url: str
    model: str


class HealthData(BaseModel):
    status: Literal['ok', 'degraded']
    app_version: str
    environment: str
    database_configured: bool
    storage_root: str
    archive_root: str
    embedding_model: str
    ollama: OllamaHealth


@router.get('/health', response_model=ResponseEnvelope[HealthData])
async def health_check(settings: SettingsDependency) -> ResponseEnvelope[HealthData]:
    ollama = await _check_ollama(settings.ollama_base_url, settings.embedding_model)
    status: Literal['ok', 'degraded'] = (
        'ok' if ollama.status == 'ok' else 'degraded'
    )
    return ok(
        HealthData(
            status=status,
            app_version=settings.app_version,
            environment=settings.environment,
            database_configured=settings.database_url is not None,
            storage_root=str(settings.vault_storage_root),
            archive_root=str(settings.vault_archive_root),
            embedding_model=settings.embedding_model,
            ollama=ollama,
        )
    )


async def _check_ollama(base_url: str, model: str) -> OllamaHealth:
    return await asyncio.to_thread(_check_ollama_sync, base_url, model)


def _check_ollama_sync(base_url: str, model: str) -> OllamaHealth:
    request = Request(f'{base_url.rstrip("/")}/api/tags', method='GET')
    try:
        with urlopen(request, timeout=2.0) as response:  # nosec B310
            raw = response.read()
        data = json.loads(raw.decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return OllamaHealth(status='unreachable', base_url=base_url, model=model)

    models = data.get('models')
    if not isinstance(models, list):
        return OllamaHealth(status='unreachable', base_url=base_url, model=model)

    names: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get('name')
        if isinstance(name, str):
            names.add(name)
    status: Literal['ok', 'model_missing'] = 'model_missing'
    if model in names or any(name.startswith(f'{model}:') for name in names):
        status = 'ok'
    return OllamaHealth(status=status, base_url=base_url, model=model)
