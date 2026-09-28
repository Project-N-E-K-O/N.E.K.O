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

"""Shared Step-shaped realtime TTS wire protocol.

Provider registration belongs to the dedicated ``step`` and ``free`` worker
modules.  Keeping the transport loop here avoids duplicating the websocket,
resampling, interruption, and jitter-buffer machinery while preserving one
public worker module per provider.
"""

import numpy as np
import soxr
import json
import base64
import websockets
import io
import wave
import asyncio
import queue as queue_module
from collections import deque

from utils.config_manager import get_config_manager
from utils.tts.providers.stepfun import STEPFUN_TTS_DEFAULT_VOICE, get_stepfun_tts_default_voice, normalize_stepfun_tts_voice

from .._infra import (
    AudioDoneEmitter,
    TTS_SHUTDOWN_SENTINEL,
    _resample_audio,
    make_audio_jitter_buffer,
    _enqueue_error,
)
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_FINISH_RETRY_SENTINEL = "__step_finish_retry__"
# Request-queue ids that are control messages, not speech rounds.
_NON_SPEECH_REQUEST_IDS = frozenset({TTS_SHUTDOWN_SENTINEL, "__interrupt__", _FINISH_RETRY_SENTINEL})

# Lanlan free servers finish the WebSocket handshake first and only then apply
# their limits, so a rejection arrives as a close frame, never as an HTTP
# status. Both daily-quota reasons ("... limit reached" at connect time and
# "... limit reached for today" mid-session) share this substring.
_LANLAN_QUOTA_CLOSE_REASON = "time limit reached"
_LANLAN_ACCESS_DENIED_CLOSE_REASON = "access denied"
# Rejections no later round can get past; the worker exits so the core's
# no-retry gate takes over instead of every reply reconnecting.
_PERMANENT_REJECTION_CODES = frozenset({"API_ACCESS_DENIED", "TTS_CONFIG_INVALID"})


def _classify_lanlan_server_close(exc) -> dict | None:
    """Map a Lanlan free-server rejection close frame to a structured TTS error.

    Returns None for anything that is not one of the server's explicit
    rejections (our own closes, network drops, 1011, unknown codes), leaving
    those on the existing retry path.
    """
    if not isinstance(exc, websockets.exceptions.ConnectionClosed):
        return None
    received = getattr(exc, "rcvd", None)
    if received is None:
        return None
    close_code = received.code
    reason = str(received.reason or "")
    lowered = reason.lower()
    if close_code == 1008 and _LANLAN_QUOTA_CLOSE_REASON in lowered:
        error_code = "API_QUOTA_TIME"
    elif close_code == 1008 and _LANLAN_ACCESS_DENIED_CLOSE_REASON in lowered:
        error_code = "API_ACCESS_DENIED"
    elif close_code == 1013:
        # Per-minute connect rate or per-IP concurrency: transient.
        error_code = "API_RATE_LIMIT"
    elif close_code == 4004:
        error_code = "TTS_CONFIG_INVALID"
    else:
        return None
    return {
        "code": error_code,
        "data": {"close_code": close_code, "message": reason},
    }


def _adjust_free_tts_url(url: str) -> str:
    """Region substitution for the free TTS URL: delegates to ConfigManager._adjust_free_api_url."""
    try:
        return get_config_manager()._adjust_free_api_url(url, True)
    except Exception:
        return url

def _get_tts_language_code() -> str:
    """Get the language_code required by the lanlan.app TTS server.

    Implementation converges on utils.language_utils.get_tts_language_code — the
    core/realtime path and the TTS server path share the same BCP-47 mapping table
    to avoid drift.
    """
    from utils.language_utils import get_tts_language_code
    return get_tts_language_code()

def _build_step_tts_create_data(sid_: str, voice_id: str, lang_hint, is_lanlan_app: bool) -> dict:
    """Assemble the shared wire payload from the endpoint and language hint."""
    data = {
        "session_id": sid_,
        "voice_id": voice_id,
        "response_format": "wav",
        "sample_rate": 24000,
    }
    if is_lanlan_app:
        # 发真实 voice_id（data 里已带传入值），由 www.lanlan.app 服务端透传给
        # Gemini 并做映射；不再客户端硬覆盖成 Leda。
        data["language_code"] = "ja-JP" if lang_hint == "ja" else _get_tts_language_code()
    else:
        # lanlan.tech free 与标准 StepFun 当前都接受 voice_label。
        if lang_hint == "ja":
            data["voice_label"] = {"language": "日语"}
    return data

def run_step_protocol_tts_worker(
    request_queue,
    response_queue,
    audio_api_key,
    voice_id,
    *,
    provider_key: str,
):
    """
    Run the Step-shaped streaming TTS protocol for one explicit provider.

    Args:
        request_queue: multiprocess request queue receiving (speech_id, text) tuples
        response_queue: multiprocess response queue sending audio data (also used for the ready signal)
        audio_api_key: API key
        voice_id: voice ID; defaults to the StepFun config in api_providers.json
    """
    if provider_key not in {"step", "free"}:
        raise ValueError(f"unsupported Step-shaped TTS provider: {provider_key}")
    is_free = provider_key == "free"
    provider_label = "Lanlan free" if is_free else "StepFun"

    # free + livestream 子模式：voice_id 优先取 api_providers.json 的
    # livestream_config.voice_id（绕过 caller 的 free_voices preset 路径）。
    # 多进程 worker 这里独立 import，与主进程对偶。
    default_voice_id = get_stepfun_tts_default_voice(provider_key)

    if is_free:
        try:
            from utils.api_config_loader import is_livestream_active, get_livestream_config
            if is_livestream_active():
                ls_voice = get_livestream_config().get('voice_id', '')
                if ls_voice:
                    voice_id = ls_voice
                else:
                    # 半配置状态（启用了但没填 voice_id）：明确告警，避免误以为
                    # 直播音色已生效却实际还在用 caller 传入或默认 preset
                    logger.warning(
                        "livestream_config.enabled=true 但 voice_id 为空，"
                        f"继续使用 caller 传入或默认音色: {voice_id or default_voice_id}"
                    )
        except Exception as e:
            logger.warning(f"读取 livestream voice_id 失败，回退到 caller 传入值: {e}")

    voice_id = (voice_id or '').strip()

    # 使用当前 provider 自己注册的默认音色。
    if not voice_id:
        voice_id = default_voice_id or STEPFUN_TTS_DEFAULT_VOICE
    else:
        normalized_voice_id, voice_recognized = normalize_stepfun_tts_voice(
            voice_id,
            provider_key,
        )
        if voice_recognized:
            voice_id = normalized_voice_id

    async def async_worker():
        """Async TTS worker main loop"""
        from utils.language_utils import detect_tts_language_hint, TTS_LANG_DETECT_MIN_CHARS

        if is_free:
            tts_url = _adjust_free_tts_url("wss://www.lanlan.tech/tts")
        else:
            tts_url = "wss://api.stepfun.com/v1/realtime/audio?model=step-tts-2"
        is_lanlan_app = 'lanlan.app' in tts_url
        ws = None
        current_speech_id = None
        receive_task = None
        session_id = None
        session_ready = asyncio.Event()
        response_done = asyncio.Event()  # 用于标记当前响应是否完成
        text_done_sent = False  # 防止同一轮次重复发送 tts.text.done
        # 延迟 tts.create：等收到 TTS_LANG_DETECT_MIN_CHARS 个字符、检测完
        # 语言后再发送 tts.create（lanlan.tech 的 voice_label.language /
        # lanlan.app 的 language_code 都只能在建 session 时指定一次，
        # 所以必须在首批文本到达后才能发），和 CosyVoice worker 对偶。
        session_created = False
        pending_text_buffer = ""
        pending_finish_retry_speech_id = None
        # 服务端明确拒绝建连（配额 / 限流 / 黑白名单）的那一轮：剩余 chunk 与
        # 收尾都丢弃，不再逐 chunk 重连或按 1s 重试收尾；下一轮回复照常再试。
        rejected_speech_id = None
        # 黑白名单 / 路径错误这类永久拒绝：worker 报未就绪后退出，交给主进程的
        # NO_RETRY 闸门，不再每轮回复都去撞一次。
        permanent_rejection = False
        warmup_close_task = None
        deferred_requests = deque()
        # 流式重采样器（24kHz→48kHz）- 维护 chunk 边界状态
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        # StepFun/免费上游首包后第一个 inter-chunk gap 偏大，会让开头几个字 jitter。
        # 用与 qwen 对偶的共享 jitter buffer 攒出首包领先量盖过去。
        audio_jitter = make_audio_jitter_buffer(response_queue)
        # 上游 done 事件 = 本轮音频流关闭。两个 receive loop 共用同一个 emitter，
        # 保证同一 speech_id 只发一次；重连 / 新 sid / 打断走 reset。
        # 额外压一道 text_done_sent 闸：只有本轮已经发过 tts.text.done，done 事件
        # 才可能是整轮收尾。上游若按句发 done，没这道闸就是早发。
        audio_done = AudioDoneEmitter(response_queue)

        def _emit_audio_done(bound_speech_id) -> None:
            """Signal end-of-stream once the round's terminal text was sent."""
            if text_done_sent:
                audio_done.emit(bound_speech_id)

        _text_done_error_suppressed = False

        def _report_server_close(exc, speech_id=None) -> bool:
            """Forward a free-server rejection close as a structured error.

            The sender and the receive task can both observe one close; the
            first to report marks the speech rejected and the second only
            classifies, so the core counts a single failure.
            """
            nonlocal permanent_rejection
            if not is_free:
                return False
            classified = _classify_lanlan_server_close(exc)
            if classified is None:
                return False
            if speech_id is not None and speech_id == rejected_speech_id:
                return True
            if speech_id is not None:
                # 把错误钉到被拒的那一轮：主进程按约定把紧随其后的 __error__
                # 记到这个 speech_id，而不是此刻可能已切到下一轮的 current_speech_id。
                response_queue.put(("__tts_sentence_failed__", speech_id, ""))
            _enqueue_error(response_queue, classified)
            if classified["code"] in _PERMANENT_REJECTION_CODES:
                permanent_rejection = True
            return True

        def _reject_on_server_close(exc) -> bool:
            """End the current speech if a failed send hit a server rejection.

            A send can observe the server's close before the receive task does,
            and the socket invalidation that follows cancels that task, so the
            sender has to report the rejection itself.
            """
            nonlocal rejected_speech_id, pending_text_buffer
            if not _report_server_close(exc, current_speech_id):
                return False
            rejected_speech_id = current_speech_id
            pending_text_buffer = ""
            return True

        def _signal_permanent_rejection_exit(dequeued_speech_id=None) -> None:
            """Close out every round the exiting worker drops, then hand retry
            policy to the core.

            The rejected round was already marked failed through its error.
            Rounds that are still queued (dequeued, deferred, or waiting in
            the request queue) get a speech-scoped failure marker first, so
            their completion waiters resolve as failed instead of timing out.
            """
            logger.warning("%s TTS 被服务端永久拒绝，worker 退出", provider_label)
            audio_done.emit(rejected_speech_id)
            dropped_speech_ids = []
            pending_requests = list(deferred_requests)
            deferred_requests.clear()
            while True:
                try:
                    pending_requests.append(request_queue.get_nowait())
                except queue_module.Empty:
                    break
                except (AttributeError, NotImplementedError):
                    break
            for candidate in [dequeued_speech_id] + [req[0] for req in pending_requests]:
                if (
                    candidate is None
                    or candidate in _NON_SPEECH_REQUEST_IDS
                    or candidate == rejected_speech_id
                    or candidate in dropped_speech_ids
                ):
                    continue
                dropped_speech_ids.append(candidate)
            for dropped_speech_id in dropped_speech_ids:
                response_queue.put(("__tts_sentence_failed__", dropped_speech_id, ""))
                audio_done.emit(dropped_speech_id)
            response_queue.put(("__ready__", False))

        def _close_rejected_round_after_terminal(bound_speech_id) -> None:
            """Emit audio_done for a round rejected after its terminal request.

            Once ``tts.text.done`` went out no terminal queue item is left to
            reach the rejected-round branch, so the receiver closes the stream.
            """
            if text_done_sent and bound_speech_id == current_speech_id:
                audio_done.emit(bound_speech_id)

        def _round_finished(event_type) -> bool:
            """Whether this event is the last one the socket will carry.

            lanlan.app sends ``tts.response.audio.done`` after every sentence
            and ``tts.response.done`` exactly once, queued behind all audio of
            the round, after it received ``tts.text.done``.
            """
            return (
                is_lanlan_app
                and text_done_sent
                and event_type == "tts.response.done"
            )

        async def _close_finished_round_socket(bound_ws) -> None:
            # 每个新 speech_id 都会重新建连，收完一轮的 socket 不会被复用；
            # 免费服务按连接时长计配额，留着它只会空挂计时。
            try:
                await bound_ws.close()
            except Exception as close_exc:
                logger.debug("关闭已完成轮次的 TTS WebSocket 失败: %s", close_exc)

        def _build_tts_create_data(sid_: str, lang_hint):
            """Assemble the tts.create data field from the URL and language hint.
            - lanlan.app: language_code (Gemini streaming-TTS style; overrides the global language on a ja hit)
            - lanlan.tech / self-hosted StepFun: protocol-symmetric, voice_label.language="Japanese" (on a ja hit)
            """
            return _build_step_tts_create_data(sid_, voice_id, lang_hint, is_lanlan_app)

        async def _reconnect_after_buffered_delta_failure() -> bool:
            """Replace a dead socket while retaining the current text buffer."""
            nonlocal ws, session_id, receive_task, session_created
            nonlocal _text_done_error_suppressed, rejected_speech_id
            nonlocal pending_text_buffer
            if receive_task and not receive_task.done():
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    # Expected after the explicit cancellation above.
                    pass
                receive_task = None
            if ws:
                try:
                    await ws.close()
                except Exception as close_exc:
                    logger.debug("关闭失效 TTS WebSocket 失败: %s", close_exc)

            try:
                ws = await websockets.connect(tts_url, additional_headers=headers)
                session_id = None
                session_ready.clear()

                async def wait_conn():
                    nonlocal session_id
                    async for message in ws:
                        event = json.loads(message)
                        if event.get("type") == "tts.connection.done":
                            session_id = event.get("data", {}).get("session_id")
                            session_ready.set()
                            break
                        if event.get("type") == "tts.response.error":
                            _enqueue_error(response_queue, event)
                            break

                await asyncio.wait_for(wait_conn(), timeout=1.0)
                if not session_id:
                    await ws.close()
                    ws = None
                    return False

                _text_done_error_suppressed = False
                session_created = False

                # bound_speech_id 在建任务时钉死本轮 sid：sid 切换路径会先推进
                # current_speech_id 再 await 关旧连接，此刻旧 receive 任务若读到
                # 迟到的 done 事件，会把上一轮的收尾错标到新一轮。
                async def receive_messages_after_reconnect(bound_ws, bound_speech_id):
                    nonlocal _text_done_error_suppressed, rejected_speech_id
                    cancelled = False
                    try:
                        async for message in bound_ws:
                            event = json.loads(message)
                            event_type = event.get("type")
                            if event_type == "tts.response.error":
                                err_msg = event.get("data", {}).get("message", "")
                                if "tts.text.done" in err_msg and "already" in err_msg:
                                    if not _text_done_error_suppressed:
                                        _text_done_error_suppressed = True
                                        logger.warning("TTS: 服务端报告 tts.text.done 重复，后续同类错误将被静默")
                                    continue
                                _enqueue_error(response_queue, event)
                            elif event_type == "tts.response.audio.delta":
                                try:
                                    audio_b64 = event.get("data", {}).get("audio", "")
                                    if audio_b64:
                                        audio_bytes = base64.b64decode(audio_b64)
                                        with io.BytesIO(audio_bytes) as wav_io:
                                            with wave.open(wav_io, 'rb') as wav_file:
                                                pcm_data = wav_file.readframes(wav_file.getnframes())
                                        audio_array = np.frombuffer(pcm_data, dtype=np.int16)
                                        audio_jitter.append(
                                            _resample_audio(audio_array, 24000, 48000, resampler)
                                        )
                                except Exception as audio_exc:
                                    logger.error(f"处理音频数据时出错: {audio_exc}")
                            elif event_type in ["tts.response.done", "tts.response.audio.done"]:
                                logger.debug(f"收到响应完成事件: {event_type}")
                                audio_jitter.flush()
                                # flush 已经把尾音投进队列，此刻本轮音频流才真正关闭
                                _emit_audio_done(bound_speech_id)
                                response_done.set()
                                if _round_finished(event_type):
                                    await _close_finished_round_socket(bound_ws)
                                    return
                    except websockets.exceptions.ConnectionClosed as closed_exc:
                        # Expected while replacing or shutting down this socket;
                        # a server rejection (e.g. quota spent mid-round) is
                        # surfaced and ends this speech like a connect rejection.
                        if _report_server_close(closed_exc, bound_speech_id):
                            rejected_speech_id = bound_speech_id
                            _close_rejected_round_after_terminal(bound_speech_id)
                    except asyncio.CancelledError:
                        cancelled = True
                        raise
                    except Exception as recv_exc:
                        logger.error(f"消息接收出错: {recv_exc}")
                    finally:
                        if not cancelled:
                            audio_jitter.flush()

                receive_task = asyncio.create_task(
                    receive_messages_after_reconnect(ws, current_speech_id)
                )
                return True
            except Exception as reconnect_exc:
                logger.warning("缓冲文本发送失败后的 TTS 重连失败: %s", reconnect_exc)
                if _report_server_close(reconnect_exc, current_speech_id):
                    # 与新语音建连被拒同一处理：本轮上报一次后整体放弃。
                    rejected_speech_id = current_speech_id
                    pending_text_buffer = ""
                if ws:
                    try:
                        await ws.close()
                    except Exception as close_exc:
                        logger.debug("关闭失败的 TTS 重连 socket 失败: %s", close_exc)
                ws = None
                session_id = None
                session_created = False
                return False

        async def _invalidate_current_socket(reason: str) -> None:
            """Retire a socket after a synchronous send failure."""
            nonlocal ws, session_id, receive_task, session_created
            if receive_task and not receive_task.done():
                # A terminal send failure invalidates the socket but is not a
                # user interrupt. Release already-generated audio below the
                # jitter threshold before cancellation; receiver cancellation
                # intentionally skips its finally-flush to preserve explicit
                # interrupt discard semantics.
                audio_jitter.flush()
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    # Expected after the explicit cancellation above.
                    pass
                receive_task = None
            if ws:
                try:
                    await ws.close()
                except Exception as close_exc:
                    logger.debug("%s WebSocket 关闭失败: %s", reason, close_exc)
            ws = None
            session_id = None
            session_created = False

        async def _queue_finish_retry(speech_id) -> None:
            """Retry a failed terminal send without busy-spinning."""
            nonlocal pending_finish_retry_speech_id
            if speech_id is None:
                return
            # Keep retry work outside the producer queue. While this backoff
            # yields, later speech can be enqueued; the consumer must still
            # finish the retained current-speech prefix before dequeuing it.
            pending_finish_retry_speech_id = speech_id
            await asyncio.sleep(1.0)

        def _defer_queued_work_until_control() -> bool:
            """Move an already-queued control request ahead of recovery work."""
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

        async def _close_candidate_best_effort(candidate, context: str) -> None:
            """Retire an unpublished socket without changing recovery policy."""
            if candidate is None:
                return
            try:
                await candidate.close()
            except Exception as close_exc:
                logger.debug("%s: %s", context, close_exc)

        async def _flush_deferred_create(
            force: bool = False,
            *,
            retry_after_reconnect: bool = True,
        ) -> bool:
            """When tts.create hasn't been sent yet, detect the language and send it, then flush the pending text.

            force=True is for the sid=None early-wrap-up case: send even below MIN_CHARS.
            Returns True if the session is ready (created just now or previously).
            """
            nonlocal ws, session_id, current_speech_id, receive_task
            nonlocal session_created, pending_text_buffer
            if session_created:
                return True
            if not ws or not session_id:
                return False
            if not force and len(pending_text_buffer) < TTS_LANG_DETECT_MIN_CHARS:
                return False
            lang_hint = detect_tts_language_hint(pending_text_buffer)
            if lang_hint:
                logger.info("%s TTS 语言提示: %s", provider_label, lang_hint)
            create_data = _build_tts_create_data(session_id, lang_hint)
            try:
                await ws.send(json.dumps({"type": "tts.create", "data": create_data}))
            except Exception as e:
                logger.error(f"发送 tts.create 失败: {e}")
                _reject_on_server_close(e)
                await _invalidate_current_socket("发送 tts.create 失败后的")
                return False
            session_created = True
            if pending_text_buffer.strip():
                try:
                    await ws.send(json.dumps({
                        "type": "tts.text.delta",
                        "data": {"session_id": session_id, "text": pending_text_buffer},
                    }))
                    _record_tts_telemetry(provider_key, len(pending_text_buffer))
                except Exception as e:
                    # delta 发失败时连接多半已断，调用方不能继续发 tts.text.done；
                    # 回滚 create 状态但保留 pending_text_buffer，让下一次调用
                    # 重试完整的 create + 首段文本，而不是静默丢掉句首。
                    logger.error(f"刷出缓冲文本失败: {e}")
                    session_created = False
                    if _reject_on_server_close(e):
                        await _invalidate_current_socket("缓冲文本被服务端拒绝后的")
                        return False
                    if (
                        retry_after_reconnect
                        and await _reconnect_after_buffered_delta_failure()
                    ):
                        replayed = await _flush_deferred_create(
                            force=True,
                            retry_after_reconnect=False,
                        )
                        if replayed:
                            return True
                    # A second failure means the replacement connection is also
                    # unusable. Invalidate it so the next speech id reconnects;
                    # keep pending_text_buffer intact for diagnostics/retry.
                    await _invalidate_current_socket("二次发送失败后的")
                    return False
            pending_text_buffer = ""
            return True

        async def _finish_current_speech() -> bool:
            nonlocal text_done_sent
            if not ws or not session_id or current_speech_id is None or text_done_sent:
                return False
            # 若缓冲中还有不足 MIN_CHARS 的文本，强制刷出以保证短句也能合成。
            if not session_created and not await _flush_deferred_create(force=True):
                return False
            try:
                done_event = {
                    "type": "tts.text.done",
                    "data": {"session_id": session_id},
                }
                await ws.send(json.dumps(done_event))
                text_done_sent = True
            except Exception as e:
                logger.warning(f"发送TTS完成信号失败: {e}")
                _reject_on_server_close(e)
                await _invalidate_current_socket("发送 tts.text.done 失败后的")
                return False
            return True

        try:
            # 连接WebSocket
            headers = {"Authorization": f"Bearer {audio_api_key}"}

            # close_timeout 限制预热连接关闭时最多残留多久（它在后台关，见下）。
            ws = await websockets.connect(
                tts_url,
                additional_headers=headers,
                close_timeout=2.0,
            )

            # 等待连接成功事件
            async def wait_for_connection():
                """Wait for the connection to succeed"""
                nonlocal session_id
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")

                        if event_type == "tts.connection.done":
                            session_id = event.get("data", {}).get("session_id")
                            session_ready.set()
                            break
                        elif event_type == "tts.response.error":
                            _enqueue_error(response_queue, event)
                            break
                except Exception as e:
                    if not _report_server_close(e):
                        _enqueue_error(response_queue, e)

            # 等待连接成功
            try:
                await asyncio.wait_for(wait_for_connection(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error("等待连接超时")
                # 发送失败信号
                response_queue.put(("__ready__", False))
                return

            if not session_ready.is_set() or not session_id:
                logger.error("连接未能正确建立")
                # 发送失败信号
                response_queue.put(("__ready__", False))
                return

            # 启动预热 session：这段只作为 WS 连通性验证，首个真实 speech_id
            # 到达时会关闭重连。仍走一次 tts.create 保证旧逻辑的 ready 信号
            # 时序不变（服务端确认 tts.response.created 后再 __ready__）。
            create_data = _build_tts_create_data(session_id, None)
            create_event = {"type": "tts.create", "data": create_data}
            await ws.send(json.dumps(create_event))
            session_created = True

            # 等待会话创建成功
            async def wait_for_session_ready():
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")

                        if event_type == "tts.response.created":
                            break
                        elif event_type == "tts.response.error":
                            logger.error(f"创建会话错误: {event}")
                            break
                except Exception as e:
                    logger.error(f"等待会话创建时出错: {e}")

            try:
                await asyncio.wait_for(wait_for_session_ready(), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("会话创建超时")

            # 发送就绪信号，通知主进程 TTS 已经可以使用
            logger.info("%s TTS 已就绪，发送就绪信号", provider_label)
            response_queue.put(("__ready__", True))

            # 预热连接只用来验证连通性：首个真实 speech_id 一定会重新建连，
            # 它从不被复用。免费服务按连接时长计配额，留着它会一直空挂到
            # 角色第一次开口（会话开着不说话时可达半小时）。
            # 放到后台关：服务端迟迟不回关闭握手时不能挡住首轮语音。
            warmup_close_task = asyncio.create_task(
                _close_finished_round_socket(ws)
            )
            ws = None
            session_id = None
            session_ready.clear()
            session_created = False

            # 主循环：处理请求队列
            loop = asyncio.get_running_loop()
            while True:
                if permanent_rejection:
                    _signal_permanent_rejection_exit()
                    break
                if pending_finish_retry_speech_id is not None:
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
                    if control_request is not None:
                        pending_finish_retry_speech_id = None
                        sid, tts_text = control_request
                    else:
                        sid = _FINISH_RETRY_SENTINEL
                        tts_text = pending_finish_retry_speech_id
                        pending_finish_retry_speech_id = None
                elif deferred_requests:
                    sid, tts_text = deferred_requests.popleft()
                else:
                    try:
                        sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                    except Exception:
                        break
                finish_requested = False
                text_staged_for_reconnect = False

                if sid == TTS_SHUTDOWN_SENTINEL:
                    break

                if permanent_rejection:
                    # 阻塞在取队列期间，接收任务可能已上报了永久拒绝；刚取出的
                    # 这一轮也要收尾，不能静默丢掉。
                    _signal_permanent_rejection_exit(sid)
                    break

                if sid == _FINISH_RETRY_SENTINEL:
                    finish_speech_id = tts_text
                    if (
                        finish_speech_id is None
                        or finish_speech_id != current_speech_id
                        or text_done_sent
                        or not pending_text_buffer.strip()
                    ):
                        continue
                    finish_requested = True
                    sid = finish_speech_id

                if sid == "__interrupt__":
                    # 打断：立即关闭连接，不发 tts.text.done、不等服务器确认
                    audio_jitter.begin_interrupt()
                    audio_done.begin_interrupt()  # 打断轮不发 audio_done（走独立 cancel 通道）
                    try:
                        if receive_task and not receive_task.done():
                            receive_task.cancel()
                            try:
                                await receive_task
                            except asyncio.CancelledError:
                                # Expected during interrupt teardown.
                                pass
                            except Exception as e:
                                logger.debug(f"Step TTS interrupted receive task cleanup failed: {e}")
                            receive_task = None
                        if ws:
                            try:
                                await ws.close()
                            except Exception as e:
                                logger.debug(f"Step TTS interrupted websocket close failed: {e}")
                            ws = None
                    finally:
                        session_id = None
                        session_ready.clear()
                        current_speech_id = None
                        text_done_sent = False
                        session_created = False
                        pending_text_buffer = ""
                        rejected_speech_id = None
                        deferred_requests.clear()
                        audio_jitter.reset()  # 打断：丢弃未放出的缓冲音频
                        audio_jitter.end_interrupt()
                        audio_done.reset()
                        audio_done.end_interrupt()
                    continue

                if rejected_speech_id is not None and (
                    sid == rejected_speech_id
                    or (sid is None and current_speech_id == rejected_speech_id)
                ):
                    # 这一轮已被服务端拒绝并上报过：剩余 chunk 与收尾都丢弃，
                    # 不再逐个重连，也不往已被关掉的 socket 上发。
                    if sid is None:
                        # 收尾照样发 audio_done：主进程已把这一轮记为投递失败，
                        # 等音频完成的调用方据此立刻以失败收场，而不是干等超时。
                        audio_done.emit(rejected_speech_id)
                    continue

                if sid is None:
                    # 正常结束（非阻塞）：发送完成信号，但不等待服务器确认、不关闭连接
                    # 音频继续通过 receive_task 流入 response_queue。lanlan.app 由
                    # receive_task 在 tts.response.done 后关连接；其余上游的连接由
                    # 下次 speech_id 切换 / __interrupt__ 关闭
                    if ws and session_id and current_speech_id is not None and not text_done_sent:
                        if not await _finish_current_speech() and pending_text_buffer.strip():
                            await _queue_finish_retry(current_speech_id)
                        continue
                    if (
                        current_speech_id is None
                        or text_done_sent
                        or not pending_text_buffer.strip()
                    ):
                        continue
                    # A failed create/replay deliberately retains the original
                    # prefix while invalidating the socket. Reconnect the same
                    # speech for this terminal queue entry instead of waiting
                    # for another text chunk that may never arrive.
                    finish_requested = True
                    sid = current_speech_id

                # 新语音，或当前语音的 socket 已失效：重新建立连接。
                # 同一语音的恢复重连必须保留尚未发送成功的文本前缀。
                is_new_speech = current_speech_id != sid
                if is_new_speech or not ws or not session_id:
                    current_speech_id = sid
                    text_done_sent = False
                    session_created = False
                    if is_new_speech:
                        pending_text_buffer = ""
                    # Retain this request before any network await.  A connect
                    # exception otherwise continues the loop before the normal
                    # buffering path below and silently drops the opening text.
                    if (
                        not finish_requested
                        and tts_text
                        and tts_text.strip()
                    ):
                        pending_text_buffer += tts_text
                        text_staged_for_reconnect = True
                    response_done.clear()
                    # Revoke the old socket/session ownership synchronously.
                    # ``ws = await connect(...)`` doesn't assign on exception,
                    # so leaving these globals intact would make the closed old
                    # socket look reusable to the next chunk of the same SID.
                    old_ws = ws
                    ws = None
                    session_id = None
                    session_ready.clear()
                    if old_ws:
                        try:
                            await old_ws.close()
                        except Exception as e:
                            # Reconnect below replaces this socket, so close
                            # failures are non-fatal; cancellation still
                            # propagates instead of being swallowed.
                            logger.debug("关闭旧 TTS WebSocket 失败: %s", e)
                    if receive_task and not receive_task.done():
                        audio_jitter.flush()
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            # Expected after the explicit cancel directly above.
                            pass
                    # 旧接收任务已完全停止后再重置流式状态：await ws.close() 会让出，
                    # 期间旧 receive_task 可能写入晚到的 audio.delta，若提前重置会被残留污染下一轮
                    resampler.clear()  # 重置重采样器状态（新轮次音频不应与上轮次连续）
                    audio_jitter.reset()  # 新轮次重置 jitter buffer 领先量
                    if is_new_speech:
                        audio_done.reset()  # 新轮次重置 audio_done 去重标记

                    # 建立新连接
                    candidate_ws = None
                    try:
                        candidate_ws = await websockets.connect(
                            tts_url,
                            additional_headers=headers,
                        )

                        # 等待连接成功
                        candidate_session_id = None
                        candidate_rejected = False

                        async def wait_conn():
                            nonlocal candidate_session_id, candidate_rejected
                            try:
                                async for message in candidate_ws:
                                    event = json.loads(message)
                                    if event.get("type") == "tts.connection.done":
                                        candidate_session_id = event.get("data", {}).get("session_id")
                                        break
                            except Exception as e:
                                # The timeout/session_id checks below own the
                                # reconnect decision; retain the exception only
                                # as diagnostic context. An explicit server
                                # rejection is the one exception that is
                                # reported and ends this speech's retries.
                                candidate_rejected = _report_server_close(e, current_speech_id)
                                logger.debug("等待新 TTS 连接确认失败: %s", e)

                        try:
                            await asyncio.wait_for(wait_conn(), timeout=1.0)
                        except asyncio.TimeoutError:
                            logger.warning("新连接超时")
                            control_preempted = _defer_queued_work_until_control()
                            retired_candidate_ws = candidate_ws
                            candidate_ws = None
                            await _close_candidate_best_effort(
                                retired_candidate_ws,
                                "关闭握手超时的 TTS socket 失败",
                            )
                            if not control_preempted:
                                control_preempted = _defer_queued_work_until_control()
                            if control_preempted:
                                continue
                            if finish_requested:
                                await _queue_finish_retry(current_speech_id)
                            continue

                        if not candidate_session_id:
                            control_preempted = _defer_queued_work_until_control()
                            retired_candidate_ws = candidate_ws
                            candidate_ws = None
                            await _close_candidate_best_effort(
                                retired_candidate_ws,
                                "关闭缺少 session_id 的 TTS socket 失败",
                            )
                            if candidate_rejected:
                                # Retrying this speech per chunk (or its finish
                                # every second) only piles rejected connects on
                                # the server. The next speech tries again.
                                rejected_speech_id = current_speech_id
                                pending_text_buffer = ""
                                continue
                            if not control_preempted:
                                control_preempted = _defer_queued_work_until_control()
                            if control_preempted:
                                continue
                            if finish_requested:
                                await _queue_finish_retry(current_speech_id)
                            continue

                        # An interrupt/shutdown queued while connect or its
                        # handshake was in flight owns the boundary.  Do not
                        # publish the recovered socket or replay stale text.
                        if _defer_queued_work_until_control():
                            control_candidate_ws = candidate_ws
                            candidate_ws = None
                            # Control already owns this boundary.  A cleanup
                            # failure must not re-enter reconnect/backoff before
                            # the queued interrupt or shutdown is processed.
                            await _close_candidate_best_effort(
                                control_candidate_ws,
                                "关闭被控制消息抢占的 TTS socket 失败",
                            )
                            continue

                        ws = candidate_ws
                        candidate_ws = None
                        session_id = candidate_session_id
                        session_ready.set()

                        # 延迟 tts.create 到首批文本到达后，由 _flush_deferred_create
                        # 发送（带语言提示）。此处仅启动接收任务消费服务端事件。
                        _text_done_error_suppressed = False  # 重连后重置错误抑制标记

                        async def receive_messages(bound_ws, bound_speech_id):
                            nonlocal _text_done_error_suppressed, rejected_speech_id
                            cancelled = False
                            try:
                                async for message in bound_ws:
                                    event = json.loads(message)
                                    event_type = event.get("type")

                                    if event_type == "tts.response.error":
                                        err_msg = event.get("data", {}).get("message", "")
                                        if "tts.text.done" in err_msg and "already" in err_msg:
                                            if not _text_done_error_suppressed:
                                                _text_done_error_suppressed = True
                                                logger.warning("TTS: 服务端报告 tts.text.done 重复，后续同类错误将被静默")
                                            continue
                                        _enqueue_error(response_queue, event)
                                    elif event_type == "tts.response.audio.delta":
                                        try:
                                            audio_b64 = event.get("data", {}).get("audio", "")
                                            if audio_b64:
                                                audio_bytes = base64.b64decode(audio_b64)
                                                # 使用 wave 模块读取 WAV 数据
                                                with io.BytesIO(audio_bytes) as wav_io:
                                                    with wave.open(wav_io, 'rb') as wav_file:
                                                        # 读取音频数据
                                                        pcm_data = wav_file.readframes(wav_file.getnframes())

                                                # 转换为 numpy 数组
                                                audio_array = np.frombuffer(pcm_data, dtype=np.int16)
                                                # 使用流式重采样器 24000Hz -> 48000Hz
                                                audio_jitter.append(_resample_audio(audio_array, 24000, 48000, resampler))
                                        except Exception as e:
                                            logger.error(f"处理音频数据时出错: {e}")
                                    elif event_type in ["tts.response.done", "tts.response.audio.done"]:
                                        # 服务器明确表示音频生成完成，设置完成标志
                                        logger.debug(f"收到响应完成事件: {event_type}")
                                        audio_jitter.flush()  # 放掉缓冲区里不足 steady 阈值的尾音
                                        # flush 已经把尾音投进队列，此刻本轮音频流才真正关闭
                                        _emit_audio_done(bound_speech_id)
                                        response_done.set()
                                        if _round_finished(event_type):
                                            await _close_finished_round_socket(bound_ws)
                                            return
                            except websockets.exceptions.ConnectionClosed as closed_exc:
                                # Normal when reconnect/shutdown closes the
                                # socket while the receiver is awaiting data;
                                # a server rejection (e.g. quota spent
                                # mid-round) is surfaced and ends this speech
                                # like a connect rejection.
                                if _report_server_close(closed_exc, bound_speech_id):
                                    rejected_speech_id = bound_speech_id
                                    _close_rejected_round_after_terminal(bound_speech_id)
                            except asyncio.CancelledError:
                                cancelled = True
                                raise
                            except Exception as e:
                                logger.error(f"消息接收出错: {e}")
                            finally:
                                if not cancelled:
                                    audio_jitter.flush()

                        receive_task = asyncio.create_task(
                            receive_messages(ws, current_speech_id)
                        )

                    except Exception as e:
                        failed_candidate_ws = candidate_ws
                        candidate_ws = None
                        await _close_candidate_best_effort(
                            failed_candidate_ws,
                            "关闭失败的新 TTS socket 失败",
                        )
                        ws = None
                        session_id = None
                        logger.error(f"重新建立连接失败: {e}")
                        if _defer_queued_work_until_control():
                            continue
                        if 'HTTP 503' in str(e):
                            _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
                        response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                        if finish_requested:
                            await _queue_finish_retry(current_speech_id)
                        else:
                            await asyncio.sleep(1.0)
                            _defer_queued_work_until_control()
                        continue

                if finish_requested:
                    if not await _finish_current_speech() and pending_text_buffer.strip():
                        await _queue_finish_retry(current_speech_id)
                    continue

                # 检查文本有效性
                if not tts_text or not tts_text.strip():
                    continue

                # 已发送 tts.text.done 后，丢弃同一轮次的残余文本（防止服务端报错）
                if text_done_sent:
                    logger.debug("TTS: 丢弃 text_done 之后的残余文本 chunk")
                    continue

                if not ws or not session_id:
                    continue

                # 尚未发送 tts.create 时，先缓冲 MIN_CHARS 个字符用于语言检测
                if not session_created:
                    if not text_staged_for_reconnect:
                        pending_text_buffer += tts_text
                    ready = await _flush_deferred_create(force=False)
                    if not ready:
                        continue
                    # 已在 _flush_deferred_create 内把 pending_text_buffer 随 tts.create
                    # 一起发出，无需再次发送当前 tts_text
                    continue

                # 发送文本
                try:
                    text_event = {
                        "type": "tts.text.delta",
                        "data": {
                            "session_id": session_id,
                            "text": tts_text
                        }
                    }
                    await ws.send(json.dumps(text_event))
                    _record_tts_telemetry(provider_key, len(tts_text))
                except Exception as e:
                    logger.error(f"发送TTS文本失败: {e}")
                    rejected_now = _reject_on_server_close(e)
                    # 连接已关闭，标记为无效以便下次重连
                    ws = None
                    session_id = None
                    if not rejected_now:
                        # 清空ID以强制下次重连；被拒的轮次保留 ID，让收尾能匹配上
                        current_speech_id = None
                    session_created = False
                    pending_text_buffer = ""
                    if receive_task and not receive_task.done():
                        receive_task.cancel()

        except Exception as e:
            logger.error(
                "%s 实时TTS Worker错误: %s: %r",
                provider_key,
                type(e).__name__,
                e,
                exc_info=True,
            )
            if 'HTTP 503' in str(e):
                _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
            response_queue.put(("__ready__", False))
        finally:
            # 清理资源
            if warmup_close_task is not None and not warmup_close_task.done():
                # close_timeout 兜底的后台关闭；worker 退出前给它一次收尾机会
                try:
                    await asyncio.wait_for(asyncio.shield(warmup_close_task), timeout=2.5)
                except Exception as e:
                    logger.debug("预热 TTS WebSocket 后台关闭未完成: %s", e)
            if receive_task and not receive_task.done():
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    # Expected after the explicit cancel directly above.
                    pass

            if ws:
                try:
                    await ws.close()
                except Exception as e:
                    # Best-effort final cleanup; the worker is already exiting.
                    logger.debug("关闭 TTS WebSocket 资源失败: %s", e)

    # 运行异步worker
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(
            "%s 实时TTS Worker启动失败: %s: %r",
            provider_key,
            type(e).__name__,
            e,
            exc_info=True,
        )
        response_queue.put(("__ready__", False))
