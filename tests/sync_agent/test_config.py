import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from obsidian_sync.sync_agent.config import (
    ATTACHMENT_MAX_BYTES_ENV,
    CONFLICT_POLICY_ENV,
    DEFAULT_ATTACHMENT_MAX_BYTES,
    DEVICE_ID_ENV,
    MAX_RETRIES_ENV,
    RETRY_BASE_DELAY_ENV,
    RETRY_MAX_DELAY_ENV,
    SERVER_ENV,
    SYNC_ATTACHMENTS_ENV,
    TOKEN_ENV,
    VAULT_ID_ENV,
    WATCH_DEBOUNCE_SECONDS_ENV,
    WATCH_INTERVAL_SECONDS_ENV,
    CliOverrides,
    ConfigError,
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


class RetryConfigPrecedenceTests(TestCase):
    def test_defaults_when_unset(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            with patch.dict('os.environ', {}, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.max_retries, 3)
        self.assertEqual(config.retry_base_delay, 1.0)
        self.assertEqual(config.retry_max_delay, 30.0)

    def test_file_values_used_when_no_env_or_cli(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'max_retries': 5,
                    'retry_base_delay': 2.0,
                    'retry_max_delay': 60.0,
                },
            )
            with patch.dict('os.environ', {}, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.max_retries, 5)
        self.assertEqual(config.retry_base_delay, 2.0)
        self.assertEqual(config.retry_max_delay, 60.0)

    def test_env_overrides_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'max_retries': 5,
                },
            )
            env = {
                MAX_RETRIES_ENV: '7',
                RETRY_BASE_DELAY_ENV: '0.5',
                RETRY_MAX_DELAY_ENV: '15',
            }
            with patch.dict('os.environ', env, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.max_retries, 7)
        self.assertEqual(config.retry_base_delay, 0.5)
        self.assertEqual(config.retry_max_delay, 15.0)

    def test_cli_overrides_env_and_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            env = {MAX_RETRIES_ENV: '7'}
            overrides = CliOverrides(
                vault_root=root,
                max_retries=1,
                retry_base_delay=3.0,
                retry_max_delay=9.0,
            )
            with patch.dict('os.environ', env, clear=True):
                config = load_config(overrides)

        self.assertEqual(config.max_retries, 1)
        self.assertEqual(config.retry_base_delay, 3.0)
        self.assertEqual(config.retry_max_delay, 9.0)

    def test_negative_max_retries_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            with patch.dict('os.environ', {}, clear=True):
                with self.assertRaises(ConfigError):
                    load_config(CliOverrides(vault_root=root, max_retries=-1))

    def test_retry_max_delay_below_base_delay_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            with patch.dict('os.environ', {}, clear=True):
                with self.assertRaises(ConfigError):
                    load_config(
                        CliOverrides(
                            vault_root=root,
                            retry_base_delay=10.0,
                            retry_max_delay=5.0,
                        )
                    )


class ConflictPolicyConfigPrecedenceTests(TestCase):
    def test_defaults_to_manual_when_unset(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            with patch.dict('os.environ', {}, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.conflict_policy, 'manual')

    def test_file_value_used_when_no_env_or_cli(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'conflict_policy': 'remote-wins',
                },
            )
            with patch.dict('os.environ', {}, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.conflict_policy, 'remote-wins')

    def test_env_overrides_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'conflict_policy': 'remote-wins',
                },
            )
            env = {CONFLICT_POLICY_ENV: 'local-wins'}
            with patch.dict('os.environ', env, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.conflict_policy, 'local-wins')

    def test_cli_overrides_env_and_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'conflict_policy': 'remote-wins',
                },
            )
            env = {CONFLICT_POLICY_ENV: 'local-wins'}
            overrides = CliOverrides(vault_root=root, conflict_policy='manual')
            with patch.dict('os.environ', env, clear=True):
                config = load_config(overrides)

        self.assertEqual(config.conflict_policy, 'manual')

    def test_invalid_value_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            with patch.dict('os.environ', {}, clear=True):
                with self.assertRaises(ConfigError):
                    load_config(
                        CliOverrides(vault_root=root, conflict_policy='obsidian-wins')
                    )


class SyncAttachmentsConfigPrecedenceTests(TestCase):
    def test_defaults_to_false(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            with patch.dict('os.environ', {}, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertFalse(config.sync_attachments)
        self.assertEqual(config.attachment_max_bytes, DEFAULT_ATTACHMENT_MAX_BYTES)

    def test_file_value_used_when_no_env_or_cli(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'sync_attachments': True,
                    'attachment_max_bytes': 1024,
                },
            )
            with patch.dict('os.environ', {}, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertTrue(config.sync_attachments)
        self.assertEqual(config.attachment_max_bytes, 1024)

    def test_env_overrides_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'sync_attachments': False,
                },
            )
            env = {
                SYNC_ATTACHMENTS_ENV: 'true',
                ATTACHMENT_MAX_BYTES_ENV: '2048',
            }
            with patch.dict('os.environ', env, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertTrue(config.sync_attachments)
        self.assertEqual(config.attachment_max_bytes, 2048)

    def test_cli_overrides_env_and_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            env = {SYNC_ATTACHMENTS_ENV: 'false'}
            overrides = CliOverrides(
                vault_root=root, sync_attachments=True, attachment_max_bytes=4096
            )
            with patch.dict('os.environ', env, clear=True):
                config = load_config(overrides)

        self.assertTrue(config.sync_attachments)
        self.assertEqual(config.attachment_max_bytes, 4096)

    def test_invalid_env_bool_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            env = {SYNC_ATTACHMENTS_ENV: 'maybe'}
            with patch.dict('os.environ', env, clear=True):
                with self.assertRaises(ConfigError):
                    load_config(CliOverrides(vault_root=root))

    def test_non_positive_attachment_max_bytes_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            with patch.dict('os.environ', {}, clear=True):
                with self.assertRaises(ConfigError):
                    load_config(CliOverrides(vault_root=root, attachment_max_bytes=0))


class WatchConfigPrecedenceTests(TestCase):
    def test_defaults_when_unset(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            with patch.dict('os.environ', {}, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.watch_debounce_seconds, 2.0)
        self.assertEqual(config.watch_interval_seconds, 0.0)

    def test_file_values_used_when_no_env_or_cli(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'watch_debounce_seconds': 5.0,
                    'watch_interval_seconds': 300.0,
                },
            )
            with patch.dict('os.environ', {}, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.watch_debounce_seconds, 5.0)
        self.assertEqual(config.watch_interval_seconds, 300.0)

    def test_env_overrides_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'watch_debounce_seconds': 5.0,
                },
            )
            env = {
                WATCH_DEBOUNCE_SECONDS_ENV: '1.5',
                WATCH_INTERVAL_SECONDS_ENV: '60',
            }
            with patch.dict('os.environ', env, clear=True):
                config = load_config(CliOverrides(vault_root=root))

        self.assertEqual(config.watch_debounce_seconds, 1.5)
        self.assertEqual(config.watch_interval_seconds, 60.0)

    def test_cli_overrides_env_and_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {
                    'server_base_url': 'https://file.example',
                    'vault_id': 'file-vault',
                    'watch_debounce_seconds': 5.0,
                },
            )
            env = {WATCH_DEBOUNCE_SECONDS_ENV: '1.5'}
            overrides = CliOverrides(
                vault_root=root,
                watch_debounce_seconds=0.25,
                watch_interval_seconds=10.0,
            )
            with patch.dict('os.environ', env, clear=True):
                config = load_config(overrides)

        self.assertEqual(config.watch_debounce_seconds, 0.25)
        self.assertEqual(config.watch_interval_seconds, 10.0)

    def test_non_positive_debounce_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            with patch.dict('os.environ', {}, clear=True):
                with self.assertRaises(ConfigError):
                    load_config(
                        CliOverrides(vault_root=root, watch_debounce_seconds=0.0)
                    )

    def test_negative_interval_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_config_file(
                root,
                {'server_base_url': 'https://file.example', 'vault_id': 'file-vault'},
            )
            with patch.dict('os.environ', {}, clear=True):
                with self.assertRaises(ConfigError):
                    load_config(
                        CliOverrides(vault_root=root, watch_interval_seconds=-1.0)
                    )
