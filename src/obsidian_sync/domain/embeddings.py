from collections.abc import Sequence
from dataclasses import dataclass

from obsidian_sync.domain.chunking import MarkdownChunk
from obsidian_sync.domain.enums import DocumentType
from obsidian_sync.domain.frontmatter import DocumentFrontmatter

DEFAULT_EMBEDDING_MODEL = 'bge-m3'
EMBEDDING_DIMENSIONS = 1024


@dataclass(frozen=True, slots=True)
class EmbeddingInputMetadata:
    title: str
    document_type: DocumentType | str
    project: str
    domain: str
    heading_path: Sequence[str]
    tags: Sequence[str]


def metadata_from_frontmatter(
    frontmatter: DocumentFrontmatter,
    chunk: MarkdownChunk,
) -> EmbeddingInputMetadata:
    return EmbeddingInputMetadata(
        title=frontmatter.title,
        document_type=frontmatter.document_type,
        project=frontmatter.project,
        domain=frontmatter.domain,
        heading_path=chunk.heading_path,
        tags=frontmatter.tags,
    )


def format_embedding_input(
    metadata: EmbeddingInputMetadata,
    chunk_content: str,
) -> str:
    heading = ' > '.join(metadata.heading_path)
    tags = ', '.join(metadata.tags)
    return (
        f'Title: {metadata.title}\n'
        f'Type: {metadata.document_type}\n'
        f'Project: {metadata.project}\n'
        f'Domain: {metadata.domain}\n'
        f'Heading: {heading}\n'
        f'Tags: {tags}\n\n'
        f'Content:\n{chunk_content.strip()}'
    )


def format_chunk_embedding_input(
    frontmatter: DocumentFrontmatter,
    chunk: MarkdownChunk,
) -> str:
    return format_embedding_input(
        metadata_from_frontmatter(frontmatter, chunk),
        chunk.content,
    )
