import argparse
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from obsidian_sync.domain.frontmatter import parse_frontmatter

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATHS = (
    _REPO_ROOT / 'scripts' / 'save_knowledge.py',
    _REPO_ROOT / 'SKILLS' / 'knowledge-management' / 'scripts' / 'save_knowledge.py',
)


def _load_script(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('script_path', _SCRIPT_PATHS)
def test_build_markdown_is_indexable(script_path: Path) -> None:
    script = _load_script(script_path)

    frontmatter = parse_frontmatter(
        script._build_markdown('Skill note', [], None, 'Searchable body.')
    )

    assert frontmatter.project == 'agent-knowledge'
    assert frontmatter.document_type.value == 'study-note'
    assert frontmatter.tags == ()
    assert frontmatter.vectorize is True


@pytest.mark.parametrize('script_path', _SCRIPT_PATHS)
def test_save_reports_reindex_failures_without_failing_upload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    script_path: Path,
) -> None:
    script = _load_script(script_path)
    responses = iter(
        ({'status': 'uploaded'}, {'failed_files': 1, 'failures': ['a.md']})
    )
    monkeypatch.setattr(script, '_post', lambda *_args, **_kwargs: next(responses))
    args = argparse.Namespace(
        content='Searchable body.',
        content_file=None,
        path='a.md',
        tags=None,
        project=None,
        title='Skill note',
        token='test-token',
        vault_id='test-vault',
        no_reindex=False,
        overwrite=False,
    )

    assert script._save('http://example.test', args) == 0
    assert 'WARNING: reindex failed for 1 file(s)' in capsys.readouterr().err


@pytest.mark.parametrize('script_path', _SCRIPT_PATHS)
def test_save_fails_when_reindex_request_errors(
    monkeypatch: pytest.MonkeyPatch,
    script_path: Path,
) -> None:
    script = _load_script(script_path)
    uploaded: dict[str, object] = {'status': 'uploaded'}
    responses: Iterator[dict[str, object] | RuntimeError] = iter(
        (uploaded, RuntimeError('embedding unavailable'))
    )

    def post(*_args: object, **_kwargs: object) -> dict[str, object]:
        response = next(responses)
        if isinstance(response, RuntimeError):
            raise response
        return response

    monkeypatch.setattr(script, '_post', post)
    args = argparse.Namespace(
        content='Searchable body.',
        content_file=None,
        path='a.md',
        tags=None,
        project=None,
        title='Skill note',
        token='test-token',
        vault_id='test-vault',
        no_reindex=False,
        overwrite=False,
    )

    with pytest.raises(RuntimeError, match='embedding unavailable'):
        script._save('http://example.test', args)
