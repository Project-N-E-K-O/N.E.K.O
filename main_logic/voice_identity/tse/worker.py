"""Owned asynchronous TSE execution with bounded waiting and explicit retirement.

The continuous worker uses one dedicated thread, avoiding another Python/ORT
process in the steady-state memory budget. Native inference cannot be forcibly
stopped: close returns False while that thread is still retiring. Enrollment
uses an owned spawn process so cancellation can actually reclaim the encoder.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import math
import multiprocessing
from pathlib import Path
import threading
import time
from typing import Sequence

import numpy as np

from ..extraction_reference import SpeakerExtractionReference
from .contracts import SAMPLE_RATE, TSE_ENCODER_IDENTITY, TseAudioChunk, TseModelError, pcm_float32, reference_float32
from .models import TseEncoder, TseModel


class TseEncoderRetirementError(TseModelError):
    """Inference finished without confirmation that its native process exited.

    Keep enrollment admission closed until retirement_task returns normally AND
    retirement_owner.confirmed_stopped is true. Cancellation of the waiter is
    not native termination and must never be treated as successful cleanup.
    """

    def __init__(self, owner: _EncoderRetirementOwner) -> None:
        super().__init__("TSE encoder process retirement is still pending")
        self.retirement_owner = owner
        self.retirement_task = owner.retirement_task


class _EncoderStartupRetirementPending(TseModelError):
    def __init__(self, process, receiver) -> None:
        super().__init__("TSE startup failed before native retirement was confirmed")
        self.process = process
        self.receiver = receiver


class _EncoderRetirementOwner:
    """A daemon reaper retains all handles even when its async waiter is cancelled.

    Each terminate/kill/join attempt is bounded. The asyncio task honors shutdown
    cancellation immediately; the native daemon thread keeps ownership without
    making asyncio shutdown wait forever for an uninterruptible OS operation.
    """

    def __init__(self, process, receiver) -> None:
        self._process = process
        self._receiver = receiver
        self._confirmed = threading.Event()
        self._retry_delay = threading.Event()
        self.last_error: str | None = None
        self._thread = threading.Thread(target=self._reap, name="neko-tse-encoder-retirement", daemon=True)
        self.retirement_task = asyncio.create_task(self._wait(), name="neko-tse-encoder-retirement")
        self._thread.start()

    @property
    def confirmed_stopped(self) -> bool:
        return self._confirmed.is_set() and not self._thread.is_alive()

    def _reap(self) -> None:
        while True:
            try:
                _stop_encoder_process(self._process)
            except Exception as exc:
                self.last_error = type(exc).__name__
                self._retry_delay.wait(0.5)
            else:
                self._receiver.close()
                self._confirmed.set()
                return

    async def _wait(self) -> None:
        while not self.confirmed_stopped:
            await asyncio.sleep(0.05)


@dataclass
class _WorkItem:
    generation: int
    pcm: np.ndarray | None
    start_sample: int
    created: float
    result: asyncio.Future

    @property
    def samples(self) -> int:
        return 0 if self.pcm is None else self.pcm.size


class TseWorker:
    """One continuous stream, shared by callers on the same asyncio loop.

    await start(timeout=1) loads the model; await push(pcm,start_sample=n) and
    await flush() return sample-aligned chunks. Cancellation, timeout, queue
    overflow, or inference failure retire the whole stream. The caller must
    stop ASR submission and start a new explicitly authorized session afterward.

    close(timeout=1)->bool blocks new work immediately, discards queued output,
    and waits up to the deadline for native execution and cleanup. False means
    resources remain owned by this retiring worker; do not start a replacement
    until stopped becomes True. There is no implicit raw-audio fallback.
    """

    def __init__(self, asset_dir: Path, embedding: np.ndarray, *, start_sample: int = 0,
                 max_pending_ms: float = 200, max_age_ms: float = 200) -> None:
        if type(start_sample) is not int or start_sample < 0:
            raise ValueError("invalid TSE sample origin")
        if any(not math.isfinite(value) or value <= 0 for value in (max_pending_ms, max_age_ms)):
            raise ValueError("invalid TSE queue budget")
        self._directory = Path(asset_dir)
        self._embedding = reference_float32(embedding)
        self._origin = self._next_input = start_sample
        self._capacity = max(1, int(max_pending_ms * SAMPLE_RATE / 1000))
        self._max_age = max_age_ms / 1000
        self._condition = threading.Condition()
        self._queue: deque[_WorkItem] = deque()
        self._inflight: _WorkItem | None = None
        self._pending = 0
        self._generation = 1
        self._closed = self._finishing = False
        self._failure_reason: str | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready: asyncio.Future | None = None
        self._stopped_event = threading.Event()

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def stopped(self) -> bool:
        return self._stopped_event.is_set() and (self._thread is None or not self._thread.is_alive())

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    @property
    def pending_samples(self) -> int:
        with self._condition:
            return self._pending

    @property
    def oldest_age_ms(self) -> float:
        with self._condition:
            oldest = self._inflight or (self._queue[0] if self._queue else None)
            return 0 if oldest is None else max(0, (time.monotonic() - oldest.created) * 1000)

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("TSE worker belongs to a different event loop")
        self._loop = loop

    async def start(self, *, timeout: float = 1.0) -> None:
        self._bind_loop()
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("invalid TSE startup timeout")
        if self._closed:
            raise TseModelError(self._failure_reason or "TSE worker is closed")
        if self._ready is None:
            self._ready = self._loop.create_future()
            self._thread = threading.Thread(target=self._run, name="neko-tse-stream", daemon=True)
            self._thread.start()
        try:
            await asyncio.wait_for(asyncio.shield(self._ready), timeout)
        except BaseException:
            self._retire("tse_start_interrupted")
            if self._ready.done() and not self._ready.cancelled():
                self._ready.exception()
            raise
        if self._closed:
            raise TseModelError(self._failure_reason or "TSE startup retired")

    def _schedule(self, callback, *args) -> None:
        try:
            self._loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            # An already-closed event loop cannot accept late native results.
            with self._condition:
                self._closed = True
                self._condition.notify_all()

    def _loaded(self, generation: int) -> None:
        if self._ready is not None and not self._ready.done():
            if self._closed or generation != self._generation:
                self._ready.set_exception(TseModelError("TSE startup retired"))
            else:
                self._ready.set_result(None)

    def _deliver(self, item: _WorkItem, chunks: list[TseAudioChunk] | None, error: str | None) -> None:
        if error is not None:
            self._retire(error)
        if item.result.done():
            return
        if self._closed or item.generation != self._generation:
            item.result.set_exception(TseModelError(self._failure_reason or "TSE generation retired"))
        else:
            item.result.set_result(chunks)

    def _startup_failed(self) -> None:
        self._retire("tse_model_load_failed")

    def _run(self) -> None:
        model = stream = None
        generation = self._generation
        try:
            model = TseModel(self._directory)
            with self._condition:
                if self._closed:
                    return
            stream = model.create_stream(self._embedding, start_sample=self._origin)
            self._schedule(self._loaded, generation)
            while True:
                with self._condition:
                    while not self._closed and not self._queue:
                        self._condition.wait()
                    if self._closed:
                        return
                    item = self._queue.popleft()
                    self._inflight = item
                chunks, error = None, None
                try:
                    if time.monotonic() - item.created > self._max_age:
                        raise TimeoutError("TSE queue age exceeded")
                    chunks = stream.flush() if item.pcm is None else stream.push(item.pcm, start_sample=item.start_sample)
                    if time.monotonic() - item.created > self._max_age:
                        raise TimeoutError("TSE result arrived too late")
                except Exception:
                    error = "tse_inference_failed_or_late"
                finally:
                    with self._condition:
                        self._pending -= item.samples
                        self._inflight = None
                    if item.pcm is not None:
                        item.pcm.fill(0)
                self._schedule(self._deliver, item, chunks, error)
                if error is not None:
                    return
        except Exception:
            self._schedule(self._startup_failed)
        finally:
            if stream is not None:
                stream.close()
            if model is not None:
                model.close()
            self._embedding.fill(0)
            self._stopped_event.set()

    def _retire(self, reason: str) -> None:
        with self._condition:
            if not self._closed:
                self._closed = True
                self._failure_reason = reason
                self._generation += 1
            queued = list(self._queue)
            self._queue.clear()
            self._pending -= sum(item.samples for item in queued)
            waiting = queued + ([self._inflight] if self._inflight is not None else [])
            self._condition.notify_all()
        for item in waiting:
            if item.pcm is not None and item is not self._inflight:
                item.pcm.fill(0)
            if not item.result.done():
                item.result.set_exception(TseModelError(self._failure_reason or reason))
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(TseModelError(self._failure_reason or reason))
        if self._thread is None:
            self._embedding.fill(0)
            self._stopped_event.set()

    async def _submit(self, pcm: np.ndarray | None, start_sample: int) -> list[TseAudioChunk]:
        self._bind_loop()
        if self._closed or self._finishing:
            if pcm is not None:
                pcm.fill(0)
            raise TseModelError(self._failure_reason or "TSE worker is closed or flushing")
        if self._ready is None or not self._ready.done():
            if pcm is not None:
                pcm.fill(0)
            raise TseModelError("TSE worker must finish start() before receiving PCM")
        if type(start_sample) is not int or start_sample != self._next_input:
            if pcm is not None:
                pcm.fill(0)
            self._retire("tse_input_discontinuity")
            raise TseModelError("TSE input gap or overlap")
        count = 0 if pcm is None else pcm.size
        if pcm is not None and count == 0:
            return []
        with self._condition:
            if self._pending + count > self._capacity:
                overflow = True
            else:
                overflow = False
                item = _WorkItem(self._generation, pcm, start_sample, time.monotonic(), self._loop.create_future())
                self._pending += count
                self._queue.append(item)
                self._next_input += count
                self._finishing = pcm is None
                self._condition.notify()
        if overflow:
            if pcm is not None:
                pcm.fill(0)
            self._retire("tse_queue_full")
            raise TseModelError("TSE pending audio exceeds budget")
        try:
            result = await asyncio.wait_for(asyncio.shield(item.result), self._max_age)
            if self._closed or item.generation != self._generation:
                raise TseModelError("TSE result belongs to a retired generation")
            return result
        except BaseException:
            self._retire("tse_operation_interrupted")
            # A shielded future must be consumed even if its caller was cancelled.
            if item.result.done() and not item.result.cancelled():
                item.result.exception()
            raise

    async def push(self, pcm: np.ndarray, *, start_sample: int) -> list[TseAudioChunk]:
        self._bind_loop()
        if np.asarray(pcm).size > self._capacity:
            self._retire("tse_queue_full")
            raise TseModelError("TSE input block exceeds pending audio budget")
        samples = pcm_float32(pcm).copy()
        return await self._submit(samples, start_sample)

    async def flush(self) -> list[TseAudioChunk]:
        return await self._submit(None, self._next_input)

    async def close(self, *, timeout: float = 1.0) -> bool:
        self._bind_loop()
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("invalid TSE close timeout")
        self._retire("tse_closed")
        deadline = time.monotonic() + timeout
        while not self.stopped and time.monotonic() < deadline:
            await asyncio.sleep(min(0.01, max(0, deadline - time.monotonic())))
        if self.stopped and self._thread is not None:
            self._thread.join(0)
        return self.stopped


async def extract_extraction_reference(asset_dir: Path, reference_pcm: Sequence[np.ndarray], *,
                                       timeout: float = 30) -> SpeakerExtractionReference:
    """Cancel-safe, process-owned extraction of exactly three three-second clips.

    Returned reference carries the raw TSE encoder identity. The caller still
    must fence enrollment/profile/model generations before committing it.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid TSE enrollment timeout")
    if len(reference_pcm) != 3:
        raise ValueError("TSE requires three enrollment reference clips")
    recordings = []
    try:
        for pcm in reference_pcm:
            if np.asarray(pcm).shape != (3 * SAMPLE_RATE,):
                raise ValueError("TSE reference clips must each be exactly three seconds")
            validated = pcm_float32(pcm)
            if validated.size != 3 * SAMPLE_RATE:
                raise ValueError("TSE reference clips must each be exactly three seconds")
            recordings.append(validated.copy())
    except BaseException:
        for pcm in recordings:
            pcm.fill(0)
        raise
    deadline = asyncio.get_running_loop().time() + timeout
    startup = asyncio.create_task(asyncio.to_thread(_start_encoder_process, Path(asset_dir), recordings))
    embedding = reference = None
    try:
        process, receiver = await asyncio.wait_for(asyncio.shield(startup), timeout)
        while not receiver.poll():
            if not process.is_alive():
                raise TseModelError("TSE encoder process exited without a reference")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("TSE enrollment timed out")
            await asyncio.sleep(0.01)
        valid, result = receiver.recv()
        if not valid:
            raise TseModelError("TSE enrollment failed")
        embedding = result
        reference = SpeakerExtractionReference(TSE_ENCODER_IDENTITY, embedding)
    finally:
        if embedding is not None:
            embedding.fill(0)
        retirement = asyncio.create_task(_finish_encoder_process(startup))
        interrupted = False
        try:
            while True:
                try:
                    await asyncio.shield(retirement)
                    break
                except asyncio.CancelledError:
                    interrupted = True
                    if retirement.cancelled():
                        raise
        except BaseException:
            if reference is not None:
                reference.close()
            raise
        finally:
            for pcm in recordings:
                pcm.fill(0)
        if interrupted:
            if reference is not None:
                reference.close()
            raise asyncio.CancelledError
    return reference


def _start_encoder_process(directory: Path, recordings: Sequence[np.ndarray]):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = None
    try:
        process = context.Process(target=_encoder_process, args=(sender, str(directory), recordings),
                                  name="neko-tse-enrollment", daemon=True)
        process.start()
        sender.close()
        return process, receiver
    except BaseException:
        sender.close()
        if process is not None:
            if process.pid is not None:
                try:
                    _stop_encoder_process(process)
                except Exception as exc:
                    # Startup ran in a thread; the async owner installs its
                    # retirement task after recovering these still-live handles.
                    raise _EncoderStartupRetirementPending(process, receiver) from exc
            else:
                process.close()
        receiver.close()
        raise


def _encoder_process(sender, directory: str, recordings: Sequence[np.ndarray]) -> None:
    embedding = None
    try:
        with TseEncoder(Path(directory)) as encoder:
            embedding = encoder.encode_references(recordings)
        sender.send((True, embedding))
    except Exception:
        sender.send((False, "tse_encoder_failed"))
    finally:
        if embedding is not None:
            embedding.fill(0)
        for pcm in recordings:
            pcm.fill(0)
        sender.close()


async def _finish_encoder_process(startup: asyncio.Task) -> None:
    try:
        process, receiver = await startup
    except _EncoderStartupRetirementPending as exc:
        raise TseEncoderRetirementError(_EncoderRetirementOwner(exc.process, exc.receiver)) from exc
    except Exception:
        return
    try:
        await asyncio.to_thread(_stop_encoder_process, process)
    except Exception as exc:
        raise TseEncoderRetirementError(_EncoderRetirementOwner(process, receiver)) from exc
    else:
        receiver.close()


def _stop_encoder_process(process) -> None:
    if process.is_alive():
        process.terminate()
    process.join(0.5)
    if process.is_alive():
        process.kill()
        process.join(0.5)
    if process.is_alive():
        raise TseModelError("TSE encoder process did not retire")
    process.close()
