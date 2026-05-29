"""macOS window scanning for cosplay_plugin.

Primary path: Quartz / CoreGraphics via pyobjc.
Fallback: returns empty list if pyobjc is unavailable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ocr_reader import DetectedGameWindow

_LOGGER = logging.getLogger(__name__)


def _scan_windows_macos() -> list["DetectedGameWindow"]:
    """Enumerate visible windows on macOS via Quartz.

    Requires: pip install pyobjc-framework-Quartz.
    Returns empty list if pyobjc is unavailable so the manager can
    surface unsupported_platform via the preflight, rather than
    crashing the construction path.
    """
    try:
        import Quartz  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return []

    from .ocr_reader import DetectedGameWindow  # noqa: PLC0415

    window_list = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    if not window_list:
        return []

    results: list[DetectedGameWindow] = []
    for window in window_list:
        try:
            layer = window.get(Quartz.kCGWindowLayer, 999)
            if layer != 0:
                continue

            name = window.get(Quartz.kCGWindowName, "")
            if not name or len(str(name)) < 2:
                continue

            pid = int(window.get(Quartz.kCGWindowOwnerPID, 0))
            owner = window.get(Quartz.kCGWindowOwnerName, "") or ""
            bounds = window.get(Quartz.kCGWindowBounds, {})
            if not isinstance(bounds, dict):
                continue

            x = int(bounds.get("X", 0))
            y = int(bounds.get("Y", 0))
            w = int(bounds.get("Width", 0))
            h = int(bounds.get("Height", 0))
            area = max(0, w * h)
            if area <= 0:
                continue

            # CGWindowNumber as the platform-level identifier
            # (macOS analogue of hwnd; stable per session).
            window_id = int(window.get(Quartz.kCGWindowNumber, 0))

            results.append(
                DetectedGameWindow(
                    hwnd=window_id,
                    title=str(name),
                    process_name=str(owner),
                    pid=pid,
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
            # Suppress unused variable noise — x/y are not stored on
            # DetectedGameWindow but the unpack documents intent.
            _ = (x, y)
        except Exception as exc:
            _LOGGER.debug("macos quartz window entry skipped: %s", exc)
            continue

    results.sort(key=lambda w: w.area, reverse=True)
    return results
