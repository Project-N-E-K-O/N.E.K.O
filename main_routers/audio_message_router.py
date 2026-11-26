"""Chat voice messages: transcribe one recording with offline ASR.

The chat window records up to 30 s of 16 kHz mono PCM16 and hands it to the
host page, which posts it here, receives the transcript, and sends that text
through the ordinary text-message path. The recording is never stored.

A retry of a failed send reuses its ``requestId``: a retry that arrives while
the first transcription is still running joins it, and a transcript that was
already produced (the text send failed afterwards, e.g. the chat was busy) is
served from a short-lived cache instead of being transcribed and billed again.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from main_logic.asr_client.offline import (
    AsrOfflineError,
    is_silent_pcm16,
    resolve_offline_transcriber,
)
from main_routers.system_router import _validate_local_mutation_request
from utils.conversation_settings_constants import (
    normalize_independent_asr_provider_preference_handshake,
)
from utils.logger_config import get_module_logger
from utils.preferences import aload_global_conversation_settings

from .shared_state import get_config_manager, get_session_manager


router = APIRouter(prefix="/api/audio-message", tags=["audio-message"])
logger = get_module_logger(__name__)

SAMPLE_RATE_HZ = 16_000
# The chat window stops recording at 30 s; one extra second of slack.
MAX_AUDIO_SECONDS = 31
MAX_PCM_BYTES = SAMPLE_RATE_HZ * 2 * MAX_AUDIO_SECONDS
# base64 grows the PCM by 4/3; the rest of the JSON is a few short fields.
MAX_BODY_BYTES = (MAX_PCM_BYTES + 2) // 3 * 4 + 4_096
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")

_CACHE_TTL_SECONDS = 600.0
_CACHE_MAX_ENTRIES = 64
# Transcriptions running at once, across all callers. A running one is not
# cancelled when its caller disconnects (a retry may join it), so without a
# shared cap fresh request IDs could pile up paid provider calls.
_MAX_RUNNING_TRANSCRIPTIONS = 4

_STATUS_BY_CODE = {
    "ASR_OFFLINE_INVALID_AUDIO": 400,
    "ASR_UNKNOWN_CORE": 409,
    "ASR_INVALID_CONFIG": 409,
    "ASR_OFFLINE_NOT_SUPPORTED": 409,
    "ASR_BACKEND_BLOCKED": 409,
    "ASR_DEPENDENCY_MISSING": 409,
    "ASR_CREDENTIALS_MISSING": 409,
    "ASR_CREDENTIALS_REJECTED": 409,
    "ASR_RATE_LIMITED": 429,
    "ASR_OFFLINE_TIMEOUT": 504,
}


class _TranscriptCache:
    """requestId -> in-flight transcription task, or its finished transcript."""

    def __init__(self) -> None:
        self._entries: OrderedDict[str, tuple[float, asyncio.Task[str] | str]] = OrderedDict()

    def _prune(self, now: float) -> None:
        finished = [key for key, (_, value) in self._entries.items() if isinstance(value, str)]
        for key in finished:
            if now - self._entries[key][0] > _CACHE_TTL_SECONDS:
                del self._entries[key]
        excess = len(self._entries) - _CACHE_MAX_ENTRIES
        for key in [k for k in finished if k in self._entries][: max(0, excess)]:
            del self._entries[key]

    def _settle(self, key: str, task: asyncio.Task[str]) -> None:
        """Record a finished task's outcome, whoever is still waiting on it.

        Runs as the task's own done callback, so a caller that disconnected
        mid-transcription cannot leave a failure cached as a task (every retry
        would replay it) or a success that never expires.
        """

        if self._entries.get(key, (0.0, None))[1] is not task:
            return
        if task.cancelled() or task.exception() is not None:
            # Failures are not cached: a retry transcribes again.
            del self._entries[key]
        else:
            self._entries[key] = (time.monotonic(), task.result())

    async def transcribe(self, key: str, start: Callable[[], Awaitable[str]]) -> str:
        now = time.monotonic()
        self._prune(now)
        entry = self._entries.get(key)
        if entry is not None:
            value = entry[1]
            if isinstance(value, str):
                return value
            if not value.done():
                return await asyncio.shield(value)
            # Finished but not yet settled (its callback is queued).
            self._settle(key, value)
            if key in self._entries:
                return self._entries[key][1]  # type: ignore[return-value]
        running = sum(
            1 for _, value in self._entries.values()
            if isinstance(value, asyncio.Task) and not value.done()
        )
        if running >= _MAX_RUNNING_TRANSCRIPTIONS:
            # Retryable: the host maps this to "busy" and keeps the draft.
            raise AsrOfflineError("ASR_RATE_LIMITED", "too many transcriptions in flight")
        task: asyncio.Task[str] = asyncio.ensure_future(start())
        self._entries[key] = (now, task)
        task.add_done_callback(lambda done: self._settle(key, done))
        # Shielded: a client that disconnects must not cancel the request
        # that a retry with the same ID may join.
        return await asyncio.shield(task)

    def clear(self) -> None:
        self._entries.clear()
        _REJECTED_CREDENTIALS.clear()


_CACHE = _TranscriptCache()
# Fingerprints (never the keys) of credentials a provider refused while
# transcribing a voice message; cleared when the same credential works again.
_REJECTED_CREDENTIALS: set[str] = set()


def _error(code: str, status_code: int | None = None) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error_code": code},
        status_code=status_code or _STATUS_BY_CODE.get(code, 502),
    )


async def _route_context(target: str) -> dict:
    """Core route, language and provider preference for one character."""

    manager = get_session_manager().get(target) if target else None
    core_type = str(getattr(manager, "core_api_type", "") or "").strip().lower()
    if not core_type:
        core_config = await get_config_manager().aget_core_config() or {}
        core_type = str(core_config.get("CORE_API_TYPE") or "").strip().lower()
    try:
        settings = await aload_global_conversation_settings()
    except Exception:
        logger.warning("audio message: conversation settings unavailable", exc_info=True)
        settings = {}
    preference = normalize_independent_asr_provider_preference_handshake(
        settings.get("independentAsrProviderPreference", "auto")
    )
    return {
        "core_type": core_type,
        "language": getattr(manager, "user_language", None),
        "provider_preference": preference,
    }


def _offline_status(core_type: str, provider_preference: str | None) -> dict:
    try:
        transcriber = resolve_offline_transcriber(
            core_type,
            provider_preference=provider_preference,
        )
    except AsrOfflineError as exc:
        return {"supported": False, "provider": exc.provider_key, "error_code": exc.code}
    if transcriber.credential_fingerprint in _REJECTED_CREDENTIALS:
        # Route resolution only proves a key is present; this one was already
        # refused by the provider, so offer voice messages again only once
        # the key is replaced (a different fingerprint).
        return {
            "supported": False,
            "provider": transcriber.provider_key,
            "error_code": "ASR_CREDENTIALS_REJECTED",
        }
    return {"supported": True, "provider": transcriber.provider_key, "error_code": None}


@router.get("/status")
async def get_audio_message_status(target: str = ""):
    """Whether voice messages can be transcribed for this character now."""

    context = await _route_context(target)
    return await asyncio.to_thread(
        _offline_status,
        context["core_type"],
        context["provider_preference"],
    )


@router.post("/transcribe")
async def transcribe_audio_message(request: Request):
    """Transcribe ``{requestId, target, audio: base64 PCM16, sampleRateHz}``."""

    rejected = _validate_local_mutation_request(
        request,
        error_defaults={"ok": False, "error_code": "csrf_validation_failed"},
    )
    if rejected is not None:
        return rejected
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return _error("ASR_OFFLINE_AUDIO_TOO_LONG", 413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            return _error("ASR_OFFLINE_AUDIO_TOO_LONG", 413)
        body.extend(chunk)
    try:
        payload = json.loads(bytes(body))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error("ASR_OFFLINE_INVALID_REQUEST", 400)
    if not isinstance(payload, dict):
        return _error("ASR_OFFLINE_INVALID_REQUEST", 400)
    request_id = payload.get("requestId")
    target = payload.get("target")
    audio_b64 = payload.get("audio")
    sample_rate = payload.get("sampleRateHz", SAMPLE_RATE_HZ)
    if (
        not isinstance(request_id, str)
        or not _REQUEST_ID_RE.fullmatch(request_id)
        or not isinstance(target, str)
        or not target.strip()
        or not isinstance(audio_b64, str)
    ):
        return _error("ASR_OFFLINE_INVALID_REQUEST", 400)
    if sample_rate != SAMPLE_RATE_HZ:
        return _error("ASR_OFFLINE_INVALID_AUDIO")
    try:
        pcm16 = base64.b64decode(audio_b64, validate=True)
    except (binascii.Error, ValueError):
        return _error("ASR_OFFLINE_INVALID_AUDIO")
    if len(pcm16) > MAX_PCM_BYTES:
        return _error("ASR_OFFLINE_AUDIO_TOO_LONG", 413)

    context = await _route_context(target.strip())

    async def start() -> str:
        transcriber = await asyncio.to_thread(
            resolve_offline_transcriber,
            context["core_type"],
            provider_preference=context["provider_preference"],
        )
        began = time.monotonic()
        fingerprint = transcriber.credential_fingerprint
        try:
            text = await transcriber.transcribe(
                pcm16,
                sample_rate_hz=SAMPLE_RATE_HZ,
                language=context["language"],
            )
        except AsrOfflineError as exc:
            if exc.code == "ASR_CREDENTIALS_REJECTED" and fingerprint:
                _REJECTED_CREDENTIALS.add(fingerprint)
            raise
        if not is_silent_pcm16(pcm16):
            # Only a recording that actually reached the provider proves the
            # credential works; a silent one is answered locally.
            _REJECTED_CREDENTIALS.discard(fingerprint)
        logger.info(
            "audio message transcribed provider=%s audio_ms=%d elapsed_ms=%d chars=%d",
            transcriber.provider_key,
            len(pcm16) * 1000 // (SAMPLE_RATE_HZ * 2),
            int((time.monotonic() - began) * 1000),
            len(text),
        )
        return text

    cache_key = f"{target.strip()}\x00{request_id}"
    try:
        text = await _CACHE.transcribe(cache_key, start)
    except AsrOfflineError as exc:
        logger.warning(
            "audio message transcription failed code=%s provider=%s provider_code=%s",
            exc.code,
            exc.provider_key,
            exc.provider_code,
        )
        return _error(exc.code)
    except Exception:
        logger.exception("audio message transcription crashed")
        return _error("ASR_OFFLINE_PROVIDER_FAILED")
    return {"ok": True, "text": text}
