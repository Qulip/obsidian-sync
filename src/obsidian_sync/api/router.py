from fastapi import APIRouter, Depends

from obsidian_sync.api.routes import health, knowledge, search_logs, tokens, vaults
from obsidian_sync.core.auth import require_admin_token, require_bearer_token

api_router = APIRouter(dependencies=[Depends(require_bearer_token)])
api_router.include_router(health.router)
api_router.include_router(vaults.router)
api_router.include_router(knowledge.router)
api_router.include_router(search_logs.router)

admin_router = APIRouter(dependencies=[Depends(require_admin_token)])
admin_router.include_router(tokens.router)
