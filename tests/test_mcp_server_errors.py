# pyright: reportMissingImports=false

import pytest

from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.core.responses import ok
from obsidian_sync.mcp_server import _run_mcp_tool


@pytest.mark.asyncio
async def test_run_mcp_tool_preserves_success_envelope_shape() -> None:
    async def tool_call() -> object:
        return ok({'value': 1})

    assert await _run_mcp_tool(tool_call) == {
        'success': True,
        'data': {'value': 1},
        'error': None,
    }


@pytest.mark.asyncio
async def test_run_mcp_tool_returns_structured_app_error_envelope() -> None:
    async def tool_call() -> object:
        raise AppError(
            ErrorCode.SYNC_CONFLICT,
            'Server revision has changed.',
            status_code=409,
            details={'client_base_revision': 1, 'server_revision': 2},
        )

    assert await _run_mcp_tool(tool_call) == {
        'success': False,
        'data': None,
        'error': {
            'code': 'SYNC_CONFLICT',
            'message': 'Server revision has changed.',
            'details': {'client_base_revision': 1, 'server_revision': 2},
            'status_code': 409,
        },
    }


@pytest.mark.asyncio
async def test_run_mcp_tool_does_not_swallow_unexpected_exceptions() -> None:
    async def tool_call() -> object:
        raise RuntimeError('boom')

    with pytest.raises(RuntimeError, match='boom'):
        await _run_mcp_tool(tool_call)
