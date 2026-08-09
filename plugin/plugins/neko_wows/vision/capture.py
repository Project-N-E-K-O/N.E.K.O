"""Turning a window (or the whole screen) into a 720p JPEG.

Two backends, in order: ``mss`` grabs the window rectangle off the desktop,
and Win32 ``PrintWindow`` asks the window to paint itself. The fallback
matters because mss reads whatever is physically on screen — if something
overlaps the game, mss returns the overlap.

Compression goes through ``utils.screenshot_utils.compress_screenshot`` so a
battle frame is the same 720p JPEG q80 as every other screenshot in the app.
"""

from __future__ import annotations

from utils.screenshot_utils import (
    COMPRESS_JPEG_QUALITY,
    COMPRESS_TARGET_HEIGHT,
    compress_screenshot,
)

from .window import GameWindow


def _to_jpeg(image) -> bytes:
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    return compress_screenshot(
        image, target_h=COMPRESS_TARGET_HEIGHT, quality=COMPRESS_JPEG_QUALITY,
    )


def _grab_with_mss(region: dict | None):
    """``region`` of None means the primary monitor."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        target = region if region is not None else sct.monitors[1]
        shot = sct.grab(target)
    return Image.frombytes("RGB", shot.size, shot.rgb)


def _grab_with_printwindow(window: GameWindow):
    """Ask the window to render itself into a bitmap.

    Works when the game is occluded, which is exactly when mss does not.
    ``PW_RENDERFULLCONTENT`` (3) is required for hardware-composited clients;
    without it a DirectX surface comes back black.
    """
    import win32con  # noqa: F401  (imported for parity with win32ui usage)
    import win32gui
    import win32ui
    from ctypes import windll
    from PIL import Image

    width, height = window.width, window.height
    window_dc = win32gui.GetWindowDC(window.hwnd)
    if not window_dc:
        raise RuntimeError("GetWindowDC returned 0")

    save_dc = None
    bitmap = None
    previous_bitmap = None
    try:
        mfc_dc = win32ui.CreateDCFromHandle(window_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        previous_bitmap = save_dc.SelectObject(bitmap)
        ok = windll.user32.PrintWindow(window.hwnd, save_dc.GetSafeHdc(), 3)
        if not ok:
            raise RuntimeError("PrintWindow returned 0")
        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        return Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1,
        )
    finally:
        if save_dc is not None:
            if previous_bitmap is not None:
                try:
                    save_dc.SelectObject(previous_bitmap)
                except Exception:
                    pass
            try:
                save_dc.DeleteDC()
            except Exception:
                pass
        if bitmap is not None:
            try:
                win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass
        try:
            win32gui.ReleaseDC(window.hwnd, window_dc)
        except Exception:
            pass


def capture_jpeg(window: GameWindow | None) -> bytes | None:
    """Capture ``window``, or the primary monitor when it is ``None``.

    Returns ``None`` when every backend failed, so the caller can report a
    reason to the model instead of raising mid tool-call.
    """
    if window is None:
        try:
            return _to_jpeg(_grab_with_mss(None))
        except Exception:
            return None

    region = {
        "left": window.left,
        "top": window.top,
        "width": window.width,
        "height": window.height,
    }
    for grab in (lambda: _grab_with_mss(region), lambda: _grab_with_printwindow(window)):
        try:
            return _to_jpeg(grab())
        except Exception:
            continue
    return None


__all__ = ["capture_jpeg"]
