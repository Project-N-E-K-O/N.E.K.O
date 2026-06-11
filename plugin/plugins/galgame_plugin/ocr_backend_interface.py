from __future__ import annotations

from typing import Any, Protocol

from .ocr_runtime_types import DetectedGameWindow, OcrCaptureProfile

__all__ = [
    "CaptureBackend",
    "OcrBackend",
]


class CaptureBackend(Protocol):
    def is_available(self) -> bool: ...

    def describe_target(self, target: DetectedGameWindow) -> str: ...

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any: ...


class OcrBackend(Protocol):
    def is_available(self) -> bool: ...

    def extract_text(self, image: Any) -> str: ...
