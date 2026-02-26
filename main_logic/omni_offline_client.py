# -- coding: utf-8 --

import asyncio
from typing import Optional, Callable, Dict, Any, Awaitable
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from openai import APIConnectionError, InternalServerError, RateLimitError
from config import get_extra_body
from utils.frontend_utils import calculate_text_similarity, count_words_and_chars
from utils.logger_config import get_module_logger

# Setup logger for this module
logger = get_module_logger(__name__, "Main")

class OmniOfflineClient:
    """
    A client for text-based chat that mimics the interface of OmniRealtimeClient.
    
    This class provides a compatible interface with OmniRealtimeClient but uses
    langchain's ChatOpenAI with OpenAI-compatible API instead of realtime WebSocket,
    suitable for text-only conversations.
    
    Attributes:
        base_url (str):
            The base URL for the OpenAI-compatible API (e.g., OPENROUTER_URL).
        api_key (str):
            The API key for authentication.
        model (str):
            Model to use for chat.
        vision_model (str):
            Model to use for vision tasks.
        vision_base_url (str):
            Optional separate base URL for vision model API.
        vision_api_key (str):
            Optional separate API key for vision model.
        llm (ChatOpenAI):
            Langchain ChatOpenAI client for streaming text generation.
        on_text_delta (Callable[[str, bool], Awaitable[None]]):
            Callback for text delta events.
        on_input_transcript (Callable[[str], Awaitable[None]]):
            Callback for input transcript events (user messages).
        on_output_transcript (Callable[[str, bool], Awaitable[None]]):
            Callback for output transcript events (assistant messages).
        on_connection_error (Callable[[str], Awaitable[None]]):
            Callback for connection errors.
        on_response_done (Callable[[], Awaitable[None]]):
            Callback when a response is complete.
    """
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "",
        vision_model: str = "",
        vision_base_url: str = "",  # 独立的视觉模型 API URL
        vision_api_key: str = "",   # 独立的视觉模型 API Key
        voice: str = "",  # Unused for text mode but kept for compatibility
        turn_detection_mode = None,  # Unused for text mode
        on_text_delta: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_audio_delta: Optional[Callable[[bytes], Awaitable[None]]] = None,  # Unused
        on_interrupt: Optional[Callable[[], Awaitable[None]]] = None,  # Unused
        on_input_transcript: Optional[Callable[[str], Awaitable[None]]] = None,
        on_output_transcript: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_connection_error: Optional[Callable[[str], Awaitable[None]]] = None,
        on_response_done: Optional[Callable[[], Awaitable[None]]] = None,
        on_repetition_detected: Optional[Callable[[], Awaitable[None]]] = None,
        on_response_discarded: Optional[Callable[[str, int, int, bool, Optional[str]], Awaitable[None]]] = None,
        extra_event_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]]] = None,
        max_response_length: Optional[int] = None
    ):
        # Use base_url directly without conversion
        self.base_url = base_url
        self.api_key = api_key if api_key and api_key != '' else None
        self.model = model
        self.vision_model = vision_model  # Store vision model for temporary switching
        # 视觉模型独立配置（如果未指定则回退到主配置）
        self.vision_base_url = vision_base_url if vision_base_url else base_url
        self.vision_api_key = vision_api_key if vision_api_key else api_key
        self.on_text_delta = on_text_delta
        self.on_input_transcript = on_input_transcript
        self.on_output_transcript = on_output_transcript
        self.handle_connection_error = on_connection_error
        self.on_response_done = on_response_done
        self.on_repetition_detected = on_repetition_detected
        self.on_response_discarded = on_response_discarded
        
        # Initialize langchain ChatOpenAI client
        self.llm = ChatOpenAI(
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            temperature=1.0,
            streaming=True,
            max_retries=0,  # 禁用 openai client 内置重试，由外层 retry loop 处理（内置重试会破坏流式 generator）
            extra_body=get_extra_body(self.model) or None
        )
        
        # State management
        self._is_responding = False
        self._conversation_history = []
        self._instructions = ""
        self._stream_task = None
        self._pending_images = []  # Store pending images to send with next text
        
        # 重复度检测
        self._recent_responses = []  # 存储最近3轮助手回复
        self._repetition_threshold = 0.8  # 相似度阈值
        self._max_recent_responses = 3  # 最多存储的回复数
        
        # ========== 普通对话守卫配置 ==========
        self.enable_response_guard = True     # 是否启用质量守卫
        self.max_response_length = max_response_length if isinstance(max_response_length, int) and max_response_length > 0 else 400
        self.max_response_rerolls = 2         # 最多允许的自动重试次数
        
        # 质量守卫回调：由 core.py 设置，用于通知前端清理气泡
        
    async def connect(self, instructions: str, native_audio=False) -> None:
        """Initialize the client with system instructions."""
        self._instructions = instructions
        # Add system message to conversation history using langchain format
        self._conversation_history = [
            SystemMessage(content=instructions)
        ]
        logger.info("OmniOfflineClient initialized with instructions")
    
    async def send_event(self, event) -> None:
        """Compatibility method - not used in text mode"""
        pass
    
    async def update_session(self, config: Dict[str, Any]) -> None:
        """Compatibility method - update instructions if provided"""
        if "instructions" in config:
            self._instructions = config["instructions"]
            # Update system message using langchain format
            if self._conversation_history and isinstance(self._conversation_history[0], SystemMessage):
                self._conversation_history[0] = SystemMessage(content=self._instructions)
    
    def switch_model(self, new_model: str, use_vision_config: bool = False) -> None:
        """
        Temporarily switch to a different model (e.g., vision model).
        This allows dynamic model switching for vision tasks.
        
        Args:
            new_model: The model to switch to
            use_vision_config: If True, use vision_base_url and vision_api_key
        """
        if new_model and new_model != self.model:
            logger.info(f"Switching model from {self.model} to {new_model}")
            self.model = new_model
            
            # 选择使用的 API 配置
            if use_vision_config:
                base_url = self.vision_base_url
                api_key = self.vision_api_key if self.vision_api_key and self.vision_api_key != '' else None
            else:
                base_url = self.base_url
                api_key = self.api_key
            
            # Recreate LLM instance with new model and config
            self.llm = ChatOpenAI(
                model=self.model,
                base_url=base_url,
                api_key=api_key,
                temperature=1.0,
                streaming=True,
                max_retries=0,  # 禁用内置重试
                extra_body=get_extra_body(self.model) or None
            )
    
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
            logger.warning(f"OmniOfflineClient: 检测到连续{high_similarity_count + 1}轮高重复度对话")
            
            # 清空对话历史（保留系统指令）
            if self._conversation_history and isinstance(self._conversation_history[0], SystemMessage):
                self._conversation_history = [self._conversation_history[0]]
            else:
                self._conversation_history = []
            
            # 清空重复检测缓存
            self._recent_responses.clear()
            
            # 触发回调
            if self.on_repetition_detected:
                await self.on_repetition_detected()
            
            return True
        
        return False

    async def _notify_response_discarded(self, reason: str, attempt: int, max_attempts: int, will_retry: bool,
                                         message: Optional[str] = None) -> None:
        """
        通知上层当前回复被丢弃，用于清空前端气泡/提示用户
        """
        if self.on_response_discarded:
            try:
                await self.on_response_discarded(reason, attempt, max_attempts, will_retry, message)
            except Exception as e:
                logger.warning(f"通知 response_discarded 失败: {e}")

    async def stream_text(self, text: str) -> None:
        """
        Send a text message to the API and stream the response.
        If there are pending images, temporarily switch to vision model for this turn.
        Uses langchain ChatOpenAI for streaming.
        """
        if not text or not text.strip():
            # If only images without text, use a default prompt
            if self._pending_images:
                text = "请分析这些图片。"
            else:
                return
        
        # Check if we need to switch to vision model
        has_images = len(self._pending_images) > 0
        
        # Prepare user message content
        if has_images:
            # Switch to vision model permanently for this session
            # (cannot switch back because image data remains in conversation history)
            if self.vision_model and self.vision_model != self.model:
                logger.info(f"🖼️ Temporarily switching to vision model: {self.vision_model} (from {self.model})")
                self.switch_model(self.vision_model, use_vision_config=True)
            
            # Multi-modal message: images + text
            content = []
            
            # Add images first
            for img_b64 in self._pending_images:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_b64}"
                    }
                })
            
            # Add text
            content.append({
                "type": "text",
                "text": text.strip()
            })
            
            user_message = HumanMessage(content=content)
            logger.info(f"Sending multi-modal message with {len(self._pending_images)} images")
            
            # Clear pending images after using them
            self._pending_images.clear()
        else:
            # Text-only message
            user_message = HumanMessage(content=text.strip())
        
        self._conversation_history.append(user_message)
        
        # Callback for user input
        if self.on_input_transcript:
            await self.on_input_transcript(text.strip())
        
        # Retry策略：重试2次，间隔1秒、2秒
        max_retries = 3
        retry_delays = [1, 2]
        assistant_message = ""
        
        try:
            self._is_responding = True
            reroll_count = 0
            
            # 防御性检查：确保对话历史中至少有用户消息
            has_user_message = any(isinstance(msg, HumanMessage) for msg in self._conversation_history)
            if not has_user_message:
                error_msg = "对话历史中没有用户消息，无法生成回复"
                logger.error(f"OmniOfflineClient: {error_msg}")
                if self.handle_connection_error:
                    await self.handle_connection_error(error_msg)
                return
            
            guard_exhausted = False
            for attempt in range(max_retries):
                try:
                    assistant_message = ""
                    guard_attempt = 0
                    while guard_attempt <= self.max_response_rerolls:
                        self._is_responding = True
                        assistant_message = ""
                        is_first_chunk = True
                        pipe_count = 0  # 围栏：追踪 | 字符的出现次数
                        fence_triggered = False  # 围栏是否已触发
                        guard_triggered = False
                        discard_reason = None
                        
                        async for chunk in self.llm.astream(self._conversation_history):
                            if not self._is_responding:
                                break
                            
                            if fence_triggered:
                                break
                            
                            content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                            
                            if content and content.strip():
                                truncated_content = content
                                for idx, char in enumerate(content):
                                    if char == '|':
                                        pipe_count += 1
                                        if pipe_count >= 2:
                                            truncated_content = content[:idx]
                                            fence_triggered = True
                                            logger.info("OmniOfflineClient: 围栏触发 - 检测到第二个 | 字符，截断输出")
                                            break
                                
                                if truncated_content and truncated_content.strip():
                                    assistant_message += truncated_content
                                    if self.on_text_delta:
                                        await self.on_text_delta(truncated_content, is_first_chunk)
                                    is_first_chunk = False
                                    
                                    if self.enable_response_guard:
                                        current_length = count_words_and_chars(assistant_message)
                                        if current_length > self.max_response_length:
                                            guard_triggered = True
                                            discard_reason = f"length>{self.max_response_length}"
                                            logger.info(f"OmniOfflineClient: 检测到长回复 ({current_length}字)，准备重试")
                                            self._is_responding = False
                                            break
                            elif content and not content.strip():
                                logger.debug(f"OmniOfflineClient: 过滤空白内容 - content_repr: {repr(content)[:100]}")
                        
                        if guard_triggered:
                            guard_attempt += 1
                            reroll_count += 1
                            will_retry = guard_attempt <= self.max_response_rerolls
                            # 区分原因：超长用明确提示，其它守卫原因用通用提示
                            if discard_reason and "length>" in discard_reason:
                                final_message = "回复过长，已放弃输出（可在配置中调大 TEXT_GUARD_MAX_LENGTH）"
                            else:
                                final_message = "AI回复不符合要求，已放弃输出"
                            failure_message = None if will_retry else final_message
                            await self._notify_response_discarded(
                                discard_reason or "guard",
                                guard_attempt,
                                self.max_response_rerolls,
                                will_retry,
                                failure_message
                            )
                            
                            if will_retry:
                                logger.info(f"OmniOfflineClient: 响应被丢弃（{discard_reason}），第 {guard_attempt}/{self.max_response_rerolls} 次重试")
                                continue
                            
                            logger.warning("OmniOfflineClient: guard 重试耗尽，放弃输出")
                            if self.handle_connection_error:
                                await self.handle_connection_error(final_message)
                            assistant_message = ""
                            guard_exhausted = True
                            break
                        
                        if assistant_message:
                            self._conversation_history.append(AIMessage(content=assistant_message))
                            await self._check_repetition(assistant_message)
                        break
                    
                    if guard_exhausted:
                        break
                    
                    if assistant_message:
                        break
                            
                except (APIConnectionError, InternalServerError, RateLimitError) as e:
                    logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                    if attempt < max_retries - 1:
                        wait_time = retry_delays[attempt]
                        logger.warning(f"OmniOfflineClient: LLM调用失败 (尝试 {attempt + 1}/{max_retries})，{wait_time}秒后重试: {e}")
                        # 通知前端正在重试
                        if self.handle_connection_error:
                            await self.handle_connection_error(f"连接问题，正在重试...（第{attempt + 1}次）")
                        await asyncio.sleep(wait_time)
                        continue  # 继续下一次重试
                    else:
                        error_msg = f"LLM调用失败，已重试{max_retries}次: {str(e)}"
                        logger.error(error_msg)
                        if self.handle_connection_error:
                            await self.handle_connection_error(error_msg)
                        break
                except Exception as e:
                    error_msg = f"Error in text streaming: {str(e)}"
                    logger.error(error_msg)
                    if self.handle_connection_error:
                        await self.handle_connection_error(error_msg)
                    break  # 非重试类错误直接退出
        finally:
            self._is_responding = False
            
            # 空回复兜底：如果所有重试都未产生文本，向前端发送错误提示
            if not assistant_message and not guard_exhausted:
                logger.warning("OmniOfflineClient: 所有重试均未产生文本回复")
                if self.on_text_delta:
                    fallback_msg = "（服务暂时不稳定，请再试一次）"
                    await self.on_text_delta(fallback_msg, True)
            
            # Call response done callback
            if self.on_response_done:
                await self.on_response_done()
    
    async def stream_audio(self, audio_chunk: bytes) -> None:
        """Compatibility method - not used in text mode"""
        pass
    
    async def stream_image(self, image_b64: str) -> None:
        """
        Add an image to pending images queue.
        Images will be sent together with the next text message.
        """
        if not image_b64:
            return
        
        # Store base64 image
        self._pending_images.append(image_b64)
        logger.info(f"Added image to pending queue (total: {len(self._pending_images)})")
    
    def has_pending_images(self) -> bool:
        """Check if there are pending images waiting to be sent."""
        return len(self._pending_images) > 0
    
    async def create_response(self, instructions: str, skipped: bool = False) -> None:
        """
        Process a system message or instruction.
        For compatibility with OmniRealtimeClient interface.
        """
        # Extract actual instruction if it starts with "SYSTEM_MESSAGE | "
        if instructions.startswith("SYSTEM_MESSAGE | "):
            instructions = instructions[17:]  # Remove prefix
        
        # Add as system message using langchain format
        if instructions.strip():
            self._conversation_history.append(SystemMessage(content=instructions))
    
    async def stream_proactive(self, instruction: str) -> bool:
        """Generate and stream a proactive AI response driven by a system instruction.

        The *instruction* is expected to be pre-formatted by the caller using the
        ========...======== convention and is injected as a temporary HumanMessage.
        It is **not** persisted to _conversation_history.  Only the AI's
        natural-language response (AIMessage) is kept in history.

        Calls on_response_done() when finished (same as stream_text).
        Returns True if any text was generated, False if aborted or empty.
        """
        if not instruction or not instruction.strip():
            return False

        # 临时注入：instruction 已由调用方用 ======== 格式封装，作为 HumanMessage 发送，
        # 不持久化到 _conversation_history，避免污染长期上下文。
        messages_to_send = (
            self._conversation_history
            + [HumanMessage(content=instruction)]
        )

        assistant_message = ""
        is_first_chunk = True

        try:
            self._is_responding = True
            async for chunk in self.llm.astream(messages_to_send):
                if not self._is_responding:
                    break
                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                if content and content.strip():
                    assistant_message += content
                    if self.on_text_delta:
                        await self.on_text_delta(content, is_first_chunk)
                    is_first_chunk = False
        except Exception as e:
            error_msg = f"OmniOfflineClient.stream_proactive error: {e}"
            logger.error(error_msg)
            if self.handle_connection_error:
                await self.handle_connection_error(error_msg)
            assistant_message = ""  # 防止残缺内容被 finally 写入历史
            return False
        finally:
            self._is_responding = False
            if assistant_message:
                self._conversation_history.append(AIMessage(content=assistant_message))
            if self.on_response_done:
                await self.on_response_done()

        return bool(assistant_message)

    async def cancel_response(self) -> None:
        """Cancel the current response if possible"""
        self._is_responding = False
        # Stop processing new chunks by setting flag
    
    async def handle_interruption(self):
        """Handle user interruption - cancel current response"""
        if not self._is_responding:
            return
        
        logger.info("Handling text mode interruption")
        await self.cancel_response()
    
    async def handle_messages(self) -> None:
        """
        Compatibility method for OmniRealtimeClient interface.
        In text mode, this is a no-op as we don't have a persistent connection.
        """
        # Keep this task alive to match the interface
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Text mode message handler cancelled")
    
    async def close(self) -> None:
        """Close the client and cleanup resources."""
        self._is_responding = False
        self._conversation_history = []
        self._pending_images.clear()
        logger.info("OmniOfflineClient closed")
