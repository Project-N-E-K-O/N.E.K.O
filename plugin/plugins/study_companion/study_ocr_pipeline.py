from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import OcrSnapshot, StudyConfig, utc_now_iso


@dataclass(slots=True)
class StudyCaptureProfile:
    left_inset_ratio: float
    right_inset_ratio: float
    top_ratio: float
    bottom_inset_ratio: float


class StudyOcrPipeline:
    def __init__(
        self,
        *,
        logger: Any,
        config: StudyConfig,
        ocr_backend: Any | None = None,
        capture_backend: Any | None = None,
    ) -> None:
        self._logger = logger
        self._config = config
        self._ocr_backend = ocr_backend
        self._capture_backend = capture_backend

    def _build_capture_profile(self) -> StudyCaptureProfile:
        return StudyCaptureProfile(
            left_inset_ratio=float(self._config.ocr_left_inset_ratio),
            right_inset_ratio=float(self._config.ocr_right_inset_ratio),
            top_ratio=float(self._config.ocr_top_ratio),
            bottom_inset_ratio=float(self._config.ocr_bottom_inset_ratio),
        )

    @staticmethod
    def _coerce_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            return " ".join(part.strip() for part in (str(item).strip() for item in value) if part)
        return str(value).strip()

    def _capture_fullscreen(self) -> Any:
        if self._capture_backend is not None and hasattr(self._capture_backend, "capture_frame"):
            profile = self._build_capture_profile()
            return self._capture_backend.capture_frame(None, profile)
        raise RuntimeError("capture backend is not configured")

    def _capture_for_target(self, target: Any) -> Any:
        if self._capture_backend is not None and hasattr(self._capture_backend, "capture_frame"):
            profile = self._build_capture_profile()
            return self._capture_backend.capture_frame(target, profile)
        return self._capture_fullscreen()

    def snapshot_from_image(self, image: Any) -> OcrSnapshot:
        if not self._config.ocr_enabled:
            return OcrSnapshot(status="disabled", captured_at=utc_now_iso(), diagnostic="ocr disabled")
        if image is None:
            return OcrSnapshot(status="empty", captured_at=utc_now_iso(), diagnostic="no image supplied")
        try:
            if self._ocr_backend is not None and hasattr(self._ocr_backend, "extract_text"):
                text = self._coerce_text(self._ocr_backend.extract_text(image))
            else:
                text = self._coerce_text(image)
        except Exception as exc:
            return OcrSnapshot(status="ocr_failed", captured_at=utc_now_iso(), diagnostic=str(exc))
        if not text:
            return OcrSnapshot(status="empty", captured_at=utc_now_iso(), diagnostic="empty OCR result")
        return OcrSnapshot(status="ok", text=text, captured_at=utc_now_iso())

    def capture_snapshot(self, target: Any | None = None) -> OcrSnapshot:
        if not self._config.ocr_enabled:
            return OcrSnapshot(status="disabled", captured_at=utc_now_iso(), diagnostic="ocr disabled")
        try:
            image = self._capture_for_target(target) if target is not None else self._capture_fullscreen()
        except Exception as exc:
            return OcrSnapshot(status="capture_failed", captured_at=utc_now_iso(), diagnostic=str(exc))
        return self.snapshot_from_image(image)
