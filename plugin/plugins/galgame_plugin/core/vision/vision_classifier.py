from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING, Any, TypeAlias

import numpy as np

from .labels import GALGAME_VISION_LABELS, vision_label_to_screen_type
from .preprocessing import IMAGENET_MEAN, IMAGENET_STD, softmax

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

    from .vision_model_loader import VisionModelLoader

    VisionInput: TypeAlias = PILImage | np.ndarray
else:
    VisionInput: TypeAlias = object


class VisionScreenClassifier:
    """Thin ONNX inference wrapper for galgame screen classification.

    The latency threshold is a post-run health check, not a hard cancellation
    mechanism for a blocked ONNX provider.
    """

    def __init__(
        self,
        loader: VisionModelLoader,
        *,
        labels: tuple[str, ...] = GALGAME_VISION_LABELS,
        input_size: tuple[int, int] = (224, 224),
        latency_check_ms: float = 200.0,
    ) -> None:
        self._loader = loader
        self._labels = tuple(labels)
        self._input_size = (
            max(1, int(input_size[0])),
            max(1, int(input_size[1])),
        )
        self._latency_check_ms = max(0.0, float(latency_check_ms))
        self._session_lock = threading.RLock()
        self._session: Any | None = None
        self._input_name = ""
        self._model_name = ""
        self.last_error = ""

    @property
    def loaded(self) -> bool:
        with self._session_lock:
            return self._session is not None and bool(self._input_name)

    def load(self, model_name: str) -> bool:
        normalized = str(model_name or "").strip()
        if not normalized:
            return False
        session = self._loader.load(normalized)
        if session is None:
            with self._session_lock:
                self._model_name = normalized
                self._session = None
                self._input_name = ""
            return False
        inputs = session.get_inputs()
        if not inputs:
            with self._session_lock:
                self._model_name = normalized
                self._session = None
                self._input_name = ""
            return False
        with self._session_lock:
            self._model_name = normalized
            self._session = session
            self._input_name = str(inputs[0].name)
        return True

    def reload(self) -> bool:
        with self._session_lock:
            model_name = self._model_name
        if not model_name:
            return False
        session = self._loader.reload(model_name)
        if session is None:
            with self._session_lock:
                self._session = None
                self._input_name = ""
            return False
        inputs = session.get_inputs()
        if not inputs:
            with self._session_lock:
                self._session = None
                self._input_name = ""
            return False
        with self._session_lock:
            self._session = session
            self._input_name = str(inputs[0].name)
        return True

    def close(self) -> None:
        """Release the classifier and loader references to native sessions."""
        with self._session_lock:
            self._session = None
            self._input_name = ""
        self._loader.close()

    def classify(self, image: VisionInput) -> dict[str, Any] | None:
        self.last_error = ""
        with self._session_lock:
            session = self._session
            input_name = self._input_name
            model_name = self._model_name
            labels = self._labels
        if session is None or not input_name or image is None:
            return None
        try:
            tensor = self._preprocess(image)
            started_at = time.perf_counter()
            outputs = session.run(None, {input_name: tensor})
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            if self._latency_check_ms and latency_ms > self._latency_check_ms:
                self.last_error = f"latency_exceeded:{latency_ms:.3f}ms"
                return None
            logits = np.asarray(outputs[0], dtype=np.float32)
            if logits.ndim == 2:
                logits = logits[0]
            if logits.ndim != 1 or logits.size <= 0:
                return None
            if logits.size != len(labels):
                self.last_error = (
                    f"logits_label_mismatch: logits={logits.size}, labels={len(labels)}"
                )
                return None
            scores = softmax(logits)
            top_index = int(np.argmax(scores))
            if top_index < 0 or top_index >= len(labels):
                return None
            label = labels[top_index]
            confidence = float(scores[top_index])
            all_scores = {
                labels[index]: round(float(score), 6)
                for index, score in enumerate(scores[: len(labels)])
            }
            return {
                "label": label,
                "screen_type": vision_label_to_screen_type(label),
                "confidence": round(max(0.0, min(confidence, 1.0)), 4),
                "all_scores": all_scores,
                "latency_ms": round(max(0.0, latency_ms), 3),
                "model_name": model_name,
            }
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    async def classify_async(self, image: VisionInput) -> dict[str, Any] | None:
        with self._session_lock:
            loaded = self._session is not None and bool(self._input_name)
        if not loaded or image is None:
            return None
        return await asyncio.to_thread(self.classify, image)

    def _preprocess(self, image: VisionInput) -> np.ndarray:
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
        array = (array - IMAGENET_MEAN) / IMAGENET_STD
        array = np.transpose(array, (2, 0, 1))
        return np.expand_dims(array.astype(np.float32, copy=False), axis=0)
