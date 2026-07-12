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
    min_score: float | None = None


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
    revision: int | None = None
    updated_at: datetime | None = None


class KnowledgeSearchResponse(BaseModel):
    request_id: str
    query: str
    vault_id: str
    project: str | None
    filters: dict[str, Any]
    answer_context: AnswerContext
    results: list[KnowledgeSearchResult]
    pending_vectorizing_jobs: int = 0
    failed_vectorizing_jobs: int = 0
    index_fresh: bool = True
    min_score: float | None = None
    low_confidence: bool = False
    no_candidates: bool = False
    reranked: bool = False


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
    feedback_helpful: bool | None = None
    feedback_selected_source_path: str | None = None
    feedback_selected_chunk_rank: int | None = None
    feedback_expected_missing: bool | None = None
    feedback_comment: str | None = None
    feedback_at: datetime | None = None


class SearchLogsResponse(BaseModel):
    logs: list[SearchLogItem] = Field(default_factory=list)


class SearchFeedbackRequest(BaseModel):
    request_id: str
    vault_id: str
    helpful: bool | None = None
    selected_source_path: str | None = None
    selected_chunk_rank: int | None = None
    expected_missing: bool | None = None
    comment: str | None = None


class SearchFeedbackResponse(BaseModel):
    request_id: str
    vault_id: str
    feedback_at: datetime
