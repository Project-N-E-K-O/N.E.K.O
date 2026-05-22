from __future__ import annotations
import threading
from typing import Any
from ..ocr_runtime_types import DetectedGameWindow, OcrCaptureProfile, _CAPTURE_BACKEND_MSS
from ._helpers import _require_visible_capture_target, _target_screen_capture_rect, _crop_window_image
class MssCaptureBackend:
    kind = _CAPTURE_BACKEND_MSS

    def __init__(self, *, logger=None) -> None:
        self._logger = logger
        self._sct = None
        self._sct_lock = threading.RLock()

    def is_available(self) -> bool:
        try:
            import mss
            return bool(mss)
        except ImportError:
            return False

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}({target.pid}) {target.title}"

    def _sct_instance(self):
        with self._sct_lock:
            if self._sct is not None:
                return self._sct
            import mss

            self._sct = mss.mss()
            return self._sct

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any:
        from PIL import Image

        _require_visible_capture_target(target, backend_kind=self.kind)
        rect = _target_screen_capture_rect(target)
        left, top, right, bottom = rect
        monitor = {
            "left": int(left),
            "top": int(top),
            "width": int(right - left),
            "height": int(bottom - top),
        }
        with self._sct_lock:
            sct = self._sct_instance()
            shot = sct.grab(monitor)
        # mss returns BGRA; convert to RGB via PIL.
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        return _crop_window_image(
            image,
            window_rect=rect,
            profile=profile,
            backend_kind=self.kind,
            backend_detail="selected",
        )
