from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from typing import Any


_LOGGER = logging.getLogger(__name__)

CAPTURE_BACKEND_DXCAM = "dxcam"
CAPTURE_BACKEND_MSS = "mss"
CAPTURE_BACKEND_PRINTWINDOW = "printwindow"
CAPTURE_BACKEND_PYAUTOGUI = "pyautogui"


def _target_value(target: Any, name: str, default: Any = 0) -> Any:
    if isinstance(target, dict):
        return target.get(name, default)
    return getattr(target, name, default)


def _target_window_rect(target: Any) -> tuple[int, int, int, int]:
    hwnd = int(_target_value(target, "hwnd", 0) or 0)
    if hwnd and sys.platform == "win32":
        try:
            import pywintypes
            import win32gui

            def _read_rect() -> tuple[int, int, int, int]:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                return int(left), int(top), int(right), int(bottom)

            return _run_with_thread_dpi_awareness(_read_rect)
        except ImportError:
            _LOGGER.debug("win32gui unavailable while resolving study capture target rect", exc_info=True)
        except (OSError, pywintypes.error):
            _LOGGER.debug("win32gui.GetWindowRect failed for study capture target", exc_info=True)

    left = int(_target_value(target, "left", _target_value(target, "x", 0)) or 0)
    top = int(_target_value(target, "top", _target_value(target, "y", 0)) or 0)
    right = _target_value(target, "right", None)
    bottom = _target_value(target, "bottom", None)
    width = int(_target_value(target, "width", 0) or 0)
    height = int(_target_value(target, "height", 0) or 0)
    if right is None:
        right = left + width
    if bottom is None:
        bottom = top + height
    rect = (left, top, int(right or 0), int(bottom or 0))
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise RuntimeError("study OCR target has no usable screen rectangle")
    return rect


def _run_with_thread_dpi_awareness(
    fn: Callable[[], tuple[int, int, int, int]],
) -> tuple[int, int, int, int]:
    windll = getattr(ctypes, "windll", None)
    user32 = getattr(windll, "user32", None) if windll is not None else None
    set_context = (
        getattr(user32, "SetThreadDpiAwarenessContext", None)
        if user32 is not None
        else None
    )
    if not callable(set_context):
        return fn()
    try:
        set_context.restype = ctypes.c_void_p
        set_context.argtypes = [ctypes.c_void_p]
    except Exception:
        pass
    old_context = None
    try:
        old_context = set_context(ctypes.c_void_p(-4))
    except Exception:
        old_context = None
    try:
        return fn()
    finally:
        if old_context is not None:
            try:
                set_context(old_context)
            except Exception:
                pass


def _target_hwnd(target: Any, *, backend_kind: str) -> int:
    hwnd = int(_target_value(target, "hwnd", 0) or 0)
    if hwnd <= 0:
        raise RuntimeError(f"{backend_kind} capture requires target hwnd")
    return hwnd


def _require_visible_capture_target(target: Any, *, backend_kind: str) -> None:
    if target is None:
        raise RuntimeError(f"{backend_kind} capture requires a target window")
    if bool(_target_value(target, "is_minimized", False)):
        raise RuntimeError(f"{backend_kind} capture target is minimized")
    if bool(_target_value(target, "eligible", True)) is False:
        reason = str(_target_value(target, "exclude_reason", "") or "ineligible")
        raise RuntimeError(f"{backend_kind} capture target is not eligible: {reason}")


def _crop_image_to_profile(image: Any, profile: Any) -> Any:
    width = max(int(getattr(image, "width", 0) or 0), 1)
    height = max(int(getattr(image, "height", 0) or 0), 1)
    left = int(width * max(0.0, min(float(getattr(profile, "left_inset_ratio", 0.0) or 0.0), 1.0)))
    right = width - int(width * max(0.0, min(float(getattr(profile, "right_inset_ratio", 0.0) or 0.0), 1.0)))
    top = int(height * max(0.0, min(float(getattr(profile, "top_ratio", 0.0) or 0.0), 1.0)))
    bottom = height - int(height * max(0.0, min(float(getattr(profile, "bottom_inset_ratio", 0.0) or 0.0), 1.0)))
    if right <= left or bottom <= top:
        return image
    return image.crop((left, top, right, bottom))


def _is_window_on_primary_monitor(rect: tuple[int, int, int, int]) -> tuple[bool, str]:
    import pyautogui

    left, top, right, bottom = rect
    primary_w, primary_h = pyautogui.size()
    if left >= primary_w:
        return False, "window_entirely_in_right_secondary_monitor"
    if right <= 0:
        return False, "window_entirely_in_left_secondary_monitor"
    if top >= primary_h:
        return False, "window_entirely_in_bottom_secondary_monitor"
    if bottom <= 0:
        return False, "window_entirely_in_top_secondary_monitor"
    if left < 0 or top < 0 or right > primary_w or bottom > primary_h:
        return False, "window_spans_across_primary_and_secondary_monitor"
    return True, ""


class _BaseCaptureBackend:
    kind = ""

    def describe_target(self, target: Any) -> str:
        process = str(_target_value(target, "process_name", "") or "")
        pid = int(_target_value(target, "pid", 0) or 0)
        title = str(_target_value(target, "title", "") or "")
        return f"{process}({pid}) {title}".strip()


class MssCaptureBackend(_BaseCaptureBackend):
    kind = CAPTURE_BACKEND_MSS

    def __init__(self) -> None:
        self._sct = None
        self._sct_lock = threading.RLock()

    def is_available(self) -> bool:
        try:
            import mss

            return bool(mss)
        except ImportError:
            return False

    def _sct_instance(self) -> Any:
        with self._sct_lock:
            if self._sct is not None:
                return self._sct
            import mss

            self._sct = mss.mss()
            return self._sct

    def capture_frame(self, target: Any, profile: Any) -> Any:
        from PIL import Image

        _require_visible_capture_target(target, backend_kind=self.kind)
        left, top, right, bottom = _target_window_rect(target)
        monitor = {
            "left": int(left),
            "top": int(top),
            "width": int(right - left),
            "height": int(bottom - top),
        }
        with self._sct_lock:
            shot = self._sct_instance().grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        return _crop_image_to_profile(image, profile)


class PyAutoGuiCaptureBackend(_BaseCaptureBackend):
    kind = CAPTURE_BACKEND_PYAUTOGUI

    def is_available(self) -> bool:
        try:
            import pyautogui

            return bool(pyautogui)
        except ImportError:
            return False

    def capture_frame(self, target: Any, profile: Any) -> Any:
        import pyautogui

        _require_visible_capture_target(target, backend_kind=self.kind)
        left, top, right, bottom = _target_window_rect(target)
        if sys.platform == "win32":
            on_primary, reason = _is_window_on_primary_monitor((left, top, right, bottom))
            if not on_primary:
                primary_w, primary_h = pyautogui.size()
                raise RuntimeError(
                    f"pyautogui: {reason}"
                    f" rect=({left},{top},{right},{bottom})"
                    f" primary=({primary_w},{primary_h})"
                    " -- switch to dxcam or mss backend for multi-monitor support"
                )
        image = pyautogui.screenshot(region=(left, top, right - left, bottom - top))
        if getattr(image, "mode", "RGB") != "RGB":
            image = image.convert("RGB")
        return _crop_image_to_profile(image, profile)


class PrintWindowCaptureBackend(_BaseCaptureBackend):
    kind = CAPTURE_BACKEND_PRINTWINDOW

    def is_available(self) -> bool:
        return sys.platform == "win32"

    def capture_frame(self, target: Any, profile: Any) -> Any:
        _require_visible_capture_target(target, backend_kind=self.kind)
        hwnd = _target_hwnd(target, backend_kind=self.kind)
        rect = _target_window_rect(target)
        image = self._capture_full_window(hwnd, rect)
        return _crop_image_to_profile(image, profile)

    @staticmethod
    def _capture_full_window(hwnd: int, rect: tuple[int, int, int, int]) -> Any:
        import win32con
        import win32gui
        import win32ui
        from PIL import Image

        width = int(rect[2] - rect[0])
        height = int(rect[3] - rect[1])
        if width <= 0 or height <= 0:
            raise RuntimeError(f"Invalid window dimensions: {width}x{height}")

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

            success = False
            try:
                version = sys.getwindowsversion()
                if version.major > 6 or (version.major == 6 and version.minor >= 3):
                    success = bool(
                        ctypes.windll.user32.PrintWindow(
                            hwnd,
                            mem_dc.GetSafeHdc(),
                            3,
                        )
                    )
            except Exception:
                success = False
            if not success:
                mem_dc.BitBlt((0, 0), (width, height), hdc_mem, (0, 0), win32con.SRCCOPY)

            bmp_info = bmp.GetInfo()
            bmp_str = bmp.GetBitmapBits(True)
            return Image.frombuffer(
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


class DxcamCaptureBackend(_BaseCaptureBackend):
    kind = CAPTURE_BACKEND_DXCAM

    def __init__(self) -> None:
        self._camera = None
        self._camera_lock = threading.RLock()

    def is_available(self) -> bool:
        try:
            import dxcam

            return bool(dxcam)
        except ImportError:
            return False

    def _camera_instance(self) -> Any:
        with self._camera_lock:
            if self._camera is not None:
                return self._camera
            import dxcam

            self._camera = dxcam.create(output_color="RGB")
            return self._camera

    def capture_frame(self, target: Any, profile: Any) -> Any:
        from PIL import Image

        _require_visible_capture_target(target, backend_kind=self.kind)
        rect = _target_window_rect(target)
        frame = self._camera_instance().grab(region=rect)
        if frame is None:
            raise RuntimeError("dxcam returned no frame")
        image = Image.fromarray(frame)
        return _crop_image_to_profile(image, profile)


__all__ = [
    "CAPTURE_BACKEND_DXCAM",
    "CAPTURE_BACKEND_MSS",
    "CAPTURE_BACKEND_PRINTWINDOW",
    "CAPTURE_BACKEND_PYAUTOGUI",
    "DxcamCaptureBackend",
    "MssCaptureBackend",
    "PrintWindowCaptureBackend",
    "PyAutoGuiCaptureBackend",
]
