from fastapi.testclient import TestClient

from obsidian_sync.app import create_app
from obsidian_sync.core.config import Settings

PRODUCTION_HOST = 'www.google.com'
INITIALIZE_REQUEST = {
    'jsonrpc': '2.0',
    'id': 1,
    'method': 'initialize',
    'params': {
        'protocolVersion': '2025-03-26',
        'capabilities': {},
        'clientInfo': {'name': 'host-test', 'version': '1.0'},
    },
}


def _mcp_response(settings: Settings, host: str):
    app = create_app(settings)
    with TestClient(app) as client:
        return client.post(
            '/mcp',
            headers={
                'accept': 'application/json, text/event-stream',
                'content-type': 'application/json',
                'host': host,
            },
            json=INITIALIZE_REQUEST,
        )


def test_configured_public_host_is_accepted_by_mcp(monkeypatch) -> None:
    monkeypatch.setenv(
        'OBSIDIAN_SYNC_MCP_ALLOWED_HOSTS', f'["{PRODUCTION_HOST}"]'
    )
    settings = Settings(
        database_url=None,
        post_sync_indexing_enabled=False,
    )

    response = _mcp_response(settings, PRODUCTION_HOST)

    assert response.status_code == 200
    assert '"protocolVersion":"2025-03-26"' in response.text


def test_unconfigured_public_host_remains_rejected_by_mcp(monkeypatch) -> None:
    monkeypatch.setenv(
        'OBSIDIAN_SYNC_MCP_ALLOWED_HOSTS', f'["{PRODUCTION_HOST}"]'
    )
    settings = Settings(
        database_url=None,
        post_sync_indexing_enabled=False,
    )

    response = _mcp_response(settings, 'evil.example')

    assert response.status_code == 421


def test_localhost_default_host_remains_accepted_by_mcp() -> None:
    settings = Settings(
        database_url=None,
        post_sync_indexing_enabled=False,
    )

    response = _mcp_response(settings, 'localhost:8000')

    assert response.status_code == 200
