from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import HTTPException

from plugin.logging_config import get_logger

logger = get_logger("server.infrastructure.config_storage")


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


def atomic_write_bytes(*, target: Path, payload: bytes, prefix: str) -> None:
    try:
        temp_fd, temp_path = tempfile.mkstemp(
            suffix=".toml",
            prefix=prefix,
            dir=str(target.parent),
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create temporary file for {target}",
        ) from exc

    temp_file_path = Path(temp_path)
    try:
        with os.fdopen(temp_fd, "wb") as temp_file:
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_file_path, target)
        _fsync_parent_dir(target)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
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
            detail=f"Failed to persist config file {target}",
        ) from exc


def atomic_write_text(*, target: Path, text: str, prefix: str) -> None:
    atomic_write_bytes(target=target, payload=text.encode("utf-8"), prefix=prefix)
