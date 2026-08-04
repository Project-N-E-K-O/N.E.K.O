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

Buffers provider-neutral 16 kHz mono PCM16 and transcribes on manual commit.
Endpointing / Smart Turn remain the caller's responsibility.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest
from ._shared import MAX_SEGMENT_PCM_BYTES, PCM16_SAMPLE_RATE_HZ, PCM16_SAMPLE_WIDTH_BYTES

_UtteranceKey = tuple[int, int, int]

# CPU stays light to avoid ingress backpressure. CUDA uses "medium" for
# usable Mandarin accuracy (small/base are too lossy → 已读乱发).
# Override with NEKO_WHISPER_MODEL anytime (large-v3 if VRAM allows).
_DEFAULT_MODEL_CPU = "base"
_DEFAULT_MODEL_CUDA = "medium"
_DEFAULT_LANGUAGE = "zh"
_ZH_INITIAL_PROMPT = "以下是普通话的句子。"
_MODEL_LOCK = threading.Lock()
_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}
_CUDA_PATH_PREPARED = False
_MIN_UTTERANCE_SAMPLES = int(0.35 * PCM16_SAMPLE_RATE_HZ)
_NOISE_ONLY_RE = re.compile(
    r"^(["
    r"\s\.\,\!\?？！。，、…~～\-—_·•\*\#]+|"
    r"谢谢(大家|观看|收看)?|請?不?要忘记?点?赞|"
    r"字幕(by|由)?.*|thanks for watching|"
    r"thank you\.?|you$|oh+|ah+|嗯+|啊+|呃+|唔+)$",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)


class _TranscribeModel(Protocol):
    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Any, Any]: ...


ModelFactory = Callable[[], _TranscribeModel]


def _env(name: str, default: str) -> str:
    return str(os.getenv(name, default) or default).strip() or default


def _prepare_cuda_dll_path() -> str | None:
    """Expose cuBLAS DLLs so ctranslate2 can actually run on GPU."""

    global _CUDA_PATH_PREPARED
    if _CUDA_PATH_PREPARED:
        return os.environ.get("NEKO_CUDA_DLL_DIR_RESOLVED")
    _CUDA_PATH_PREPARED = True

    try:
        root = Path(__file__).resolve().parents[3]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from scripts.prepare_cuda_asr_path import prepare_cuda_asr_path

        found = prepare_cuda_asr_path()
    except Exception:
        found = None
    if found:
        os.environ["NEKO_CUDA_DLL_DIR_RESOLVED"] = found
        logger.info("ASR CUDA DLL path ready: %s", found)
    return found


def _resolve_device(requested: str) -> tuple[str, str]:
    """Return (device, compute_type) for WhisperModel."""

    choice = (requested or "auto").strip().lower()
    if choice in {"cuda", "gpu", "auto"}:
        _prepare_cuda_dll_path()
    explicit_compute = str(os.getenv("NEKO_WHISPER_COMPUTE", "") or "").strip()
    if choice in {"cuda", "gpu"}:
        return "cuda", (explicit_compute or "float16")
    if choice == "cpu":
        return "cpu", (explicit_compute or "int8")
    # auto
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", (explicit_compute or "float16")
    except Exception:
        pass
    return "cpu", (explicit_compute or "int8")


def _resolve_language(config: AsrSessionConfig) -> str | None:
    env_lang = _env("NEKO_WHISPER_LANGUAGE", _DEFAULT_LANGUAGE).lower()
    if env_lang in {"", "auto"}:
        hint = str(config.language or "auto").strip().lower()
    else:
        hint = env_lang
    if hint in {"", "auto"}:
        return None
    if hint in {"zh", "zh-cn", "zh_cn", "chinese"}:
        return "zh"
    if hint in {"en", "en-us", "en_us", "english"}:
        return "en"
    # Pass through other ISO-ish codes Whisper understands.
    return hint.split("-", 1)[0]


def _pcm16_to_float32(pcm16: bytes) -> np.ndarray:
    if len(pcm16) % PCM16_SAMPLE_WIDTH_BYTES:
        raise ValueError("PCM16LE data has an odd byte length")
    audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
    audio *= 1.0 / 32768.0
    return audio


def _probe_model(model: _TranscribeModel) -> None:
    """Run one tiny encode so missing CUDA runtimes fail at warm time."""

    audio = np.zeros(PCM16_SAMPLE_RATE_HZ, dtype=np.float32)
    segments, _info = model.transcribe(
        audio,
        language="zh",
        task="transcribe",
        vad_filter=False,
        beam_size=1,
    )
    # Materialize the generator: encode happens lazily inside it.
    for _segment in segments:
        break


def _resolve_model_size(device: str) -> str:
    """Pick Whisper size: explicit env wins, else CUDA=small / CPU=base."""

    explicit = str(os.getenv("NEKO_WHISPER_MODEL", "") or "").strip()
    if explicit:
        return explicit
    return _DEFAULT_MODEL_CUDA if device == "cuda" else _DEFAULT_MODEL_CPU


def _default_model_factory() -> _TranscribeModel:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "ASR_LOCAL_DEPENDENCY_MISSING: pip install faster-whisper"
        ) from exc

    preferred_device, preferred_compute = _resolve_device(
        _env("NEKO_WHISPER_DEVICE", "auto")
    )
    # Probe order: preferred CUDA compute → int8_float16 (VRAM-tight) → CPU.
    # ctranslate2 may report CUDA even when cublas is missing; first encode fails.
    candidates: list[tuple[str, str]] = [(preferred_device, preferred_compute)]
    if preferred_device == "cuda":
        if preferred_compute != "int8_float16":
            candidates.append(("cuda", "int8_float16"))
        candidates.append(("cpu", "int8"))

    with _MODEL_LOCK:
        last_error: BaseException | None = None
        for candidate_device, candidate_compute in candidates:
            model_size = _resolve_model_size(candidate_device)
            cache_key = (model_size, candidate_device, candidate_compute)
            cached = _MODEL_CACHE.get(cache_key)
            if cached is not None:
                return cached
            try:
                model = WhisperModel(
                    model_size,
                    device=candidate_device,
                    compute_type=candidate_compute,
                )
                _probe_model(model)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "faster-whisper load failed model=%s device=%s compute=%s: %s",
                    model_size,
                    candidate_device,
                    candidate_compute,
                    exc,
                )
                if candidate_device == "cuda":
                    continue
                raise RuntimeError(
                    f"ASR_LOCAL_MODEL_LOAD_FAILED: failed to load faster-whisper "
                    f"model={model_size!r} device={candidate_device}: {exc}"
                ) from exc
            logger.info(
                "faster-whisper ready model=%s device=%s compute=%s",
                model_size,
                candidate_device,
                candidate_compute,
            )
            _MODEL_CACHE[cache_key] = model
            return model
        raise RuntimeError(
            f"ASR_LOCAL_MODEL_LOAD_FAILED: failed to load faster-whisper: {last_error}"
        ) from last_error


def _is_noise_only_transcript(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return True
    if len(cleaned) <= 1:
        return True
    return bool(_NOISE_ONLY_RE.match(cleaned))


def _beam_size() -> int:
    raw = str(os.getenv("NEKO_WHISPER_BEAM_SIZE", "") or "").strip()
    try:
        value = int(raw) if raw else 5
    except ValueError:
        value = 5
    return max(1, min(value, 10))


def _run_transcribe(
    model: _TranscribeModel,
    pcm16: bytes,
    language: str | None,
) -> str:
    audio = _pcm16_to_float32(pcm16)
    if audio.size == 0:
        return ""
    # Too-short buffers are almost never usable speech; skip the GPU round-trip.
    if audio.size < _MIN_UTTERANCE_SAMPLES:
        return ""
    # Energy gate: ignore near-silence / fan noise before Whisper runs.
    if float(np.sqrt(np.mean(np.square(audio)))) < 0.008:
        return ""
    # Silero/Smart Turn already cut the utterance. Running Whisper's own VAD
    # again chops Mandarin syllables and yields 已读乱发; keep it off by default.
    vad_filter = _env("NEKO_WHISPER_VAD_FILTER", "0").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    beam = _beam_size()
    initial_prompt = None
    if language in {None, "zh"}:
        initial_prompt = _env("NEKO_WHISPER_INITIAL_PROMPT", _ZH_INITIAL_PROMPT)
        if not initial_prompt.strip():
            initial_prompt = None
    segments, _info = model.transcribe(
        audio,
        language=language,
        task="transcribe",
        vad_filter=vad_filter,
        beam_size=beam,
        best_of=beam,
        temperature=0.0,
        condition_on_previous_text=False,
        without_timestamps=True,
        initial_prompt=initial_prompt,
        no_speech_threshold=0.5,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        hallucination_silence_threshold=2.0,
    )
    parts: list[str] = []
    for segment in segments:
        text = str(getattr(segment, "text", "") or "").strip()
        if text and not _is_noise_only_transcript(text):
            parts.append(text)
    text = "".join(parts).strip()
    if _is_noise_only_transcript(text):
        return ""
    return text


async def faster_whisper_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
    *,
    model_factory: ModelFactory | None = None,
) -> None:
    """Buffer 16 kHz PCM and transcribe each manually committed utterance locally."""

    _ = api_key
    last_generation = 0
    current_generation = 0
    current_buffer_epoch = 0
    request_task: asyncio.Task[_AsrWorkerRequest] | None = None
    pending: dict[asyncio.Task[_AsrWorkerEvent], _UtteranceKey] = {}
    buffers: dict[_UtteranceKey, bytearray] = {}
    committed: set[_UtteranceKey] = set()
    failure_sent = False
    factory = model_factory or _default_model_factory
    model: _TranscribeModel | None = None
    language = _resolve_language(config)
    warm_task: asyncio.Task[_TranscribeModel | None] | None = None

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

    async def ensure_model() -> _TranscribeModel | None:
        nonlocal model
        if model is not None:
            return model
        try:
            model = await asyncio.to_thread(factory)
            return model
        except RuntimeError as exc:
            message = str(exc)
            code = (
                "ASR_LOCAL_DEPENDENCY_MISSING"
                if "ASR_LOCAL_DEPENDENCY_MISSING" in message
                else "ASR_LOCAL_MODEL_LOAD_FAILED"
            )
            await emit_error(code, message)
            return None
        except Exception as exc:
            await emit_error(
                "ASR_LOCAL_MODEL_LOAD_FAILED",
                f"failed to load faster-whisper model: {exc}",
            )
            return None

    async def transcribe_utterance(
        key: _UtteranceKey,
        pcm16: bytes,
    ) -> _AsrWorkerEvent:
        generation, buffer_epoch, utterance_id = key
        loaded = await ensure_model()
        if loaded is None:
            raise RuntimeError("ASR_LOCAL_MODEL_LOAD_FAILED")
        try:
            text = await asyncio.to_thread(_run_transcribe, loaded, pcm16, language)
        except Exception as exc:
            raise RuntimeError(f"ASR_LOCAL_TRANSCRIBE_FAILED: {exc}") from exc
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

        # Fail fast only on a missing package. Do NOT block ready on model
        # download/load — IndependentAsrRuntime gives ~10s for ready, and the
        # first HuggingFace pull of "small" routinely exceeds that.
        if model_factory is None:
            try:
                import faster_whisper as _faster_whisper_pkg  # noqa: F401
            except ImportError:
                await emit_error(
                    "ASR_LOCAL_DEPENDENCY_MISSING",
                    "ASR_LOCAL_DEPENDENCY_MISSING: pip install faster-whisper",
                )
                return

        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        # Warm in the background so the first commit is less likely to stall.
        warm_task = asyncio.create_task(
            ensure_model(),
            name="faster-whisper-warm",
        )
        request_task = asyncio.create_task(
            request_queue.get(),  # noqa: ASYNC_BLOCK - this is an asyncio.Queue.
            name="faster-whisper-asr-request",
        )

        while True:
            done, _ = await asyncio.wait(
                {request_task, *pending},
                return_when=asyncio.FIRST_COMPLETED,
            )

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
                                pass
                            else:
                                pcm16 = buffers.pop(key, None)
                                if pcm16:
                                    committed.add(key)
                                    task = asyncio.create_task(
                                        transcribe_utterance(key, bytes(pcm16)),
                                        name="faster-whisper-asr-transcribe",
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
                except RuntimeError as exc:
                    if key[:2] != (current_generation, current_buffer_epoch):
                        continue
                    message = str(exc)
                    code = message.split(":", 1)[0].strip() or "ASR_LOCAL_TRANSCRIBE_FAILED"
                    await emit_error(code, message, item_key=key)
                    return
                except Exception as exc:
                    if key[:2] != (current_generation, current_buffer_epoch):
                        continue
                    await emit_error(
                        "ASR_LOCAL_TRANSCRIBE_FAILED",
                        f"faster-whisper transcription failed: {exc}",
                        item_key=key,
                    )
                    return
                if key[:2] == (current_generation, current_buffer_epoch):
                    await response_queue.put(event)
    except asyncio.CancelledError:
        raise
    except Exception:
        await emit_error(
            "ASR_LOCAL_WORKER_FAILED",
            "faster-whisper transcription worker failed",
        )
    finally:
        if warm_task is not None and not warm_task.done():
            warm_task.cancel()
            await asyncio.gather(warm_task, return_exceptions=True)
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
        await response_queue.put(
            _AsrWorkerEvent(kind="closed", generation=last_generation)
        )


# Keep sample-rate constant discoverable for tests / docs.
WORKER_INPUT_SAMPLE_RATE_HZ = PCM16_SAMPLE_RATE_HZ
