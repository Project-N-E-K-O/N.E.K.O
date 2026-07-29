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

from .._infra import TTS_SHUTDOWN_SENTINEL, _resample_audio, make_audio_jitter_buffer, _enqueue_error
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_FINISH_RETRY_SENTINEL = "__step_finish_retry__"


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
        deferred_requests = deque()
        # 流式重采样器（24kHz→48kHz）- 维护 chunk 边界状态
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        # StepFun/免费上游首包后第一个 inter-chunk gap 偏大，会让开头几个字 jitter。
        # 用与 qwen 对偶的共享 jitter buffer 攒出首包领先量盖过去。
        audio_jitter = make_audio_jitter_buffer(response_queue)
        _text_done_error_suppressed = False

        def _build_tts_create_data(sid_: str, lang_hint):
            """Assemble the tts.create data field from the URL and language hint.
            - lanlan.app: language_code (Gemini streaming-TTS style; overrides the global language on a ja hit)
            - lanlan.tech / self-hosted StepFun: protocol-symmetric, voice_label.language="Japanese" (on a ja hit)
            """
            return _build_step_tts_create_data(sid_, voice_id, lang_hint, is_lanlan_app)

        async def _reconnect_after_buffered_delta_failure() -> bool:
            """Replace a dead socket while retaining the current text buffer."""
            nonlocal ws, session_id, receive_task, session_created
            nonlocal _text_done_error_suppressed
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

                async def receive_messages_after_reconnect():
                    nonlocal _text_done_error_suppressed
                    cancelled = False
                    try:
                        async for message in ws:
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
                                response_done.set()
                    except websockets.exceptions.ConnectionClosed:
                        # Expected while replacing or shutting down this socket.
                        pass
                    except asyncio.CancelledError:
                        cancelled = True
                        raise
                    except Exception as recv_exc:
                        logger.error(f"消息接收出错: {recv_exc}")
                    finally:
                        if not cancelled:
                            audio_jitter.flush()

                receive_task = asyncio.create_task(receive_messages_after_reconnect())
                return True
            except Exception as reconnect_exc:
                logger.warning("缓冲文本发送失败后的 TTS 重连失败: %s", reconnect_exc)
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
                await _invalidate_current_socket("发送 tts.text.done 失败后的")
                return False
            return True

        try:
            # 连接WebSocket
            headers = {"Authorization": f"Bearer {audio_api_key}"}

            ws = await websockets.connect(tts_url, additional_headers=headers)

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

            # 初始接收任务
            async def receive_messages_initial():
                """Initial receive task"""
                nonlocal _text_done_error_suppressed
                cancelled = False
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")

                        if event_type == "tts.response.error":
                            # 抑制 "tts.text.done already sent" 错误级联
                            err_msg = event.get("data", {}).get("message", "")
                            if "tts.text.done" in err_msg and "already" in err_msg:
                                if not _text_done_error_suppressed:
                                    _text_done_error_suppressed = True
                                    logger.warning("TTS: 服务端报告 tts.text.done 重复，后续同类错误将被静默")
                                continue
                            _enqueue_error(response_queue, event)
                        elif event_type == "tts.response.audio.delta":
                            try:
                                # StepFun 返回 BASE64 编码的完整音频（包含 wav header）
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
                            response_done.set()
                except websockets.exceptions.ConnectionClosed:
                    # Normal when a speech-id change or shutdown closes the
                    # socket while this receiver is awaiting the next frame.
                    pass
                except asyncio.CancelledError:
                    cancelled = True
                    raise
                except Exception as e:
                    logger.error(f"消息接收出错: {e}")
                finally:
                    if not cancelled:
                        audio_jitter.flush()

            receive_task = asyncio.create_task(receive_messages_initial())

            # 主循环：处理请求队列
            loop = asyncio.get_running_loop()
            while True:
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

                if sid == TTS_SHUTDOWN_SENTINEL:
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
                        deferred_requests.clear()
                        audio_jitter.reset()  # 打断：丢弃未放出的缓冲音频
                        audio_jitter.end_interrupt()
                    continue

                if sid is None:
                    # 正常结束（非阻塞）：发送完成信号，但不等待服务器确认、不关闭连接
                    # 音频继续通过 receive_task 流入 response_queue，
                    # 连接由下次 speech_id 切换 / __interrupt__ 关闭
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
                    response_done.clear()
                    if ws:
                        try:
                            await ws.close()
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

                    # 建立新连接
                    try:
                        ws = await websockets.connect(tts_url, additional_headers=headers)

                        # 等待连接成功
                        session_id = None
                        session_ready.clear()

                        async def wait_conn():
                            nonlocal session_id
                            try:
                                async for message in ws:
                                    event = json.loads(message)
                                    if event.get("type") == "tts.connection.done":
                                        session_id = event.get("data", {}).get("session_id")
                                        session_ready.set()
                                        break
                            except Exception as e:
                                # The timeout/session_id checks below own the
                                # reconnect decision; retain the exception only
                                # as diagnostic context.
                                logger.debug("等待新 TTS 连接确认失败: %s", e)

                        try:
                            await asyncio.wait_for(wait_conn(), timeout=1.0)
                        except asyncio.TimeoutError:
                            logger.warning("新连接超时")
                            if finish_requested:
                                await _queue_finish_retry(current_speech_id)
                            continue

                        if not session_id:
                            if finish_requested:
                                await _queue_finish_retry(current_speech_id)
                            continue

                        # 延迟 tts.create 到首批文本到达后，由 _flush_deferred_create
                        # 发送（带语言提示）。此处仅启动接收任务消费服务端事件。
                        _text_done_error_suppressed = False  # 重连后重置错误抑制标记

                        async def receive_messages():
                            nonlocal _text_done_error_suppressed
                            cancelled = False
                            try:
                                async for message in ws:
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
                                        response_done.set()
                            except websockets.exceptions.ConnectionClosed:
                                # Normal when reconnect/shutdown closes the
                                # socket while the receiver is awaiting data.
                                pass
                            except asyncio.CancelledError:
                                cancelled = True
                                raise
                            except Exception as e:
                                logger.error(f"消息接收出错: {e}")
                            finally:
                                if not cancelled:
                                    audio_jitter.flush()

                        receive_task = asyncio.create_task(receive_messages())

                    except Exception as e:
                        logger.error(f"重新建立连接失败: {e}")
                        if 'HTTP 503' in str(e):
                            _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
                        response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                        if finish_requested:
                            await _queue_finish_retry(current_speech_id)
                        else:
                            await asyncio.sleep(1.0)
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
                    # 连接已关闭，标记为无效以便下次重连
                    ws = None
                    session_id = None
                    current_speech_id = None  # 清空ID以强制下次重连
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
