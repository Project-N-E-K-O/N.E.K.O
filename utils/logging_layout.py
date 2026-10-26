"""Shared log identity and directory policy, without initialization side effects.

Writers perform writable-directory probes; cold readers only inspect candidates.
The plugin runtime exposes its actual configured directory after startup, so a
writer fallback is authoritative rather than guessed from filesystem access.
Importing this module never installs handlers, creates directories or cleans logs.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import re
from datetime import date
from pathlib import Path
from typing import Literal

from config import APP_NAME

NEKO_STORAGE_SELECTED_ROOT_ENV = "NEKO_STORAGE_SELECTED_ROOT"
_LOG_SUFFIX = re.compile(r"(?:\d{8}|error)\.log(?:\.\d+|\.\d{4}-\d{2}-\d{2})?\Z")


def _log_prefix(service_name: str | None, app_name: str | None) -> str:
    name = APP_NAME if app_name is None else app_name
    return f"{name}_{service_name}_" if service_name else f"{name}_"


def log_filename(
    service_name: str | None, *, app_name: str | None = None,
    kind: Literal["regular", "error", "debug"] = "regular", day: date | None = None,
) -> str:
    """Canonical writer names, also used by published file-path inspectors."""
    stamp = (day or date.today()).strftime("%Y%m%d")
    suffix = {"regular": stamp, "error": "error", "debug": f"debug_{stamp}"}[kind]
    return f"{_log_prefix(service_name, app_name)}{suffix}.log"


def is_service_log_file(filename: str, service_name: str, *, app_name: str | None = None) -> bool:
    """Match exact service ownership, including error copies and rotations."""
    prefix = _log_prefix(service_name, app_name)
    return filename.startswith(prefix) and _LOG_SUFFIX.fullmatch(filename[len(prefix):]) is not None


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
    """Get the system's user documents directory (via system APIs)"""
    if sys.platform == "win32":
        # Windows: use the system API to find the actual Documents path.
        try:
            import ctypes
            from ctypes import windll, wintypes

            # Use SHGetFolderPath to get the Documents path.
            CSIDL_PERSONAL = 5  # My Documents
            SHGFP_TYPE_CURRENT = 0

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
            docs_dir = Path(buf.value)

            if docs_dir.exists():
                return docs_dir
        except Exception as e:
            print(f"Warning: Failed to get Documents path via API: {e}", file=sys.stderr)

        # Fallback: try reading the registry.
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            )
            docs_dir = Path(winreg.QueryValueEx(key, "Personal")[0])
            winreg.CloseKey(key)

            # Expand environment variables.
            docs_dir = Path(os.path.expandvars(str(docs_dir)))
            if docs_dir.exists():
                return docs_dir
        except Exception as e:
            print(f"Warning: Failed to get Documents path from registry: {e}", file=sys.stderr)

        # Final fallback.
        docs_dir = Path.home() / "Documents"
        if not docs_dir.exists():
            docs_dir = Path.home() / "文档"
        return docs_dir

    elif sys.platform == "darwin":
        # macOS
        return Path.home() / "Documents"
    else:
        # Linux: try XDG.
        xdg_docs = os.getenv('XDG_DOCUMENTS_DIR')
        if xdg_docs:
            return Path(xdg_docs)
        return Path.home() / "Documents"

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
    """Best-effort cold inspection; active callers use their writer's directory."""
    return next((candidate for candidate in candidates if _can_use(candidate)), candidates[-1])


def resolve_log_directory(*, app_name: str | None = None, log_subdir: str | None = None) -> Path:
    return select_log_directory(log_directory_candidates(app_name)) / log_subdirectory(log_subdir)
