from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import subprocess
import sys
import time
from typing import Any

from PIL import Image

from .models import (
    ActivitySnapshot,
    OcrSnapshot,
    StudyConfig,
    _OCR_SNIPPET_MAX_CHARS,
    utc_now_iso,
)
from .screen_classifier import classify_app_from_title, classify_screen_from_ocr

CAPTURE_BACKEND_AUTO = "auto"
CAPTURE_BACKEND_DXCAM = "dxcam"
CAPTURE_BACKEND_MSS = "mss"
CAPTURE_BACKEND_PRINTWINDOW = "printwindow"
CAPTURE_BACKEND_PYAUTOGUI = "pyautogui"
_LIGHTWEIGHT_MAX_WIDTH = 1280
_LIGHTWEIGHT_INITIAL_JPEG_QUALITY = 60
_LIGHTWEIGHT_MIN_JPEG_QUALITY = 35
_PHASH_CHANGE_THRESHOLD = 5
_VISION_SNAPSHOT_JPEG_QUALITY = 72
_VISION_SNAPSHOT_TTL_SECONDS = 8.0

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


@dataclass(slots=True, frozen=True)
class LightweightSnapshot:
    status: str
    captured_at: str
    diagnostic: str = ""
    jpeg_bytes: bytes | None = None
    jpeg_base64: str = ""
    thumbnail_phash: str = ""
    window_title: str = ""
    app_type: str = "other"
    ocr_text_snippet: str = ""
    activity_type: str = ""
    has_content_change: bool = False

    def to_activity_snapshot(self) -> ActivitySnapshot | None:
        if self.status not in ("ok", "empty"):
            return None
        now = time.time()
        return ActivitySnapshot(
            timestamp=now,
            first_seen_at=now,
            app_type=self.app_type,
            activity_type=self.activity_type,
            classify_method="both" if self.ocr_text_snippet else "title",
            ocr_text_snippet=self.ocr_text_snippet,
            window_title=self.window_title,
            has_content_change=self.has_content_change,
            _thumbnail_hash=self.thumbnail_phash,
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
        self._last_thumbnail_phash = ""

    def update_config(self, config: StudyConfig) -> None:
        self._config = config
        self._ocr_backend = None
        self._capture_backend = None
        self._last_thumbnail_phash = ""
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
        started = time.monotonic()
        captured_at = utc_now_iso()
        try:
            if target is None:
                frame = self._capture_fullscreen()
                window_title = self._get_active_window_title()
            else:
                profile = StudyCaptureProfile(
                    left_inset_ratio=self._config.ocr_left_inset_ratio,
                    right_inset_ratio=self._config.ocr_right_inset_ratio,
                    top_ratio=self._config.ocr_top_ratio,
                    bottom_inset_ratio=self._config.ocr_bottom_inset_ratio,
                )
                frame = self._resolve_capture_backend().capture_frame(target, profile)
                window_title = self._target_window_title(target) or self._get_active_window_title()
        except Exception as exc:
            return LightweightSnapshot(
                status="capture_failed",
                captured_at=captured_at,
                diagnostic=f"capture failed: {exc}",
            )

        if frame is None or not hasattr(frame, "save"):
            return LightweightSnapshot(
                status="empty",
                captured_at=captured_at,
                diagnostic="no image supplied",
                window_title=window_title,
                app_type=classify_app_from_title(window_title),
            )

        try:
            thumbnail = self._prepare_lightweight_image(frame)
            jpeg_bytes = self._encode_lightweight_jpeg(
                thumbnail,
                max_bytes=self._config.awareness.image_max_bytes,
            )
            thumbnail_phash = self._calculate_thumbnail_phash(thumbnail)
            has_content_change = self._has_content_change(thumbnail_phash)
            self._last_thumbnail_phash = thumbnail_phash
        except Exception as exc:
            return LightweightSnapshot(
                status="capture_failed",
                captured_at=captured_at,
                diagnostic=f"lightweight processing failed: {exc}",
                window_title=window_title,
                app_type=classify_app_from_title(window_title),
            )

        app_type = classify_app_from_title(window_title)
        activity_type = ""
        ocr_text_snippet = ""
        ocr_diagnostic = ""
        classify_mode = str(self._config.awareness.classify_mode or "title_first")
        if classify_mode in {"ocr_text", "both"} and self._config.ocr_enabled:
            ocr_snapshot = self._extract_image(
                thumbnail, backend_name=self._config.ocr_backend_selection
            )
            ocr_diagnostic = f"; ocr_status={ocr_snapshot.status}"
            if ocr_snapshot.status in {"ok", "empty"}:
                normalized_text = str(ocr_snapshot.text or "").strip()
                ocr_text_snippet = normalized_text[:_OCR_SNIPPET_MAX_CHARS]
                classification = classify_screen_from_ocr(
                    normalized_text,
                    window_title=window_title,
                )
                activity_type = classification.screen_type

        elapsed = max(0.0, time.monotonic() - started)
        diagnostic = (
            f"duration_seconds={elapsed:.3f}; jpeg_bytes={len(jpeg_bytes)}"
            f"; phash={thumbnail_phash}{ocr_diagnostic}"
        )
        return LightweightSnapshot(
            status="ok",
            captured_at=captured_at,
            diagnostic=diagnostic,
            jpeg_bytes=jpeg_bytes,
            jpeg_base64=base64.b64encode(jpeg_bytes).decode("ascii"),
            thumbnail_phash=thumbnail_phash,
            window_title=window_title,
            app_type=app_type,
            ocr_text_snippet=ocr_text_snippet,
            activity_type=activity_type,
            has_content_change=has_content_change,
        )

    @staticmethod
    def _capture_fullscreen() -> Any:
        try:
            from PIL import ImageGrab

            return ImageGrab.grab()
        except Exception:
            import pyautogui

            return pyautogui.screenshot()

    @staticmethod
    def _prepare_lightweight_image(image: Any) -> Image.Image:
        frame = image.convert("RGB") if hasattr(image, "convert") else image
        width, height = frame.size
        if width <= 0 or height <= 0:
            raise RuntimeError(f"invalid image dimensions: {width}x{height}")
        scale = min(1.0, float(_LIGHTWEIGHT_MAX_WIDTH) / float(max(width, 1)))
        if scale < 1.0:
            frame = frame.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                _PIL_RESAMPLING.LANCZOS,
            )
        return frame

    @staticmethod
    def _encode_lightweight_jpeg(image: Image.Image, *, max_bytes: int) -> bytes:
        target_bytes = max(10240, int(max_bytes or 204800))
        frame = image
        quality = _LIGHTWEIGHT_INITIAL_JPEG_QUALITY
        while True:
            while quality >= _LIGHTWEIGHT_MIN_JPEG_QUALITY:
                buffer = io.BytesIO()
                frame.save(buffer, format="JPEG", quality=quality, optimize=True)
                raw = buffer.getvalue()
                if len(raw) <= target_bytes or quality == _LIGHTWEIGHT_MIN_JPEG_QUALITY:
                    break
                quality -= 5
            if len(raw) <= target_bytes or max(frame.size) <= 320:
                return raw
            width, height = frame.size
            frame = frame.resize(
                (max(1, int(width * 0.85)), max(1, int(height * 0.85))),
                _PIL_RESAMPLING.LANCZOS,
            )
            quality = _LIGHTWEIGHT_INITIAL_JPEG_QUALITY

    @staticmethod
    def _calculate_thumbnail_phash(image: Image.Image) -> str:
        try:
            import imagehash
        except ImportError as exc:
            raise ImportError(
                "imagehash is required for study awareness pHash; install imagehash"
            ) from exc
        return str(imagehash.phash(image, hash_size=8))

    def _has_content_change(self, thumbnail_phash: str) -> bool:
        previous = str(self._last_thumbnail_phash or "").strip()
        current = str(thumbnail_phash or "").strip()
        if not previous or not current:
            return True
        try:
            distance = (int(previous, 16) ^ int(current, 16)).bit_count()
        except ValueError:
            return previous != current
        return distance > _PHASH_CHANGE_THRESHOLD

    @staticmethod
    def _target_window_title(target: Any) -> str:
        if isinstance(target, dict):
            return str(target.get("title") or target.get("window_title") or "").strip()
        return str(
            getattr(target, "title", "") or getattr(target, "window_title", "") or ""
        ).strip()

    @staticmethod
    def _get_active_window_title() -> str:
        if sys.platform == "win32":
            try:
                import win32gui

                hwnd = win32gui.GetForegroundWindow()
                return str(win32gui.GetWindowText(hwnd) or "").strip()
            except Exception:
                return ""
        if sys.platform == "darwin":
            try:
                result = subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'tell application "System Events" to get name of first application process whose frontmost is true',
                    ],
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=1.0,
                )
                return str(result.stdout or "").strip()
            except Exception:
                return ""
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True,
                check=False,
                text=True,
                timeout=1.0,
            )
            return str(result.stdout or "").strip()
        except Exception:
            return ""

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
