from __future__ import annotations

import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from fastapi import HTTPException

from plugin.logging_config import get_logger

logger = get_logger("server.infrastructure.config_storage")
_T = TypeVar("_T")


class ConfigCommitCancelled(Exception):
    """Raised when a config write loses the request-deadline commit race."""


class ConfigCommitGuard:
    """Choose exactly one terminal outcome: cancel before commit or commit once."""

    def __init__(self, *, deadline: float) -> None:
        self._deadline = deadline
        self._lock = threading.Lock()
        self._state = "pending"

    def cancel_if_pending(self) -> bool:
        # A held lock means os.replace has already started and cannot be
        # safely interrupted.  Do not queue another worker just to discover
        # that the commit won the race.
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if self._state == "pending":
                self._state = "cancelled"
            return self._state == "cancelled"
        finally:
            self._lock.release()

    def commit(self, operation: Callable[[], _T]) -> _T:
        with self._lock:
            if self._state != "pending" or time.monotonic() >= self._deadline:
                if self._state == "pending":
                    self._state = "cancelled"
                raise ConfigCommitCancelled
            self._state = "committing"
            try:
                result = operation()
            except BaseException:
                self._state = "failed"
                raise
            self._state = "committed"
            return result


def _fsync_parent_dir(path: Path) -> None:
    try:
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        return
    finally:
        os.close(directory_fd)


def atomic_write_bytes(
    *,
    target: Path,
    payload: bytes,
    prefix: str,
    commit_guard: ConfigCommitGuard | None = None,
) -> None:
    try:
        temp_fd, temp_path = tempfile.mkstemp(
            suffix=".toml",
            prefix=prefix,
            dir=str(target.parent),
        )
    except OSError as exc:
        logger.exception(
            "Failed to create temporary config file: target={}, parent={}, prefix={}, err_type={}, err={}",
            target,
            target.parent,
            prefix,
            type(exc).__name__,
            str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create temporary file for {target}: {type(exc).__name__}: {exc}",
        ) from exc

    temp_file_path = Path(temp_path)
    stage = "write_temp"
    try:
        with os.fdopen(temp_fd, "wb") as temp_file:
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        stage = "replace"
        if commit_guard is None:
            os.replace(temp_file_path, target)
        else:
            commit_guard.commit(lambda: os.replace(temp_file_path, target))
        stage = "fsync_parent"
        _fsync_parent_dir(target)
    except ConfigCommitCancelled:
        try:
            if temp_file_path.exists():
                temp_file_path.unlink()
        except OSError as cleanup_exc:
            logger.warning(
                "Failed to cleanup cancelled config temp file {}: {}",
                temp_file_path,
                str(cleanup_exc),
            )
        raise
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.exception(
            "Failed to persist config file: target={}, temp_path={}, stage={}, err_type={}, err={}",
            target,
            temp_file_path,
            stage,
            type(exc).__name__,
            str(exc),
        )
        try:
            if temp_file_path.exists():
                temp_file_path.unlink()
        except OSError as cleanup_exc:
            logger.warning(
                "Failed to cleanup temp config file {}: {}",
                temp_file_path,
                str(cleanup_exc),
            )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to persist config file {target} while {stage}: {type(exc).__name__}: {exc}",
        ) from exc


def atomic_write_text(*, target: Path, text: str, prefix: str) -> None:
    atomic_write_bytes(target=target, payload=text.encode("utf-8"), prefix=prefix)
