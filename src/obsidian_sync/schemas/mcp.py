from pydantic import BaseModel, Field

from obsidian_sync.schemas.search import SearchFiltersRequest


class McpSyncFileRequest(BaseModel):
    path: str
    content: str
    mime_type: str | None = 'text/markdown'
    overwrite: bool = Field(
        default=False,
        description=(
            'Fail-closed by default: if path already exists with different '
            'content, the call returns a 409 conflict instead of silently '
            'overwriting it. Set overwrite=True to explicitly replace the '
            'existing content. Either way the write goes through the same '
            'revision bump + sync_events log as the bidirectional sync API, '
            'so base_revision clients observe the change on their next pull.'
        ),
    )


class McpKnowledgeSearchRequest(BaseModel):
    vault_id: str
    query: str
    project: str | None = None
    domain: str | None = None
    filters: SearchFiltersRequest | None = None
    top_k: int | None = None
