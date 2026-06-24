import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from obsidian_sync.domain.hashing import sha256_file
from obsidian_sync.domain.paths import safe_vault_destination


@dataclass(frozen=True, slots=True)
class ArchiveMove:
    source_path: str
    original_path: Path
    holding_path: Path
    archive_temp_path: Path
    archive_final_path: Path


class VaultStorage:
    def __init__(self, vault_root: Path, archive_root: Path) -> None:
        self._vault_root = vault_root
        self._archive_root = archive_root

    def vault_path(self, vault_id: str, source_path: str) -> Path:
        return safe_vault_destination(self._vault_root / vault_id, source_path)

    def file_hash(self, vault_id: str, source_path: str) -> str | None:
        path = self.vault_path(vault_id, source_path)
        if not path.exists():
            return None
        return sha256_file(path)

    def write_atomic(self, vault_id: str, source_path: str, content: bytes) -> None:
        destination = self.vault_path(vault_id, source_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_name(f'.{destination.name}.tmp-{os.getpid()}')
        with temp_path.open('wb') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, destination)

    def prepare_archive_moves(
        self,
        vault_id: str,
        source_paths: list[str],
    ) -> list[ArchiveMove]:
        moves: list[ArchiveMove] = []
        for source_path in source_paths:
            original = self.vault_path(vault_id, source_path)
            archive_final = safe_vault_destination(
                self._archive_root / vault_id,
                source_path,
            )
            archive_temp = archive_final.with_name(
                f'.{archive_final.name}.tmp-{os.getpid()}'
            )
            holding = original.with_name(f'.{original.name}.archiving-{os.getpid()}')
            moves.append(
                ArchiveMove(
                    source_path=source_path,
                    original_path=original,
                    holding_path=holding,
                    archive_temp_path=archive_temp,
                    archive_final_path=archive_final,
                )
            )
        return moves

    def stage_archive_moves(self, moves: list[ArchiveMove]) -> None:
        staged: list[ArchiveMove] = []
        try:
            for move in moves:
                move.archive_temp_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(move.original_path, move.archive_temp_path)
                os.replace(move.original_path, move.holding_path)
                os.replace(move.archive_temp_path, move.archive_final_path)
                staged.append(move)
        except Exception:
            self.rollback_archive_moves(staged)
            for move in moves:
                if move.archive_temp_path.exists():
                    move.archive_temp_path.unlink()
            raise

    def rollback_archive_moves(self, moves: list[ArchiveMove]) -> None:
        for move in reversed(moves):
            if move.holding_path.exists():
                if move.original_path.exists():
                    move.original_path.unlink()
                os.replace(move.holding_path, move.original_path)
            if move.archive_final_path.exists():
                move.archive_final_path.unlink()

    def finish_archive_moves(self, moves: list[ArchiveMove]) -> None:
        for move in moves:
            if move.holding_path.exists():
                move.holding_path.unlink()
            _remove_empty_parents(move.original_path.parent, self._vault_root)


def _remove_empty_parents(path: Path, stop_at: Path) -> None:
    stop = stop_at.resolve(strict=False)
    current = path.resolve(strict=False)
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
