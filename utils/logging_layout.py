"""Shared log identity and directory policy, without initialization side effects.

Writers perform writable-directory probes; cold readers only inspect candidates.
The plugin runtime exposes its actual configured directory after startup, so a
writer fallback is authoritative rather than guessed from filesystem access.
Importing this module never installs handlers, creates directories or cleans logs.
"""
from __future__ import annotations

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


def get_selected_storage_root() -> Path | None:
    raw_root = str(os.environ.get(NEKO_STORAGE_SELECTED_ROOT_ENV) or "").strip()
    if not raw_root:
        return None

    try:
        selected_root = Path(raw_root).expanduser()
    except Exception:
        return None

    if not selected_root.is_absolute():
        return None
    return selected_root


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

def log_directory_candidates(app_name=None, *, documents=None, application=None, selected_root=None):
    """Yield candidates lazily in the historical writer order, without writes.

    Writer hooks remain injectable for packaged applications and existing tests.
    Failures resolving one candidate do not prevent trying the next one.
    """
    name = APP_NAME if app_name is None else app_name
    documents = documents_directory if documents is None else documents
    application = application_directory if application is None else application
    selected_root = get_selected_storage_root if selected_root is None else selected_root
    try:
        selected = selected_root()
        if selected is not None:
            yield selected / "logs"
    except Exception as error:
        print(f"Warning: Failed to use selected storage log directory: {error}", file=sys.stderr)
    try:
        yield (documents() if callable(documents) else documents) / name / "logs"
    except Exception as error:
        print(f"Warning: Failed to use Documents directory: {error}", file=sys.stderr)
    try:
        yield application() / "logs"
    except Exception as error:
        print(f"Warning: Failed to use application directory: {error}", file=sys.stderr)
    try:
        if sys.platform == "win32":
            if os.getenv("APPDATA"):
                yield Path(os.environ["APPDATA"]) / name / "logs"
        elif sys.platform == "darwin":
            yield Path.home() / "Library" / "Application Support" / name / "logs"
        else:
            xdg_data_home = os.getenv("XDG_DATA_HOME")
            base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
            yield base / name / "logs"
    except Exception as error:
        print(f"Warning: Failed to get system data directory: {error}", file=sys.stderr)
    try:
        yield Path.home() / f".{name}" / "logs"
    except Exception as error:
        print(f"Warning: Failed to use home directory: {error}", file=sys.stderr)
    try:
        yield Path(tempfile.gettempdir()) / name / "logs"
    except Exception as error:
        print(f"Warning: Failed to use temp directory: {error}", file=sys.stderr)


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


def select_log_directory(candidates):
    """Best-effort cold inspection; active callers use their writer's directory."""
    last = None
    for candidate in candidates:
        last = candidate
        if _can_use(candidate):
            return candidate
    return last if last is not None else application_directory() / "logs"


def resolve_log_directory(*, app_name=None, log_subdir=None):
    return select_log_directory(log_directory_candidates(app_name)) / log_subdirectory(log_subdir)
