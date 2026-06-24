import re
from dataclasses import dataclass
from typing import Any

from obsidian_sync.domain.enums import (
    DocumentPriority,
    DocumentStatus,
    DocumentType,
    DocumentVisibility,
)
from obsidian_sync.domain.errors import DomainValidationError

REQUIRED_FRONTMATTER_FIELDS = frozenset(
    {
        'title',
        'type',
        'project',
        'domain',
        'status',
        'priority',
        'visibility',
        'tags',
        'vectorize',
        'created',
        'updated',
    }
)

_DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')


@dataclass(frozen=True, slots=True)
class DocumentFrontmatter:
    title: str
    document_type: DocumentType
    project: str
    domain: str
    status: DocumentStatus
    priority: DocumentPriority
    visibility: DocumentVisibility
    tags: tuple[str, ...]
    vectorize: bool
    created: str
    updated: str


def split_frontmatter(markdown: str) -> tuple[str, str]:
    lines = markdown.splitlines(keepends=True)
    if not lines or lines[0].strip() != '---':
        raise DomainValidationError('markdown frontmatter is required')

    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == '---':
            raw_frontmatter = ''.join(lines[1:index])
            body = ''.join(lines[index + 1 :])
            return raw_frontmatter, body

    raise DomainValidationError('markdown frontmatter closing delimiter is required')


def strip_frontmatter(markdown: str) -> str:
    _, body = split_frontmatter(markdown)
    return body


def parse_frontmatter(markdown: str) -> DocumentFrontmatter:
    raw_frontmatter, _ = split_frontmatter(markdown)
    parsed = _parse_frontmatter_mapping(raw_frontmatter)
    return validate_frontmatter(parsed)


def validate_frontmatter(values: dict[str, object]) -> DocumentFrontmatter:
    missing = sorted(REQUIRED_FRONTMATTER_FIELDS.difference(values))
    if missing:
        raise DomainValidationError(
            'frontmatter is missing required fields',
            {'missing': missing},
        )

    return DocumentFrontmatter(
        title=_require_string(values, 'title'),
        document_type=_require_enum(values, 'type', DocumentType),
        project=_require_string(values, 'project'),
        domain=_require_string(values, 'domain'),
        status=_require_enum(values, 'status', DocumentStatus),
        priority=_require_enum(values, 'priority', DocumentPriority),
        visibility=_require_enum(values, 'visibility', DocumentVisibility),
        tags=_require_tags(values['tags']),
        vectorize=_require_bool(values, 'vectorize'),
        created=_require_date(values, 'created'),
        updated=_require_date(values, 'updated'),
    )


def _parse_frontmatter_mapping(raw_frontmatter: str) -> dict[str, object]:
    result: dict[str, object] = {}
    lines = raw_frontmatter.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line[:1].isspace():
            raise DomainValidationError('frontmatter nested mappings are not supported')
        key, raw_value = _split_key_value(line)
        if raw_value == '':
            items: list[str] = []
            while index < len(lines) and lines[index].startswith('  - '):
                items.append(_parse_string_scalar(lines[index][4:].strip()))
                index += 1
            result[key] = items if items else ''
        else:
            result[key] = _parse_scalar(raw_value)
    return result


def _split_key_value(line: str) -> tuple[str, str]:
    if ':' not in line:
        raise DomainValidationError('frontmatter line must use key: value syntax')
    key, raw_value = line.split(':', 1)
    normalized_key = key.strip()
    if not normalized_key:
        raise DomainValidationError('frontmatter key cannot be empty')
    return normalized_key, raw_value.strip()


def _parse_scalar(raw_value: str) -> object:
    if raw_value == '[]':
        return []
    if raw_value.startswith('[') and raw_value.endswith(']'):
        inner = raw_value[1:-1].strip()
        if not inner:
            return []
        return [_parse_string_scalar(item.strip()) for item in inner.split(',')]
    if raw_value in {'true', 'false'}:
        return raw_value == 'true'
    return _parse_string_scalar(raw_value)


def _parse_string_scalar(raw_value: str) -> str:
    is_quoted = (
        len(raw_value) >= 2
        and raw_value[0] == raw_value[-1]
        and raw_value[0] in {'"', "'"}
    )
    if is_quoted:
        return raw_value[1:-1]
    return raw_value


def _require_string(values: dict[str, object], key: str) -> str:
    value = values[key]
    if not isinstance(value, str):
        raise DomainValidationError(f'frontmatter field {key} must be a string')
    return value


def _require_bool(values: dict[str, object], key: str) -> bool:
    value = values[key]
    if not isinstance(value, bool):
        raise DomainValidationError(f'frontmatter field {key} must be a boolean')
    return value


def _require_date(values: dict[str, object], key: str) -> str:
    value = _require_string(values, key)
    if not _DATE_PATTERN.fullmatch(value):
        raise DomainValidationError(
            f'frontmatter field {key} must use YYYY-MM-DD format',
            {'field': key, 'value': value},
        )
    return value


def _require_enum[T: str](
    values: dict[str, object],
    key: str,
    enum_type: type[T],
) -> T:
    value = _require_string(values, key)
    try:
        return enum_type(value)
    except ValueError as exc:
        raise DomainValidationError(
            f'frontmatter field {key} has an unsupported value',
            {'field': key, 'value': value},
        ) from exc


def _require_tags(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise DomainValidationError('frontmatter field tags must be a list')
    tags: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise DomainValidationError('frontmatter tags must be strings')
        if item:
            tags.append(item)
    return tuple(tags)
