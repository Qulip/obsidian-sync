from unittest import TestCase

from obsidian_sync.domain import chunking
from obsidian_sync.domain.chunking import (
    MAX_CHUNK_TOKENS,
    MIN_CHUNK_TOKENS,
    chunk_markdown,
)

_FRONTMATTER = '---\n---\n'


def _repeat_words(prefix: str, count: int) -> str:
    return ' '.join(f'{prefix}{i}' for i in range(count))


def _doc(body: str) -> str:
    """Prefix a minimal (empty) frontmatter block, required by
    chunk_markdown -> strip_frontmatter regardless of field validity."""
    return _FRONTMATTER + body


class HeadingSectionSplitTests(TestCase):
    """Locks down _split_heading_sections' heading_stack behavior for H1-H6."""

    def test_h4_h5_h6_headings_are_captured_in_heading_path(self) -> None:
        markdown = (
            '# H1\n\nintro\n\n#### H4\n\nfour\n\n##### H5\n\nfive\n\n###### H6\n\nsix\n'
        )
        sections = chunking._split_heading_sections(markdown)
        paths = [section.heading_path for section in sections]

        self.assertEqual(
            paths,
            [
                ('H1',),
                ('H1', 'H4'),
                ('H1', 'H4', 'H5'),
                ('H1', 'H4', 'H5', 'H6'),
            ],
        )

    def test_h4_after_h2_skips_missing_h3_level(self) -> None:
        markdown = '## H2\n\nbody\n\n#### H4\n\nbody\n'
        sections = chunking._split_heading_sections(markdown)
        paths = [section.heading_path for section in sections]

        self.assertEqual(paths, [('H2',), ('H2', 'H4')])

    def test_returns_to_shallower_level_after_deep_nesting(self) -> None:
        markdown = '# H1\n\nintro\n\n###### H6\n\ndeep\n\n## H2\n\nshallow again\n'
        sections = chunking._split_heading_sections(markdown)
        paths = [section.heading_path for section in sections]

        self.assertEqual(paths, [('H1',), ('H1', 'H6'), ('H1', 'H2')])


class ChunkMarkdownHeadingIntegrationTests(TestCase):
    def test_deep_headings_survive_into_chunk_heading_path(self) -> None:
        body_words = MIN_CHUNK_TOKENS + 50
        markdown = _doc(
            '# Guide\n\n'
            + _repeat_words('intro', body_words)
            + '\n\n'
            + '#### Deep Section\n\n'
            + _repeat_words('deep', body_words)
            + '\n'
        )
        chunks = chunk_markdown(markdown)
        heading_paths = {chunk.heading_path for chunk in chunks}

        self.assertIn(('Guide', 'Deep Section'), heading_paths)


class LargeParagraphSplitTests(TestCase):
    def test_oversized_single_line_paragraph_is_bounded_by_char_slicing(self) -> None:
        huge_line = _repeat_words('word', 4000)
        markdown = _doc('# Title\n\n' + huge_line + '\n')
        chunks = chunk_markdown(markdown)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(chunk.token_count, MAX_CHUNK_TOKENS * 1.1)
            self.assertEqual(chunk.heading_path, ('Title',))

    def test_oversized_multiline_paragraph_is_bounded_by_line_grouping(self) -> None:
        lines = [_repeat_words(f'l{i}_', 60) for i in range(40)]
        paragraph = '\n'.join(lines)
        markdown = _doc('# Title\n\n' + paragraph + '\n')
        chunks = chunk_markdown(markdown)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(chunk.token_count, MAX_CHUNK_TOKENS * 1.1)

    def test_chunk_index_is_contiguous_after_splitting(self) -> None:
        huge_line = _repeat_words('token', 4000)
        markdown = _doc('# Title\n\n' + huge_line + '\n')
        chunks = chunk_markdown(markdown)

        self.assertEqual([c.chunk_index for c in chunks], list(range(len(chunks))))


class MergedHeadingPathHelperTests(TestCase):
    """Direct coverage of the merge heading-path decision, isolated from the
    surrounding section-merge loop."""

    def test_common_prefix_is_kept_when_paths_diverge(self) -> None:
        result = chunking._merged_heading_path(('A', 'B'), ('A', 'C'))
        self.assertEqual(result, ('A',))

    def test_first_path_is_kept_when_no_common_prefix_exists(self) -> None:
        result = chunking._merged_heading_path(('A',), ('B',))
        self.assertEqual(result, ('A',))

    def test_empty_first_path_falls_back_to_second(self) -> None:
        result = chunking._merged_heading_path((), ('B',))
        self.assertEqual(result, ('B',))

    def test_empty_second_path_falls_back_to_first(self) -> None:
        result = chunking._merged_heading_path(('A',), ())
        self.assertEqual(result, ('A',))


class MergeShortSectionsIntegrationTests(TestCase):
    def test_merging_divergent_subsections_keeps_common_ancestor_only(self) -> None:
        # A stays standalone (long enough on its own). B and C are each
        # individually short, so they merge with each other. Since B and C
        # diverge right after 'A', the merged chunk should keep only the
        # common ancestor ('A',) rather than misleadingly inheriting B's
        # full path (the previous behavior).
        long_words = MIN_CHUNK_TOKENS + 60
        short_words = 80
        markdown = _doc(
            '# A\n\n'
            + _repeat_words('intro', long_words)
            + '\n\n'
            + '## B\n\n'
            + _repeat_words('b', short_words)
            + '\n\n'
            + '## C\n\n'
            + _repeat_words('c', short_words)
            + '\n'
        )
        chunks = chunk_markdown(markdown)

        non_root_paths = [c.heading_path for c in chunks if c.heading_path != ('A',)]
        self.assertEqual(non_root_paths, [])
        self.assertTrue(
            any('b0' in c.content and 'c0' in c.content for c in chunks),
            'expected B and C bodies to end up merged into the same chunk',
        )

    def test_leading_no_heading_content_merges_using_next_heading_path(self) -> None:
        short_words = MIN_CHUNK_TOKENS // 3
        markdown = _doc(
            _repeat_words('lead', short_words)
            + '\n\n'
            + '# Title\n\n'
            + _repeat_words('body', short_words)
            + '\n'
        )
        chunks = chunk_markdown(markdown)

        self.assertTrue(all(c.heading_path == ('Title',) for c in chunks))


class RemovedConstantsTests(TestCase):
    def test_unused_target_token_constants_were_removed(self) -> None:
        self.assertFalse(hasattr(chunking, 'TARGET_MIN_CHUNK_TOKENS'))
        self.assertFalse(hasattr(chunking, 'TARGET_MAX_CHUNK_TOKENS'))


class RegressionTests(TestCase):
    def test_code_fence_hashes_are_not_treated_as_headings(self) -> None:
        # Exercise _split_heading_sections directly: with short bodies,
        # chunk_markdown's own merge step would collapse 'Title' and 'Real
        # Heading' down to their common prefix ('Title',) regardless of
        # fence handling, which would make this test pass or fail for the
        # wrong reason. Section splitting is the actual property under test.
        markdown = (
            '# Title\n\n'
            '```\n# not a heading\n## also not\n```\n\n'
            '## Real Heading\n\nreal content\n'
        )
        sections = chunking._split_heading_sections(markdown)
        paths = [section.heading_path for section in sections]

        self.assertEqual(paths, [('Title',), ('Title', 'Real Heading')])
        self.assertIn('# not a heading', sections[0].content)

    def test_code_fence_content_is_preserved_end_to_end(self) -> None:
        markdown = _doc(
            '# Title\n\n'
            '```\n# not a heading\n## also not\n```\n\n'
            '## Real Heading\n\nreal content\n'
        )
        chunks = chunk_markdown(markdown)
        combined_content = '\n'.join(c.content for c in chunks)

        self.assertIn('# not a heading', combined_content)
        self.assertIn('Real Heading', combined_content)

    def test_overlap_increases_total_duplicated_tokens_vs_no_overlap(self) -> None:
        huge_line = _repeat_words('tok', 4000)
        markdown = _doc('# Title\n\n' + huge_line + '\n')

        with_overlap = chunk_markdown(markdown, overlap_tokens=50)
        without_overlap = chunk_markdown(markdown, overlap_tokens=0)

        self.assertGreater(len(with_overlap), 1)
        total_with = sum(c.token_count for c in with_overlap)
        total_without = sum(c.token_count for c in without_overlap)
        self.assertGreater(total_with, total_without)

    def test_short_markdown_produces_a_single_chunk(self) -> None:
        markdown = _doc('# Title\n\nJust a short note.\n')
        chunks = chunk_markdown(markdown)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].chunk_index, 0)
        self.assertEqual(chunks[0].heading_path, ('Title',))
        self.assertEqual(chunks[0].heading, 'Title')
