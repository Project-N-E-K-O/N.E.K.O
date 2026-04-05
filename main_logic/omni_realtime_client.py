# -- coding: utf-8 --

import asyncio
import websockets
import json
import base64
import time
import wave
import numpy as np
from pathlib import Path

from typing import Optional, Callable, Dict, Any, Awaitable
from enum import Enum
from config import NATIVE_IMAGE_MIN_INTERVAL, IMAGE_IDLE_RATE_MULTIPLIER
from utils.config_manager import get_config_manager
from utils.audio_processor import AudioProcessor
from utils.file_utils import atomic_write_json
from utils.frontend_utils import calculate_text_similarity
from utils.logger_config import get_module_logger
from utils.ssl_env_diagnostics import write_ssl_diagnostic

# Gemini Live API SDK (startup-time import)
try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
    _GEMINI_IMPORT_ERROR = None
except Exception as e:
    GEMINI_AVAILABLE = False
    _GEMINI_IMPORT_ERROR = e
    genai = None
    types = None

# Setup logger for this module
logger = get_module_logger(__name__, "Main")

# ── Proactive audio prompt cache ──────────────────────────────────────
_PROACTIVE_AUDIO_DIR = Path(__file__).resolve().parent.parent / "static" / "proactive_audio"
_PROACTIVE_AUDIO_CACHE: Dict[str, bytes] = {}


def _load_proactive_audio(filename: str) -> bytes:
    """Load a proactive prompt WAV file as raw PCM16 bytes (cached).

    Validates that the file is PCM16 mono 16 kHz before caching.
    Raises ``ValueError`` on format mismatch, ``FileNotFoundError`` if absent.
    """
    if filename in _PROACTIVE_AUDIO_CACHE:
        return _PROACTIVE_AUDIO_CACHE[filename]
    path = _PROACTIVE_AUDIO_DIR / filename
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000 or wf.getcomptype() != "NONE":
            raise ValueError(
                f"{filename}: expected PCM16 mono 16kHz, got "
                f"ch={wf.getnchannels()} sw={wf.getsampwidth()} "
                f"rate={wf.getframerate()} comp={wf.getcomptype()}"
            )
        data = wf.readframes(wf.getnframes())
    _PROACTIVE_AUDIO_CACHE[filename] = data
    return data


class TurnDetectionMode(Enum):
    SERVER_VAD = "server_vad"
    MANUAL = "manual"

_config_manager = get_config_manager()

if not GEMINI_AVAILABLE and _GEMINI_IMPORT_ERROR is not None:
    diagnostics_dir = Path(_config_manager.app_docs_dir) / "logs" / "diagnostics"
    sentinel_path = diagnostics_dir / "gemini_sdk_import_failed.last.json"
    throttle_window_seconds = 24 * 60 * 60
    now_ts = time.time()

    recent_diag_path = None
    try:
        if sentinel_path.exists():
            with open(sentinel_path, "r", encoding="utf-8") as f:
                sentinel_data = json.load(f)
            sentinel_diag_path = sentinel_data.get("path")
            sentinel_ts = float(sentinel_data.get("timestamp", 0))
            if sentinel_diag_path and (now_ts - sentinel_ts) < throttle_window_seconds:
                if Path(sentinel_diag_path).exists():
                    recent_diag_path = sentinel_diag_path
    except Exception as sentinel_err:
        logger.error(f"Gemini diagnostic sentinel read failed: {sentinel_err}")

    if recent_diag_path is None:
        try:
            if diagnostics_dir.exists():
                for diag_file in diagnostics_dir.glob("ssl_diagnostic_*.json"):
                    try:
                        with open(diag_file, "r", encoding="utf-8") as f:
                            payload = json.load(f)
                        if payload.get("event") != "gemini_sdk_import_failed":
                            continue
                        file_mtime = diag_file.stat().st_mtime
                        if (now_ts - file_mtime) < throttle_window_seconds:
                            if (
                                recent_diag_path is None
                                or file_mtime > Path(recent_diag_path).stat().st_mtime
                            ):
                                recent_diag_path = str(diag_file)
                    except Exception as diag_file_err:
                        logger.debug(
                            "Skipping diagnostic file scan due to parse/read error: %s (%s)",
                            diag_file,
                            diag_file_err,
                        )
                        continue
        except Exception as scan_err:
            logger.error(f"Gemini diagnostic scan failed: {scan_err}")

    if recent_diag_path:
        logger.warning(f"Gemini SDK import failed, recent diagnostic exists: {recent_diag_path}")
    else:
        try:
            diag_path = write_ssl_diagnostic(
                event="gemini_sdk_import_failed",
                output_dir=str(diagnostics_dir),
                error=_GEMINI_IMPORT_ERROR,
                extra={"stage": "module_import"},
            )
            if diag_path:
                logger.warning(f"Gemini SDK import failed, diagnostic saved: {diag_path}")
                try:
                    diagnostics_dir.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(
                        sentinel_path,
                        {
                            "path": diag_path,
                            "timestamp": now_ts,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                except Exception as sentinel_write_err:
                    logger.error(f"Gemini diagnostic sentinel write failed: {sentinel_write_err}")
        except Exception as diag_err:
            logger.error(f"Gemini SDK diagnostic write failed: {diag_err}")


class OmniRealtimeClient:
    """
    A demo client for interacting with the Omni Realtime API.

    This class provides methods to connect to the Realtime API, send text and audio data,
    handle responses, and manage the WebSocket connection.

    Attributes:
        base_url (str):
            The base URL for the Realtime API.
        api_key (str):
            The API key for authentication.
        model (str):
            Omni model to use for chat.
        voice (str):
            The voice to use for audio output.
        turn_detection_mode (TurnDetectionMode):
            The mode for turn detection.
        on_text_delta (Callable[[str, bool], Awaitable[None]]):
            Callback for text delta events.
            Takes in a string and returns an awaitable.
        on_audio_delta (Callable[[bytes], Awaitable[None]]):
            Callback for audio delta events.
            Takes in bytes and returns an awaitable.
        on_input_transcript (Callable[[str], Awaitable[None]]):
            Callback for input transcript events.
            Takes in a string and returns an awaitable.
        on_interrupt (Callable[[], Awaitable[None]]):
            Callback for user interrupt events, should be used to stop audio playback.
        on_output_transcript (Callable[[str, bool], Awaitable[None]]):
            Callback for output transcript events.
            Takes in a string and returns an awaitable.
        extra_event_handlers (Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]]):
            Additional event handlers.
            Is a mapping of event names to functions that process the event payload.
    """
    def __init__(
        self,
        base_url,
        api_key: str,
        model: str = "",
        voice: str = None,
        turn_detection_mode: TurnDetectionMode = TurnDetectionMode.SERVER_VAD,
        on_text_delta: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_audio_delta: Optional[Callable[[bytes], Awaitable[None]]] = None,
        on_new_message: Optional[Callable[[], Awaitable[None]]] = None,
        on_input_transcript: Optional[Callable[[str], Awaitable[None]]] = None,
        on_output_transcript: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_connection_error: Optional[Callable[[str], Awaitable[None]]] = None,
        on_response_done: Optional[Callable[[], Awaitable[None]]] = None,
        on_silence_timeout: Optional[Callable[[], Awaitable[None]]] = None,
        on_status_message: Optional[Callable[[str], Awaitable[None]]] = None,
        on_repetition_detected: Optional[Callable[[], Awaitable[None]]] = None,
        extra_event_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]]] = None,
        api_type: Optional[str] = None
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self._model_lower = model.lower() if model else ''
        self.voice = voice
        self.ws = None
        self.instructions = None
        self.on_text_delta = on_text_delta
        self.on_audio_delta = on_audio_delta
        self.on_new_message = on_new_message
        self.on_input_transcript = on_input_transcript
        self.on_output_transcript = on_output_transcript
        self.turn_detection_mode = turn_detection_mode
        self.on_connection_error = on_connection_error
        self.on_response_done = on_response_done
        self.on_silence_timeout = on_silence_timeout
        self.on_status_message = on_status_message
        self.on_repetition_detected = on_repetition_detected
        self.extra_event_handlers = extra_event_handlers or {}
        self._bg_tasks: set = set()  # 防止 fire-and-forget 任务被 GC 回收

        # Track current response state
        self._current_response_id = None
        self._current_item_id = None
        self._is_responding = False
        # Track printing state for input and output transcripts
        self._is_first_text_chunk = False
        self._is_first_transcript_chunk = False
        self._print_input_transcript = False
        self._output_transcript_buffer = ""
        self._modalities = ["text", "audio"]
        self._audio_in_buffer = False
        self._skip_until_next_response = False
        self._audio_delta_count = 0  # diagnostic: count audio.delta events per session
        # Track image recognition per turn
        self._image_recognized_this_turn = False
        self._image_sent_this_turn = False
        self._image_being_analyzed = False
        self._image_description = "[实时屏幕截图或相机画面正在分析中。先不要瞎编内容，可以稍等片刻。在此期间不要用搜索功能应付。等收到画面分析结果后再描述画面。]"
        self._latest_image_b64 = None  # Cached latest screenshot for proactive injection
        self._proactive_image_consumed = True  # Whether the cached image has been used by a proactive nudge
        
        # Silence detection for auto-closing inactive sessions
        # 只在 GLM 和 free API 时启用90秒静默超时，Qwen 和 Step 放行
        self._last_speech_time = None
        self._api_type = api_type or ""
        # 只在 GLM 和 free 时启用静默超时
        self._enable_silence_timeout = self._api_type.lower() in ['glm', 'free']
        self._silence_timeout_seconds = 90  # 90秒无语音输入则自动关闭
        self._silence_check_task = None
        self._silence_timeout_triggered = False
        
        # Audio preprocessing with RNNoise for noise reduction
        # Auto-resets after 2 seconds of no speech to prevent state drift
        # Input: 48kHz from PC, 16kHz from mobile
        # Output: 16kHz for API
        self._audio_processor = AudioProcessor(
            input_sample_rate=48000,
            output_sample_rate=16000,
            noise_reduce_enabled=True,  # RNNoise noise reduction + VAD
            on_silence_reset=self._on_silence_reset  # 静音重置时发送 input_audio_buffer.clear
        )
        
        # 静音重置事件异步队列（RNNoise 4秒静音回调用）
        self._silence_reset_pending = False
        # 按“上次语音时间”做静音清 buffer：无 RNNoise 时也生效，与 RESET_TIMEOUT 一致
        self._silence_buffer_clear_seconds = 4.0
        self._last_silence_clear_speech_time = 0.0
        # 叠加本地音量：必须连续 2 秒本地静音才允许 clear，避免 VAD 延迟导致误清
        self._local_quiet_seconds = 2.0
        self._last_local_loud_time = 0.0
        
        # 重复度检测
        self._recent_responses = []  # 存储最近3轮助手回复
        self._repetition_threshold = 0.8  # 相似度阈值
        self._max_recent_responses = 3  # 最多存储的回复数
        self._current_response_transcript = ""  # 当前回复的转录文本
        
        # Backpressure control - 防止503过载错误
        self._send_semaphore = asyncio.Semaphore(25)  # 最多25个并发发送
        self._is_throttled = False  # 503检测后节流状态
        self._throttle_until = 0.0  # 节流结束时间戳
        self._throttle_duration = 2.0  # 节流持续时间（秒）
        
        # Fatal error detection - 检测到致命错误后立即中断
        self._fatal_error_occurred = False  # 致命错误标志

    def _fire_task(self, coro):
        """Create a background task with GC protection."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task
        
        # Interruption state - suppress output after user interruption until next response
        self._interrupted = False  # 打断状态标志，防止重复消息块
        
        # Native image input rate limiting
        self._last_native_image_time = 0.0  # 上次原生图片输入时间戳
        
        # Unified VAD for image throttling (priority: server VAD > RNNoise > RMS)
        # All native-image paths use _client_vad_active to adjust send rate
        self._client_vad_active = False  # 语音活动检测（统一标志）
        self._client_vad_last_speech_time = 0.0  # 上次检测到语音的时间戳
        self._client_vad_grace_period = 2.0  # 语音结束后保持活跃的宽限期（秒）
        self._client_vad_threshold = 500  # RMS 能量阈值（int16 范围，fallback用）
        self._speech_detect_start = 0.0  # RNNoise 连续检测到语音的起始时间
        self._speech_sustain_threshold = 0.5  # 需持续 500ms 才算真正说话（防噪音误触）
        self._rnnoise_vad_active = False  # RNNoise VAD 是否正在运行（48kHz + denoiser ok）
        
        # 防止log刷屏机制（当websocket关闭后）
        self._last_ws_none_warning_time = 0.0  # 上次websocket为None警告的时间戳
        self._ws_none_warning_interval = 5.0  # websocket为None警告的最小间隔（秒）
        
        # Image processing lock
        self._image_lock = asyncio.Lock()
        
        # Audio processing lock to ensure sequential processing in thread pool
        self._audio_processing_lock = asyncio.Lock()
        
        # Gemini Live API specific attributes
        self._is_gemini = self._api_type.lower() == 'gemini'
        
        # Whether this API returns server-side VAD events (speech_started/speech_stopped)
        # Gemini (direct) and lanlan.app+free (Gemini proxy) do NOT have server VAD
        self._has_server_vad = not self._is_gemini and not (
            'lanlan.app' in (base_url or '') and 'free' in self._model_lower
        )

        # Whether this client supports native image input
        # qwen/glm/gpt/gemini have native vision; lanlan.app replacement server (free, non-mainland) also does
        self._supports_native_image = (
            any(m in self._model_lower for m in ['qwen', 'glm', 'gpt'])
            or self._is_gemini
            or ('lanlan.app' in (base_url or '') and 'free' in self._model_lower)
        )
        self._gemini_client = None  # genai.Client instance
        self._gemini_session = None  # Live session from SDK
        self._gemini_context_manager = None  # For proper cleanup
        self._gemini_current_transcript = ""  # Current response transcript for Gemini
        self._gemini_user_transcript = ""  # Accumulated user input transcript

    async def process_audio_chunk_async(self, audio_chunk: bytes) -> bytes:
        """
        Asynchronously process audio chunk using RNNoise in a separate thread.
        This prevents blocking the main event loop during heavy calculation.
        """
        if self._audio_processor is None:
            return audio_chunk

        async with self._audio_processing_lock:
            # Use run_in_executor to offload heavy processing
            # None = use default ThreadPoolExecutor
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, 
                self._audio_processor.process_chunk, 
                audio_chunk
            )

    async def _check_silence_timeout(self):
        """定期检查是否超过静默超时时间，如果是则触发超时回调"""
        # 如果未启用静默超时（Qwen 或 Step），直接返回
        if not self._enable_silence_timeout:
            logger.debug(f"静默超时检测已禁用（API类型: {self._api_type}）")
            return
        
        try:
            while self.ws:
                # 检查websocket是否还有效（直接访问并捕获异常）
                try:
                    if not self.ws:
                        break
                except Exception:
                    break
                    
                await asyncio.sleep(10)  # 每10秒检查一次
                
                if self._silence_timeout_triggered:
                    continue
                
                # 选择语音活动时间源：有 server VAD 用 _last_speech_time，否则用客户端 VAD
                if self._has_server_vad:
                    speech_time = self._last_speech_time
                else:
                    # 无 server VAD 时（free/gemini），用客户端能量/RNNoise 检测的时间戳
                    speech_time = self._client_vad_last_speech_time if self._client_vad_last_speech_time > 0 else None
                
                if speech_time is None:
                    # 还没有检测到任何语音，从现在开始计时
                    self._last_speech_time = time.time()
                    self._client_vad_last_speech_time = self._last_speech_time
                    continue
                
                elapsed = time.time() - speech_time
                if elapsed >= self._silence_timeout_seconds:
                    logger.warning(f"⏰ 检测到{self._silence_timeout_seconds}秒无语音输入，触发自动关闭")
                    self._silence_timeout_triggered = True
                    if self.on_silence_timeout:
                        await self.on_silence_timeout()
                    break
        except asyncio.CancelledError:
            logger.info("静默检测任务被取消")
        except Exception as e:
            logger.error(f"静默检测任务出错: {e}")
    
    def _on_silence_reset(self):
        """当音频处理器检测到4秒静音并重置缓存时调用。标记待发送clear事件。"""
        self._silence_reset_pending = True
    
    def _should_clear_audio_buffer_on_silence(
        self, current_time: float, use_rnnoise_path: bool
    ) -> bool:
        """是否应在静音时清空 input_audio_buffer。
        
        有 RNNoise 且当前走 RNNoise 路径：以 RNNoise 为准（内部 4 秒静音回调置 _silence_reset_pending）。
        无 RNNoise（或未走 RNNoise 路径）：以 VAD + 连续本地静音为准。
        
        连续静音判定标准：
        - 时长：最近 _local_quiet_seconds 秒（默认 2 秒）内无“大音量”；
        - 大音量：原始 PCM 的 RMS > _client_vad_threshold（默认 500，int16 范围）。
        即：每帧用原始输入算 RMS，超过阈值则更新 _last_local_loud_time；只有
        (current_time - _last_local_loud_time) >= _local_quiet_seconds 才认为连续静音。
        
        返回 True 时，若来自 RNNoise 则调用方需置 _silence_reset_pending=False；
        若来自 VAD+静音则本函数已更新 _last_silence_clear_speech_time。
        """
        if use_rnnoise_path:
            # RNNoise 路径：仅以 RNNoise 的 4 秒静音回调为准；
            # 若尚未收到回调（_silence_reset_pending=False），直接返回 False，
            # 不得降级到 VAD 时间戳逻辑，否则会误触提前清空。
            return self._silence_reset_pending
        # 无 RNNoise 路径：VAD 静音 ≥ _silence_buffer_clear_seconds 且 连续本地静音 ≥ _local_quiet_seconds
        if self._has_server_vad:
            last_speech = self._last_speech_time
        else:
            last_speech = self._client_vad_last_speech_time if self._client_vad_last_speech_time > 0 else None
        if last_speech is None:
            return False
        local_quiet_elapsed = current_time - self._last_local_loud_time
        if local_quiet_elapsed < self._local_quiet_seconds:
            return False
        silence_elapsed = current_time - last_speech
        if silence_elapsed < self._silence_buffer_clear_seconds:
            return False
        if last_speech <= self._last_silence_clear_speech_time:
            return False
        self._last_silence_clear_speech_time = last_speech
        return True
    
    async def clear_audio_buffer(self):
        """发送 input_audio_buffer.clear 事件清空服务端缓存。"""
        clear_event = {
            "type": "input_audio_buffer.clear"
        }
        await self.send_event(clear_event)
        logger.debug("📤 已发送 input_audio_buffer.clear 事件")

    async def connect(self, instructions: str, native_audio=True) -> None:
        """Establish WebSocket connection with the Realtime API."""
        
        # Gemini uses google-genai SDK, not raw WebSocket
        if self._is_gemini:
            await self._connect_gemini(instructions, native_audio)
            return

        # 确保开始新连接时状态完全重置
        self._silence_reset_pending = False
        self._last_silence_clear_speech_time = 0.0
        self._last_local_loud_time = 0.0
        self._client_vad_active = False
        self._client_vad_last_speech_time = 0.0
        self._speech_detect_start = 0.0
        self._rnnoise_vad_active = False
        if self._audio_processor is not None:
            self._audio_processor.reset()

        # WebSocket-based APIs (GLM, Qwen, GPT, Step, Free)
        url = f"{self.base_url}?model={self.model}" if self._model_lower != "free-model" else self.base_url
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        self.ws = await websockets.connect(url, additional_headers=headers)
        
        # 启动静默检测任务（只在启用时）
        self._last_speech_time = time.time()
        self._silence_timeout_triggered = False
        if self._silence_check_task:
            self._silence_check_task.cancel()
        # 只在启用静默超时时启动检测任务
        if self._enable_silence_timeout:
            self._silence_check_task = asyncio.create_task(self._check_silence_timeout())
        else:
            logger.info(f"静默超时检测已禁用（API类型: {self._api_type}），不会自动关闭会话")

        # Set up default session configuration
        if self.turn_detection_mode == TurnDetectionMode.MANUAL:
            raise NotImplementedError("Manual turn detection is not supported")
        elif self.turn_detection_mode == TurnDetectionMode.SERVER_VAD:
            self._modalities = ["text", "audio"] if native_audio else ["text"]
            if 'glm' in self._model_lower:
                await self.update_session({
                    "instructions": instructions,
                    "modalities": self._modalities ,
                    "voice": self.voice if self.voice else "tongtong",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm",
                    "turn_detection": {
                        "type": "server_vad",
                    },
                    "input_audio_noise_reduction": {
                        "type": "far_field",
                    },
                    "beta_fields":{
                        "chat_mode": "video_passive",
                        "auto_search": True,
                    },
                    "temperature": 1.0
                })
            elif "qwen" in self._model_lower:
                await self.update_session({
                    "instructions": instructions,
                    "modalities": self._modalities ,
                    "voice": self.voice if self.voice else "Momo",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_transcription": {
                        "model": "gummy-realtime-v1"
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.55,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 650
                    },
                    "repetition_penalty": 1.2,
                    "temperature": 0.7,
                    # "enable_search": True,
                    # "search_options": {'enable_source': True}
                })
            elif "gpt" in self._model_lower:
                await self.update_session({
                    "type": "realtime",
                    "model": self.model,
                    "instructions": instructions + '\n请使用卡哇伊的声音与用户交流。\n',
                    "output_modalities": ['audio'] if 'audio' in self._modalities else ['text'],
                    "audio": {
                        "input": {
                            "transcription": {"model": "gpt-4o-mini-transcribe"},
                            "turn_detection": { "type": "semantic_vad",
                                "eagerness": "auto",
                                "create_response": True,
                                "interrupt_response": True 
                            },
                        },
                        "output": {
                            "voice": self.voice if self.voice else "marin",
                            "speed": 1.0
                        }
                    }
                })
            elif "step" in self._model_lower:
                await self.update_session({
                    "instructions": instructions,
                    "modalities": ['text', 'audio'], # Step API只支持这一个模式
                    "voice": self.voice if self.voice else "qingchunshaonv",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": {
                        "type": "server_vad"
                    },
                    "tools": [
                        {
                            "type": "web_search",# 固定值
                            "function": {
                                "description": "这个web_search用来搜索互联网的信息"# 描述什么样的信息需要大模型进行搜索。
                            }
                        }
                    ]
                })
            elif "free" in self._model_lower:
                await self.update_session({
                    "instructions": instructions,
                    "modalities": ['text', 'audio'], # Step API只支持这一个模式
                    "voice": self.voice if self.voice else "qingchunshaonv",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": {
                        "type": "server_vad"
                    },
                    "tools": [
                        {
                            "type": "web_search",# 固定值
                            "function": {
                                "description": "这个web_search用来搜索互联网的信息"# 描述什么样的信息需要大模型进行搜索。
                            }
                        }
                    ]
                })
            else:
                raise ValueError(f"Invalid model: {self.model}")
            self.instructions = instructions
        else:
            raise ValueError(f"Invalid turn detection mode: {self.turn_detection_mode}")
    
    async def _connect_gemini(self, instructions: str, native_audio: bool = True) -> None:
        """Establish connection with Gemini Live API using google-genai SDK."""
        if not GEMINI_AVAILABLE or genai is None or types is None:
            detail = f": {_GEMINI_IMPORT_ERROR}" if _GEMINI_IMPORT_ERROR else ""
            raise RuntimeError(
                "google-genai SDK unavailable. "
                "If this is an SSL/证书问题, repair your system certificate chain or switch to non-Gemini API"
                f"{detail}"
            )
        
        try:
            # 创建 Gemini 客户端
            self._gemini_client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1alpha"})
            
            # 配置会话
            config = {
                "response_modalities": ["AUDIO"],
                "system_instruction": instructions,
                "media_resolution": types.MediaResolution.MEDIA_RESOLUTION_LOW,
                "tools": [types.Tool(google_search=types.GoogleSearch())],
                "generation_config": {"temperature": 1.1},
                "input_audio_transcription": {},
                "output_audio_transcription": {},
                "speech_config": types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Leda")
                    )
                ),
            }
            
            # 建立 Live 连接 - connect() 返回 async context manager
            logger.info(f"Connecting to Gemini Live API with model: {self.model}")
            self._gemini_context_manager = self._gemini_client.aio.live.connect(
                model=self.model,
                config=config,
            )
            # 手动进入 async context manager
            self._gemini_session = await self._gemini_context_manager.__aenter__()
            
            # 设置 ws 为 session，用于兼容性检查
            self.ws = self._gemini_session
            
            self._last_speech_time = time.time()
            self.instructions = instructions
            logger.info("✅ Gemini Live API connected successfully")
            
        except Exception as e:
            error_msg = f"Failed to connect to Gemini Live API: {e}"
            logger.error(error_msg)
            self._fatal_error_occurred = True
            if self.on_connection_error:
                await self.on_connection_error(error_msg)
            raise

    async def send_event(self, event) -> None:
        # 检查是否已发生致命错误，直接跳过发送
        if self._fatal_error_occurred:
            return
        
        # Gemini 不使用 WebSocket 风格的事件发送
        # 而是使用 session.send_client_content() 或 session.send_realtime_input()
        if self._is_gemini:
            # Gemini 的事件通过专用方法处理，这里直接返回
            # 对于 session.update / conversation.item.create 等事件，Gemini 不支持
            logger.debug(f"Gemini mode: skipping WebSocket event {event.get('type', 'unknown')}")
            return
        
        # Backpressure: 检查是否处于节流状态
        if self._is_throttled:
            if time.time() < self._throttle_until:
                # 仍在节流期，丢弃音频帧以减轻服务器压力
                if event.get("type") == "input_audio_buffer.append":
                    return  # 丢弃音频帧
            else:
                # 节流期结束，恢复正常发送
                self._is_throttled = False
                logger.info("🔄 Backpressure throttle ended, resuming sends")
        
        # 检查websocket是否有效
        if not self.ws:
            return
        
        event['event_id'] = "event_" + str(int(time.time() * 1000))
        async with self._send_semaphore:  # 限制并发发送数量
            try:
                if not self.ws:
                    return
                await self.ws.send(json.dumps(event))
            except Exception as e:
                error_msg = str(e)
                if '1000' not in error_msg:
                    logger.warning(f"⚠️ 发送 {event.get('type', '未知')} 事件失败: {error_msg}")
                
                # 检测致命错误：Response timeout 或 1011 错误码
                if 'Response timeout' in error_msg or '1011' in error_msg:
                    if not self._fatal_error_occurred:
                        self._fatal_error_occurred = True
                        logger.error("💥 检测到致命错误 (Response timeout / 1011)，立即中断语音对话")
                        if self.on_connection_error:
                            self._fire_task(self.on_connection_error(json.dumps({"code": "RESPONSE_TIMEOUT"})))
                        # 尝试关闭连接
                        self._fire_task(self.close())
                    return  # 不再抛出异常，直接返回
                
                raise

    async def update_session(self, config: Dict[str, Any]) -> None:
        """Update session configuration."""
        event = {
            "type": "session.update",
            "session": config
        }
        await self.send_event(event)

    async def stream_audio(self, audio_chunk: bytes) -> None:
        """Stream raw audio data to the API.
        
        Supports two input modes:
        - 48kHz from PC: Apply RNNoise then downsample to 16kHz
        - 16kHz from mobile: Pass through directly (no RNNoise)
        """
        # 检查是否已发生致命错误，如果是则直接返回
        if self._fatal_error_occurred:
            return
        
        current_time = time.time()
        # 本地音量判定：用原始输入做 RMS，避免 VAD 延迟时误清 buffer
        raw_samples = np.frombuffer(audio_chunk, dtype=np.int16)
        if len(raw_samples) > 0:
            local_rms = np.sqrt(np.mean(raw_samples.astype(np.float32) ** 2))
            if local_rms > self._client_vad_threshold:
                self._last_local_loud_time = current_time
        
        # Detect input sample rate based on chunk size
        # 48kHz: 480 samples (10ms) = 960 bytes
        # 16kHz: 512 samples (~32ms) = 1024 bytes
        num_samples = len(audio_chunk) // 2  # 16-bit = 2 bytes per sample
        is_48khz = (num_samples == 480)  # RNNoise frame size
        
        
        use_rnnoise_path = is_48khz and self._audio_processor is not None
        # Apply RNNoise noise reduction only for 48kHz input (PC)
        if use_rnnoise_path:
            # Use async wrapper to avoid blocking main loop
            audio_chunk = await self.process_audio_chunk_async(audio_chunk)
            
            # Skip if RNNoise is buffering (returns empty)
            if len(audio_chunk) == 0:
                return
        
        # Unified VAD update (priority: server VAD > RNNoise > RMS)
        # Grace period check: always runs regardless of VAD source
        if self._client_vad_active and current_time - self._client_vad_last_speech_time > self._client_vad_grace_period:
            self._client_vad_active = False
        
        # Client-side speech detection (only when no server VAD — server events handle it in handle_messages)
        # use_rnnoise_path is true only for 48kHz input when AudioProcessor exists;
        # for 16kHz/mobile input RNNoise doesn't run, so fall back to RMS.
        _rnnoise_vad_live = use_rnnoise_path and self._audio_processor.noise_reduce_enabled and self._audio_processor._denoiser is not None
        self._rnnoise_vad_active = _rnnoise_vad_live
        if not self._has_server_vad:
            if _rnnoise_vad_live:
                # Priority 2: RNNoise speech probability with sustained threshold
                if self._audio_processor.speech_probability > 0.4:
                    if self._speech_detect_start == 0.0:
                        self._speech_detect_start = current_time
                    elif current_time - self._speech_detect_start >= self._speech_sustain_threshold:
                        self._client_vad_last_speech_time = current_time
                        self._client_vad_active = True
                else:
                    self._speech_detect_start = 0.0
            else:
                # Priority 3: RMS energy fallback
                samples = np.frombuffer(audio_chunk, dtype=np.int16)
                if len(samples) > 0:
                    rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
                    if rms > self._client_vad_threshold:
                        self._client_vad_last_speech_time = current_time
                        self._client_vad_active = True
        
        # 静音清 buffer：有 RNNoise 以 RNNoise 为准，否则 VAD + 连续本地静音（见 _should_clear_audio_buffer_on_silence）
        if self._should_clear_audio_buffer_on_silence(current_time, use_rnnoise_path):
            if use_rnnoise_path:
                self._silence_reset_pending = False
            await self.clear_audio_buffer()
        
        # Gemini uses different API
        if self._is_gemini:
            await self._stream_audio_gemini(audio_chunk)
            return
        
        audio_b64 = base64.b64encode(audio_chunk).decode()

        append_event = {
            "type": "input_audio_buffer.append",
            "audio": audio_b64
        }
        await self.send_event(append_event)
    
    async def _stream_audio_gemini(self, audio_chunk: bytes) -> None:
        """Send audio data to Gemini Live API."""
        if not self._gemini_session:
            return
        
        try:
            # 发送实时音频输入
            await self._gemini_session.send_realtime_input(
                audio={"data": audio_chunk, "mime_type": "audio/pcm"}
            )
            self._last_speech_time = time.time()
        except Exception as e:
            logger.error(f"Error sending audio to Gemini: {e}")
            if "closed" in str(e).lower():
                self._fatal_error_occurred = True

    async def _analyze_image_with_vision_model(self, image_b64: str) -> str:
        """Use VISION_MODEL to analyze image and return description."""
        try:
            # 使用统一的视觉分析函数
            from utils.screenshot_utils import analyze_image_with_vision_model
            
            description = await analyze_image_with_vision_model(
                image_b64=image_b64,
                max_tokens=500
            )
            
            if description:
                self._image_description = f"[实时屏幕截图或相机画面]: {description}"
                logger.info("✅ Image analysis complete.")
                self._image_recognized_this_turn = True
                return description
            else:
                logger.warning("VISION_MODEL not configured or analysis failed")
                self._image_description = "[实时屏幕截图或相机画面]: 画面分析失败或暂时无法识别。"
                self._image_recognized_this_turn = True
                return ""
            
        except Exception as e:
            logger.error(f"Error analyzing image with vision model: {e}")
            self.image_recognized_this_turn = True
            self._image_being_analyzed = False
            self._image_description = f"[实时屏幕截图或相机画面]: 分析出错: {str(e)}"
            # 检测内容审查错误并发送中文提示到前端（不关闭session）
            error_str = str(e)
            if 'censorship' in error_str:
                if self.on_status_message:
                    await self.on_status_message(json.dumps({"code": "IMAGE_BLOCKED"}))
            return "图片识别发生严重错误！"
    
    async def stream_image(self, image_b64: str) -> None:
        """Stream raw image data to the API."""
        # Cache latest frame for proactive injection
        self._latest_image_b64 = image_b64
        self._proactive_image_consumed = False

        try:
            # Models without native vision (step, free on lanlan.tech) — first frame triggers VISION_MODEL analysis
            if '实时屏幕截图或相机画面正在分析中' in self._image_description and not self._supports_native_image:
                await self._analyze_image_with_vision_model(image_b64)
                return
            
            # Rate limiting for native image input (with VAD-based throttling)
            if self._supports_native_image:
                current_time = time.time()
                elapsed = current_time - self._last_native_image_time
                min_interval = NATIVE_IMAGE_MIN_INTERVAL
                if not self._client_vad_active:
                    min_interval *= IMAGE_IDLE_RATE_MULTIPLIER
                if elapsed < min_interval:
                    # Skip this image frame due to rate limiting
                    return
                self._last_native_image_time = current_time

            # Gemini uses SDK, not WebSocket events (_audio_in_buffer is not set for Gemini)
            if self._is_gemini:
                if self._gemini_session:
                    try:
                        image_bytes = base64.b64decode(image_b64)
                        await self._gemini_session.send_realtime_input(
                            media={"data": image_bytes, "mime_type": "image/jpeg"}
                        )
                    except Exception as e:
                        logger.error(f"Error sending image to Gemini: {e}")
                        if "closed" in str(e).lower():
                            self._fatal_error_occurred = True
                return

            if ('lanlan.app' in self.base_url and 'free' in self._model_lower):
                append_event = {
                    "type": "input_image_buffer.append" ,
                    "image": image_b64
                }
                await self.send_event(append_event)
                return

            if self._audio_in_buffer:
                if "qwen" in self._model_lower:
                    append_event = {
                        "type": "input_image_buffer.append" ,
                        "image": image_b64
                    }
                elif "glm" in self._model_lower:
                    append_event = {
                        "type": "input_audio_buffer.append_video_frame",
                        "video_frame": image_b64
                    }
                elif "gpt" in self._model_lower:
                    append_event = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_image",
                                    "image_url": "data:image/jpeg;base64," + image_b64
                                }
                            ]
                        }
                    }
                else:
                    # Model does not support video streaming, use VISION_MODEL to analyze
                    # Only recognize one image per conversation turn
                    async with self._image_lock:
                        if not self._image_recognized_this_turn:
                            if not self._image_being_analyzed:
                                self._image_being_analyzed = True
                                text_event = {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "message",
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "input_text",
                                                "text": self._image_description
                                            }
                                        ]
                                    }
                                }
                                logger.info("Sending image description before recognition.")
                                await self.send_event(text_event)
                                await self._analyze_image_with_vision_model(image_b64)
                        elif not self._image_sent_this_turn:
                            self._image_sent_this_turn = True
                            text_event = {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "message",
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "input_text",
                                                "text": self._image_description
                                            }
                                        ]
                                    }
                                }
                            logger.info("Sending image description after recognition.")
                            await self.send_event(text_event)
                    return
                    
                await self.send_event(append_event)
        except Exception as e:
            logger.error(f"Error streaming image: {e}")
            raise e

    async def create_response(self, instructions: str, skipped: bool = False) -> None:
        """Request a response from the API. First adds message to conversation, then creates response."""
        if skipped:
            self._skip_until_next_response = True
        
        # Gemini 使用 send_client_content 发送文本内容
        if self._is_gemini:
            await self._create_response_gemini(instructions)
            return

        if "qwen" in self._model_lower:
            await self.update_session({"instructions": self.instructions + '\n' + instructions})

            logger.info("Creating response with instructions override")
            await self.send_event({"type": "response.create"})
        else:
            # 先通过 conversation.item.create 添加系统消息（增量）
            item_event = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": instructions
                        }
                    ]
                }
            }
            await self.send_event(item_event)
            
            # 然后调用 response.create，不带 instructions（避免替换 session instructions）
            logger.info("Creating response without instructions override")
            await self.send_event({"type": "response.create"})
    
    async def _create_response_gemini(self, instructions: str) -> None:
        """Send text content to Gemini and trigger response."""
        if not self._gemini_session:
            logger.warning("Gemini session not available for create_response")
            return
        
        # 🔧 修复：跳过空内容的发送，避免预热时污染 Gemini 对话历史
        # 预热时 instructions 为空字符串，发送空 turn 会导致首轮对话被吞掉
        if not instructions or not instructions.strip():
            logger.info("Gemini: skipping empty content (warmup or empty message)")
            # 直接触发 response_done 回调，让预热逻辑正常完成
            if self.on_response_done:
                await self.on_response_done()
            return
        
        try:
            # Gemini 使用 send_client_content 发送文本
            from google.genai import types as genai_types
            
            content = genai_types.Content(
                parts=[genai_types.Part(text=instructions)],
                role="user"
            )
            await self._gemini_session.send_client_content(
                turns=[content],
                turn_complete=True
            )
            logger.info("Gemini: sent client content, waiting for response")
        except Exception as e:
            logger.error(f"Error sending client content to Gemini: {e}")

    async def stream_proactive(self, instruction: str = "", *, language: str = "zh") -> bool:
        """Send a pre-recorded audio prompt to trigger proactive AI speech.

        Injects a short WAV clip via ``input_audio_buffer.append`` so the
        realtime model "hears" a conversational nudge and responds.  Bypasses
        ``stream_audio()`` (no RNNoise / AGC) since the audio is clean.

        Chunk pacing mirrors hot-swap flush: 1600 bytes/chunk, 0.025 s sleep,
        40 chunks/s → 2× real-time delivery.

        Returns True if the audio was fully sent, False if skipped or aborted.
        """
        # ── Guard checks ──────────────────────────────────────────────
        if self._fatal_error_occurred or self.ws is None:
            return False
        if self._is_responding:
            logger.debug("stream_proactive: skipped — already responding")
            return False
        # Client VAD guard: only when RNNoise VAD is actively processing audio
        # (48kHz input + denoiser running). For 16kHz/mobile or when RNNoise is
        # unavailable, VAD falls back to RMS which is too noisy — skip to avoid
        # permanently blocking proactive.
        if self._rnnoise_vad_active:
            if self._client_vad_active:
                logger.debug("stream_proactive: skipped — user speaking (VAD active)")
                return False
            if time.time() - self._client_vad_last_speech_time < self._client_vad_grace_period:
                logger.debug("stream_proactive: skipped — VAD grace period")
                return False

        # ── Choose audio file ─────────────────────────────────────────
        # Vision context exists if an image was analyzed this turn (via
        # VISION_MODEL text description OR native image input) or we have
        # an unconsumed frame from stream_image().
        has_vision = self._image_recognized_this_turn or (
            self._latest_image_b64 is not None and not self._proactive_image_consumed
        )
        # Only backends with native image support can receive raw screenshots;
        # step / lanlan.tech+free consume vision context as text only.
        can_inject_image = has_vision and self._supports_native_image

        # Snapshot the current image so concurrent stream_image() calls don't
        # cause us to mark a newer frame as consumed.
        snapshot_image_b64 = self._latest_image_b64 if has_vision else None

        prompt_type = "vision" if has_vision else "general"
        lang = (language or "zh")[:2]
        filename = f"prompt_{prompt_type}_{lang}.wav"

        try:
            pcm_data = _load_proactive_audio(filename)
        except FileNotFoundError:
            try:
                pcm_data = _load_proactive_audio(f"prompt_{prompt_type}_zh.wav")
            except FileNotFoundError:
                logger.warning("stream_proactive: no audio file found for %s", filename)
                return False

        # ── Non-native vision: inject text description before audio ───
        # step / lanlan.tech+free can't receive raw images; send the
        # VISION_MODEL text analysis so the model has visual context.
        if has_vision and not can_inject_image and self._image_recognized_this_turn and self._image_description:
            await self.send_event({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": self._image_description}],
                },
            })
            logger.info("stream_proactive: injected vision text description for non-native backend")

        # ── Send audio chunks (same pacing as hot-swap flush) ─────────
        # 320 bytes = 10 ms @16 kHz 16-bit mono, ×5 multiplier → 1600 bytes
        chunk_size = 320 * 5  # 1600 bytes = 50 ms of audio
        sleep_interval = 0.025  # 25 ms → 40 chunks/s, 2× real-time

        logger.info(
            "stream_proactive: injecting %s (%d bytes, %s)",
            filename, len(pcm_data), "vision" if has_vision else "general",
        )

        total_chunks = (len(pcm_data) + chunk_size - 1) // chunk_size
        mid_chunk = total_chunks // 2  # Insert image at the midpoint
        image_injected = False

        for chunk_idx, i in enumerate(range(0, len(pcm_data), chunk_size)):
            # Abort if AI starts responding, or user speaking (only when RNNoise VAD active)
            if self._is_responding or (self._rnnoise_vad_active and self._client_vad_active):
                logger.info("stream_proactive: aborted — user spoke or response started")
                await self.clear_audio_buffer()
                return False

            chunk = pcm_data[i : i + chunk_size]
            if self._is_gemini:
                if self._gemini_session:
                    await self._gemini_session.send_realtime_input(
                        audio={"data": chunk, "mime_type": "audio/pcm"}
                    )
            else:
                audio_b64 = base64.b64encode(chunk).decode()
                await self.send_event({
                    "type": "input_audio_buffer.append",
                    "audio": audio_b64,
                })

            # Inject cached screenshot at midpoint (only for native-image backends)
            if can_inject_image and not image_injected and chunk_idx >= mid_chunk and snapshot_image_b64:
                if self._is_gemini:
                    if self._gemini_session:
                        image_bytes = base64.b64decode(snapshot_image_b64)
                        await self._gemini_session.send_realtime_input(
                            media={"data": image_bytes, "mime_type": "image/jpeg"}
                        )
                elif "gpt" in self._model_lower:
                    await self.send_event({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{
                                "type": "input_image",
                                "image_url": "data:image/jpeg;base64," + snapshot_image_b64,
                            }],
                        },
                    })
                elif "qwen" in self._model_lower or ("lanlan.app" in self.base_url and "free" in self._model_lower):
                    await self.send_event({
                        "type": "input_image_buffer.append",
                        "image": snapshot_image_b64,
                    })
                elif "glm" in self._model_lower:
                    await self.send_event({
                        "type": "input_audio_buffer.append_video_frame",
                        "video_frame": snapshot_image_b64,
                    })
                image_injected = True
                logger.info("stream_proactive: injected screenshot at chunk %d/%d", chunk_idx, total_chunks)

            await asyncio.sleep(sleep_interval)

        # Mark vision context consumed only if the shared image hasn't been
        # replaced by a newer frame from stream_image() during our async loop.
        if has_vision and self._latest_image_b64 == snapshot_image_b64:
            self._proactive_image_consumed = True
        logger.info("stream_proactive: audio injection complete (%s%s), waiting for VAD → response",
                     "vision" if has_vision else "general",
                     "+image" if image_injected else "")
        return True

    async def cancel_response(self) -> None:
        """Cancel the current response."""
        event = {
            "type": "response.cancel"
        }
        await self.send_event(event)
    
    async def _check_repetition(self, response: str) -> bool:
        """
        检查回复是否与近期回复高度重复。
        如果连续3轮都高度重复，返回 True 并触发回调。
        """
        
        # 与最近的回复比较相似度
        high_similarity_count = 0
        for recent in self._recent_responses:
            similarity = calculate_text_similarity(response, recent)
            if similarity >= self._repetition_threshold:
                high_similarity_count += 1
        
        # 添加到最近回复列表
        self._recent_responses.append(response)
        if len(self._recent_responses) > self._max_recent_responses:
            self._recent_responses.pop(0)
        
        # 如果与最近2轮都高度重复（即第3轮重复），触发检测
        if high_similarity_count >= 2:
            logger.warning(f"OmniRealtimeClient: 检测到连续{high_similarity_count + 1}轮高重复度对话")
            
            # 清空重复检测缓存
            self._recent_responses.clear()
            
            # 触发回调
            if self.on_repetition_detected:
                await self.on_repetition_detected()
            
            return True
        
        return False

    async def handle_interruption(self):
        """Handle user interruption of the current response."""
        if not self._is_responding:
            return

        logger.info("Handling interruption")

        # Mark as interrupted to suppress any remaining output until next response
        self._interrupted = True

        # 1. Cancel the current response
        if self._current_response_id:
            await self.cancel_response()

        self._is_responding = False
        self._current_response_id = None
        self._current_item_id = None
        # 清空转录buffer和重置标志，防止打断后的错位
        self._output_transcript_buffer = ""
        self._is_first_transcript_chunk = True

    async def handle_messages(self) -> None:
        # Gemini uses different message handling
        if self._is_gemini:
            await self._handle_messages_gemini()
            return
            
        try:
            if not self.ws:
                logger.error("WebSocket connection is not established")
                return
                
            async for message in self.ws:
                event = json.loads(message)
                event_type = event.get("type")
                
                # if event_type not in ["response.audio.delta", "response.audio_transcript.delta",  "response.output_audio.delta", "response.output_audio_transcript.delta"]:
                #     # print(f"Received event: {event}")
                #     print(f"Received event: {event_type}")
                # else:
                #     print(f"Event type: {event_type}")
                if event_type == "error":
                    error_msg = str(event.get('error', ''))
                    logger.error(f"API Error: {error_msg}")
                    
                    # 检测503过载错误，触发backpressure节流
                    if '503' in error_msg or 'overloaded' in error_msg.lower():
                        self._is_throttled = True
                        self._throttle_until = time.time() + self._throttle_duration
                        logger.warning(f"⚡ 503 detected, throttling for {self._throttle_duration}s")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "SERVER_BUSY_THROTTLE"}))
                        continue
                    
                    error_msg_lower = error_msg.lower()

                    # Idle timeout — Qwen 约 25s 无操作断连
                    if 'too long without operation' in error_msg_lower or 'idle' in error_msg_lower:
                        logger.warning("⏰ Idle timeout from API: %s", error_msg)
                        if self.on_connection_error:
                            await self.on_connection_error(json.dumps({"code": "API_IDLE_TIMEOUT", "details": {"msg": error_msg}}))
                        await self.close()
                        continue

                    if ('欠费' in error_msg or 'standing' in error_msg_lower or 'time limit' in error_msg_lower or
                        'policy violation' in error_msg_lower or '1008' in error_msg_lower or
                        '429' in error_msg_lower or 'quota' in error_msg_lower or 'too many' in error_msg_lower):
                        if self.on_connection_error:
                            await self.on_connection_error(error_msg)
                        await self.close()
                    continue
                elif event_type == "response.done":
                    # 解析实时 API 返回的 token 用量
                    try:
                        resp_data = event.get("response", {})
                        _rt_usage = resp_data.get("usage")
                        if _rt_usage:
                            from utils.token_tracker import TokenTracker
                            TokenTracker.get_instance().record(
                                model=resp_data.get("model", self.model or "realtime"),
                                prompt_tokens=_rt_usage.get("input_tokens", 0),
                                completion_tokens=_rt_usage.get("output_tokens", 0),
                                total_tokens=_rt_usage.get("total_tokens", 0),
                                call_type="conversation_realtime",
                                source="main_logic/omni_realtime_client",
                            )
                    except Exception:
                        pass
                    self._is_responding = False
                    self._current_response_id = None
                    self._current_item_id = None
                    self._skip_until_next_response = False
                    # 响应完成，检测重复度
                    if self._current_response_transcript:
                        print(f"OmniRealtimeClient: response.done - 当前转录: '{self._current_response_transcript[:50]}...' | audio_deltas={self._audio_delta_count}")
                        await self._check_repetition(self._current_response_transcript)
                        self._current_response_transcript = ""
                    else:
                        print(f"OmniRealtimeClient: response.done - 没有转录文本 | audio_deltas={self._audio_delta_count}")
                    self._audio_delta_count = 0
                    # 确保 buffer 被清空
                    self._output_transcript_buffer = ""
                    self._image_recognized_this_turn = False
                    self._image_sent_this_turn = False
                    if self.on_response_done:
                        await self.on_response_done()
                elif event_type == "response.created":
                    self._current_response_id = event.get("response", {}).get("id")
                    self._is_responding = True
                    self._interrupted = False  # Clear interruption flag on new response
                    self._is_first_text_chunk = self._is_first_transcript_chunk = True
                    # 清空转录 buffer，防止累积旧内容
                    self._output_transcript_buffer = ""
                    self._current_response_transcript = ""  # 重置当前回复转录
                elif event_type == "response.output_item.added":
                    self._current_item_id = event.get("item", {}).get("id")
                # Handle interruptions
                elif event_type == "input_audio_buffer.speech_started":
                    logger.info("Speech detected")
                    self._audio_in_buffer = True
                    # 重置静默计时器
                    self._last_speech_time = time.time()
                    # Priority 1: server VAD → sync to unified _client_vad_active
                    self._client_vad_active = True
                    self._client_vad_last_speech_time = self._last_speech_time
                    if self._is_responding:
                        logger.info("Handling interruption")
                        await self.handle_interruption()
                elif event_type == "input_audio_buffer.speech_stopped":
                    logger.info("Speech ended")
                    if self.on_new_message:
                        await self.on_new_message()
                    self._audio_in_buffer = False
                    # Update timestamp so grace period starts from speech end
                    self._client_vad_last_speech_time = time.time()
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    self._print_input_transcript = True
                elif event_type in ["response.audio_transcript.done", "response.output_audio_transcript.done"]:
                    self._print_input_transcript = False
                    if self._output_transcript_buffer and self.on_output_transcript and not self._skip_until_next_response and not self._interrupted:
                        await self.on_output_transcript(self._output_transcript_buffer, self._is_first_transcript_chunk)
                        self._is_first_transcript_chunk = False
                    self._output_transcript_buffer = ""

                if not self._skip_until_next_response and not self._interrupted:
                    if event_type in ["response.text.delta", "response.output_text.delta"]:
                        if self.on_text_delta:
                            if "glm" not in self._model_lower:
                                await self.on_text_delta(event["delta"], self._is_first_text_chunk)
                                self._is_first_text_chunk = False
                    elif event_type in ["response.audio.delta", "response.output_audio.delta"]:
                        self._audio_delta_count += 1
                        if self._audio_delta_count == 1:
                            logger.info(f"🔊 首个 audio.delta 已收到 (type={event_type}, bytes={len(event.get('delta',''))})")
                        if self.on_audio_delta:
                            audio_bytes = base64.b64decode(event["delta"])
                            await self.on_audio_delta(audio_bytes)
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        transcript = event.get("transcript", "")
                        if self.on_input_transcript:
                            await self.on_input_transcript(transcript)
                    elif event_type in ["response.audio_transcript.done", "response.output_audio_transcript.done"]:
                        if self.on_output_transcript and self._is_first_transcript_chunk:
                            transcript = event.get("transcript", "")
                            if transcript:
                                await self.on_output_transcript(transcript, True)
                                self._is_first_transcript_chunk = False
                    elif event_type in ["response.audio_transcript.delta", "response.output_audio_transcript.delta"]:
                        if self.on_output_transcript:
                            delta = event.get("delta", "")
                            # 累积当前回复的转录文本用于重复度检测
                            self._current_response_transcript += delta
                            if not self._print_input_transcript:
                                self._output_transcript_buffer += delta
                            else:
                                if self._output_transcript_buffer:
                                    # logger.info(f"{self._output_transcript_buffer} is_first_chunk: True")
                                    await self.on_output_transcript(self._output_transcript_buffer, self._is_first_transcript_chunk)
                                    self._is_first_transcript_chunk = False
                                    self._output_transcript_buffer = ""
                                await self.on_output_transcript(delta, self._is_first_transcript_chunk)
                                self._is_first_transcript_chunk = False
                    
                    elif event_type in self.extra_event_handlers:
                        await self.extra_event_handlers[event_type](event)

        except websockets.exceptions.ConnectionClosedOK:
            logger.info("Connection closed as expected")
        except websockets.exceptions.ConnectionClosedError as e:
            error_msg = str(e)
            logger.error(f"Connection closed with error: {error_msg}")
            if self.on_connection_error:
                await self.on_connection_error(error_msg)
        except asyncio.TimeoutError:
            if self.ws:
                await self.ws.close()
            if self.on_connection_error:
                await self.on_connection_error(json.dumps({"code": "CONNECTION_TIMEOUT"}))
        except Exception as e:
            logger.error(f"Error in message handling: {str(e)}")
            raise e

    async def close(self) -> None:
        """Close the WebSocket connection."""
        # 取消静默检测任务
        if self._silence_check_task:
            self._silence_check_task.cancel()
            try:
                await self._silence_check_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error cancelling silence check task: {e}")
            finally:
                self._silence_check_task = None

        # 重置静默超时相关状态
        self._silence_timeout_triggered = False
        self._last_speech_time = None
        self._silence_reset_pending = False
        self._last_silence_clear_speech_time = 0.0
        self._last_local_loud_time = 0.0
        self._client_vad_active = False
        self._client_vad_last_speech_time = 0.0
        self._speech_detect_start = 0.0
        self._rnnoise_vad_active = False

        # 保存 debug 音频（RNNoise 处理前后的对比音频）
        if self._audio_processor is not None:
            try:
                self._audio_processor.save_debug_audio()
            except Exception as e:
                logger.error(f"Error saving debug audio: {e}")

        # 重置音频处理器状态
        if self._audio_processor is not None:
            self._audio_processor.reset()

        # Gemini uses different cleanup
        if self._is_gemini:
            await self._close_gemini()
            return
        
        if self.ws:
            try:
                # 尝试关闭websocket连接
                await self.ws.close()
            except Exception as e:
                logger.error(f"Error closing websocket: {e}")
            finally:
                self.ws = None  # 清空引用，防止后续误用
                logger.info("WebSocket connection closed")
        else:
            logger.warning("WebSocket connection is already closed or None")
    
    async def _close_gemini(self) -> None:
        """Close Gemini Live API session."""
        if self._gemini_context_manager:
            try:
                await self._gemini_context_manager.__aexit__(None, None, None)
            except Exception as e:
                logger.error(f"Error closing Gemini session: {e}")
            finally:
                self._gemini_session = None
                self._gemini_context_manager = None
                self.ws = None

                # 重置静默超时相关状态（与普通close()保持一致）
                self._silence_timeout_triggered = False
                self._last_speech_time = None
                self._silence_reset_pending = False
                self._last_silence_clear_speech_time = 0.0
                self._last_local_loud_time = 0.0
                self._client_vad_active = False
                self._client_vad_last_speech_time = 0.0
                self._speech_detect_start = 0.0
                self._rnnoise_vad_active = False

                # 重置音频处理器状态
                if self._audio_processor is not None:
                    self._audio_processor.reset()

                logger.info("Gemini Live API session closed")
    
    async def _handle_messages_gemini(self) -> None:
        """Handle messages from Gemini Live API."""
        if not self._gemini_session:
            logger.error("Gemini session not established")
            return
        
        try:
            while not self._fatal_error_occurred:
                try:
                    # 接收响应流
                    turn = self._gemini_session.receive()
                    async for response in turn:
                        await self._process_gemini_response(response)
                    # receive() 是 session 级 async generator，仅在连接断开时退出；
                    # 正常会话期间此行不会执行。缺失 turn_complete 的兜底已移至
                    # _process_gemini_response 中基于 model_turn 时间间隔的检测。
                    self._is_responding = False
                except asyncio.CancelledError:
                    logger.info("Gemini message handler cancelled")
                    break
                except Exception as e:
                    error_msg = str(e)
                    # 检测正常关闭：包含 "closed" 或者是 WebSocket 1000 正常关闭码
                    if "closed" in error_msg.lower() or "1000" in error_msg:
                        logger.info("Gemini session closed")
                        break
                    else:
                        logger.error(f"Error receiving Gemini response: {e}")
                        if self.on_connection_error:
                            await self.on_connection_error(error_msg)
                        break
        except Exception as e:
            logger.error(f"Gemini message handler error: {e}")
    
    async def _process_gemini_response(self, response) -> None:
        """Process a single Gemini response event."""
        try:
            # 处理工具调用
            if hasattr(response, 'tool_call') and response.tool_call:
                logger.info(f"Gemini tool call: {response.tool_call}")
            
            # 检查是否有服务器内容
            if response.server_content:
                server_content = response.server_content
                
                # 处理用户输入转录 - 只累积，不立即发送（避免碎片化显示）
                if hasattr(server_content, 'input_transcription') and server_content.input_transcription:
                    input_trans = server_content.input_transcription
                    if hasattr(input_trans, 'text') and input_trans.text:
                        self._gemini_user_transcript += input_trans.text
                
                # 检查是否有 AI 内容（model_turn 或 output_transcription）
                has_ai_content = (
                    server_content.model_turn or 
                    (hasattr(server_content, 'output_transcription') and server_content.output_transcription)
                )
                
                # ⚠️ 重要：检测 turn 开始 - 无论是 model_turn 还是 output_transcription 先到
                if has_ai_content and not self._is_responding:
                    # 在AI开始响应前，发送累积的用户输入
                    if self._gemini_user_transcript and self.on_input_transcript:
                        await self.on_input_transcript(self._gemini_user_transcript)
                        self._gemini_user_transcript = ""  # 清空累积
                    
                    self._is_responding = True
                    self._is_first_text_chunk = True  # 重置第一个 chunk 标记
                    self._gemini_current_transcript = ""  # 清空累积
                    if self.on_new_message:
                        await self.on_new_message()
                
                # 处理输出转录 - 流式发送每个 chunk 到前端
                # 不参与新 turn 检测；turn_complete 后到达的迟到转录会以 isNewMessage=false
                # 追加到当前轮次的气泡（正确行为）
                if hasattr(server_content, 'output_transcription') and server_content.output_transcription:
                    output_trans = server_content.output_transcription
                    if hasattr(output_trans, 'text') and output_trans.text:
                        text = output_trans.text
                        self._gemini_current_transcript += text
                        if self.on_text_delta:
                            await self.on_text_delta(text, self._is_first_text_chunk)
                            self._is_first_text_chunk = False
                
                # 处理模型输出 (音频)
                if server_content.model_turn:
                    for part in server_content.model_turn.parts:
                        # 跳过 thinking/thought 部分
                        if hasattr(part, 'thought') and part.thought:
                            continue
                        
                        # 处理音频
                        if hasattr(part, 'inline_data') and part.inline_data:
                            if isinstance(part.inline_data.data, bytes):
                                if self.on_audio_delta:
                                    await self.on_audio_delta(part.inline_data.data)
                
                # 检查是否 turn 完成（用 getattr 防止 SDK 无该字段时抛错）
                if getattr(server_content, 'turn_complete', False):
                    # Gemini Live API 不返回 token 数，仅记录调用次数
                    try:
                        from utils.token_tracker import TokenTracker
                        TokenTracker.get_instance().record(
                            model=self.model or "gemini-live",
                            prompt_tokens=0, completion_tokens=0, total_tokens=0,
                            call_type="conversation_realtime_gemini",
                            source="main_logic/omni_realtime_client",
                        )
                    except Exception:
                        pass
                    self._is_responding = False
                    # 不再调用 on_output_transcript（已通过 on_text_delta 流式发送）
                    if self.on_response_done:
                        await self.on_response_done()
                
                # 检查是否被中断
                if hasattr(server_content, 'interrupted') and server_content.interrupted:
                    self._interrupted = True
                    self._is_responding = False
                    # 被中断时也发送已累积的用户输入
                    if self._gemini_user_transcript and self.on_input_transcript:
                        await self.on_input_transcript(self._gemini_user_transcript)
                        self._gemini_user_transcript = ""
                    logger.info("Gemini response was interrupted by user")
        
        except Exception as e:
            logger.error(f"Error processing Gemini response: {e}")
