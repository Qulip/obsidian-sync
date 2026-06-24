from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from obsidian_sync.api.deps import SettingsDependency
from obsidian_sync.core.responses import ResponseEnvelope, ok

router = APIRouter(tags=['health'])


class HealthData(BaseModel):
    status: Literal['ok']
    postgres: Literal['not_configured']
    ollama: Literal['not_configured']
    embedding_model: str


@router.get('/health', response_model=ResponseEnvelope[HealthData])
async def health_check(settings: SettingsDependency) -> ResponseEnvelope[HealthData]:
    return ok(
        HealthData(
            status='ok',
            postgres='not_configured',
            ollama='not_configured',
            embedding_model=settings.embedding_model,
        )
    )
