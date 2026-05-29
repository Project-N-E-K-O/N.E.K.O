from __future__ import annotations

import ctypes
import sys
from typing import Any

from ..ocr_runtime_types import (
    DetectedGameWindow,
    OcrCaptureProfile,
    _CAPTURE_BACKEND_PRINTWINDOW,
)
from ._helpers import (
    _crop_image_to_screen_rect,
    _crop_window_image,
    _require_visible_capture_target,
    _target_content_rect,
    _target_screen_capture_rect,
    _target_window_rect,
)


class PrintWindowCaptureBackend:
    kind = _CAPTURE_BACKEND_PRINTWINDOW

    def __init__(self, *, logger=None) -> None:
        self._logger = logger

    def is_available(self) -> bool:
        try:
            import win32gui
            import win32ui
            import win32con
            return bool(win32gui and win32ui and win32con)
        except ImportError:
            return False

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}({target.pid}) {target.title}"

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any:
        _require_visible_capture_target(target, backend_kind=self.kind)
        try:
            capture_rect = _target_screen_capture_rect(target)
        except Exception:
            capture_rect = _target_content_rect(target)
        window_rect = _target_window_rect(target)
        image = self._capture_full_window(target.hwnd, window_rect)
        if capture_rect != window_rect:
            image = _crop_image_to_screen_rect(
                image,
                image_rect=window_rect,
                crop_rect=capture_rect,
            )
        return _crop_window_image(
            image,
            window_rect=capture_rect,
            profile=profile,
            backend_kind=self.kind,
            backend_detail="selected_legacy_fallback",
        )

    @staticmethod
    def _capture_full_window(hwnd: int, rect: tuple[int, int, int, int]) -> Any:
        import win32gui
        import win32ui
        import win32con
        from PIL import Image

        width = int(rect[2] - rect[0])
        height = int(rect[3] - rect[1])
        hdc = win32gui.GetWindowDC(hwnd)
        if not hdc:
            raise RuntimeError("Failed to get window DC")

        bmp = None
        mem_dc = None
        hdc_mem = None
        previous_bitmap = None
        try:
            hdc_mem = win32ui.CreateDCFromHandle(hdc)
            mem_dc = hdc_mem.CreateCompatibleDC()

            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(hdc_mem, width, height)
            previous_bitmap = mem_dc.SelectObject(bmp)

            # Try PrintWindow with PW_RENDERFULLCONTENT (3) for better game capture
            # Only available on Windows 8.1+ (version 6.3+)
            PW_RENDERFULLCONTENT = 3
            success = False
            ver = sys.getwindowsversion()
            if ver.major > 6 or (ver.major == 6 and ver.minor >= 3):
                success = ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
            if not success:
                mem_dc.BitBlt((0, 0), (width, height), hdc_mem, (0, 0), win32con.SRCCOPY)

            bmp_info = bmp.GetInfo()
            bmp_str = bmp.GetBitmapBits(True)
            image = Image.frombuffer(
                "RGB",
                (bmp_info["bmWidth"], bmp_info["bmHeight"]),
                bmp_str,
                "raw",
                "BGRX",
                0,
                1,
            )
        finally:
            if mem_dc is not None:
                if previous_bitmap is not None:
                    try:
                        mem_dc.SelectObject(previous_bitmap)
                    except Exception:
                        pass
                mem_dc.DeleteDC()
            if bmp is not None:
                win32gui.DeleteObject(bmp.GetHandle())
            win32gui.ReleaseDC(hwnd, hdc)
        return image
