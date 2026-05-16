"""Platform detection and cross-platform capture dispatch for galgame_plugin.

IMPORTANT: This module MUST NOT import from ocr_reader at module level
to avoid circular imports. All ocr_reader imports are lazy (inside functions).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ocr_reader import DetectedGameWindow

def is_windows() -> bool:
    # Read sys.platform dynamically rather than caching at module-load
    # time so existing tests that monkeypatch sys.platform continue to
    # exercise the cross-platform branches.
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def platform_name() -> str:
    if is_windows():
        return "windows"
    if is_macos():
        return "macos"
    if is_linux():
        return "linux"
    return "unknown"


def platform_supports_dxcam() -> bool:
    return is_windows()


def platform_supports_printwindow() -> bool:
    return is_windows()


_WIN32_ONLY_BACKEND_KINDS = frozenset({"dxcam", "printwindow"})


def is_win32_only_backend_kind(kind: str) -> bool:
    """Return True if the backend kind requires Win32 APIs."""
    return kind in _WIN32_ONLY_BACKEND_KINDS


def scan_windows() -> list["DetectedGameWindow"]:
    """Cross-platform window enumeration (lazy imports to avoid cycles).

    Windows: delegates to _default_window_scanner (win32gui.EnumWindows).
    macOS:   delegates to window_scanner_macos._scan_windows_macos (Quartz).
    Linux:   delegates to window_scanner_linux._scan_windows_linux
             (python-xlib -> wmctrl). Wayland detection is handled
             inside _scan_windows_linux (both tools fail naturally on
             pure Wayland, so it returns []); we don't hard-block by
             XDG_SESSION_TYPE at the entry layer because XWayland uses
             the same env var but X11 tools still work.
    """
    if is_windows():
        from .ocr_reader import _default_window_scanner  # noqa: PLC0415

        return _default_window_scanner()
    if is_macos():
        from .window_scanner_macos import _scan_windows_macos  # noqa: PLC0415

        return _scan_windows_macos()
    if is_linux():
        from .window_scanner_linux import _scan_windows_linux  # noqa: PLC0415

        return _scan_windows_linux()
    return []
