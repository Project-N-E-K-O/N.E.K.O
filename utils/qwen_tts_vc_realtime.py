"""
这份代码用于使用阿里云的实时TTS API，实现语音合成。这些代码已经通过测试。但是，目前改模型存在严重bug，无法正常使用。
这里的代码目前仅供备份与分享。
"""

### 以下代码用于替换main_server.py中的voice_clone函数
@app.post('/api/voice_clone')
async def voice_clone(file: UploadFile = File(...), prefix: str = Form(...)):
    import os
    import base64
    import pathlib
    import requests
    import wave
    import struct
    import mimetypes
    
    # 默认配置
    DEFAULT_TARGET_MODEL = "qwen-tts-vc-realtime-2025-08-20"
    DEFAULT_PREFERRED_NAME = "user_customized"
    core_config = _config_manager.get_core_config()
    fallback_audio_api_key = core_config.get('AUDIO_API_KEY', '')
    
    def validate_audio_file(file_path: str) -> tuple[str, str]:
        """
        验证音频文件类型和格式
        返回: (mime_type, error_message)
        """
        file_path_obj = pathlib.Path(file_path)
        file_extension = file_path_obj.suffix.lower()
        
        # 检查文件扩展名
        if file_extension not in ['.wav', '.mp3']:
            return "", f"不支持的文件格式: {file_extension}。仅支持 WAV 和 MP3 格式。"
        
        # 根据扩展名确定MIME类型
        if file_extension == '.wav':
            mime_type = "audio/wav"
            # 检查WAV文件是否为16bit
            try:
                with wave.open(file_path, 'rb') as wav_file:
                    # 检查采样宽度（bit depth）
                    if wav_file.getsampwidth() != 2:  # 2 bytes = 16 bits
                        return "", f"WAV文件必须是16bit格式，当前文件是{wav_file.getsampwidth() * 8}bit。"
                    
                    # 检查声道数（建议单声道）
                    channels = wav_file.getnchannels()
                    if channels > 1:
                        return "", f"建议使用单声道WAV文件，当前文件有{channels}个声道。"
                    
                    # 检查采样率
                    sample_rate = wav_file.getframerate()
                    if sample_rate not in [8000, 16000, 22050, 44100, 48000]:
                        return "", f"建议使用标准采样率(8000, 16000, 22050, 44100, 48000)，当前文件采样率: {sample_rate}Hz。"
                    
            except Exception as e:
                return "", f"WAV文件格式错误: {str(e)}"
                
        elif file_extension == '.mp3':
            mime_type = "audio/mpeg"
            # MP3文件格式检查相对简单，主要检查文件头
            try:
                with open(file_path, 'rb') as f:
                    header = f.read(10)
                    # 检查MP3文件头标识
                    if not (header.startswith(b'\xff\xfb') or header.startswith(b'\xff\xf3') or 
                           header.startswith(b'\xff\xf2') or header.startswith(b'\xff\xe3')):
                        return "", "MP3文件格式无效或已损坏。"
            except Exception as e:
                return "", f"MP3文件读取错误: {str(e)}"
        
        return mime_type, ""
    
    def create_voice(file_path: str,
                     target_model: str = DEFAULT_TARGET_MODEL,
                     preferred_name: str = DEFAULT_PREFERRED_NAME,
                     audio_mime_type: str = "audio/wav") -> str:
        """
        创建音色，并返回 voice 参数
        """
        # 若没有将API Key配置到环境变量中，需将下一行替换为：api_key = "your-api-key"。your-api-key为实际的API Key，格式为"sk-xxxx"。
        api_key = os.getenv("DASHSCOPE_API_KEY") or fallback_audio_api_key
        
        if not api_key:
            raise RuntimeError("未配置DASHSCOPE_API_KEY环境变量或AUDIO_API_KEY")

        file_path_obj = pathlib.Path(file_path)
        if not file_path_obj.exists():
            raise FileNotFoundError(f"音频文件不存在: {file_path}")

        base64_str = base64.b64encode(file_path_obj.read_bytes()).decode()
        data_uri = f"data:{audio_mime_type};base64,{base64_str}"

        url = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
        payload = {
            "model": "qwen-voice-enrollment", # 不要修改该值
            "input": {
                "action": "create",
                "target_model": target_model,
                "preferred_name": preferred_name,
                "audio": {"data": data_uri}
            }
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        resp = requests.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"创建 voice 失败: {resp.status_code}, {resp.text}")

        try:
            return resp.json()["output"]["voice"]
        except (KeyError, ValueError) as e:
            raise RuntimeError(f"解析 voice 响应失败: {e}")

    temp_path = f'tmp_{file.filename}'
    try:
        # 保存上传的文件到临时路径
        with open(temp_path, 'wb') as f:
            f.write(await file.read())
        
        # 验证音频文件
        mime_type, error_msg = validate_audio_file(temp_path)
        if error_msg:
            return JSONResponse({'error': error_msg}, status_code=400)
        
        # 使用新的create_voice函数直接处理音频文件
        voice_id = create_voice(
            file_path=temp_path,
            target_model=DEFAULT_TARGET_MODEL,
            preferred_name=prefix,  # 使用用户提供的前缀作为preferred_name
            audio_mime_type=mime_type
        )
        
        return JSONResponse({
            'voice_id': voice_id,
            'request_id': None,  # 新接口不返回request_id
            'file_url': None     # 不再需要文件URL
        })
        
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)
    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass


### 以下代码用于替换core.py
"""
本文件是主逻辑文件，负责管理整个对话流程。当选择不使用TTS时，将会通过OpenAI兼容接口使用Omni模型的原生语音输出。
当选择使用TTS时，将会通过额外的TTS API去合成语音。注意，TTS API的输出是流式输出、且需要与用户输入进行交互，实现打断逻辑。
TTS部分使用TTSRealtimeClient与阿里云实时TTS API进行WebSocket通信，支持流式音频输出和打断逻辑。
"""
import asyncio
import json
import struct  # For packing audio data
import threading
import re
import requests
import logging
from datetime import datetime
from websockets import exceptions as web_exceptions
from fastapi import WebSocket, WebSocketDisconnect
from utils.frontend_utils import contains_chinese, replace_blank, replace_corner_mark, remove_bracket, \
    is_only_punctuation, split_paragraph
from main_helper.omni_realtime_client import OmniRealtimeClient
import base64
from io import BytesIO
from PIL import Image
from config import MEMORY_SERVER_PORT
from utils.config_manager import get_config_manager
from multiprocessing import Process, Queue as MPQueue
from uuid import uuid4
import numpy as np
import httpx 
import websockets
import time
from enum import Enum

# Setup logger for this module
logger = logging.getLogger(__name__)
_config_manager = get_config_manager()

class SessionMode(Enum):
    SERVER_COMMIT = "server_commit"
    COMMIT = "commit"

async def tts_audio_worker(in_queue: MPQueue, out_queue: MPQueue):
    """
    异步音频处理协程：从 in_queue 读取 24kHz PCM（int16 LE），重复采样为 48kHz 后写入 out_queue。
    收到 None 作为哨兵值时退出。为兼容 multiprocessing，进程入口见 _tts_audio_worker_entry。
    """
    import numpy as _np
    import asyncio as _asyncio
    loop = _asyncio.get_running_loop()

    while True:
        # MPQueue.get() 是阻塞的；放到线程池避免阻塞事件循环
        audio_bytes = await loop.run_in_executor(None, in_queue.get)

        if audio_bytes is None:
            # 透传一个 None 给发送协程（_start_audio_sender），帮助其尽快结束
            await loop.run_in_executor(None, out_queue.put, None)
            break

        try:
            audio_array = _np.frombuffer(audio_bytes, dtype=_np.int16)
            resampled = _np.repeat(audio_array, 2) # 24kHz -> 48kHz（简单重复法）
            await loop.run_in_executor(None, out_queue.put, resampled.tobytes())
        except Exception:
        # 处理失败则退回原始数据，避免中断播放链路
            await loop.run_in_executor(None, out_queue.put, audio_bytes)

def _tts_audio_worker_entry(in_queue: MPQueue, out_queue: MPQueue):
    """
    进程入口（同步）：在独立事件循环中运行异步 tts_audio_worker。
    - 顶层定义，适配 Windows 的 spawn（可被 pickle）。
    - 保持与原多进程设计兼容。
    """
    import asyncio as _asyncio
    import logging as _logging


    try:
        _asyncio.run(tts_audio_worker(in_queue, out_queue))
    except Exception as e:
        _logging.getLogger(__name__).error(f"TTS worker fatal error: {e}")

class TTSRealtimeClient:
    """
    与 TTS Realtime API 交互的客户端。

    该类提供了连接 TTS Realtime API、发送文本数据、获取音频输出以及管理 WebSocket 连接的相关方法。

    属性说明:
        base_url (str):
            Realtime API 的基础地址。
        api_key (str):
            用于身份验证的 API Key。
        voice (str):
            服务器合成语音所使用的声音。
        mode (SessionMode):
            会话模式，可选 server_commit 或 commit。
        audio_callback (Callable[[bytes], None]):
            接收音频数据的回调函数。
    """
    def __init__(
        self,
        base_url: str,
        api_key: str,
        voice: str = "Cherry",
        mode: SessionMode = SessionMode.SERVER_COMMIT,
        audio_callback=None
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.voice = voice
        self.mode = mode
        self.ws = None
        self.audio_callback = audio_callback

        # 当前回复状态
        self._current_response_id = None
        self._current_item_id = None
        self._is_responding = False

        # 音频完成状态跟踪
        self._audio_completion_event = asyncio.Event()
        self._audio_complete = False

        # 多进程队列与后台任务
        self._mp_in_queue = MPQueue(maxsize=64)
        self._mp_out_queue = MPQueue(maxsize=64)
        self._audio_process: Process | None = None
        self._process_stop_event = threading.Event()
        self._audio_sender_task: asyncio.Task | None = None

        # 文本缓冲区状态跟踪
        self._has_text_in_buffer = False

    async def connect(self) -> None:
        """与 TTS Realtime API 建立 WebSocket 连接。"""
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        self.ws = await websockets.connect(self.base_url, additional_headers=headers)

        # 重置音频状态
        self._audio_completion_event.clear()
        self._audio_complete = False
        # 重置文本缓冲区状态
        self._has_text_in_buffer = False
        # 启动音频处理进程和异步发送任务
        self._process_stop_event.clear()
        self._start_audio_process()
        await self._start_audio_sender()

        # 设置默认会话配置
        await self.update_session({
            "mode": self.mode.value,
            "voice": self.voice,
            "response_format": "pcm",
            "sample_rate": 24000,  # TTS API只支持24kHz
            "channels": 1,
            "bit_depth": 16
        })

    async def send_event(self, event) -> None:
        """发送事件到服务器。"""
        event['event_id'] = "event_" + str(int(time.time() * 1000))
        # 减少发送事件日志，只在调试时显示
        # logger.info(f"发送TTS事件: type={event['type']}, event_id={event['event_id']}")
        await self.ws.send(json.dumps(event))

    async def update_session(self, config: dict) -> None:
        """更新会话配置。"""
        event = {
            "type": "session.update",
            "session": config
        }
        # 减少会话配置日志，只在调试时显示
        # logger.info(f"更新TTS会话配置: {event}")
        await self.send_event(event)

    async def append_text(self, text: str) -> None:
        """向 API 发送文本数据。"""
        if text and text.strip():  # 检查文本是否为空或只包含空白字符
            event = {
                "type": "input_text_buffer.append",
                "text": text
            }
            await self.send_event(event)
            self._has_text_in_buffer = True

    async def commit_text_buffer(self) -> None:
        """提交文本缓冲区以触发处理。"""
        if self._has_text_in_buffer:
            event = {
                "type": "input_text_buffer.commit"
            }
            await self.send_event(event)
            # 提交后重置状态
            self._has_text_in_buffer = False
        else:
            # 如果没有文本，直接标记音频完成
            self._audio_completion_event.set()

    async def clear_text_buffer(self) -> None:
        """清除文本缓冲区。"""
        self._has_text_in_buffer = False
        event = {
            "type": "input_text_buffer.clear"
        }
        await self.send_event(event)

    def has_text_in_buffer(self) -> bool:
        """检查文本缓冲区是否有内容"""
        return self._has_text_in_buffer

    async def finish_session(self) -> None:
        """结束会话。"""
        event = {
            "type": "session.finish"
        }
        await self.send_event(event)

    async def handle_messages(self) -> None:
        """处理来自服务器的消息。"""
        try:
            async for message in self.ws:
                event = json.loads(message)
                event_type = event.get("type")
                
                # 只记录错误和重要事件
                if event_type == "error":
                    logger.error(f"TTS错误: {event}")
                    continue
                elif event_type == "session.created":
                    session_id = event.get('session', {}).get('id')
                    logger.info(f"TTS会话创建，ID: {session_id}")
                elif event_type == "input_text_buffer.committed":
                    item_id = event.get('item_id')
                    self._has_text_in_buffer = False
                    print(event)
                    logger.info(f"TTS文本缓冲区已提交，项目ID: {item_id}")
                elif event_type == "response.created":
                    self._current_response_id = event.get("response", {}).get("id")
                    self._is_responding = True
                    logger.info(f"TTS响应已创建，ID: {self._current_response_id}")
                # 处理音频增量
                elif event_type == "response.audio.delta" and self.audio_callback:
                    try:
                        audio_bytes = base64.b64decode(event.get("delta", ""))
                        # 将音频放入输入队列，交由后台进程处理
                        self._mp_in_queue.put_nowait(audio_bytes)
                    except Exception as e:
                        logger.error(f"处理TTS音频数据时出错: {e}")
                elif event_type == "response.audio.done":
                    logger.info("TTS音频生成完成")
                    self._audio_complete = True
                elif event_type == "response.done":
                    self._is_responding = False
                    self._current_response_id = None
                    self._current_item_id = None
                    logger.info("TTS响应完成")
                    self._has_text_in_buffer = False
                    # 标记音频完成事件，由 wait_for_audio_completion 中进一步排队
                    self._audio_completion_event.set()
                    
                elif event_type == "session.finished":
                    logger.info("TTS会话已结束")

        except websockets.exceptions.ConnectionClosed:
            # 减少连接关闭日志，只在调试时显示
            # logger.info("TTS连接已关闭")
            pass
        except Exception as e:
            logger.error("TTS消息处理出错: ", str(e))

    async def wait_for_audio_completion(self, timeout: float = 10.0) -> bool:
        """等待音频完成，返回是否成功完成"""
        try:
            await asyncio.wait_for(self._audio_completion_event.wait(), timeout=timeout)
            # 等待队列清空，最多2秒
            start_time = time.time()
            while (not self._mp_in_queue.empty() or not self._mp_out_queue.empty()) and (time.time() - start_time < 2.0):
                await asyncio.sleep(0.05)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"等待音频完成超时 ({timeout}s)")
            return False

    def _start_audio_process(self):
        """
        启动后台音频处理进程。若进程已存在则直接返回。
        """
        if self._audio_process and self._audio_process.is_alive():
            return
        from multiprocessing import Process
        self._audio_process = Process(
            target=_tts_audio_worker_entry, # 进程内启动异步协程
            args=(self._mp_in_queue, self._mp_out_queue),
        )
        self._audio_process.daemon = True
        self._audio_process.start()

    def _stop_audio_process(self):
        """
        停止后台音频处理进程以及异步发送任务。
        """
        # 设置退出标志
        self._process_stop_event.set()
        # 取消发送任务
        if self._audio_sender_task and not self._audio_sender_task.done():
            self._audio_sender_task.cancel()
        self._audio_sender_task = None
        # 通知子进程退出
        if self._audio_process and self._audio_process.is_alive():
            try:
                self._mp_in_queue.put_nowait(None)
                # 等待进程退出，设置超时
                self._audio_process.join(timeout=2.0)
                
                # 如果进程还在运行，强制终止
                if self._audio_process.is_alive():
                    logger.warning("TTS 音频进程未能在超时内退出，强制终止")
                    self._audio_process.terminate()
                    self._audio_process.join(timeout=1.0)
                    
                    # 如果还是无法退出，强制杀死
                    if self._audio_process.is_alive():
                        logger.error("TTS 音频进程无法终止，强制杀死")
                        self._audio_process.kill()
                        self._audio_process.join(timeout=1.0)
            except Exception as e:
                logger.error(f"停止 TTS 音频进程时出错: {e}")
                # 强制终止进程
                try:
                    if self._audio_process.is_alive():
                        self._audio_process.terminate()
                        self._audio_process.join(timeout=1.0)
                except Exception:
                    pass
        
        # 清理进程引用
        self._audio_process = None
        
        # 清空队列
        try:
            while not self._mp_in_queue.empty():
                self._mp_in_queue.get_nowait()
            while not self._mp_out_queue.empty():
                self._mp_out_queue.get_nowait()
        except Exception:
            pass

    async def _start_audio_sender(self):
        """
        创建并启动一个异步任务，从 out_queue 中获取已重采样的音频数据，并依次调用 audio_callback。
        """
        if self._audio_sender_task and not self._audio_sender_task.done():
            return
        loop = asyncio.get_running_loop()
        async def sender():
            while not self._process_stop_event.is_set():
                try:
                    audio_bytes = await loop.run_in_executor(None, self._mp_out_queue.get)
                    if audio_bytes is None:
                        break
                    if self.audio_callback:
                        await self.audio_callback(audio_bytes)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"音频发送任务异常: {e}")
                    continue
        self._audio_sender_task = asyncio.create_task(sender())
    
    async def close(self) -> None:
        """关闭 WebSocket 连接。"""
        try:
            # 取消音频发送任务
            if self._audio_sender_task and not self._audio_sender_task.done():
                self._audio_sender_task.cancel()
                try:
                    await asyncio.wait_for(self._audio_sender_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                self._audio_sender_task = None
            
            # 关闭 WebSocket 连接
            if self.ws:
                try:
                    await asyncio.wait_for(self.ws.close(), timeout=1.0)
                except asyncio.TimeoutError:
                    logger.warning("TTS WebSocket 关闭超时")
                except Exception as e:
                    logger.warning(f"TTS WebSocket 关闭异常: {e}")
                self.ws = None
            
            # 停止音频进程和相关任务
            self._stop_audio_process()
        except Exception as e:
            logger.error(f"TTS 客户端关闭异常: {e}")
        finally:
            # 确保状态被重置
            self._has_text_in_buffer = False
            self._audio_completion_event.set()

# --- 一个带有定期上下文压缩+在线热切换的语音会话管理器 ---
class LLMSessionManager:
    def __init__(self, sync_message_queue, lanlan_name, lanlan_prompt):
        self.websocket = None
        self.sync_message_queue = sync_message_queue
        self.session = None
        self.last_time = None
        self.is_active = False
        self.active_session_is_idle = False
        self.current_expression = None
        self.tts_client = None  # TTS实时客户端
        self.tts_handler_task = None  # TTS消息处理任务
        self.lock = asyncio.Lock()  # 使用异步锁替代同步锁
        self.current_speech_id = None
        self.emoji_pattern = re.compile(r'[^\w\u4e00-\u9fff\s>][^\w\u4e00-\u9fff\s]{2,}[^\w\u4e00-\u9fff\s<]', flags=re.UNICODE)
        self.emoji_pattern2 = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                           "]+", flags=re.UNICODE)
        self.emotion_pattern = re.compile('<(.*?)>')

        self.lanlan_prompt = lanlan_prompt
        self.lanlan_name = lanlan_name
        # 获取角色相关配置
        (
            self.master_name,
            self.her_name,
            self.master_basic_config,
            self.lanlan_basic_config,
            self.name_mapping,
            self.lanlan_prompt_map,
            self.semantic_store,
            self.time_store,
            self.setting_store,
            self.recent_log
        ) = _config_manager.get_character_data()
        # 获取API相关配置
        core_config = _config_manager.get_core_config()
        self.model = core_config['CORE_MODEL']
        self.core_url = core_config['CORE_URL']
        self.core_api_key = core_config['CORE_API_KEY']
        self.core_api_type = core_config.get('CORE_API_TYPE', 'qwen')  # 获取API类型，用于判断是否启用静默超时
        self.memory_server_port = MEMORY_SERVER_PORT
        self.audio_api_key = core_config['AUDIO_API_KEY']
        self.voice_id = self.lanlan_basic_config[self.lanlan_name].get('voice_id', '')
        self.use_tts = False if not self.voice_id else True
        self.generation_config = {}  # Qwen暂时不用
        self.message_cache_for_new_session = []
        self.is_preparing_new_session = False
        self.summary_triggered_time = None
        self.initial_cache_snapshot_len = 0
        self.pending_session_warmed_up_event = None
        self.pending_session_final_prime_complete_event = None
        self.session_start_time = None
        self.pending_connector = None
        self.pending_session = None
        self.is_hot_swap_imminent = False
        # 热切换相关变量
        self.background_preparation_task = None
        self.final_swap_task = None
        self.receive_task = None
        self.message_handler_task = None
        # 任务完成后的额外回复队列（将在下一次切换时统一汇报）
        self.pending_extra_replies = []
        # 由前端控制的Agent相关开关
        self.agent_flags = {
            'agent_enabled': False,
            'computer_use_enabled': False,
            'mcp_enabled': False,
        }

        # 注册回调
        self.session = OmniRealtimeClient(
            base_url=self.core_url,
            api_key=self.core_api_key,
            model=self.model,
            voice="Cherry",
            on_text_delta=self.handle_text_data,
            on_audio_delta=self.handle_audio_data,
            on_interrupt=self.handle_interrupt,
            on_input_transcript=self.handle_input_transcript,
            on_output_transcript=self.handle_output_transcript,
            on_connection_error=self.handle_connection_error,
            on_response_done=self.handle_response_complete,
            api_type=self.core_api_type  # 传入API类型，用于判断是否启用静默超时
        )

    async def handle_interrupt(self):
        if self.use_tts:
            if self.tts_client:
                # 清除文本缓冲区并结束当前会话
                # await self.tts_client.finish_session()
                await self.tts_client.commit_text_buffer()
                
        await self.send_user_activity()

    async def handle_text_data(self, text: str, is_first_chunk: bool = False):
        """Qwen文本回调：可用于前端显示、语音合成"""
        if self.use_tts:
            if self.tts_client:
                await self.tts_client.append_text(text)
            await self.send_lanlan_response(text, is_first_chunk)
        else:
            pass
            # logger.info(f"\nAssistant: {text}")

    async def handle_response_complete(self):
        """Qwen完成回调：用于处理Core API的响应完成事件，包含TTS和热切换逻辑"""
        if self.use_tts:
            print("Response complete")
            if self.tts_client:
                # 先结束TTS会话，触发音频生成
                # await self.tts_client.finish_session()
                await self.tts_client.commit_text_buffer()
                
        self.sync_message_queue.put({'type': 'system', 'data': 'turn end'})
        
        # 直接向前端发送turn end消息
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json({'type': 'system', 'data': 'turn end'})
        except Exception as e:
            logger.error(f"💥 WS Send Turn End Error: {e}")

        # 如果有挂起的额外提示：触发热切换准备并安排renew，会在最终swap时统一植入提示
        try:
            if getattr(self, 'pending_extra_replies', None) and len(self.pending_extra_replies) > 0 \
               and not self.is_preparing_new_session and not self.is_hot_swap_imminent:
                await self._trigger_immediate_preparation_for_extra()
        except Exception as e:
            logger.error(f"💥 Extra reply preparation error: {e}")
        
        # 如果正在热切换过程中，跳过所有热切换逻辑
        if self.is_hot_swap_imminent:
            return
            
        if hasattr(self, 'is_preparing_new_session') and not self.is_preparing_new_session:
            if self.session_start_time and \
                        (datetime.now() - self.session_start_time).total_seconds() >= 40:
                logger.info("Main Listener: Uptime threshold met. Marking for new session preparation.")
                self.is_preparing_new_session = True  # Mark that we are in prep mode
                self.summary_triggered_time = datetime.now()
                self.message_cache_for_new_session = []  # Reset cache for this new cycle
                self.initial_cache_snapshot_len = 0  # Reset snapshot marker
                self.sync_message_queue.put({'type': 'system', 'data': 'renew session'}) 

        # If prep mode is active, summary time has passed, and a turn just completed in OLD session:
        # AND background task for initial warmup isn't already running
        if self.is_preparing_new_session and \
                self.summary_triggered_time and \
                (datetime.now() - self.summary_triggered_time).total_seconds() >= 10 and \
                (not self.background_preparation_task or self.background_preparation_task.done()) and \
                not (
                        self.pending_session_warmed_up_event and self.pending_session_warmed_up_event.is_set()):  # Don't restart if already warmed up
            logger.info("Main Listener: Conditions met to start BACKGROUND PREPARATION of pending session.")
            self.pending_session_warmed_up_event = asyncio.Event()  # Create event for this prep cycle
            self.background_preparation_task = asyncio.create_task(self._background_prepare_pending_session())

        # Stage 2: Trigger FINAL SWAP if pending session is warmed up AND this old session just completed a turn
        elif self.pending_session_warmed_up_event and \
                self.pending_session_warmed_up_event.is_set() and \
                not self.is_hot_swap_imminent and \
                (not self.final_swap_task or self.final_swap_task.done()):
            logger.info(
                "Main Listener: OLD session completed a turn & PENDING session is warmed up. Triggering FINAL SWAP sequence.")
            self.is_hot_swap_imminent = True  # Prevent re-triggering

            # The main cache self.message_cache_for_new_session is now "spent" for transfer purposes
            # It will be fully cleared after a successful swap by _reset_preparation_state.
            self.pending_session_final_prime_complete_event = asyncio.Event()
            self.final_swap_task = asyncio.create_task(
                self._perform_final_swap_sequence()
            )
            # The old session listener's current turn is done.
            # The final_swap_task will now manage the actual switch.
            # This listener will be cancelled by the final_swap_task.


    async def handle_audio_data(self, audio_data: bytes):
        """Qwen音频回调：推送音频到WebSocket前端"""
        if not self.use_tts:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                # 这里假设audio_data为PCM16字节流，直接推送
                audio = np.frombuffer(audio_data, dtype=np.int16)
                # 使用简单的重复采样，将 24kHz 转为 48kHz
                audio = np.repeat(audio, 2)

                await self.send_speech(audio.tobytes())
                # 你可以根据需要加上格式、isNewMessage等标记
                # await self.websocket.send_json({"type": "cozy_audio", "format": "blob", "isNewMessage": True})
            else:
                pass  # websocket未连接时忽略

    async def handle_input_transcript(self, transcript: str):
        """Qwen输入转录回调：同步转录文本到消息队列和缓存"""
        # 推送到同步消息队列
        self.sync_message_queue.put({"type": "user", "data": {"input_type": "transcript", "data": transcript.strip()}})
        # 缓存到session cache
        if hasattr(self, 'is_preparing_new_session') and self.is_preparing_new_session:
            if not hasattr(self, 'message_cache_for_new_session'):
                self.message_cache_for_new_session = []
            if len(self.message_cache_for_new_session) == 0 or self.message_cache_for_new_session[-1]['role'] == self.lanlan_name:
                self.message_cache_for_new_session.append({"role": self.master_name, "text": transcript.strip()})
            elif self.message_cache_for_new_session[-1]['role'] == self.master_name:
                self.message_cache_for_new_session[-1]['text'] += transcript.strip()
        # 可选：推送用户活动
        async with self.lock:
            self.current_speech_id = str(uuid4())

    async def handle_output_transcript(self, text: str, is_first_chunk: bool = False):
        if self.use_tts:
            if self.tts_client:
                await self.tts_client.append_text(text)
        await self.send_lanlan_response(text, is_first_chunk)

    async def send_lanlan_response(self, text: str, is_first_chunk: bool = False):
        """Qwen输出转录回调：可用于前端显示/缓存/同步。"""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                text = self.emotion_pattern.sub('', text)
                message = {
                    "type": "gemini_response",
                    "text": text,
                    "isNewMessage": is_first_chunk  # 标记是否是新消息的第一个chunk
                }
                await self.websocket.send_json(message)
                self.sync_message_queue.put({"type": "json", "data": message})
                if hasattr(self, 'is_preparing_new_session') and self.is_preparing_new_session:
                    if not hasattr(self, 'message_cache_for_new_session'):
                        self.message_cache_for_new_session = []
                    if len(self.message_cache_for_new_session) == 0 or self.message_cache_for_new_session[-1]['role']==self.master_name:
                        self.message_cache_for_new_session.append(
                            {"role": self.lanlan_name, "text": text})
                    elif self.message_cache_for_new_session[-1]['role'] == self.lanlan_name:
                        self.message_cache_for_new_session[-1]['text'] += text

        except WebSocketDisconnect:
            logger.info("Frontend disconnected.")
        except Exception as e:
            logger.error(f"💥 WS Send Lanlan Response Error: {e}")
        
    async def handle_connection_error(self, message=None):
        if message:
            if '欠费' in message:
                await self.send_status("💥 智谱API触发欠费bug。请考虑充值1元。")
            elif 'standing' in message:
                await self.send_status("💥 阿里API已欠费。")
            else:
                await self.send_status(message)
        logger.info("💥 Session closed by API Server.")
        await self.disconnected_by_server()

    def _reset_preparation_state(self, clear_main_cache=False, from_final_swap=False):
        """[热切换相关] Helper to reset flags and pending components related to new session prep."""
        self.is_preparing_new_session = False
        self.summary_triggered_time = None
        self.initial_cache_snapshot_len = 0
        if self.background_preparation_task and not self.background_preparation_task.done():  # If bg prep was running
            self.background_preparation_task.cancel()
        if self.final_swap_task and not self.final_swap_task.done() and not from_final_swap:  # If final swap was running
            self.final_swap_task.cancel()
        self.background_preparation_task = None
        self.final_swap_task = None
        self.pending_session_warmed_up_event = None
        self.pending_session_final_prime_complete_event = None

        if clear_main_cache:
            self.message_cache_for_new_session = []

    async def _cleanup_pending_session_resources(self):
        """[热切换相关] Safely cleans up ONLY PENDING connector and session if they exist AND are not the current main session."""
        # Stop any listener specifically for the pending session (if different from main listener structure)
        # The _listen_for_pending_session_response tasks are short-lived and managed by their callers.
        if self.pending_session:
            await self.pending_session.close()
        self.pending_session = None  # Managed by connector's __aexit__

    def _init_renew_status(self):
        self._reset_preparation_state(True)
        self.session_start_time = None  # 记录当前 session 开始时间
        self.pending_session = None  # Managed by connector's __aexit__
        self.is_hot_swap_imminent = False

    def normalize_text(self, text): # 对文本进行基本预处理
        text = text.strip()
        text = text.replace("\n", "")
        if contains_chinese(text):
            text = replace_blank(text)
            text = replace_corner_mark(text)
            text = text.replace(".", "。")
            text = text.replace(" - ", "，")
            text = remove_bracket(text)
            text = re.sub(r'[，、]+$', '。', text)
        else:
            text = remove_bracket(text)
        text = self.emoji_pattern2.sub('', text)
        text = self.emoji_pattern.sub('', text)
        if is_only_punctuation(text) and text not in ['<', '>']:
            return ""
        return text

    async def start_session(self, websocket: WebSocket, new=False):
        self.websocket = websocket
        async with self.lock:
            if self.is_active:
                return

        # new session时重置部分状态
        if self.use_tts:
                         # 启动TTS客户端
             if self.tts_client is None:
                 # 使用阿里云实时TTS API
                 tts_url = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen-tts-vc-realtime-2025-08-20"
                 self.tts_client = TTSRealtimeClient(
                     base_url=tts_url,
                     api_key=self.audio_api_key,
                     voice=self.voice_id,
                     mode=SessionMode.SERVER_COMMIT,
                     audio_callback=self.handle_tts_audio_data
                 )
                 # 连接TTS服务
                 await self.tts_client.connect()
                 # 启动消息处理任务
                 self.tts_handler_task = asyncio.create_task(self.tts_client.handle_messages())

        if new:
            self.message_cache_for_new_session = []
            self.last_time = None
            self.is_preparing_new_session = False
            self.summary_triggered_time = None
            self.initial_cache_snapshot_len = 0

        try:
            # 获取初始 prompt
            initial_prompt = ("你是一个角色扮演大师，并且精通电脑操作。请按要求扮演以下角色（self.lanlan_name），不得透露角色扮演相关的信息。并且，在对方请求时、回答“我试试”并尝试操纵电脑。\n" if self._is_agent_enabled() else "你是一个角色扮演大师。请按要求扮演以下角色（self.lanlan_name）。不得透露角色扮演相关的信息。\n") + self.lanlan_prompt
            initial_prompt += requests.get(f"http://localhost:{self.memory_server_port}/new_dialog/{self.lanlan_name}").text
            # logger.info("====Initial Prompt=====")
            # logger.info(initial_prompt)

            # 标记 session 激活
            if self.session:
                await self.session.connect(initial_prompt, native_audio = not self.use_tts)
                async with self.lock:
                    self.is_active = True
                # await self.session.create_response("SYSTEM_MESSAGE | " + initial_prompt)
                # await self.session.create_response("SYSTEM_MESSAGE | 当前时间：" + str(
                #             datetime.now().strftime(
                #                 "%Y-%m-%d %H:%M")) + f'。 现在请{self.lanlan_name}准备，即将开始用语音与{MASTER_NAME}继续对话。\n')
                self.session_start_time = datetime.now()
                
                # 启动消息处理任务
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())
            else:
                raise Exception("Session not initialized")
            
        except Exception as e:
            error_message = f"Error starting session: {e}"
            logger.error(f"💥 {error_message}")
            await self.send_status(error_message)
            if 'actively refused it' in str(e):
                await self.send_status("💥 记忆服务器已崩溃。请检查API Key是否正确。")
            elif '401' in str(e):
                await self.send_status("💥 API Key被服务器拒绝。请检查API Key是否与所选模型匹配。")
            await self.cleanup()

    async def send_user_activity(self):
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                message = {
                    "type": "user_activity"
                }
                await self.websocket.send_json(message)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send User Activity Error: {e}")

    def _convert_cache_to_str(self, cache):
        """[热切换相关] 将cache转换为字符串"""
        res = ""
        for i in cache:
            res += f"{i['role']} | {i['text']}\n"
        return res

    def _is_agent_enabled(self):
        return self.agent_flags['agent_enabled'] and (self.agent_flags['computer_use_enabled'] or self.agent_flags['mcp_enabled'])

    async def _background_prepare_pending_session(self):
        """[热切换相关] 后台预热pending session"""

        # 2. Create PENDING session components (as before, store in self.pending_connector, self.pending_session)
        try:
            # 创建新的pending session
            self.pending_session = OmniRealtimeClient(
                base_url=self.core_url,
                api_key=self.core_api_key,
                model=self.model,
                voice="Cherry",
                on_text_delta=self.handle_text_data,
                on_audio_delta=self.handle_audio_data,
                on_interrupt=self.handle_interrupt,
                on_input_transcript=self.handle_input_transcript,
                on_output_transcript=self.handle_output_transcript,
                on_connection_error=self.handle_connection_error,
                on_response_done=self.handle_response_complete,
                api_type=self.core_api_type  # 传入API类型，用于判断是否启用静默超时
            )

            initial_prompt = ("你是一个角色扮演大师，并且精通电脑操作。请按要求扮演以下角色（self.lanlan_name），不得透露角色扮演相关的信息。并且，在对方请求时、回答“我试试”并尝试操纵电脑。\n" if self._is_agent_enabled() else "你是一个角色扮演大师。请按要求扮演以下角色（self.lanlan_name）。不得透露角色扮演相关的信息。\n") + self.lanlan_prompt
            
            self.initial_cache_snapshot_len = len(self.message_cache_for_new_session)
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://localhost:{self.memory_server_port}/new_dialog/{self.lanlan_name}")
                initial_prompt += resp.text + self._convert_cache_to_str(self.message_cache_for_new_session)
            # print(initial_prompt)
            await self.pending_session.connect(initial_prompt, native_audio = not self.use_tts)

            # 4. Start temporary listener for PENDING session's *first* ignored response
            #    and wait for it to complete.
            if self.pending_session_warmed_up_event:
                self.pending_session_warmed_up_event.set() 

        except asyncio.CancelledError:
            logger.error("💥 BG Prep Stage 1: Task cancelled.")
            await self._cleanup_pending_session_resources()
            # Do not set warmed_up_event here if cancelled.
        except Exception as e:
            logger.error(f"💥 BG Prep Stage 1: Error: {e}")
            await self._cleanup_pending_session_resources()
            # Do not set warmed_up_event on error.
        finally:
            # Ensure this task variable is cleared so it's known to be done
            if self.background_preparation_task and self.background_preparation_task.done():
                self.background_preparation_task = None

    async def _trigger_immediate_preparation_for_extra(self):
        """当需要注入额外提示时，如果当前未进入准备流程，立即开始准备并安排renew逻辑。"""
        try:
            if not self.is_preparing_new_session:
                logger.info("Extra Reply: Triggering preparation due to pending extra reply.")
                self.is_preparing_new_session = True
                self.summary_triggered_time = datetime.now()
                self.message_cache_for_new_session = []
                self.initial_cache_snapshot_len = 0
                # 立即启动后台预热，不等待10秒
                self.pending_session_warmed_up_event = asyncio.Event()
                if not self.background_preparation_task or self.background_preparation_task.done():
                    self.background_preparation_task = asyncio.create_task(self._background_prepare_pending_session())
        except Exception as e:
            logger.error(f"💥 Extra Reply: preparation trigger error: {e}")

    # 供主服务调用，更新Agent模式相关开关
    def update_agent_flags(self, flags: dict):
        try:
            for k in ['agent_enabled', 'computer_use_enabled', 'mcp_enabled']:
                if k in flags and isinstance(flags[k], bool):
                    self.agent_flags[k] = flags[k]
        except Exception:
            pass

    async def _perform_final_swap_sequence(self):
        """[热切换相关] 执行最终的swap序列"""
        logger.info("Final Swap Sequence: Starting...")
        if not self.pending_session:
            logger.error("💥 Final Swap Sequence: Pending session not found. Aborting swap.")
            self._reset_preparation_state(clear_main_cache=False)  # Reset flags, keep cache for next attempt
            self.is_hot_swap_imminent = False
            return

        try:
            incremental_cache = self.message_cache_for_new_session[self.initial_cache_snapshot_len:]
            # 1. Send incremental cache (or a heartbeat) to PENDING session for its *second* ignored response
            if incremental_cache:
                final_prime_text = f"SYSTEM_MESSAGE | " + self._convert_cache_to_str(incremental_cache)
            else:  # Ensure session cycles a turn even if no incremental cache
                logger.error(f"💥 Unexpected: No incremental cache found. {len(self.message_cache_for_new_session)}, {self.initial_cache_snapshot_len}")
                final_prime_text = f"SYSTEM_MESSAGE | 系统自动报时，当前时间： " + str(datetime.now().strftime("%Y-%m-%d %H:%M"))

            # 若存在需要植入的额外提示，则指示模型忽略上一条消息，并在下一次响应中统一向用户补充这些提示
            if self.pending_extra_replies and len(self.pending_extra_replies) > 0:
                try:
                    items = "\n".join([f"- {txt}" for txt in self.pending_extra_replies if isinstance(txt, str) and txt.strip()])
                except Exception:
                    items = ""
                final_prime_text += (
                    "\n[注入指令] 请忽略上一次用户的最后一条输入，不要继续该轮对话。"
                    " 在你的下一次响应中，用简洁自然的一段话汇报和解释你先前执行的任务的结果，简要说明你做了什么：\n"
                    + items +
                    "\n完成上述汇报后，恢复正常的对话节奏。"
                )
                # 清空队列，避免重复注入
                self.pending_extra_replies.clear()
                await self.pending_session.create_response(final_prime_text, skipped=False)
            else:
                final_prime_text += f"=======以上为前情概要。现在请{self.lanlan_name}准备，即将开始用语音与{self.master_name}继续对话。\n"
                await self.pending_session.create_response(final_prime_text, skipped=True)

            # 2. Start temporary listener for PENDING session's *second* ignored response
            if self.pending_session_final_prime_complete_event:
                self.pending_session_final_prime_complete_event.set()

            # --- PERFORM ACTUAL HOT SWAP ---
            logger.info("Final Swap Sequence: Starting actual session swap...")
            old_main_session = self.session
            old_main_message_handler_task = self.message_handler_task
            
            # 先停止旧session的消息处理任务
            if old_main_message_handler_task and not old_main_message_handler_task.done():
                logger.info("Final Swap Sequence: Cancelling old message handler task...")
                old_main_message_handler_task.cancel()
                try:
                    await asyncio.wait_for(old_main_message_handler_task, timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning("Final Swap Sequence: Warning: Old message handler task cancellation timeout.")
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"💥 Final Swap Sequence: Error cancelling old message handler: {e}")
            
            # 执行session切换
            logger.info("Final Swap Sequence: Swapping sessions...")
            self.session = self.pending_session
            self.session_start_time = datetime.now()

            # Start the main listener for the NEWLY PROMOTED self.session
            if self.session and hasattr(self.session, 'handle_messages'):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())

            # 关闭旧session
            if old_main_session:
                logger.info("Final Swap Sequence: Closing old session...")
                try:
                    await old_main_session.close()
                    logger.info("Final Swap Sequence: Old session closed successfully.")
                except Exception as e:
                    logger.error(f"💥 Final Swap Sequence: Error closing old session: {e}")

        
            # Reset all preparation states and clear the *main* cache now that it's fully transferred
            self.pending_session = None
            self._reset_preparation_state(
                clear_main_cache=True, from_final_swap=True)  # This will clear pending_*, is_preparing_new_session, etc. and self.message_cache_for_new_session
            logger.info("Final Swap Sequence: Hot swap completed successfully.")

            if self.tts_client._is_responding:
                await self.tts_client.wait_for_audio_completion()

            tts_url = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen-tts-vc-realtime-2025-08-20"
            self.tts_client = TTSRealtimeClient(
                base_url=tts_url,
                api_key=self.audio_api_key,
                voice=self.voice_id,
                mode=SessionMode.SERVER_COMMIT,
                audio_callback=self.handle_tts_audio_data
            )
            # 连接TTS服务
            await self.tts_client.connect()
            # 启动消息处理任务
            self.tts_handler_task = asyncio.create_task(self.tts_client.handle_messages())

        except asyncio.CancelledError:
            logger.info("Final Swap Sequence: Task cancelled.")
            # If cancelled mid-swap, state could be inconsistent. Prioritize cleaning pending.
            await self._cleanup_pending_session_resources()
            self._reset_preparation_state(clear_main_cache=False)  # Don't clear cache if swap didn't complete
            # The old main session listener might have been cancelled, needs robust restart if still active
            if self.is_active and self.session and hasattr(self.session, 'handle_messages') and (not self.message_handler_task or self.message_handler_task.done()):
                logger.info(
                    "Final Swap Sequence: Task cancelled, ensuring main listener is running for potentially old session.")
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())

        except Exception as e:
            logger.error(f"💥 Final Swap Sequence: Error: {e}")
            await self.send_status(f"内部更新切换失败: {e}.")
            await self._cleanup_pending_session_resources()
            self._reset_preparation_state(clear_main_cache=False)
            if self.is_active and self.session and hasattr(self.session, 'handle_messages') and (not self.message_handler_task or self.message_handler_task.done()):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())
        finally:
            self.is_hot_swap_imminent = False  # Always reset this flag
            if self.final_swap_task and self.final_swap_task.done():
                self.final_swap_task = None
            logger.info("Final Swap Sequence: Routine finished.")

    async def system_timer(self):  #定期向Lanlan发送心跳，允许Lanlan主动向用户搭话。
        '''这个模块在开源版中没有实际用途，因为开源版不支持主动搭话。原因是在实际测试中，搭话效果不佳。'''
        while True:
            if self.session and self.active_session_is_idle:
                if self.last_time != str(datetime.now().strftime("%Y-%m-%d %H:%M")):
                    self.last_time = str(datetime.now().strftime("%Y-%m-%d %H:%M"))
                    try:
                        await self.session.create_response("SYSTEM_MESSAGE | 当前时间：" + self.last_time + "。")
                    except web_exceptions.ConnectionClosedOK:
                        break
                    except web_exceptions.ConnectionClosedError as e:
                        logger.error(f"💥 System timer: Error sending data to session: {e}")
                        await self.disconnected_by_server()
                    except Exception as e:
                        error_message = f"System timer: Error sending data to session: {e}"
                        logger.error(f"💥 {error_message}")
                        await self.send_status(error_message)
            await asyncio.sleep(5)

    async def disconnected_by_server(self):
        await self.send_status(f"{self.lanlan_name}失联了，即将重启！")
        self.sync_message_queue.put({'type': 'system', 'data': 'API server disconnected'})
        await self.cleanup()

    async def stream_data(self, message: dict):  # 向Core API发送Media数据
        if not self.is_active or not self.session:
            return
            
        # 额外检查session是否有效
        if not hasattr(self.session, 'ws') or not self.session.ws:
            logger.error("💥 Stream: Session websocket not available")
            return
            
        data = message.get("data")
        input_type = message.get("input_type")
        try:
            if input_type == 'audio':
                try:
                    if isinstance(data, list):
                        audio_bytes = struct.pack(f'<{len(data)}h', *data)
                        await self.session.stream_audio(audio_bytes)
                    else:
                        logger.error(f"💥 Stream: Invalid audio data type: {type(data)}")
                        return

                except struct.error as se:
                    logger.error(f"💥 Stream: Struct packing error (audio): {se}")
                    return
                except web_exceptions.ConnectionClosedOK:
                    return
                except Exception as e:
                    logger.error(f"💥 Stream: Error processing audio data: {e}")
                    return

            elif input_type in ['screen', 'camera']:
                try:
                    if isinstance(data, str) and data.startswith('data:image/jpeg;base64,'):
                        img_data = data.split(',')[1]
                        img_bytes = base64.b64decode(img_data)
                        # Resize to 480p (height=480, keep aspect ratio)
                        image = Image.open(BytesIO(img_bytes))
                        w, h = image.size
                        new_h = 480
                        new_w = int(w * (new_h / h))
                        image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
                        buffer = BytesIO()
                        image.save(buffer, format='JPEG')
                        buffer.seek(0)
                        resized_bytes = buffer.read()
                        resized_b64 = base64.b64encode(resized_bytes).decode('utf-8')
                        await self.session.stream_image(resized_b64)
                    else:
                        logger.error(f"💥 Stream: Invalid screen data format.")
                        return
                except ValueError as ve:
                    logger.error(f"💥 Stream: Base64 decoding error (screen): {ve}")
                    return
                except Exception as e:
                    logger.error(f"💥 Stream: Error processing screen data: {e}")
                    return

        except web_exceptions.ConnectionClosedError as e:
            logger.error(f"💥 Stream: Error sending data to session: {e}")
            if '1011' in str(e):
                print(f"💥 备注：检测到1011错误。该错误表示API服务器异常。请首先检查自己的麦克风是否有声音。")
            if '1007' in str(e):
                print(f"💥 备注：检测到1007错误。该错误大概率是欠费导致。")
            await self.disconnected_by_server()
            return
        except Exception as e:
            error_message = f"Stream: Error sending data to session: {e}"
            logger.error(f"💥 {error_message}")
            await self.send_status(error_message)

    async def end_session(self, by_server=False):  # 与Core API断开连接
        self._init_renew_status()

        async with self.lock:
            if not self.is_active:
                return

        logger.info("End Session: Starting cleanup...")
        self.sync_message_queue.put({'type': 'system', 'data': 'session end'})
        async with self.lock:
            self.is_active = False

        if self.message_handler_task:
            self.message_handler_task.cancel()
            try:
                await asyncio.wait_for(self.message_handler_task, timeout=3.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                logger.warning("End Session: Warning: Listener task cancellation timeout.")
            except Exception as e:
                logger.error(f"💥 End Session: Error during listener task cancellation: {e}")
            self.message_handler_task = None

        if self.session:
            try:
                logger.info("End Session: Closing connection...")
                await self.session.close()
                logger.info("End Session: Qwen connection closed.")
            except Exception as e:
                logger.error(f"💥 End Session: Error during cleanup: {e}")
        
        # 关闭TTS客户端
        if self.use_tts and self.tts_client:
            if self.tts_handler_task and not self.tts_handler_task.done():
                self.tts_handler_task.cancel()
                try:
                    await asyncio.wait_for(self.tts_handler_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception as e:
                    logger.error(f"💥 End Session: TTS handler task cancellation error: {e}")
                finally:
                    self.tts_handler_task = None
            
            try:
                await asyncio.wait_for(self.tts_client.close(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("End Session: TTS client close timeout.")
            except Exception as e:
                logger.error(f"💥 End Session: TTS client close error: {e}")
            finally:
                self.tts_client = None

        self.last_time = None
        await self.send_expressions()
        if not by_server:
            await self.send_status(f"{self.lanlan_name}已离开。")
            logger.info("End Session: Resources cleaned up.")

    async def cleanup(self):
        await self.end_session(by_server=True)

    async def send_status(self, message: str): # 向前端发送status message
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "status", "message": message})
                await self.websocket.send_text(data)

                # 同步到同步服务器
                self.sync_message_queue.put({'type': 'json', 'data': {"type": "status", "message": message}})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Status Error: {e}")

    async def send_expressions(self, prompt=""):
        '''这个函数在直播版本中有用，用于控制Live2D模型的表情动作。但是在开源版本目前没有实际用途。'''
        try:
            expression_map = {}
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                if prompt in expression_map:
                    if self.current_expression:
                        await self.websocket.send_json({
                            "type": "expression",
                            "message": '-',
                        })
                    await self.websocket.send_json({
                        "type": "expression",
                        "message": expression_map[prompt] + '+',
                    })
                    self.current_expression = expression_map[prompt]
                else:
                    if self.current_expression:
                        await self.websocket.send_json({
                            "type": "expression",
                            "message": '-',
                        })

                if prompt in expression_map:
                    self.sync_message_queue.put({"type": "json",
                                                 "data": {
                        "type": "expression",
                        "message": expression_map[prompt] + '+',
                    }})
                else:
                    if self.current_expression:
                        self.sync_message_queue.put({"type": "json",
                         "data": {
                             "type": "expression",
                             "message": '-',
                         }})
                        self.current_expression = None

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Response Error: {e}")


    async def send_speech(self, tts_audio):
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_bytes(tts_audio)

                # 同步到同步服务器
                self.sync_message_queue.put({"type": "binary", "data": tts_audio})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Response Error: {e}")

    async def handle_tts_audio_data(self, audio_data: bytes):
        """TTS音频回调：推送音频到WebSocket前端"""
        try:
            # 对于实时 TTS，音频已经在子进程中进行了重采样，此处无需再次处理
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_bytes(audio_data)
                # 同步到同步服务器
                self.sync_message_queue.put({"type": "binary", "data": audio_data})
            else:
                logger.warning("WebSocket未连接，无法发送音频数据")
        except Exception as e:
            logger.error(f"TTS音频回调处理出错: {e}")








