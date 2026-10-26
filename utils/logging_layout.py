"""Shared log identity and directory policy, without initialization side effects.

Startup creates the selected directory; readers resolve the same pure policy.
Importing this module never installs handlers, creates directories or cleans logs.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

from config import APP_NAME

NEKO_STORAGE_SELECTED_ROOT_ENV = "NEKO_STORAGE_SELECTED_ROOT"


def sanitize_log_identifier(plugin_id: str, max_len: int = 64) -> str:
    raw = str(plugin_id)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw).strip("_-")
    if not safe:
        safe = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    if len(safe) > max_len:
        digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
        safe = f"{safe[:max_len - 13]}_{digest}"
    return safe


def plugin_log_service_name(plugin_id: str) -> str:
    return f"Plugin_{sanitize_log_identifier(plugin_id)}"


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def documents_directory() -> Path:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except (AttributeError, OSError):
            pass
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                return Path(os.path.expandvars(winreg.QueryValueEx(key, "Personal")[0]))
        except (ImportError, OSError):
            pass
        directory = Path.home() / "Documents"
        return directory if directory.exists() else Path.home() / "文档"
    if sys.platform == "darwin":
        return Path.home() / "Documents"
    return Path(os.path.expandvars(os.getenv("XDG_DOCUMENTS_DIR", "~/Documents"))).expanduser()


def log_directory_candidates(app_name: str | None = None, *, documents: Path | None = None) -> list[Path]:
    name = APP_NAME if app_name is None else app_name
    candidates: list[Path] = []
    selected = os.getenv(NEKO_STORAGE_SELECTED_ROOT_ENV, "").strip()
    if selected and Path(selected).expanduser().is_absolute():
        candidates.append(Path(selected).expanduser() / "logs")
    candidates.extend([(documents if documents is not None else documents_directory()) / name / "logs",
                       application_directory() / "logs"])
    if sys.platform == "win32":
        if os.getenv("APPDATA"):
            candidates.append(Path(os.environ["APPDATA"]) / name / "logs")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / name / "logs")
    else:
        candidates.append(Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / name / "logs")
    candidates.extend([Path.home() / f".{name}" / "logs", Path(tempfile.gettempdir()) / name / "logs"])
    return list(dict.fromkeys(candidates))


def _can_use(directory: Path) -> bool:
    """Check the existing ancestor, without creating or probing the directory."""
    ancestor = directory
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    return ancestor.is_dir() and os.access(ancestor, os.W_OK | os.X_OK)


def log_subdirectory(log_subdir: str | None) -> Path:
    if not log_subdir:
        return Path()
    if log_subdir in (".", "..") or "/" in log_subdir or "\\" in log_subdir:
        raise ValueError("log_subdir must be a single directory name")
    return Path(log_subdir)


def select_log_directory(candidates: list[Path]) -> Path:
    """One side-effect-free selection policy shared by writers and readers."""
    return next((candidate for candidate in candidates if _can_use(candidate)), candidates[-1])


def resolve_log_directory(*, app_name: str | None = None, log_subdir: str | None = None) -> Path:
    return select_log_directory(log_directory_candidates(app_name)) / log_subdirectory(log_subdir)
