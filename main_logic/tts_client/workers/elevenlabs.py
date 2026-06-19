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

"""ElevenLabs TTS worker."""

import numpy as np
import soxr
import json
import re
import base64
import websockets
import asyncio

from functools import partial
from utils.elevenlabs_tts_voices import ELEVENLABS_TTS_DEFAULT_MODEL, ELEVENLABS_TTS_DEFAULT_OUTPUT_FORMAT, normalize_elevenlabs_voice_id

from .._infra import TTS_SHUTDOWN_SENTINEL, _resample_audio, _enqueue_error
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

def _resolve_elevenlabs_api_key(cm) -> str:
    return (cm.get_tts_api_key('elevenlabs') or '').strip()

def _normalize_elevenlabs_voice_id(voice_id: str | None) -> str:
    return normalize_elevenlabs_voice_id(voice_id)

def _parse_elevenlabs_pcm_sample_rate(output_format: str | None) -> int:
    match = re.match(r"^pcm_(\d+)$", (output_format or "").strip())
    if not match:
        return 24000
    try:
        return int(match.group(1))
    except ValueError:
        return 24000

def _is_elevenlabs_pcm_output_format(output_format: str | None) -> bool:
    return bool(re.match(r"^pcm_(\d+)$", (output_format or "").strip()))

def _get_elevenlabs_options(base_url=None):
    raw_base_url = (
        base_url
        or "https://api.elevenlabs.io"
    )
    base_url = (raw_base_url or "https://api.elevenlabs.io").strip().rstrip('/')

    return {
        'base_url': base_url,
        'model': ELEVENLABS_TTS_DEFAULT_MODEL,
        'output_format': ELEVENLABS_TTS_DEFAULT_OUTPUT_FORMAT,
        'stability': 0.5,
        'similarity_boost': 0.75,
        'style': 0.0,
        'use_speaker_boost': True,
    }

_ELEVENLABS_WS_CHUNK_SCHEDULE = [120, 160, 250, 290]

def _elevenlabs_ws_base_url(base_url: str | None) -> str:
    raw = (base_url or "https://api.elevenlabs.io").strip().rstrip("/")
    if raw.startswith("https://"):
        return "wss://" + raw[len("https://"):]
    if raw.startswith("http://"):
        return "ws://" + raw[len("http://"):]
    if raw.startswith("wss://") or raw.startswith("ws://"):
        return raw
    return "wss://" + raw

def elevenlabs_tts_worker(request_queue, response_queue, audio_api_key, voice_id, base_url=None):
    """ElevenLabs TTS worker - WebSocket stream-input PCM output."""
    from urllib.parse import urlencode

    normalized_voice_id = _normalize_elevenlabs_voice_id(voice_id)
    options = _get_elevenlabs_options(base_url)
    output_format = options['output_format']
    if not _is_elevenlabs_pcm_output_format(output_format):
        _enqueue_error(response_queue, {
            "code": "ELEVENLABS_OUTPUT_FORMAT_UNSUPPORTED",
            "provider": "elevenlabs",
            "message": f"ElevenLabs TTS worker requires PCM output, got {output_format!r}",
        })
        response_queue.put(("__ready__", False))
        return

    ws_base_url = _elevenlabs_ws_base_url(options['base_url'])
    ws_url = f"{ws_base_url}/v1/text-to-speech/{normalized_voice_id}/stream-input"
    ws_params = urlencode({
        "model_id": options['model'],
        "output_format": output_format,
    })
    ws_url = f"{ws_url}?{ws_params}"
    chunk_schedule = list(_ELEVENLABS_WS_CHUNK_SCHEDULE)
    pcm_sample_rate = _parse_elevenlabs_pcm_sample_rate(output_format)

    def _build_voice_settings() -> dict:
        return {
            "stability": options['stability'],
            "similarity_boost": options['similarity_boost'],
            "style": options['style'],
            "use_speaker_boost": options['use_speaker_boost'],
            "speed": 1.0,
        }

    async def async_worker():
        ws = None
        receive_task = None
        current_speech_id = None
        response_finished = asyncio.Event()
        text_done_sent = False
        resampler = None
        pending_text: list[str] = []
        pending_text_sid: str | None = None

        def _reset_session_metrics() -> None:
            nonlocal response_finished, text_done_sent, resampler
            response_finished = asyncio.Event()
            text_done_sent = False
            resampler = (
                soxr.ResampleStream(pcm_sample_rate, 48000, 1, dtype='float32')
                if pcm_sample_rate != 48000
                else None
            )

        async def _close_ws(send_final_empty: bool = False, wait_for_final: bool = False) -> None:
            nonlocal ws, receive_task, text_done_sent
            if ws is not None:
                if send_final_empty and not text_done_sent:
                    try:
                        await ws.send(json.dumps({"text": ""}))
                        text_done_sent = True
                    except Exception as exc:
                        logger.debug("ElevenLabs WS final empty send failed: %s", exc)
                if wait_for_final:
                    try:
                        await asyncio.wait_for(response_finished.wait(), timeout=30.0)
                    except Exception:
                        pass
                try:
                    await asyncio.wait_for(ws.close(), timeout=0.5)
                except Exception:
                    pass
            ws = None
            if receive_task and not receive_task.done():
                receive_task.cancel()
                try:
                    await receive_task
                except (asyncio.CancelledError, Exception):
                    pass
            receive_task = None

        async def _open_ws(speech_id: str) -> None:
            nonlocal ws, receive_task, current_speech_id
            if not normalized_voice_id:
                raise RuntimeError("ElevenLabs voice_id is not configured")
            if not audio_api_key:
                raise RuntimeError("ElevenLabs API key is not configured")
            ws = await websockets.connect(
                ws_url,
                additional_headers={"xi-api-key": audio_api_key},
                ping_interval=None,
                close_timeout=0.5,
                max_size=10 * 1024 * 1024,
            )
            _reset_session_metrics()
            current_speech_id = speech_id
            receive_task = asyncio.create_task(_receive_ws_messages(speech_id))
            init_payload = {
                "text": " ",
                "voice_settings": _build_voice_settings(),
                "generation_config": {
                    "chunk_length_schedule": chunk_schedule,
                },
                "xi_api_key": audio_api_key,
            }
            await ws.send(json.dumps(init_payload))

        async def _receive_ws_messages(speech_id: str) -> None:
            try:
                async for message in ws:
                    audio_bytes = None
                    is_final = False
                    payload = None

                    if isinstance(message, bytes):
                        if message[:1] == b"{":
                            try:
                                payload = json.loads(message.decode("utf-8", errors="replace"))
                            except Exception:
                                payload = None
                        if payload is None:
                            audio_bytes = message
                    else:
                        try:
                            payload = json.loads(message)
                        except Exception:
                            preview = message if len(message) < 200 else message[:200] + "...<truncated>"
                            logger.warning("ElevenLabs WS recv non-JSON: %s", preview)
                            continue

                    if payload is not None:
                        event_type = payload.get("type")
                        audio_b64 = payload.get("audio") or payload.get("data") or payload.get("delta") or ""
                        if audio_b64:
                            try:
                                audio_bytes = base64.b64decode(audio_b64)
                            except Exception as exc:
                                logger.warning("ElevenLabs WS audio decode failed: %s", exc)
                                audio_bytes = None
                        is_final = bool(
                            payload.get("isFinal")
                            or payload.get("is_final")
                            or payload.get("final")
                            or event_type in {"final", "audio.done"}
                        )
                        if event_type == "error":
                            _enqueue_error(response_queue, {
                                "code": "API_REQUEST_FAILED",
                                "provider": "elevenlabs",
                                "message": f"ElevenLabs TTS API error: {payload}",
                            })
                            continue
                        if not audio_bytes and not is_final:
                            preview = message if isinstance(message, str) else repr(message[:200])
                            logger.debug(
                                "ElevenLabs WS recv unknown event type=%r raw=%s",
                                event_type,
                                preview,
                            )
                            continue

                    if audio_bytes:
                        usable_len = len(audio_bytes) - (len(audio_bytes) % 2)
                        if usable_len <= 0:
                            continue
                        if usable_len < len(audio_bytes):
                            audio_bytes = audio_bytes[:usable_len]
                        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                        response_queue.put(_resample_audio(audio_array, pcm_sample_rate, 48000, resampler))
                    if is_final:
                        response_finished.set()
                        break
            except websockets.exceptions.ConnectionClosed as exc:
                if exc.code != 1000:
                    logger.info(
                        "ElevenLabs WS closed: speech_id=%s code=%s reason=%r",
                        speech_id,
                        exc.code,
                        exc.reason,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("ElevenLabs WS receive failed: %s", exc)
            finally:
                response_finished.set()

        async def _send_text(text: str, speech_id: str, *, final: bool = False) -> None:
            if ws is None:
                raise RuntimeError("ElevenLabs WS is not connected")
            payload = {"text": text}
            if text and text.strip():
                payload["try_trigger_generation"] = True
            if final and text:
                payload["flush"] = True
            await ws.send(json.dumps(payload))

        async def _ensure_session(speech_id: str) -> None:
            nonlocal current_speech_id, pending_text_sid
            if current_speech_id != speech_id or ws is None:
                wait_for_previous_final = (
                    ws is not None
                    and text_done_sent
                    and not response_finished.is_set()
                )
                await _close_ws(send_final_empty=False, wait_for_final=wait_for_previous_final)
                if pending_text and pending_text_sid not in (None, speech_id):
                    logger.debug(
                        "ElevenLabs WS dropping stale pending text: pending_sid=%s current_sid=%s len=%d",
                        pending_text_sid,
                        speech_id,
                        sum(len(part) for part in pending_text),
                    )
                    pending_text.clear()
                    pending_text_sid = None
                await _open_ws(speech_id)

        try:
            if not normalized_voice_id:
                _enqueue_error(response_queue, {
                    "code": "TTS_VOICE_ID_MISSING",
                    "provider": "elevenlabs",
                    "message": "ElevenLabs voice_id is not configured",
                })
                response_queue.put(("__ready__", False))
                return
            if not audio_api_key:
                _enqueue_error(response_queue, {
                    "code": "API_KEY_MISSING",
                    "provider": "elevenlabs",
                    "message": "ElevenLabs API key is not configured",
                })
                response_queue.put(("__ready__", False))
                return
            response_queue.put(("__ready__", True))
            loop = asyncio.get_running_loop()
            while True:
                try:
                    sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                except Exception:
                    break

                if sid == TTS_SHUTDOWN_SENTINEL:
                    break

                if sid == "__interrupt__":
                    await _close_ws(send_final_empty=False, wait_for_final=False)
                    current_speech_id = None
                    pending_text.clear()
                    pending_text_sid = None
                    continue

                if sid is None:
                    if pending_text and pending_text_sid is not None:
                        target_sid = pending_text_sid
                        try:
                            if ws is None or current_speech_id != target_sid:
                                await _ensure_session(target_sid)
                            if ws is not None and current_speech_id == target_sid:
                                sent_text = "".join(pending_text)
                                await _send_text(sent_text, current_speech_id)
                                _record_tts_telemetry(options['model'], len(sent_text))
                        except Exception as exc:
                            logger.warning("ElevenLabs WS flush pending text failed: %s", exc)
                            _enqueue_error(response_queue, {
                                "code": "API_REQUEST_FAILED",
                                "provider": "elevenlabs",
                                "message": f"ElevenLabs pending text flush failed: {exc}",
                            })
                            response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                            await _close_ws(send_final_empty=False, wait_for_final=False)
                            current_speech_id = None
                            continue
                        pending_text.clear()
                        pending_text_sid = None
                    # 只发 final empty，让 receive_task 在后台继续把剩余音频抽完；
                    # 真正的 close 由下一个 sid 切换 / __interrupt__ / shutdown 触发，
                    # 避免主循环在这里阻塞最长 30s 拖慢下一句 utterance 首音延迟喵。
                    if ws is not None and not text_done_sent:
                        try:
                            await ws.send(json.dumps({"text": ""}))
                            text_done_sent = True
                        except Exception as exc:
                            logger.debug("ElevenLabs WS final empty send failed: %s", exc)
                    current_speech_id = None
                    pending_text.clear()
                    pending_text_sid = None
                    continue

                if tts_text and tts_text.strip():
                    payload_text = tts_text
                    if pending_text and pending_text_sid == sid:
                        payload_text = "".join(pending_text) + tts_text
                        pending_text.clear()
                        pending_text_sid = None
                    elif pending_text and pending_text_sid not in (None, sid):
                        logger.debug(
                            "ElevenLabs WS dropping cross-utterance pending text: pending_sid=%s current_sid=%s len=%d",
                            pending_text_sid,
                            sid,
                            sum(len(part) for part in pending_text),
                        )
                        pending_text.clear()
                        pending_text_sid = None
                    try:
                        await _ensure_session(sid)
                    except Exception as exc:
                        logger.warning("ElevenLabs WS ensure session failed: %s", exc)
                        pending_text.append(payload_text)
                        pending_text_sid = sid
                        await _close_ws(send_final_empty=False, wait_for_final=False)
                        current_speech_id = None
                        continue
                    try:
                        await _send_text(payload_text, current_speech_id)
                        _record_tts_telemetry(options['model'], len(payload_text))
                    except Exception as exc:
                        logger.warning("ElevenLabs WS send text failed: %s", exc)
                        pending_text.append(payload_text)
                        pending_text_sid = sid
                        await _close_ws(send_final_empty=False, wait_for_final=False)
                        current_speech_id = None

        except Exception as exc:
            logger.error("ElevenLabs WS Worker error: %s", exc, exc_info=True)
            response_queue.put(("__ready__", False))
        finally:
            try:
                await _close_ws(send_final_empty=False, wait_for_final=False)
            except Exception:
                pass

    try:
        asyncio.run(async_worker())
    except Exception as exc:
        logger.error("ElevenLabs WS Worker startup failed: %s", exc, exc_info=True)
        response_queue.put(("__ready__", False))

def _elevenlabs_clone_is_selected(ctx) -> bool:
    vm = ctx.voice_meta
    return bool(vm and vm.get('provider') == 'elevenlabs')

def _elevenlabs_clone_resolve(ctx):
    vm = ctx.voice_meta or {}
    logger.info("检测到 ElevenLabs 克隆音色: %s，使用 ElevenLabs TTS Worker", ctx.voice_id)
    elevenlabs_options = _get_elevenlabs_options()
    base_url = vm.get('elevenlabs_base_url') or elevenlabs_options['base_url']
    return partial(elevenlabs_tts_worker, base_url=base_url), _resolve_elevenlabs_api_key(ctx.cm), 'elevenlabs'
