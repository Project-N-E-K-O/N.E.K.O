"""Resolve bundle / user-data paths for standalone Testbench builds.

Used only by ``desktop_main`` / bootstrap inside ``testbench_dist``.
Testbench business code must never import this module.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True under PyInstaller (``sys.frozen``) or Nuitka (``__compiled__``)."""
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def bundle_dir() -> Path:
    """Directory that holds packaged code and datas.

    PyInstaller one-dir: ``sys._MEIPASS`` (or exe directory for some layouts).
    Dev: repository project root (``…/project``).
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    # tests/testbench_dist/src/frozen_runtime.py → parents[3] = project root
    return Path(__file__).resolve().parents[3]


def exe_dir() -> Path:
    """Directory containing the running executable (or this source tree)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def user_data_dir(*, app_name: str = "NEKO-Testbench") -> Path:
    """OS-standard writable data root for sandboxes / logs / api_keys."""
    override = os.environ.get("NEKO_TESTBENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / app_name
    return Path.home() / ".local" / "share" / app_name


def platform_runtime_tag() -> str:
    """Folder name under ``plugin/testbench/runtime/`` for the current OS/arch."""
    machine = (os.environ.get("PROCESSOR_ARCHITECTURE") or "").lower()
    if sys.platform == "win32":
        return "win-arm64" if "arm" in machine else "win-x64"
    if sys.platform == "darwin":
        import platform

        return "macos-arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "macos-x64"
    import platform

    return "linux-arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "linux-x64"
