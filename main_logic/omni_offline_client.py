# -- coding: utf-8 --

import asyncio
import json
import time
from typing import Optional, Callable, Dict, Any, Awaitable
from utils.llm_client import SystemMessage, HumanMessage, AIMessage, create_chat_llm
from openai import APIConnectionError, InternalServerError, RateLimitError
from utils.frontend_utils import calculate_text_similarity, count_words_and_chars
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type

# Setup logger for this module
logger = get_module_logger(__name__, "Main")

class OmniOfflineClient:
    """
    A client for text-based chat that mimics the interface of OmniRealtimeClient.
    
    This class provides a compatible interface with OmniRealtimeClient but uses
    ChatOpenAI with OpenAI-compatible API instead of realtime WebSocket,
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
            ChatOpenAI client for streaming text generation.
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
        on_status_message: Optional[Callable[[str], Awaitable[None]]] = None,
        extra_event_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]]] = None,
        max_response_length: Optional[int] = None,
        lanlan_name: str = "",
        master_name: str = ""
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
        self.on_status_message = on_status_message
        self.on_response_done = on_response_done
        self.on_proactive_done: Optional[Callable[[], Awaitable[None]]] = None
        self.on_repetition_detected = on_repetition_detected
        self.on_response_discarded = on_response_discarded
        
        # Initialize ChatOpenAI client
        self.llm = create_chat_llm(
            self.model, self.base_url, self.api_key,
            temperature=1.0, streaming=True, max_retries=0,
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
        self.max_response_length = max_response_length if isinstance(max_response_length, int) and max_response_length > 0 else 300
        self.max_response_rerolls = 2         # 最多允许的自动重试次数

        # ========== 输出前缀检测 ==========
        self.lanlan_name = lanlan_name
        self.master_name = master_name
        self._prefix_buffer_size = max(len(lanlan_name), len(master_name)) + 3 if (lanlan_name or master_name) else 0

        # 质量守卫回调：由 core.py 设置，用于通知前端清理气泡

    def update_max_response_length(self, max_length: int) -> None:
        """更新回复字数限制(用户可能在对话期间修改设置)"""
        if isinstance(max_length, int) and max_length >= 0:
            self.max_response_length = max_length if max_length > 0 else 999999
            logger.debug(f"OmniOfflineClient: 字数限制已更新为 {max_length}")

    def _match_name_prefix(self, text: str, name: str) -> int:
        """Check if text starts with a name prefix like 'Name | ' or 'Name |'.
        Returns the length of the matched prefix, or 0 if no match.
        Handles variants with/without spaces around the pipe character.
        """
        if not name:
            return 0
        for variant in (f"{name} | ", f"{name} |", f"{name}| ", f"{name}|"):
            if text.startswith(variant):
                return len(variant)
        return 0

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
    
    async def switch_model(self, new_model: str, use_vision_config: bool = False) -> None:
        """
        Temporarily switch to a different model (e.g., vision model).
        This allows dynamic model switching for vision tasks.

        Args:
            new_model: The model to switch to
            use_vision_config: If True, use vision_base_url and vision_api_key
        """
        if new_model and new_model != self.model:
            logger.info(f"Switching model from {self.model} to {new_model}")

            # 选择使用的 API 配置
            if use_vision_config:
                base_url = self.vision_base_url
                api_key = self.vision_api_key if self.vision_api_key and self.vision_api_key != '' else None
            else:
                base_url = self.base_url
                api_key = self.api_key

            # 先创建新 client，成功后再原子替换，避免半切换状态
            new_llm = create_chat_llm(
                new_model, base_url, api_key,
                temperature=1.0, streaming=True, max_retries=0,
            )
            old_llm = self.llm
            self.llm = new_llm
            self.model = new_model
            try:
                await old_llm.aclose()
            except Exception as e:
                logger.warning(f"switch_model: old client aclose failed: {e}")
    
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
                await self.switch_model(self.vision_model, use_vision_config=True)
            
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
        status_reported = False
        guard_exhausted = False
        
        try:
            self._is_responding = True
            reroll_count = 0
            set_call_type("conversation")

            # 防御性检查：确保对话历史中至少有用户消息
            has_user_message = any(isinstance(msg, HumanMessage) for msg in self._conversation_history)
            if not has_user_message:
                error_msg = "对话历史中没有用户消息，无法生成回复"
                logger.error(f"OmniOfflineClient: {error_msg}")
                if self.on_status_message:
                    await self.on_status_message(json.dumps({"code": "NO_USER_MESSAGE"}))
                    status_reported = True
                return
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
                        chunk_usage = None
                        prefix_buffer = ""
                        prefix_checked = not bool(self._prefix_buffer_size)

                        async for chunk in self.llm.astream(self._conversation_history):
                            if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                                chunk_usage = chunk.usage_metadata
                                logger.debug(f"🔍 [Usage] {chunk_usage}")
                            if hasattr(chunk, 'response_metadata') and chunk.response_metadata:
                                if 'token_usage' in chunk.response_metadata or 'usage' in chunk.response_metadata:
                                    logger.debug(f"🔍 [Meta] {chunk.response_metadata}")
                            if not self._is_responding:
                                break

                            if fence_triggered:
                                break

                            content = chunk.content if hasattr(chunk, 'content') else str(chunk)

                            if content and content.strip():
                                truncated_content = content

                                # ── 前缀检测阶段：缓冲初始输出，判断是否有角色名前缀 ──
                                if not prefix_checked:
                                    prefix_buffer += truncated_content
                                    if len(prefix_buffer) >= self._prefix_buffer_size:
                                        prefix_checked = True
                                        master_match = self._match_name_prefix(prefix_buffer, self.master_name)
                                        lanlan_match = self._match_name_prefix(prefix_buffer, self.lanlan_name)
                                        if master_match:
                                            guard_triggered = True
                                            discard_reason = "role_hallucination"
                                            logger.info(f"OmniOfflineClient: 检测到主人名前缀 '{prefix_buffer[:master_match]}'，触发重试")
                                            self._is_responding = False
                                            break
                                        elif lanlan_match:
                                            logger.info(f"OmniOfflineClient: 剥离角色名前缀 '{prefix_buffer[:lanlan_match]}'")
                                            truncated_content = prefix_buffer[lanlan_match:]
                                        else:
                                            truncated_content = prefix_buffer
                                        # 前缀解析完毕，将结果送入下方的通用 emit/guard 路径
                                        if not (truncated_content and truncated_content.strip()):
                                            continue
                                    else:
                                        continue  # 缓冲区未满，等更多 chunk

                                for idx, char in enumerate(truncated_content):
                                    if char == '|':
                                        pipe_count += 1
                                        if pipe_count >= 2:
                                            truncated_content = truncated_content[:idx]
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

                        # 流结束后：flush 未处理的前缀缓冲区（走通用 emit/guard 路径）
                        if prefix_buffer and not prefix_checked:
                            prefix_checked = True
                            master_match = self._match_name_prefix(prefix_buffer, self.master_name)
                            lanlan_match = self._match_name_prefix(prefix_buffer, self.lanlan_name)
                            if master_match:
                                guard_triggered = True
                                discard_reason = "role_hallucination"
                                logger.info(f"OmniOfflineClient: 流结束时检测到主人名前缀 '{prefix_buffer[:master_match]}'，触发重试")
                            else:
                                flush_text = prefix_buffer
                                if lanlan_match:
                                    logger.info(f"OmniOfflineClient: 流结束时剥离角色名前缀 '{prefix_buffer[:lanlan_match]}'")
                                    flush_text = prefix_buffer[lanlan_match:]
                                # fence + length guard
                                for idx, char in enumerate(flush_text):
                                    if char == '|':
                                        pipe_count += 1
                                        if pipe_count >= 2:
                                            flush_text = flush_text[:idx]
                                            fence_triggered = True
                                            break
                                if flush_text and flush_text.strip():
                                    assistant_message += flush_text
                                    if self.on_text_delta:
                                        await self.on_text_delta(flush_text, is_first_chunk)
                                    is_first_chunk = False
                                    if self.enable_response_guard:
                                        current_length = count_words_and_chars(assistant_message)
                                        if current_length > self.max_response_length:
                                            guard_triggered = True
                                            discard_reason = f"length>{self.max_response_length}"

                        if guard_triggered:
                            guard_attempt += 1
                            reroll_count += 1
                            will_retry = guard_attempt <= self.max_response_rerolls
                            # 区分原因：超长用明确提示，其它守卫原因用通用提示
                            if discard_reason and "length>" in discard_reason:
                                final_message = json.dumps({"code": "RESPONSE_TOO_LONG"})
                            else:
                                final_message = json.dumps({"code": "RESPONSE_INVALID"})
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
                            if self.on_status_message:
                                await self.on_status_message(final_message)
                                status_reported = True
                            assistant_message = ""
                            guard_exhausted = True
                            break
                        
                        # Token usage 由 _AsyncStreamWrapper hook 在流结束时自动记录，
                        # 此处不再手动调用 TokenTracker.record() 避免双重计数。

                        if assistant_message:
                            self._conversation_history.append(AIMessage(content=assistant_message))
                            await self._check_repetition(assistant_message)
                        break
                    
                    if guard_exhausted:
                        break
                    
                    if assistant_message:
                        break
                            
                except (APIConnectionError, InternalServerError, RateLimitError) as e:
                    error_type = type(e).__name__
                    error_str_lower = str(e).lower()
                    is_internal_error = isinstance(e, InternalServerError)
                    logger.info(f"ℹ️ 捕获到 {error_type} 错误")

                    # 欠费/API Key 错误立即上报并终止；配额错误上报但继续重试
                    if '欠费' in error_str_lower or 'standing' in error_str_lower:
                        logger.error(f"OmniOfflineClient: 检测到欠费错误，直接上报: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_ARREARS"}))
                            status_reported = True
                        break
                    elif ('401' in error_str_lower or 'unauthorized' in error_str_lower
                            or 'authentication' in error_str_lower
                            or ('invalid' in error_str_lower and 'key' in error_str_lower)):
                        logger.error(f"OmniOfflineClient: 检测到 API Key 错误，直接上报: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_KEY_REJECTED"}))
                            status_reported = True
                        break
                    elif 'quota' in error_str_lower or 'time limit' in error_str_lower:
                        logger.warning(f"OmniOfflineClient: 检测到配额错误，上报前端: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_QUOTA_TIME"}))

                    if attempt < max_retries - 1:
                        wait_time = retry_delays[attempt]
                        logger.warning(f"OmniOfflineClient: LLM调用失败 (尝试 {attempt + 1}/{max_retries})，{wait_time}秒后重试: {e}")
                        # 如果 attempt 已经向前端吐过 chunk，通知前端清除废气泡，
                        # 否则 retry 的新流会接在旧气泡后面，产生两段不同内容拼接。
                        if assistant_message and self.on_response_discarded:
                            await self._notify_response_discarded(
                                f"api_error:{error_type}",
                                attempt + 1,
                                max_retries,
                                will_retry=True,
                                message=None,
                            )
                        assistant_message = ""
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        error_msg = f"💥 LLM连接失败（{error_type}），已重试{max_retries}次: {e}"
                        logger.error(error_msg)
                        if self.on_status_message:
                            if is_internal_error:
                                await self.on_status_message(json.dumps({"code": "LLM_UPSTREAM_ERROR"}))
                            else:
                                await self.on_status_message(json.dumps({"code": "LLM_CONNECTION_EXHAUSTED", "details": {"error_type": error_type, "max_retries": max_retries, "error": str(e)}}))
                            status_reported = True
                        break
                except Exception as e:
                    error_msg = f"💥 文本生成异常: {type(e).__name__}: {e}"
                    logger.error(error_msg)
                    if self.on_status_message:
                        await self.on_status_message(json.dumps({"code": "TEXT_GEN_ERROR", "details": {"error_type": type(e).__name__, "error": str(e)}}))
                        status_reported = True
                    break
        finally:
            self._is_responding = False
            
            if not assistant_message and not guard_exhausted and not status_reported:
                logger.warning("OmniOfflineClient: 所有重试均未产生文本回复")
                if self.on_status_message:
                    await self.on_status_message(json.dumps({"code": "LLM_NO_RESPONSE"}))
            
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
    
    # ------------------------------------------------------------------
    # LLM message injection channels
    #
    # There are three distinct channels for injecting content into the
    # LLM context.  Each has different persistence and triggering
    # semantics.  Callers should pick the right one:
    #
    #   prime_context(text, skipped)
    #       Session-start context priming.  Appends *text* to the system
    #       prompt (position 0 in _conversation_history).  Used during
    #       hot-swap to inject incremental conversation cache and task
    #       summaries into a freshly created session.  The text becomes
    #       part of the permanent system prompt.
    #       Typical caller: core._perform_final_swap_sequence()
    #
    #   create_response(text, skipped)
    #       Mid-conversation persistent message.  Appends a HumanMessage
    #       to _conversation_history so the instruction and its reply
    #       both persist across turns.  Mirrors the OpenAI Realtime API's
    #       conversation.item.create + response.create pattern.
    #       No active callers at present; kept as a stable interface.
    #
    #   prompt_ephemeral(instruction)
    #       Fire-and-forget instruction.  The instruction is sent to the
    #       LLM together with the conversation history but is NOT saved;
    #       only the AI's response (AIMessage) is persisted.  Used for
    #       agent task notifications, greetings, and other proactive
    #       messages where the instruction is a stage direction that
    #       should not pollute long-term context.
    #       Typical callers: core.trigger_agent_callbacks(),
    #                        core.trigger_greeting()
    # ------------------------------------------------------------------

    async def prime_context(self, text: str, skipped: bool = False) -> None:
        """Append context to the system prompt at session start.

        Called during hot-swap to inject incremental conversation cache
        and/or task summaries into a freshly created session.  The *text*
        is concatenated to the existing SystemMessage at position 0 —
        format naturally continues the ``role | text`` lines already
        present in the initial prompt, followed by ``======`` delimiters.

        This method MUST only be called before any user interaction on the
        session (i.e. the conversation history contains only the initial
        SystemMessage from ``connect()``).

        Args:
            text: Context to append (incremental cache + summary/ready).
            skipped: Accepted for interface compatibility with
                     OmniRealtimeClient but not implemented in the
                     offline (text-mode) path.
        """
        if not text or not text.strip():
            return

        if self._conversation_history and isinstance(self._conversation_history[0], SystemMessage):
            self._conversation_history[0] = SystemMessage(
                content=self._conversation_history[0].content + text
            )
        else:
            # Defensive: should never happen — connect() always sets [0].
            self._conversation_history.insert(0, SystemMessage(content=text))

    async def create_response(self, instructions: str, skipped: bool = False) -> None:
        """Inject a persistent message and trigger an LLM response.

        Appends *instructions* as a HumanMessage to the conversation
        history.  Both the instruction and the LLM's reply persist across
        turns.  This mirrors the OpenAI Realtime API's
        ``conversation.item.create`` (role=user) + ``response.create``
        pattern.

        Unlike ``prime_context`` (system-prompt level, session start only)
        and ``prompt_ephemeral`` (instruction discarded after response),
        messages injected here become permanent conversation history.

        No active callers at present; kept as a stable interface for
        future mid-conversation injection needs.

        Args:
            instructions: Text to inject as a HumanMessage.
            skipped: Accepted for interface compatibility with
                     OmniRealtimeClient but not implemented in the
                     offline (text-mode) path.
        """
        if instructions and instructions.strip():
            self._conversation_history.append(HumanMessage(content=instructions))
    
    async def prompt_ephemeral(self, instruction: str) -> bool:
        """Send a fire-and-forget instruction to the LLM and stream the response.

        The *instruction* (typically wrapped in ``======...======`` delimiters)
        is appended as a temporary HumanMessage for this single LLM call
        but is **not** persisted to ``_conversation_history``.  Only the
        AI's natural-language response (AIMessage) is kept in history.

        This is the correct channel for agent task notifications, greeting
        nudges, and any scenario where the AI should respond to a stage
        direction that must not pollute long-term context.

        Unlike ``prime_context`` (appends to system prompt, session start)
        and ``create_response`` (persistent HumanMessage), the instruction
        here is truly ephemeral — it exists only for the duration of this
        single LLM inference call.

        Calls ``on_proactive_done()`` when finished — a lightweight
        callback that only flushes TTS and sends ``turn_end`` to the
        frontend, WITHOUT triggering hot-swap or ``analyze_request``
        logic.  Falls back to ``on_response_done()`` if
        ``on_proactive_done`` is not set.

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
        chunk_usage = None
        prefix_buffer = ""
        prefix_checked = not bool(self._prefix_buffer_size)

        try:
            self._is_responding = True
            set_call_type("proactive")
            async for chunk in self.llm.astream(messages_to_send):
                if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                    chunk_usage = chunk.usage_metadata
                    logger.debug(f"🔍 [Usage-Proactive] {chunk_usage}")
                if hasattr(chunk, 'response_metadata') and chunk.response_metadata:
                    if 'token_usage' in chunk.response_metadata or 'usage' in chunk.response_metadata:
                        logger.debug(f"🔍 [Meta-Proactive] {chunk.response_metadata}")

                if not self._is_responding:
                    break
                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                if content and content.strip():
                    emit_content = content

                    # ── 前缀检测阶段：缓冲初始输出，剥离角色名前缀 ──
                    if not prefix_checked:
                        prefix_buffer += emit_content
                        if len(prefix_buffer) >= self._prefix_buffer_size:
                            prefix_checked = True
                            master_match = self._match_name_prefix(prefix_buffer, self.master_name)
                            lanlan_match = self._match_name_prefix(prefix_buffer, self.lanlan_name)
                            if master_match:
                                logger.info(f"OmniOfflineClient.prompt_ephemeral: 剥离主人名前缀 '{prefix_buffer[:master_match]}'")
                                emit_content = prefix_buffer[master_match:]
                            elif lanlan_match:
                                logger.info(f"OmniOfflineClient.prompt_ephemeral: 剥离角色名前缀 '{prefix_buffer[:lanlan_match]}'")
                                emit_content = prefix_buffer[lanlan_match:]
                            else:
                                emit_content = prefix_buffer
                            if not (emit_content and emit_content.strip()):
                                continue
                        else:
                            continue  # 缓冲区未满，等更多 chunk

                    assistant_message += emit_content
                    if self.on_text_delta:
                        await self.on_text_delta(emit_content, is_first_chunk)
                    is_first_chunk = False

            # ── flush 前缀缓冲区（流提前结束时） ──
            if prefix_buffer and not prefix_checked:
                prefix_checked = True
                master_match = self._match_name_prefix(prefix_buffer, self.master_name)
                lanlan_match = self._match_name_prefix(prefix_buffer, self.lanlan_name)
                if master_match:
                    logger.info("OmniOfflineClient.prompt_ephemeral: 流结束时剥离主人名前缀")
                    flush_text = prefix_buffer[master_match:]
                elif lanlan_match:
                    logger.info("OmniOfflineClient.prompt_ephemeral: 流结束时剥离角色名前缀")
                    flush_text = prefix_buffer[lanlan_match:]
                else:
                    flush_text = prefix_buffer
                if flush_text and flush_text.strip():
                    assistant_message += flush_text
                    if self.on_text_delta:
                        await self.on_text_delta(flush_text, is_first_chunk)
                    is_first_chunk = False
        except Exception as e:
            logger.error("OmniOfflineClient.prompt_ephemeral error: %s", e, exc_info=True)
            if self.on_status_message:
                await self.on_status_message(json.dumps({"code": "PROACTIVE_GEN_FAILED", "details": {"error_type": type(e).__name__, "error": str(e)}}))
            assistant_message = ""
            return False
        finally:
            self._is_responding = False
            # Token usage 由 _AsyncStreamWrapper hook 在流结束时自动记录，
            # 此处不再手动调用 TokenTracker.record() 避免双重计数。
            if assistant_message:
                self._conversation_history.append(AIMessage(content=assistant_message))
            # Use lightweight proactive-done callback (TTS flush + turn_end only),
            # falling back to full on_response_done for backward compatibility.
            done_cb = getattr(self, "on_proactive_done", None) or self.on_response_done
            if done_cb:
                await done_cb()

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
        if self.llm:
            try:
                await self.llm.aclose()
            except Exception as e:
                logger.warning(f"OmniOfflineClient.close: aclose failed: {e}")
            self.llm = None
        logger.info("OmniOfflineClient closed")
