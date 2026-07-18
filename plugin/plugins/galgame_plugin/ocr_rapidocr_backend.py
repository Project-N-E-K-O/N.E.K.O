from __future__ import annotations

import threading
import time
from typing import Any

from .ocr_runtime_types import (
    OcrTextBox,
    _RAPIDOCR_INFERENCE_LOCK,
    _RAPIDOCR_RUNTIME_CACHE_LOCK,
    _RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS,
    _acquire_rapidocr_runtime_cache,
    _get_rapidocr_runtime_cache,
    _prepare_ocr_image,
    _release_rapidocr_runtime_cache,
    _rapidocr_lines_from_output,
    _rapidocr_runtime_cache_key,
    _rapidocr_text_from_output,
    _store_rapidocr_runtime_cache,
)
from plugin.plugins._shared.rapidocr.rapidocr_support import (
    inspect_rapidocr_installation,
    load_rapidocr_runtime,
)

__all__ = ["RapidOcrBackend"]

class RapidOcrBackend:
    def __init__(
        self,
        *,
        install_target_dir_raw: str,
        engine_type: str,
        lang_type: str,
        model_type: str,
        ocr_version: str,
    ) -> None:
        self._install_target_dir_raw = install_target_dir_raw
        self._engine_type = engine_type
        self._lang_type = lang_type
        self._model_type = model_type
        self._ocr_version = ocr_version
        self._runtime = None
        self._runtime_lock = threading.Lock()
        self._runtime_cache_key: tuple[str, str, str, str, str] | None = None
        self._runtime_last_used_at = 0.0
        self._runtime_cache_acquired = False
        self._warmup_started = False
        self._warmup_completed = False
        self._warmup_error = ""
        self._closed = False

    def is_available(self) -> bool:
        inspection = inspect_rapidocr_installation(
            install_target_dir_raw=self._install_target_dir_raw,
            engine_type=self._engine_type,
            lang_type=self._lang_type,
            model_type=self._model_type,
            ocr_version=self._ocr_version,
            plugin_id="galgame_plugin",
        )
        return bool(inspection.get("installed"))

    def _ensure_runtime(self) -> Any:
        now = time.monotonic()
        key = _rapidocr_runtime_cache_key(
            install_target_dir_raw=self._install_target_dir_raw,
            engine_type=self._engine_type,
            lang_type=self._lang_type,
            model_type=self._model_type,
            ocr_version=self._ocr_version,
        )
        with self._runtime_lock:
            if self._closed:
                raise RuntimeError("RapidOCR backend is closed")
            if (
                self._runtime is not None
                and self._runtime_cache_key == key
                and now - float(self._runtime_last_used_at or 0.0) < _RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS
            ):
                self._runtime_last_used_at = now
                with _RAPIDOCR_RUNTIME_CACHE_LOCK:
                    _store_rapidocr_runtime_cache(key, self._runtime, now=now)
                return self._runtime

            if self._runtime_cache_acquired and self._runtime_cache_key is not None:
                _release_rapidocr_runtime_cache(
                    self._runtime_cache_key,
                    runtime=self._runtime,
                    owner_acquired=True,
                )
                self._runtime_cache_acquired = False
            self._runtime = None
            self._runtime_cache_key = key
            with _RAPIDOCR_RUNTIME_CACHE_LOCK:
                runtime = _get_rapidocr_runtime_cache(key, now=now)
                if runtime is None:
                    runtime, _metadata = load_rapidocr_runtime(
                        install_target_dir_raw=self._install_target_dir_raw,
                        engine_type=self._engine_type,
                        lang_type=self._lang_type,
                        model_type=self._model_type,
                        ocr_version=self._ocr_version,
                        plugin_id="galgame_plugin",
                    )
                    _store_rapidocr_runtime_cache(key, runtime, now=now)
                _acquire_rapidocr_runtime_cache(key)
            self._runtime = runtime
            self._runtime_cache_acquired = True
            self._runtime_last_used_at = now
            return runtime

    def warmup_async(self, logger: Any | None = None) -> None:
        if self._closed or self._warmup_started or self._warmup_completed:
            return
        self._warmup_started = True

        def _warmup() -> None:
            try:
                import numpy as np
                from PIL import Image

                runtime = self._ensure_runtime()
                with _RAPIDOCR_INFERENCE_LOCK:
                    runtime(np.asarray(Image.new("RGB", (640, 360), "white")))
                self._warmup_completed = True
            except Exception as exc:
                self._warmup_error = str(exc)
                if logger is not None:
                    try:
                        logger.debug("ocr_reader RapidOCR warmup skipped/failed: {}", exc)
                    except Exception:
                        pass

        threading.Thread(target=_warmup, name="galgame-rapidocr-warmup", daemon=True).start()

    def close(self) -> None:
        """Release Python references that own RapidOCR's native ONNX sessions."""
        with self._runtime_lock:
            if self._closed:
                return
            self._closed = True
            runtime = self._runtime
            key = self._runtime_cache_key
            self._runtime = None
            self._runtime_cache_key = None
            self._runtime_last_used_at = 0.0
            if key is not None:
                _release_rapidocr_runtime_cache(
                    key,
                    runtime=runtime,
                    owner_acquired=self._runtime_cache_acquired,
                )
            self._runtime_cache_acquired = False

    def extract_text(self, image: Any) -> str:
        import numpy as np

        runtime = self._ensure_runtime()
        prepared = _prepare_ocr_image(image, apply_filters=False).convert("RGB")
        with _RAPIDOCR_INFERENCE_LOCK:
            output = runtime(np.asarray(prepared))
        return _rapidocr_text_from_output(output)

    def extract_text_with_boxes(self, image: Any) -> tuple[str, list[OcrTextBox]]:
        import numpy as np

        runtime = self._ensure_runtime()
        prepared = _prepare_ocr_image(image, apply_filters=False).convert("RGB")
        with _RAPIDOCR_INFERENCE_LOCK:
            output = runtime(np.asarray(prepared))
        lines = _rapidocr_lines_from_output(output)
        if not lines:
            return "", []
        scale_x = prepared.width / max(float(getattr(image, "width", prepared.width)), 1.0)
        scale_y = prepared.height / max(float(getattr(image, "height", prepared.height)), 1.0)
        boxes = [
            OcrTextBox(
                text=box.text,
                left=box.left / scale_x,
                top=box.top / scale_y,
                right=box.right / scale_x,
                bottom=box.bottom / scale_y,
                score=float(score),
            )
            for _text, _score, box in lines
            for score in (_score,)
        ]
        return "\n".join(text for text, _score, _box in lines), boxes
