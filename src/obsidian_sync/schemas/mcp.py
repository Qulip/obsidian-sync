from pydantic import BaseModel

from obsidian_sync.schemas.search import SearchFiltersRequest


class McpSyncFileRequest(BaseModel):
    path: str
    content: str
    mime_type: str | None = 'text/markdown'


class McpKnowledgeSearchRequest(BaseModel):
    vault_id: str
    query: str
    project: str | None = None
    domain: str | None = None
    filters: SearchFiltersRequest | None = None
    top_k: int | None = None
