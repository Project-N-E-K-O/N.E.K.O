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

"""StepFun / free realtime TTS worker."""

import numpy as np
import soxr
import json
import base64
import websockets
import io
import wave
import asyncio

from utils.config_manager import get_config_manager
from utils.tts.native_voice_registry import make_native_tts_resolver, register_tts_worker_resolver
from utils.tts.providers.stepfun import STEPFUN_TTS_DEFAULT_VOICE, get_stepfun_tts_default_voice, normalize_stepfun_tts_voice

from .._infra import TTS_SHUTDOWN_SENTINEL, _resample_audio, make_audio_jitter_buffer, _enqueue_error
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

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
    """Assemble the tts.create data field for Step/free TTS from the URL and language hint."""
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
        # lanlan.tech (free) 和自建 StepFun 协议对称，都用 voice_label。
        if lang_hint == "ja":
            data["voice_label"] = {"language": "日语"}
    return data

def step_realtime_tts_worker(request_queue, response_queue, audio_api_key, voice_id, free_mode=False):
    """
    StepFun realtime TTS worker (for default voices)
    Uses StepFun's realtime TTS API (step-tts-mini)

    Args:
        request_queue: multiprocess request queue receiving (speech_id, text) tuples
        response_queue: multiprocess response queue sending audio data (also used for the ready signal)
        audio_api_key: API key
        voice_id: voice ID; defaults to the StepFun config in api_providers.json
    """
    # free + livestream 子模式：voice_id 优先取 api_providers.json 的
    # livestream_config.voice_id（绕过 caller 的 free_voices preset 路径）。
    # 多进程 worker 这里独立 import，与主进程对偶。
    native_provider_key = 'free' if free_mode else 'step'
    default_voice_id = get_stepfun_tts_default_voice(native_provider_key)

    if free_mode:
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

    # 使用配置中的默认 StepFun 音色
    if not voice_id:
        voice_id = default_voice_id or STEPFUN_TTS_DEFAULT_VOICE
    else:
        normalized_voice_id, voice_recognized = normalize_stepfun_tts_voice(
            voice_id,
            native_provider_key,
        )
        if voice_recognized:
            voice_id = normalized_voice_id
    
    async def async_worker():
        """Async TTS worker main loop"""
        from utils.language_utils import detect_tts_language_hint, TTS_LANG_DETECT_MIN_CHARS

        if free_mode:
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
        # 流式重采样器（24kHz→48kHz）- 维护 chunk 边界状态
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        # StepFun/免费上游首包后第一个 inter-chunk gap 偏大，会让开头几个字 jitter。
        # 用与 qwen 对偶的共享 jitter buffer 攒出首包领先量盖过去。
        audio_jitter = make_audio_jitter_buffer(response_queue)

        def _build_tts_create_data(sid_: str, lang_hint):
            """Assemble the tts.create data field from the URL and language hint.
            - lanlan.app: language_code (Gemini streaming-TTS style; overrides the global language on a ja hit)
            - lanlan.tech / self-hosted StepFun: protocol-symmetric, voice_label.language="Japanese" (on a ja hit)
            """
            return _build_step_tts_create_data(sid_, voice_id, lang_hint, is_lanlan_app)

        async def _flush_deferred_create(force: bool = False) -> bool:
            """When tts.create hasn't been sent yet, detect the language and send it, then flush the pending text.

            force=True is for the sid=None early-wrap-up case: send even below MIN_CHARS.
            Returns True if the session is ready (created just now or previously).
            """
            nonlocal session_created, pending_text_buffer
            if session_created:
                return True
            if not ws or not session_id:
                return False
            if not force and len(pending_text_buffer) < TTS_LANG_DETECT_MIN_CHARS:
                return False
            lang_hint = detect_tts_language_hint(pending_text_buffer)
            if lang_hint:
                logger.info(f"StepFun TTS 语言提示: {lang_hint}")
            create_data = _build_tts_create_data(session_id, lang_hint)
            try:
                await ws.send(json.dumps({"type": "tts.create", "data": create_data}))
            except Exception as e:
                logger.error(f"发送 tts.create 失败: {e}")
                return False
            session_created = True
            if pending_text_buffer.strip():
                try:
                    await ws.send(json.dumps({
                        "type": "tts.text.delta",
                        "data": {"session_id": session_id, "text": pending_text_buffer},
                    }))
                    _record_tts_telemetry("stepfun", len(pending_text_buffer))
                except Exception as e:
                    # delta 发失败时连接多半已断，调用方不能继续发 tts.text.done；
                    # 返回 False 让 sid=None/文本发送路径都走 continue 触发重连。
                    logger.error(f"刷出缓冲文本失败: {e}")
                    return False
            pending_text_buffer = ""
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
            logger.info("StepFun TTS 已就绪，发送就绪信号")
            response_queue.put(("__ready__", True))
            
            # 初始接收任务
            _text_done_error_suppressed = False  # 抑制 "tts.text.done already sent" 错误洪泛

            async def receive_messages_initial():
                """Initial receive task"""
                nonlocal _text_done_error_suppressed
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
                    pass
                except Exception as e:
                    logger.error(f"消息接收出错: {e}")
            
            receive_task = asyncio.create_task(receive_messages_initial())
            
            # 主循环：处理请求队列
            loop = asyncio.get_running_loop()
            while True:
                try:
                    sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                except Exception:
                    break

                if sid == TTS_SHUTDOWN_SENTINEL:
                    break

                if sid == "__interrupt__":
                    # 打断：立即关闭连接，不发 tts.text.done、不等服务器确认
                    if ws:
                        try:
                            await ws.close()
                        except Exception:
                            pass
                        ws = None
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            pass
                        receive_task = None
                    session_id = None
                    session_ready.clear()
                    current_speech_id = None
                    text_done_sent = False
                    session_created = False
                    pending_text_buffer = ""
                    audio_jitter.reset()  # 打断：丢弃未放出的缓冲音频
                    continue

                if sid is None:
                    # 正常结束（非阻塞）：发送完成信号，但不等待服务器确认、不关闭连接
                    # 音频继续通过 receive_task 流入 response_queue，
                    # 连接由下次 speech_id 切换 / __interrupt__ 关闭
                    if ws and session_id and current_speech_id is not None and not text_done_sent:
                        # 若缓冲中还有不足 MIN_CHARS 的文本，强制刷出以保证短句也能合成
                        if not session_created:
                            if not await _flush_deferred_create(force=True):
                                # flush 失败（tts.create 或 delta 发失败），连接已死，
                                # 跳过 tts.text.done，等待下一个 speech_id 触发重连
                                continue
                        try:
                            done_event = {
                                "type": "tts.text.done",
                                "data": {"session_id": session_id}
                            }
                            await ws.send(json.dumps(done_event))
                            text_done_sent = True
                        except Exception as e:
                            logger.warning(f"发送TTS完成信号失败: {e}")
                    continue

                # 新的语音ID，重新建立连接
                if current_speech_id != sid:
                    current_speech_id = sid
                    text_done_sent = False
                    session_created = False
                    pending_text_buffer = ""
                    response_done.clear()
                    if ws:
                        try:
                            await ws.close()
                        except:  # noqa: E722
                            pass
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
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
                            except Exception:
                                pass
                        
                        try:
                            await asyncio.wait_for(wait_conn(), timeout=1.0)
                        except asyncio.TimeoutError:
                            logger.warning("新连接超时")
                            continue
                        
                        if not session_id:
                            continue

                        # 延迟 tts.create 到首批文本到达后，由 _flush_deferred_create
                        # 发送（带语言提示）。此处仅启动接收任务消费服务端事件。
                        _text_done_error_suppressed = False  # 重连后重置错误抑制标记

                        async def receive_messages():
                            nonlocal _text_done_error_suppressed
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
                                pass
                            except Exception as e:
                                logger.error(f"消息接收出错: {e}")
                        
                        receive_task = asyncio.create_task(receive_messages())
                        
                    except Exception as e:
                        logger.error(f"重新建立连接失败: {e}")
                        if 'HTTP 503' in str(e):
                            _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
                        response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                        await asyncio.sleep(1.0)
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
                    _record_tts_telemetry("stepfun", len(tts_text))
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
            logger.error(f"StepFun实时TTS Worker错误: {type(e).__name__}: {e!r}", exc_info=True)
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
                    pass

            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass

    # 运行异步worker
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"StepFun实时TTS Worker启动失败: {type(e).__name__}: {e!r}", exc_info=True)
        response_queue.put(("__ready__", False))

register_tts_worker_resolver(
    'step',
    make_native_tts_resolver(step_realtime_tts_worker, 'tts_default_api_key'),
)

register_tts_worker_resolver(
    'free',
    make_native_tts_resolver(
        step_realtime_tts_worker,
        'tts_default_api_key',
        worker_kwargs={'free_mode': True},
    ),
)

# free_intl（海外免费 *.lanlan.app）：上游 Gemini 代理走 www.lanlan.app/tts，
# 协议同 free（StepFun-shape streaming，proxy 把 voice_id 透传给 Gemini），
# 因此复用 free 的 worker。与 free 对偶，仅 provider key 不同（registry 按
# host 把 free→free_intl 重映射，让 yui/Gemini 音色短路到这里而非外部 TTS）。
register_tts_worker_resolver(
    'free_intl',
    make_native_tts_resolver(
        step_realtime_tts_worker,
        'tts_default_api_key',
        worker_kwargs={'free_mode': True},
    ),
)
