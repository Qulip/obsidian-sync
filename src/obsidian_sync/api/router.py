from fastapi import APIRouter, Depends

from obsidian_sync.api.routes import health, knowledge, search_logs, vaults
from obsidian_sync.core.auth import require_bearer_token

api_router = APIRouter(dependencies=[Depends(require_bearer_token)])
api_router.include_router(health.router)
api_router.include_router(vaults.router)
api_router.include_router(knowledge.router)
api_router.include_router(search_logs.router)
