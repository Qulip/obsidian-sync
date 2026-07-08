import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_DIRNAME = '.obsidian-sync-agent'
CONFIG_FILENAME = 'config.json'

TOKEN_ENV = 'OBSIDIAN_SYNC_AGENT_TOKEN'
SERVER_ENV = 'OBSIDIAN_SYNC_AGENT_SERVER'
VAULT_ID_ENV = 'OBSIDIAN_SYNC_AGENT_VAULT_ID'
DEVICE_ID_ENV = 'OBSIDIAN_SYNC_AGENT_DEVICE_ID'
DEVICE_NAME_ENV = 'OBSIDIAN_SYNC_AGENT_DEVICE_NAME'
OBSIDIAN_KEY_ENV = 'OBSIDIAN_LOCAL_REST_API_KEY'

DEFAULT_OBSIDIAN_BASE_URL = 'https://127.0.0.1:27124'


class ConfigError(Exception):
    """Raised when the agent configuration is incomplete or invalid."""


@dataclass(frozen=True, slots=True)
class ObsidianConfig:
    enabled: bool = False
    base_url: str = DEFAULT_OBSIDIAN_BASE_URL
    api_key: str | None = None
    verify_tls: bool = False
    reload_command: bool = False


@dataclass(frozen=True, slots=True)
class AgentConfig:
    server_base_url: str
    vault_id: str
    vault_root: Path
    device_id: str
    api_token: str | None = None
    device_name: str | None = None
    obsidian: ObsidianConfig = field(default_factory=ObsidianConfig)
    require_obsidian_refresh: bool = False


@dataclass(frozen=True, slots=True)
class CliOverrides:
    vault_root: Path | None = None
    vault_id: str | None = None
    server_base_url: str | None = None
    device_id: str | None = None
    require_obsidian_refresh: bool | None = None


def sanitize_device_id(raw: str) -> str:
    cleaned = ''.join(
        character if character.isalnum() or character in '-_.' else '-'
        for character in raw
    )
    return cleaned.strip('-_.') or 'device'


def default_device_id() -> str:
    return sanitize_device_id(socket.gethostname())


def config_path(vault_root: Path) -> Path:
    return vault_root / CONFIG_DIRNAME / CONFIG_FILENAME


def _load_config_file(vault_root: Path) -> dict[str, Any]:
    path = config_path(vault_root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f'could not read config file {path}: {exc}') from exc
    if not isinstance(raw, dict):
        raise ConfigError(f'config file {path} must contain a JSON object')
    return raw


def _pick_str(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if candidate is not None and candidate != '':
            return candidate
    return None


def _pick_bool(*candidates: bool | None) -> bool | None:
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


def _obsidian_from_file(data: dict[str, Any]) -> ObsidianConfig:
    section = data.get('obsidian')
    section = section if isinstance(section, dict) else {}
    return ObsidianConfig(
        enabled=bool(section.get('enabled', False)),
        base_url=str(section.get('base_url', DEFAULT_OBSIDIAN_BASE_URL)),
        api_key=_pick_str(
            os.environ.get(OBSIDIAN_KEY_ENV),
            _as_optional_str(section.get('api_key')),
        ),
        verify_tls=bool(section.get('verify_tls', False)),
        reload_command=bool(section.get('reload_command', False)),
    )


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def load_config(overrides: CliOverrides) -> AgentConfig:
    """Resolve configuration with precedence: CLI > env > file > defaults."""
    vault_root = _pick_root(overrides)
    file_data = _load_config_file(vault_root)

    server = _pick_str(
        overrides.server_base_url,
        os.environ.get(SERVER_ENV),
        _as_optional_str(file_data.get('server_base_url')),
    )
    if server is None:
        raise ConfigError(
            'server base url is required (use --server, '
            f'{SERVER_ENV}, or config file server_base_url)'
        )

    vault_id = _pick_str(
        overrides.vault_id,
        os.environ.get(VAULT_ID_ENV),
        _as_optional_str(file_data.get('vault_id')),
    )
    if vault_id is None:
        raise ConfigError(
            'vault id is required (use --vault-id, '
            f'{VAULT_ID_ENV}, or config file vault_id)'
        )

    device_id = _pick_str(
        overrides.device_id,
        os.environ.get(DEVICE_ID_ENV),
        _as_optional_str(file_data.get('device_id')),
    )
    if device_id is None:
        device_id = default_device_id()

    device_name = _pick_str(
        os.environ.get(DEVICE_NAME_ENV),
        _as_optional_str(file_data.get('device_name')),
    )

    require_refresh = _pick_bool(
        overrides.require_obsidian_refresh,
        _as_optional_bool(file_data.get('require_obsidian_refresh')),
    )

    return AgentConfig(
        server_base_url=server.rstrip('/'),
        vault_id=vault_id,
        vault_root=vault_root,
        device_id=sanitize_device_id(device_id),
        api_token=os.environ.get(TOKEN_ENV),
        device_name=device_name,
        obsidian=_obsidian_from_file(file_data),
        require_obsidian_refresh=bool(require_refresh),
    )


def _as_optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _pick_root(overrides: CliOverrides) -> Path:
    if overrides.vault_root is not None:
        return overrides.vault_root.expanduser().resolve()
    env_root = os.environ.get('OBSIDIAN_SYNC_AGENT_VAULT_ROOT')
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path.cwd().resolve()
