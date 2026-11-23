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

"""xAI Grok streaming TTS worker."""

import numpy as np
import soxr
import json
import base64
import websockets
import asyncio
import time
from collections import deque

from utils.tts.native_voice_registry import make_native_tts_resolver, register_tts_worker_resolver

from .._infra import (
    AudioDoneEmitter,
    TTS_PRECONNECT_SENTINEL,
    TTS_SHUTDOWN_SENTINEL,
    _resample_audio,
    make_audio_jitter_buffer,
    _enqueue_error,
)
from .._preconnect import (
    FINISHED_ROUND_QUIET_SECONDS,
    SpareSocket,
    defer_queued_work_until_control,
    ws_may_hold_connection,
)
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_XAI_TTS_HOST = "api.x.ai"
# Every socket closes with this bound: a half-open connection must not hold the
# main loop (or a later connect waiting for the close) for websockets' default 10s.
_XAI_TTS_CLOSE_TIMEOUT_SECONDS = 0.5

# xAI documents individual deltas as capped at 15,000 characters.
# Accumulated pending_text/merged long utterances may exceed this limit; split before sending.
_XAI_TTS_DELTA_CAP = 15000

def _grok_chunk_text_delta(text: str, cap: int = _XAI_TTS_DELTA_CAP) -> list[str]:
    """Split text that may exceed the xAI text.delta cap into multiple sequentially sent segments.
    Each returned segment has length <= cap; empty input returns an empty list."""
    if not text:
        return []
    if len(text) <= cap:
        return [text]
    return [text[i:i + cap] for i in range(0, len(text), cap)]

def grok_streaming_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    xAI Grok streaming TTS worker (wss://api.x.ai/v1/tts)

    Protocol traits (vs. step):
      - no session handshake / no tts.create; push text.delta right after connecting
      - all configuration goes through query params (voice/language/codec/sample_rate)
      - with codec=pcm the audio is raw 16-bit little-endian, no WAV header
      - language is required; use auto for server-side detection, skipping client-side language detection

    Args:
        request_queue: multiprocess request queue receiving (speech_id, text) tuples
        response_queue: multiprocess response queue sending audio data and the ready signal
        audio_api_key: xAI API key
        voice_id: built-in voice (eve/ara/leo/rex/sal) / alias (male, female-voice
            labels, etc.) / custom 8-char voice id / empty. The routing layer
            (native_voice_registry) recognizes aliases as native; the worker then
            normalizes to the xAI canonical id here, because the xAI endpoint's
            voice query param only accepts canonical ids or custom 8-char ids,
            not aliases.
    """
    from utils.tts.providers.grok import normalize_grok_tts_voice
    # Strip first: whitespace-only input like '   ' equals empty; otherwise not voice_id
    # fails and residual whitespace reaches xAI's voice query parameter, breaking synthesis.
    voice_id = (voice_id or "").strip()
    canonical_voice, recognized = normalize_grok_tts_voice(voice_id)
    if recognized or not voice_id:
        # Recognized native IDs/aliases use normalized canonical values;
        # empty input already normalizes to default eve.
        voice_id = canonical_voice
    # Otherwise unrecognized nonempty input is a user-defined 8-character voice_id, passed unchanged to xAI.

    async def async_worker():
        from urllib.parse import urlencode
        params = urlencode({
            "voice": voice_id,
            "language": "auto",
            "codec": "pcm",
            "sample_rate": 24000,
        })
        tts_url = f"wss://{_XAI_TTS_HOST}/v1/tts?{params}"
        headers = {"Authorization": f"Bearer {audio_api_key}"}

        ws = None
        current_speech_id = None
        receive_task = None
        text_done_sent = False
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        # Symmetric with step/qwen: xAI's first audio interchunk gap is large, so shared
        # jitter buffers accumulate initial lead to cover the opening words.
        audio_jitter = make_audio_jitter_buffer(response_queue)
        # audio.done closes this turn's audio stream. Freeze bound_speech_id when creating tasks,
        # preventing late old receive-task audio.done from being attributed to new turns after sid changes.
        # Also require text_done_sent: audio.done can finalize the whole turn only after text.done;
        # upstream per-delta audio.done otherwise causes premature completion.
        audio_done = AudioDoneEmitter(response_queue)

        def _emit_audio_done(bound_speech_id) -> None:
            """Signal end-of-stream once the round's terminal text was sent."""
            if text_done_sent:
                audio_done.emit(bound_speech_id)
        # Buffer unsent text chunks for the same utterance after reconnect failure. When the next
        # same-sid chunk reconnects successfully, prepend the buffer to the first text.delta,
        # preventing the reconnect-triggering chunk from disappearing at continue;
        # especially important for single-chunk replies.
        # pending_text_sid binds buffered content to its originating sid. Across utterances, including sid changes,
        # interruption, or failed end-of-utterance flush, discard old pending text or it contaminates
        # the next utterance's first text.delta, audibly mixing previous-turn content
        # into the new reply.
        pending_text: list[str] = []
        pending_text_sid: str | None = None
        # Requests read ahead of a queued interrupt/shutdown (see
        # defer_queued_work_until_control); consumed before the queue.
        deferred_requests = deque()
        # Loop time of the last audio chunk, for _round_socket_finished.
        last_audio_at = None

        def _defer_control() -> bool:
            return defer_queued_work_until_control(request_queue, deferred_requests)

        async def _connect_socket():
            return await websockets.connect(
                tts_url,
                additional_headers=headers,
                close_timeout=_XAI_TTS_CLOSE_TIMEOUT_SECONDS,
            )

        # Socket opened ahead of the next speech on core's preconnect request,
        # bound to no speech until a connect adopts it. xAI has no session
        # handshake (configuration rides on the URL), so the spare is the
        # whole connection.
        spare = SpareSocket(connect=_connect_socket, defer_control=_defer_control)

        async def _open_speech_socket(speech_id):
            """Adopt the spare or connect, once retired sockets are closed.

            Returns None when a queued interrupt or shutdown preempts the wait
            for those closes; it is then at the front of deferred_requests.
            """
            connect_started = time.perf_counter()
            try:
                adopted = await spare.take()
            except asyncio.TimeoutError:
                adopted = None
            if adopted is not None:
                new_ws = adopted[0]
            else:
                if not await spare.await_cleanup():
                    return None
                new_ws = await _connect_socket()
            if _defer_control():
                # An interrupt/shutdown queued while connecting owns this
                # boundary: nothing goes out on this socket.
                spare.close_tracked(new_ws)
                return None
            logger.info(
                "[voice-chain] stage=tts_socket speech_id=%s host=%s preconnected=%s wait_ms=%d",
                speech_id,
                _XAI_TTS_HOST,
                adopted is not None,
                int((time.perf_counter() - connect_started) * 1000),
            )
            return new_ws

        def _round_socket_finished() -> bool:
            """Whether nothing is still expected on the socket ``ws`` holds.

            True for the warmup socket (bound to no speech; the first speech
            replaces it anyway) and for a round whose text.done went out,
            whose end the core was already told about (audio_done), and that
            has been quiet for FINISHED_ROUND_QUIET_SECONDS.
            """
            if current_speech_id is None:
                return True
            if not text_done_sent or not audio_done.emitted(current_speech_id):
                return False
            if last_audio_at is None:
                return True
            return asyncio.get_running_loop().time() - last_audio_at >= FINISHED_ROUND_QUIET_SECONDS

        async def _retire_finished_socket() -> None:
            """Close a finished round's socket so a spare can replace it.

            The next speech would close it anyway before connecting; doing it
            when the next reply starts lets the spare open without a second
            connection. Closed in the background, tracked by the spare so the
            next connect still waits for it.
            """
            nonlocal ws, receive_task
            old_ws = ws
            ws = None
            if receive_task and not receive_task.done():
                # Released as the next speech used to before replacing it.
                audio_jitter.flush()
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug(f"xAI TTS retired receive task cleanup failed: {e}")
            receive_task = None
            spare.close_tracked(old_ws)

        async def receive_messages(bound_speech_id):
            # xAI may send binary raw PCM or JSON-wrapped base64 audio.delta;
            # Documentation is unclear, so retain both paths. Prefer 'delta', the OpenAI Realtime standard,
            # with 'audio' fallback against future xAI naming changes.
            nonlocal last_audio_at
            cancelled = False
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        last_audio_at = asyncio.get_running_loop().time()
                        try:
                            audio_array = np.frombuffer(message, dtype=np.int16)
                            audio_jitter.append(_resample_audio(audio_array, 24000, 48000, resampler))
                        except Exception as e:
                            logger.error(f"xAI TTS 二进制音频解码失败: {e}")
                        continue
                    try:
                        event = json.loads(message)
                    except Exception as e:
                        preview = message if len(message) < 200 else message[:200] + "...<truncated>"
                        logger.warning(f"xAI TTS recv (non-JSON): {preview} err={e}")
                        continue
                    event_type = event.get("type")
                    if event_type == "audio.delta":
                        audio_b64 = event.get("delta") or event.get("audio") or ""
                        if not audio_b64:
                            logger.warning(f"xAI TTS audio.delta 无音频字段，event keys={list(event.keys())}")
                            continue
                        last_audio_at = asyncio.get_running_loop().time()
                        try:
                            audio_bytes = base64.b64decode(audio_b64)
                            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                            audio_jitter.append(_resample_audio(audio_array, 24000, 48000, resampler))
                        except Exception as e:
                            logger.error(f"xAI TTS 音频解码失败: {e}")
                    elif event_type == "audio.done":
                        # This round's audio ended: release tails below the steady buffer threshold.
                        audio_jitter.flush()
                        # flush already enqueued the tail audio; only now is this round's audio stream truly closed.
                        _emit_audio_done(bound_speech_id)
                    elif event_type == "error":
                        logger.error(f"xAI TTS server error: {event}")
                        _enqueue_error(response_queue, event)
                    else:
                        # Log unknown events at INFO so new event types are immediately visible.
                        preview = message if len(message) < 200 else message[:200] + "...<truncated>"
                        logger.info(f"xAI TTS recv unknown type={event_type!r} raw={preview}")
            except websockets.exceptions.ConnectionClosed as e:
                # Log abnormal closures only, 1006 or application 4xxx; keep normal 1000 silent,
                # avoiding logs on every worker-initiated sid-switch close.
                if e.code != 1000:
                    logger.info(f"xAI TTS WebSocket closed: code={e.code} reason={e.reason!r}")
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as e:
                logger.error(f"xAI TTS 接收出错: {type(e).__name__}: {e}")
            finally:
                if not cancelled:
                    audio_jitter.flush()

        try:
            # close_timeout=0.5 bounds handshakes, preventing half-open connections from blocking main loops for seconds
            # during sid changes/interruption/finally cleanup and delaying later TTS.
            ws = await _connect_socket()
            # Warmup connections carry no speech; the first real sid necessarily reconnects, so bind None.
            receive_task = asyncio.create_task(receive_messages(current_speech_id))

            logger.info("xAI Grok TTS 已就绪，发送就绪信号")
            response_queue.put(("__ready__", True))

            loop = asyncio.get_running_loop()
            while True:
                if deferred_requests:
                    sid, tts_text = deferred_requests.popleft()
                else:
                    try:
                        sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                    except Exception:
                        break

                if sid == TTS_SHUTDOWN_SENTINEL:
                    break

                if sid == TTS_PRECONNECT_SENTINEL:
                    # A reply's LLM request just started; have its socket ready
                    # before the first text. A finished round's socket (kept
                    # until the next speech) is retired first so the spare is
                    # never a second connection.
                    if ws_may_hold_connection(ws) and _round_socket_finished():
                        await _retire_finished_socket()
                    spare.start(lambda: ws_may_hold_connection(ws), after_cleanup=True)
                    continue

                if sid == "__interrupt__":
                    audio_jitter.begin_interrupt()
                    audio_done.begin_interrupt()  # Interrupted turns omit audio_done, using the independent cancellation channel.
                    try:
                        if receive_task and not receive_task.done():
                            receive_task.cancel()
                            try:
                                await receive_task
                            except asyncio.CancelledError:
                                # Expected during interrupt teardown.
                                pass
                            except Exception as e:
                                logger.debug(f"xAI TTS interrupted receive task cleanup failed: {e}")
                            receive_task = None
                        if ws:
                            # Via the spare's tracked close: an inline wait_for would cancel a slow close and leave the socket CLOSING untracked, beside the next connect.
                            spare.close_tracked(ws)
                            ws = None
                    finally:
                        current_speech_id = None
                        text_done_sent = False
                        pending_text.clear()
                        pending_text_sid = None
                        deferred_requests.clear()
                        audio_jitter.reset()  # Interruption: discard buffered audio not yet released.
                        audio_jitter.end_interrupt()
                        audio_done.reset()
                        audio_done.end_interrupt()
                    continue

                if sid is None:
                    # Current speech text ends. If WebSocket remains dead after repeated reconnect failures
                    # but pending contains same-sid text, typically a single-chunk short reply whose initial reconnect
                    # failed before the next sid=None ending signal,
                    # make one last reconnect attempt, send pending, then text.done.
                    # Give up on failure; this extra attempt protects short replies from transient network loss.
                    if ws is None and pending_text and pending_text_sid is not None:
                        try:
                            ws = await _open_speech_socket(pending_text_sid)
                            if ws is None:
                                raise RuntimeError("preempted by a queued interrupt/shutdown")
                            # Bind pending_text_sid; current_speech_id is assigned only on the next line.
                            receive_task = asyncio.create_task(receive_messages(pending_text_sid))
                            current_speech_id = pending_text_sid
                            text_done_sent = False
                            resampler.clear()
                            audio_jitter.reset()
                            logger.info("xAI TTS last-chance reconnect on utterance end succeeded")
                        except Exception as e:
                            logger.warning(f"xAI TTS last-chance reconnect 失败，pending 丢弃: {e}")

                    if ws and current_speech_id is not None and not text_done_sent:
                        if pending_text and pending_text_sid == current_speech_id:
                            try:
                                for delta in _grok_chunk_text_delta("".join(pending_text)):
                                    await ws.send(json.dumps({"type": "text.delta", "delta": delta}))
                            except Exception as e:
                                # send failure may indicate a half-dead socket from last-chance reconnect,
                                # closed by the server between utterances. Retry with a fresh reconnect
                                # to rescue pending text and avoid silent truncated endings.
                                logger.warning(f"flush pending_text 首次失败，尝试重连重试: {e}")
                                if ws:
                                    # Via the spare's tracked close: an inline wait_for would cancel a slow close and leave the socket CLOSING untracked, beside the next connect.
                                    spare.close_tracked(ws)
                                    # Cleared so a failed retry below never
                                    # leaves the closed socket looking usable.
                                    ws = None
                                if receive_task and not receive_task.done():
                                    receive_task.cancel()
                                    try:
                                        await receive_task
                                    except asyncio.CancelledError:
                                        pass
                                try:
                                    ws = await _open_speech_socket(current_speech_id)
                                    if ws is None:
                                        raise RuntimeError("preempted by a queued interrupt/shutdown")
                                    receive_task = asyncio.create_task(receive_messages(current_speech_id))
                                    # New receive_task: like last-chance reconnect, reset unflushed old-connection
                                    # audio before retry audio. Only previously live, appended paths can retain data;
                                    # last-chance reconnect paths already have empty buffers.
                                    resampler.clear()
                                    audio_jitter.reset()
                                    for delta in _grok_chunk_text_delta("".join(pending_text)):
                                        await ws.send(json.dumps({"type": "text.delta", "delta": delta}))
                                    logger.info("flush pending_text 重连重试成功")
                                except Exception as e2:
                                    logger.warning(f"flush pending_text 重连重试仍失败，pending 丢失: {e2}")
                        if ws is None:
                            # The retry was preempted or failed; a queued
                            # interrupt/shutdown or the next speech takes over.
                            pending_text.clear()
                            pending_text_sid = None
                            continue
                        try:
                            await ws.send(json.dumps({"type": "text.done"}))
                            text_done_sent = True
                        except Exception as e:
                            logger.warning(f"发送 text.done 失败: {e}")
                    pending_text.clear()
                    pending_text_sid = None
                    continue

                # New speech_id: close old/open new, symmetric with step-worker reconnect policy.
                if current_speech_id != sid:
                    # Always close old connections/cancel old receive_task against leaks, but
                    # defer committing current_speech_id/text_done_sent/resampler changes
                    # until connect succeeds. Otherwise one transient connect failure prematurely advances
                    # sid, and subsequent same-sid chunks silently hit ``if not ws: continue``
                    # until a new sid retries, muting the entire utterance.
                    if ws:
                        # Closed in the background via the spare's tracked close: an inline wait_for would cancel a slow close and leave the socket CLOSING untracked, beside the next connect; the
                        # socket's own 0.5s close_timeout bounds it, and the
                        # connect below waits for it (preemptible).
                        spare.close_tracked(ws)
                        ws = None
                    if receive_task and not receive_task.done():
                        audio_jitter.flush()
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            pass
                        receive_task = None
                    try:
                        ws = await _open_speech_socket(sid)
                        if ws is None:
                            # A queued interrupt/shutdown owns this boundary.
                            continue
                        # Bind sid now; commit current_speech_id only after connect succeeds.
                        receive_task = asyncio.create_task(receive_messages(sid))
                    except Exception as e:
                        logger.error(f"xAI TTS 重连失败: {e}")
                        if 'HTTP 503' in str(e):
                            _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
                        response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                        # Buffer the current chunk or continue loses it permanently;
                        # single-chunk replies become entirely silent. Bind sid so later
                        # changes can discard pending from another utterance before sending,
                        # avoiding cross-utterance contamination.
                        if tts_text and tts_text.strip():
                            if pending_text_sid != sid:
                                # Discard residue from the previous failed utterance and rebind to current sid.
                                pending_text.clear()
                            pending_text.append(tts_text)
                            pending_text_sid = sid
                        await asyncio.sleep(1.0)
                        # Leave current_speech_id unchanged so the next same-sid chunk retries reconnection.
                        continue
                    # Commit sid changes/reset state only after connect succeeds.
                    current_speech_id = sid
                    text_done_sent = False
                    resampler.clear()
                    audio_jitter.reset()  # Reset jitter-buffer lead for the new turn.
                    audio_done.reset()  # Reset audio_done deduplication for the new turn.

                if not tts_text or not tts_text.strip():
                    continue
                if text_done_sent:
                    continue
                if not ws:
                    continue

                # Prepend same-sid text buffered by failed reconnects before sending,
                # retaining original chunk order. Discard different-sid pending as
                # cross-utterance residue to prevent contamination.
                if pending_text and pending_text_sid == current_speech_id:
                    payload_text = "".join(pending_text) + tts_text
                    pending_text.clear()
                    pending_text_sid = None
                else:
                    if pending_text:
                        logger.debug(
                            "xAI TTS 丢弃跨 utterance 的残留 pending_text (sid=%s, current=%s, len=%d)",
                            pending_text_sid, current_speech_id, sum(len(x) for x in pending_text),
                        )
                        pending_text.clear()
                        pending_text_sid = None
                    payload_text = tts_text

                try:
                    # Use 'delta', the OpenAI Realtime standard; xAI documentation names the body
                    # "deltas"——"Individual deltas are capped at 15,000 characters"）。
                    # 'text' makes servers silently treat input as empty, synthesize zero bytes, and emit audio.done.
                    # Merged buffers can exceed 15k; split sequentially by cap. xAI processes streaming
                    # synthesis in arrival order, making multiple deltas equivalent to one.
                    for delta in _grok_chunk_text_delta(payload_text):
                        await ws.send(json.dumps({"type": "text.delta", "delta": delta}))
                    _record_tts_telemetry("grok", len(payload_text))
                except Exception as e:
                    logger.error(f"发送 text.delta 失败: {type(e).__name__}: {e}")
                    # On send failure, restore text to current-sid pending for retry after reconnection.
                    pending_text.append(payload_text)
                    pending_text_sid = current_speech_id
                    ws = None
                    current_speech_id = None
                    # Symmetric with step/qwen: synchronously cancel old receive_task on send failure,
                    # preventing zombie receivers from putting residual audio into response_queue
                    # during the gap. The dead connection naturally yields
                    # ConnectionClosed; cancellation only accelerates cleanup.
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
                    receive_task = None

        except Exception as e:
            logger.error(f"xAI Grok TTS Worker 错误: {type(e).__name__}: {e!r}", exc_info=True)
            if 'HTTP 503' in str(e):
                _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
            response_queue.put(("__ready__", False))
        finally:
            try:
                spare.discard()
                await spare.drain(2.5)
            except Exception as e:
                logger.debug(f"关闭预建 TTS socket 失败: {e}")
            if receive_task and not receive_task.done():
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
            if ws:
                try:
                    await asyncio.wait_for(ws.close(), timeout=0.5)
                except Exception:
                    pass

    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"xAI Grok TTS Worker 启动失败: {type(e).__name__}: {e!r}", exc_info=True)
        response_queue.put(("__ready__", False))

# Recognizes TTS_PRECONNECT_SENTINEL (see .._preconnect.SpareSocket).
grok_streaming_tts_worker.supports_preconnect = True

# Built-in xAI Grok voices eve/ara/leo/rex/sal also use CORE_API_KEY.
# Without registration, nonempty voice_id makes core._has_custom_tts() return True,
# routing get_tts_worker() to cosyvoice_vc_tts_worker before the default
# core_api_type=='grok' branch, causing silence/auth failures; PR #1306 Codex review.
register_tts_worker_resolver(
    'grok',
    make_native_tts_resolver(grok_streaming_tts_worker, 'core_api_key'),
)
