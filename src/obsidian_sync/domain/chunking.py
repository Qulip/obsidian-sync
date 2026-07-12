import re
from dataclasses import dataclass

from obsidian_sync.domain.frontmatter import strip_frontmatter

MIN_CHUNK_TOKENS = 150
MAX_CHUNK_TOKENS = 1200
DEFAULT_OVERLAP_TOKENS = 100

_HEADING_PATTERN = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$')
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
                heading_path=_merged_heading_path(
                    pending.heading_path, section.heading_path
                ),
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
                    heading_path=_merged_heading_path(
                        previous.heading_path, pending.heading_path
                    ),
                )
            )
        else:
            merged.append(pending)
    return merged


def _merged_heading_path(
    first: tuple[str, ...],
    second: tuple[str, ...],
) -> tuple[str, ...]:
    """Pick a heading path for two sections being merged into one chunk.

    When both sections have a heading path, keep their common prefix so the
    merged chunk isn't attributed to a heading it only partially belongs to.
    If there is no common prefix, fall back to the first section's path.
    Either way, the discarded heading text is not lost: it still appears in
    the merged content as the original `#### Heading` markdown line, so
    downstream consumers of `chunk.content` retain it even when it drops out
    of `chunk.heading_path`.
    """
    if not first:
        return second
    if not second:
        return first
    common: list[str] = []
    for left, right in zip(first, second, strict=False):
        if left != right:
            break
        common.append(left)
    return tuple(common) if common else first


def _split_large_section(
    content: str,
    *,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    if estimate_token_count(content) <= max_tokens:
        return [content]

    paragraphs = _bound_paragraphs(re.split(r'\n\s*\n', content), max_tokens)
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


def _bound_paragraphs(paragraphs: list[str], max_tokens: int) -> list[str]:
    """Ensure no single paragraph exceeds max_tokens on its own.

    A paragraph that is still too large is first split on line breaks; a
    single unbroken line that alone exceeds max_tokens is, as a last
    resort, sliced by character count. Markdown structure (e.g. an open
    code fence) may be broken by a mid-line/paragraph cut; that tradeoff is
    accepted here to guarantee a hard token cap on every chunk.
    """
    bounded: list[str] = []
    for paragraph in paragraphs:
        if estimate_token_count(paragraph) <= max_tokens:
            bounded.append(paragraph)
        else:
            bounded.extend(_split_paragraph_by_lines(paragraph, max_tokens))
    return bounded


def _split_paragraph_by_lines(paragraph: str, max_tokens: int) -> list[str]:
    lines = paragraph.split('\n')
    groups: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for line in lines:
        line_tokens = estimate_token_count(line)
        if line_tokens > max_tokens:
            if current:
                groups.append('\n'.join(current))
                current = []
                current_tokens = 0
            groups.extend(_slice_line_by_chars(line, max_tokens))
            continue
        if current and current_tokens + line_tokens > max_tokens:
            groups.append('\n'.join(current))
            current = []
            current_tokens = 0
        current.append(line)
        current_tokens += line_tokens
    if current:
        groups.append('\n'.join(current))
    return groups


def _slice_line_by_chars(line: str, max_tokens: int) -> list[str]:
    pieces: list[str] = []
    remaining = line
    while remaining:
        piece = _take_prefix_within_tokens(remaining, max_tokens)
        pieces.append(piece)
        remaining = remaining[len(piece) :]
    return pieces


def _take_prefix_within_tokens(text: str, max_tokens: int) -> str:
    low, high, best = 1, len(text), 1
    while low <= high:
        mid = (low + high) // 2
        if estimate_token_count(text[:mid]) <= max_tokens:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return text[:best]


def _take_suffix_within_tokens(text: str, max_tokens: int) -> str:
    low, high, best = 1, len(text), 1
    while low <= high:
        mid = (low + high) // 2
        if estimate_token_count(text[-mid:]) <= max_tokens:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return text[-best:]


def _overlap_paragraphs(chunk: str, overlap_tokens: int) -> list[str]:
    if overlap_tokens <= 0:
        return []
    paragraphs = [paragraph for paragraph in re.split(r'\n\s*\n', chunk) if paragraph]
    overlap: list[str] = []
    total = 0
    for paragraph in reversed(paragraphs):
        token_count = estimate_token_count(paragraph)
        if total + token_count > overlap_tokens:
            # A single paragraph can itself exceed the overlap budget (this
            # happens once paragraphs have been bounded to max_tokens by
            # `_bound_paragraphs`, which may still be far larger than
            # overlap_tokens). Take only a bounded suffix of it instead of
            # carrying the whole paragraph forward, otherwise the next
            # chunk could grow by a full paragraph's worth of tokens beyond
            # max_tokens.
            remaining_budget = overlap_tokens - total
            if remaining_budget > 0:
                overlap.insert(
                    0, _take_suffix_within_tokens(paragraph, remaining_budget)
                )
            break
        overlap.insert(0, paragraph)
        total += token_count
    return overlap
