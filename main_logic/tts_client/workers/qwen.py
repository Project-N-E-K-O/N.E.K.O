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

import asyncio
import base64
from collections import deque
import json
import numpy as np
import queue as queue_module
import re
import soxr
import time
import websockets

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
_QWEN_MAX_RECONNECT_ATTEMPTS = 3
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
        deferred_requests = deque()
        pending = None  # Retain the current segment locally during disconnected retries to preserve order, without returning it to the shared queue
        reconnect_attempts = 0
        last_flush_error = None

        # server_commit can synthesize before the terminal commit. Replay a
        # lost socket's text only while no audio from it reached this worker.
        appended_text = []
        audio_started = False
        idle_closed = False

        def _fail_incomplete_speech(sid, error):
            nonlocal rejected_speech_id, pending_text_buffer, pending, reconnect_attempts, idle_closed
            rejected_speech_id = str(sid)
            response_queue.put(("__tts_sentence_failed__", str(sid), ""))
            _enqueue_error(response_queue, json.dumps({"code": "TTS_CONNECTION_FAILED", "msg": str(error)}))
            audio_done.emit(str(sid))
            pending_text_buffer = ""
            pending = None
            appended_text.clear()
            idle_closed = False
            reconnect_attempts = 0

        async def _record_transient_retry_or_exhaust(
            sid_value,
            *,
            pending_item,
            err: Exception,
        ) -> None:
            """Advance the bounded reconnect counter, or retire the speech round."""
            nonlocal pending, reconnect_attempts
            if (
                deferred_requests
                and deferred_requests[0][0] in {TTS_SHUTDOWN_SENTINEL, "__interrupt__"}
            ) or _defer_queued_work_until_control():
                return
            reconnect_attempts += 1
            if reconnect_attempts >= _QWEN_MAX_RECONNECT_ATTEMPTS:
                _fail_incomplete_speech(sid_value, err)
                return
            pending = pending_item
            response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
            delay = min(2.0, 0.5 * (2 ** (reconnect_attempts - 1)))
            await asyncio.sleep(delay)
            _defer_queued_work_until_control()

        async def _invalidate_current_socket(reason: str) -> None:
            """Retire the active socket while preserving speech identity and buffered text."""
            nonlocal ws, receive_task, session_configured, buffer_committed, idle_closed
            if receive_task and not receive_task.done():
                qwen_audio_jitter.flush()
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
                receive_task = None
            if ws:
                try:
                    await ws.close()
                except Exception as close_exc:
                    logger.debug("%s WebSocket 关闭失败: %s", reason, close_exc)
            ws = None
            idle_closed = False
            session_ready.clear()
            session_configured = False
            buffer_committed = False

        def _defer_queued_work_until_control() -> bool:
            """Move any queued shutdown or interrupt ahead of pending recovery work."""
            control_request = None
            while True:
                try:
                    queued_request = request_queue.get_nowait()
                except queue_module.Empty:
                    break
                except (AttributeError, NotImplementedError):
                    break
                if queued_request[0] in {
                    TTS_SHUTDOWN_SENTINEL,
                    "__interrupt__",
                }:
                    control_request = queued_request
                    break
                deferred_requests.append(queued_request)
            if control_request is None:
                return False
            deferred_requests.appendleft(control_request)
            return True

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
            nonlocal session_configured, pending_text_buffer, last_flush_error
            if session_configured:
                return True
            if not ws:
                return False
            if not force and len(pending_text_buffer) < TTS_LANG_DETECT_MIN_CHARS:
                return False
            last_flush_error = None
            lang_hint = detect_tts_language_hint(pending_text_buffer)
            try:
                await ws.send(json.dumps(build_config_message(lang_hint)))
            except Exception as e:
                logger.error(f"发送延迟 session.update 失败: {e}")
                last_flush_error = e
                _report_server_rejection(e, current_speech_id)
                await _invalidate_current_socket("发送延迟 session.update 失败后的")
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
                    if not audio_started:
                        appended_text.append(pending_text_buffer)
                    _record_tts_telemetry("qwen", len(pending_text_buffer))
                except Exception as e:
                    # Failed append usually means disconnection; roll back session_configured
                    # and retain pending_text_buffer so reconnection can replay it cleanly.
                    logger.error(f"发送缓冲文本失败: {e}")
                    last_flush_error = e
                    session_configured = False
                    _report_server_rejection(e, current_speech_id)
                    await _invalidate_current_socket("发送缓冲文本失败后的")
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
                text_staged_for_reconnect = False
                finish_requested = False
                # Control requests (__interrupt__ / shutdown) must preempt pending retries.
                if deferred_requests and deferred_requests[0][0] in {
                    TTS_SHUTDOWN_SENTINEL,
                    "__interrupt__",
                }:
                    sid, tts_text = deferred_requests.popleft()
                elif pending is not None:
                    sid, tts_text = pending
                    pending = None
                elif deferred_requests:
                    sid, tts_text = deferred_requests.popleft()
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
                        pending = None
                        deferred_requests.clear()
                        reconnect_attempts = 0
                        rejected_speech_id = None
                        audio_started = False
                        idle_closed = False
                        appended_text.clear()
                        last_flush_error = None
                        qwen_audio_jitter.reset()
                        qwen_audio_jitter.end_interrupt()
                        audio_done.reset()
                        audio_done.end_interrupt()
                    continue

                if rejected_speech_id is not None and (
                    (sid is not None and str(sid) == rejected_speech_id)
                    or (sid is None and str(current_speech_id or "") == rejected_speech_id)
                ):
                    if sid is None:
                        audio_done.emit(rejected_speech_id)
                    continue

                if sid is None:
                    # Normal nonblocking finish: commit the buffer without awaiting acknowledgment or closing the connection
                    # Audio continues from receive_task into response_queue;
                    # the next speech_id switch or __interrupt__ closes the connection
                    if current_speech_id is None or buffer_committed:
                        continue
                    if not pending_text_buffer.strip() and not appended_text and not audio_started:
                        continue
                    finish_requested = True
                    sid = current_speech_id

                # New speech ID or broken current socket: reconnect, preserving same-speech buffered text.
                is_new_speech = current_speech_id != sid
                if is_new_speech or not ws:
                    if is_new_speech:
                        current_speech_id = sid
                        pending_text_buffer = ""
                        reconnect_attempts = 0
                        audio_started = False
                        idle_closed = False
                        appended_text.clear()
                        last_flush_error = None
                    elif audio_started:
                        if not idle_closed:
                            _fail_incomplete_speech(sid, "Connection lost after partial audio delivery")
                            continue
                        idle_closed = False
                        if finish_requested and not pending_text_buffer.strip() and not appended_text:
                            qwen_audio_jitter.flush()
                            buffer_committed = True
                            audio_done.emit(str(sid))
                            continue
                        audio_started = False
                        appended_text.clear()
                    else:
                        idle_closed = False
                        pending_text_buffer = "".join(appended_text) + pending_text_buffer
                        appended_text.clear()
                    buffer_committed = False
                    session_configured = False
                    response_done.clear()
                    if (
                        not finish_requested
                        and tts_text
                        and tts_text.strip()
                    ):
                        pending_text_buffer += tts_text
                        text_staged_for_reconnect = True
                    if receive_task and not receive_task.done():
                        qwen_audio_jitter.flush()
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            pass
                        receive_task = None
                    if ws:
                        try:
                            await ws.close()
                        except Exception:
                            pass
                        ws = None
                    session_ready.clear()
                    # Reset streaming state only after the old receive task fully stops: await ws.close() yields,
                    # letting old receive_task write late audio.delta; early reset would contaminate the next turn.
                    if is_new_speech:
                        resampler.clear()
                        qwen_audio_jitter.reset()
                        audio_done.reset()

                    # Open a connection, deferring session.update with language hints until initial text arrives
                    try:
                        ws = await websockets.connect(tts_url, additional_headers=headers)
                        session_ready.clear()

                        if not is_new_speech and _defer_queued_work_until_control():
                            # The control branch owns socket teardown.
                            continue

                        # Start a new receive task that also observes session.updated
                        async def receive_messages(bound_speech_id, bound_ws):
                            nonlocal ws, audio_started, idle_closed
                            cancelled = False
                            try:
                                async for message in bound_ws:
                                    event = json.loads(message)
                                    event_type = event.get("type")

                                    if event_type in ["session.created", "session.updated"]:
                                        session_ready.set()
                                    elif event_type == "error":
                                        # Idle timeout/session expiration: do not report errors; mark disconnected and reconnect on demand
                                        err_msg = event.get("error", {}).get("message", "")
                                        if "request timeout" in err_msg or "session_expired" in err_msg:
                                            logger.debug(f"Qwen TTS 空闲超时，标记连接已断开: {err_msg}")
                                            if ws is bound_ws:
                                                idle_closed = True
                                            break
                                        _enqueue_error(response_queue, event)
                                    elif event_type == "response.audio.delta":
                                        try:
                                            audio_bytes = base64.b64decode(event.get("delta", ""))
                                            if audio_bytes:
                                                audio_started = True
                                                appended_text.clear()
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
                                if _report_server_rejection(exc, bound_speech_id) and buffer_committed:
                                    qwen_audio_jitter.flush()
                                    audio_done.emit(bound_speech_id)
                            except asyncio.CancelledError:
                                cancelled = True
                                raise
                            except Exception as e:
                                logger.error(f"消息接收出错: {e}")
                            finally:
                                if not cancelled:
                                    qwen_audio_jitter.flush()
                                # Receive loop exited on timeout/disconnection; clear connection state for on-demand main-loop reconnection
                                if ws is bound_ws:
                                    ws = None
                                    session_ready.clear()
                                    try:
                                        await bound_ws.close()
                                    except Exception:
                                        pass

                        receive_task = asyncio.create_task(receive_messages(current_speech_id, ws))

                    except Exception as e:
                        logger.error(f"重新建立连接失败: {e}")
                        ws = None
                        if _report_server_rejection(e, sid):
                            pending = None
                            pending_text_buffer = ""
                            audio_done.emit(str(sid))
                            continue
                        if 'HTTP 503' in str(e):
                            _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
                        await _record_transient_retry_or_exhaust(
                            sid,
                            pending_item=(
                                (None, None)
                                if finish_requested
                                else (sid, "" if text_staged_for_reconnect else tts_text)
                            ),
                            err=e,
                        )
                        continue

                if finish_requested:
                    flushed = await _flush_deferred_config(force=True)
                    if flushed and ws and not buffer_committed:
                        try:
                            await ws.send(json.dumps({
                                "type": "input_text_buffer.commit",
                                "event_id": f"event_{int(time.time() * 1000)}_commit"
                            }))
                            buffer_committed = True
                            if rejected_speech_id == str(sid):
                                # A close can arrive while the commit send yields.
                                qwen_audio_jitter.flush()
                                audio_done.emit(str(sid))
                            reconnect_attempts = 0
                        except Exception as e:
                            logger.warning(f"提交缓冲区失败: {e}")
                            rejected_now = _report_server_rejection(e, current_speech_id)
                            await _invalidate_current_socket("提交缓冲区失败后的")
                            if rejected_now:
                                pending_text_buffer = ""
                                audio_done.emit(str(current_speech_id))
                            else:
                                await _record_transient_retry_or_exhaust(
                                    current_speech_id,
                                    pending_item=(None, None),
                                    err=e,
                                )
                    elif not flushed:
                        if (
                            rejected_speech_id is not None
                            and str(current_speech_id) == rejected_speech_id
                        ):
                            pending_text_buffer = ""
                            audio_done.emit(rejected_speech_id)
                        elif pending_text_buffer.strip() or appended_text:
                            await _record_transient_retry_or_exhaust(
                                current_speech_id,
                                pending_item=(None, None),
                                err=last_flush_error or RuntimeError("deferred config flush failed"),
                            )
                    continue

                # Validate text (allow staged reconnect buffers even when tts_text is "")
                if (not tts_text or not tts_text.strip()) and not pending_text_buffer.strip():
                    continue

                if not ws:
                    # Idle timeout disconnected the connection; retain the segment without clearing current_speech_id.
                    qwen_audio_jitter.flush()
                    pending = (sid, "" if text_staged_for_reconnect else tts_text)
                    continue

                # Before session.update, buffer MIN_CHARS for language detection
                if not session_configured:
                    if not text_staged_for_reconnect and tts_text:
                        pending_text_buffer += tts_text
                    ready = await _flush_deferred_config(force=False)
                    if not ready:
                        if not ws and not (
                            rejected_speech_id is not None
                            and str(sid) == rejected_speech_id
                        ):
                            await _record_transient_retry_or_exhaust(
                                sid,
                                pending_item=(sid, ""),
                                err=last_flush_error or RuntimeError("deferred config flush failed"),
                            )
                        continue
                    reconnect_attempts = 0
                    # _flush_deferred_config already sent pending_text_buffer with append
                    continue

                if not session_ready.is_set():
                    # session.update sent but session unready due to timeout/disconnection; trigger reconnect
                    await _invalidate_current_socket("等待 session_ready 失败后的")
                    pending = (sid, "" if text_staged_for_reconnect else tts_text)
                    continue

                # Append text without immediate commit; wait for the response-completion termination signal
                try:
                    await ws.send(json.dumps({
                        "type": "input_text_buffer.append",
                        "event_id": f"event_{int(time.time() * 1000)}",
                        "text": tts_text
                    }))
                    if not audio_started:
                        appended_text.append(tts_text)
                    _record_tts_telemetry("qwen", len(tts_text))
                    reconnect_attempts = 0
                except Exception as e:
                    logger.error(f"发送TTS文本失败: {e}")
                    # Report before invalidating below: this is the actual branch taken by
                    # the observed 1007 in incident logs, when the sender saw closure first.
                    rejected_now = _report_server_rejection(e, sid)
                    await _invalidate_current_socket("发送TTS文本失败后的")
                    if not rejected_now:
                        await _record_transient_retry_or_exhaust(
                            sid,
                            pending_item=(sid, tts_text),
                            err=e,
                        )
        
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
