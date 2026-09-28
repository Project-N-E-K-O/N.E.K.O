# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Local faster-whisper segmented ASR worker.

The worker receives provider-neutral 16 kHz mono PCM16 chunks, buffers them by
session/epoch/utterance identity, and transcribes one utterance per manual
commit on the local machine. Endpoint selection (VAD / Smart Turn) remains the
caller's responsibility, exactly as for the cloud segmented workers.

Blocking work never runs on the event loop: importing ``faster_whisper`` (which
pulls in ctranslate2, av and tokenizers), loading or downloading model weights,
and decoding all happen in worker threads.

Model lifetime: loaded models live in a process-wide pool keyed by the
requested model spec. Every running worker holds one lease. When the last lease
is released the model is kept for ``_MODEL_IDLE_RELEASE_SECONDS`` so that the
per-turn reconnects done by resource optimization reuse it, then it is dropped
from a timer thread. ``release_idle_faster_whisper_models`` drops idle models
immediately.

Weights are fetched by faster-whisper through huggingface_hub on first use,
which honors ``HF_ENDPOINT`` / ``HF_HOME``; this module never pins a mirror.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import importlib
import logging
import os
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from config.prompts.prompts_voice import WHISPER_SILENCE_HALLUCINATIONS

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest
from ..delivery import (
    begin_transport_write,
    complete_transport_write,
    delivery_evidence,
)
from ..warmup import begin_provider_warmup, complete_provider_warmup
from ._shared import MAX_SEGMENT_PCM_BYTES, PCM16_SAMPLE_WIDTH_BYTES


logger = logging.getLogger(__name__)

PROVIDER_KEY = "faster_whisper"

# Optional overrides for advanced users. Model may be a size name
# ("base", "small", "medium", "large-v3", ...) or a local CTranslate2 model dir.
_MODEL_ENV = "NEKO_WHISPER_MODEL"
_DEVICE_ENV = "NEKO_WHISPER_DEVICE"
_COMPUTE_ENV = "NEKO_WHISPER_COMPUTE"

# CPU stays light so decoding keeps up with speech; CUDA can afford a model
# that is noticeably more accurate on Mandarin and other non-English speech.
_DEFAULT_MODEL_CPU = "base"
_DEFAULT_MODEL_CUDA = "medium"
_DEFAULT_COMPUTE_CPU = "int8"
_DEFAULT_COMPUTE_CUDA = "float16"
_FALLBACK_COMPUTE_CUDA = "int8_float16"

_BEAM_SIZE = 5
_MODEL_IDLE_RELEASE_SECONDS = 300.0
# Committed utterances allowed to wait for (or be in) decoding per session.
# Decoding is serialized, so a sender streaming faster than the model decodes
# would otherwise pile up tasks and PCM without bound; past this the session
# fails with ASR_LOCAL_DECODE_BACKLOG instead of lagging further behind.
_MAX_PENDING_DECODES = 3
# Decodes queued or running across ALL sessions. The single decode thread
# drains one at a time, and its executor queue itself is unbounded, so many
# open sessions could otherwise keep PCM queued without limit.
_MAX_PROCESS_DECODES = 4

# Whisper's own silence heuristics already drop the obvious cases; these gates
# only decide whether an exact known hallucination phrase is trusted.
_HALLUCINATION_MIN_NO_SPEECH_PROB = 0.3
_HALLUCINATION_MAX_AVG_LOGPROB = -0.8
_HALLUCINATION_EDGE_CHARS = (
    " \t\r\n.,!?;:'\"`"
    "。，！？；：、…~～"
    "-—()（）[]【】「」『』《》"
)

# Whisper tokenizer language codes (multilingual checkpoints up to large-v2).
# ``yue`` is intentionally absent: only large-v3 accepts it.
_WHISPER_LANGUAGE_CODES = frozenset({
    "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo", "br", "bs",
    "ca", "cs", "cy", "da", "de", "el", "en", "es", "et", "eu", "fa", "fi",
    "fo", "fr", "gl", "gu", "ha", "haw", "he", "hi", "hr", "ht", "hu", "hy",
    "id", "is", "it", "ja", "jw", "ka", "kk", "km", "kn", "ko", "la", "lb",
    "ln", "lo", "lt", "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt",
    "my", "ne", "nl", "nn", "no", "oc", "pa", "pl", "ps", "pt", "ro", "ru",
    "sa", "sd", "si", "sk", "sl", "sn", "so", "sq", "sr", "su", "sv", "sw",
    "ta", "te", "tg", "th", "tk", "tl", "tr", "tt", "uk", "ur", "uz", "vi",
    "yi", "yo", "zh",
})
_WHISPER_LANGUAGE_ALIASES = {"nb": "no", "iw": "he", "jv": "jw"}

_UtteranceKey = tuple[int, int, int]


class _TranscribeModel(Protocol):
    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Iterable[Any], Any]: ...


@dataclass(frozen=True, slots=True)
class _ModelSpec:
    """What the user asked for; the pool keys loaded models by this value."""

    model: str | None
    device: str
    compute_type: str | None


ModelLoader = Callable[[_ModelSpec], _TranscribeModel]


class _LocalAsrFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _whisper_language_code(language: str) -> str | None:
    """Map a normalized session language onto a Whisper language code.

    ``auto`` maps to ``None`` (Whisper detects the language itself). Unknown
    languages raise ``ValueError`` so the session factory can fall back to
    automatic detection instead of failing the session.
    """

    normalized = str(language or "").strip().lower()
    if not normalized or normalized == "auto":
        return None
    base = normalized.replace("_", "-").split("-", 1)[0]
    base = _WHISPER_LANGUAGE_ALIASES.get(base, base)
    if base in _WHISPER_LANGUAGE_CODES:
        return base
    raise ValueError(
        "ASR_LANGUAGE_NOT_SUPPORTED: faster-whisper language is unsupported"
    )


def _session_language(config: AsrSessionConfig) -> str | None:
    try:
        return _whisper_language_code(config.language)
    except ValueError:
        return None


def _model_spec_from_env() -> _ModelSpec:
    model = str(os.getenv(_MODEL_ENV, "") or "").strip() or None
    device = str(os.getenv(_DEVICE_ENV, "") or "").strip().lower() or "auto"
    if device == "gpu":
        device = "cuda"
    if device not in {"auto", "cpu", "cuda"}:
        device = "auto"
    compute_type = str(os.getenv(_COMPUTE_ENV, "") or "").strip() or None
    return _ModelSpec(model=model, device=device, compute_type=compute_type)


def _import_faster_whisper() -> Any:
    """Import the optional package; call only from a worker thread."""

    try:
        return importlib.import_module("faster_whisper")
    except ImportError as exc:
        raise _LocalAsrFailure(
            "ASR_LOCAL_DEPENDENCY_MISSING",
            "faster-whisper is not installed",
        ) from exc


def _cuda_device_count() -> int:
    """Count CUDA devices visible to ctranslate2; call only from a thread."""

    try:
        ctranslate2 = importlib.import_module("ctranslate2")
        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def _device_candidates(spec: _ModelSpec) -> list[tuple[str, str]]:
    """Ordered (device, compute_type) attempts; CUDA always ends on CPU."""

    if spec.device == "cpu":
        return [("cpu", spec.compute_type or _DEFAULT_COMPUTE_CPU)]
    if spec.device == "auto" and _cuda_device_count() <= 0:
        # No GPU: the explicit compute type is the user's choice for the only
        # device left, so honor it just like an explicit "cpu" device.
        return [("cpu", spec.compute_type or _DEFAULT_COMPUTE_CPU)]
    candidates = [("cuda", spec.compute_type or _DEFAULT_COMPUTE_CUDA)]
    if spec.compute_type is None:
        # Less VRAM, same device: useful on small GPUs before giving up.
        candidates.append(("cuda", _FALLBACK_COMPUTE_CUDA))
    candidates.append(("cpu", _DEFAULT_COMPUTE_CPU))
    return candidates


def _probe_model(model: _TranscribeModel) -> None:
    """Force one encode so missing CUDA runtime libraries fail at load time.

    ctranslate2 may report a CUDA device and construct the model even when
    cuBLAS/cuDNN are missing; the first encode is what actually fails.
    """

    silence = np.zeros(16_000, dtype=np.float32)
    segments, _info = model.transcribe(
        silence,
        beam_size=1,
        vad_filter=False,
        without_timestamps=True,
    )
    for _segment in segments:
        break


def _load_whisper_model(spec: _ModelSpec) -> _TranscribeModel:
    """Load one model, falling back from CUDA to CPU; blocking."""

    module = _import_faster_whisper()
    whisper_model_cls = module.WhisperModel
    last_error: BaseException | None = None
    for device, compute_type in _device_candidates(spec):
        model_name = spec.model or (
            _DEFAULT_MODEL_CUDA if device == "cuda" else _DEFAULT_MODEL_CPU
        )
        model = None
        try:
            model = whisper_model_cls(
                model_name,
                device=device,
                compute_type=compute_type,
            )
            if device == "cuda":
                _probe_model(model)
        except Exception as exc:
            # Drop a model that loaded but failed its probe before building the
            # next candidate: its VRAM would otherwise make the lower-memory
            # retry on the same GPU fail too.
            model = None
            # Keep the error but not its traceback: the probe's frame in it
            # still references the failed model and would pin its VRAM.
            last_error = exc.with_traceback(None)
            logger.warning(
                "faster-whisper load failed model=%s device=%s compute=%s: %s",
                model_name,
                device,
                compute_type,
                exc,
            )
            continue
        logger.info(
            "faster-whisper ready model=%s device=%s compute=%s",
            model_name,
            device,
            compute_type,
        )
        return model
    raise _LocalAsrFailure(
        "ASR_LOCAL_MODEL_LOAD_FAILED",
        "faster-whisper model could not be loaded",
    ) from last_error


@dataclass(slots=True)
class _PoolEntry:
    model: _TranscribeModel | None
    leases: int = 0
    expiry: threading.Timer | None = field(default=None, repr=False)


class _WhisperModelPool:
    """Share loaded models across workers and drop them after idling.

    ``acquire`` blocks (it may import and load) and must run in a thread.
    ``release`` only touches a short critical section, so it is safe to call
    from the event loop.
    """

    def __init__(self, *, idle_release_seconds: float) -> None:
        self._idle_release_seconds = idle_release_seconds
        self._state_lock = threading.Lock()
        self._load_locks: dict[_ModelSpec, threading.Lock] = {}
        self._entries: dict[_ModelSpec, _PoolEntry] = {}
        # One in-flight load per spec, run on the pool's own single thread:
        # sessions that start and end while a model downloads share that one
        # load instead of each parking a thread of the default executor.
        self._inflight: dict[_ModelSpec, concurrent.futures.Future[None]] = {}
        self._loader_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._decoder_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._decode_slots_used = 0

    def try_reserve_decode(self) -> bool:
        """Take one process-wide decode slot, or return False when all are used."""
        with self._state_lock:
            if self._decode_slots_used >= _MAX_PROCESS_DECODES:
                return False
            self._decode_slots_used += 1
            return True

    def release_decode(self, _task: object = None) -> None:
        """Give back a slot taken by ``try_reserve_decode`` (usable as a callback)."""
        with self._state_lock:
            if self._decode_slots_used > 0:
                self._decode_slots_used -= 1

    def decoder_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        """The single thread every local decode runs on, process-wide.

        A session's decode cannot be interrupted once started, and a session
        can end and a new one start while it runs. Bounding decodes per
        session would let such churn stack native decodes; one shared thread
        bounds them for the whole process, and queued work of a cancelled
        session is dropped before it starts.
        """
        with self._state_lock:
            if self._decoder_executor is None:
                self._decoder_executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="faster-whisper-decode",
                )
            return self._decoder_executor

    def try_lease(self, spec: _ModelSpec) -> _TranscribeModel | None:
        """Lease an already loaded model without blocking, or return None."""
        with self._state_lock:
            entry = self._entries.get(spec)
            if entry is None or entry.model is None:
                return None
            entry.leases += 1
            if entry.expiry is not None:
                entry.expiry.cancel()
                entry.expiry = None
            return entry.model

    def ensure_loading(
        self, spec: _ModelSpec, loader: ModelLoader
    ) -> concurrent.futures.Future[None]:
        """Start (or join) the load of ``spec``; the future carries no lease."""
        with self._state_lock:
            future = self._inflight.get(spec)
            if future is not None:
                return future
            if self._loader_executor is None:
                self._loader_executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="faster-whisper-load",
                )
            future = self._loader_executor.submit(self._load_unleased, spec, loader)
            self._inflight[spec] = future
        future.add_done_callback(functools.partial(self._forget_inflight, spec))
        return future

    def _forget_inflight(
        self, spec: _ModelSpec, future: concurrent.futures.Future[None]
    ) -> None:
        with self._state_lock:
            if self._inflight.get(spec) is future:
                del self._inflight[spec]

    def _load_unleased(self, spec: _ModelSpec, loader: ModelLoader) -> None:
        # Loaded with no lease and an idle timer running: if every session
        # that wanted it has gone, the model is still reclaimed.
        self.acquire(spec, loader)
        self.release(spec)

    def acquire(self, spec: _ModelSpec, loader: ModelLoader) -> _TranscribeModel:
        with self._state_lock:
            load_lock = self._load_locks.setdefault(spec, threading.Lock())
        # The per-spec load lock serializes slow loads without holding the
        # state lock, so ``release`` never waits behind a download.
        with load_lock:
            with self._state_lock:
                entry = self._entries.get(spec)
                if entry is not None and entry.model is not None:
                    entry.leases += 1
                    if entry.expiry is not None:
                        entry.expiry.cancel()
                        entry.expiry = None
                    return entry.model
            model = loader(spec)
            with self._state_lock:
                self._entries[spec] = _PoolEntry(model=model, leases=1)
            return model

    def release(self, spec: _ModelSpec) -> None:
        with self._state_lock:
            entry = self._entries.get(spec)
            if entry is None or entry.leases <= 0:
                return
            entry.leases -= 1
            if entry.leases:
                return
            timer = threading.Timer(
                self._idle_release_seconds,
                self._expire,
                args=(spec, entry),
            )
            timer.daemon = True
            timer.name = "faster-whisper-idle-release"
            entry.expiry = timer
        timer.start()

    def _expire(self, spec: _ModelSpec, entry: _PoolEntry) -> None:
        with self._state_lock:
            if self._entries.get(spec) is not entry or entry.leases:
                return
            del self._entries[spec]
            model, entry.model = entry.model, None
            entry.expiry = None
        # Drop the last reference here, off the event loop: freeing a
        # CTranslate2 model releases native (possibly GPU) memory.
        del model
        logger.info("faster-whisper model released after idling")

    def release_idle(self) -> int:
        """Drop every model without active leases now; blocking."""

        dropped: list[_TranscribeModel | None] = []
        with self._state_lock:
            for spec, entry in list(self._entries.items()):
                if entry.leases:
                    continue
                if entry.expiry is not None:
                    entry.expiry.cancel()
                    entry.expiry = None
                del self._entries[spec]
                dropped.append(entry.model)
                entry.model = None
        count = len(dropped)
        dropped.clear()
        return count

    def loaded_count(self) -> int:
        with self._state_lock:
            return len(self._entries)

    def lease_count(self, spec: _ModelSpec) -> int:
        with self._state_lock:
            entry = self._entries.get(spec)
            return entry.leases if entry is not None else 0


_MODEL_POOL = _WhisperModelPool(idle_release_seconds=_MODEL_IDLE_RELEASE_SECONDS)


async def release_idle_faster_whisper_models() -> int:
    """Drop cached local ASR models that no running session is using."""

    return await asyncio.to_thread(_MODEL_POOL.release_idle)


def _normalize_hallucination_candidate(text: str) -> str:
    collapsed = " ".join(str(text or "").split()).casefold()
    return collapsed.strip(_HALLUCINATION_EDGE_CHARS)


def _is_silence_hallucination(text: str, segments: list[Any]) -> bool:
    """Return whether a whole transcript is a known low-confidence hallucination.

    Both conditions are required: the complete transcript must equal a known
    phrase, and Whisper itself must have signalled weak evidence of speech.
    A confidently recognized "thank you" is kept.
    """

    if _normalize_hallucination_candidate(text) not in WHISPER_SILENCE_HALLUCINATIONS:
        return False
    no_speech = [
        float(value)
        for value in (getattr(segment, "no_speech_prob", None) for segment in segments)
        if isinstance(value, (int, float))
    ]
    avg_logprob = [
        float(value)
        for value in (getattr(segment, "avg_logprob", None) for segment in segments)
        if isinstance(value, (int, float))
    ]
    if no_speech and max(no_speech) >= _HALLUCINATION_MIN_NO_SPEECH_PROB:
        return True
    if avg_logprob and (
        sum(avg_logprob) / len(avg_logprob)
    ) <= _HALLUCINATION_MAX_AVG_LOGPROB:
        return True
    return False


def _transcribe_pcm16(
    model: _TranscribeModel,
    pcm16: bytes,
    language: str | None,
) -> str:
    """Decode one utterance; blocking, run it in a worker thread."""

    if len(pcm16) % PCM16_SAMPLE_WIDTH_BYTES:
        raise ValueError("PCM16LE data has an odd byte length")
    audio = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
    if audio.size == 0:
        return ""
    segments_iter, _info = model.transcribe(
        audio,
        language=language,
        task="transcribe",
        beam_size=_BEAM_SIZE,
        # Smart Turn already cut the utterance; a second VAD pass clips
        # syllables at the edges.
        vad_filter=False,
        condition_on_previous_text=False,
        without_timestamps=True,
    )
    # The generator performs the actual decoding lazily.
    segments = list(segments_iter)
    # Whisper keeps the leading space of space-delimited languages and emits
    # none for CJK, so plain concatenation preserves both.
    text = "".join(str(getattr(segment, "text", "") or "") for segment in segments)
    text = " ".join(text.split())
    if not text or _is_silence_hallucination(text, segments):
        return ""
    return text


@dataclass
class _DecodeHandoff:
    """Whether a decode job reached the executor (which then owns its slot)."""

    submitted: bool = False


def _return_slot_unless_handed_off(
    pool: "_WhisperModelPool",
    handoff: _DecodeHandoff,
    _task: asyncio.Task[Any],
) -> None:
    if not handoff.submitted:
        pool.release_decode()


def _consume_decode_outcome(future: asyncio.Future[Any]) -> None:
    """Retrieve a decode's result so an abandoned one never logs as unretrieved."""
    if not future.cancelled():
        future.exception()


def _decodes_in_flight(pending: dict[asyncio.Task[Any], Any]) -> int:
    """Decode tasks still waiting for or running on the decoder."""
    return sum(1 for task in pending if not task.done())


async def faster_whisper_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
    *,
    model_loader: ModelLoader | None = None,
    model_pool: _WhisperModelPool | None = None,
) -> None:
    """Buffer normalized PCM and transcribe each committed utterance locally."""

    # Local inference has no credential; the argument exists for the shared
    # worker signature only.
    _ = api_key
    loader = model_loader or _load_whisper_model
    pool = model_pool or _MODEL_POOL
    spec = _model_spec_from_env()
    language = _session_language(config)
    last_generation = 0
    current_generation = 0
    current_buffer_epoch = 0
    request_task: asyncio.Task[_AsrWorkerRequest] | None = None
    model_task: asyncio.Task[_TranscribeModel] | None = None
    pending: dict[asyncio.Task[_AsrWorkerEvent], _UtteranceKey] = {}
    buffers: dict[_UtteranceKey, bytearray] = {}
    committed: set[_UtteranceKey] = set()
    failure_sent = False

    async def emit_error(
        code: str,
        message: str,
        *,
        item_key: _UtteranceKey | None = None,
    ) -> None:
        nonlocal failure_sent
        if failure_sent:
            return
        failure_sent = True
        generation, buffer_epoch, utterance_id = (
            item_key if item_key is not None else (last_generation, 0, None)
        )
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=generation,
                buffer_epoch=buffer_epoch,
                utterance_id=utterance_id,
                error_code=code,
                error_message=message,
            )
        )

    async def cancel_pending(*, keep_current_scope: bool = False) -> None:
        tasks = [
            task
            for task, key in pending.items()
            if not keep_current_scope
            or key[:2] != (current_generation, current_buffer_epoch)
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in tasks:
            pending.pop(task, None)

    async def acquire_model() -> _TranscribeModel:
        # Tell the runtime that model preparation (possibly a first download)
        # is in progress, so its per-utterance final watchdog does not count it.
        begin_provider_warmup(request_queue)
        try:
            while True:
                model = pool.try_lease(spec)
                if model is not None:
                    return model
                # Shared per spec; cancelling this session only stops waiting.
                # No lease is taken until the load is done, so there is none
                # to hand back when the session ends mid-download.
                await asyncio.shield(
                    asyncio.wrap_future(pool.ensure_loading(spec, loader))
                )
        finally:
            complete_provider_warmup(request_queue)

    async def transcribe(
        key: _UtteranceKey,
        pcm16: bytes,
        handoff: _DecodeHandoff,
    ) -> _AsrWorkerEvent:
        # The process-wide decode slot was taken at commit. Once the job is
        # handed to the executor, the decode thread gives it back when the job
        # leaves the queue (run or skipped); before that, the task's done
        # callback does (see _return_slot_unless_handed_off).
        generation, buffer_epoch, utterance_id = key
        assert model_task is not None
        # Shield: cancelling one utterance must not cancel the shared load.
        model = await asyncio.shield(model_task)
        # The decoder actually starting on the PCM is this provider's
        # transport write. Waiting in the decode queue is not: a job
        # cancelled before it starts never reached the model and must stay
        # a definite non-delivery. The decode thread marks the attempt
        # itself, before the model sees the audio, so a reader on the loop
        # never finds a running decode still reported as not attempted.
        # The evidence object is created here, on the loop, so the thread
        # only flips one attribute on it.
        evidence = delivery_evidence(request_queue)
        skip = threading.Event()

        def decode() -> str | None:
            # Reaching the decoder ends this job's wait behind other sessions'
            # decodes; the per-utterance final timeout counts from here.
            complete_provider_warmup(request_queue)
            try:
                if skip.is_set():
                    # Cancelled while queued: drop the PCM without decoding.
                    return None
                begin_transport_write(request_queue)
                return _transcribe_pcm16(model, pcm16, language)
            finally:
                pool.release_decode()

        # Waiting in the process-wide decode queue (behind another session's
        # uninterruptible decode) is not recognition time either: publish it
        # like model preparation so the runtime's final watchdog holds off
        # until the job reaches the decoder, within the warm-up budget.
        begin_provider_warmup(request_queue)
        try:
            decode_future = asyncio.get_running_loop().run_in_executor(
                pool.decoder_executor(), decode
            )
        except BaseException:
            complete_provider_warmup(request_queue)
            raise
        handoff.submitted = True
        decode_future.add_done_callback(_consume_decode_outcome)
        try:
            # Not cancelled through: the executor keeps a cancelled work
            # item (and its PCM) queued until the single thread reaches it,
            # so the slot must stay taken until then. The skip flag makes
            # that dequeue cheap instead.
            text = await asyncio.shield(decode_future)
        except asyncio.CancelledError:
            skip.set()
            raise
        except Exception as exc:
            raise _LocalAsrFailure(
                "ASR_LOCAL_TRANSCRIBE_FAILED",
                "faster-whisper transcription failed",
            ) from exc
        complete_transport_write(
            evidence, len(pcm16), generation=generation,
            buffer_epoch=buffer_epoch, provider=PROVIDER_KEY,
        )
        return _AsrWorkerEvent(
            kind="final",
            generation=generation,
            buffer_epoch=buffer_epoch,
            utterance_id=utterance_id,
            text=text,
        )

    try:
        if config.endpointing_mode != "manual":
            await emit_error(
                "ASR_ENDPOINTING_NOT_SUPPORTED",
                "faster-whisper only supports manual endpointing",
            )
            return

        # Load in the background: the first download can take far longer than
        # the session ready timeout. Commits wait for this task.
        model_task = asyncio.create_task(
            acquire_model(),
            name="faster-whisper-asr-load",
        )
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        request_task = asyncio.create_task(
            request_queue.get(),  # noqa: ASYNC_BLOCK - this is an asyncio.Queue.
            name="faster-whisper-asr-request",
        )

        while True:
            waitables: set[asyncio.Task[Any]] = {request_task, *pending}
            if not model_task.done():
                waitables.add(model_task)
            done, _ = await asyncio.wait(
                waitables,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if model_task in done and model_task.exception() is not None:
                exc = model_task.exception()
                if isinstance(exc, _LocalAsrFailure):
                    await emit_error(exc.code, exc.message)
                else:
                    await emit_error(
                        "ASR_LOCAL_MODEL_LOAD_FAILED",
                        "faster-whisper model could not be loaded",
                    )
                return

            should_stop = False
            if request_task in done:
                completed_request_task = request_task
                request_task = None
                request = completed_request_task.result()
                last_generation = request.generation
                try:
                    if request.kind == "shutdown":
                        current_generation = request.generation
                        current_buffer_epoch = request.buffer_epoch
                        buffers.clear()
                        committed.clear()
                        await cancel_pending()
                        should_stop = True
                    else:
                        stale = False
                        scope_advanced = False
                        if request.generation < current_generation:
                            stale = True
                        elif request.generation > current_generation:
                            current_generation = request.generation
                            current_buffer_epoch = request.buffer_epoch
                            scope_advanced = True
                        elif request.buffer_epoch < current_buffer_epoch:
                            stale = True
                        elif request.buffer_epoch > current_buffer_epoch:
                            current_buffer_epoch = request.buffer_epoch
                            scope_advanced = True

                        if scope_advanced:
                            buffers.clear()
                            committed.clear()
                            await cancel_pending(keep_current_scope=True)

                        if stale:
                            pass
                        elif request.kind == "clear":
                            buffers.clear()
                            committed.clear()
                            await cancel_pending()
                        elif request.utterance_id is None:
                            await emit_error(
                                "ASR_LOCAL_PROTOCOL_ERROR",
                                "faster-whisper worker received a command without an utterance ID",
                            )
                            should_stop = True
                        elif request.kind == "audio":
                            key = (
                                request.generation,
                                request.buffer_epoch,
                                request.utterance_id,
                            )
                            if key in committed or key in pending.values():
                                # Late audio for a committed utterance must not
                                # re-accumulate a second buffer for its key.
                                pass
                            elif len(request.audio) % PCM16_SAMPLE_WIDTH_BYTES:
                                await emit_error(
                                    "ASR_LOCAL_PROTOCOL_ERROR",
                                    "faster-whisper worker received invalid PCM16 audio",
                                )
                                should_stop = True
                            else:
                                buffer = buffers.setdefault(key, bytearray())
                                buffer.extend(request.audio)
                                if len(buffer) > MAX_SEGMENT_PCM_BYTES:
                                    buffers.pop(key, None)
                                    await emit_error(
                                        "ASR_LOCAL_AUDIO_TOO_LONG",
                                        "faster-whisper utterance exceeds the 28 second limit",
                                    )
                                    should_stop = True
                        elif request.kind == "commit":
                            key = (
                                request.generation,
                                request.buffer_epoch,
                                request.utterance_id,
                            )
                            if key in committed or key in pending.values():
                                # A duplicate commit must not decode twice or
                                # emit a second final.
                                pass
                            else:
                                pcm16 = buffers.pop(key, None)
                                # 只数还没完成的：本轮刚解完、下面才出队的不占积压名额。
                                # 名额在拷贝 PCM、建任务之前占：本会话积压上限之外，
                                # 还有一个跨所有会话的进程级上限。
                                if pcm16 and (
                                    _decodes_in_flight(pending) >= _MAX_PENDING_DECODES
                                    or not pool.try_reserve_decode()
                                ):
                                    await emit_error(
                                        "ASR_LOCAL_DECODE_BACKLOG",
                                        "faster-whisper cannot decode as fast as audio is committed",
                                        item_key=key,
                                    )
                                    should_stop = True
                                elif pcm16:
                                    committed.add(key)
                                    handoff = _DecodeHandoff()
                                    task = asyncio.create_task(
                                        transcribe(key, bytes(pcm16), handoff),
                                        name="faster-whisper-asr-transcribe",
                                    )
                                    # A task cancelled before it first runs never
                                    # enters its body, so its own finally cannot
                                    # return the slot; this callback always runs.
                                    task.add_done_callback(
                                        functools.partial(
                                            _return_slot_unless_handed_off,
                                            pool,
                                            handoff,
                                        )
                                    )
                                    pending[task] = key
                        else:
                            await emit_error(
                                "ASR_LOCAL_PROTOCOL_ERROR",
                                "faster-whisper worker received an unsupported command",
                            )
                            should_stop = True
                finally:
                    request_queue.task_done()

                if should_stop:
                    break
                request_task = asyncio.create_task(
                    request_queue.get(),  # noqa: ASYNC_BLOCK - asyncio.Queue.
                    name="faster-whisper-asr-request",
                )

            completed_transcriptions = [task for task in done if task in pending]
            for task in completed_transcriptions:
                key = pending.pop(task)
                try:
                    event = task.result()
                except asyncio.CancelledError:
                    continue
                except _LocalAsrFailure as exc:
                    if key[:2] != (current_generation, current_buffer_epoch):
                        continue
                    await emit_error(exc.code, exc.message, item_key=key)
                    return
                if key[:2] == (current_generation, current_buffer_epoch):
                    await response_queue.put(event)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("faster-whisper worker failed")
        await emit_error(
            "ASR_LOCAL_WORKER_FAILED",
            "faster-whisper transcription worker failed",
        )
    finally:
        if request_task is not None:
            if not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            if not request_task.cancelled():
                try:
                    request_task.result()
                except Exception:
                    pass
                else:
                    request_queue.task_done()
        await cancel_pending()
        buffers.clear()
        if model_task is not None:
            if not model_task.done():
                # acquire_model hands the lease back itself once the thread
                # finishes.
                model_task.cancel()
                await asyncio.gather(model_task, return_exceptions=True)
            elif not model_task.cancelled() and model_task.exception() is None:
                pool.release(spec)
        await response_queue.put(
            _AsrWorkerEvent(kind="closed", generation=last_generation)
        )
