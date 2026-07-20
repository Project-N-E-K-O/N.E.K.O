from __future__ import annotations

import logging
from pathlib import Path
import re
import threading
from typing import Any

import numpy as np

try:  # pragma: no cover - import availability is environment dependent.
    import onnxruntime as ort
except ImportError:  # pragma: no cover
    ort = None  # type: ignore[assignment]


_LOGGER = logging.getLogger(__name__)
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class VisionModelLoader:
    """ONNX screen-classifier loader with provider detection and session cache."""

    def __init__(self, model_dir: str | Path, *, warmup: bool = True) -> None:
        self.model_dir = Path(model_dir)
        self._lock = threading.RLock()
        self._sessions: dict[str, Any] = {}
        self._providers = self._detect_providers()
        self._warmup_enabled = bool(warmup)
        self.last_error = ""
        self.last_warning = ""

    @property
    def providers(self) -> list[str]:
        return list(self._providers)

    def _detect_providers(self) -> list[str]:
        if ort is None:
            return []
        available = list(ort.get_available_providers())
        accelerator = next(
            (
                preferred
                for preferred in ("DmlExecutionProvider", "CUDAExecutionProvider")
                if preferred in available
            ),
            None,
        )
        providers: list[str] = [accelerator] if accelerator else []
        if "CPUExecutionProvider" in available:
            providers.append("CPUExecutionProvider")
        return providers or ["CPUExecutionProvider"]

    def load(self, model_name: str) -> Any | None:
        model_name = str(model_name or "").strip()
        if not _MODEL_NAME_RE.fullmatch(model_name):
            self.last_error = f"invalid_model_name:{model_name!r}"
            return None
        if ort is None:
            self.last_error = "onnxruntime_unavailable"
            return None
        with self._lock:
            self.last_error = ""
            if model_name in self._sessions:
                return self._sessions[model_name]
            self.last_warning = ""
            root = self.model_dir.resolve()
            path = (self.model_dir / f"{model_name}.onnx").resolve()
            try:
                path.relative_to(root)
            except ValueError:
                self.last_error = f"invalid_model_path:{path}"
                return None
            if not path.exists():
                self.last_error = f"model_not_found:{path}"
                return None
            try:
                session = ort.InferenceSession(
                    str(path),
                    providers=self._providers or ["CPUExecutionProvider"],
                    sess_options=self._session_options(),
                )
            except Exception as exc:
                self.last_error = f"session_load_failed:{path}: {type(exc).__name__}: {exc}"
                _LOGGER.warning("failed to load vision ONNX session %s: %s", path, exc)
                return None
            if self._warmup_enabled:
                try:
                    self._warmup(session)
                except Exception as exc:
                    self.last_warning = f"warmup_failed:{path}: {type(exc).__name__}: {exc}"
                    _LOGGER.warning(
                        "vision ONNX warmup failed for %s; keeping session cached: %s",
                        path,
                        exc,
                    )
            self._sessions[model_name] = session
            return session

    def reload(self, model_name: str) -> Any | None:
        model_name = str(model_name or "").strip()
        with self._lock:
            self._sessions.pop(model_name, None)
        return self.load(model_name)

    def close(self) -> None:
        """Release cached ONNX sessions owned by this loader."""
        with self._lock:
            self._sessions.clear()

    def _session_options(self) -> Any:
        if ort is None:
            return None
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        opts.enable_mem_pattern = True
        return opts

    @staticmethod
    def _warmup(session: Any) -> None:
        inputs = session.get_inputs()
        if not inputs:
            return
        input_name = str(inputs[0].name)
        dummy = np.zeros((1, 3, 224, 224), dtype=np.float32)
        session.run(None, {input_name: dummy})
