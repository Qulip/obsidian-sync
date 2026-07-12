"""Automatic conflict resolution for the ``local-wins`` policy.

``manual`` conflicts are handled by callers directly via
``conflict.write_conflict_file``. ``remote-wins`` conflicts are a single
local write (see ``conflict.write_local_backup_file``) and need no retry
loop, so they are handled inline by callers too. ``local-wins`` is the only
policy that needs a bounded retry loop against the server, which is what
this module centralizes so ``engine.py`` can reuse it for both the pull-side
and push-side conflict sites.
"""

import logging
from dataclasses import dataclass

from obsidian_sync.domain.hashing import sha256_bytes
from obsidian_sync.sync_agent.client import SyncClient, SyncConflictError
from obsidian_sync.sync_agent.config import AgentConfig

# The first PUT/DELETE attempt already happened (that is what produced the
# conflict being resolved here); this bounds the *additional* retries so a
# path that keeps changing underneath us cannot loop forever.
LOCAL_WINS_MAX_ATTEMPTS = 2


@dataclass(slots=True)
class LocalWinsUpsertOutcome:
    resolved: bool
    revision: int = 0
    content_hash: str = ''


def resolve_local_wins_upsert(
    client: SyncClient,
    config: AgentConfig,
    *,
    path: str,
    content: bytes,
    base_revision: int,
    logger: logging.Logger,
) -> LocalWinsUpsertOutcome:
    """Retry pushing local content as the winner, up to a bounded attempt count.

    ``content`` is the raw file bytes (``SyncClient.put_file`` decides the
    wire encoding from ``path``'s extension, so this works unchanged for
    both markdown and attachments). ``base_revision`` is the server revision
    to try first (0 to recreate a file the server has soft-deleted). On
    another 409, the reported ``server_revision`` becomes the next attempt's
    base so a single concurrent update in between does not immediately doom
    the retry.
    """
    content_hash = sha256_bytes(content)
    revision = base_revision
    for attempt in range(1, LOCAL_WINS_MAX_ATTEMPTS + 1):
        try:
            result = client.put_file(
                config.vault_id,
                path,
                device_id=config.device_id,
                base_revision=revision,
                content_hash=content_hash,
                content=content,
            )
        except SyncConflictError as exc:
            revision = _int_detail(exc.details.get('server_revision'))
            logger.warning(
                'local-wins retry %d/%d for %s hit another conflict '
                '(new server revision %s)',
                attempt,
                LOCAL_WINS_MAX_ATTEMPTS,
                path,
                revision,
            )
            continue
        return LocalWinsUpsertOutcome(
            resolved=True,
            revision=result.revision,
            content_hash=result.content_hash,
        )
    return LocalWinsUpsertOutcome(resolved=False)


def resolve_local_wins_delete(
    client: SyncClient,
    config: AgentConfig,
    *,
    path: str,
    base_revision: int,
    logger: logging.Logger,
) -> bool:
    """Retry deleting on the server as the winner, up to a bounded attempts."""
    revision = base_revision
    for attempt in range(1, LOCAL_WINS_MAX_ATTEMPTS + 1):
        try:
            client.delete_file(
                config.vault_id,
                path,
                device_id=config.device_id,
                base_revision=revision,
            )
        except SyncConflictError as exc:
            revision = _int_detail(exc.details.get('server_revision'))
            logger.warning(
                'local-wins retry %d/%d for delete of %s hit another conflict '
                '(new server revision %s)',
                attempt,
                LOCAL_WINS_MAX_ATTEMPTS,
                path,
                revision,
            )
            continue
        return True
    return False


def _int_detail(value: object) -> int:
    return value if isinstance(value, int) else 0
