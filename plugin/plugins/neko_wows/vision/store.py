"""A small ring of recent frames on disk, addressed by opaque handle.

Why keep them at all: the picture is pulled out of conversation history at the
end of the turn that produced it, so without a store there is no way for the
character to look again. The handle in the tool output is what makes "let me
check that shot once more" possible.

Why handles and not paths: the game's in-battle chat is *in the frame*, so a
teammate can type anything they like into a picture the model reads. If the
recall tool accepted a path fragment, that would be a complete chain from
"teammate types a line" to "LLM reads an arbitrary local file, contents go to
the model provider". Handles are looked up in an in-memory table; the path is
only ever constructed here, and nothing from the caller reaches the
filesystem.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

# Handles the plugin mints itself. Anything not matching is rejected before a
# table lookup, so a probing argument never even reaches the dict.
_HANDLE_PATTERN = re.compile(r"^shot_\d+$")


@dataclass(frozen=True)
class ShotRecord:
    shot_id: str
    path: Path
    captured_at: float
    size_bytes: int

    def as_dict(self) -> dict:
        return {
            "shot_id": self.shot_id,
            "captured_at": self.captured_at,
            "size_bytes": self.size_bytes,
        }


class ShotStore:
    """Keeps the most recent ``retain`` frames, dropping the oldest."""

    def __init__(self, directory, retain: int = 20, *, logger=None) -> None:
        self._dir = Path(directory)
        self._retain = max(1, int(retain))
        self._logger = logger
        self._records: dict[str, ShotRecord] = {}
        self._order: list[str] = []
        self._counter = 0

    def apply_retain(self, retain: int) -> None:
        self._retain = max(1, int(retain))
        self._evict()

    # ------------------------------------------------------------------
    def save(self, jpeg: bytes) -> ShotRecord | None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self._log("warning", f"screenshot dir unusable: {exc}")
            return None

        self._counter += 1
        shot_id = f"shot_{self._counter}"
        path = self._dir / f"{shot_id}.jpg"
        try:
            path.write_bytes(jpeg)
        except Exception as exc:
            self._log("warning", f"screenshot write failed: {exc}")
            return None

        record = ShotRecord(
            shot_id=shot_id,
            path=path,
            captured_at=time.time(),
            size_bytes=len(jpeg),
        )
        self._records[shot_id] = record
        self._order.append(shot_id)
        self._evict()
        return record

    def load(self, shot_id) -> bytes | None:
        """Read a frame back by handle. Never touches the filesystem for an
        argument that is not a handle this store minted."""
        if not isinstance(shot_id, str) or not _HANDLE_PATTERN.fullmatch(shot_id):
            return None
        record = self._records.get(shot_id)
        if record is None:
            return None
        try:
            return record.path.read_bytes()
        except Exception as exc:
            self._log("warning", f"screenshot read failed for {shot_id}: {exc}")
            return None

    def recent(self, limit: int = 20) -> list[ShotRecord]:
        ids = self._order[-max(0, int(limit)):] if limit else []
        return [self._records[i] for i in reversed(ids) if i in self._records]

    def clear(self) -> int:
        removed = 0
        for record in list(self._records.values()):
            if self._unlink(record):
                removed += 1
        self._records.clear()
        self._order.clear()
        return removed

    # ------------------------------------------------------------------
    def _evict(self) -> None:
        while len(self._order) > self._retain:
            oldest = self._order.pop(0)
            record = self._records.pop(oldest, None)
            if record is not None:
                self._unlink(record)

    def _unlink(self, record: ShotRecord) -> bool:
        try:
            record.path.unlink(missing_ok=True)
            return True
        except Exception as exc:
            self._log("warning", f"screenshot delete failed for {record.shot_id}: {exc}")
            return False

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        method = getattr(self._logger, level, None)
        if callable(method):
            try:
                method(message)
            except Exception:
                pass


__all__ = ["ShotRecord", "ShotStore"]
