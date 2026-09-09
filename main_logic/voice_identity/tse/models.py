"""Synchronous CPU ONNX adapters; create/run/close on a controlled worker.

TseModel owns one shared immutable ONNX session. Every create_stream call owns
its own reference and recurrent state. TseEncoder is registration-only and
should be closed before the separator is loaded to avoid overlapping peaks.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from threading import RLock
from typing import Any, Sequence

import numpy as np

from .contracts import EMBEDDING_DIM, REFERENCE_METHOD, STATE_SHAPE, TseModelError, reference_float32
from .frontend import TseEnrollmentFrontend


def _make_session(path: Path) -> Any:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.enable_cpu_mem_arena = False
    expected = {
        "tse_stateful_fp32.onnx": (75778515, "bce77f0146c5ff01b0fc62a500b8dd94553212373b235639e8436d7be3d809a6"),
        "tse_ecapa_fp32.onnx": (24897704, "6eb9e96eed042cc59b875631deb448ea8b11a7b40918a8424d0aa0161be70e99"),
    }
    if path.name not in expected:
        raise TseModelError("unrecognized TSE model file")
    size, digest = expected[path.name]
    before = path.stat()
    if before.st_size != size:
        raise TseModelError("TSE model size mismatch")
    with path.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != digest:
        raise TseModelError("TSE model hash mismatch")
    # These exact hashes were verified to contain all tensor data. Loading the
    # verified version directory by path avoids a second 75 MB serialized copy.
    # Installers publish immutable directories; stat checks catch replacement
    # during ordinary concurrent filesystem activity, not hostile local writers.
    checked = path.stat()
    identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if identity(before) != identity(checked):
        raise TseModelError("TSE model changed during verification")
    session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    if identity(checked) != identity(path.stat()):
        raise TseModelError("TSE model changed during loading")
    return session


def _check_metadata(session: Any, inputs: dict[str, tuple], outputs: dict[str, tuple]) -> None:
    for actual, expected in ((session.get_inputs(), inputs), (session.get_outputs(), outputs)):
        if {item.name for item in actual} != set(expected):
            raise TseModelError("TSE ONNX input/output names do not match the resource contract")
        for item in actual:
            wanted = expected[item.name]
            if item.type != "tensor(float)" or len(item.shape) != len(wanted):
                raise TseModelError("TSE ONNX input/output types do not match the resource contract")
            if any(isinstance(got, int) and want is not None and got != want
                   for got, want in zip(item.shape, wanted)):
                raise TseModelError("TSE ONNX input/output shapes do not match the resource contract")


class TseEncoder:
    """Load from an installed resource directory and emit raw (192,) references."""

    reference_method = REFERENCE_METHOD

    def __init__(self, asset_dir: Path) -> None:
        self._lock = RLock()
        self._frontend = TseEnrollmentFrontend(Path(asset_dir) / "tse_frontend_constants.npz")
        self._session = _make_session(Path(asset_dir) / "tse_ecapa_fp32.onnx")
        _check_metadata(self._session, {"fbank": (1, None, 80)}, {"embedding": (1, EMBEDDING_DIM)})

    def encode(self, pcm: np.ndarray) -> np.ndarray:
        with self._lock:
            if self._session is None:
                raise TseModelError("TSE encoder is closed")
            try:
                features = self._frontend.fbank(pcm)
                embedding = self._session.run(["embedding"], {"fbank": features})[0]
                return reference_float32(embedding).reshape(EMBEDDING_DIM)
            except Exception as exc:
                raise TseModelError("TSE reference extraction failed") from exc

    def encode_references(self, reference_pcm: Sequence[np.ndarray]) -> np.ndarray:
        """Mean of exactly three independent raw embeddings; quality awaits P5.

        The separate validation recording must never be included. This method
        deliberately does not share or normalize the SpeechBrain PVAD space.
        """
        if len(reference_pcm) != 3:
            raise ValueError("TSE requires exactly three reference segments")
        with self._lock:
            embeddings = np.stack([self.encode(pcm) for pcm in reference_pcm])
            try:
                return reference_float32(embeddings.mean(axis=0, dtype=np.float32)).reshape(EMBEDDING_DIM)
            finally:
                embeddings.fill(0)

    def close(self) -> None:
        with self._lock:
            self._session = None

    def __enter__(self) -> TseEncoder:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class TseModel:
    """One single-thread CPU separator session, reusable across independent streams."""

    def __init__(self, asset_dir: Path) -> None:
        self._lock = RLock()
        self._session = _make_session(Path(asset_dir) / "tse_stateful_fp32.onnx")
        _check_metadata(
            self._session,
            {"spectrum": (1, 2, 257, None), "embedding": (1, EMBEDDING_DIM),
             "hidden": STATE_SHAPE, "cell": STATE_SHAPE},
            {"estimated": (1, 2, 257, None), "next_hidden": STATE_SHAPE, "next_cell": STATE_SHAPE},
        )

    def infer(self, spectrum: np.ndarray, embedding: np.ndarray,
              hidden: np.ndarray, cell: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        with self._lock:
            if self._session is None:
                raise TseModelError("TSE model is closed")
            try:
                estimated, next_hidden, next_cell = self._session.run(
                    ["estimated", "next_hidden", "next_cell"],
                    {"spectrum": np.ascontiguousarray(spectrum), "embedding": embedding,
                     "hidden": hidden, "cell": cell},
                )
                for value, shape in ((estimated, spectrum.shape), (next_hidden, STATE_SHAPE), (next_cell, STATE_SHAPE)):
                    if value.shape != shape or value.dtype != np.float32 or not np.isfinite(value).all():
                        raise TseModelError("TSE produced an invalid tensor")
                return estimated, next_hidden, next_cell
            except Exception as exc:
                raise TseModelError("TSE inference failed") from exc

    def create_stream(self, embedding: np.ndarray, *, start_sample: int = 0):
        from .streaming import TseStream

        with self._lock:
            if self._session is None:
                raise TseModelError("TSE model is closed")
            return TseStream(self, embedding, start_sample=start_sample)

    def close(self) -> None:
        """Wait for the synchronous call to return before releasing the session."""
        with self._lock:
            self._session = None

    def __enter__(self) -> TseModel:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
