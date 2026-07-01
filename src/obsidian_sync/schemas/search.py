from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from obsidian_sync.domain.enums import (
    DocumentPriority,
    DocumentStatus,
    DocumentType,
    DocumentVisibility,
)


class SearchFiltersRequest(BaseModel):
    types: list[DocumentType] | None = None
    status: list[DocumentStatus] | None = None
    priority: list[DocumentPriority] | None = None
    visibility: list[DocumentVisibility] | None = None
    tags: list[str] | None = None


class KnowledgeSearchRequest(BaseModel):
    vault_id: str
    query: str
    project: str | None = None
    domain: str | None = None
    filters: dict[str, Any] | None = None
    top_k: int | None = None


class AnswerContext(BaseModel):
    summary: str
    recommended_action: str


class KnowledgeSearchResult(BaseModel):
    rank: int
    score: float
    source_path: str
    title: str | None
    heading_path: list[str]
    type: str | None
    project: str | None
    domain: str | None
    priority: str
    status: str
    visibility: str
    tags: list[str]
    content: str
    agent_hint: str | None


class KnowledgeSearchResponse(BaseModel):
    query: str
    vault_id: str
    project: str | None
    filters: dict[str, Any]
    answer_context: AnswerContext
    results: list[KnowledgeSearchResult]


class SearchLogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: str
    token_id: str | None
    vault_id: str
    client_ip: str | None
    user_agent: str | None
    query: str
    filters: dict[str, Any] | None
    top_k: int | None
    result_count: int | None
    latency_ms: int | None
    created_at: datetime


class SearchLogsResponse(BaseModel):
    logs: list[SearchLogItem] = Field(default_factory=list)
