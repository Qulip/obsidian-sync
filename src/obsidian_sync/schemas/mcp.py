from pydantic import BaseModel


class McpSyncFileRequest(BaseModel):
    path: str
    content: str
    mime_type: str | None = 'text/markdown'
