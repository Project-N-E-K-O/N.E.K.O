"""Open a local directory in the platform file manager."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from utils.storage_policy import normalize_runtime_root


class DirectoryOpenError(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = (
            str(error_code or "open_directory_unavailable").strip()
            or "open_directory_unavailable"
        )
        self.message = (
            str(message or "当前环境暂不支持直接打开目录。").strip()
            or "当前环境暂不支持直接打开目录。"
        )


def open_directory(path: Path | str) -> None:
    target_path = normalize_runtime_root(path)
    if not target_path.exists() or not target_path.is_dir():
        raise DirectoryOpenError(
            "directory_unavailable",
            "目录不存在或不可访问。",
        )

    try:
        if sys.platform == "win32":
            os.startfile(str(target_path))  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target_path)])
            return

        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:
            raise DirectoryOpenError(
                "open_directory_unavailable",
                "当前系统未找到可用的文件管理器打开命令。",
            )
        if os.path.basename(opener) == "gio":
            subprocess.Popen([opener, "open", str(target_path)])
        else:
            subprocess.Popen([opener, str(target_path)])
    except DirectoryOpenError:
        raise
    except Exception as exc:
        raise DirectoryOpenError(
            "open_directory_failed",
            f"打开目录失败: {exc}",
        ) from exc
