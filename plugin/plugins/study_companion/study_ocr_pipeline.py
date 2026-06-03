from __future__ import annotations

import base64
from dataclasses import dataclass, field
import io
import logging
import math
import subprocess
import sys
import time
from typing import Any

from PIL import Image

from .models import ActivitySnapshot, OcrSnapshot, StudyConfig, utc_now_iso
from .screen_classifier import classify_screen_from_ocr

CAPTURE_BACKEND_AUTO = "auto"
CAPTURE_BACKEND_DXCAM = "dxcam"
CAPTURE_BACKEND_MSS = "mss"
CAPTURE_BACKEND_PRINTWINDOW = "printwindow"
CAPTURE_BACKEND_PYAUTOGUI = "pyautogui"
_VISION_SNAPSHOT_JPEG_QUALITY = 72
_VISION_SNAPSHOT_TTL_SECONDS = 8.0
_logger = logging.getLogger(__name__)
_win32_title_warning_emitted = False

try:
    _PIL_RESAMPLING = Image.Resampling
except AttributeError:  # pragma: no cover - Pillow < 9.1 compatibility.
    _PIL_RESAMPLING = Image


@dataclass(slots=True)
class StudyCaptureProfile:
    left_inset_ratio: float = 0.03
    right_inset_ratio: float = 0.03
    top_ratio: float = 0.0
    bottom_inset_ratio: float = 0.0


@dataclass(slots=True)
class LightweightSnapshot:
    status: str = "empty"
    captured_at: str = ""
    window_title: str = ""
    app_type: str = "other"
    activity_type: str = ""
    ocr_text_snippet: str = ""
    jpeg_bytes: bytes | None = None
    jpeg_base64: str = ""
    jpeg_metadata: dict[str, Any] = field(default_factory=dict)
    diagnostic: str = ""
    timestamp: float = 0.0
    classify_method: str = ""

    def __post_init__(self) -> None:
        if not self.captured_at:
            self.captured_at = utc_now_iso()
        if not self.timestamp:
            self.timestamp = time.time()
        self.window_title = str(self.window_title or "").strip()
        self.app_type = str(self.app_type or "other").strip() or "other"
        self.activity_type = str(self.activity_type or "").strip()
        self.ocr_text_snippet = str(self.ocr_text_snippet or "").strip()
        self.jpeg_base64 = str(self.jpeg_base64 or "")
        self.jpeg_metadata = dict(self.jpeg_metadata or {})
        self.diagnostic = str(self.diagnostic or "")
        self.classify_method = str(self.classify_method or "").strip()

    def to_activity_snapshot(self) -> ActivitySnapshot | None:
        if self.status not in {"ok", "empty"}:
            return None
        method = self.classify_method
        if not method:
            method = "both" if self.ocr_text_snippet else "title"
        return ActivitySnapshot(
            timestamp=self.timestamp,
            first_seen_at=self.timestamp,
            app_type=self.app_type,
            activity_type=self.activity_type,
            classify_method=method,
            ocr_text_snippet=self.ocr_text_snippet,
            window_title=self.window_title,
        )


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
        self._latest_vision_snapshot: dict[str, Any] = {}
        self._latest_vision_image_base64 = ""

    def update_config(self, config: StudyConfig) -> None:
        self._config = config
        self._ocr_backend = None
        self._capture_backend = None
        self._clear_vision_snapshot()

    def snapshot_from_image(self, image: Any, *, backend_name: str = "") -> OcrSnapshot:
        if image is None:
            self._clear_vision_snapshot()
            return OcrSnapshot(status="empty", captured_at=utc_now_iso(), diagnostic="no image supplied")
        return self._extract_image(image, backend_name=backend_name or self._config.ocr_backend_selection)

    def capture_snapshot(self, target: Any | None = None) -> OcrSnapshot:
        if not self._config.ocr_enabled:
            self._clear_vision_snapshot()
            return OcrSnapshot(status="disabled", captured_at=utc_now_iso(), diagnostic="OCR is disabled")
        if target is None:
            try:
                frame = self._capture_fullscreen()
            except Exception as exc:
                self._clear_vision_snapshot()
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
            self._clear_vision_snapshot()
            return OcrSnapshot(
                status="capture_failed",
                captured_at=utc_now_iso(),
                diagnostic=str(exc),
            )
        return self._extract_image(frame, backend_name=self._config.ocr_backend_selection)

    def capture_lightweight(self, target: Any | None = None) -> LightweightSnapshot:
        title = self._target_title(target) or self._get_active_window_title()
        captured_at = utc_now_iso()
        try:
            image = self._capture_lightweight_image(target)
        except Exception as exc:
            return LightweightSnapshot(
                status="capture_failed",
                captured_at=captured_at,
                window_title=title,
                diagnostic=str(exc),
            )

        jpeg_metadata: dict[str, int | float] = {}
        try:
            jpeg_bytes = self._encode_lightweight_jpeg(
                image,
                max_bytes=self._config.awareness.image_max_bytes,
                metadata=jpeg_metadata,
            )
        except Exception as exc:
            return LightweightSnapshot(
                status="capture_failed",
                captured_at=captured_at,
                window_title=title,
                diagnostic=f"lightweight jpeg encode failed: {exc}",
            )

        app_type = "unknown"
        ocr_result = self._extract_lightweight_ocr(
            image,
            title=title,
            app_type=app_type,
            jpeg_bytes=jpeg_bytes,
            jpeg_metadata=jpeg_metadata,
            captured_at=captured_at,
        )
        if isinstance(ocr_result, LightweightSnapshot):
            return ocr_result
        activity_type, ocr_text, classify_method, diagnostic = ocr_result

        has_semantic_signal = bool(
            title or ocr_text or (activity_type and activity_type != "idle")
        )
        status = "ok" if has_semantic_signal else "empty"
        return LightweightSnapshot(
            status=status,
            captured_at=captured_at,
            window_title=title,
            app_type=app_type,
            activity_type=activity_type,
            ocr_text_snippet=ocr_text,
            jpeg_bytes=jpeg_bytes,
            jpeg_base64=self._jpeg_data_url(jpeg_bytes),
            jpeg_metadata=jpeg_metadata,
            diagnostic=diagnostic,
            classify_method=classify_method,
        )

    def _extract_lightweight_ocr(
        self,
        image: Any,
        *,
        title: str,
        app_type: str,
        jpeg_bytes: bytes,
        jpeg_metadata: dict[str, int | float],
        captured_at: str,
    ) -> tuple[str, str, str, str] | LightweightSnapshot:
        if self._config.awareness.classify_mode not in {"ocr_text", "both"}:
            return "", "", "title", ""
        if not self._config.ocr_enabled:
            return "", "", "title", "OCR is disabled"

        started = time.monotonic()
        try:
            backend = self._resolve_ocr_backend()
            raw = backend.extract_text(image)
            ocr_text, _boxes = self._normalize_ocr_output(raw)
        except Exception as exc:
            diagnostic = f"ocr_failed: {exc}"
            if title:
                classification = classify_screen_from_ocr("", window_title=title)
                return LightweightSnapshot(
                    status="ok",
                    captured_at=captured_at,
                    window_title=title,
                    app_type=app_type,
                    activity_type=classification.screen_type,
                    jpeg_bytes=jpeg_bytes,
                    jpeg_base64=self._jpeg_data_url(jpeg_bytes),
                    jpeg_metadata=jpeg_metadata,
                    diagnostic=diagnostic,
                    classify_method="title",
                )
            return LightweightSnapshot(
                status="ocr_failed",
                captured_at=captured_at,
                window_title=title,
                app_type=app_type,
                jpeg_bytes=jpeg_bytes,
                jpeg_base64=self._jpeg_data_url(jpeg_bytes),
                jpeg_metadata=jpeg_metadata,
                diagnostic=diagnostic,
            )
        classification = classify_screen_from_ocr(
            ocr_text,
            window_title=title,
        )
        return (
            classification.screen_type,
            ocr_text,
            "both" if title else "ocr",
            f"ocr_duration_seconds={time.monotonic() - started:.3f}",
        )

    def _capture_lightweight_image(self, target: Any | None) -> Any:
        if target is None:
            return self._capture_fullscreen()
        profile = StudyCaptureProfile(
            left_inset_ratio=self._config.ocr_left_inset_ratio,
            right_inset_ratio=self._config.ocr_right_inset_ratio,
            top_ratio=self._config.ocr_top_ratio,
            bottom_inset_ratio=self._config.ocr_bottom_inset_ratio,
        )
        return self._resolve_capture_backend().capture_frame(target, profile)

    @staticmethod
    def _target_title(target: Any | None) -> str:
        if target is None:
            return ""
        if isinstance(target, dict):
            return str(target.get("title") or target.get("window_title") or "").strip()
        return str(
            getattr(target, "title", None)
            or getattr(target, "window_title", None)
            or ""
        ).strip()

    @staticmethod
    def _jpeg_data_url(raw: bytes | None) -> str:
        if not raw:
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")

    @staticmethod
    def _encode_lightweight_jpeg(
        image: Any,
        *,
        max_bytes: int,
        metadata: dict[str, int | float] | None = None,
    ) -> bytes:
        if image is None or not hasattr(image, "save"):
            raise RuntimeError("lightweight capture returned no image")
        frame = image.convert("RGB") if hasattr(image, "convert") else image
        limit = max(10_240, int(max_bytes or 65_536))
        quality = 72
        last_raw = b""
        attempts = 0
        for _ in range(24):
            attempts += 1
            buffer = io.BytesIO()
            frame.save(buffer, format="JPEG", quality=quality, optimize=True)
            raw = buffer.getvalue()
            last_raw = raw
            if len(raw) <= limit:
                if metadata is not None:
                    metadata.update(
                        {
                            "attempts": attempts,
                            "limit_bytes": limit,
                            "encoded_bytes": len(raw),
                            "final_quality": quality,
                            "final_width": int(frame.size[0]),
                            "final_height": int(frame.size[1]),
                        }
                    )
                return raw
            width, height = frame.size
            if width <= 16 or height <= 16:
                break
            scale = math.sqrt(limit / max(float(len(raw)), 1.0)) * 0.9
            if quality > 36:
                quality = max(36, quality - 8)
            new_size = (
                max(16, int(width * max(0.2, min(0.95, scale)))),
                max(16, int(height * max(0.2, min(0.95, scale)))),
            )
            if new_size == frame.size:
                quality = max(24, quality - 8)
                if quality == 24:
                    new_size = (max(16, width - 1), max(16, height - 1))
            frame = frame.resize(new_size, _PIL_RESAMPLING.LANCZOS)
        if len(last_raw) <= limit:
            if metadata is not None:
                metadata.update(
                    {
                        "attempts": attempts,
                        "limit_bytes": limit,
                        "encoded_bytes": len(last_raw),
                        "final_quality": quality,
                        "final_width": int(frame.size[0]),
                        "final_height": int(frame.size[1]),
                    }
                )
            return last_raw
        raise RuntimeError(
            f"unable to encode lightweight jpeg under {limit} bytes"
        )

    @staticmethod
    def _get_active_window_title() -> str:
        global _win32_title_warning_emitted
        if sys.platform == "darwin":
            scripts = (
                'tell application "System Events" to tell (first application process whose frontmost is true) to name of first window',
                'tell application "System Events" to name of first application process whose frontmost is true',
            )
            for script in scripts:
                try:
                    result = subprocess.run(
                        ["osascript", "-e", script],
                        capture_output=True,
                        text=True,
                        timeout=1.0,
                    )
                except Exception:
                    continue
                if result.returncode == 0:
                    title = str(result.stdout or "").strip()
                    if title:
                        return title
            return ""
        if sys.platform == "win32":
            try:
                import ctypes

                user32 = ctypes.windll.user32
                hwnd = user32.GetForegroundWindow()
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    return str(buffer.value or "").strip()
            except Exception as exc:
                if not _win32_title_warning_emitted:
                    _win32_title_warning_emitted = True
                    _logger.warning(
                        "study active window title win32 lookup failed: %s",
                        exc,
                    )
        try:
            import pygetwindow

            window = pygetwindow.getActiveWindow()
            return str(getattr(window, "title", "") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _capture_fullscreen() -> Any:
        try:
            from PIL import ImageGrab

            return ImageGrab.grab(all_screens=True)
        except Exception:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                monitor = sct.monitors[0]
                shot = sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.rgb)

    def _extract_image(self, image: Any, *, backend_name: str) -> OcrSnapshot:
        started = time.monotonic()
        self._remember_vision_snapshot(image, now=started)
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

    def _clear_vision_snapshot(self) -> None:
        self._latest_vision_snapshot = {}
        self._latest_vision_image_base64 = ""

    def _remember_vision_snapshot(
        self, image: Any, *, now: float | None = None
    ) -> None:
        self._clear_vision_snapshot()
        if not bool(self._config.llm_vision_enabled):
            return
        if image is None or not hasattr(image, "save"):
            return
        if now is None:
            now = time.monotonic()
        try:
            frame = image.convert("RGB") if hasattr(image, "convert") else image
            width, height = frame.size
            if width <= 0 or height <= 0:
                self._logger.debug(
                    "study vision snapshot skipped: invalid image dimensions {}x{}",
                    width,
                    height,
                )
                return
            max_px = max(64, int(self._config.llm_vision_max_image_px or 768))
            scale = min(1.0, float(max_px) / float(max(width, height)))
            if scale < 1.0:
                frame = frame.resize(
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    _PIL_RESAMPLING.LANCZOS,
                )
                width, height = frame.size
            buffer = io.BytesIO()
            frame.save(
                buffer,
                format="JPEG",
                quality=_VISION_SNAPSHOT_JPEG_QUALITY,
                optimize=True,
            )
            raw = buffer.getvalue()
            if not raw:
                self._logger.debug("study vision snapshot skipped: empty encoded buffer")
                return
            expires_at = now + _VISION_SNAPSHOT_TTL_SECONDS
            self._latest_vision_image_base64 = (
                "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
            )
            self._latest_vision_snapshot = {
                "captured_at": utc_now_iso(),
                "expires_at_monotonic": expires_at,
                "source": "ocr_screenshot",
                "width": int(width),
                "height": int(height),
                "byte_size": len(raw),
                "ttl_seconds": _VISION_SNAPSHOT_TTL_SECONDS,
            }
        except MemoryError as exc:
            self._logger.warning("study vision snapshot memory error: {}", exc)
        except Exception as exc:
            self._logger.debug("study vision snapshot encoding skipped: {}", exc)

    def latest_vision_snapshot(self) -> dict[str, Any]:
        if not bool(self._config.llm_vision_enabled):
            return {}
        snapshot = dict(self._latest_vision_snapshot or {})
        image_base64 = str(self._latest_vision_image_base64 or "")
        if not snapshot or not image_base64:
            return {}
        now = time.monotonic()
        if now >= float(snapshot.get("expires_at_monotonic") or 0.0):
            self._clear_vision_snapshot()
            return {}
        return {
            **{
                key: value
                for key, value in snapshot.items()
                if key != "expires_at_monotonic"
            },
            "vision_image_base64": image_base64,
        }

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
            from plugin.plugins._shared.rapidocr.ocr_backends import _join_ocr_segments

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
            from .tesseract_support import TesseractOcrBackend

            self._ocr_backend = TesseractOcrBackend(
                tesseract_path=self._config.ocr_tesseract_path,
                install_target_dir_raw=self._config.ocr_install_target_dir,
                languages=self._config.ocr_languages,
            )
        else:
            from plugin.plugins._shared.rapidocr.ocr_backends import RapidOcrBackend

            self._ocr_backend = RapidOcrBackend(
                install_target_dir_raw=self._config.rapidocr_install_target_dir,
                engine_type=self._config.rapidocr_engine_type,
                lang_type=self._config.rapidocr_lang_type,
                model_type=self._config.rapidocr_model_type,
                ocr_version=self._config.rapidocr_ocr_version,
                plugin_id="study_companion",
            )
        return self._ocr_backend

    def _resolve_capture_backend(self) -> Any:
        if self._capture_backend is not None:
            return self._capture_backend
        from .study_capture_backends import (
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
