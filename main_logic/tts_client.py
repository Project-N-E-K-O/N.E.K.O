"""
TTS Helper模块
负责处理TTS语音合成，支持自定义音色（阿里云CosyVoice）和默认音色（各core_api的原生TTS）
"""
import numpy as np
import soxr
import time
import json
import base64
import websockets
import io
import wave
import aiohttp
import asyncio
from functools import partial
from config import GSV_VOICE_PREFIX
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")


class CustomTTSVoiceFetchError(Exception):
    """Raised when custom TTS voice list cannot be fetched from provider."""


async def get_custom_tts_voices(base_url: str, provider: str = 'gptsovits'):
    """Fetch available custom TTS voices via provider adapter.

    Args:
        base_url: provider API base URL
        provider: provider key (currently supports 'gptsovits')

    Returns:
        list[dict]: normalized voices with fields: voice_id/raw_id/name/description/version
    """
    if provider != 'gptsovits':
        raise CustomTTSVoiceFetchError(f"Unsupported custom TTS provider: {provider}")

    base_url = (base_url or "").strip().rstrip("/")
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base_url}/api/v3/voices") as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise CustomTTSVoiceFetchError(f"HTTP {resp.status}: {text[:200]}")
                voices_data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        raise CustomTTSVoiceFetchError(str(e)) from e

    voices = []
    if not isinstance(voices_data, list):
        logger.warning(f"GPT-SoVITS /api/v3/voices 返回了非列表格式: {type(voices_data).__name__}")
        return voices

    for idx, v in enumerate(voices_data):
        if not isinstance(v, dict):
            logger.warning(
                "GPT-SoVITS /api/v3/voices 第 %d 项不是对象，已跳过: %s",
                idx,
                type(v).__name__,
            )
            continue
        raw_id = v.get('id', '')
        if not raw_id:
            continue
        voices.append({
            'voice_id': f"{GSV_VOICE_PREFIX}{raw_id}",
            'raw_id': raw_id,
            'name': v.get('name', raw_id),
            'description': v.get('description', ''),
            'version': v.get('version', ''),
        })

    return voices


def _resample_audio(audio_int16: np.ndarray, src_rate: int, dst_rate: int, 
                    resampler: 'soxr.ResampleStream | None' = None) -> bytes:
    """使用 soxr 进行高质量音频重采样
    
    Args:
        audio_int16: int16 格式的音频 numpy 数组
        src_rate: 源采样率
        dst_rate: 目标采样率
        resampler: 可选的流式重采样器，用于维护 chunk 间状态
        
    Returns:
        重采样后的 bytes
    """
    if src_rate == dst_rate:
        return audio_int16.tobytes()
    
    # 转换为 float32 进行高质量重采样
    audio_float = audio_int16.astype(np.float32) / 32768.0
    
    if resampler is not None:
        # 使用流式重采样器（维护 chunk 边界状态）
        resampled_float = resampler.resample_chunk(audio_float)
    else:
        # 无状态重采样（不推荐用于流式音频）
        resampled_float = soxr.resample(audio_float, src_rate, dst_rate, quality='HQ')
    
    # 转回 int16
    resampled_int16 = (resampled_float * 32768.0).clip(-32768, 32767).astype(np.int16)
    return resampled_int16.tobytes()


def step_realtime_tts_worker(request_queue, response_queue, audio_api_key, voice_id, free_mode=False):
    """
    StepFun实时TTS worker（用于默认音色）
    使用阶跃星辰的实时TTS API（step-tts-mini）
    
    Args:
        request_queue: 多进程请求队列，接收(speech_id, text)元组
        response_queue: 多进程响应队列，发送音频数据（也用于发送就绪信号）
        audio_api_key: API密钥
        voice_id: 音色ID，默认使用"qingchunshaonv"
    """
    import asyncio
    
    # 使用默认音色 "qingchunshaonv"
    if not voice_id:
        voice_id = "qingchunshaonv"
    
    async def async_worker():
        """异步TTS worker主循环"""
        if free_mode:
            tts_url = "wss://lanlan.tech/tts"
        else:
            tts_url = "wss://api.stepfun.com/v1/realtime/audio?model=step-tts-2"
        ws = None
        current_speech_id = None
        receive_task = None
        session_id = None
        session_ready = asyncio.Event()
        response_done = asyncio.Event()  # 用于标记当前响应是否完成
        # 流式重采样器（24kHz→48kHz）- 维护 chunk 边界状态
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        
        try:
            # 连接WebSocket
            headers = {"Authorization": f"Bearer {audio_api_key}"}
            
            ws = await websockets.connect(tts_url, additional_headers=headers)
            
            # 等待连接成功事件
            async def wait_for_connection():
                """等待连接成功"""
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
                            logger.error(f"TTS服务器错误: {event}")
                            break
                except Exception as e:
                    logger.error(f"等待连接时出错: {e}")
            
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
            
            # 发送创建会话事件
            create_event = {
                "type": "tts.create",
                "data": {
                    "session_id": session_id,
                    "voice_id": voice_id,
                    "response_format": "wav",
                    "sample_rate": 24000
                }
            }
            await ws.send(json.dumps(create_event))
            
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
            async def receive_messages_initial():
                """初始接收任务"""
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")
                        
                        if event_type == "tts.response.error":
                            logger.error(f"TTS错误: {event}")
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
                                    response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))
                            except Exception as e:
                                logger.error(f"处理音频数据时出错: {e}")
                        elif event_type in ["tts.response.done", "tts.response.audio.done"]:
                            # 服务器明确表示音频生成完成，设置完成标志
                            logger.debug(f"收到响应完成事件: {event_type}")
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
                
                if sid is None:
                    # 提交缓冲区完成当前合成
                    if ws and session_id and current_speech_id is not None:
                        try:
                            response_done.clear()  # 清除完成标志，准备等待新的完成事件
                            done_event = {
                                "type": "tts.text.done",
                                "data": {"session_id": session_id}
                            }
                            await ws.send(json.dumps(done_event))
                            # 等待服务器返回响应完成事件，然后关闭连接
                            try:
                                await asyncio.wait_for(response_done.wait(), timeout=20.0)
                                logger.debug("音频生成完成，主动关闭连接")
                            except asyncio.TimeoutError:
                                logger.warning("等待响应完成超时（20秒），强制关闭连接")
                            
                            # 主动关闭连接，避免连接一直保持到超时
                            if ws:
                                try:
                                    await ws.close()
                                except:  # noqa: E722
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
                            current_speech_id = None  # 清空ID以便下次重连
                        except Exception as e:
                            logger.error(f"完成生成失败: {e}")
                    continue
                
                # 新的语音ID，重新建立连接
                if current_speech_id != sid:
                    current_speech_id = sid
                    response_done.clear()
                    resampler.clear()  # 重置重采样器状态（新轮次音频不应与上轮次连续）
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
                        
                        # 创建会话
                        await ws.send(json.dumps({
                            "type": "tts.create",
                            "data": {
                                "session_id": session_id,
                                "voice_id": voice_id,
                                "response_format": "wav",
                                "sample_rate": 24000
                            }
                        }))
                        
                        # 启动新的接收任务
                        async def receive_messages():
                            try:
                                async for message in ws:
                                    event = json.loads(message)
                                    event_type = event.get("type")
                                    
                                    if event_type == "tts.response.error":
                                        logger.error(f"TTS错误: {event}")
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
                                                response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))
                                        except Exception as e:
                                            logger.error(f"处理音频数据时出错: {e}")
                                    elif event_type in ["tts.response.done", "tts.response.audio.done"]:
                                        # 服务器明确表示音频生成完成，设置完成标志
                                        logger.debug(f"收到响应完成事件: {event_type}")
                                        response_done.set()
                            except websockets.exceptions.ConnectionClosed:
                                pass
                            except Exception as e:
                                logger.error(f"消息接收出错: {e}")
                        
                        receive_task = asyncio.create_task(receive_messages())
                        
                    except Exception as e:
                        logger.error(f"重新建立连接失败: {e}")
                        continue
                
                # 检查文本有效性
                if not tts_text or not tts_text.strip():
                    continue
                
                if not ws or not session_id:
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
                except Exception as e:
                    logger.error(f"发送TTS文本失败: {e}")
                    # 连接已关闭，标记为无效以便下次重连
                    ws = None
                    session_id = None
                    current_speech_id = None  # 清空ID以强制下次重连
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
        
        except Exception as e:
            logger.error(f"StepFun实时TTS Worker错误: {e}")
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
        logger.error(f"StepFun实时TTS Worker启动失败: {e}")


def qwen_realtime_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    Qwen实时TTS worker（用于默认音色）
    使用阿里云的实时TTS API（qwen3-tts-flash-2025-09-18）
    
    Args:
        request_queue: 多进程请求队列，接收(speech_id, text)元组
        response_queue: 多进程响应队列，发送音频数据（也用于发送就绪信号）
        audio_api_key: API密钥
        voice_id: 音色ID, 默认使用"Momo"
    """
    import asyncio

    if not voice_id:
        voice_id = "Momo"
    
    async def async_worker():
        """异步TTS worker主循环"""
        tts_url = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-tts-flash-realtime-2025-11-27"
        ws = None
        current_speech_id = None
        receive_task = None
        session_ready = asyncio.Event()
        response_done = asyncio.Event()  # 用于标记当前响应是否完成
        # 流式重采样器（24kHz→48kHz）- 维护 chunk 边界状态
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        
        try:
            # 连接WebSocket
            headers = {"Authorization": f"Bearer {audio_api_key}"}
            
            # 配置会话消息模板（在重连时复用）
            # 使用 SERVER_COMMIT 模式：多次 append 文本，最后手动 commit 触发合成
            # 这样可以累积文本，避免"一个字一个字往外蹦"的问题
            config_message = {
                "type": "session.update",
                "event_id": f"event_{int(time.time() * 1000)}",
                "session": {
                    "mode": "server_commit",
                    "voice": voice_id,
                    "response_format": "pcm",
                    "sample_rate": 24000,
                    "channels": 1,
                    "bit_depth": 16
                }
            }
            
            ws = await websockets.connect(tts_url, additional_headers=headers)
            
            # 等待并处理初始消息
            async def wait_for_session_ready():
                """等待会话创建确认"""
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")
                        
                        # Qwen TTS API 返回 session.updated 而不是 session.created
                        if event_type in ["session.created", "session.updated"]:
                            session_ready.set()
                            break
                        elif event_type == "error":
                            logger.error(f"TTS服务器错误: {event}")
                            break
                except Exception as e:
                    logger.error(f"等待会话就绪时出错: {e}")
            
            # 发送配置
            await ws.send(json.dumps(config_message))
            
            # 等待会话就绪（超时5秒）
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
            
            # 发送就绪信号
            logger.info("Qwen TTS 已就绪，发送就绪信号")
            response_queue.put(("__ready__", True))
            
            # 初始接收任务（会在每次新 speech_id 时重新创建）
            async def receive_messages_initial():
                """初始接收任务"""
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")
                        
                        if event_type == "error":
                            logger.error(f"TTS错误: {event}")
                        elif event_type == "response.audio.delta":
                            try:
                                audio_bytes = base64.b64decode(event.get("delta", ""))
                                audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                                # 使用流式重采样器 24000Hz -> 48000Hz
                                response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))
                            except Exception as e:
                                logger.error(f"处理音频数据时出错: {e}")
                        elif event_type in ["response.done", "response.audio.done", "output.done"]:
                            # 服务器明确表示音频生成完成，设置完成标志
                            logger.debug(f"收到响应完成事件: {event_type}")
                            response_done.set()
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    logger.error(f"消息接收出错: {e}")
            
            receive_task = asyncio.create_task(receive_messages_initial())
            
            # 主循环：处理请求队列
            loop = asyncio.get_running_loop()
            while True:
                # 非阻塞检查队列
                try:
                    sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                except Exception:
                    break
                
                if sid is None:
                    # 提交缓冲区完成当前合成（仅当之前有文本时）
                    if ws and session_ready.is_set() and current_speech_id is not None:
                        try:
                            response_done.clear()  # 清除完成标志，准备等待新的完成事件
                            await ws.send(json.dumps({
                                "type": "input_text_buffer.commit",
                                "event_id": f"event_{int(time.time() * 1000)}_interrupt_commit"
                            }))
                            # 等待服务器返回响应完成事件，然后关闭连接
                            try:
                                await asyncio.wait_for(response_done.wait(), timeout=20.0)
                                logger.debug("音频生成完成，主动关闭连接")
                            except asyncio.TimeoutError:
                                logger.warning("等待响应完成超时（20秒），强制关闭连接")
                            
                            # 主动关闭连接，避免连接一直保持到超时
                            if ws:
                                try:
                                    await ws.close()
                                except:  # noqa: E722
                                    pass
                                ws = None
                            if receive_task and not receive_task.done():
                                receive_task.cancel()
                                try:
                                    await receive_task
                                except asyncio.CancelledError:
                                    pass
                                receive_task = None
                            session_ready.clear()
                            current_speech_id = None  # 清空ID以便下次重连
                        except Exception as e:
                            logger.error(f"提交缓冲区失败: {e}")
                    continue
                
                # 新的语音ID，重新建立连接（类似 speech_synthesis_worker 的逻辑）
                # 直接关闭旧连接，打断旧语音
                if current_speech_id != sid:
                    current_speech_id = sid
                    response_done.clear()
                    resampler.clear()  # 重置重采样器状态（新轮次音频不应与上轮次连续）
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
                    
                    # 建立新连接
                    try:
                        ws = await websockets.connect(tts_url, additional_headers=headers)
                        await ws.send(json.dumps(config_message))
                        
                        # 等待 session.created
                        session_ready.clear()
                        
                        async def wait_ready():
                            try:
                                async for message in ws:
                                    event = json.loads(message)
                                    event_type = event.get("type")
                                    # Qwen TTS API 返回 session.updated 而不是 session.created
                                    if event_type in ["session.created", "session.updated"]:
                                        session_ready.set()
                                        break
                                    elif event_type == "error":
                                        logger.error(f"等待期间收到错误: {event}")
                                        break
                            except Exception as e:
                                logger.error(f"wait_ready 异常: {e}")
                        
                        try:
                            await asyncio.wait_for(wait_ready(), timeout=2.0)
                        except asyncio.TimeoutError:
                            logger.warning("新会话创建超时")
                        
                        # 启动新的接收任务
                        async def receive_messages():
                            try:
                                async for message in ws:
                                    event = json.loads(message)
                                    event_type = event.get("type")
                                    
                                    if event_type == "error":
                                        logger.error(f"TTS错误: {event}")
                                    elif event_type == "response.audio.delta":
                                        try:
                                            audio_bytes = base64.b64decode(event.get("delta", ""))
                                            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                                            # 使用流式重采样器 24000Hz -> 48000Hz
                                            response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))
                                        except Exception as e:
                                            logger.error(f"处理音频数据时出错: {e}")
                                    elif event_type in ["response.done", "response.audio.done", "output.done"]:
                                        # 服务器明确表示音频生成完成，设置完成标志
                                        logger.debug(f"收到响应完成事件: {event_type}")
                                        response_done.set()
                            except websockets.exceptions.ConnectionClosed:
                                pass
                            except Exception as e:
                                logger.error(f"消息接收出错: {e}")
                        
                        receive_task = asyncio.create_task(receive_messages())
                        
                    except Exception as e:
                        logger.error(f"重新建立连接失败: {e}")
                        continue
                
                # 检查文本有效性
                if not tts_text or not tts_text.strip():
                    continue
                
                if not ws or not session_ready.is_set():
                    continue
                
                # 追加文本到缓冲区（不立即提交，等待响应完成时的终止信号再 commit）
                try:
                    await ws.send(json.dumps({
                        "type": "input_text_buffer.append",
                        "event_id": f"event_{int(time.time() * 1000)}",
                        "text": tts_text
                    }))
                except Exception as e:
                    logger.error(f"发送TTS文本失败: {e}")
                    # 连接已关闭，标记为无效以便下次重连
                    ws = None
                    current_speech_id = None  # 清空ID以强制下次重连
                    session_ready.clear()
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
        
        except Exception as e:
            logger.error(f"Qwen实时TTS Worker错误: {e}")
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
        logger.error(f"Qwen实时TTS Worker启动失败: {e}")


def cosyvoice_vc_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    TTS多进程worker函数，用于阿里云CosyVoice TTS
    
    Args:
        request_queue: 多进程请求队列，接收(speech_id, text)元组
        response_queue: 多进程响应队列，发送音频数据（也用于发送就绪信号）
        audio_api_key: API密钥
        voice_id: 音色ID
    """
    import re
    import dashscope
    from dashscope.audio.tts_v2 import ResultCallback, SpeechSynthesizer, AudioFormat
    
    dashscope.api_key = audio_api_key
    
    _RE_KANA = re.compile(r'[\u3040-\u309F\u30A0-\u30FF]')
    MIN_BUFFER_CHARS = 6
    
    # CosyVoice 不需要预连接，直接发送就绪信号
    logger.info("CosyVoice TTS 已就绪，发送就绪信号")
    response_queue.put(("__ready__", True))
    
    class Callback(ResultCallback):
        def __init__(self, response_queue):
            self.response_queue = response_queue
            
        def on_open(self): 
            # 连接已建立，发送就绪信号
            elapsed = time.time() - self.construct_start_time if hasattr(self, 'construct_start_time') else -1
            logger.debug(f"TTS 连接已建立 (构造到open耗时: {elapsed:.2f}s)")
            
        def on_complete(self): 
            pass
                
        def on_error(self, message: str): 
            if "request timeout after 23 seconds" not in message:
                logger.error(f"TTS Error: {message}")
            
        def on_close(self): 
            pass
            
        def on_event(self, message): 
            pass
            
        def on_data(self, data: bytes) -> None:
            # 直接转发 OGG OPUS 数据到前端解码
            self.response_queue.put(data)
            
    callback = Callback(response_queue)
    current_speech_id = None
    synthesizer = None
    char_buffer = ""
    detected_lang = None

    def _create_synthesizer(lang_hint=None):
        """创建新的 SpeechSynthesizer，可选语言提示"""
        kwargs = dict(
            model="cosyvoice-v3-plus",
            voice=voice_id,
            speech_rate=1.1,
            format=AudioFormat.OGG_OPUS_48KHZ_MONO_64KBPS,
            callback=callback,
        )
        if lang_hint:
            kwargs["language_hints"] = [lang_hint]
        callback.construct_start_time = time.time()
        syn = SpeechSynthesizer(**kwargs)
        syn.streaming_call("。")
        return syn

    def _flush_buffer():
        """检测语言、创建 synthesizer（如果需要）并刷出缓冲区，返回 (synthesizer, detected_lang, char_buffer)"""
        nonlocal synthesizer, char_buffer, detected_lang
        if not char_buffer.strip():
            char_buffer = ""
            return
        if _RE_KANA.search(char_buffer):
            detected_lang = "ja"
            logger.info("CosyVoice 检测到假名，语言标记为日文")
        if synthesizer is None:
            synthesizer = _create_synthesizer(detected_lang)
        synthesizer.streaming_call(char_buffer)
        char_buffer = ""

    while True:
        # 非阻塞检查队列，优先处理打断
        if request_queue.empty():
            time.sleep(0.01)
            continue

        sid, tts_text = request_queue.get()

        if sid is None:
            # 停止当前合成 - 告诉TTS没有更多文本了
            try:
                _flush_buffer()
            except Exception as e:
                logger.warning(f"TTS flush buffer 失败: {e}")
            if synthesizer is not None:
                try:
                    synthesizer.streaming_complete(complete_timeout_millis=20000)
                except Exception as e:
                    logger.warning(f"TTS streaming_complete 失败: {e}")
                finally:
                    try:
                        synthesizer.close()
                    except Exception:
                        pass
                    synthesizer = None   
            current_speech_id = None
            char_buffer = ""
            detected_lang = None
            continue

        if current_speech_id is None:
            current_speech_id = sid
        elif current_speech_id != sid:
            if synthesizer is not None:
                try:
                    synthesizer.close()
                except Exception:
                    pass
            synthesizer = None
            current_speech_id = sid
            char_buffer = ""
            detected_lang = None
            
        if tts_text is None or not tts_text.strip():
            time.sleep(0.01)
            continue

        # 尚未创建 synthesizer 时先缓冲，等够 MIN_BUFFER_CHARS 个字符再一起发送
        if synthesizer is None:
            char_buffer += tts_text
            if _RE_KANA.search(tts_text):
                detected_lang = "ja"
            if len(char_buffer) < MIN_BUFFER_CHARS:
                continue
            try:
                if detected_lang == "ja":
                    logger.info("CosyVoice 检测到假名，语言标记为日文")
                synthesizer = _create_synthesizer(detected_lang)
                synthesizer.streaming_call(char_buffer)
                char_buffer = ""
            except Exception as e:
                logger.error(f"TTS Init Error: {e}")
                synthesizer = None
                current_speech_id = None
                char_buffer = ""
                detected_lang = None
                time.sleep(0.1)
                continue
        else:
            try:
                synthesizer.streaming_call(tts_text)
            except Exception:
                if synthesizer is not None:
                    try:
                        synthesizer.close()
                    except Exception:
                        pass
                    synthesizer = None

                try:
                    synthesizer = _create_synthesizer(detected_lang)
                    synthesizer.streaming_call(tts_text)
                except Exception as reconnect_error:
                    logger.error(f"TTS Reconnect Error: {reconnect_error}")
                    synthesizer = None
                    current_speech_id = None


def cogtts_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    智谱AI CogTTS worker（用于默认音色）
    使用智谱AI的CogTTS API（cogtts）
    注意：CogTTS不支持流式输入，只支持流式输出
    因此需要累积文本后一次性发送，但可以流式接收音频
    
    Args:
        request_queue: 多进程请求队列，接收(speech_id, text)元组
        response_queue: 多进程响应队列，发送音频数据（也用于发送就绪信号）
        audio_api_key: API密钥
        voice_id: 音色ID，默认使用"tongtong"（支持：tongtong, chuichui, xiaochen, jam, kazi, douji, luodo）
    """
    import asyncio
    
    # 使用默认音色 "tongtong"
    if not voice_id:
        voice_id = "tongtong"
    
    async def async_worker():
        """异步TTS worker主循环"""
        tts_url = "https://open.bigmodel.cn/api/paas/v4/audio/speech"
        current_speech_id = None
        text_buffer = []  # 累积文本缓冲区
        
        # CogTTS 是基于 HTTP 的，无需建立持久连接，直接发送就绪信号
        logger.info("CogTTS TTS 已就绪，发送就绪信号")
        response_queue.put(("__ready__", True))
        
        try:
            loop = asyncio.get_running_loop()
            
            while True:
                try:
                    sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                except Exception:
                    break
                
                # 新的语音ID，清空缓冲区并重新开始
                if current_speech_id != sid and sid is not None:
                    current_speech_id = sid
                    text_buffer = []
                
                if sid is None:
                    # 收到终止信号，合成累积的文本
                    if text_buffer and current_speech_id is not None:
                        full_text = "".join(text_buffer)
                        if full_text.strip():
                            try:
                                # 发送HTTP请求进行TTS合成
                                headers = {
                                    "Authorization": f"Bearer {audio_api_key}",
                                    "Content-Type": "application/json"
                                }
                                
                                payload = {
                                    "model": "cogtts",
                                    "input": full_text[:1024],  # CogTTS最大支持1024字符
                                    "voice": voice_id,
                                    "response_format": "pcm",
                                    "encode_format": "base64",  # 返回base64编码的PCM
                                    "speed": 1.0,
                                    "volume": 1.0,
                                    "stream": True,
                                }
                                
                                # 使用异步HTTP客户端流式接收SSE响应
                                async with aiohttp.ClientSession() as session:
                                    async with session.post(tts_url, headers=headers, json=payload) as resp:
                                        if resp.status == 200:
                                            # CogTTS返回SSE格式: data: {...JSON...}
                                            # 使用缓冲区逐块读取，避免 "Chunk too big" 错误
                                            buffer = ""
                                            first_audio_received = False  # 用于调试第一个音频块
                                            async for chunk in resp.content.iter_any():
                                                # 解码并添加到缓冲区
                                                buffer += chunk.decode('utf-8')
                                                
                                                # 按行分割处理
                                                while '\n' in buffer:
                                                    line, buffer = buffer.split('\n', 1)
                                                    line = line.strip()
                                                    
                                                    # 跳过空行
                                                    if not line:
                                                        continue
                                                    
                                                    # 解析SSE格式: data: {...}
                                                    if line.startswith('data: '):
                                                        json_str = line[6:]  # 去掉 "data: " 前缀
                                                        try:
                                                            event_data = json.loads(json_str)
                                                            
                                                            # 提取音频数据: choices[0].delta.content
                                                            choices = event_data.get('choices', [])
                                                            if choices and 'delta' in choices[0]:
                                                                delta = choices[0]['delta']
                                                                audio_b64 = delta.get('content', '')
                                                                
                                                                if audio_b64:
                                                                    # Base64解码得到PCM数据
                                                                    audio_bytes = base64.b64decode(audio_b64)
                                                                    
                                                                    # 跳过过小的音频块（可能是初始化数据）
                                                                    # 至少需要 100 个采样点（约 4ms@24kHz）才处理
                                                                    if len(audio_bytes) < 200:  # 100 samples * 2 bytes
                                                                        logger.debug(f"跳过过小的音频块: {len(audio_bytes)} bytes")
                                                                        continue
                                                                    
                                                                    # CogTTS返回PCM格式（24000Hz, mono, 16bit）
                                                                    # 从返回的 return_sample_rate 获取采样率
                                                                    sample_rate = delta.get('return_sample_rate', 24000)
                                                                    
                                                                    # 转换为 float32 进行高质量重采样
                                                                    audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                                                                    
                                                                    # 对第一个音频块，裁剪掉开头的噪音部分（CogTTS有初始化噪音）
                                                                    if not first_audio_received:
                                                                        first_audio_received = True
                                                                        # 裁剪掉前 1s 的音频（通常包含初始化噪音）
                                                                        trim_samples = int(sample_rate)
                                                                        if len(audio_array) > trim_samples:
                                                                            audio_array = audio_array[trim_samples:]
                                                                            logger.debug(f"裁剪第一个音频块的前 {trim_samples} 个采样点（{trim_samples/sample_rate:.2f}秒）")
                                                                        # 对裁剪后的开头应用短淡入（10ms），平滑过渡
                                                                        fade_samples = min(int(sample_rate * 0.01), len(audio_array))
                                                                        if fade_samples > 0:
                                                                            fade_curve = np.linspace(0.0, 1.0, fade_samples)
                                                                            audio_array[:fade_samples] *= fade_curve
                                                                    
                                                                    # 使用 soxr 进行高质量重采样
                                                                    resampled = soxr.resample(audio_array, sample_rate, 48000, quality='HQ')
                                                                    # 转回 int16 格式
                                                                    resampled_int16 = (resampled * 32768.0).clip(-32768, 32767).astype(np.int16)
                                                                    response_queue.put(resampled_int16.tobytes())
                                                        except json.JSONDecodeError as e:
                                                            logger.warning(f"解析SSE JSON失败: {e}")
                                                        except Exception as e:
                                                            logger.error(f"处理音频数据时出错: {e}")
                                        else:
                                            error_text = await resp.text()
                                            logger.error(f"CogTTS API错误 ({resp.status}): {error_text}")
                            except Exception as e:
                                logger.error(f"CogTTS合成失败: {e}")
                    
                    # 清空缓冲区
                    text_buffer = []
                    continue
                
                # 累积文本到缓冲区（不立即发送）
                if tts_text and tts_text.strip():
                    text_buffer.append(tts_text)
        
        except Exception as e:
            logger.error(f"CogTTS Worker错误: {e}")
    
    # 运行异步worker
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"CogTTS Worker启动失败: {e}")


def _gemini_tts_httpx_call(http_client, url, text, voice_id, timeout_s=20):
    """
    Gemini TTS 直连：httpx POST → 解码 base64 音频 → PCM int16 bytes.
    比 google-genai SDK 更快（省去 AFC 协商和 SDK 开销），
    且使用独立连接池，不与 LLM chat 竞争同一 httpx 实例。
    """
    import base64
    wrapped = f'Say the text with a proper tone, don\'t omit or add any words:\n"{text}"'
    payload = {
        "contents": [{"parts": [{"text": wrapped}]}],
        "generationConfig": {
            "response_modalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice_id}
                }
            }
        }
    }
    r = http_client.post(url, json=payload, timeout=timeout_s)
    r.raise_for_status()
    data = r.json()
    candidates = data.get("candidates", [])
    if not candidates:
        return None
    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        return None
    inline = parts[0].get("inlineData", {})
    audio_b64 = inline.get("data")
    if not audio_b64:
        return None
    return base64.b64decode(audio_b64)


def gemini_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    Gemini TTS worker（用于默认音色）
    使用 httpx 直连 Gemini REST API（绕过 google-genai SDK 以减少 AFC 开销）
    独立连接池 + 连接预热 + 超时重试

    Args:
        request_queue: 线程队列，接收 (speech_id, text) 元组
        response_queue: 线程队列，发送音频数据（也用于发送就绪信号）
        audio_api_key: Gemini API Key
        voice_id: 音色 ID，默认 "Leda"
    """
    import httpx

    if not voice_id:
        voice_id = "Leda"

    MODEL = "gemini-2.5-flash-preview-tts"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{MODEL}:generateContent?key={audio_api_key}"
    )
    TTS_TIMEOUT = 12   # 单次请求超时（>12s 大概率是慢实例，及时放弃换下一个）
    MAX_RETRIES = 3    # 最多重试次数

    try:
        http_client = httpx.Client(
            timeout=httpx.Timeout(TTS_TIMEOUT + 2, connect=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
    except Exception as e:
        logger.error(f"❌ Gemini TTS httpx 客户端初始化失败: {e}")
        response_queue.put(("__ready__", False))
        while True:
            try:
                sid, _ = request_queue.get()
                if sid is None:
                    continue
            except Exception:
                break
        return

    # TLS 连接预热：只做 HTTPS 握手，不消耗 TTS 配额
    try:
        logger.info("Gemini TTS TLS 预热中...")
        http_client.get(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{MODEL}",
            params={"key": audio_api_key},
            timeout=10,
        )
        logger.info("Gemini TTS TLS 预热完成")
    except Exception as e:
        logger.warning(f"Gemini TTS TLS 预热失败（不影响后续使用）: {e}")

    logger.info(f"Gemini TTS 已就绪，发送就绪信号 (response_queue id={id(response_queue):#x})")
    response_queue.put(("__ready__", True))

    current_speech_id = None
    text_buffer = []

    while True:
        try:
            sid, tts_text = request_queue.get()
        except Exception:
            break

        if current_speech_id != sid and sid is not None:
            current_speech_id = sid
            text_buffer = []

        if sid is None:
            if text_buffer and current_speech_id is not None:
                full_text = "".join(text_buffer)
                if full_text.strip():
                    logger.info(f"Gemini TTS 开始合成: {len(full_text)} chars, voice={voice_id}")
                    audio_data = None
                    for attempt in range(1, MAX_RETRIES + 1):
                        t0 = time.time()
                        try:
                            audio_data = _gemini_tts_httpx_call(
                                http_client, url, full_text, voice_id,
                                timeout_s=TTS_TIMEOUT,
                            )
                            dt = time.time() - t0
                            if audio_data:
                                logger.info(f"Gemini TTS API 返回: {len(audio_data)}B, {dt:.1f}s (attempt {attempt})")
                            break
                        except Exception as e:
                            dt = time.time() - t0
                            logger.warning(f"Gemini TTS attempt {attempt}/{MAX_RETRIES} 失败 ({dt:.1f}s): {e}")

                    if audio_data:
                        audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
                        resampled = soxr.resample(audio_array, 24000, 48000, quality='HQ')
                        resampled_int16 = (resampled * 32768.0).clip(-32768, 32767).astype(np.int16)
                        audio_bytes = resampled_int16.tobytes()
                        response_queue.put(audio_bytes)
                        logger.info(
                            f"Gemini TTS 合成完成: {len(resampled_int16)} samples, "
                            f"{len(audio_bytes)}B → queue(id={id(response_queue):#x}, qsize≈{response_queue.qsize()})"
                        )
                    else:
                        logger.warning("Gemini TTS 所有尝试均未返回音频数据")
                else:
                    logger.debug("Gemini TTS 跳过: 累积文本为空白")
            else:
                logger.debug(f"Gemini TTS flush 无操作: buffer_len={len(text_buffer)}, speech_id={current_speech_id}")

            text_buffer = []
            current_speech_id = None
            continue

        if tts_text and tts_text.strip():
            text_buffer.append(tts_text)


def openai_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    OpenAI TTS worker（用于默认音色）
    使用 OpenAI 的 TTS API（gpt-4o-mini-tts）
    注意：OpenAI TTS 不支持流式输入，只支持流式输出
    因此需要累积文本后一次性发送，但可以流式接收音频
    
    Args:
        request_queue: 多进程请求队列，接收(speech_id, text)元组
        response_queue: 多进程响应队列，发送音频数据（也用于发送就绪信号）
        audio_api_key: API密钥
        voice_id: 音色ID，默认使用"marin"（支持：marin, alloy, ash, ballad, coral, echo, fable, onyx, nova, sage, shimmer）
    """
    import asyncio
    
    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.error("❌ 无法导入 openai 库，OpenAI TTS 不可用")
        response_queue.put(("__ready__", False))
        while True:
            try:
                sid, _ = request_queue.get()
                if sid is None:
                    continue
            except Exception:
                break
        return
    
    # 使用默认音色 "marin"
    if not voice_id:
        voice_id = "marin"
    
    async def async_worker():
        """异步TTS worker主循环"""
        current_speech_id = None
        text_buffer = []  # 累积文本缓冲区
        
        # 初始化 OpenAI 客户端
        client = AsyncOpenAI(api_key=audio_api_key)
        
        # OpenAI TTS 是基于 HTTP 的，无需建立持久连接，直接发送就绪信号
        logger.info("OpenAI TTS 已就绪，发送就绪信号")
        response_queue.put(("__ready__", True))
        
        try:
            loop = asyncio.get_running_loop()
            
            while True:
                try:
                    sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                except Exception:
                    break
                
                # 新的语音ID，清空缓冲区并重新开始
                if current_speech_id != sid and sid is not None:
                    current_speech_id = sid
                    text_buffer = []
                
                if sid is None:
                    # 收到终止信号，合成累积的文本
                    if text_buffer and current_speech_id is not None:
                        full_text = "".join(text_buffer)
                        if full_text.strip():
                            try:
                                # 使用 OpenAI TTS API 进行流式合成
                                # PCM 格式: 24000Hz, 16-bit, mono
                                async with client.audio.speech.with_streaming_response.create(
                                    model="gpt-4o-mini-tts",
                                    voice=voice_id,
                                    input=full_text,
                                    response_format="pcm",
                                ) as response:
                                    # 流式接收音频数据
                                    async for chunk in response.iter_bytes(chunk_size=4096):
                                        if chunk:
                                            # OpenAI TTS 返回 PCM 16-bit @ 24000Hz
                                            audio_array = np.frombuffer(chunk, dtype=np.int16)
                                            # 重采样到 48000Hz
                                            resampled_bytes = _resample_audio(audio_array, 24000, 48000)
                                            response_queue.put(resampled_bytes)
                                            
                            except Exception as e:
                                logger.error(f"OpenAI TTS 合成失败: {e}")
                    
                    # 清空缓冲区
                    text_buffer = []
                    current_speech_id = None
                    continue
                
                # 累积文本到缓冲区（不立即发送）
                if tts_text and tts_text.strip():
                    text_buffer.append(tts_text)
        
        except Exception as e:
            logger.error(f"OpenAI TTS Worker错误: {e}")
    
    # 运行异步worker
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"OpenAI TTS Worker启动失败: {e}")


def gptsovits_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """GPT-SoVITS TTS Worker - 使用 v3 WebSocket stream-input 双工模式
    
    Args:
        request_queue: 多进程请求队列，接收 (speech_id, text) 元组
        response_queue: 多进程响应队列，发送音频数据（也用于发送就绪信号）
        audio_api_key: API密钥（未使用，保持接口一致）
        voice_id: v3 声音配置ID，格式为 "voice_id" 或 "voice_id|高级参数JSON"
                  例如: "my_voice" 或 "my_voice|{\"speed\":1.2,\"text_lang\":\"all_zh\"}"
    
    配置项（通过 TTS_MODEL_URL 设置）:
        base_url: GPT-SoVITS API 地址，如 "http://127.0.0.1:9881"
                  会自动转换为 ws:// 协议用于 WebSocket 连接
    """
    _ = audio_api_key  # 未使用，但保持接口一致

    # 获取配置
    cm = get_config_manager()
    tts_config = cm.get_model_api_config('tts_custom')
    base_url = (tts_config.get('base_url') or 'http://127.0.0.1:9881').rstrip('/')

    # 转换为 WS URL
    if base_url.startswith('http://'):
        ws_base = 'ws://' + base_url[7:]
    elif base_url.startswith('https://'):
        ws_base = 'wss://' + base_url[8:]
    elif base_url.startswith('ws://') or base_url.startswith('wss://'):
        ws_base = base_url
    else:
        ws_base = 'ws://' + base_url

    WS_URL = f'{ws_base}/api/v3/tts/stream-input'

    # 剥离 gsv: 前缀（角色系统用于标识 GPT-SoVITS voice_id 的路由前缀）
    # 解析 voice_id：支持 "voice_id" 或 "voice_id|{JSON高级参数}" 格式
    extra_params = {}
    raw_voice = voice_id.strip() if voice_id else ""
    if raw_voice.startswith(GSV_VOICE_PREFIX):
        raw_voice = raw_voice[len(GSV_VOICE_PREFIX):].strip()
    if '|' in raw_voice:
        parts = raw_voice.split('|', 1)
        v3_voice_id = parts[0].strip() or "_default"
        try:
            extra_params = json.loads(parts[1])
            if not isinstance(extra_params, dict):
                logger.warning(f"[GPT-SoVITS v3] 高级参数不是对象，已忽略: {type(extra_params).__name__}")
                extra_params = {}
        except (json.JSONDecodeError, IndexError, TypeError, ValueError) as e:
            logger.warning(f"[GPT-SoVITS v3] voice_id 高级参数解析失败，忽略: {e}")
            extra_params = {}
    else:
        v3_voice_id = raw_voice or "_default"

    # 预加载 websockets State（兼容不同版本）
    try:
        from websockets.connection import State as _WsState
    except (ImportError, AttributeError):
        _WsState = None

    def _ws_is_open(ws_conn):
        """检查 WS 连接是否仍然打开（兼容 websockets v14+/v16）"""
        if ws_conn is None:
            return False
        if _WsState is not None:
            return getattr(ws_conn, 'state', None) is _WsState.OPEN
        # fallback: 旧版 websockets
        return not getattr(ws_conn, 'closed', True)

    def _extract_pcm_from_wav(wav_bytes: bytes) -> tuple:
        """从 WAV chunk 中提取 PCM 数据和采样率"""
        if len(wav_bytes) < 44:
            return None, 0
        src_rate = int.from_bytes(wav_bytes[24:28], 'little')
        pcm_data = wav_bytes[44:]
        if len(pcm_data) < 2:
            return None, 0
        # 确保偶数长度
        if len(pcm_data) % 2 != 0:
            pcm_data = pcm_data[:-1]
        return pcm_data, src_rate

    async def async_worker():
        """异步 TTS worker 主循环 - WebSocket 双工模式"""
        ws = None
        receive_task = None
        current_speech_id = None
        resampler = None

        async def receive_loop(ws_conn):
            """独立接收协程：处理 WS 返回的音频 chunk 和 JSON 消息"""
            nonlocal resampler
            try:
                async for message in ws_conn:
                    if isinstance(message, bytes):
                        # 每个 binary frame 是完整 WAV chunk（含 header）
                        pcm_data, src_rate = _extract_pcm_from_wav(message)
                        if pcm_data is not None and len(pcm_data) > 0:
                            audio_array = np.frombuffer(pcm_data, dtype=np.int16)
                            if src_rate != 48000:
                                if resampler is None:
                                    resampler = soxr.ResampleStream(src_rate, 48000, 1, dtype='float32')
                                resampled_bytes = _resample_audio(audio_array, src_rate, 48000, resampler)
                            else:
                                resampled_bytes = audio_array.tobytes()
                            response_queue.put(resampled_bytes)
                    else:
                        # JSON 消息（日志用）
                        try:
                            msg = json.loads(message)
                            msg_type = msg.get('type', '')
                            if msg_type == 'sentence':
                                logger.debug(f"[GPT-SoVITS v3] 合成: {msg.get('text', '')[:30]}...")
                            elif msg_type == 'sentence_done':
                                logger.debug(f"[GPT-SoVITS v3] 句完成 (task={msg.get('task_id')}, chunks={msg.get('chunks_sent', '?')})")
                            elif msg_type == 'done':
                                logger.debug("[GPT-SoVITS v3] 会话完成")
                            elif msg_type == 'error':
                                logger.error(f"[GPT-SoVITS v3] 服务端错误: {msg.get('message', '')}")
                            elif msg_type == 'flushed':
                                logger.debug("[GPT-SoVITS v3] flush 完成")
                        except json.JSONDecodeError:
                            pass
            except websockets.exceptions.ConnectionClosed:
                logger.debug("[GPT-SoVITS v3] WS 连接已关闭")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"[GPT-SoVITS v3] 接收循环异常: {e}")

        async def close_session(ws_conn, recv_task, send_end=True):
            """关闭当前 WS 会话"""
            nonlocal resampler
            if send_end and _ws_is_open(ws_conn):
                try:
                    await ws_conn.send(json.dumps({"cmd": "end"}))
                    # 等待 done 消息（最多 30 秒，让推理完成）
                    await asyncio.wait_for(recv_task, timeout=30.0)
                except (asyncio.TimeoutError, Exception):
                    pass
            if recv_task and not recv_task.done():
                recv_task.cancel()
                try:
                    await recv_task
                except (asyncio.CancelledError, Exception):
                    pass
            if _ws_is_open(ws_conn):
                try:
                    await ws_conn.close()
                except Exception:
                    pass
            resampler = None

        async def create_connection():
            """创建新的 WS 连接并发送 init"""
            nonlocal ws, receive_task, resampler
            resampler = None

            logger.debug(f"[GPT-SoVITS v3] 连接: {WS_URL}")
            ws = await websockets.connect(WS_URL, ping_interval=None, max_size=10 * 1024 * 1024)

            # 发送 init 指令（合并高级参数，过滤保留字段防止覆盖）
            safe_params = {k: v for k, v in extra_params.items() if k not in ("cmd", "voice_id")}
            init_msg = {"cmd": "init", "voice_id": v3_voice_id, **safe_params}
            await ws.send(json.dumps(init_msg))

            # 等待 ready 响应
            ready_msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
            ready_data = json.loads(ready_msg)
            if ready_data.get('type') != 'ready':
                raise RuntimeError(f"init 失败: {ready_data}")

            logger.debug(f"[GPT-SoVITS v3] 会话就绪 (voice={v3_voice_id})")

            # 启动接收协程
            receive_task = asyncio.create_task(receive_loop(ws))
            return ws

        # ─── 初始连接验证 ───
        try:
            await create_connection()
            logger.info(f"[GPT-SoVITS v3] TTS 已就绪 (WS 双工模式): {WS_URL}")
            logger.info(f"  voice_id: {v3_voice_id}")
            response_queue.put(("__ready__", True))
        except Exception as e:
            logger.error(f"[GPT-SoVITS v3] 初始连接失败: {e}")
            logger.error("请确保 GPT-SoVITS 服务已运行且端口正确")
            response_queue.put(("__ready__", False))
            return

        # ─── 主循环 ───
        try:
            loop = asyncio.get_running_loop()

            while True:
                try:
                    sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                except Exception:
                    break

                # speech_id 变化 → 打断旧会话，创建新连接
                # 打断时不发 end（避免等待推理完成），直接关闭连接
                if sid != current_speech_id and sid is not None:
                    if _ws_is_open(ws):
                        await close_session(ws, receive_task, send_end=False)
                        ws = None
                        receive_task = None
                    current_speech_id = sid
                    for _retry in range(3):
                        try:
                            await create_connection()
                            break
                        except Exception as e:
                            logger.warning(f"[GPT-SoVITS v3] 连接失败 (retry {_retry+1}/3): {e}")
                            ws = None
                            if _retry < 2:
                                await asyncio.sleep(0.5 * (2 ** _retry))
                    else:
                        logger.error("[GPT-SoVITS v3] 连接重试耗尽，跳过当前文本")
                        continue

                if sid is None:
                    # 终止信号：发送 end 关闭会话（v3 end 会自动 flush 剩余文本）
                    if _ws_is_open(ws):
                        await close_session(ws, receive_task, send_end=True)
                        ws = None
                        receive_task = None
                    current_speech_id = None
                    continue

                if not tts_text or not tts_text.strip():
                    continue

                # 用 append 累积碎片文本，v3 TextBuffer 自动按标点切句推理
                if _ws_is_open(ws):
                    try:
                        await ws.send(json.dumps({"cmd": "append", "data": tts_text}))
                        logger.debug(f"[GPT-SoVITS v3] append: {tts_text[:30]}...")
                    except Exception as e:
                        logger.error(f"[GPT-SoVITS v3] 发送失败: {e}")
                        ws = None
                        receive_task = None
                        current_speech_id = None

        except Exception as e:
            logger.error(f"[GPT-SoVITS v3] Worker 错误: {e}")
        finally:
            # 清理
            if _ws_is_open(ws):
                await close_session(ws, receive_task, send_end=False)

    # 运行异步 worker
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"[GPT-SoVITS v3] Worker 启动失败: {e}")


def dummy_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    空的TTS worker（用于不支持TTS的core_api）
    持续清空请求队列但不生成任何音频，使程序正常运行但无语音输出
    
    Args:
        request_queue: 多进程请求队列，接收(speech_id, text)元组
        response_queue: 多进程响应队列（也用于发送就绪信号）
        audio_api_key: API密钥（不使用）
        voice_id: 音色ID（不使用）
    """
    logger.warning("TTS Worker 未启用，不会生成语音")
    
    # 立即发送就绪信号
    response_queue.put(("__ready__", True))
    
    while True:
        try:
            # 持续清空队列以避免阻塞，但不做任何处理
            sid, tts_text = request_queue.get()
            # 如果收到结束信号，继续等待下一个请求
            if sid is None:
                continue
        except Exception as e:
            logger.error(f"Dummy TTS Worker 错误: {e}")
            break


def get_tts_worker(core_api_type='qwen', has_custom_voice=False):
    """
    根据 core_api 类型和是否有自定义音色，返回对应的 TTS worker 函数
    
    Args:
        core_api_type: core API 类型 ('qwen', 'step', 'glm' 等)
        has_custom_voice: 是否有自定义音色 (voice_id)
    
    Returns:
        对应的 TTS worker 函数
    """

    try:
        cm = get_config_manager()
        tts_config = cm.get_model_api_config('tts_custom')
        # 只有当 is_custom=True（即 ENABLE_CUSTOM_API=true 且用户明确配置了自定义 TTS）时才使用本地 worker
        if tts_config.get('is_custom'):
            base_url = tts_config.get('base_url') or ''
            # GPT-SoVITS v3：配置 http/https URL，worker 内部自动转为 ws:// 连接
            # local_cosyvoice：配置 ws:// URL，直接使用 WebSocket
            if base_url.startswith('http://') or base_url.startswith('https://'):
                return gptsovits_tts_worker
            return local_cosyvoice_worker
    except Exception as e:
        logger.warning(f'TTS调度器检查报告:{e}')

    # 如果有自定义音色，使用 CosyVoice（仅阿里云支持）
    if has_custom_voice:
        return cosyvoice_vc_tts_worker

    # 没有自定义音色时，使用与 core_api 匹配的默认 TTS
    if core_api_type == 'qwen':
        return qwen_realtime_tts_worker
    if core_api_type == 'free':
        return partial(step_realtime_tts_worker, free_mode=True)
    elif core_api_type == 'step':
        return step_realtime_tts_worker
    elif core_api_type == 'glm':
        return cogtts_tts_worker
    elif core_api_type == 'gemini':
        return gemini_tts_worker
    elif core_api_type == 'openai':
        return openai_tts_worker
    else:
        logger.error(f"{core_api_type}不支持原生TTS，请使用自定义语音")
        return dummy_tts_worker


def local_cosyvoice_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    本地 CosyVoice WebSocket Worker（OpenAI 兼容 bistream 版本）
    适配 openai_server.py 定义的 /v1/audio/speech/stream 接口
    
    协议流程：
    1. 连接后发送 config: {"voice": ..., "speed": ...}
    2. 发送文本: {"text": ...}
    3. 发送结束信号: {"event": "end"}
    4. 接收 bytes 音频数据（16-bit PCM, 22050Hz）
    
    特性：
    - 双工流：发送和接收独立运行，互不阻塞
    - 打断支持：speech_id 变化时关闭旧连接，打断旧语音
    - 非阻塞：异步架构，不会卡住主循环
    
    注意：audio_api_key 参数未使用（本地模式不需要 API Key），保留是为了与其他 worker 保持统一签名
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
                if sid is None:
                    continue
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

        async def receive_loop(ws_conn):
            """独立接收任务，处理音频流"""
            try:
                async for message in ws_conn:
                    if isinstance(message, bytes):
                        # 服务器返回 16-bit PCM @ 22050Hz
                        audio_array = np.frombuffer(message, dtype=np.int16)
                        resampled_bytes = _resample_audio(audio_array, SRC_RATE, 48000, resampler)
                        response_queue.put(resampled_bytes)
            except websockets.exceptions.ConnectionClosed:
                logger.debug("本地 WebSocket 连接已关闭")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"接收循环异常: {e}")

        async def send_end_signal(ws_conn):
            """发送结束信号（文本已在主循环中实时发送，此处只需发送 end）"""
            try:
                await ws_conn.send(json.dumps({"event": "end"}))
                logger.debug("发送结束信号")
            except Exception as e:
                logger.error(f"发送结束信号失败: {e}")

        async def create_connection():
            """创建新连接并发送配置"""
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
            receive_task = asyncio.create_task(receive_loop(ws))
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

            # speech_id 变化 -> 打断旧语音，建立新连接
            if sid != current_speech_id and sid is not None:
                # 发送结束信号（文本已在实时流中发送过了）
                if ws:
                    await send_end_signal(ws)
                
                current_speech_id = sid
                try:
                    await create_connection()
                except Exception as e:
                    logger.error(f"重连失败: {e}")
                    ws = None
                    continue

            if sid is None:
                # 终止信号：发送结束信号
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
                    logger.debug(f"发送合成片段: {tts_text}")
                except Exception as e:
                    logger.error(f"发送失败: {e}")
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