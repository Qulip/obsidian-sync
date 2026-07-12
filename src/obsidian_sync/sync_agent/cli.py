import argparse
import logging
import sys
from pathlib import Path

from obsidian_sync.sync_agent.config import CliOverrides, ConfigError, load_config
from obsidian_sync.sync_agent.engine import (
    SyncError,
    SyncSummary,
    run_status,
    run_sync,
)
from obsidian_sync.sync_agent.watch import run_watch

EXIT_OK = 0
EXIT_CONFLICTS = 1
EXIT_ERROR = 2
EXIT_OBSIDIAN = 3

logger = logging.getLogger('obsidian_sync.agent')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='obsidian-sync-agent',
        description='Sync a local Obsidian vault with the obsidian-sync server.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    sync_parser = subparsers.add_parser('sync', help='run a full sync cycle')
    _add_common_arguments(sync_parser)
    sync_parser.add_argument(
        '--dry-run',
        action='store_true',
        help='print planned actions without writing or pushing',
    )
    sync_parser.add_argument(
        '--require-obsidian-refresh',
        action='store_true',
        help='exit non-zero if the Obsidian refresh step fails',
    )

    status_parser = subparsers.add_parser(
        'status', help='show server and local sync status'
    )
    _add_common_arguments(status_parser)

    watch_parser = subparsers.add_parser(
        'watch',
        help='watch the vault and run a sync cycle after debounced changes',
    )
    _add_common_arguments(watch_parser)
    watch_parser.add_argument(
        '--require-obsidian-refresh',
        action='store_true',
        help='log an error if the Obsidian refresh step fails after a sync',
    )
    watch_parser.add_argument(
        '--watch-debounce-seconds',
        type=float,
        help='quiet period (seconds) after the last change before syncing',
    )
    watch_parser.add_argument(
        '--watch-interval-seconds',
        type=float,
        help='periodic safety-net sync interval (seconds); 0 disables it',
    )
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--vault-root', type=Path, help='local vault directory')
    parser.add_argument('--vault-id', help='server vault id')
    parser.add_argument('--server', help='server base url')
    parser.add_argument('--device-id', help='override the device id')
    parser.add_argument('--verbose', action='store_true', help='enable debug logging')
    parser.add_argument(
        '--max-retries',
        type=int,
        help='max retry attempts for transient sync server errors',
    )
    parser.add_argument(
        '--retry-base-delay',
        type=float,
        help='base delay (seconds) for exponential backoff retries',
    )
    parser.add_argument(
        '--retry-max-delay',
        type=float,
        help='cap (seconds) on the computed backoff delay',
    )
    parser.add_argument(
        '--conflict-policy',
        help=(
            'conflict resolution policy: manual (default, writes .conflict '
            'files), local-wins, or remote-wins'
        ),
    )
    parser.add_argument(
        '--sync-attachments',
        action='store_true',
        default=None,
        help='also sync allowed attachments (images/PDFs), not just .md',
    )
    parser.add_argument(
        '--no-sync-attachments',
        dest='sync_attachments',
        action='store_false',
        help='disable attachment syncing even if config/env enables it',
    )
    parser.add_argument(
        '--attachment-max-bytes',
        type=int,
        help='skip local attachments larger than this many bytes',
    )


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format='%(levelname)s %(message)s',
        stream=sys.stderr,
    )


def _overrides(args: argparse.Namespace) -> CliOverrides:
    require_refresh = getattr(args, 'require_obsidian_refresh', False)
    return CliOverrides(
        vault_root=args.vault_root,
        vault_id=args.vault_id,
        server_base_url=args.server,
        device_id=args.device_id,
        require_obsidian_refresh=True if require_refresh else None,
        max_retries=args.max_retries,
        retry_base_delay=args.retry_base_delay,
        retry_max_delay=args.retry_max_delay,
        conflict_policy=args.conflict_policy,
        sync_attachments=args.sync_attachments,
        attachment_max_bytes=args.attachment_max_bytes,
        watch_debounce_seconds=getattr(args, 'watch_debounce_seconds', None),
        watch_interval_seconds=getattr(args, 'watch_interval_seconds', None),
    )


def _print_summary(summary: SyncSummary) -> None:
    prefix = 'DRY RUN: ' if summary.dry_run else ''
    logger.info(
        '%spulled=%d applied=%d locally_deleted=%d pushed=%d '
        'remotely_deleted=%d conflicts=%d warnings=%d',
        prefix,
        summary.pulled,
        summary.applied,
        summary.locally_deleted,
        summary.pushed,
        summary.remotely_deleted,
        len(summary.conflicts),
        len(summary.warnings),
    )
    for path in summary.conflicts:
        logger.info('conflict: %s (a .conflict copy was written)', path)


def _run_sync_command(args: argparse.Namespace) -> int:
    config = load_config(_overrides(args))
    summary = run_sync(config, dry_run=bool(args.dry_run), logger=logger)
    _print_summary(summary)

    if summary.dry_run:
        return EXIT_OK
    if config.require_obsidian_refresh and not summary.obsidian_ok:
        logger.error(
            'obsidian refresh required but failed: %s', summary.obsidian_message
        )
        return EXIT_OBSIDIAN
    if summary.conflicts:
        logger.info(
            'sync completed with %d conflict(s); review the .conflict files',
            len(summary.conflicts),
        )
        return EXIT_CONFLICTS
    logger.info('sync completed successfully')
    return EXIT_OK


def _run_watch_command(args: argparse.Namespace) -> int:
    config = load_config(_overrides(args))
    return run_watch(config, logger)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(bool(args.verbose))
    try:
        if args.command == 'sync':
            return _run_sync_command(args)
        if args.command == 'status':
            run_status(load_config(_overrides(args)), logger)
            return EXIT_OK
        if args.command == 'watch':
            return _run_watch_command(args)
    except ConfigError as exc:
        logger.error('configuration error: %s', exc)
        return EXIT_ERROR
    except SyncError as exc:
        logger.error('sync failed: %s', exc)
        return EXIT_ERROR
    return EXIT_ERROR


if __name__ == '__main__':
    raise SystemExit(main())
