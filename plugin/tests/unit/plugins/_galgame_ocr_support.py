from __future__ import annotations

from _galgame_bridge_support import (
    _CropAwareOcrBackend,
    _enable_injected_ocr_reader,
    _FakeAdvanceInputMonitor,
    _FakeBackgroundHashCaptureBackend,
    _FakeBackgroundHashFrame,
    _FakeCaptureBackend,
    _FakeImage,
    _FakeImageCaptureBackend,
    _FakeOcrBackend,
    _FakePrintWindowBlankCaptureBackend,
    _ocr_reader_session,
    _prepare_fake_tesseract_install,
)

__all__ = [name for name in globals() if not name.startswith("__")]
