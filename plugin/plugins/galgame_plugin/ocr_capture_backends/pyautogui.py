from __future__ import annotations
from typing import Any
from ..ocr_runtime_types import DetectedGameWindow, OcrCaptureProfile, _CAPTURE_BACKEND_PYAUTOGUI
from ._helpers import _require_visible_capture_target, _target_screen_capture_rect, _crop_window_image


def _is_window_on_primary_monitor(
    rect: tuple[int, int, int, int],
) -> tuple[bool, str]:
    """Check whether a window rect lies entirely within the primary monitor.

    On Windows the primary monitor occupies (0, 0, primary_w, primary_h) in
    virtual-screen coordinates.  pyautogui can only capture the primary
    monitor, so any window that extends beyond this rectangle will produce
    a corrupt (black / offset) screenshot.

    The ``import pyautogui`` below is safe because this function is only
    called from ``capture_frame()``, which is only reached after
    ``is_available()`` has already confirmed pyautogui can be imported
    (i.e. we are not headless / WSL / missing-DISPLAY).
    """
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


class PyAutoGuiCaptureBackend:
    """Cross-platform fallback in the spirit of pyautogui's screenshot path.

    Functionally similar to MssCaptureBackend on Windows (both go through GDI),
    kept as a defense-in-depth fallback in case mss fails (e.g. handle
    exhaustion).

    The backend intentionally uses pyautogui.screenshot() so the user-facing
    backend label matches the actual capture mechanism. pyautogui 0.9.54 only
    captures the primary monitor on Windows, so capture_frame() rejects windows
    outside the primary monitor and lets auto/smart mode fall through to dxcam
    or mss.
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
        import pyautogui

        _require_visible_capture_target(target, backend_kind=self.kind)
        rect = _target_screen_capture_rect(target)
        left, top, right, bottom = rect
        # Reject windows that pyautogui cannot capture (not on primary monitor).
        on_primary, reason = _is_window_on_primary_monitor(rect)
        if not on_primary:
            primary_w, primary_h = pyautogui.size()
            raise RuntimeError(
                f"pyautogui: {reason}"
                f" rect=({left},{top},{right},{bottom})"
                f" primary=({primary_w},{primary_h})"
                f" -- switch to dxcam or mss backend for multi-monitor support"
            )
        image = pyautogui.screenshot(
            region=(int(left), int(top), int(right - left), int(bottom - top))
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
