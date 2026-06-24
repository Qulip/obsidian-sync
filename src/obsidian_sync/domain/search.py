import re
from collections.abc import Mapping
from dataclasses import dataclass

from obsidian_sync.domain.enums import (
    DocumentPriority,
    DocumentStatus,
    DocumentType,
    DocumentVisibility,
)
from obsidian_sync.domain.errors import DomainValidationError

DEFAULT_TOP_K = 5
MAX_TOP_K = 10

_VAULT_ID_PATTERN = re.compile(r'^[a-z0-9-]+$')


@dataclass(frozen=True, slots=True)
class SearchFilters:
    types: tuple[DocumentType, ...] = ()
    status: tuple[DocumentStatus, ...] = (DocumentStatus.CURRENT,)
    priority: tuple[DocumentPriority, ...] = ()
    visibility: tuple[DocumentVisibility, ...] = ()
    tags: tuple[str, ...] = ()
    project: str | None = None
    domain: str | None = None
    vectorize: bool = True


@dataclass(frozen=True, slots=True)
class NormalizedSearchQuery:
    vault_id: str
    query: str
    filters: SearchFilters
    top_k: int


def normalize_top_k(top_k: int | None) -> int:
    if top_k is None:
        return DEFAULT_TOP_K
    if top_k < 1:
        raise DomainValidationError('top_k must be at least 1')
    if top_k > MAX_TOP_K:
        raise DomainValidationError(
            'top_k exceeds maximum',
            {'top_k': top_k, 'max_top_k': MAX_TOP_K},
        )
    return top_k


def normalize_search_query(
    *,
    vault_id: str,
    query: str,
    filters: Mapping[str, object] | None = None,
    top_k: int | None = None,
    project: str | None = None,
    domain: str | None = None,
) -> NormalizedSearchQuery:
    normalized_vault_id = vault_id.strip()
    if not normalized_vault_id:
        raise DomainValidationError('vault_id is required')
    if _VAULT_ID_PATTERN.fullmatch(normalized_vault_id) is None:
        raise DomainValidationError(
            'vault_id must contain lowercase letters, numbers, and hyphens only'
        )

    normalized_query = query.strip()
    if not normalized_query:
        raise DomainValidationError('query is required')

    return NormalizedSearchQuery(
        vault_id=normalized_vault_id,
        query=normalized_query,
        filters=normalize_search_filters(filters, project=project, domain=domain),
        top_k=normalize_top_k(top_k),
    )


def normalize_search_filters(
    filters: Mapping[str, object] | None = None,
    *,
    project: str | None = None,
    domain: str | None = None,
) -> SearchFilters:
    raw_filters = filters or {}
    return SearchFilters(
        types=_normalize_enum_list(raw_filters.get('types'), DocumentType, 'types'),
        status=_normalize_enum_list(
            raw_filters.get('status'),
            DocumentStatus,
            'status',
            default=(DocumentStatus.CURRENT,),
        ),
        priority=_normalize_enum_list(
            raw_filters.get('priority'),
            DocumentPriority,
            'priority',
        ),
        visibility=_normalize_enum_list(
            raw_filters.get('visibility'),
            DocumentVisibility,
            'visibility',
        ),
        tags=_normalize_string_list(raw_filters.get('tags'), 'tags'),
        project=_normalize_optional_string(
            project or raw_filters.get('project'),
            'project',
        ),
        domain=_normalize_optional_string(
            domain or raw_filters.get('domain'),
            'domain',
        ),
        vectorize=True,
    )


def _normalize_enum_list[T: str](
    value: object,
    enum_type: type[T],
    field: str,
    *,
    default: tuple[T, ...] = (),
) -> tuple[T, ...]:
    if value is None:
        return default
    values = _require_list(value, field)
    normalized: list[T] = []
    for item in values:
        if not isinstance(item, str):
            raise DomainValidationError(f'filter {field} values must be strings')
        try:
            normalized.append(enum_type(item))
        except ValueError as exc:
            raise DomainValidationError(
                f'filter {field} has an unsupported value',
                {'field': field, 'value': item},
            ) from exc
    return tuple(dict.fromkeys(normalized))


def _normalize_string_list(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    values = _require_list(value, field)
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise DomainValidationError(f'filter {field} values must be strings')
        stripped = item.strip()
        if stripped:
            normalized.append(stripped)
    return tuple(dict.fromkeys(normalized))


def _normalize_optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainValidationError(f'filter {field} must be a string')
    stripped = value.strip()
    return stripped or None


def _require_list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise DomainValidationError(f'filter {field} must be a list')
    return value
