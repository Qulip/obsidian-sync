from obsidian_sync.domain.chunking import MarkdownChunk
from obsidian_sync.domain.enums import DocumentPriority
from obsidian_sync.domain.frontmatter import DocumentFrontmatter


def build_agent_hint(frontmatter: DocumentFrontmatter, chunk: MarkdownChunk) -> str:
    """Build a compact hint string for AI consumers from frontmatter and chunk
    metadata."""
    parts: list[str] = [f'[{frontmatter.document_type.value}]']

    if frontmatter.priority is not DocumentPriority.MEDIUM:
        parts.append(f'priority:{frontmatter.priority.value}')

    if frontmatter.project:
        parts.append(f'project:{frontmatter.project}')

    if frontmatter.domain:
        parts.append(f'domain:{frontmatter.domain}')

    if chunk.heading:
        parts.append(f'section:{chunk.heading}')

    if frontmatter.tags:
        parts.append(f'tags:{",".join(frontmatter.tags[:5])}')

    return ' '.join(parts)
