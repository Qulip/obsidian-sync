import re
from dataclasses import dataclass

from obsidian_sync.domain.frontmatter import strip_frontmatter

MIN_CHUNK_TOKENS = 150
TARGET_MIN_CHUNK_TOKENS = 600
TARGET_MAX_CHUNK_TOKENS = 900
MAX_CHUNK_TOKENS = 1200
DEFAULT_OVERLAP_TOKENS = 100

_HEADING_PATTERN = re.compile(r'^(#{1,3})\s+(.+?)\s*#*\s*$')
_TOKEN_PATTERN = re.compile(r'\w+|[^\w\s]', re.UNICODE)


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    chunk_index: int
    content: str
    heading_path: tuple[str, ...]
    token_count: int

    @property
    def heading(self) -> str | None:
        return self.heading_path[-1] if self.heading_path else None


@dataclass(frozen=True, slots=True)
class _Section:
    content: str
    heading_path: tuple[str, ...]


def estimate_token_count(text: str) -> int:
    return len(_TOKEN_PATTERN.findall(text))


def chunk_markdown(
    markdown: str,
    *,
    min_tokens: int = MIN_CHUNK_TOKENS,
    max_tokens: int = MAX_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[MarkdownChunk]:
    body = strip_frontmatter(markdown)
    sections = _split_heading_sections(body)
    merged_sections = _merge_short_sections(sections, min_tokens)

    chunks: list[MarkdownChunk] = []
    for section in merged_sections:
        for content in _split_large_section(
            section.content,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
        ):
            normalized = content.strip()
            if normalized:
                chunks.append(
                    MarkdownChunk(
                        chunk_index=len(chunks),
                        content=normalized,
                        heading_path=section.heading_path,
                        token_count=estimate_token_count(normalized),
                    )
                )
    return chunks


def _split_heading_sections(markdown_body: str) -> list[_Section]:
    sections: list[_Section] = []
    heading_stack: list[str] = []
    current_lines: list[str] = []
    current_path: tuple[str, ...] = ()
    in_fence = False
    fence_marker = ''

    for line in markdown_body.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(('```', '~~~')):
            marker = stripped[:3]
            if in_fence and marker == fence_marker:
                in_fence = False
                fence_marker = ''
            elif not in_fence:
                in_fence = True
                fence_marker = marker

        match = None if in_fence else _HEADING_PATTERN.match(line.rstrip('\r\n'))
        if match is not None:
            _append_section(sections, current_lines, current_path)
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(title)
            current_path = tuple(heading_stack)
            current_lines = [line]
        else:
            current_lines.append(line)

    _append_section(sections, current_lines, current_path)
    return sections


def _append_section(
    sections: list[_Section],
    lines: list[str],
    heading_path: tuple[str, ...],
) -> None:
    content = ''.join(lines).strip()
    if content:
        sections.append(_Section(content=content, heading_path=heading_path))


def _merge_short_sections(sections: list[_Section], min_tokens: int) -> list[_Section]:
    merged: list[_Section] = []
    pending: _Section | None = None
    for section in sections:
        if pending is None:
            pending = section
        elif estimate_token_count(pending.content) < min_tokens:
            pending = _Section(
                content=f'{pending.content}\n\n{section.content}',
                heading_path=pending.heading_path or section.heading_path,
            )
        else:
            merged.append(pending)
            pending = section
    if pending is not None:
        if merged and estimate_token_count(pending.content) < min_tokens:
            previous = merged.pop()
            merged.append(
                _Section(
                    content=f'{previous.content}\n\n{pending.content}',
                    heading_path=previous.heading_path,
                )
            )
        else:
            merged.append(pending)
    return merged


def _split_large_section(
    content: str,
    *,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    if estimate_token_count(content) <= max_tokens:
        return [content]

    paragraphs = re.split(r'\n\s*\n', content)
    chunks: list[str] = []
    current: list[str] = []
    for paragraph in paragraphs:
        candidate = '\n\n'.join([*current, paragraph]).strip()
        if current and estimate_token_count(candidate) > max_tokens:
            chunk = '\n\n'.join(current).strip()
            chunks.append(chunk)
            current = _overlap_paragraphs(chunk, overlap_tokens)
        current.append(paragraph)

    if current:
        chunks.append('\n\n'.join(current).strip())

    return [chunk for chunk in chunks if chunk]


def _overlap_paragraphs(chunk: str, overlap_tokens: int) -> list[str]:
    if overlap_tokens <= 0:
        return []
    paragraphs = [paragraph for paragraph in re.split(r'\n\s*\n', chunk) if paragraph]
    overlap: list[str] = []
    total = 0
    for paragraph in reversed(paragraphs):
        token_count = estimate_token_count(paragraph)
        if overlap and total + token_count > overlap_tokens:
            break
        overlap.insert(0, paragraph)
        total += token_count
    return overlap
