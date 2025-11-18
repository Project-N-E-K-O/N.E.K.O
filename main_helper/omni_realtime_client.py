# -- coding: utf-8 --

import asyncio
import websockets
import json
import base64
import time
import logging

from typing import Optional, Callable, Dict, Any, Awaitable
from enum import Enum
from langchain_openai import ChatOpenAI
from utils.config_manager import get_config_manager

# Setup logger for this module
logger = logging.getLogger(__name__)

class TurnDetectionMode(Enum):
    SERVER_VAD = "server_vad"
    MANUAL = "manual"

_config_manager = get_config_manager()


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
        extra_event_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]]] = None,
        api_type: Optional[str] = None
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
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
        self.extra_event_handlers = extra_event_handlers or {}

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
        # Track image recognition per turn
        self._image_recognized_this_turn = False
        self._image_being_analyzed = False
        self._image_description = "[用户的实时屏幕截图或相机画面正在分析中。你先不要瞎编内容，可以请用户稍等片刻。在此期间不要用搜索功能应付。等收到画面分析结果后再描述画面。]"
        
        # Silence detection for auto-closing inactive sessions
        # 只在 GLM 和 free API 时启用90秒静默超时，Qwen 和 Step 放行
        self._last_speech_time = None
        self._api_type = api_type or ""
        # 只在 GLM 和 free 时启用静默超时
        self._enable_silence_timeout = self._api_type.lower() in ['glm', 'free']
        self._silence_timeout_seconds = 90  # 90秒无语音输入则自动关闭
        self._silence_check_task = None
        self._silence_timeout_triggered = False

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
                
                if self._last_speech_time is None:
                    # 还没有检测到任何语音，从现在开始计时
                    self._last_speech_time = time.time()
                    continue
                
                elapsed = time.time() - self._last_speech_time
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

    async def connect(self, instructions: str, native_audio=True) -> None:
        """Establish WebSocket connection with the Realtime API."""
        url = f"{self.base_url}?model={self.model}" if self.model != "free-model" else self.base_url
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
            if 'glm' in self.model:
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
                    "temperature": 0.7
                })
            elif "qwen" in self.model:
                await self.update_session({
                    "instructions": instructions,
                    "modalities": self._modalities ,
                    "voice": self.voice if self.voice else "Cherry",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_transcription": {
                        "model": "gummy-realtime-v1"
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms":300,
                        "silence_duration_ms": 500
                    },
                    "temperature": 0.4
                })
            elif "gpt" in self.model:
                await self.update_session({
                    "type": "realtime",
                    "model": "gpt-realtime",
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
            elif "step" in self.model:
                await self.update_session({
                    "instructions": instructions + '\n请使用默认女声与用户交流。\n',
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
            elif "free" in self.model:
                await self.update_session({
                    "instructions": instructions + '\n请使用默认女声与用户交流。\n',
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

    async def send_event(self, event) -> None:
        event['event_id'] = "event_" + str(int(time.time() * 1000))
        if self.ws:
            try:
                await self.ws.send(json.dumps(event))
            except Exception as e:
                logger.warning(f"⚠️ 发送事件失败: {e}")
                raise

    async def update_session(self, config: Dict[str, Any]) -> None:
        """Update session configuration."""
        event = {
            "type": "session.update",
            "session": config
        }
        await self.send_event(event)

    async def stream_audio(self, audio_chunk: bytes) -> None:
        """Stream raw audio data to the API."""
        # only support 16bit 16kHz mono pcm
        audio_b64 = base64.b64encode(audio_chunk).decode()

        append_event = {
            "type": "input_audio_buffer.append",
            "audio": audio_b64
        }
        await self.send_event(append_event)

    async def _analyze_image_with_vision_model(self, image_b64: str) -> str:
        """Use VISION_MODEL to analyze image and return description."""
        try:
            self._image_being_analyzed = True
            core_config = _config_manager.get_core_config()
            vision_model = core_config.get('VISION_MODEL', '')
            openrouter_url = core_config.get('OPENROUTER_URL', '')
            openrouter_api_key = core_config.get('OPENROUTER_API_KEY', '')
            
            if not vision_model:
                logger.warning("VISION_MODEL not configured, skipping image analysis")
                return ""
            
            logger.info(f"🖼️ Using VISION_MODEL ({vision_model}) to analyze image")
            
            # Create vision LLM client
            vision_llm = ChatOpenAI(
                model=vision_model,
                base_url=openrouter_url,
                api_key=openrouter_api_key,
                temperature=0.1,
                max_tokens=500
            )
            
            # Prepare multi-modal message
            messages = [
                {
                    "role": "system",
                    "content": "你是一个图像描述助手, 请简洁地描述图片中的主要内容、关键细节和你觉得有趣的地方。你的回答不能超过250字。"
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": "请描述这张图片的内容。"
                        }
                    ]
                }
            ]
            
            # Call vision model
            response = await vision_llm.ainvoke(messages)
            description = response.content.strip()
            self._image_description = f"[用户的实时屏幕截图或相机画面]: {description}"
            
            logger.info(f"✅ Image analysis complete.")
            self._image_being_analyzed = False
            return description
            
        except Exception as e:
            logger.error(f"Error analyzing image with vision model: {e}")
            self._image_being_analyzed = False
            return ""
    
    async def stream_image(self, image_b64: str) -> None:
        """Stream raw image data to the API."""

        try:
            if '用户的实时屏幕截图或相机画面正在分析中' in self._image_description and self.model in ['step', 'free']:
                await self._analyze_image_with_vision_model(image_b64)
                return

            if self._audio_in_buffer:
                if "qwen" in self.model:
                    append_event = {
                        "type": "input_image_buffer.append" ,
                        "image": image_b64
                    }
                elif "glm" in self.model:
                    append_event = {
                        "type": "input_audio_buffer.append_video_frame",
                        "video_frame": image_b64
                    }
                elif "gpt" in self.model:
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
                    if not self._image_recognized_this_turn:
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
                        logger.info(f"✅ Image description injected into conversation context: {self._image_description[:100]}...")
                        await self.send_event(text_event)
                        self._image_recognized_this_turn = True
                    
                    if self._image_being_analyzed:
                        return
                    
                    logger.info(f"⚠️ Model {self.model} does not support video streaming, using VISION_MODEL")
                    await self._analyze_image_with_vision_model(image_b64)
                    return
                    
                await self.send_event(append_event)
        except Exception as e:
            logger.error(f"Error streaming image: {e}")
            raise e

    async def create_response(self, instructions: str, skipped: bool = False) -> None:
        """Request a response from the API. First adds message to conversation, then creates response."""
        if skipped == True:
            self._skip_until_next_response = True

        if "qwen" in self.model:
            await self.update_session({"instructions": self.instructions + '\n' + instructions})

            logger.info(f"Creating response with instructions override")
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
            logger.info(f"Adding conversation item: {item_event}")
            await self.send_event(item_event)
            
            # 然后调用 response.create，不带 instructions（避免替换 session instructions）
            logger.info(f"Creating response without instructions override")
            await self.send_event({"type": "response.create"})

    async def cancel_response(self) -> None:
        """Cancel the current response."""
        event = {
            "type": "response.cancel"
        }
        await self.send_event(event)

    async def handle_interruption(self):
        """Handle user interruption of the current response."""
        if not self._is_responding:
            return

        logger.info("Handling interruption")

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
                    logger.error(f"API Error: {event['error']}")
                    if '欠费' in event['error'] or 'standing' in event['error']:
                        if self.on_connection_error:
                            await self.on_connection_error(event['error'])
                        await self.close()
                    continue
                elif event_type == "response.done":
                    self._is_responding = False
                    self._current_response_id = None
                    self._current_item_id = None
                    self._skip_until_next_response = False
                    # 响应完成，确保buffer被清空
                    self._output_transcript_buffer = ""
                    self._image_recognized_this_turn = False
                    if self.on_response_done:
                        await self.on_response_done()
                elif event_type == "response.created":
                    self._current_response_id = event.get("response", {}).get("id")
                    self._is_responding = True
                    self._is_first_text_chunk = self._is_first_transcript_chunk = True
                    # 清空转录buffer，防止累积旧内容
                    self._output_transcript_buffer = ""
                elif event_type == "response.output_item.added":
                    self._current_item_id = event.get("item", {}).get("id")
                # Handle interruptions
                elif event_type == "input_audio_buffer.speech_started":
                    logger.info("Speech detected")
                    self._audio_in_buffer = True
                    # 重置静默计时器
                    self._last_speech_time = time.time()
                    if self._is_responding:
                        logger.info("Handling interruption")
                        await self.handle_interruption()
                elif event_type == "input_audio_buffer.speech_stopped":
                    logger.info("Speech ended")
                    if self.on_new_message:
                        await self.on_new_message()
                    self._audio_in_buffer = False
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    self._print_input_transcript = True
                elif event_type in ["response.audio_transcript.done", "response.output_audio_transcript.done"]:
                    self._print_input_transcript = False
                    self._output_transcript_buffer = ""

                if not self._skip_until_next_response:
                    if event_type in ["response.text.delta", "response.output_text.delta"]:
                        if self.on_text_delta:
                            if "glm" not in self.model:
                                await self.on_text_delta(event["delta"], self._is_first_text_chunk)
                                self._is_first_text_chunk = False
                    elif event_type in ["response.audio.delta", "response.output_audio.delta"]:
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
                await self.on_connection_error("💥 连接超时，请检查网络连接。")
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
        
        if self.ws:
            try:
                # 尝试关闭websocket连接
                await self.ws.close()
            except websockets.exceptions.ConnectionClosedOK:
                logger.warning("OmniRealtimeClient: WebSocket connection already closed (OK).")
            except websockets.exceptions.ConnectionClosedError as e:
                logger.error(f"OmniRealtimeClient: WebSocket connection closed with error: {e}")
            except Exception as e:
                logger.error(f"OmniRealtimeClient: Error closing WebSocket connection: {e}")
            finally:
                self.ws = None
