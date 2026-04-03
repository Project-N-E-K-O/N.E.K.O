from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


_WINDOWS_REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.5)


def _replace_with_fallback(temp_path: str, target_path: Path, content: str, *, encoding: str) -> None:
    try:
        os.replace(temp_path, target_path)
        return
    except PermissionError:
        if sys.platform != "win32":
            raise

    for delay in _WINDOWS_REPLACE_RETRY_DELAYS:
        time.sleep(delay)
        try:
            os.replace(temp_path, target_path)
            return
        except PermissionError:
            continue

    # Windows 上某些防护/索引进程会短暂占用目标文件，退化为直接覆写可避免整个启动失败。
    with open(target_path, "w", encoding=encoding) as target_file:
        target_file.write(content)
        target_file.flush()
        os.fsync(target_file.fileno())

    try:
        os.remove(temp_path)
    except OSError:
        pass


def atomic_write_text(path: str | os.PathLike[str], content: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace a text file in the same directory."""
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=str(target_path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding=encoding) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        _replace_with_fallback(temp_path, target_path, content, encoding=encoding)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def atomic_write_json(
    path: str | os.PathLike[str],
    data: Any,
    *,
    encoding: str = "utf-8",
    ensure_ascii: bool = False,
    indent: int | None = 2,
    **json_kwargs: Any,
) -> None:
    """Serialize JSON and atomically replace the destination file."""
    content = json.dumps(
        data,
        ensure_ascii=ensure_ascii,
        indent=indent,
        **json_kwargs,
    )
    atomic_write_text(path, content, encoding=encoding)
