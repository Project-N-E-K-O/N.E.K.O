from __future__ import annotations
from typing import Any
from ..ocr_runtime_types import DetectedGameWindow, OcrCaptureProfile, _CAPTURE_BACKEND_PYAUTOGUI
from ._helpers import _require_visible_capture_target, _target_screen_capture_rect, _crop_window_image
class PyAutoGuiCaptureBackend:
    """Cross-platform fallback in the spirit of pyautogui's screenshot path.

    Functionally similar to MssCaptureBackend on Windows (both go through GDI),
    kept as a defense-in-depth fallback in case mss fails (e.g. handle
    exhaustion).

    Internally we call PIL ImageGrab.grab() directly with all_screens=True
    instead of pyautogui.screenshot(), because pyautogui 0.9.54 wraps
    ImageGrab without exposing all_screens — its capture silently truncates
    to the primary monitor on multi-display setups, which would corrupt
    OCR for any galgame window placed on a secondary screen or at negative
    coordinates. The is_available() probe still gates on `import pyautogui`
    so the backend's lifecycle still tracks the user-facing PyAutoGUI label.
    """

    kind = _CAPTURE_BACKEND_PYAUTOGUI

    def __init__(self, *, logger=None) -> None:
        self._logger = logger
        self._availability_error = ""
        self._availability_error_logged = False

    @property
    def availability_error(self) -> str:
        return self._availability_error

    def is_available(self) -> bool:
        # `import pyautogui` can throw beyond ImportError in headless / WSL /
        # missing-DISPLAY environments — pyautogui's mouse module touches
        # platform display state at import time and may raise KeyError /
        # RuntimeError. Catch broadly so backend probing degrades cleanly to
        # "unavailable" instead of bubbling up and aborting capture preflight.
        try:
            import pyautogui  # noqa: F401 — gate on user-facing label
            from PIL import ImageGrab  # noqa: F401 — actual capture mechanism
            return True
        except Exception as exc:
            self._availability_error = str(exc)
            if not self._availability_error_logged and self._logger is not None:
                self._availability_error_logged = True
                debug = getattr(self._logger, "debug", None)
                if callable(debug):
                    try:
                        debug("ocr_reader pyautogui capture backend unavailable: {}", exc)
                    except Exception:
                        pass
            return False

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}({target.pid}) {target.title}"

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any:
        from PIL import ImageGrab

        _require_visible_capture_target(target, backend_kind=self.kind)
        rect = _target_screen_capture_rect(target)
        left, top, right, bottom = rect
        # all_screens=True is Windows-only in Pillow but harmlessly ignored
        # on macOS/Linux — covers multi-monitor layouts including secondary
        # displays at negative coordinates relative to the primary screen.
        image = ImageGrab.grab(
            bbox=(int(left), int(top), int(right), int(bottom)),
            all_screens=True,
        )
        if image.mode != "RGB":
            image = image.convert("RGB")
        return _crop_window_image(
            image,
            window_rect=rect,
            profile=profile,
            backend_kind=self.kind,
            backend_detail="selected",
        )
