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
        os.replace(temp_file_path, target)
        stage = "fsync_parent"
        _fsync_parent_dir(target)
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
