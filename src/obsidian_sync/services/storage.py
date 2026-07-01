import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from obsidian_sync.domain.hashing import sha256_file
from obsidian_sync.domain.paths import safe_vault_destination


@dataclass(slots=True)
class ArchiveMove:
    source_path: str
    original_path: Path
    holding_path: Path
    archive_temp_path: Path
    archive_final_path: Path
    archive_holding_path: Path
    archive_final_created: bool = False


@dataclass(slots=True)
class StagedReplace:
    source_path: str
    destination_path: Path
    temp_path: Path
    holding_path: Path
    promoted: bool = False

    def promote(self) -> None:
        self.destination_path.parent.mkdir(parents=True, exist_ok=True)
        if self.destination_path.exists():
            os.replace(self.destination_path, self.holding_path)
        os.replace(self.temp_path, self.destination_path)
        self.promoted = True

    def rollback(self) -> None:
        if self.promoted and self.destination_path.exists():
            self.destination_path.unlink()
        if self.holding_path.exists():
            os.replace(self.holding_path, self.destination_path)
        if self.temp_path.exists():
            self.temp_path.unlink()

    def cleanup(self, vault_root: Path) -> None:
        if self.temp_path.exists():
            self.temp_path.unlink()
        if self.holding_path.exists():
            self.holding_path.unlink()
        _remove_empty_parents(self.destination_path.parent, vault_root)


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
        staged = self.stage_replace(vault_id, source_path, content)
        try:
            staged.promote()
        except Exception:
            self.rollback_replace(staged)
            raise
        self.finish_replace(staged)

    def stage_replace(
        self,
        vault_id: str,
        source_path: str,
        content: bytes,
    ) -> StagedReplace:
        destination = self.vault_path(vault_id, source_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        suffix = uuid.uuid4().hex
        temp_path = destination.with_name(f'.{destination.name}.tmp-{suffix}')
        holding_path = destination.with_name(f'.{destination.name}.holding-{suffix}')
        with temp_path.open('wb') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        return StagedReplace(
            source_path=source_path,
            destination_path=destination,
            temp_path=temp_path,
            holding_path=holding_path,
        )

    def rollback_replace(self, staged: StagedReplace) -> None:
        staged.rollback()
        _remove_empty_parents(staged.destination_path.parent, self._vault_root)

    def finish_replace(self, staged: StagedReplace) -> None:
        staged.cleanup(self._vault_root)

    def prepare_archive_moves(
        self,
        vault_id: str,
        source_paths: list[str],
    ) -> list[ArchiveMove]:
        moves: list[ArchiveMove] = []
        for source_path in source_paths:
            suffix = uuid.uuid4().hex
            original = self.vault_path(vault_id, source_path)
            archive_final = safe_vault_destination(
                self._archive_root / vault_id,
                source_path,
            )
            archive_temp = archive_final.with_name(
                f'.{archive_final.name}.tmp-{suffix}'
            )
            holding = original.with_name(f'.{original.name}.archiving-{suffix}')
            archive_holding = archive_final.with_name(
                f'.{archive_final.name}.existing-{suffix}'
            )
            moves.append(
                ArchiveMove(
                    source_path=source_path,
                    original_path=original,
                    holding_path=holding,
                    archive_temp_path=archive_temp,
                    archive_final_path=archive_final,
                    archive_holding_path=archive_holding,
                )
            )
        return moves

    def stage_archive_moves(self, moves: list[ArchiveMove]) -> None:
        staged: list[ArchiveMove] = []
        try:
            for move in moves:
                staged.append(move)
                move.archive_temp_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(move.original_path, move.archive_temp_path)
                if move.archive_final_path.exists():
                    os.replace(move.archive_final_path, move.archive_holding_path)
                os.replace(move.original_path, move.holding_path)
                os.replace(move.archive_temp_path, move.archive_final_path)
                move.archive_final_created = True
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
            if move.archive_final_created and move.archive_final_path.exists():
                move.archive_final_path.unlink()
            if move.archive_holding_path.exists():
                os.replace(move.archive_holding_path, move.archive_final_path)

    def finish_archive_moves(self, moves: list[ArchiveMove]) -> None:
        for move in moves:
            if move.holding_path.exists():
                move.holding_path.unlink()
            if move.archive_holding_path.exists():
                move.archive_holding_path.unlink()
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
