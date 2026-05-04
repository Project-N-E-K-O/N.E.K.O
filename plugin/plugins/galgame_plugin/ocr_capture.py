from __future__ import annotations

from . import ocr_reader as _ocr_reader

CAPTURE_BACKEND_AUTO = _ocr_reader._CAPTURE_BACKEND_AUTO
CAPTURE_BACKEND_DXCAM = _ocr_reader._CAPTURE_BACKEND_DXCAM
CAPTURE_BACKEND_IMAGEGRAB = _ocr_reader._CAPTURE_BACKEND_IMAGEGRAB
CAPTURE_BACKEND_PRINTWINDOW = _ocr_reader._CAPTURE_BACKEND_PRINTWINDOW

_BACKGROUND_HASH_BOTTOM_INSET_RATIO = _ocr_reader._BACKGROUND_HASH_BOTTOM_INSET_RATIO
_BACKGROUND_SCENE_HASH_SIZE = _ocr_reader._BACKGROUND_SCENE_HASH_SIZE
_DXCAM_GRAB_RETRY_ATTEMPTS = _ocr_reader._DXCAM_GRAB_RETRY_ATTEMPTS
_DXCAM_GRAB_RETRY_DELAY_SECONDS = _ocr_reader._DXCAM_GRAB_RETRY_DELAY_SECONDS

CaptureBackend = _ocr_reader.CaptureBackend
ImageGrabCaptureBackend = _ocr_reader.ImageGrabCaptureBackend
PrintWindowCaptureBackend = _ocr_reader.PrintWindowCaptureBackend
DxcamCaptureBackend = _ocr_reader.DxcamCaptureBackend
Win32CaptureBackend = _ocr_reader.Win32CaptureBackend

_perceptual_hash_image = _ocr_reader._perceptual_hash_image
_target_window_rect = _ocr_reader._target_window_rect
_run_with_thread_dpi_awareness = _ocr_reader._run_with_thread_dpi_awareness
_target_client_rect = _ocr_reader._target_client_rect
_require_visible_capture_target = _ocr_reader._require_visible_capture_target
_crop_window_image = _ocr_reader._crop_window_image


def __getattr__(name: str):
    try:
        return getattr(_ocr_reader, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


__all__ = [
    "CAPTURE_BACKEND_AUTO",
    "CAPTURE_BACKEND_DXCAM",
    "CAPTURE_BACKEND_IMAGEGRAB",
    "CAPTURE_BACKEND_PRINTWINDOW",
    "CaptureBackend",
    "DxcamCaptureBackend",
    "ImageGrabCaptureBackend",
    "PrintWindowCaptureBackend",
    "Win32CaptureBackend",
    "_BACKGROUND_HASH_BOTTOM_INSET_RATIO",
    "_BACKGROUND_SCENE_HASH_SIZE",
    "_DXCAM_GRAB_RETRY_ATTEMPTS",
    "_DXCAM_GRAB_RETRY_DELAY_SECONDS",
    "_crop_window_image",
    "_perceptual_hash_image",
    "_require_visible_capture_target",
    "_run_with_thread_dpi_awareness",
    "_target_client_rect",
    "_target_window_rect",
]
