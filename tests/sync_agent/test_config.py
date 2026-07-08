import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from obsidian_sync.sync_agent.config import (
    DEVICE_ID_ENV,
    SERVER_ENV,
    TOKEN_ENV,
    VAULT_ID_ENV,
    CliOverrides,
    load_config,
)


def _write_config_file(root: Path, data: dict[str, object]) -> None:
    config_dir = root / '.obsidian-sync-agent'
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / 'config.json').write_text(json.dumps(data), encoding='utf-8')


class LoadConfigPrecedenceTests(TestCase):
    def test_file_values_used_when_no_env_or_cli(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'device_id': 'file-device',
                },
            )
            with patch.dict('os.environ', {}, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.server_base_url, 'https://file.example')
        self.assertEqual(config.vault_id, 'file-vault')
        self.assertEqual(config.device_id, 'file-device')

    def test_env_overrides_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'device_id': 'file-device',
                },
            )
            env = {
                SERVER_ENV: 'https://env.example',
                VAULT_ID_ENV: 'env-vault',
                DEVICE_ID_ENV: 'env-device',
            }
            with patch.dict('os.environ', env, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.server_base_url, 'https://env.example')
        self.assertEqual(config.vault_id, 'env-vault')
        self.assertEqual(config.device_id, 'env-device')

    def test_cli_overrides_env_and_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                },
            )
            env = {
                SERVER_ENV: 'https://env.example',
                VAULT_ID_ENV: 'env-vault',
                DEVICE_ID_ENV: 'env-device',
            }
            overrides = CliOverrides(
                vault_root=root,
                server_base_url='https://cli.example',
                vault_id='cli-vault',
                device_id='cli-device',
            )
            with patch.dict('os.environ', env, clear=True):
                config = load_config(overrides)

        self.assertEqual(config.server_base_url, 'https://cli.example')
        self.assertEqual(config.vault_id, 'cli-vault')
        self.assertEqual(config.device_id, 'cli-device')

    def test_token_is_read_from_env_only(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'api_token': 'file-token-should-be-ignored',
                },
            )
            env = {TOKEN_ENV: 'env-token'}
            with patch.dict('os.environ', env, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.api_token, 'env-token')

    def test_token_absent_when_env_not_set(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'api_token': 'file-token-should-be-ignored',
                },
            )
            with patch.dict('os.environ', {}, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertIsNone(config.api_token)
