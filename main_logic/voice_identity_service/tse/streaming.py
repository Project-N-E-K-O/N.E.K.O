"""Continuous TSE audio with explicit raw-sample ranges and bounded DSP state."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

import numpy as np

from .contracts import BLOCK_SAMPLES, STATE_SHAPE, TseAudioChunk, TseModelError, pcm_float32, reference_float32
from .frontend import StreamingISTFT, StreamingSTFT


class SpectralModel(Protocol):
    def infer(self, spectrum: np.ndarray, embedding: np.ndarray,
              hidden: np.ndarray, cell: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]: ...


class TseStream:
    """A single continuous stream, called synchronously on one inference worker.

    push(pcm, start_sample=...) -> list[TseAudioChunk] rejects gaps and overlaps.
    flush() emits exactly the remaining real samples, without reflected padding.
    close() discards pending audio; it never flushes audio into a retired owner.
    reset(...) begins a new sample axis and zero recurrent/overlap state. A caller
    must fence any retired output before creating/resetting another stream.

    Analysis look-ahead is 256 samples. Overlap-add additionally retains future
    frames: 384..511 real samples await output during steady streaming, and the
    first output requires 512 input samples. Positions never shift by that delay.
    A caller may feed arbitrarily sized chunks; inference batches are <=40 ms.
    """

    def __init__(self, model: SpectralModel, embedding: np.ndarray, *, start_sample: int = 0) -> None:
        self._model = model
        self._lock = RLock()
        self._closed = False
        self._finished = False
        self._failed = False
        self._embedding = reference_float32(embedding)
        self._initialize(start_sample)

    def _initialize(self, start_sample: int) -> None:
        if type(start_sample) is not int or start_sample < 0:
            raise ValueError("invalid TSE start sample")
        self._next_input = start_sample
        self._next_output = start_sample
        self._origin = start_sample
        self._hidden = np.zeros(STATE_SHAPE, np.float32)
        self._cell = np.zeros(STATE_SHAPE, np.float32)
        self._stft = StreamingSTFT()
        self._istft = StreamingISTFT()

    @property
    def next_input_sample(self) -> int:
        return self._next_input

    @property
    def next_output_sample(self) -> int:
        return self._next_output

    def _ensure_active(self) -> None:
        if self._closed or self._finished or self._failed:
            raise TseModelError("TSE stream is closed, finished, or failed")

    def _chunk(self, pcm: np.ndarray) -> list[TseAudioChunk]:
        if not pcm.size:
            return []
        end_sample = self._next_output + pcm.size
        if end_sample > self._next_input:
            raise TseModelError("TSE output exceeds its real input range")
        chunk = TseAudioChunk(self._next_output, end_sample, pcm)
        self._next_output = end_sample
        return [chunk]

    def _estimate(self, spectrum: np.ndarray) -> np.ndarray:
        if spectrum.shape[-1]:
            spectrum, self._hidden, self._cell = self._model.infer(
                spectrum, self._embedding, self._hidden, self._cell,
            )
        return self._istft.push(spectrum)

    def push(self, pcm: np.ndarray, *, start_sample: int | None = None) -> list[TseAudioChunk]:
        with self._lock:
            self._ensure_active()
            samples = pcm_float32(pcm)
            if start_sample is not None and (type(start_sample) is not int or start_sample != self._next_input):
                raise ValueError("TSE input gap or overlap; begin a new stream")
            pieces = []
            try:
                for offset in range(0, samples.size, BLOCK_SAMPLES):
                    block = samples[offset:offset + BLOCK_SAMPLES]
                    self._next_input += block.size
                    pieces.extend(self._chunk(self._estimate(self._stft.push(block))))
                return pieces
            except Exception:
                # State may have advanced even if the native call failed. No
                # retry or raw-audio fallback is safe inside this stream.
                self._failed = True
                raise

    def flush(self) -> list[TseAudioChunk]:
        with self._lock:
            self._ensure_active()
            try:
                pieces = self._chunk(self._estimate(self._stft.flush()))
                pieces.extend(self._chunk(self._istft.flush(self._next_input - self._origin)))
                if self._next_output != self._next_input:
                    raise TseModelError("TSE final sample count mismatch")
                self._finished = True
                return pieces
            except Exception:
                self._failed = True
                raise

    def reset(self, *, embedding: np.ndarray | None = None, start_sample: int = 0) -> None:
        with self._lock:
            if self._closed:
                raise TseModelError("TSE stream is closed")
            if type(start_sample) is not int or start_sample < 0:
                raise ValueError("invalid TSE start sample")
            if embedding is not None:
                replacement = reference_float32(embedding)
                self._embedding.fill(0)
                self._embedding = replacement
            self._hidden.fill(0)
            self._cell.fill(0)
            self._initialize(start_sample)
            self._finished = self._failed = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._embedding.fill(0)
            self._hidden.fill(0)
            self._cell.fill(0)
            self._stft = StreamingSTFT()
            self._istft = StreamingISTFT()
