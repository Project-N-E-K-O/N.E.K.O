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

"""Qwen realtime TTS worker."""

import numpy as np
import soxr
import time
import json
import base64
import websockets
import asyncio
import re

from urllib.parse import quote
from utils.config_manager import get_config_manager
from utils.dashscope_region import dashscope_ws_url_from_base

from .._infra import (
    AudioDoneEmitter,
    TTS_SHUTDOWN_SENTINEL,
    _resample_audio,
    classify_server_close,
    make_audio_jitter_buffer,
    _enqueue_error,
)
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_QWEN_REALTIME_TTS_MODEL = "qwen3-tts-flash-realtime"
_DASHSCOPE_DEFAULT_REALTIME_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
# Qwen TTS-family model IDs (qwen3-tts-flash-realtime, qwen3-tts-instruct-…,
# and future qwen3.x-tts-… generations). TTS_MODEL may instead contain an omnimodal name copied by default from
# REALTIME_MODEL, or another provider's model when this worker is a fallback (tts-1,
# cosyvoice-v3-plus); none can be sent to DashScope's realtime TTS endpoint.
_QWEN_TTS_MODEL_PATTERN = re.compile(r"^qwen[0-9.]*-tts", re.IGNORECASE)


def _resolve_qwen_realtime_tts_url() -> str:
    """Pick the realtime TTS WebSocket URL based on the current Qwen/Qwen Intl core config."""
    try:
        core_config = get_config_manager().get_core_config() or {}
    except Exception:
        core_config = {}
    base_ws_url = dashscope_ws_url_from_base(
        core_config.get("CORE_URL", ""),
        "realtime",
        _DASHSCOPE_DEFAULT_REALTIME_WS_URL,
    )
    configured_model = str(core_config.get("TTS_MODEL") or "").strip()
    if _QWEN_TTS_MODEL_PATTERN.match(configured_model):
        model = configured_model
    else:
        model = _QWEN_REALTIME_TTS_MODEL
    return f"{base_ws_url}?model={quote(model, safe='')}"

def qwen_realtime_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    Qwen realtime TTS worker (for default voices)
    Uses Aliyun's realtime TTS API. The model comes from TTS_MODEL when it
    names a Qwen TTS model; otherwise qwen3-tts-flash-realtime.
    
    Args:
        request_queue: multiprocess request queue receiving (speech_id, text) tuples
        response_queue: multiprocess response queue sending audio data (also used for the ready signal)
        audio_api_key: API key
        voice_id: voice ID, defaults to "Momo"
    """
    if not voice_id:
        voice_id = "Momo"

    from utils.language_utils import detect_tts_language_hint, TTS_LANG_DETECT_MIN_CHARS

    async def async_worker():
        """Async TTS worker main loop"""
        tts_url = _resolve_qwen_realtime_tts_url()
        ws = None
        current_speech_id = None
        receive_task = None
        session_ready = asyncio.Event()
        response_done = asyncio.Event()  # Mark whether the current response has completed
        buffer_committed = False  # Prevent duplicate buffer submission in one turn
        session_configured = False  # Whether session.update was sent on this connection, deferred until initial text arrives
        pending_text_buffer = ""  # Deferred text buffer for language detection on the first N characters
        # Streaming resampler (24kHz→48kHz), preserving state across chunk boundaries
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        # Qwen realtime can produce 1-2s inter-chunk gaps. A small jitter buffer
        # gives the client enough queued PCM to ride over short upstream stalls.
        # Shared AudioJitterBuffer, retaining legacy NEKO_QWEN_TTS_* overrides; see make_audio_jitter_buffer.
        qwen_audio_jitter = make_audio_jitter_buffer(response_queue, legacy_env_prefix="NEKO_QWEN_TTS")
        # Upstream done closes this turn's audio stream. Both receive tasks share one emitter,
        # and bound_speech_id captures this turn's sid at task creation: sid switching advances
        # current_speech_id before awaiting old-connection closure, so an old receive task observing a late
        # done would otherwise misattribute the preceding turn's completion to the new turn.
        # Also require buffer_committed: only after this turn's buffer is committed can done
        # indicate whole-turn completion; sentence-level upstream done would otherwise emit prematurely.
        audio_done = AudioDoneEmitter(response_queue)

        def _emit_audio_done(bound_speech_id) -> None:
            """Signal end-of-stream once the round's buffer commit was sent."""
            if buffer_committed:
                audio_done.emit(bound_speech_id)

        # Sender and receiver can each observe the same close frame; the first marks this turn rejected,
        # while the later observation only classifies it, making core count one rejection as one failure, symmetric with _step_protocol.
        rejected_speech_id = None

        def _report_server_rejection(exc, bound_speech_id) -> bool:
            """Forward a server refusal close as an error pinned to its own round.

            DashScope refuses account-level problems (arrears is 1007 + "…account
            is in good standing") with a bare close frame and no error event, on a
            connection that already reported ready. Without this the round is
            silent with nothing in the log and nothing on the frontend.
            """
            nonlocal rejected_speech_id
            payload = classify_server_close(exc)
            if payload is None:
                return False
            sid = str(bound_speech_id or "")
            if not sid:
                # Rejected prewarm connections have no turn and use the __ready__ failure path; enqueueing them without
                # turn ownership would make core charge the error to whichever turn is currently active.
                return False
            if sid == rejected_speech_id:
                return True
            rejected_speech_id = sid
            # Queue marker immediately before __error__: core uses it to bind errors to this rejected turn,
            # rather than current_speech_id, which may already identify the next turn.
            response_queue.put(("__tts_sentence_failed__", sid, ""))
            _enqueue_error(response_queue, payload)
            return True

        def build_config_message(lang_hint=None):
            """Build the session.update message; lang_hint='ja' specifies Japanese, anything else uses server-side Auto."""
            session = {
                "mode": "server_commit",
                "voice": voice_id,
                "response_format": "pcm",
                "sample_rate": 24000,
                "channels": 1,
                "bit_depth": 16,
            }
            if lang_hint == "ja":
                session["language_type"] = "Japanese"
            return {
                "type": "session.update",
                "event_id": f"event_{int(time.time() * 1000)}",
                "session": session,
            }

        async def _flush_deferred_config(force: bool = False) -> bool:
            """Send the deferred session.update on demand and append the buffered text out.

            - Below threshold and not force: returns False.
            - Already sent or after executing: returns True.
            """
            nonlocal session_configured, pending_text_buffer
            if session_configured:
                return True
            if not ws:
                return False
            if not force and len(pending_text_buffer) < TTS_LANG_DETECT_MIN_CHARS:
                return False
            lang_hint = detect_tts_language_hint(pending_text_buffer)
            try:
                await ws.send(json.dumps(build_config_message(lang_hint)))
            except Exception as e:
                logger.error(f"发送延迟 session.update 失败: {e}")
                _report_server_rejection(e, current_speech_id)
                return False
            session_configured = True
            try:
                await asyncio.wait_for(session_ready.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("Qwen TTS: 延迟 session.update 等待超时")
            if pending_text_buffer.strip():
                try:
                    await ws.send(json.dumps({
                        "type": "input_text_buffer.append",
                        "event_id": f"event_{int(time.time() * 1000)}",
                        "text": pending_text_buffer,
                    }))
                    _record_tts_telemetry("qwen", len(pending_text_buffer))
                except Exception as e:
                    # Failed append usually means disconnection; callers must not send commit afterward.
                    # Return False so sid=None/text paths continue and trigger reconnection.
                    logger.error(f"发送缓冲文本失败: {e}")
                    _report_server_rejection(e, current_speech_id)
                    return False
            pending_text_buffer = ""
            return True

        try:
            # Connect WebSocket
            headers = {"Authorization": f"Bearer {audio_api_key}"}

            ws = await websockets.connect(tts_url, additional_headers=headers)
            
            # Await and handle initial messages
            async def wait_for_session_ready():
                """Wait for session-creation confirmation"""
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")
                        
                        # Qwen TTS API returns session.updated rather than session.created
                        if event_type in ["session.created", "session.updated"]:
                            session_ready.set()
                            break
                        elif event_type == "error":
                            _enqueue_error(response_queue, event)
                            break
                except Exception as e:
                    _enqueue_error(response_queue, e)
            
            # Send prewarm configuration; after initial text arrives, _flush_deferred_config resends
            # the actual session.update with language hints.
            await ws.send(json.dumps(build_config_message(None)))
            session_configured = True

            # Wait for session readiness, with a 5-second timeout
            try:
                await asyncio.wait_for(wait_for_session_ready(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error("❌ 等待会话就绪超时")
                response_queue.put(("__ready__", False))
                return

            if not session_ready.is_set():
                logger.error("❌ 会话未能正确初始化")
                response_queue.put(("__ready__", False))
                return

            # Send readiness signal
            logger.info("Qwen TTS 已就绪，发送就绪信号")
            response_queue.put(("__ready__", True))

            # Initial receive task, recreated for every new speech_id
            async def receive_messages_initial(bound_speech_id):
                """Initial receive task"""
                nonlocal ws
                cancelled = False
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")

                        if event_type == "error":
                            # Idle timeout/session expiration: do not report errors; mark disconnected and reconnect on demand
                            err_msg = event.get("error", {}).get("message", "")
                            if "request timeout" in err_msg or "session_expired" in err_msg:
                                logger.debug(f"Qwen TTS 空闲超时，标记连接已断开: {err_msg}")
                                break
                            _enqueue_error(response_queue, event)
                        elif event_type == "response.audio.delta":
                            try:
                                audio_bytes = base64.b64decode(event.get("delta", ""))
                                audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                                # Use streaming resampling from 24000Hz to 48000Hz
                                qwen_audio_jitter.append(_resample_audio(audio_array, 24000, 48000, resampler))
                            except Exception as e:
                                logger.error(f"处理音频数据时出错: {e}")
                        elif event_type in ["response.done", "response.audio.done", "output.done"]:
                            # The server explicitly finished audio generation; set the completion flag
                            logger.debug(f"收到响应完成事件: {event_type}")
                            qwen_audio_jitter.flush()
                            # Prewarm connections bind None; the first real sid necessarily reconnects,
                            # so emit(None) silently skips. Retain the argument to keep both receive tasks symmetric.
                            _emit_audio_done(bound_speech_id)
                            response_done.set()
                except websockets.exceptions.ConnectionClosed as exc:
                    _report_server_rejection(exc, bound_speech_id)
                except asyncio.CancelledError:
                    cancelled = True
                    raise
                except Exception as e:
                    logger.error(f"消息接收出错: {e}")
                finally:
                    if not cancelled:
                        qwen_audio_jitter.flush()
                    # Receive loop exited on timeout/disconnection; clear connection state for on-demand main-loop reconnection
                    if ws:
                        try:
                            await ws.close()
                        except Exception:
                            pass
                        ws = None
                    session_ready.clear()

            receive_task = asyncio.create_task(receive_messages_initial(current_speech_id))

            # Main loop: process the request queue
            loop = asyncio.get_running_loop()
            pending = None  # Retain the current segment locally during disconnected retries to preserve order, without returning it to the shared queue
            while True:
                # Process retained disconnected segments before taking new requests
                if pending:
                    sid, tts_text = pending
                    pending = None
                else:
                    try:
                        sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                    except Exception:
                        break

                if sid == TTS_SHUTDOWN_SENTINEL:
                    break

                if sid == "__interrupt__":
                    # Interrupt: close immediately without commit or server acknowledgment
                    qwen_audio_jitter.begin_interrupt()
                    audio_done.begin_interrupt()  # Interrupted turns emit no audio_done; they use the separate cancellation channel
                    try:
                        if receive_task and not receive_task.done():
                            receive_task.cancel()
                            try:
                                await receive_task
                            except asyncio.CancelledError:
                                # Expected during interrupt teardown.
                                pass
                            except Exception as e:
                                logger.debug(f"Qwen TTS interrupted receive task cleanup failed: {e}")
                            receive_task = None
                        if ws:
                            try:
                                await ws.close()
                            except Exception as e:
                                logger.debug(f"Qwen TTS interrupted websocket close failed: {e}")
                            ws = None
                    finally:
                        session_ready.clear()
                        current_speech_id = None
                        buffer_committed = False
                        session_configured = False
                        pending_text_buffer = ""
                        qwen_audio_jitter.reset()
                        qwen_audio_jitter.end_interrupt()
                        audio_done.reset()
                        audio_done.end_interrupt()
                    continue

                if sid is None:
                    # Normal nonblocking finish: commit the buffer without awaiting acknowledgment or closing the connection
                    # Audio continues from receive_task into response_queue;
                    # the next speech_id switch or __interrupt__ closes the connection
                    if ws and current_speech_id is not None:
                        # If this turn has fewer than MIN_CHARS and session.update was not sent, force it once
                        if not session_configured:
                            if not await _flush_deferred_config(force=True):
                                # Failed flush (session.update/append send failure) means a dead connection;
                                # skip commit and await reconnection on the next speech_id
                                continue
                        # For short sentences, session.updated may arrive after _flush's 2s wait;
                        # send commit without depending on session_ready, since the server processes append + commit in order
                        # after session.updated is ready. Missing commit silently loses short sentences.
                        if not buffer_committed:
                            try:
                                await ws.send(json.dumps({
                                    "type": "input_text_buffer.commit",
                                    "event_id": f"event_{int(time.time() * 1000)}_commit"
                                }))
                                buffer_committed = True
                            except Exception as e:
                                logger.warning(f"提交缓冲区失败: {e}")
                                _report_server_rejection(e, current_speech_id)
                    continue
                
                # New speech ID: reconnect, matching speech_synthesis_worker logic
                # Close the old connection directly, interrupting old speech
                if current_speech_id != sid:
                    current_speech_id = sid
                    buffer_committed = False
                    session_configured = False
                    pending_text_buffer = ""
                    response_done.clear()
                    if ws:
                        try:
                            await ws.close()
                        except:  # noqa: E722
                            pass
                    if receive_task and not receive_task.done():
                        qwen_audio_jitter.flush()
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            pass
                    # Reset streaming state only after the old receive task fully stops: await ws.close() yields,
                    # letting old receive_task write late audio.delta; early reset would contaminate the next turn
                    resampler.clear()  # Reset resampler state; new-turn audio must not continue the previous turn
                    qwen_audio_jitter.reset()
                    audio_done.reset()  # Reset audio_done deduplication for the new turn.

                    # Open a connection, deferring session.update with language hints until initial text arrives
                    try:
                        ws = await websockets.connect(tts_url, additional_headers=headers)
                        session_ready.clear()

                        # Start a new receive task that also observes session.updated
                        async def receive_messages(bound_speech_id):
                            nonlocal ws
                            cancelled = False
                            try:
                                async for message in ws:
                                    event = json.loads(message)
                                    event_type = event.get("type")

                                    if event_type in ["session.created", "session.updated"]:
                                        session_ready.set()
                                    elif event_type == "error":
                                        # Idle timeout/session expiration: do not report errors; mark disconnected and reconnect on demand
                                        err_msg = event.get("error", {}).get("message", "")
                                        if "request timeout" in err_msg or "session_expired" in err_msg:
                                            logger.debug(f"Qwen TTS 空闲超时，标记连接已断开: {err_msg}")
                                            break
                                        _enqueue_error(response_queue, event)
                                    elif event_type == "response.audio.delta":
                                        try:
                                            audio_bytes = base64.b64decode(event.get("delta", ""))
                                            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                                            # Use streaming resampling from 24000Hz to 48000Hz
                                            qwen_audio_jitter.append(_resample_audio(audio_array, 24000, 48000, resampler))
                                        except Exception as e:
                                            logger.error(f"处理音频数据时出错: {e}")
                                    elif event_type in ["response.done", "response.audio.done", "output.done"]:
                                        # The server explicitly finished audio generation; set the completion flag
                                        logger.debug(f"收到响应完成事件: {event_type}")
                                        qwen_audio_jitter.flush()
                                        # flush already enqueued the tail audio; only now is this round's audio stream truly closed.
                                        _emit_audio_done(bound_speech_id)
                                        response_done.set()
                            except websockets.exceptions.ConnectionClosed as exc:
                                _report_server_rejection(exc, bound_speech_id)
                            except asyncio.CancelledError:
                                cancelled = True
                                raise
                            except Exception as e:
                                logger.error(f"消息接收出错: {e}")
                            finally:
                                if not cancelled:
                                    qwen_audio_jitter.flush()
                                # Receive loop exited on timeout/disconnection; clear connection state for on-demand main-loop reconnection
                                if ws:
                                    try:
                                        await ws.close()
                                    except Exception:
                                        pass
                                    ws = None
                                session_ready.clear()
                        
                        receive_task = asyncio.create_task(receive_messages(current_speech_id))

                    except Exception as e:
                        logger.error(f"重新建立连接失败: {e}")
                        if 'HTTP 503' in str(e):
                            _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
                        response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                        await asyncio.sleep(1.0)
                        continue

                # Validate text
                if not tts_text or not tts_text.strip():
                    continue

                if not ws:
                    # Idle timeout disconnected the connection; retain the segment and reset speech_id to trigger reconnection
                    # Flush residual jitter-buffer PCM before disconnecting: reconnect takes the speech_id-switch path and reset(),
                    # which otherwise discards this turn's below-threshold trailing audio; speech_id still matches here, preserving continuity
                    qwen_audio_jitter.flush()
                    current_speech_id = None
                    pending = (sid, tts_text)
                    continue

                # Before session.update, buffer MIN_CHARS for language detection
                if not session_configured:
                    pending_text_buffer += tts_text
                    ready = await _flush_deferred_config(force=False)
                    if not ready:
                        continue
                    # _flush_deferred_config already sent pending_text_buffer with append
                    continue

                if not session_ready.is_set():
                    # session.update sent but session unready due to timeout/disconnection; trigger reconnect
                    current_speech_id = None
                    pending = (sid, tts_text)
                    continue

                # Append text without immediate commit; wait for the response-completion termination signal
                try:
                    await ws.send(json.dumps({
                        "type": "input_text_buffer.append",
                        "event_id": f"event_{int(time.time() * 1000)}",
                        "text": tts_text
                    }))
                    _record_tts_telemetry("qwen", len(tts_text))
                except Exception as e:
                    logger.error(f"发送TTS文本失败: {e}")
                    # Report before clearing ws/current_speech_id below: this is the actual branch taken by
                    # the observed 1007 in incident logs, when the sender saw closure first.
                    _report_server_rejection(e, sid)
                    # Connection closed; mark invalid for the next reconnect
                    ws = None
                    current_speech_id = None  # Clear ID to force reconnection next time
                    session_ready.clear()
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
        
        except Exception as e:
            logger.error(f"Qwen实时TTS Worker错误: {type(e).__name__}: {e!r}", exc_info=True)
            if 'HTTP 503' in str(e):
                _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
            response_queue.put(("__ready__", False))
        finally:
            # Clean up resources
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
    
    # Run the asynchronous worker
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"Qwen实时TTS Worker启动失败: {type(e).__name__}: {e!r}", exc_info=True)
        response_queue.put(("__ready__", False))
