# -- coding: utf-8 --
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

from ._shared import (
    Any,
    Awaitable,
    Callable,
    DIALOG_LLM_STREAM_TIMEOUT_SECONDS,
    Dict,
    List,
    OMNI_RECENT_RESPONSES_MAX,
    OnToolCallCallback,
    Optional,
    ToolDefinition,
    _UNLIMITED_BUDGET,
    _budget_to_max_tokens,
    asyncio,
    create_chat_llm,
)

from ._genai_support import (
    _should_use_genai_sdk,
)

from ._tools import _ToolingMixin
from ._genai_support import _GenaiMixin
from ._streaming import _StreamingMixin
from ._media import _MediaMixin
from ._lifecycle import _LifecycleMixin


class OmniOfflineClient(_ToolingMixin, _GenaiMixin, _StreamingMixin, _MediaMixin, _LifecycleMixin):
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
        provider_type: str | None = None,
        vision_provider_type: str | None = None,
        voice: str = "",  # Unused for text mode but kept for compatibility
        turn_detection_mode = None,  # Unused for text mode
        on_text_delta: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_thinking_active: Optional[Callable[[bool], Awaitable[None]]] = None,
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
        master_name: str = "",
        on_tool_call: Optional[OnToolCallCallback] = None,
        tool_definitions: Optional[List[ToolDefinition]] = None,
        max_tool_iterations: int = 3,
        enable_long_response_summary: bool = False,
        user_language_provider: Optional[Callable[[], Optional[str]]] = None,
    ):
        # Use base_url directly without conversion
        self.base_url = base_url
        self._user_language_provider = user_language_provider
        self.api_key = api_key if api_key and api_key != '' else None
        self.model = model
        self.vision_model = vision_model  # Store vision model for temporary switching
        # 视觉模型独立配置（如果未指定则回退到主配置）
        self.vision_base_url = vision_base_url if vision_base_url else base_url
        self.vision_api_key = vision_api_key if vision_api_key else api_key
        self.provider_type = provider_type
        self.vision_provider_type = vision_provider_type or provider_type
        self._model_switch_lock = asyncio.Lock()
        self.on_text_delta = on_text_delta
        # Called with True the first time a stream emits a reasoning / thinking
        # chunk (the text itself is filtered out before it reaches text/TTS —
        # only the "is thinking" boolean is surfaced), and with False to clear
        # when that stream ends without an external unconditional clear (see
        # _notify_reasoning_done). Drives the chat thinking-dots bubble for ANY
        # turn that actually reasons, decoupled from Focus mode.
        self.on_thinking_active = on_thinking_active
        # Reasoning-pulse ownership. A proactive prompt_ephemeral turn can
        # interleave with a user stream_text on this SAME client (core drops stale
        # proactive chunks via the expected-sid guard rather than awaiting the
        # prompt). Ownership is tracked by a single source of truth — NOT a shared
        # boolean, which _begin_reasoning_stream would reset out from under an
        # older still-running stream (Codex P2):
        #   _reasoning_stream_seq      — monotonic counter, bumped per stream entry
        #                                (stream_text / prompt_ephemeral) so each
        #                                stream has a distinct token.
        #   _reasoning_active_pulse_seq — the seq that currently owns an un-cleared
        #                                True pulse (None = bubble not lit by us).
        #                                A pulse stamps it; a clear only fires for
        #                                the owning seq. Because it is NOT reset on
        #                                stream entry, a preempted proactive turn
        #                                still clears its OWN pulse, yet cannot
        #                                clear a newer stream that already re-pulsed.
        self._reasoning_stream_seq = 0
        self._reasoning_active_pulse_seq: Optional[int] = None
        self.on_input_transcript = on_input_transcript
        self.on_output_transcript = on_output_transcript
        self.handle_connection_error = on_connection_error
        self.on_status_message = on_status_message
        self.on_response_done = on_response_done
        self.on_proactive_done: Optional[Callable[[bool], Awaitable[None]]] = None
        self.on_repetition_detected = on_repetition_detected
        self.on_response_discarded = on_response_discarded

        # 普通对话守卫配置（先决定 max_response_length，create_chat_llm
        # 用得到 _budget_to_max_tokens(self.max_response_length)）。
        # 0 / 负数 在 update_max_response_length 路径里被解释成"无限制"
        # （= _UNLIMITED_BUDGET）；__init__ 必须用同样的语义，否则首轮
        # 持久化配置直接读到 0 时会先按 300+20 cap 创建 LLM，直到用户再
        # 改一次滑块才恢复 unlimited。
        self.enable_response_guard = True
        if not isinstance(max_response_length, int):
            self.max_response_length = 300
        elif max_response_length > 0:
            self.max_response_length = max_response_length
        else:
            self.max_response_length = _UNLIMITED_BUDGET
        # 最多允许的自动重 roll 次数：1 次 reroll → 总共 2 次尝试。
        # 第 2 次仍超长时不再丢弃整段，而是回退到最后一个句末标点截断。
        self.max_response_rerolls = 1

        # 长回复 summary 路径开关：开 → 长但可读的回复不再 inline abort+truncate，
        # 而是让模型继续写到 budget+slack（最少 _SUMMARY_API_BUDGET_FLOOR），TTS
        # 在 budget 后的下个 terminator 停掉，emotion-tier 小模型用人设口吻把
        # 尾巴压成一两句话续到 TTS。前端 live 看完整原文、history 存 prefix+summary，
        # 刻意的语义分叉。默认 False（保留 game 这种 max=100 短台词的现行 abort
        # 行为），core 在创建 chat client 时显式打开。
        self.enable_long_response_summary = bool(enable_long_response_summary)

        # Initialize ChatOpenAI client. max_completion_tokens 设为
        # max_response_length + 20 让 LLM API 自然在 budget+20 token 处停下来，
        # 既省掉无效生成成本，又给 fence 留 20 token slack 看到 overshoot
        # 能区分 truncate / gibberish-filter 路径。
        # ⚠️ 这里**永远**用普通 budget，不烤进 summary 的 3000 floor —— 因为
        # 同一个 self.llm 既给 stream_text 又给 prompt_ephemeral（proactive）用，
        # 把 3000 烤进 client 会让没有长度 guard 的 proactive 轮次也能吐到 3000
        # token。summary 的 budget 抬升改成 stream_text 内临时 bump + finally
        # 还原（见 stream_text 顶部），把 3000 严格限定在长回复流式路径里。
        self.llm = create_chat_llm(
            self.model, self.base_url, self.api_key,
            streaming=True, max_retries=0,
            max_completion_tokens=_budget_to_max_tokens(self.max_response_length),
            timeout=DIALOG_LLM_STREAM_TIMEOUT_SECONDS,  # hang-guard; generous so normal/long replies aren't truncated
            provider_type=self.provider_type,
        )

        # ── Tool calling state ────────────────────────────────────────
        # ``tool_definitions`` is the canonical list (ToolDefinition objects);
        # the wire-format snapshots are rebuilt from it on each request so
        # callers can mutate the list (register/unregister) between turns.
        self.on_tool_call: Optional[OnToolCallCallback] = on_tool_call
        self._tool_definitions: List[ToolDefinition] = list(tool_definitions or [])
        self.max_tool_iterations = max(1, int(max_tool_iterations))
        self._use_genai_sdk = _should_use_genai_sdk(self.model, self.base_url)
        self._genai_client = None  # initialized lazily inside _stream_text_genai
        self._genai_tools_unsupported = False  # set True if genai path falls back at runtime

        # State management
        self._is_responding = False
        self._conversation_history = []
        self._instructions = ""
        self._stream_task = None
        self._pending_images = []  # Store pending images to send with next text
        # 主动搭话以「屏幕」为素材投递后遗留的那张截图，待下一条用户 text 回复
        # 时作为前导视觉背景注入（让对话模型「看到」刚才搭话评论的屏幕）。刻意
        # 与 _pending_images（用户自己的下一帧）隔离：共用会偷走用户的待发帧，
        # 见 core.py proactive media 注释（Codex P2）。单张、一次性消费、带 TTL
        # （_proactive_image_staged_at = 暂存时刻的 monotonic 秒，0.0 = 无暂存）。
        # _proactive_image_history_len = 暂存那一刻的历史长度：截图只对「紧接它的
        # 下一条用户回复」有效，若中途又来了别的 AI 轮（greeting / agent 回调走
        # prompt_ephemeral，不过 finish_proactive_delivery）使历史变长，这张就过时
        # 了、注入时丢弃——把截图钉死在「最后一条 AI 轮」上（Codex P2）。
        self._proactive_image_to_inject = None
        self._proactive_image_staged_at = 0.0
        self._proactive_image_history_len = 0

        # ── Empty-completion 诊断（finish_reason / prompt_tokens / block_reason）──
        # Gemini 在 SAFETY / RECITATION / MAX_TOKENS 等场景会返回 finish_reason
        # 非 stop 但 content 为空；走 OpenAI-compat 代理时 HTTP 仍是 200，没异常，
        # 上层只能看到"流跑完了，0 个文本 token"。这里记最后一次 attempt 在 LLM
        # 这一层看到的 finish_reason / block_reason / prompt_tokens，让 stream_text
        # 的 "所有重试均未产生文本回复" / prompt_ephemeral 的 delivered=False 兜底
        # 警告能把"为什么 empty"原样吐出来。
        self._last_finish_reason: Optional[str] = None
        self._last_block_reason: Optional[str] = None
        self._last_prompt_tokens: Optional[int] = None

        # 重复度检测
        self._recent_responses = []  # 存储最近3轮助手回复
        self._repetition_threshold = 0.8  # 相似度阈值
        self._max_recent_responses = OMNI_RECENT_RESPONSES_MAX  # 最多存储的回复数

        # ========== 输出前缀检测 ==========
        self.lanlan_name = lanlan_name
        self.master_name = master_name
        self._prefix_buffer_size = max(len(lanlan_name), len(master_name)) + 3 if (lanlan_name or master_name) else 0
