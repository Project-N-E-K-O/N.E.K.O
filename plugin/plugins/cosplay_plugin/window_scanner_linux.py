"""Linux window scanning for cosplay_plugin.

Primary path: python-xlib (X11).
Fallback path: wmctrl subprocess.

Wayland is not directly supported. On pure Wayland, xlib cannot connect
and wmctrl returns no results, so this function naturally returns [].
XWayland sessions still work via the same xlib path. We do NOT hard-block
by XDG_SESSION_TYPE in the entry layer because that env var is "wayland"
under XWayland too, where the X11 tools remain available.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ocr_reader import DetectedGameWindow

_LOGGER = logging.getLogger(__name__)


def _scan_windows_linux() -> list["DetectedGameWindow"]:
    """Enumerate visible windows on Linux.

    Tries python-xlib first, falls back to wmctrl.
    Returns empty list if neither is available (pure Wayland, or missing deps).
    """
    try:
        result = _scan_windows_linux_xlib()
        if result:
            return result
    except Exception as exc:
        _LOGGER.debug("linux xlib window scan failed: %s", exc)
    try:
        return _scan_windows_linux_wmctrl()
    except Exception as exc:
        _LOGGER.debug("linux wmctrl window scan failed: %s", exc)
    return []


def _scan_windows_linux_xlib() -> list["DetectedGameWindow"]:
    """Window enumeration via python-xlib (X11/XWayland)."""
    from Xlib import X  # type: ignore[import-not-found]  # noqa: PLC0415
    from Xlib import display as xdisplay  # type: ignore[import-not-found]  # noqa: PLC0415

    from .ocr_reader import DetectedGameWindow  # noqa: PLC0415

    d = xdisplay.Display()
    try:
        root = d.screen().root

        net_client_list = d.intern_atom("_NET_CLIENT_LIST")
        net_wm_name = d.intern_atom("_NET_WM_NAME")
        net_wm_pid = d.intern_atom("_NET_WM_PID")
        net_wm_visible_name = d.intern_atom("_NET_WM_VISIBLE_NAME")
        net_wm_state = d.intern_atom("_NET_WM_STATE")
        net_wm_state_hidden = d.intern_atom("_NET_WM_STATE_HIDDEN")
        wm_state = d.intern_atom("WM_STATE")

        try:
            raw = root.get_full_property(net_client_list, X.AnyPropertyType)
            window_ids = raw.value if raw else []
        except Exception:
            return []

        results: list[DetectedGameWindow] = []
        for wid in window_ids:
            try:
                window = d.create_resource_object("window", wid)
                geom = window.get_geometry()

                title = _x11_get_window_title(window, net_wm_name, net_wm_visible_name)
                pid = _x11_get_window_pid(window, net_wm_pid)
                is_minimized = _x11_is_window_minimized(
                    window,
                    net_wm_state,
                    net_wm_state_hidden,
                    wm_state,
                )

                if not title or len(str(title)) < 2:
                    continue

                width = max(0, int(geom.width))
                height = max(0, int(geom.height))
                area = width * height
                if area <= 0:
                    continue

                results.append(
                    DetectedGameWindow(
                        hwnd=int(wid),
                        title=str(title),
                        process_name="",
                        pid=int(pid) if pid else 0,
                        class_name="",
                        exe_path="",
                        width=width,
                        height=height,
                        area=area,
                        is_foreground=False,
                        is_minimized=is_minimized,
                        score=float(area),
                    )
                )
            except Exception as exc:
                _LOGGER.debug("linux xlib window entry skipped: %s", exc)
                continue

        results.sort(key=lambda w: w.area, reverse=True)
        return results
    finally:
        try:
            d.close()
        except Exception as exc:
            _LOGGER.debug("linux xlib display close failed: %s", exc)


def _x11_get_window_title(
    window: Any,
    net_wm_name: Any,
    net_wm_visible_name: Any,
) -> str:
    """Extract window title from _NET_WM_VISIBLE_NAME or _NET_WM_NAME."""
    for atom in (net_wm_visible_name, net_wm_name):
        try:
            prop = window.get_full_property(atom, 0)
            if prop and prop.value:
                value = prop.value
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return str(value)
        except Exception as exc:
            _LOGGER.debug("linux xlib title read failed: %s", exc)
            continue
    return ""


def _x11_get_window_pid(window: Any, net_wm_pid: Any) -> int | None:
    """Extract PID from _NET_WM_PID."""
    try:
        prop = window.get_full_property(net_wm_pid, 0)
        if prop and prop.value:
            return int(prop.value[0])
    except Exception as exc:
        _LOGGER.debug("linux xlib pid read failed: %s", exc)
    return None


def _x11_is_window_minimized(
    window: Any,
    net_wm_state: Any,
    net_wm_state_hidden: Any,
    wm_state: Any,
) -> bool:
    """Return True for X11/EWMH hidden or ICCCM iconic windows."""
    try:
        prop = window.get_full_property(net_wm_state, 0)
        if prop and prop.value is not None:
            if any(int(value) == int(net_wm_state_hidden) for value in prop.value):
                return True
    except Exception as exc:
        _LOGGER.debug("linux xlib _NET_WM_STATE read failed: %s", exc)

    try:
        prop = window.get_full_property(wm_state, 0)
        if prop and prop.value is not None and len(prop.value) > 0:
            return int(prop.value[0]) == 3  # ICCCM IconicState
    except Exception as exc:
        _LOGGER.debug("linux xlib WM_STATE read failed: %s", exc)
    return False


def _scan_windows_linux_wmctrl() -> list["DetectedGameWindow"]:
    """Window enumeration via wmctrl subprocess (fallback).

    Requires the system wmctrl binary. Output format:
        <window_id> <desktop> <pid> <x> <y> <w> <h> <host> <title>
    """
    from .ocr_reader import DetectedGameWindow  # noqa: PLC0415

    wmctrl_path = shutil.which("wmctrl")
    if not wmctrl_path:
        return []
    output = subprocess.check_output([wmctrl_path, "-lpG"], text=True, timeout=5)

    results: list[DetectedGameWindow] = []
    for line in output.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        try:
            window_id = int(parts[0], 16)
            pid = int(parts[2])
            x = int(parts[3])
            y = int(parts[4])
            w = int(parts[5])
            h = int(parts[6])
            title = parts[8]
            if not title or len(title) < 2:
                continue
            area = max(0, w * h)
            if area <= 0:
                continue
            results.append(
                DetectedGameWindow(
                    hwnd=window_id,
                    title=title,
                    process_name=_linux_process_name_from_pid(pid),
                    pid=max(0, pid),
                    class_name="",
                    exe_path="",
                    width=max(0, w),
                    height=max(0, h),
                    area=area,
                    is_foreground=False,
                    is_minimized=False,
                    score=float(area),
                )
            )
        except (ValueError, IndexError):
            continue

    results.sort(key=lambda w: w.area, reverse=True)
    return results


def _linux_process_name_from_pid(pid: int) -> str:
    """Best-effort Linux process name for wmctrl's -p PID field."""
    pid = max(0, int(pid or 0))
    if pid <= 0:
        return ""
    try:
        exe_path = os.readlink(f"/proc/{pid}/exe")
        name = os.path.basename(exe_path).strip()
        if name:
            return name
    except OSError:
        pass
    try:
        with open(f"/proc/{pid}/comm", "r", encoding="utf-8") as fh:
            name = fh.read().strip()
            if name:
                return name
    except OSError:
        pass
    return ""
