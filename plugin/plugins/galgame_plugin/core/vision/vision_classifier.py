from __future__ import annotations

import asyncio
import time
from typing import Any

import numpy as np

from ...models import (
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
)


GALGAME_VISION_LABELS: tuple[str, ...] = (
    "dialogue",
    "choice_menu",
    "backlog",
    "save_load",
    "gallery",
    "title_screen",
    "config",
    "gameplay",
    "menu_main",
    "loading",
    "unknown",
)

_LABEL_TO_SCREEN_TYPE = {
    "dialogue": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    "choice_menu": OCR_CAPTURE_PROFILE_STAGE_MENU,
    "backlog": OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    "save_load": OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    "gallery": OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    "title_screen": OCR_CAPTURE_PROFILE_STAGE_TITLE,
    "config": OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    "gameplay": OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    "menu_main": OCR_CAPTURE_PROFILE_STAGE_MENU,
    "loading": OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    "unknown": OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
}

_IMAGENET_MEAN = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)


class VisionScreenClassifier:
    """Thin ONNX inference wrapper for galgame screen classification."""

    def __init__(
        self,
        loader: Any,
        *,
        labels: tuple[str, ...] = GALGAME_VISION_LABELS,
        input_size: tuple[int, int] = (224, 224),
        inference_timeout_ms: float = 200.0,
    ) -> None:
        self._loader = loader
        self._labels = tuple(labels)
        self._input_size = (
            max(1, int(input_size[0])),
            max(1, int(input_size[1])),
        )
        self._inference_timeout_ms = max(0.0, float(inference_timeout_ms))
        self._session: Any | None = None
        self._input_name = ""
        self._model_name = ""
        self.last_error = ""

    @property
    def loaded(self) -> bool:
        return self._session is not None and bool(self._input_name)

    def load(self, model_name: str) -> bool:
        self._model_name = str(model_name or "").strip()
        if not self._model_name:
            return False
        session = self._loader.load(self._model_name)
        if session is None:
            self._session = None
            self._input_name = ""
            return False
        inputs = session.get_inputs()
        if not inputs:
            self._session = None
            self._input_name = ""
            return False
        self._session = session
        self._input_name = str(inputs[0].name)
        return True

    def reload(self) -> bool:
        if not self._model_name:
            return False
        session = self._loader.reload(self._model_name)
        if session is None:
            self._session = None
            self._input_name = ""
            return False
        inputs = session.get_inputs()
        if not inputs:
            self._session = None
            self._input_name = ""
            return False
        self._session = session
        self._input_name = str(inputs[0].name)
        return True

    def classify(self, image: Any) -> dict[str, Any] | None:
        self.last_error = ""
        if self._session is None or not self._input_name or image is None:
            return None
        try:
            tensor = self._preprocess(image)
            started_at = time.perf_counter()
            outputs = self._session.run(None, {self._input_name: tensor})
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            if self._inference_timeout_ms and latency_ms > self._inference_timeout_ms:
                self.last_error = f"timeout:{latency_ms:.3f}ms"
                return None
            logits = np.asarray(outputs[0], dtype=np.float32)
            if logits.ndim == 2:
                logits = logits[0]
            if logits.ndim != 1 or logits.size <= 0:
                return None
            scores = _softmax(logits)
            top_index = int(np.argmax(scores))
            if top_index < 0 or top_index >= len(self._labels):
                return None
            label = self._labels[top_index]
            confidence = float(scores[top_index])
            all_scores = {
                self._labels[index]: round(float(score), 6)
                for index, score in enumerate(scores[: len(self._labels)])
            }
            return {
                "label": label,
                "screen_type": _LABEL_TO_SCREEN_TYPE.get(label, OCR_CAPTURE_PROFILE_STAGE_DEFAULT),
                "confidence": round(max(0.0, min(confidence, 1.0)), 4),
                "all_scores": all_scores,
                "latency_ms": round(max(0.0, latency_ms), 3),
                "model_name": self._model_name,
            }
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    async def classify_async(self, image: Any) -> dict[str, Any] | None:
        if self._session is None or not self._input_name or image is None:
            return None
        return await asyncio.to_thread(self.classify, image)

    def _preprocess(self, image: Any) -> np.ndarray:
        image_error: ImportError | None = None
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover
            Image = None  # type: ignore[assignment]
            image_error = exc

        if hasattr(image, "convert") and hasattr(image, "resize"):
            pil_image = image.convert("RGB")
            resampling = (
                getattr(getattr(Image, "Resampling", None), "BILINEAR", None)
                if Image is not None
                else None
            )
            if resampling is None:
                resampling = getattr(Image, "BILINEAR", 2) if Image is not None else 2
            pil_image = pil_image.resize(self._input_size, resampling)
            array = np.asarray(pil_image, dtype=np.float32)
        else:
            array = np.asarray(image, dtype=np.float32)
            if array.ndim == 2:
                array = np.stack([array, array, array], axis=-1)
            if array.ndim != 3:
                raise ValueError("vision classifier image must be HWC or PIL image")
            if array.shape[-1] > 3:
                array = array[..., :3]
            if array.shape[0] != self._input_size[1] or array.shape[1] != self._input_size[0]:
                if Image is None:  # pragma: no cover
                    raise ValueError(
                        "Pillow is required to resize ndarray images"
                    ) from image_error
                resampling = getattr(getattr(Image, "Resampling", None), "BILINEAR", None)
                if resampling is None:
                    resampling = getattr(Image, "BILINEAR", 2)
                array = np.asarray(
                    Image.fromarray(np.clip(array, 0, 255).astype(np.uint8)).resize(
                        self._input_size,
                        resampling,
                    ),
                    dtype=np.float32,
                )
        array = array / 255.0
        array = (array - _IMAGENET_MEAN) / _IMAGENET_STD
        array = np.transpose(array, (2, 0, 1))
        return np.expand_dims(array.astype(np.float32, copy=False), axis=0)


def _softmax(values: np.ndarray) -> np.ndarray:
    clipped = values.astype(np.float32, copy=False)
    shifted = clipped - np.max(clipped)
    exp = np.exp(shifted)
    total = float(np.sum(exp))
    if total <= 0.0:
        return np.zeros_like(exp)
    return exp / total
