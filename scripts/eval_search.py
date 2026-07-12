#!/usr/bin/env python3
"""Evaluate vector search quality against a golden query set (local use only).

For each entry in a golden-queries YAML file (see
docs/eval/golden-queries.yaml for the expected format), this script runs
KnowledgeSearchService.search directly against the configured PostgreSQL
database and Ollama embedding server -- the same Settings and clients the
API itself uses -- and reports Recall@K and Mean Reciprocal Rank (MRR@K)
across the set.

This is a standalone local evaluation harness, not part of CI: it requires
a real, indexed vault and a running Ollama server, neither of which are
available in automated test environments.

Usage:
  uv run python scripts/eval_search.py
  uv run python scripts/eval_search.py --golden docs/eval/golden-queries.yaml
  uv run python scripts/eval_search.py --top-k 10

Requires OBSIDIAN_SYNC_DATABASE_URL (or OBSIDIAN_POSTGRESQL_URL /
DATABASE_URL) pointing at a reachable PostgreSQL instance, and a reachable
Ollama server at the configured ollama_base_url (default
http://localhost:11434).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from sqlalchemy.exc import SQLAlchemyError

from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.config import Settings
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.db.session import build_async_engine, build_sessionmaker
from obsidian_sync.repositories.search import SearchRepository
from obsidian_sync.services.search import KnowledgeSearchService

_TOP_K_MIN = 1
_TOP_K_MAX = 10
_DEFAULT_TOP_K = 5


class EvalConnectionError(RuntimeError):
    """Raised when PostgreSQL or Ollama cannot be reached during evaluation."""


@dataclass(frozen=True, slots=True)
class GoldenQuery:
    query: str
    vault_id: str
    expected_sources: tuple[str, ...]
    expected_headings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryEvalResult:
    golden: GoldenQuery
    ranked_sources: tuple[str, ...]
    recall: float
    reciprocal_rank: float
    note: str | None


def main() -> int:
    args = _parse_args()

    try:
        golden_queries = _load_golden_queries(args.golden)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f'ERROR: failed to load golden query file {args.golden}: {exc}')
        return 1

    if not golden_queries:
        print(f'ERROR: no golden queries found in {args.golden}', file=sys.stderr)
        return 1

    settings = Settings()
    try:
        results = asyncio.run(_run_eval(settings, golden_queries, top_k=args.top_k))
    except EvalConnectionError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1

    _report(results, golden_path=args.golden, top_k=args.top_k)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Run a golden query set against vector search and report '
            'Recall@K / MRR@K. Local evaluation harness -- not run in CI.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--golden',
        type=Path,
        default=Path('docs/eval/golden-queries.yaml'),
        help='Path to the golden queries YAML file (default: %(default)s)',
    )
    parser.add_argument(
        '--top-k',
        type=_top_k_type,
        default=_DEFAULT_TOP_K,
        help=(
            f'Number of search results to request per query, '
            f'{_TOP_K_MIN}-{_TOP_K_MAX} (default: {_DEFAULT_TOP_K})'
        ),
    )
    return parser.parse_args()


def _top_k_type(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f'{raw!r} is not an integer') from exc
    if value < _TOP_K_MIN or value > _TOP_K_MAX:
        raise argparse.ArgumentTypeError(
            f'--top-k must be between {_TOP_K_MIN} and {_TOP_K_MAX}'
        )
    return value


def _load_golden_queries(path: Path) -> list[GoldenQuery]:
    raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('golden query file must contain a YAML list')

    golden_queries: list[GoldenQuery] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f'entry {index} must be a mapping')
        golden_queries.append(_parse_golden_entry(entry, index))
    return golden_queries


def _parse_golden_entry(entry: dict[str, Any], index: int) -> GoldenQuery:
    query = entry.get('query')
    vault_id = entry.get('vault_id')
    expected_sources = entry.get('expected_sources')
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f'entry {index}: "query" must be a non-empty string')
    if not isinstance(vault_id, str) or not vault_id.strip():
        raise ValueError(f'entry {index}: "vault_id" must be a non-empty string')
    if not isinstance(expected_sources, list) or not expected_sources:
        raise ValueError(f'entry {index}: "expected_sources" must be a non-empty list')
    expected_headings = entry.get('expected_headings') or []
    if not isinstance(expected_headings, list):
        raise ValueError(f'entry {index}: "expected_headings" must be a list')

    return GoldenQuery(
        query=query,
        vault_id=vault_id,
        expected_sources=tuple(cast(list[str], expected_sources)),
        expected_headings=tuple(cast(list[str], expected_headings)),
    )


async def _run_eval(
    settings: Settings, golden_queries: list[GoldenQuery], *, top_k: int
) -> list[QueryEvalResult]:
    if settings.database_url is None:
        raise EvalConnectionError(
            'OBSIDIAN_SYNC_DATABASE_URL (or OBSIDIAN_POSTGRESQL_URL / '
            'DATABASE_URL) is not configured.'
        )

    engine = build_async_engine(settings.database_url)
    sessionmaker = build_sessionmaker(engine)
    ollama_client = OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.embedding_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )

    try:
        # A single session for the whole run, never committed: search reads
        # don't need a commit, and this keeps eval traffic out of the
        # persisted search_logs table (rolled back when the session closes).
        async with sessionmaker() as session:
            service = KnowledgeSearchService(
                repository=SearchRepository(session),
                ollama_client=ollama_client,
                settings=settings,
            )
            return [
                await _evaluate_query(service, golden, top_k=top_k)
                for golden in golden_queries
            ]
    except (OSError, SQLAlchemyError) as exc:
        raise EvalConnectionError(
            f'Could not query PostgreSQL at the configured database_url: {exc}'
        ) from exc
    finally:
        await engine.dispose()


async def _evaluate_query(
    service: KnowledgeSearchService, golden: GoldenQuery, *, top_k: int
) -> QueryEvalResult:
    try:
        response = await service.search(
            vault_id=golden.vault_id,
            query=golden.query,
            filters=None,
            top_k=top_k,
            project=None,
            domain=None,
            min_score=None,
            token_id=None,
            client_ip=None,
            user_agent='eval-search-harness/0.1',
        )
    except AppError as exc:
        if exc.code is ErrorCode.EMBEDDING_FAILED:
            raise EvalConnectionError(
                'Could not reach Ollama at the configured ollama_base_url: '
                f'{exc.message}'
            ) from exc
        # Any other AppError (e.g. vault not found, invalid query) is a
        # golden-set authoring issue, not a connectivity failure -- score it
        # as a complete miss for this entry and keep evaluating the rest.
        return QueryEvalResult(
            golden=golden,
            ranked_sources=(),
            recall=0.0,
            reciprocal_rank=0.0,
            note=f'{exc.code.value}: {exc.message}',
        )

    ranked_sources = tuple(result.source_path for result in response.results)
    return QueryEvalResult(
        golden=golden,
        ranked_sources=ranked_sources,
        recall=_recall(ranked_sources, golden.expected_sources),
        reciprocal_rank=_reciprocal_rank(ranked_sources, golden.expected_sources),
        note=None,
    )


def _recall(
    ranked_sources: tuple[str, ...], expected_sources: tuple[str, ...]
) -> float:
    if not expected_sources:
        return 0.0
    matched = sum(1 for source in expected_sources if source in ranked_sources)
    return matched / len(expected_sources)


def _reciprocal_rank(
    ranked_sources: tuple[str, ...], expected_sources: tuple[str, ...]
) -> float:
    expected_set = set(expected_sources)
    for position, source in enumerate(ranked_sources, start=1):
        if source in expected_set:
            return 1.0 / position
    return 0.0


def _report(results: list[QueryEvalResult], *, golden_path: Path, top_k: int) -> None:
    query_count = len(results)
    mean_recall = sum(result.recall for result in results) / query_count
    mean_reciprocal_rank = (
        sum(result.reciprocal_rank for result in results) / query_count
    )

    summary = {
        'golden_file': str(golden_path),
        'top_k': top_k,
        'query_count': query_count,
        'recall_at_k': round(mean_recall, 4),
        'mrr_at_k': round(mean_reciprocal_rank, 4),
        'queries': [
            {
                'query': result.golden.query,
                'vault_id': result.golden.vault_id,
                'expected_sources': list(result.golden.expected_sources),
                'ranked_sources': list(result.ranked_sources),
                'recall': round(result.recall, 4),
                'reciprocal_rank': round(result.reciprocal_rank, 4),
                'note': result.note,
            }
            for result in results
        ],
    }
    print(json.dumps(summary, indent=2, sort_keys=False, ensure_ascii=False))


if __name__ == '__main__':
    raise SystemExit(main())
