"""Shared lifecycle for small, CPU-only ONNX voice-turn models."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

from .asset_manifest import AssetManifestError, resolve_verified_assets


class RuntimeState(Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    CLOSED = "closed"


class RuntimeUnavailableError(RuntimeError):
    pass


class RuntimeInferenceError(RuntimeError):
    pass


class OnnxModelRuntime:
    """Lazy, single-flight ONNX lifecycle with a per-instance circuit breaker."""

    asset_filenames: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        enabled: bool = False,
        asset_dir: Path | None = None,
        intra_op_threads: int = 1,
        inference_error_limit: int = 3,
        enable_cpu_mem_arena: bool = False,
    ) -> None:
        if intra_op_threads <= 0:
            raise ValueError("intra_op_threads must be positive")
        if inference_error_limit <= 0:
            raise ValueError("inference_error_limit must be positive")
        self._enabled = enabled
        self._asset_dir = asset_dir
        self._intra_op_threads = intra_op_threads
        self._inference_error_limit = inference_error_limit
        self._enable_cpu_mem_arena = enable_cpu_mem_arena
        self._state = RuntimeState.UNLOADED if enabled else RuntimeState.UNAVAILABLE
        self._reason = None if enabled else "disabled_by_config"
        self._session: Any | None = None
        self._load_lock = Lock()
        self._inference_lock = Lock()
        self._consecutive_errors = 0

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def unavailable_reason(self) -> str | None:
        return self._reason

    @property
    def is_ready(self) -> bool:
        return self._state in (RuntimeState.READY, RuntimeState.DEGRADED)

    def load(self) -> bool:
        """Load once; concurrent callers share the same attempt."""

        if self.is_ready:
            return True
        if self._state in (RuntimeState.UNAVAILABLE, RuntimeState.CLOSED):
            return False
        with self._load_lock:
            if self.is_ready:
                return True
            if self._state in (RuntimeState.UNAVAILABLE, RuntimeState.CLOSED):
                return False
            self._state = RuntimeState.LOADING
            try:
                _, manifest, paths = resolve_verified_assets(
                    self.asset_filenames, override=self._asset_dir
                )
                import onnxruntime as ort

                self._load_verified(paths, manifest, ort)
            except (AssetManifestError, ImportError) as exc:
                self._mark_unavailable(type(exc).__name__.lower())
                return False
            except Exception as exc:  # model/session failures are contained
                self._mark_unavailable(f"load_error:{type(exc).__name__}")
                return False
            self._state = RuntimeState.READY
            self._reason = None
            return True

    def _make_session(self, model_path: Path, ort: Any) -> Any:
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads = self._intra_op_threads
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_cpu_mem_arena = self._enable_cpu_mem_arena
        return ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    def _load_verified(self, paths: dict[str, Path], manifest: Any, ort: Any) -> None:
        raise NotImplementedError

    def _run_session(self, output_names: Any, inputs: dict[str, Any]) -> Any:
        if not self.is_ready or self._session is None:
            raise RuntimeUnavailableError(self._reason or self._state.value)
        with self._inference_lock:
            try:
                session = self._session
                if session is None:
                    raise RuntimeUnavailableError("runtime_closed")
                outputs = session.run(output_names, inputs)
            except RuntimeUnavailableError:
                raise
            except Exception as exc:
                self._consecutive_errors += 1
                if self._consecutive_errors >= self._inference_error_limit:
                    self._mark_unavailable(f"inference_circuit_open:{type(exc).__name__}")
                else:
                    self._state = RuntimeState.DEGRADED
                    self._reason = f"inference_error:{type(exc).__name__}"
                raise RuntimeInferenceError(str(exc)) from exc
            self._consecutive_errors = 0
            self._state = RuntimeState.READY
            self._reason = None
            return outputs

    def _mark_unavailable(self, reason: str) -> None:
        self._session = None
        self._state = RuntimeState.UNAVAILABLE
        self._reason = reason

    def unload(self) -> bool:
        """Release a healthy session while keeping lazy reload available."""

        with self._load_lock:
            with self._inference_lock:
                if self._state is RuntimeState.UNLOADED:
                    return True
                if self._state not in (RuntimeState.READY, RuntimeState.DEGRADED):
                    return False
                self._session = None
                self._state = RuntimeState.UNLOADED
                self._reason = None
                self._consecutive_errors = 0
                return True

    def close(self) -> None:
        with self._load_lock:
            with self._inference_lock:
                self._session = None
                self._state = RuntimeState.CLOSED
                self._reason = "closed"
