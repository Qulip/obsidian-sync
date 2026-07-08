import logging
from dataclasses import dataclass
from typing import Any

import httpx

from obsidian_sync.sync_agent.config import ObsidianConfig

RELOAD_COMMAND_ID = 'app:reload'
HEALTH_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


@dataclass(frozen=True, slots=True)
class ObsidianResult:
    ok: bool
    message: str


def refresh_obsidian(
    config: ObsidianConfig,
    logger: logging.Logger,
) -> ObsidianResult:
    """Best-effort Obsidian Local REST API health check and optional reload.

    Returns ``ok=True`` when disabled or when the check succeeds. Any failure
    yields ``ok=False`` with a message; the caller decides whether that is a
    warning or a hard failure (``require_obsidian_refresh``).
    """
    if not config.enabled:
        return ObsidianResult(ok=True, message='obsidian integration disabled')

    headers = {'Accept': 'application/json'}
    if config.api_key:
        headers['Authorization'] = f'Bearer {config.api_key}'

    try:
        with httpx.Client(
            base_url=config.base_url.rstrip('/'),
            headers=headers,
            timeout=HEALTH_TIMEOUT,
            verify=config.verify_tls,
        ) as client:
            authenticated = _health_check(client, logger)
            if config.reload_command:
                if not authenticated:
                    return ObsidianResult(
                        ok=False,
                        message='obsidian reload requires an authenticated API key',
                    )
                return _reload(client, logger)
            return ObsidianResult(
                ok=True,
                message='obsidian is reachable; relying on its file watcher',
            )
    except httpx.HTTPError as exc:
        return ObsidianResult(ok=False, message=f'obsidian is not reachable: {exc}')


def _health_check(client: httpx.Client, logger: logging.Logger) -> bool:
    response = client.get('/')
    response.raise_for_status()
    payload: Any = response.json()
    authenticated = bool(
        isinstance(payload, dict) and payload.get('authenticated') is True
    )
    logger.debug('obsidian health check authenticated=%s', authenticated)
    return authenticated


def _reload(client: httpx.Client, logger: logging.Logger) -> ObsidianResult:
    commands = client.get('/commands/')
    commands.raise_for_status()
    payload: Any = commands.json()
    if not _has_reload_command(payload):
        return ObsidianResult(
            ok=False,
            message=f'obsidian command {RELOAD_COMMAND_ID} is unavailable',
        )
    logger.warning(
        'issuing obsidian %s; unsaved editor state will be discarded',
        RELOAD_COMMAND_ID,
    )
    reloaded = client.post(f'/commands/{RELOAD_COMMAND_ID}/')
    reloaded.raise_for_status()
    return ObsidianResult(ok=True, message='obsidian reloaded')


def _has_reload_command(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    commands = payload.get('commands')
    if not isinstance(commands, list):
        return False
    return any(
        isinstance(command, dict) and command.get('id') == RELOAD_COMMAND_ID
        for command in commands
    )
