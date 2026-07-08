from fastapi import APIRouter, Depends

from obsidian_sync.api.routes.mcp import knowledge, sync, vaults
from obsidian_sync.core.auth import require_bearer_token

mcp_router = APIRouter(dependencies=[Depends(require_bearer_token)])
mcp_router.include_router(vaults.router)
mcp_router.include_router(sync.router)
mcp_router.include_router(knowledge.router)
