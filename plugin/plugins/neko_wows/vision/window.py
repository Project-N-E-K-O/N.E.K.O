"""Locating the World of Warships window.

Deliberately narrower than the capture stacks in ``galgame_plugin`` and
``study_companion``: this looks for one known process, does not feed OCR, and
does not need backend reordering for occluded windows. Importing their
plugin-private code across a plugin boundary is not a supported contract, so
this is a focused reimplementation rather than a fourth consumer of theirs.

Returning ``None`` is a normal outcome — the caller falls back to a full-screen
grab. The game not running, being minimized, or pywin32 being absent are all
just "no window", never an exception.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# WoWS ships as WorldOfWarships.exe / WorldOfWarships64.exe depending on the
# client generation, so match the stem rather than an exact name.
_PROCESS_PREFIX = "worldofwarships"


@dataclass(frozen=True)
class GameWindow:
    """A visible, non-minimized window with a usable rectangle."""

    hwnd: int
    left: int
    top: int
    right: int
    bottom: int
    title: str = ""
    process_name: str = ""

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def _enumerate_candidates() -> list[dict]:
    """Return every visible top-level window as a plain dict.

    Kept as a separate seam so tests can hand in a window list without a
    Windows desktop. Any import or API failure means "no windows" — a broken
    enumeration must never take down a tool call.
    """
    try:
        import win32gui
        import win32process
    except Exception:
        return []

    try:
        import psutil
    except Exception:
        psutil = None  # type: ignore[assignment]

    found: list[dict] = []

    def _collect(hwnd, _extra):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            entry = {
                "hwnd": hwnd,
                "title": win32gui.GetWindowText(hwnd) or "",
                "minimized": bool(win32gui.IsIconic(hwnd)),
                "rect": (left, top, right, bottom),
                "process_name": "",
                "exe_path": "",
            }
            if psutil is not None:
                _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid:
                    proc = psutil.Process(pid)
                    entry["process_name"] = proc.name() or ""
                    try:
                        entry["exe_path"] = proc.exe() or ""
                    except Exception:
                        # Access denied on an elevated process — the name is
                        # still enough to identify the game.
                        entry["exe_path"] = ""
            found.append(entry)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_collect, None)
    except Exception:
        return []
    return found


def _is_under(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath([
            os.path.realpath(path), os.path.realpath(directory),
        ]) == os.path.realpath(directory)
    except (ValueError, OSError):
        # Different drives on Windows raise ValueError from commonpath.
        return False


def find_game_window(game_dir: str = "") -> GameWindow | None:
    """Find the World of Warships window, or ``None``.

    ``game_dir`` (the plugin's existing install-directory setting) is used to
    cross-check the executable path when available, so an unrelated window
    that happens to be named like the game cannot be captured instead. A
    candidate whose path can't be read is still accepted on its process name
    alone — refusing would break capture on elevated installs.
    """
    for entry in _enumerate_candidates():
        process_name = str(entry.get("process_name") or "")
        if not process_name.lower().startswith(_PROCESS_PREFIX):
            continue
        if entry.get("minimized"):
            continue
        rect = entry.get("rect") or (0, 0, 0, 0)
        left, top, right, bottom = (int(v) for v in rect)
        if right - left <= 0 or bottom - top <= 0:
            continue
        exe_path = str(entry.get("exe_path") or "")
        if game_dir and exe_path and not _is_under(exe_path, game_dir):
            continue
        return GameWindow(
            hwnd=int(entry.get("hwnd") or 0),
            left=left, top=top, right=right, bottom=bottom,
            title=str(entry.get("title") or ""),
            process_name=process_name,
        )
    return None


__all__ = ["GameWindow", "find_game_window"]
