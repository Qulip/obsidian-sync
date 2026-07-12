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
            'so base_revision clients observe the change on their next pull. '
            'Mutually exclusive with base_revision. Requires a token with '
            'overwrite permission, or the call fails with 403 -- this '
            'permission gate fires as soon as overwrite=True is set, even '
            'if path does not exist yet.'
        ),
    )
    base_revision: int | None = Field(
        default=None,
        ge=0,
        description=(
            'Opt in to strict optimistic-concurrency writes instead of the '
            'fail-closed/overwrite behavior above: the write is only applied '
            "if the server's current revision for `path` matches this value "
            '(same semantics as the bidirectional sync API), otherwise it '
            'returns a 409 sync conflict. `base_revision=0` means "create '
            'a new file" (fails if one already exists). Mutually exclusive '
            'with overwrite=True; unlike overwrite, no special token '
            'permission is required since the revision check already '
            'prevents silently clobbering concurrent writes.'
        ),
    )


class McpKnowledgeSearchRequest(BaseModel):
    vault_id: str
    query: str
    project: str | None = None
    domain: str | None = None
    filters: SearchFiltersRequest | None = None
    top_k: int | None = None
    min_score: float | None = Field(
        default=None,
        description=(
            'Minimum cosine-similarity score (0.0-1.0) a chunk must meet to '
            'be returned; falls back to the server default when omitted.'
        ),
    )
