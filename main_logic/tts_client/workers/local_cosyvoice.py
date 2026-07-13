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

"""Local CosyVoice (OpenAI-compatible bistream) worker."""

import numpy as np
import soxr
import json
import websockets
import asyncio

from utils.config_manager import get_config_manager

from .._infra import TTS_SHUTDOWN_SENTINEL, _resample_audio, _enqueue_error
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

def local_cosyvoice_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    Local CosyVoice WebSocket worker (OpenAI-compatible bistream version)
    Adapted to the /v1/audio/speech/stream endpoint defined by openai_server.py
    
    Protocol flow:
    1. After connecting, send config: {"voice": ..., "speed": ...}
    2. Send text: {"text": ...}
    3. Send the end signal: {"event": "end"}
    4. Receive bytes audio data (16-bit PCM, 22050Hz)
    
    Features:
    - Duplex streaming: sending and receiving run independently without blocking each other
    - Interrupt support: when speech_id changes, the old connection is closed, interrupting the old speech
    - Non-blocking: async architecture, never stalls the main loop
    
    Note: the audio_api_key parameter is unused (local mode needs no API key); it is kept to match the unified worker signature
    """
    _ = audio_api_key  # 本地模式不需要 API Key

    cm = get_config_manager()
    tts_config = cm.get_model_api_config('tts_custom')

    ws_base = tts_config.get('base_url', '')
    if (ws_base and not ws_base.startswith('ws://') and not ws_base.startswith('wss://')) or not ws_base:
        if ws_base:
            logger.error(f'本地cosyvoice URL协议无效: {ws_base}，需要 ws/wss 协议')
        else:
            logger.error('本地cosyvoice未配置url, 请在设置中填写正确的端口')
        response_queue.put(("__ready__", True))
        # 模仿 dummy_tts：持续清空队列但不生成音频
        while True:
            try:
                sid, _ = request_queue.get()
                if sid == TTS_SHUTDOWN_SENTINEL:
                    break
            except Exception:
                break
        return
    
    # OpenAI 兼容端点
    WS_URL = f'{ws_base}/v1/audio/speech/stream'
    
    # 从 voice_id 解析 voice 和 speed（格式：voice 或 voice:speed）
    voice_name = voice_id or "中文女"
    speech_speed = 1.0
    if voice_id and ':' in voice_id:
        parts = voice_id.split(':', 1)
        voice_name = parts[0]
        try:
            speech_speed = float(parts[1])
        except ValueError:
            pass
    
    # 服务器返回的采样率（22050Hz）
    SRC_RATE = 22050

    async def async_worker():
        ws = None
        receive_task = None
        current_speech_id = None
        
        resampler = soxr.ResampleStream(SRC_RATE, 48000, 1, dtype='float32')

        async def receive_loop(ws_conn, source_speech_id):
            """Independent receive task, handles the audio stream"""
            try:
                async for message in ws_conn:
                    if isinstance(message, bytes):
                        # 服务器返回 16-bit PCM @ 22050Hz
                        audio_array = np.frombuffer(message, dtype=np.int16)
                        resampled_bytes = _resample_audio(audio_array, SRC_RATE, 48000, resampler)
                        if source_speech_id is not None:
                            response_queue.put(("__audio__", source_speech_id, resampled_bytes))
            except websockets.exceptions.ConnectionClosed:
                logger.debug("本地 WebSocket 连接已关闭")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                _enqueue_error(response_queue, f"接收循环异常: {e}")

        async def send_end_signal(ws_conn):
            """Send the end signal (text was already sent in real time in the main loop; only end needs sending here)"""
            try:
                await ws_conn.send(json.dumps({"event": "end"}))
                logger.debug("发送结束信号")
            except Exception as e:
                _enqueue_error(response_queue, f"发送结束信号失败: {e}")

        async def create_connection(source_speech_id=None):
            """Create a new connection and send the config"""
            nonlocal ws, receive_task, resampler
            
            # 清理旧连接
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
            
            # 重置 resampler
            resampler = soxr.ResampleStream(SRC_RATE, 48000, 1, dtype='float32')
            
            logger.info(f"🔄 [LocalTTS] 正在连接: {WS_URL}")
            ws = await websockets.connect(WS_URL, ping_interval=None)
            logger.info("✅ [LocalTTS] 连接成功")
            
            # 发送配置
            config = {
                "voice": voice_name,
                "speed": speech_speed,
            }
            await ws.send(json.dumps(config))
            logger.debug(f"发送配置: {config}")
            
            # 启动接收任务
            receive_task = asyncio.create_task(receive_loop(ws, source_speech_id))
            return ws

        # 初始连接
        try:
            await create_connection()
            response_queue.put(("__ready__", True))
        except Exception as e:
            logger.error(f"❌ [LocalTTS] 初始连接失败: {e}")
            logger.error("请确保服务器已运行且端口正确")
            response_queue.put(("__ready__", False))
            return

        # 主循环
        loop = asyncio.get_running_loop()
        while True:
            try:
                sid, tts_text = await loop.run_in_executor(None, request_queue.get)
            except Exception as e:
                logger.error(f'队列获取异常: {e}')
                break

            if sid == TTS_SHUTDOWN_SENTINEL:
                break

            if sid == "__interrupt__":
                # 打断：立即关闭连接，不发 end 信号
                if receive_task and not receive_task.done():
                    receive_task.cancel()
                    try:
                        await receive_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    receive_task = None
                if ws:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    ws = None
                current_speech_id = None
                continue

            # speech_id 变化 -> 打断旧语音，建立新连接
            if sid != current_speech_id and sid is not None:
                if ws:
                    await send_end_signal(ws)
                
                current_speech_id = sid
                try:
                    await create_connection(sid)
                except Exception as e:
                    logger.error(f"重连失败: {e}")
                    ws = None
                    continue

            if sid is None:
                # 正常结束：发送结束信号
                if ws:
                    await send_end_signal(ws)
                current_speech_id = None
                continue

            if not tts_text or not tts_text.strip():
                continue
            
            # 同时发送（bistream 模式允许边发边收）
            if ws:
                try:
                    await ws.send(json.dumps({"text": tts_text}))
                    _record_tts_telemetry("local_cosyvoice", len(tts_text))
                    # TTS 文本原文不写 logger
                    logger.debug(f"发送合成片段 (len={len(tts_text)} chars)")
                    print(f"发送合成片段: {tts_text}")
                except Exception as e:
                    _enqueue_error(response_queue, f"发送失败: {e}")
                    ws = None

        # 清理
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

    # 运行 Asyncio 循环
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"Local CosyVoice Worker 崩溃: {e}")
        response_queue.put(("__ready__", False))
