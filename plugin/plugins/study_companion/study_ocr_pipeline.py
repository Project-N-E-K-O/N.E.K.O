from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from .models import OcrSnapshot, StudyConfig, utc_now_iso

CAPTURE_BACKEND_AUTO = "auto"
CAPTURE_BACKEND_DXCAM = "dxcam"
CAPTURE_BACKEND_MSS = "mss"
CAPTURE_BACKEND_PRINTWINDOW = "printwindow"
CAPTURE_BACKEND_PYAUTOGUI = "pyautogui"


@dataclass(slots=True)
class StudyCaptureProfile:
    left_inset_ratio: float = 0.03
    right_inset_ratio: float = 0.03
    top_ratio: float = 0.0
    bottom_inset_ratio: float = 0.0


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

    def update_config(self, config: StudyConfig) -> None:
        self._config = config
        self._ocr_backend = None
        self._capture_backend = None

    def snapshot_from_image(self, image: Any, *, backend_name: str = "") -> OcrSnapshot:
        if image is None:
            return OcrSnapshot(status="empty", captured_at=utc_now_iso(), diagnostic="no image supplied")
        return self._extract_image(image, backend_name=backend_name or self._config.ocr_backend_selection)

    def capture_snapshot(self, target: Any | None = None) -> OcrSnapshot:
        if not self._config.ocr_enabled:
            return OcrSnapshot(status="disabled", captured_at=utc_now_iso(), diagnostic="OCR is disabled")
        if target is None:
            try:
                frame = self._capture_fullscreen()
            except Exception as exc:
                return OcrSnapshot(
                    status="capture_failed",
                    captured_at=utc_now_iso(),
                    diagnostic=f"fullscreen capture failed: {exc}",
                )
            return self._extract_image(frame, backend_name=self._config.ocr_backend_selection)
        try:
            profile = StudyCaptureProfile(
                left_inset_ratio=self._config.ocr_left_inset_ratio,
                right_inset_ratio=self._config.ocr_right_inset_ratio,
                top_ratio=self._config.ocr_top_ratio,
                bottom_inset_ratio=self._config.ocr_bottom_inset_ratio,
            )
            frame = self._resolve_capture_backend().capture_frame(target, profile)
        except Exception as exc:
            return OcrSnapshot(
                status="capture_failed",
                captured_at=utc_now_iso(),
                diagnostic=str(exc),
            )
        return self._extract_image(frame, backend_name=self._config.ocr_backend_selection)

    @staticmethod
    def _capture_fullscreen() -> Any:
        try:
            from PIL import ImageGrab

            return ImageGrab.grab()
        except Exception:
            import pyautogui

            return pyautogui.screenshot()

    def _extract_image(self, image: Any, *, backend_name: str) -> OcrSnapshot:
        started = time.monotonic()
        try:
            backend = self._resolve_ocr_backend()
            raw = backend.extract_text(image)
            text, boxes = self._normalize_ocr_output(raw)
        except Exception as exc:
            return OcrSnapshot(
                status="ocr_failed",
                backend=backend_name,
                captured_at=utc_now_iso(),
                diagnostic=str(exc),
            )
        elapsed = max(0.0, time.monotonic() - started)
        return OcrSnapshot(
            text=text,
            boxes=boxes,
            status="ok" if text.strip() else "empty",
            backend=backend_name,
            captured_at=utc_now_iso(),
            diagnostic=f"ocr_duration_seconds={elapsed:.3f}",
        )

    @staticmethod
    def _normalize_ocr_output(raw: Any) -> tuple[str, list[dict[str, Any]]]:
        if raw is None:
            return "", []
        if isinstance(raw, str):
            return raw.strip(), []
        if isinstance(raw, list):
            boxes: list[dict[str, Any]] = []
            texts: list[str] = []
            for item in raw:
                to_dict = getattr(item, "to_dict", None)
                if callable(to_dict) and hasattr(item, "text"):
                    boxes.append(dict(to_dict()))
                    texts.append(str(getattr(item, "text", "") or ""))
                elif isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                    if text:
                        texts.append(text)
                    boxes.append(dict(item))
                else:
                    text = str(item or "").strip()
                    if text:
                        texts.append(text)
            return StudyOcrPipeline._join_segments(texts).strip(), boxes
        return str(raw).strip(), []

    @staticmethod
    def _join_segments(parts: list[str]) -> str:
        try:
            from plugin.plugins.galgame_plugin.ocr_backends import _join_ocr_segments

            return _join_ocr_segments(parts)
        except Exception:
            rendered = ""
            for part in parts:
                normalized = str(part or "").replace("\n", " ").strip()
                if not normalized:
                    continue
                if rendered and rendered[-1:].isascii() and normalized[:1].isascii():
                    rendered += " "
                rendered += normalized
            return rendered

    def _resolve_ocr_backend(self) -> Any:
        if self._ocr_backend is not None:
            return self._ocr_backend
        selection = str(self._config.ocr_backend_selection or "rapidocr").strip().lower()
        if selection == "tesseract":
            from plugin.plugins.galgame_plugin.ocr_backends import TesseractOcrBackend

            self._ocr_backend = TesseractOcrBackend(
                tesseract_path=self._config.ocr_tesseract_path,
                install_target_dir_raw=self._config.ocr_install_target_dir,
                languages=self._config.ocr_languages,
            )
        else:
            from plugin.plugins.galgame_plugin.ocr_backends import RapidOcrBackend

            self._ocr_backend = RapidOcrBackend(
                install_target_dir_raw=self._config.rapidocr_install_target_dir,
                engine_type=self._config.rapidocr_engine_type,
                lang_type=self._config.rapidocr_lang_type,
                model_type=self._config.rapidocr_model_type,
                ocr_version=self._config.rapidocr_ocr_version,
            )
        return self._ocr_backend

    def _resolve_capture_backend(self) -> Any:
        if self._capture_backend is not None:
            return self._capture_backend
        from plugin.plugins.galgame_plugin.ocr_capture import (
            DxcamCaptureBackend,
            MssCaptureBackend,
            PrintWindowCaptureBackend,
            PyAutoGuiCaptureBackend,
        )

        selection = str(self._config.ocr_capture_backend or CAPTURE_BACKEND_AUTO).strip().lower()
        if selection == CAPTURE_BACKEND_DXCAM:
            self._capture_backend = DxcamCaptureBackend()
        elif selection == CAPTURE_BACKEND_MSS:
            self._capture_backend = MssCaptureBackend()
        elif selection == CAPTURE_BACKEND_PYAUTOGUI:
            self._capture_backend = PyAutoGuiCaptureBackend()
        elif selection == CAPTURE_BACKEND_PRINTWINDOW:
            self._capture_backend = PrintWindowCaptureBackend()
        else:
            self._capture_backend = DxcamCaptureBackend()
        return self._capture_backend
