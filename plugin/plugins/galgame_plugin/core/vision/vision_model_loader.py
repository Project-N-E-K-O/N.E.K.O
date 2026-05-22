from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

try:  # pragma: no cover - import availability is environment dependent.
    import onnxruntime as ort
except ImportError:  # pragma: no cover
    ort = None  # type: ignore[assignment]


class VisionModelLoader:
    """ONNX screen-classifier loader with provider detection and session cache."""

    def __init__(self, model_dir: str | Path, *, warmup: bool = True) -> None:
        self.model_dir = Path(model_dir)
        self._sessions: dict[str, Any] = {}
        self._providers = self._detect_providers()
        self._warmup_enabled = bool(warmup)

    @property
    def providers(self) -> list[str]:
        return list(self._providers)

    def _detect_providers(self) -> list[str]:
        if ort is None:
            return []
        available = list(ort.get_available_providers())
        providers: list[str] = []
        for preferred in (
            "DmlExecutionProvider",
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ):
            if preferred in available:
                providers.append(preferred)
                break
        return providers or ["CPUExecutionProvider"]

    def load(self, model_name: str) -> Any | None:
        if ort is None:
            return None
        if model_name in self._sessions:
            return self._sessions[model_name]
        path = self.model_dir / f"{model_name}.onnx"
        if not path.exists():
            return None
        session = ort.InferenceSession(
            str(path),
            providers=self._providers or ["CPUExecutionProvider"],
            sess_options=self._session_options(),
        )
        if self._warmup_enabled:
            self._warmup(session)
        self._sessions[model_name] = session
        return session

    def reload(self, model_name: str) -> Any | None:
        self._sessions.pop(model_name, None)
        return self.load(model_name)

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
