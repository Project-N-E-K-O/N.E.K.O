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

"""vLLM-Omni TTS worker."""

import numpy as np
import soxr
import json
import websockets
import asyncio

from functools import partial
from urllib.parse import urlparse, urlunparse
from utils.config_manager import _as_bool
from utils.gptsovits_config import redact_url_for_log

from .._infra import (
    AudioDoneEmitter,
    TTS_SHUTDOWN_SENTINEL,
    _enqueue_error,
    _resample_audio,
    configured_tts_unavailable_worker,
    enqueue_configured_tts_failure,
    log_configured_tts_failure,
)
from .._telemetry import _record_tts_telemetry
from .dummy import dummy_tts_worker
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

VLLM_OMNI_DEFAULT_BASE_URL = "ws://localhost:8091/v1"
VLLM_OMNI_DEFAULT_MODEL = "Qwen3-TTS"


def _vllm_omni_normalize_ws_endpoint(base_url: str) -> str:
    """Normalize a vLLM-Omni base URL into the full WebSocket speech endpoint.

    Extracted from the worker body so the clone-preview branch in
    ``characters_router`` can share the same normalization (http→ws,
    path completion, idempotent endpoint assembly) without copy-paste.

    - ``https://`` → ``wss://``, ``http://`` → ``ws://``; ``ws://``/``wss://``
      pass through unchanged.
    - Bare ``host:port`` defaults to ``ws://``.
    - Empty path gets ``/v1`` prepended.
    - Idempotent: if the path already ends with ``/audio/speech/stream``,
      it is not appended again.

    Returns the full ws endpoint, or ``''`` when ``base_url`` is blank (caller
    decides how to surface the missing-config error).
    """
    raw_base_url = (base_url or '').strip().rstrip('/')
    if not raw_base_url:
        return ''

    if raw_base_url.startswith("https://"):
        ws_url = "wss://" + raw_base_url[len("https://"):]
    elif raw_base_url.startswith("http://"):
        ws_url = "ws://" + raw_base_url[len("http://"):]
    elif raw_base_url.startswith(("ws://", "wss://")):
        ws_url = raw_base_url
    else:
        # Bare host:port defaults to ws.
        ws_url = "ws://" + raw_base_url

    parsed = urlparse(ws_url)
    base_path = (parsed.path or "").rstrip("/")
    if base_path in ("", "/"):
        base_path = "/v1"
    # Idempotent: do not append endpoints already present in paths.
    if not base_path.endswith("/audio/speech/stream"):
        base_path = f"{base_path}/audio/speech/stream"
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            base_path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def vllm_omni_tts_worker(request_queue, response_queue, audio_api_key, voice_id,
                          base_url='', model='', voice='', ref_audio='', ref_text=''):
    """vLLM-Omni TTS worker — full-duplex WebSocket streaming synthesis.

    Protocol: ``ws://{base_url}/v1/audio/speech/stream``

    Client → Server:
      1. ``{"type": "session.config", "model": "...", "voice": "...", ...}``
      2. ``{"type": "input.text", "text": "..."}``  (may be sent multiple times)
      3. ``{"type": "input.done"}``

    Server → Client:
      1. ``{"type": "audio.start", "sentence_index": N, ...}``
      2. <binary frame: PCM 24kHz/16bit/mono>
      3. ``{"type": "audio.done", "sentence_index": N}``
      4. ``{"type": "session.done", "total_sentences": N}``

    Args:
        base_url:  vLLM-Omni service root URL (e.g. ``http://localhost:8091``);
                   automatically rewritten to ws:// scheme.
        model:     Model name (defaults to ``Qwen3-TTS``).
        voice:     Voice id exposed by vllm-omni.
        ref_audio: Voice-clone reference audio as a ``data:audio/...;base64,...``
                   URI. When set, the worker switches to inline-clone mode and
                   forwards it as ``session.config.ref_audio`` (dual to MiMo's
                   inline ``clone_voice``). ⚠ vllm-omni only accepts the field
                   name ``ref_audio`` (NOT ``prompt_audio``).
        ref_text:  The transcript of the reference audio, forwarded as
                   ``session.config.ref_text`` when set. ⚠ field name ``ref_text``
                   (NOT ``prompt_text``).
    """
    raw_base_url = (base_url or '').strip().rstrip('/')
    if not raw_base_url:
        enqueue_configured_tts_failure(
            response_queue, "vllm_omni", "configuration"
        )
        response_queue.put(("__ready__", False))
        return

    # Normalize URLs, append /v1, convert HTTP->WS, and complete endpoints idempotently through
    # shared _vllm_omni_normalize_ws_endpoint, also used by cloned previews; see docstring.
    ws_endpoint = _vllm_omni_normalize_ws_endpoint(raw_base_url)

    effective_model = (model or '').strip() or 'Qwen3-TTS'
    # Clone mode inlines reference audio, symmetric with MiMo clone_voice; ref_audio is a data URI and ref_text
    # its transcript. Pass both nonempty values into session.config. Names must be exactly ref_audio/ref_text;
    # prompt_audio/prompt_text cause vllm-omni HTTP 500.
    effective_ref_audio = (ref_audio or '').strip()
    effective_ref_text = (ref_text or '').strip()
    is_clone = bool(effective_ref_audio)

    # Voice resolution:
    # 1. Clone mode with nonempty ref_audio ignores voice_id, a local N.E.K.O identifier such as
    #    vllm-omni-clone-ch-xxx rather than a server-known preset voice;
    #    use only voice, 'default' from clone resolution.
    # 2. Clone-looking voice_id without ref_audio is abnormal: resolve should have
    #    included it through clone logic, possibly missed due to voice_meta cache/timing.
    #    Force default voice plus warning rather than sending clone IDs and causing
    #    Invalid Voice/server crashes; vLLM-Omni interprets IDs as speaker names and
    #    raises ValueError when expected ref_audio is absent.
    # 3. Normal presets prefer voice_id, symmetric with other workers' voice_id->voice fallback.
    _voice_id_is_clone_id = bool(voice_id and str(voice_id).startswith('vllm-omni-clone-'))
    if is_clone:
        effective_voice = (voice or '').strip() or 'default'
    elif _voice_id_is_clone_id:
        logger.warning(
            "[vLLM-Omni TTS] voice_id='%s' 是克隆音色 ID 但 ref_audio 为空，"
            "回退到 default voice（clone resolve 可能漏传 ref_audio）",
            voice_id,
        )
        effective_voice = (voice or '').strip() or 'default'
    else:
        effective_voice = (voice_id or '').strip() or (voice or '').strip() or 'default'

    logger.info(
        "[vLLM-Omni TTS] ws=%s model=%s voice=%s clone=%s",
        redact_url_for_log(ws_endpoint), effective_model, effective_voice, is_clone,
    )

    async def async_worker():
        ws = None
        receive_task = None
        # Streaming 24 kHz -> 48 kHz resampler.
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        # Fix PR #1764 review #3, CodeRabbit: session-lifecycle state.
        # session.done clears it; rebuild/reconfigure before next input.
        session_state = {
            "active": False,
            "awaiting_done": False,
            "speech_id": None,
        }
        pending_text: list[str] = []
        pending_text_sid: str | None = None
        # session.done closes the whole utterance's audio stream; audio.done is per-sentence
        # with sentence_index, never a turn-finalization signal.
        audio_done = AudioDoneEmitter(response_queue)

        async def _connect_and_config() -> bool:
            """Open the WS connection and send session.config; return success.

            PR #1764 review #2 (CodeRabbit) fix: forward audio_api_key both via
            the WS handshake Authorization header and the session.config.api_key
            field so deployments behind reverse proxies / auth layers are covered.
            """
            nonlocal ws
            ws_kwargs = {"max_size": None}
            key_for_auth = (audio_api_key or "").strip() if audio_api_key else ""
            if key_for_auth:
                # websockets >= 12: additional_headers；< 12: extra_headers
                ws_kwargs["additional_headers"] = [
                    ("Authorization", f"Bearer {key_for_auth}"),
                ]
            try:
                ws = await websockets.connect(ws_endpoint, **ws_kwargs)
            except TypeError:
                # Older websockets uses extra_headers instead.
                if "additional_headers" in ws_kwargs:
                    ws_kwargs["extra_headers"] = ws_kwargs.pop("additional_headers")
                try:
                    ws = await websockets.connect(ws_endpoint, **ws_kwargs)
                except Exception:
                    log_configured_tts_failure("vllm_omni", "connect_legacy")
                    return False
            except Exception:
                log_configured_tts_failure("vllm_omni", "connect")
                return False

            try:
                config = {
                    "type": "session.config",
                    "model": effective_model,
                    "voice": effective_voice,
                    "response_format": "pcm",
                    "speed": 1.0,
                    "stream_audio": True,
                    "split_granularity": "sentence",
                }
                # Clone mode inlines reference audio/text, symmetric with MiMo clone_voice.
                # vllm-omni accepts exactly ref_audio/ref_text field names.
                # Send ref_text only with ref_audio; text alone makes vLLM-Omni raise ValueError
                # after interpreting voice as speaker name and expecting
                # reference audio that is absent.
                if effective_ref_audio:
                    config["ref_audio"] = effective_ref_audio
                    if effective_ref_text:
                        config["ref_text"] = effective_ref_text
                # Session authentication; some self-hosted servers read api_key from config.
                if key_for_auth:
                    config["api_key"] = key_for_auth
                await ws.send(json.dumps(config))
                return True
            except Exception:
                log_configured_tts_failure("vllm_omni", "session_config")
                try:
                    await ws.close()
                except Exception:
                    pass
                ws = None
                return False

        async def _receive_loop():
            """Receive WS messages: JSON events plus binary PCM frames."""
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        # Binary 24 kHz/16-bit/mono PCM frames resampled to 48 kHz.
                        if len(message) < 2:
                            continue
                        audio_array = np.frombuffer(message, dtype=np.int16)
                        response_queue.put(
                            _resample_audio(audio_array, 24000, 48000, resampler)
                        )
                    else:
                        try:
                            event = json.loads(message)
                        except json.JSONDecodeError:
                            continue
                        event_type = event.get("type", "")
                        if event_type == "session.done":
                            logger.debug(
                                "[vLLM-Omni TTS] session.done: total_sentences=%s",
                                event.get("total_sentences", "?"),
                            )
                            # Fix PR #1764 review #3: mark session ended and clear resampler.
                            # Main loop rebuilds/reconfigures before next input.text.
                            # PCM enters queues synchronously within this receiver loop, so all this turn's
                            # audio is delivered here. Capture sid before clearing state.
                            audio_done.emit(session_state.get("speech_id"))
                            session_state["active"] = False
                            session_state["awaiting_done"] = False
                            session_state["speech_id"] = None
                            try:
                                resampler.clear()
                            except Exception:
                                pass
                        elif event_type == "audio.start":
                            logger.debug(
                                "[vLLM-Omni TTS] audio.start: idx=%s text=%s",
                                event.get("sentence_index"),
                                event.get("sentence_text", "")[:40],
                            )
                        elif event_type == "audio.done":
                            pass  # Per-sentence completion; session.done finalizes turns.
                        elif event_type == "error":
                            # Provider events may echo signed URLs or input text.
                            # Convert server errors to stable codes only, without logging original payloads.
                            enqueue_configured_tts_failure(
                                response_queue, "vllm_omni", "server_event"
                            )
                            # Fix PR #1764 review round six: server errors invalidate sessions;
                            # mark unusable and rebuild before next input, matching session.done handling.
                            session_state["active"] = False
                            session_state["awaiting_done"] = False
                            session_state["speech_id"] = None
                            response_queue.put(("__ready__", False))
            except websockets.exceptions.ConnectionClosed:
                was_awaiting_done = bool(session_state.get("awaiting_done"))
                # Fix PR #1764 review round six: synchronize local state on WS closure,
                # avoiding sends to dead connections that require noisy, slow exception-driven recovery.
                session_state["active"] = False
                session_state["awaiting_done"] = False
                session_state["speech_id"] = None
                if was_awaiting_done:
                    _enqueue_error(response_queue, {
                        "code": "TTS_CONNECTION_FAILED",
                        "provider": "vllm_omni",
                        "message": "vLLM-Omni TTS 连接在 session.done 前关闭",
                    })
                    response_queue.put(("__ready__", False))
            except Exception:
                was_awaiting_done = bool(session_state.get("awaiting_done"))
                log_configured_tts_failure("vllm_omni", "receive")
                session_state["active"] = False
                session_state["awaiting_done"] = False
                session_state["speech_id"] = None
                if was_awaiting_done:
                    _enqueue_error(response_queue, {
                        "code": "TTS_CONNECTION_FAILED",
                        "provider": "vllm_omni",
                        "message": "vLLM-Omni TTS 接收异常，session.done 未完成",
                    })
                    response_queue.put(("__ready__", False))

        # Initial connection and readiness signal.
        if not await _connect_and_config():
            _enqueue_error(response_queue, {
                "code": "TTS_CONNECTION_FAILED",
                "provider": "vllm_omni",
                "message": "vLLM-Omni TTS 初始连接失败",
            })
            response_queue.put(("__ready__", False))
            return

        session_state["active"] = True  # Fix PR #1764 review #3.
        receive_task = asyncio.create_task(_receive_loop())
        response_queue.put(("__ready__", True))
        logger.info("[vLLM-Omni TTS] 已就绪")

        async def _rebuild_session() -> bool:
            """PR #1764 review #3 helper: tear down the old session and rebuild a new one.

            Called after session.done / on ws.send failure / on __interrupt__.
            Returns True on success, False on failure (outer loop should stop).
            """
            nonlocal ws, receive_task
            if receive_task is not None and not receive_task.done():
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            receive_task = None
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
                ws = None
            try:
                resampler.clear()
            except Exception:
                pass
            if not await _connect_and_config():
                session_state["active"] = False
                session_state["awaiting_done"] = False
                session_state["speech_id"] = None
                return False
            session_state["active"] = True
            session_state["awaiting_done"] = False
            session_state["speech_id"] = None
            audio_done.reset()  # Reset audio_done deduplication on a new session.
            receive_task = asyncio.create_task(_receive_loop())
            return True

        async def _replay_pending_text() -> bool:
            if not pending_text:
                return True
            try:
                replay_text = "".join(pending_text)
                await ws.send(json.dumps({
                    "type": "input.text",
                    "text": replay_text,
                }))
                session_state["speech_id"] = pending_text_sid
                session_state["awaiting_done"] = False
                return True
            except Exception:
                log_configured_tts_failure("vllm_omni", "replay_pending")
                session_state["active"] = False
                return False

        def _fail_pending_flush(message: str):
            nonlocal pending_text_sid
            pending_text.clear()
            pending_text_sid = None
            session_state["active"] = False
            session_state["awaiting_done"] = False
            session_state["speech_id"] = None
            _enqueue_error(response_queue, {
                "code": "TTS_CONNECTION_FAILED",
                "provider": "vllm_omni",
                "message": message,
            })
            response_queue.put(("__ready__", False))

        loop = asyncio.get_running_loop()

        while True:
            try:
                sid, tts_text = await loop.run_in_executor(None, request_queue.get)
            except Exception:
                break

            if sid == TTS_SHUTDOWN_SENTINEL:
                break

            if sid == "__interrupt__":
                # Fix PR #1764 review round two #3: interruption destroys current connections and invalidates sessions,
                # without immediate reconnect, preventing transient upstream failure from exiting whole workers.
                # Defer reconnection until next input's liveness check below.
                audio_done.begin_interrupt()  # Interrupted turns omit audio_done via the independent cancellation channel.
                # Match step/qwen/grok/elevenlabs/gptsovits try/finally: unexpected teardown
                # failures must not strand emitters in interrupted state, silently suppressing
                # audio_done on every later turn of this session.
                try:
                    if receive_task is not None and not receive_task.done():
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            # This cancellation was issued above and is expected.
                            pass
                        except Exception:
                            # Receiver shutdown errors are unrelated to this interruption; swallow and continue teardown.
                            pass
                    receive_task = None
                    if ws is not None:
                        try:
                            await ws.close()
                        except Exception:
                            # The connection is being discarded; close failures cannot block teardown.
                            pass
                        ws = None
                    try:
                        resampler.clear()
                    except Exception:
                        # Rebuild resampler next turn; cleanup failure must not block interruption.
                        pass
                    session_state["active"] = False
                    session_state["awaiting_done"] = False
                    # Match _receive_loop / _rebuild_session / _fail_pending_flush /
                    # sid switches by clearing speech_id, preventing stale utterance IDs after interruption.
                    session_state["speech_id"] = None
                    pending_text.clear()
                    pending_text_sid = None
                    audio_done.reset()
                finally:
                    audio_done.end_interrupt()
                continue

            if sid is None:
                if not pending_text:
                    continue
                if not session_state["active"] or ws is None:
                    logger.info("[vLLM-Omni TTS] 会话已结束/失效，重建连接以发送 flush")
                    if not await _rebuild_session():
                        logger.error("[vLLM-Omni TTS] 重建会话失败，标记 worker 未就绪")
                        _fail_pending_flush("vLLM-Omni TTS flush 重连失败")
                        break
                    if not await _replay_pending_text():
                        _fail_pending_flush("vLLM-Omni TTS flush 重放失败")
                        break
                if ws is not None:
                    try:
                        await ws.send(json.dumps({"type": "input.done"}))
                        pending_text.clear()
                        pending_text_sid = None
                        session_state["awaiting_done"] = True
                    except Exception:
                        log_configured_tts_failure("vllm_omni", "input_done")
                        session_state["active"] = False
                        session_state["awaiting_done"] = False
                        if not await _rebuild_session():
                            _fail_pending_flush("vLLM-Omni TTS flush 重连失败")
                            break
                        try:
                            if not await _replay_pending_text():
                                _fail_pending_flush("vLLM-Omni TTS flush 重放失败")
                                break
                            await ws.send(json.dumps({"type": "input.done"}))
                            pending_text.clear()
                            pending_text_sid = None
                            session_state["awaiting_done"] = True
                            logger.info("[vLLM-Omni TTS] 重放 pending_text 并重发 input.done 成功")
                        except Exception:
                            log_configured_tts_failure("vllm_omni", "input_done_retry")
                            _fail_pending_flush("vLLM-Omni TTS flush 重发失败")
                            break
                else:
                    _fail_pending_flush("vLLM-Omni TTS flush 连接不可用")
                    break
                continue

            # Fix PR #1764 review #3: check session reuse before sending.
            # Active sessions carry one utterance only; rebuild on sid changes to prevent mixed audio.
            if (
                session_state["active"]
                and session_state.get("speech_id") not in (None, sid)
            ):
                logger.info(
                    "[vLLM-Omni TTS] 收到新 sid=%s，重建会话避免跨 utterance 复用",
                    sid,
                )
                pending_text.clear()
                pending_text_sid = None
                session_state["active"] = False
            if not session_state["active"] or ws is None:
                logger.info("[vLLM-Omni TTS] 会话已结束/失效，重建连接以发送新输入")
                if not await _rebuild_session():
                    logger.error("[vLLM-Omni TTS] 重建会话失败，标记 worker 未就绪")
                    _enqueue_error(response_queue, {
                        "code": "TTS_CONNECTION_FAILED",
                        "provider": "vllm_omni",
                        "message": "vLLM-Omni TTS 重连失败",
                    })
                    response_queue.put(("__ready__", False))
                    break
                if pending_text and pending_text_sid == sid:
                    if not await _replay_pending_text():
                        _fail_pending_flush("vLLM-Omni TTS input.text 重连后重放失败")
                        break

            if tts_text and tts_text.strip() and ws is not None:
                if pending_text and pending_text_sid not in (None, sid):
                    logger.debug(
                        "[vLLM-Omni TTS] 丢弃跨 utterance 的 pending_text (sid=%s, current=%s, len=%d)",
                        pending_text_sid,
                        sid,
                        sum(len(part) for part in pending_text),
                    )
                    pending_text.clear()
                    pending_text_sid = None
                payload = json.dumps({
                    "type": "input.text",
                    "text": tts_text,
                })
                try:
                    await ws.send(payload)
                    _record_tts_telemetry(effective_model, len(tts_text))
                    pending_text.append(tts_text)
                    pending_text_sid = sid
                    session_state["speech_id"] = sid
                    session_state["awaiting_done"] = False
                except Exception:
                    log_configured_tts_failure("vllm_omni", "input_text")
                    session_state["active"] = False
                    session_state["awaiting_done"] = False
                    if await _rebuild_session():
                        try:
                            if not await _replay_pending_text():
                                _fail_pending_flush("vLLM-Omni TTS input.text 重放失败")
                                break
                            await ws.send(payload)
                            _record_tts_telemetry(effective_model, len(tts_text))
                            pending_text.append(tts_text)
                            pending_text_sid = sid
                            session_state["speech_id"] = sid
                            session_state["awaiting_done"] = False
                            logger.info("[vLLM-Omni TTS] 重发 input.text 成功")
                        except Exception:
                            log_configured_tts_failure("vllm_omni", "input_text_retry")
                            session_state["active"] = False
                            _enqueue_error(response_queue, {
                                "code": "TTS_CONNECTION_FAILED",
                                "provider": "vllm_omni",
                                "message": "vLLM-Omni TTS 发送失败",
                            })
                            response_queue.put(("__ready__", False))
                            break
                    else:
                        _enqueue_error(response_queue, {
                            "code": "TTS_CONNECTION_FAILED",
                            "provider": "vllm_omni",
                            "message": "vLLM-Omni TTS 重连失败",
                        })
                        response_queue.put(("__ready__", False))
                        break

        # Cleanup.
        if receive_task and not receive_task.done():
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
        if ws:
            try:
                await ws.close()
            except Exception:
                pass

    try:
        asyncio.run(async_worker())
    except Exception:
        log_configured_tts_failure("vllm_omni", "worker_start")
        response_queue.put(("__ready__", False))

# -- vLLM-Omni local self-hosted services --
# Combine two selection mechanisms in one entry, design section 3.1, following MiMo:
#   1. Config presets: explicit ttsModelProvider=='vllm_omni' uses preset/user-entered
#      voice IDs without clone reference audio.
#   2. Clone metadata: selected vLLM-Omni cloned voices use voice_meta.provider==
#      'vllm_omni', symmetric with MiMo. Clones have no remote voice_id; local
#      voice_meta clone_sample_b64 is read at dispatch and inlined as session.config.ref_audio.

def _vllm_omni_voice_meta_is_clone(vm) -> bool:
    return bool(vm and vm.get('provider') == 'vllm_omni')

def _build_vllm_omni_clone_data_uri(voice_meta) -> str | None:
    """Build the ``data:`` reference-audio URI for a vLLM-Omni clone from its
    voice_meta (the clone identity lives entirely in voice_storage.json — the
    sample base64 is stored inline, dual to MiMo's ``clone_sample_b64``), or None
    when absent.

    The stored value is already base64, so this only frames it as a data URI —
    no decode/re-encode.
    """
    b64 = str((voice_meta or {}).get('clone_sample_b64') or '').strip()
    if not b64:
        return None
    mime = str((voice_meta or {}).get('clone_sample_mime') or '').strip() or 'audio/wav'
    return f"data:{mime};base64,{b64}"

def _vllm_omni_is_selected(ctx) -> bool:
    # Test config presets before any ctx.voice_meta access: metadata is lazy, and explicit
    # vllm_omni dropdown defaults must not load it, preserving
    # test_get_tts_worker_routes_explicit_vllm_before_cloned_voice short-circuiting. Match _mimo_is_selected:
    # config selection before clone metadata.
    core_config = ctx.core_config
    if _as_bool(core_config.get('ENABLE_CUSTOM_API'), False):
        # Selection uses the existing sanitized snapshot; sensitive credentials
        # remain a resolver-only disk read.
        # Selection reads snapshots only; raw API keys remain resolve-stage reads.
        if 'ttsModelProvider' in core_config:
            configured_provider = str(core_config.get('ttsModelProvider') or '').strip()
        else:
            try:
                raw = ctx.cm.load_json_config('core_config.json', {}) or {}
                configured_provider = str(raw.get('ttsModelProvider') or '').strip()
            except Exception:
                # Selection errors use the same redacted diagnostic contract.
                # Selection failures must never expose original config exception text in backend logs.
                log_configured_tts_failure("vllm_omni", "selection")
        if configured_provider == 'vllm_omni':
            return True
    # Clone selection routes by voice_meta.provider lazily, loading nothing when earlier config-selected
    # providers or this provider's config branch match.
    return _vllm_omni_voice_meta_is_clone(ctx.voice_meta)

def _vllm_omni_clone_is_selected(ctx) -> bool:
    """Clone-only selection predicate (for symmetry with _mimo dispatch / tests)."""
    return _vllm_omni_voice_meta_is_clone(ctx.voice_meta)

def _vllm_omni_clone_resolve(ctx):
    """Resolve a vLLM-Omni *clone* voice to its worker (inline reference audio).

    Dual to ``_mimo_resolve``'s clone branch: reads the reference-audio sample
    from ``voice_meta`` and builds a data URI to inline into
    ``session.config.ref_audio``; reads ``clone_ref_text`` as ``ref_text``.
    vLLM-Omni is a local service with no API key — the api_key override returns
    an empty string (consistent with ``_vllm_omni_resolve``'s credential-leak
    prevention: never fall back to another provider's key).
    """
    cm = ctx.cm
    vm = ctx.voice_meta or {}
    clone_uri = _build_vllm_omni_clone_data_uri(vm)
    if not clone_uri:
        logger.warning(
            "vLLM-Omni 克隆音色 %s 缺少参考音频样本，改用 dummy TTS worker", ctx.voice_id)
        return dummy_tts_worker, None, None
    try:
        raw = cm.load_json_config('core_config.json', {})
    except Exception:
        raw = {}
    # With vLLM-Omni enabled, current config is the sole endpoint, preventing stale follow_assist
    # URLs in old clones from overriding later user-selected vLLM services. Only disabled config uses historical
    # snapshots for compatibility; local saving remains allowed, while unconfigured clones fail as before at runtime.
    raw_vllm_omni_selected = (
        _as_bool(raw.get('enableCustomApi'), False)
        and str(raw.get('ttsModelProvider') or '').strip() == 'vllm_omni'
    )
    current_vllm_url = (
        str(raw.get('ttsModelUrl') or raw.get('TTS_MODEL_URL') or '').strip()
        if raw_vllm_omni_selected
        else ''
    )
    # An explicitly selected vLLM-Omni provider owns the endpoint choice. Its
    # blank URL is the supported localhost default, not a signal to reuse a
    # stale clone snapshot captured while TTS followed another provider.
    if raw_vllm_omni_selected:
        vllm_url = current_vllm_url or VLLM_OMNI_DEFAULT_BASE_URL
    else:
        vllm_url = (
            str(vm.get('vllm_omni_base_url') or '').strip()
            or (raw.get('ttsModelUrl') or '').strip()
            or VLLM_OMNI_DEFAULT_BASE_URL
        )
    vllm_model = (raw.get('ttsModelId') or '').strip() or VLLM_OMNI_DEFAULT_MODEL
    clone_ref_text = str(vm.get('clone_ref_text') or '').strip()
    if not clone_ref_text:
        # Matching ref_text/audio is essential for clone quality; frontend voice_clone.js requires text,
        # but old/manual voice_storage data may lack it. Empty ref_text still synthesizes
        # without blocking, merely degrading quality; warn for observability.
        logger.warning(
            "vLLM-Omni 克隆音色 %s 缺少 ref_text，克隆音质可能下降", ctx.voice_id)
    worker = partial(
        vllm_omni_tts_worker,
        base_url=vllm_url, model=vllm_model, voice='default',
        ref_audio=clone_uri, ref_text=clone_ref_text,
    )
    # Prevent credential leakage: like presets, read ttsModelApiKey; absent keys yield empty strings, never
    # another provider's credentials (same-source _vllm_omni_resolve L713 behavior).
    vllm_key = (raw.get('ttsModelApiKey') or '').strip()
    return worker, vllm_key, 'vllm_omni'

def _vllm_omni_resolve(ctx):
    cm = ctx.cm
    try:
        raw = cm.load_json_config('core_config.json', {})
    except Exception:
        log_configured_tts_failure("vllm_omni", "configuration")
        # Preserve WSS/HTTPS provider symmetry: both report setup failure through
        # the same ready channel before core activates the existing fallback.
        # WSS/HTTPS config-read failures report the original provider before entering the same fallback seam.
        return configured_tts_unavailable_worker, "", "vllm_omni"

    def _config_value(key):
        # Selection and resolution must use one session snapshot. Disk is only
        # a compatibility fallback when an older snapshot omits the field.
        # Selection/resolution share session snapshots, falling back to disk only for missing legacy fields.
        if key in ctx.core_config:
            return ctx.core_config.get(key)
        return raw.get(key)

    configured_voice = str(_config_value('ttsVoiceId') or '').strip() or 'default'
    configured_provider = str(_config_value('ttsModelProvider') or '').strip()
    config_selected = (
        _as_bool(ctx.core_config.get('ENABLE_CUSTOM_API'), False)
        and configured_provider == 'vllm_omni'
    )
    raw_provider = str(raw.get('ttsModelProvider') or '').strip()
    if config_selected and raw_provider and raw_provider != configured_provider:
        # The raw credential may already belong to a newly selected provider.
        # Never send it to the endpoint retained by this session snapshot.
        # Disk credentials may now belong to another provider; never send them to old session endpoints.
        log_configured_tts_failure("vllm_omni", "configuration")
        return configured_tts_unavailable_worker, "", "vllm_omni"
    # The exact configured preset owns a same-ID collision; a different clone
    # still keeps the historical clone-first behavior.
    # Explicit config overrides same-named clones only when character voice exactly equals configured Voice ID;
    # other clones retain their existing priority.
    configured_preset_selected = (
        config_selected
        and str(ctx.voice_id or '').strip() == configured_voice
    )

    # Clones normally precede default presets because selecting another cloned voice expresses explicit intent,
    # even with vllm_omni default TTS. Previous not-config_selected guards instead
    # routed configured vllm_omni plus selected clones through presets, sending
    # internal IDs such as vllm-omni-clone-ch-xxx as server voice names,
    # causing Invalid Voice because these IDs are local N.E.K.O
    # storage identifiers, not server presets. config_selected originally prevented
    # lazy voice_meta loading, but _vllm_omni_is_selected already checks it lazily
    # after config selection. Reaching resolve means is_selected returned True
    # and metadata is loaded, so no short-circuit issue remains.
    if (
        _vllm_omni_voice_meta_is_clone(ctx.voice_meta)
        and not configured_preset_selected
    ):
        return _vllm_omni_clone_resolve(ctx)
    vllm_url = str(_config_value('ttsModelUrl') or '').strip() or VLLM_OMNI_DEFAULT_BASE_URL
    vllm_model = str(_config_value('ttsModelId') or '').strip() or VLLM_OMNI_DEFAULT_MODEL
    vllm_voice = configured_voice
    # Credentials remain a resolver-only raw read and never enter the session
    # snapshot; an empty key must still block fallback to another provider's key.
    # Read raw credentials only during resolution, never snapshots; empty keys cannot borrow other providers' credentials.
    # Return empty strings, not None, for absent keys; core.resolve_tts_api_key's
    # provider_key=='vllm_omni' exception forbids cross-provider fallback,
    # as documented in get_tts_worker / PR #1764 review round three #3.
    vllm_key = (raw.get('ttsModelApiKey') or '').strip()
    worker = partial(
        vllm_omni_tts_worker,
        base_url=vllm_url, model=vllm_model, voice=vllm_voice,
    )
    return worker, vllm_key, 'vllm_omni'
