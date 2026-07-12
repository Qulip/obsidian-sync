import contextlib
import importlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

cli = importlib.import_module('obsidian_sync.sync_agent.cli')
config_module = importlib.import_module('obsidian_sync.sync_agent.config')
conflict_module = importlib.import_module('obsidian_sync.sync_agent.conflict')
manifest_module = importlib.import_module('obsidian_sync.sync_agent.manifest')


def _config(root: Path, *, require_obsidian_refresh: bool = False):
    return config_module.AgentConfig(
        server_base_url='https://server.example',
        vault_id='vault',
        vault_root=root,
        device_id='device',
        require_obsidian_refresh=require_obsidian_refresh,
    )


class CliHelpContractTests(TestCase):
    def _help_for(self, *argv: str) -> str:
        parser = cli.build_parser()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as exit:
            parser.parse_args([*argv, '--help'])
        self.assertEqual(exit.exception.code, 0)
        return stdout.getvalue()

    def test_root_help_lists_sync_and_status_commands(self) -> None:
        help_text = self._help_for()

        self.assertIn('obsidian-sync-agent', help_text)
        self.assertIn('sync', help_text)
        self.assertIn('status', help_text)

    def test_sync_help_lists_contract_flags(self) -> None:
        help_text = self._help_for('sync')

        for flag in (
            '--vault-root',
            '--vault-id',
            '--server',
            '--device-id',
            '--verbose',
            '--dry-run',
            '--require-obsidian-refresh',
        ):
            self.assertIn(flag, help_text)

    def test_status_help_lists_common_flags_without_sync_only_flags(self) -> None:
        help_text = self._help_for('status')

        for flag in (
            '--vault-root',
            '--vault-id',
            '--server',
            '--device-id',
            '--verbose',
        ):
            self.assertIn(flag, help_text)
        self.assertNotIn('--dry-run', help_text)
        self.assertNotIn('--require-obsidian-refresh', help_text)


class CliExitCodeContractTests(TestCase):
    def test_sync_dry_run_exits_zero_and_passes_dry_run_to_engine(self) -> None:
        with TemporaryDirectory() as directory:
            config = _config(Path(directory))
            summary = cli.SyncSummary(dry_run=True)
            with (
                patch('obsidian_sync.sync_agent.cli.load_config', return_value=config),
                patch(
                    'obsidian_sync.sync_agent.cli.run_sync', return_value=summary
                ) as run_sync,
            ):
                exit_code = cli.main(['sync', '--dry-run'])

        self.assertEqual(exit_code, cli.EXIT_OK)
        run_sync.assert_called_once()
        self.assertTrue(run_sync.call_args.kwargs['dry_run'])

    def test_sync_conflicts_exit_one(self) -> None:
        with TemporaryDirectory() as directory:
            config = _config(Path(directory))
            summary = cli.SyncSummary(conflicts=['notes/JPA.md'])
            with (
                patch('obsidian_sync.sync_agent.cli.load_config', return_value=config),
                patch('obsidian_sync.sync_agent.cli.run_sync', return_value=summary),
            ):
                exit_code = cli.main(['sync'])

        self.assertEqual(exit_code, cli.EXIT_CONFLICTS)

    def test_required_obsidian_refresh_failure_exits_three(self) -> None:
        with TemporaryDirectory() as directory:
            config = _config(Path(directory), require_obsidian_refresh=True)
            summary = cli.SyncSummary(
                obsidian_ok=False,
                obsidian_message='reload failed',
            )
            with (
                patch('obsidian_sync.sync_agent.cli.load_config', return_value=config),
                patch('obsidian_sync.sync_agent.cli.run_sync', return_value=summary),
            ):
                exit_code = cli.main(['sync', '--require-obsidian-refresh'])

        self.assertEqual(exit_code, cli.EXIT_OBSIDIAN)

    def test_missing_required_config_exits_two(self) -> None:
        with TemporaryDirectory() as directory:
            with patch.dict('os.environ', {}, clear=True):
                exit_code = cli.main(['sync', '--vault-root', directory])

        self.assertEqual(exit_code, cli.EXIT_ERROR)

    def test_status_dispatches_to_status_engine_and_exits_zero(self) -> None:
        with TemporaryDirectory() as directory:
            config = _config(Path(directory))
            with (
                patch('obsidian_sync.sync_agent.cli.load_config', return_value=config),
                patch('obsidian_sync.sync_agent.cli.run_status') as run_status,
            ):
                exit_code = cli.main(['status'])

        self.assertEqual(exit_code, cli.EXIT_OK)
        run_status.assert_called_once()


class ManifestJsonContractTests(TestCase):
    def test_saved_manifest_uses_porting_contract_shape(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_module.save_manifest(
                root,
                manifest_module.Manifest(
                    vault_id='vault',
                    device_id='device',
                    last_sync_cursor=7,
                    files={
                        'notes/JPA.md': manifest_module.ManifestEntry(
                            server_revision=3,
                            content_hash='a' * 64,
                            last_synced_at='2026-07-07T00:00:00+00:00',
                        )
                    },
                    conflicts={
                        'notes/JPA.md': manifest_module.ManifestConflict(
                            server_revision=4,
                            server_content_hash=None,
                            local_content_hash='b' * 64,
                            server_deleted=True,
                        )
                    },
                ),
            )
            raw = json.loads(
                manifest_module.manifest_path(root).read_text(encoding='utf-8')
            )

        self.assertEqual(
            set(raw),
            {'conflicts', 'device_id', 'files', 'last_sync_cursor', 'vault_id'},
        )
        self.assertEqual(
            set(raw['files']['notes/JPA.md']),
            {'content_hash', 'last_synced_at', 'server_revision'},
        )
        self.assertEqual(
            set(raw['conflicts']['notes/JPA.md']),
            {
                'local_content_hash',
                'server_content_hash',
                'server_deleted',
                'server_revision',
            },
        )
        self.assertIsNone(raw['conflicts']['notes/JPA.md']['server_content_hash'])
        self.assertTrue(raw['conflicts']['notes/JPA.md']['server_deleted'])


class ConflictFileContractTests(TestCase):
    def test_conflict_file_contains_metadata_and_deleted_placeholder(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = conflict_module.write_conflict_file(
                root,
                'notes/JPA.md',
                device_id='device',
                client_base_revision=2,
                server_revision=3,
                local_content='local body',
                server_content=conflict_module.SERVER_DELETED_PLACEHOLDER,
            )
            body = destination.read_text(encoding='utf-8')

        self.assertIn('- Path: notes/JPA.md', body)
        self.assertIn('- Device: device', body)
        self.assertIn('- Client base revision: 2', body)
        self.assertIn('- Server revision: 3', body)
        self.assertIn('## Local Version', body)
        self.assertIn('local body', body)
        self.assertIn('## Server Version', body)
        self.assertIn(conflict_module.SERVER_DELETED_PLACEHOLDER, body)
