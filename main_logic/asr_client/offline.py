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

"""Offline speech recognition: one complete recording in, one transcript out.

This sits beside the realtime session API and shares its provider selection
(Core route, region preference, user-selected local provider, credentials),
but never touches ``IndependentAsrRuntime``: there is no microphone input
token, endpoint detection, voiceprint or turn token here.

Each provider declares how it transcribes a whole recording in
``AsrProviderMeta.offline`` (see ``_registry_meta.py``):

* ``request``: every segment is one file request. GLM, Gemini and
  faster-whisper call the exact request/decode functions their realtime
  workers use; OpenAI and xAI call their native file-transcription endpoints.
* ``session``: the recording is replayed through a realtime session in manual
  endpointing mode, one commit per segment, so each commit yields exactly one
  final (Qwen, Soniox, and the development ``dummy`` provider).
* ``unsupported``: refused with ``ASR_OFFLINE_NOT_SUPPORTED``.

Recordings longer than the provider's ``max_segment_ms`` are split into
equal segments here and the transcripts joined; callers never size audio for
a provider. Every failure is an :class:`AsrOfflineError` carrying a machine
code, so callers never parse exception text.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
import numpy as np

from . import (
    _CORE_ASR_ROUTES,
    _ASR_PROVIDER_REGISTRY,
    _AsrProviderAvailability,
    _AsrSelection,
    _IMPLEMENTED_WORKERS,
    _create_asr_session_from_selection,
    _optional_dependency_available,
    _resolve_asr_selection,
    _resolve_session_language,
    _resolve_user_selected_provider,
)
from ._infra import AsrSessionConfig, RealtimeAsrSession
from ._registry_meta import AsrOfflineSpec, AsrProviderMeta
from .workers import gemini as _gemini_worker
from .workers._http_transcription import HttpClient, post_wav_transcription
from .workers._shared import (
    PCM16_SAMPLE_RATE_HZ,
    PCM16_SAMPLE_WIDTH_BYTES,
    AsrRequestFailure,
    AsrRequestFailureKind,
    encode_pcm16_wav,
    normalize_zh_en_language,
)
from .workers.faster_whisper import transcribe_pcm16_locally
from .workers.glm import request_glm_transcript


logger = logging.getLogger(__name__)


__all__ = [
    "ASR_OFFLINE_ERROR_CODES",
    "AsrOfflineError",
    "OfflineAsrStatus",
    "OfflineAsrTranscriber",
    "get_offline_asr_status",
    "resolve_offline_transcriber",
    "transcribe_recording",
]


# Machine-readable failure codes. Provider-specific codes (``ASR_GLM_TIMEOUT``
# and friends) are kept on ``AsrOfflineError.provider_code`` for logs only.
ASR_UNKNOWN_CORE: Final = "ASR_UNKNOWN_CORE"
ASR_INVALID_CONFIG: Final = "ASR_INVALID_CONFIG"
ASR_OFFLINE_NOT_SUPPORTED: Final = "ASR_OFFLINE_NOT_SUPPORTED"
ASR_BACKEND_BLOCKED: Final = "ASR_BACKEND_BLOCKED"
ASR_DEPENDENCY_MISSING: Final = "ASR_DEPENDENCY_MISSING"
ASR_CREDENTIALS_MISSING: Final = "ASR_CREDENTIALS_MISSING"
ASR_CREDENTIALS_REJECTED: Final = "ASR_CREDENTIALS_REJECTED"
ASR_OFFLINE_INVALID_AUDIO: Final = "ASR_OFFLINE_INVALID_AUDIO"
ASR_OFFLINE_TIMEOUT: Final = "ASR_OFFLINE_TIMEOUT"
ASR_RATE_LIMITED: Final = "ASR_RATE_LIMITED"
ASR_OFFLINE_PROVIDER_REJECTED: Final = "ASR_OFFLINE_PROVIDER_REJECTED"
ASR_OFFLINE_INVALID_RESPONSE: Final = "ASR_OFFLINE_INVALID_RESPONSE"
ASR_OFFLINE_PROVIDER_FAILED: Final = "ASR_OFFLINE_PROVIDER_FAILED"

ASR_OFFLINE_ERROR_CODES: Final = frozenset(
    {
        ASR_UNKNOWN_CORE,
        ASR_INVALID_CONFIG,
        ASR_OFFLINE_NOT_SUPPORTED,
        ASR_BACKEND_BLOCKED,
        ASR_DEPENDENCY_MISSING,
        ASR_CREDENTIALS_MISSING,
        ASR_CREDENTIALS_REJECTED,
        ASR_OFFLINE_INVALID_AUDIO,
        ASR_OFFLINE_TIMEOUT,
        ASR_RATE_LIMITED,
        ASR_OFFLINE_PROVIDER_REJECTED,
        ASR_OFFLINE_INVALID_RESPONSE,
        ASR_OFFLINE_PROVIDER_FAILED,
    }
)

_FAILURE_KIND_CODES: dict[AsrRequestFailureKind, str] = {
    "auth": ASR_CREDENTIALS_REJECTED,
    "rejected": ASR_OFFLINE_PROVIDER_REJECTED,
    "rate_limited": ASR_RATE_LIMITED,
    "timeout": ASR_OFFLINE_TIMEOUT,
    "invalid_response": ASR_OFFLINE_INVALID_RESPONSE,
    "failed": ASR_OFFLINE_PROVIDER_FAILED,
}

# Realtime session failure codes that keep their meaning offline. Anything
# else a replayed session reports is a generic provider failure.
_SESSION_FAILURE_CODES: dict[str, str] = {
    "ASR_CREDENTIALS_MISSING": ASR_CREDENTIALS_MISSING,
    "ASR_CREDENTIALS_REJECTED": ASR_CREDENTIALS_REJECTED,
    "ASR_RATE_LIMITED": ASR_RATE_LIMITED,
    "ASR_SONIOX_CONNECTION_LIMIT": ASR_RATE_LIMITED,
    "ASR_CONNECT_TIMEOUT": ASR_OFFLINE_TIMEOUT,
    "ASR_DEPENDENCY_MISSING": ASR_DEPENDENCY_MISSING,
}

_AVAILABILITY_CODES: dict[_AsrProviderAvailability, str] = {
    _AsrProviderAvailability.BLOCKED_BACKEND: ASR_BACKEND_BLOCKED,
    _AsrProviderAvailability.MISSING_CREDENTIALS: ASR_CREDENTIALS_MISSING,
    _AsrProviderAvailability.MISSING_DEPENDENCY: ASR_DEPENDENCY_MISSING,
}

_OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
_XAI_TRANSCRIPTION_URL = "https://api.x.ai/v1/stt"

# Segment requests of one recording in flight at once. A voice message needs
# at most two; a long recording must not open hundreds of provider requests.
_MAX_CONCURRENT_SEGMENT_REQUESTS = 2

# A realtime session accepts at most one second per ``stream_audio`` call.
_SESSION_CHUNK_BYTES = PCM16_SAMPLE_RATE_HZ * PCM16_SAMPLE_WIDTH_BYTES


class AsrOfflineError(RuntimeError):
    """Offline transcription failure with a machine-readable ``code``."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        provider_key: str | None = None,
        provider_code: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.provider_key = provider_key
        self.provider_code = provider_code


def _create_http_client(timeout_seconds: float) -> HttpClient:
    """Patched by tests; the realtime workers own their clients separately."""

    return httpx.AsyncClient(timeout=timeout_seconds)


def _create_gemini_client(api_key: str) -> Any:
    return _gemini_worker._create_gemini_client(api_key)


async def _close_client(client: Any) -> None:
    for owner in (client, getattr(client, "aio", None)):
        close = getattr(owner, "aclose", None)
        if callable(close):
            try:
                await close()
            except Exception:
                logger.debug("offline ASR client close failed", exc_info=True)
            return


# A segment is sent only if it has some speech-level energy. Model-audio
# providers (Gemini in live tests) invent text for silence, e.g. two seconds
# of digital silence came back as an English sentence, so an empty or
# noise-only voice message must never reach them. 20 ms frames; speech peaks
# around 7,000-14,000 RMS, a quiet room stays well below 200 (about -44 dBFS).
_SILENCE_FRAME_SAMPLES = 320
_SILENCE_FRAME_RMS = 200.0
_MIN_VOICED_FRAMES = 5  # 100 ms


def _voiced_frame_mask(pcm16: bytes) -> np.ndarray:
    """Per 20 ms frame of 16 kHz PCM16: whether it is above the speech floor."""

    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
    usable = len(samples) - len(samples) % _SILENCE_FRAME_SAMPLES
    if usable == 0:
        return np.zeros(0, dtype=bool)
    frames = samples[:usable].reshape(-1, _SILENCE_FRAME_SAMPLES)
    return np.sqrt(np.mean(frames * frames, axis=1)) >= _SILENCE_FRAME_RMS


def voiced_frame_count(pcm16: bytes) -> int:
    """Number of 20 ms frames of 16 kHz PCM16 above the speech floor."""

    return int(np.count_nonzero(_voiced_frame_mask(pcm16)))


def is_silent_pcm16(pcm16: bytes) -> bool:
    """Whether 16 kHz PCM16 holds less than 100 ms above the speech floor."""

    return voiced_frame_count(pcm16) < _MIN_VOICED_FRAMES


def split_pcm16_segments(pcm16: bytes, max_segment_ms: int) -> list[bytes]:
    """Split 16 kHz PCM16 into the fewest equal segments within the limit.

    Equal parts avoid a near-empty trailing request (a 27.1 s recording with a
    27 s limit becomes two 13.55 s segments, not 27 s plus 0.1 s).
    """

    max_bytes = (max_segment_ms * PCM16_SAMPLE_RATE_HZ * PCM16_SAMPLE_WIDTH_BYTES // 1000) & ~1
    if max_bytes <= 0:
        raise ValueError("max_segment_ms is too small")
    count = max(1, math.ceil(len(pcm16) / max_bytes))
    samples = len(pcm16) // PCM16_SAMPLE_WIDTH_BYTES
    segments: list[bytes] = []
    for index in range(count):
        start = samples * index // count
        end = samples * (index + 1) // count
        segments.append(
            pcm16[start * PCM16_SAMPLE_WIDTH_BYTES : end * PCM16_SAMPLE_WIDTH_BYTES]
        )
    return segments


def _is_wide_char(char: str) -> bool:
    return unicodedata.east_asian_width(char) in {"W", "F"}


def join_segment_transcripts(texts: list[str]) -> str:
    """Join per-segment transcripts like the realtime segment aggregator.

    Whitespace is normalized and empty segments dropped; unlike the realtime
    aggregator no space is inserted at a CJK boundary, because a fixed-size
    split can fall in the middle of a sentence.
    """

    result = ""
    for raw in texts:
        text = " ".join(str(raw or "").split())
        if not text:
            continue
        if result and not (_is_wide_char(result[-1]) or _is_wide_char(text[0])):
            result += " "
        result += text
    return result


def _failure_error(provider_key: str, failure: AsrRequestFailure) -> AsrOfflineError:
    return AsrOfflineError(
        _FAILURE_KIND_CODES.get(failure.kind, ASR_OFFLINE_PROVIDER_FAILED),
        failure.message,
        provider_key=provider_key,
        provider_code=failure.code,
    )


def _zh_en_language(provider_name: str, language: str) -> str | None:
    try:
        return normalize_zh_en_language(language, provider_name=provider_name)
    except ValueError:
        return None


SegmentRequest = Callable[["OfflineAsrTranscriber", bytes, str, Any], Awaitable[str]]


async def _request_glm(
    transcriber: "OfflineAsrTranscriber", pcm16: bytes, language: str, client: Any
) -> str:
    _ = language  # GLM ASR detects the language itself.
    return await request_glm_transcript(
        client,
        transcriber._api_key,
        encode_pcm16_wav(pcm16),
        model=transcriber.spec.model or "",
    )


async def _request_openai(
    transcriber: "OfflineAsrTranscriber", pcm16: bytes, language: str, client: Any
) -> str:
    # gpt-transcribe detects the spoken language itself and documents a
    # ``languages`` field in place of ``language``; no hint is sent until that
    # contract is verified against the live API.
    _ = language
    fields = {"model": transcriber.spec.model or ""}
    return await post_wav_transcription(
        client,
        url=_OPENAI_TRANSCRIPTION_URL,
        api_key=transcriber._api_key,
        wav_audio=encode_pcm16_wav(pcm16),
        form_fields=fields,
        code_prefix="ASR_OPENAI_OFFLINE",
        provider_label="OpenAI",
    )


async def _request_grok(
    transcriber: "OfflineAsrTranscriber", pcm16: bytes, language: str, client: Any
) -> str:
    fields = {"model": transcriber.spec.model or ""}
    hint = _zh_en_language("xAI", language)
    if hint:
        fields["language"] = hint
    return await post_wav_transcription(
        client,
        url=_XAI_TRANSCRIPTION_URL,
        api_key=transcriber._api_key,
        wav_audio=encode_pcm16_wav(pcm16),
        form_fields=fields,
        code_prefix="ASR_GROK_OFFLINE",
        provider_label="xAI",
    )


async def _request_gemini(
    transcriber: "OfflineAsrTranscriber", pcm16: bytes, language: str, client: Any
) -> str:
    return await _gemini_worker.request_gemini_transcript(
        client,
        encode_pcm16_wav(pcm16),
        model=transcriber.spec.model or _gemini_worker._GEMINI_MODEL,
        timeout_seconds=transcriber.spec.request_timeout_ms / 1000,
        language=language,
    )


async def _request_faster_whisper(
    transcriber: "OfflineAsrTranscriber", pcm16: bytes, language: str, client: Any
) -> str:
    _ = client
    return await transcribe_pcm16_locally(
        pcm16,
        language=language,
        load_timeout_seconds=max(transcriber.meta.provider_warmup_timeout_ms, 1) / 1000,
        decode_timeout_seconds=transcriber.spec.request_timeout_ms / 1000,
    )


@dataclass(frozen=True, slots=True)
class _RequestBackend:
    request: SegmentRequest
    client: str  # "http", "gemini" or "none"
    # Segments may run concurrently; the local decoder is serial anyway.
    concurrent: bool = True
    # The outer per-segment deadline; backends with their own bounded
    # timeouts (Gemini wait_for, local load + decode) opt out.
    outer_timeout: bool = True


_REQUEST_BACKENDS: dict[str, _RequestBackend] = {
    "glm": _RequestBackend(_request_glm, client="http"),
    "openai": _RequestBackend(_request_openai, client="http"),
    "grok": _RequestBackend(_request_grok, client="http"),
    "gemini": _RequestBackend(_request_gemini, client="gemini", outer_timeout=False),
    "faster_whisper": _RequestBackend(
        _request_faster_whisper,
        client="none",
        concurrent=False,
        outer_timeout=False,
    ),
}


@dataclass(frozen=True, slots=True)
class OfflineAsrTranscriber:
    """A resolved offline route; reusable for any number of recordings."""

    core_type: str
    provider_key: str
    meta: AsrProviderMeta = field(repr=False)
    _selection: _AsrSelection = field(repr=False, compare=False)

    @property
    def spec(self) -> AsrOfflineSpec:
        return self.meta.offline

    @property
    def _api_key(self) -> str:
        return self._selection._api_key

    @property
    def credential_fingerprint(self) -> str:
        """A short one-way digest of provider + credential ("" without one).

        Lets callers remember that a credential was rejected (and notice when
        it is replaced) without ever holding the key itself.
        """

        if not self._api_key:
            return ""
        digest = hashlib.sha256(f"{self.provider_key}:{self._api_key}".encode())
        return digest.hexdigest()[:16]

    async def transcribe(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int = PCM16_SAMPLE_RATE_HZ,
        language: str | None = None,
    ) -> str:
        """Transcribe one complete 16 kHz mono PCM16LE recording.

        ``language`` is a hint ("zh", "en-US", "auto", ...); values the
        provider cannot take fall back to automatic detection, exactly like
        realtime sessions. Silence yields ``""`` without contacting the
        provider; silent segments of a longer recording are skipped.
        """

        if not isinstance(pcm16, (bytes, bytearray, memoryview)):
            raise AsrOfflineError(
                ASR_OFFLINE_INVALID_AUDIO,
                "audio must be bytes",
                provider_key=self.provider_key,
            )
        audio = bytes(pcm16)
        if sample_rate_hz != PCM16_SAMPLE_RATE_HZ:
            raise AsrOfflineError(
                ASR_OFFLINE_INVALID_AUDIO,
                "offline ASR only accepts 16 kHz PCM16",
                provider_key=self.provider_key,
            )
        if not audio or len(audio) % PCM16_SAMPLE_WIDTH_BYTES:
            raise AsrOfflineError(
                ASR_OFFLINE_INVALID_AUDIO,
                "PCM16LE audio is empty or has an odd byte length",
                provider_key=self.provider_key,
            )
        assert self.spec.max_segment_ms is not None
        # The speech threshold applies to the whole recording: a short word
        # split across a segment boundary must not fall below it twice. Once
        # the recording holds speech, only segments with no speech-level frame
        # at all are skipped.
        voiced = _voiced_frame_mask(audio)
        if int(np.count_nonzero(voiced)) < _MIN_VOICED_FRAMES:
            return ""
        # Judge segments on the recording's own frame grid: a segment's local
        # grid can cut a voiced frame in two quieter halves.
        segments: list[bytes] = []
        start_sample = 0
        for segment in split_pcm16_segments(audio, self.spec.max_segment_ms):
            end_sample = start_sample + len(segment) // PCM16_SAMPLE_WIDTH_BYTES
            first = start_sample // _SILENCE_FRAME_SAMPLES
            last = -(-end_sample // _SILENCE_FRAME_SAMPLES)  # ceil
            if bool(voiced[first:last].any()):
                segments.append(segment)
            start_sample = end_sample
        session_language = _resolve_session_language(self.provider_key, language)
        if self.spec.mode == "session":
            texts = await self._transcribe_with_session(segments, session_language)
        else:
            texts = await self._transcribe_with_requests(segments, session_language)
        return join_segment_transcripts(texts)

    async def _transcribe_with_requests(
        self, segments: list[bytes], language: str
    ) -> list[str]:
        backend = _REQUEST_BACKENDS.get(self.provider_key)
        if backend is None:
            raise AsrOfflineError(
                ASR_OFFLINE_NOT_SUPPORTED,
                "provider has no offline request backend",
                provider_key=self.provider_key,
            )
        timeout_seconds = self.spec.request_timeout_ms / 1000
        client: Any = None
        try:
            if backend.client == "http":
                client = _create_http_client(timeout_seconds)
            elif backend.client == "gemini":
                try:
                    client = _create_gemini_client(self._api_key)
                except Exception as exc:
                    raise AsrOfflineError(
                        ASR_DEPENDENCY_MISSING,
                        "Gemini SDK is unavailable",
                        provider_key=self.provider_key,
                        provider_code="ASR_GEMINI_SDK_UNAVAILABLE",
                    ) from exc

            slots = asyncio.Semaphore(
                _MAX_CONCURRENT_SEGMENT_REQUESTS if backend.concurrent else 1
            )

            async def run_segment(segment: bytes) -> str:
                try:
                    # The time budget starts once the segment holds a slot, so
                    # waiting behind earlier segments never counts against it.
                    async with slots:
                        if not backend.outer_timeout:
                            return await backend.request(self, segment, language, client)
                        async with asyncio.timeout(timeout_seconds):
                            return await backend.request(self, segment, language, client)
                except AsrRequestFailure as exc:
                    raise _failure_error(self.provider_key, exc) from exc
                except TimeoutError as exc:
                    raise AsrOfflineError(
                        ASR_OFFLINE_TIMEOUT,
                        "offline transcription request timed out",
                        provider_key=self.provider_key,
                    ) from exc

            if not backend.concurrent or len(segments) == 1:
                return [await run_segment(segment) for segment in segments]
            try:
                async with asyncio.TaskGroup() as group:
                    tasks = [group.create_task(run_segment(segment)) for segment in segments]
            except BaseExceptionGroup as group_error:
                # Report the first segment failure; siblings were cancelled.
                for exc in group_error.exceptions:
                    if isinstance(exc, AsrOfflineError):
                        raise exc from group_error
                raise
            return [task.result() for task in tasks]
        finally:
            if client is not None:
                await _close_client(client)

    async def _transcribe_with_session(
        self, segments: list[bytes], language: str
    ) -> list[str]:
        finals: asyncio.Queue[str] = asyncio.Queue()
        failed = asyncio.Event()

        async def on_transcript(text: str) -> None:
            finals.put_nowait(text)

        async def on_connection_error(_message: str) -> None:
            failed.set()

        try:
            session = _create_asr_session_from_selection(
                self.core_type,
                selection=self._selection,
                config=AsrSessionConfig(language=language, endpointing_mode="manual"),
                on_input_transcript=on_transcript,
                on_connection_error=on_connection_error,
                # One commit per segment is the endpoint: no Smart Turn model.
                external_endpointing_runtime=True,
            )
        except (RuntimeError, ValueError) as exc:
            raise AsrOfflineError(
                ASR_OFFLINE_NOT_SUPPORTED,
                "provider cannot open a manual offline session",
                provider_key=self.provider_key,
            ) from exc

        texts: list[str] = []
        try:
            async with asyncio.timeout(
                self.spec.request_timeout_ms / 1000 * len(segments)
            ):
                await session.connect()
                for segment in segments:
                    for offset in range(0, len(segment), _SESSION_CHUNK_BYTES):
                        await session.stream_audio(
                            segment[offset : offset + _SESSION_CHUNK_BYTES],
                            sample_rate_hz=PCM16_SAMPLE_RATE_HZ,
                        )
                    await session.signal_user_activity_end()
                    texts.append(await self._next_final(finals, failed, session))
        except TimeoutError as exc:
            raise AsrOfflineError(
                ASR_OFFLINE_TIMEOUT,
                "offline session did not return a transcript in time",
                provider_key=self.provider_key,
                provider_code=session.last_failure_code,
            ) from exc
        except AsrOfflineError:
            raise
        except (RuntimeError, ValueError) as exc:
            raise self._session_error(session) from exc
        finally:
            try:
                await session.close()
            except Exception:
                logger.debug("offline ASR session close failed", exc_info=True)
        return texts

    async def _next_final(
        self,
        finals: asyncio.Queue[str],
        failed: asyncio.Event,
        session: RealtimeAsrSession,
    ) -> str:
        final_task = asyncio.ensure_future(finals.get())
        failed_task = asyncio.ensure_future(failed.wait())
        try:
            await asyncio.wait(
                {final_task, failed_task}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (final_task, failed_task):
                if not task.done():
                    task.cancel()
            await asyncio.wait({final_task, failed_task})
        if final_task.done() and not final_task.cancelled():
            return final_task.result()
        raise self._session_error(session)

    def _session_error(self, session: RealtimeAsrSession) -> AsrOfflineError:
        provider_code = session.last_failure_code
        return AsrOfflineError(
            _SESSION_FAILURE_CODES.get(provider_code or "", ASR_OFFLINE_PROVIDER_FAILED),
            "offline session failed",
            provider_key=self.provider_key,
            provider_code=provider_code,
        )


def _local_offline_selection() -> _AsrSelection | None:
    """A local provider usable without any Core credential, if installed."""

    for provider_key, meta in _ASR_PROVIDER_REGISTRY.items():
        if not (
            meta.user_selectable
            and not meta.requires_credential
            and meta.offline.supported
            and meta.availability is _AsrProviderAvailability.IMPLEMENTED
            and provider_key in _IMPLEMENTED_WORKERS
        ):
            continue
        if not _optional_dependency_available(meta.optional_dependency):
            continue
        return _AsrSelection(
            provider_key=provider_key,
            endpointing_mode="manual",
            _worker_fn=_IMPLEMENTED_WORKERS[provider_key],
        )
    return None


def resolve_offline_transcriber(
    core_type: str,
    *,
    user_region: str | None = None,
    provider_preference: str | None = None,
) -> OfflineAsrTranscriber:
    """Choose the offline route for the current Core and user settings.

    Selection mirrors realtime ASR: an explicit user-selectable provider
    (``independentAsrProviderPreference``) wins, then the region-driven
    Soniox preference, then the Core's own provider. A Core without
    independent ASR (``free``) has no cloud route; it may only use an
    installed local provider. Raises :class:`AsrOfflineError`.

    Reads the core config and probes optional packages (filesystem access):
    call through ``asyncio.to_thread`` from latency-sensitive async code.
    """

    core_key = str(core_type or "").strip().lower()
    route = _CORE_ASR_ROUTES.get(core_key)
    if route is None:
        raise AsrOfflineError(ASR_UNKNOWN_CORE, core_key or "<empty>")

    try:
        selection = _resolve_user_selected_provider(
            route,
            provider_preference,
            ignore_route_capability=True,
        )
        if selection is None and not route.capabilities.supports_independent_asr:
            selection = _local_offline_selection()
            if selection is None:
                raise AsrOfflineError(
                    ASR_OFFLINE_NOT_SUPPORTED,
                    "this Core has no offline ASR route",
                    provider_key=route.provider_key,
                )
        if selection is None:
            selection = _resolve_asr_selection(core_key, user_region=user_region)
    except AsrOfflineError:
        raise
    except RuntimeError as exc:
        raise AsrOfflineError(
            ASR_INVALID_CONFIG,
            "ASR route could not be resolved",
            provider_key=route.provider_key,
        ) from exc

    provider_key = selection.provider_key
    meta = _ASR_PROVIDER_REGISTRY[provider_key]
    # Capability first: an unsupported route stays unsupported whatever its
    # credentials, so the answer does not flip when a key is added.
    if not meta.offline.supported:
        raise AsrOfflineError(
            ASR_OFFLINE_NOT_SUPPORTED,
            meta.offline.unsupported_reason,
            provider_key=provider_key,
        )
    availability_code = _AVAILABILITY_CODES.get(selection.availability)
    if availability_code is not None:
        raise AsrOfflineError(availability_code, provider_key, provider_key=provider_key)
    if (
        provider_key != "dummy"
        and meta.requires_credential
        and not selection._api_key
    ):
        raise AsrOfflineError(ASR_CREDENTIALS_MISSING, provider_key, provider_key=provider_key)
    return OfflineAsrTranscriber(
        core_type=core_key,
        provider_key=provider_key,
        meta=meta,
        _selection=selection,
    )


@dataclass(frozen=True, slots=True)
class OfflineAsrStatus:
    """Whether offline ASR can run for a Core, without transcribing anything."""

    supported: bool
    provider_key: str | None = None
    error_code: str | None = None


def get_offline_asr_status(
    core_type: str,
    *,
    user_region: str | None = None,
    provider_preference: str | None = None,
) -> OfflineAsrStatus:
    """Report offline capability, e.g. to decide whether to show a voice button."""

    try:
        transcriber = resolve_offline_transcriber(
            core_type,
            user_region=user_region,
            provider_preference=provider_preference,
        )
    except AsrOfflineError as exc:
        return OfflineAsrStatus(
            supported=False,
            provider_key=exc.provider_key,
            error_code=exc.code,
        )
    return OfflineAsrStatus(supported=True, provider_key=transcriber.provider_key)


async def transcribe_recording(
    core_type: str,
    pcm16: bytes,
    *,
    sample_rate_hz: int = PCM16_SAMPLE_RATE_HZ,
    language: str | None = None,
    user_region: str | None = None,
    provider_preference: str | None = None,
) -> str:
    """Resolve the current offline route and transcribe one recording."""

    transcriber = await asyncio.to_thread(
        resolve_offline_transcriber,
        core_type,
        user_region=user_region,
        provider_preference=provider_preference,
    )
    return await transcriber.transcribe(
        pcm16,
        sample_rate_hz=sample_rate_hz,
        language=language,
    )
