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
    AIMessage,
    Any,
    Awaitable,
    Callable,
    DIALOG_LLM_STREAM_TIMEOUT_SECONDS,
    Dict,
    FOCUS_THINKING_EXTRA_TOKENS,
    HumanMessage,
    Optional,
    SystemMessage,
    ThinkingStreamStripper,
    _PROACTIVE_SCREENSHOT_TTL_SECONDS,
    _SENTENCE_END_CHARS,
    _SUMMARY_GIBBERISH_RECHECK_TOKENS,
    _SUMMARY_HARD_TOKEN_CAP,
    _SUMMARY_LATE_FINISH_SLACK,
    _UNLIMITED_BUDGET,
    _budget_to_max_tokens,
    _find_summary_terminator,
    _is_api_key_rejected_error,
    _is_gibberish_response,
    _is_safety_violation_signal,
    _llm_retry_error_types,
    _truncate_to_last_sentence_end,
    asyncio,
    calculate_text_similarity,
    count_tokens,
    create_chat_llm_async,
    json,
    logger,
    set_call_type,
    time,
    truncate_to_tokens,
)

from ._genai_support import (
    _should_use_genai_sdk,
)
from ._lifecycle import _with_dialog_slop

class _StreamingMixin:
    def update_max_response_length(self, max_length: int) -> None:
        """Update the response token cap (the user may change settings mid-conversation).
        Same unit as ``self.max_response_length``: tiktoken token count.
        Also refreshes ``self.llm.max_completion_tokens`` so the next astream
        request stops naturally at the new budget+20.

        ``0`` / negative values are both interpreted as "unlimited", matching the
        ``__init__`` semantics; an upper layer passing -1 as a cancel-the-cap signal
        also passes through correctly."""
        if isinstance(max_length, int):
            self.max_response_length = max_length if max_length > 0 else _UNLIMITED_BUDGET
            if self.llm is not None:
                # 普通 budget；summary 的 3000 抬升只在 stream_text 内临时生效。
                self.llm.max_completion_tokens = _budget_to_max_tokens(self.max_response_length)
            logger.debug(f"OmniOfflineClient: token 上限已更新为 {max_length}")

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
        lock = getattr(self, "_model_switch_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._model_switch_lock = lock

        async with lock:
            if not new_model or new_model == self.model:
                return

            logger.info(f"Switching model from {self.model} to {new_model}")

            # 选择使用的 API 配置
            if use_vision_config:
                base_url = self.vision_base_url
                api_key = self.vision_api_key if self.vision_api_key and self.vision_api_key != '' else None
                provider_type = self.vision_provider_type
            else:
                base_url = self.base_url
                api_key = self.api_key
                provider_type = self.provider_type

            # 先创建新 client，成功后再原子替换，避免半切换状态。
            # max_completion_tokens 跟随当前 max_response_length 同步设置
            # （和 __init__ 一致）。
            new_llm = await create_chat_llm_async(
                new_model, base_url, api_key,
                streaming=True, max_retries=0,
                # 普通 budget；summary 的 3000 抬升只在 stream_text 内临时生效。
                max_completion_tokens=_budget_to_max_tokens(self.max_response_length),
                timeout=DIALOG_LLM_STREAM_TIMEOUT_SECONDS,  # hang-guard; generous so normal/long replies aren't truncated
                provider_type=provider_type,
            )
            old_llm = self.llm
            self.llm = new_llm
            self.model = new_model
            # ⚠️ 同步 self.base_url / self.api_key —— 否则后续 _astream_with_tools
            # 重新计算 _use_genai_sdk 时拿到的还是旧 conversation 配置，会
            # 把 vision 走的 Gemini endpoint 错误路由到 OpenAI-compat（反之亦然）。
            self.base_url = base_url
            self.api_key = api_key
            # 路由旗标随之刷新；旧 _genai_client 抛弃（若 api_key 变了它已失效）。
            # genai.Client 内部持有 httpx 连接池——直接 = None 靠 GC 回收虽不
            # 是 leak，但提早 close() 能马上释放底层连接（SDK 没暴露 aclose，
            # close 是同步方法，放进 to_thread 不阻事件循环）。
            old_genai = self._genai_client
            self._use_genai_sdk = _should_use_genai_sdk(self.model, self.base_url)
            self._genai_client = None
            self._genai_tools_unsupported = False
            if old_genai is not None and hasattr(old_genai, "close"):
                try:
                    await asyncio.to_thread(old_genai.close)
                except Exception as _close_err:
                    logger.warning(
                        "switch_model: old genai client close failed: %s",
                        _close_err,
                    )
            try:
                await old_llm.aclose()
            except Exception as e:
                logger.warning(f"switch_model: old client aclose failed: {e}")

    async def _check_repetition(self, response: str) -> bool:
        """
        Check whether the reply is highly repetitive of recent replies.
        Returns True and triggers the callback if 3 consecutive turns are highly repetitive.
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
        Notify the upper layer that the current reply was discarded, so the frontend bubble can be cleared / the user informed
        """
        if self.on_response_discarded:
            try:
                await self.on_response_discarded(reason, attempt, max_attempts, will_retry, message)
            except Exception as e:
                logger.warning(f"通知 response_discarded 失败: {e}")

    async def _summarize_tail_for_tts(self, prefix: str, tail: str) -> Optional[str]:
        """Small-model call for the long-reply summary path.

        ``prefix`` is the part TTS has already played to the user (used as a context
        anchor so the summary flows naturally); ``tail`` is the part after the cutover
        that was never read out and needs compressing. Returns the 1-2 closing
        sentences written by the emotion-tier LLM, or ``None`` when config is missing /
        the call fails — the caller then falls back to "read the full original text".

        The prompt does not include the persona — prefix/tail were already written by
        the main model in the persona's voice, so the small model only needs to keep
        the existing tone and shorten the tail, not re-enact the persona. Language is
        detected from ``tail`` (the prefix may be very short; the tail is more
        informative).
        """
        if not (tail and tail.strip()):
            return None

        # emotion 配置在 config/api_providers.json 每个 provider 下都有
        # `emotion_model` 字段；config_manager 拿到的就是当前 provider 的
        # emotion 子配置（model/base_url/api_key）。
        try:
            from utils.config_manager import get_config_manager  # 延迟 import 防循环
            cfg_mgr = get_config_manager()
            emotion_config = cfg_mgr.get_model_api_config('emotion') if cfg_mgr else None
        except Exception as e:
            logger.warning("summary: 取 emotion 配置失败: %s", e)
            return None
        if not emotion_config:
            return None
        emotion_api_key = emotion_config.get('api_key')
        emotion_model = emotion_config.get('model')
        emotion_base_url = emotion_config.get('base_url')
        emotion_provider_type = emotion_config.get('provider_type')
        if not (emotion_api_key and emotion_model):
            logger.info("summary: emotion 模型/Key 未配置，跳过长回复摘要")
            return None

        try:
            from utils.language_utils import detect_language, normalize_language_code
            detected = detect_language(tail) or 'zh'
            lang = normalize_language_code(detected, format='short') or 'zh'
        except Exception:
            lang = 'zh'

        try:
            from config.prompts.prompts_response import get_long_response_tail_summary_prompts
            templates = get_long_response_tail_summary_prompts(lang)
        except Exception as e:
            logger.warning("summary: prompt 模板取失败: %s", e)
            return None

        # system 模板 persona-agnostic，无占位符直接用；user 模板只填 prefix/tail。
        system_text = templates['system']
        try:
            user_text = templates['user_template'].format(
                prefix=prefix or '',
                tail=tail,
            )
        except KeyError as e:
            logger.warning("summary: prompt 占位符缺失: %s", e)
            return None

        # 调用 token 用量打到 "long_response_summary" 类别下，与 emotion 区分。
        set_call_type("long_response_summary")
        messages = [
            SystemMessage(content=system_text),
            HumanMessage(content=user_text),
        ]
        # 不传 temperature：project policy 让 provider 默认值决定
        # （scripts/check_no_temperature.py 会守门）。emotion-tier 模型自带
        # 一个合适的 temperature，不需要 caller 干预。
        try:
            llm = await create_chat_llm_async(
                emotion_model, emotion_base_url, emotion_api_key,
                max_completion_tokens=120,
                timeout=30,
                provider_type=emotion_provider_type,
            )
        except Exception as e:
            logger.warning("summary: 构造 emotion LLM 失败: %s", e)
            return None

        try:
            async with llm:
                result = await llm.ainvoke(messages)
        except Exception as e:
            logger.warning("summary: emotion 模型调用失败: %s", e)
            return None

        summary = ""
        try:
            summary = (result.content or "").strip()
        except Exception:
            summary = ""
        # 兜底：去掉模型可能加的引号 / 元前缀。emotion-tier 模型偶尔仍会写
        # "总之，xxx" / "总结：xxx" 之类，prompt 已禁但模型不一定听话；这里
        # 只剥首尾的引号和最常见的元前缀，剩下的就当 character 自然口语。
        if summary and summary[0] in '“”"\'「『' and summary[-1] in '“”"\'」』':
            summary = summary[1:-1].strip()
        if not summary:
            return None
        # 硬性收口：emotion-tier 模型即使被 prompt 约束也可能输出 3-4 句话，
        # 直接灌进 TTS 会把"短促收尾"这个核心目标打废。最多保留 2 个 sentence-end
        # 段，再硬截到 ``_SUMMARY_HARD_TOKEN_CAP`` 个 token。两个限制叠加：先按
        # 句末切，再按 token 兜底——任意一条触发都收口。token 口径与 budget
        # 一致，跨语种行为统一（字符口径会过度惩罚 CJK）。
        sentence_segments: list[str] = []
        cursor = 0
        for idx, ch in enumerate(summary):
            if ch in _SENTENCE_END_CHARS:
                sentence_segments.append(summary[cursor:idx + 1])
                cursor = idx + 1
                if len(sentence_segments) >= 2:
                    break
        if sentence_segments:
            trimmed = "".join(sentence_segments)
            # 若模型在 2 句之外还塞了尾巴，丢弃
            summary = trimmed.strip()
        if count_tokens(summary) > _SUMMARY_HARD_TOKEN_CAP:
            summary = truncate_to_tokens(summary, _SUMMARY_HARD_TOKEN_CAP).rstrip()
        if not summary:
            return None
        return summary

    @staticmethod
    def _focus_stream_overrides(
        thinking_on: bool, model: str, base_max_tokens: int | None = None,
    ) -> dict:
        """Per-call streaming overrides for a Focus turn.

        When thinking-on, override extra_body with ``focus_extra_body(model)`` —
        the provider's thinking knob flipped to its ENABLED form (per provider
        dialect) while PRESERVING non-thinking provider extras (e.g. step-2-mini's
        built-in web_search), which a blunt ``extra_body=None`` would drop.
        Returns ``{}`` (instance default, thinking off) otherwise.

        Also bumps ``max_completion_tokens`` by ``FOCUS_THINKING_EXTRA_TOKENS``
        — but ONLY when this turn actually flips thinking ON for the provider:
        thinking models (Qwen / GLM / Kimi / Doubao / OpenRouter) bill reasoning
        tokens against the SAME budget as the visible reply, so without headroom
        the chain-of-thought squeezes the answer short. "Actually on" is detected
        by ``focus_extra_body(model) != get_extra_body(model)`` — when the focus
        form equals the regular (thinking-off) extra_body (Claude kept disabled,
        unknown models → None, non-thinking providers only preserving their
        non-thinking extras) there is no reasoning to reserve for, and a needless
        +800 could push a request past a model's output ceiling near the cap /
        summary floor. The bump is layered on ``base_max_tokens`` (the live
        instance ceiling, already reflecting summary-mode lift / vision-model
        switch), so it composes with both. ``base_max_tokens=None`` (unlimited
        budget) omits the field — the request stays uncapped. The Python-side
        length guard still caps the visible reply at ``max_response_length``;
        this only gives reasoning its own slack on the API side.

        Vision-model turns are included: Focus runs thinking-on regardless of
        whether the turn carries images. The inline streaming timeout
        (``DIALOG_LLM_STREAM_TIMEOUT_SECONDS``, 180s) is generous enough for a
        vision reasoning turn — unlike the short-windowed proactive Phase-2 path,
        which still keeps thinking off (its 16-25s window would time out).
        """
        if not thinking_on:
            return {}
        from config.providers import focus_extra_body, get_extra_body
        fb = focus_extra_body(model)
        overrides: dict = {"extra_body": fb}
        # Headroom only when Focus actually enables thinking for this provider:
        # ``fb is None`` ⇒ no thinking-enable override at all (unknown model);
        # ``fb == get_extra_body(model)`` ⇒ focus form equals the regular
        # (thinking-off) form (Claude kept disabled, non-thinking providers only
        # preserving their own extras). Either way there's no reasoning to
        # reserve for, and a needless +800 could push a request past a model's
        # output ceiling.
        if (
            base_max_tokens is not None
            and fb is not None
            and fb != get_extra_body(model)
        ):
            overrides["max_completion_tokens"] = base_max_tokens + FOCUS_THINKING_EXTRA_TOKENS
        return overrides

    @_with_dialog_slop
    async def stream_text(
        self,
        text: str,
        *,
        system_prefix: str | None = None,
        thinking_on: bool = False,
        input_transcript_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        history_replacement_text: str | None = None,
    ) -> None:
        """
        Send a text message to the API and stream the response.
        If there are pending images, temporarily switch to vision model for this turn.
        Uses langchain ChatOpenAI for streaming.

        ``thinking_on`` (Focus mode 凝神, docs/design/focus-truename-mode.md):
        when True, this single turn drops the auto-resolved thinking-off
        ``extra_body`` so the provider runs its default reasoning ("放飞自我").
        It is a per-call override (``extra_body=None`` threaded into
        ``astream``) — the session LLM is NOT rebuilt and the next regular
        turn falls straight back to thinking-off. Applies to the
        OpenAI-compat path (where the thinking-off knob lives); the native
        google-genai path is already thinking-capable by default, so the
        override is a no-op there.

        Purpose of ``system_prefix``: the caller (typically SessionManager rendering a
        passive agent callback into watermarked ``======[系统通知] xxx======`` text)
        splices this neutral system-notice text **in place, as a prefix to this turn's
        user message content** — the LLM treats it as "extra context attached at the
        moment the user spoke" and mentions it naturally within the same turn, without
        starting a separate turn or a separate SystemMessage.

        Symmetry with voice mode: ``OmniRealtimeClient.prime_context(skipped=False)``
        on GPT/GLM/Step likewise goes through ``create_response`` to inject the
        callback as a user-role message and trigger a response. Inlining into user
        content means accepting that the callback text is persisted into
        ``_conversation_history`` along with the user message (consistent with the
        voice side's user-role injection semantics).

        ``input_transcript_callback`` lets a caller bind the transcript recording
        callback to this request. This is used when the frontend sends a long prompt
        but wants memory/history to record a concise user-facing summary.

        ``history_replacement_text`` keeps the full prompt available for the current
        LLM turn, then replaces the just-appended user history entry before the next
        turn reuses ``_conversation_history``.
        """  # noqa: DOCSTRING_CJK
        if not text or not text.strip():
            # If only images without text, use a default prompt
            if self._pending_images:
                text = "请分析这些图片。"
            else:
                return

        # Fresh stream: open a new reasoning-pulse scope (bump the ownership seq)
        # so this turn's first reasoning chunk re-pulses the bubble. stream_text
        # does not clear via _notify_reasoning_done — core's inline finally clears
        # unconditionally — so it only needs the bump, not an owner token.
        self._begin_reasoning_stream()

        # Check if we need to switch to vision model. A staged proactive-vision
        # screenshot (the screen she just commented on) counts as an image too,
        # so a text-only user reply still goes multi-modal and the model sees it.
        # The staged screenshot is dropped (not injected) when either:
        #  - TTL: older than _PROACTIVE_SCREENSHOT_TTL_SECONDS (the screen has moved
        #    on — a stale frame would mislead more than help); or
        #  - superseded: a later AI turn was appended after staging (e.g. a
        #    greeting / agent callback via prompt_ephemeral), so this reply isn't
        #    answering the screen-based talk anymore. History only grows by
        #    appends between staging and this read (the user hasn't been appended
        #    yet), so a length change means an intervening AI turn (Codex P2).
        proactive_image = self._proactive_image_to_inject
        if proactive_image:
            _expired = (
                time.monotonic() - self._proactive_image_staged_at
                > _PROACTIVE_SCREENSHOT_TTL_SECONDS
            )
            _superseded = len(self._conversation_history) != self._proactive_image_history_len
            if _expired or _superseded:
                logger.info(
                    "Proactive screenshot dropped (expired=%s superseded=%s)",
                    _expired, _superseded,
                )
                self._proactive_image_to_inject = None
                self._proactive_image_staged_at = 0.0
                self._proactive_image_history_len = 0
                proactive_image = None
        has_images = bool(proactive_image) or len(self._pending_images) > 0
        # 就地植入 system_prefix：拼到 user content 的 text 段前缀（watermark
        # 自带，不补 separator 也能区分）。callback 文本随 HumanMessage 一起
        # 落 history，跟 voice mode user-role 注入对偶。
        _user_text = text.strip()
        _prefix_clean = (system_prefix or "").strip()
        _user_text_with_prefix = (
            f"{_prefix_clean}\n\n{_user_text}" if _prefix_clean else _user_text
        )

        # Prepare user message content
        if has_images:
            # Switch to vision model permanently for this session
            # (cannot switch back because image data remains in conversation history)
            if self.vision_model and self.vision_model != self.model:
                logger.info(f"🖼️ Temporarily switching to vision model: {self.vision_model} (from {self.model})")
                await self.switch_model(self.vision_model, use_vision_config=True)

            # Multi-modal message: images + text
            content = []

            # Add images first. Temporal order: the proactive screenshot (the
            # screen she commented on, BEFORE the user spoke) leads, then the
            # user's own pending frame(s) — so the model doesn't mistake the
            # earlier screen for what the user just captured.
            if proactive_image:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{proactive_image}"
                    }
                })
            for img_b64 in self._pending_images:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_b64}"
                    }
                })

            # Add text（已含 system_prefix watermark 前缀，若有）
            content.append({
                "type": "text",
                "text": _user_text_with_prefix,
            })

            user_message = HumanMessage(content=content)
            _img_count = len(self._pending_images) + (1 if proactive_image else 0)
            logger.info(
                f"Sending multi-modal message with {_img_count} image(s)"
                f"{' (incl. proactive screen)' if proactive_image else ''}"
            )

            # Clear pending images after using them (content already holds the
            # data urls). The proactive screenshot is one-shot: consumed by this
            # reply, then cleared so it never re-injects into later turns.
            self._pending_images.clear()
            self._proactive_image_to_inject = None
            self._proactive_image_staged_at = 0.0
            self._proactive_image_history_len = 0
        else:
            # Text-only message（已含 system_prefix watermark 前缀，若有）
            user_message = HumanMessage(content=_user_text_with_prefix)

        self._conversation_history.append(user_message)
        history_replacement_index = len(self._conversation_history) - 1
        history_replacement_text = (
            str(history_replacement_text).strip()
            if history_replacement_text is not None
            else ""
        )
        if history_replacement_text and _prefix_clean:
            history_replacement_text = f"{_prefix_clean}\n\n{history_replacement_text}"
        if has_images:
            self._evict_old_images()

        # Callback for user input
        transcript_callback = input_transcript_callback or self.on_input_transcript
        if transcript_callback:
            await transcript_callback(text.strip())

        # Retry策略：重试2次，间隔1秒、2秒
        max_retries = 3
        retry_delays = [1, 2]
        assistant_message = ""        # 仅最后一段未持久化的 text（final-segment）
        assistant_message_total = ""  # 整轮累计（含 pre-tool），整轮级判定看它
        status_reported = False
        guard_exhausted = False
        # Empty-completion 诊断字段重置：每轮 turn 独立，否则会读到上一轮的旧值。
        self._last_finish_reason = None
        self._last_block_reason = None
        self._last_prompt_tokens = None

        # 单测会用 __new__ 绕过 __init__ 直接 mock OmniOfflineClient，此时
        # ``enable_long_response_summary`` 属性根本不存在。这里取一次本地
        # snapshot 避免后面每一处都 getattr，也防止运行中外部改属性导致
        # state machine 半生效。
        summary_mode_enabled = bool(getattr(self, "enable_long_response_summary", False))

        # summary 模式临时把 API budget 抬到 _SUMMARY_API_BUDGET_FLOOR：让模型
        # 有空间把话写完，cutover 之后的尾巴才有东西可摘要（普通 budget 下模型
        # 在 ~budget 处就停了，没有尾巴）。**只在 stream_text 内 bump**，finally
        # 还原，避免泄漏到共用同一 self.llm 的 prompt_ephemeral（proactive 没有
        # 长度 guard，被抬到 3000 会吐超长回复）。snapshot 原值精确还原，兼容
        # 运行中 update_max_response_length 改过 budget 的场景。
        _summary_prev_max_tokens = None
        if summary_mode_enabled and getattr(self, "llm", None) is not None:
            _summary_prev_max_tokens = self.llm.max_completion_tokens
            self.llm.max_completion_tokens = _budget_to_max_tokens(
                self.max_response_length, summary_mode=True,
            )

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
                # close() 是唯一会把 self.llm 设为 None 的路径。它若在前一次
                # APIConnectionError 后的 retry sleep 期间触发（用户切模式 /
                # 断连 / session 熔断），就不再重试 —— 否则下面的 reroll while
                # 会先把 _is_responding 重置回 True 然后对 None 调 .astream，
                # 触发 NoneType.astream AttributeError。
                # 同样若 cancel_response() / handle_interruption() 在 retry sleep
                # 期间把 _is_responding 翻成 False（用户主动打断），也不该再
                # 启动新一轮 attempt —— reroll while 会无条件 reset 回 True，
                # 默默吞掉用户的取消意图。和 prompt_ephemeral 的守卫保持一致。
                # 用 hasattr 守卫：单元测试用 __new__ 绕过 __init__ 不会设这个
                # 属性，但真实代码 __init__ 必设；区分"未初始化（测试桩）"和
                # "已关闭（生产）"两种情况。
                if (hasattr(self, "llm") and self.llm is None) or not self._is_responding:
                    logger.info("OmniOfflineClient.stream_text: client 已 close 或响应已被取消，终止 retry")
                    # 标记 status_reported 抑制 finally 的 LLM_NO_RESPONSE 兜底：
                    # 这是用户主动 cancel / close，不是 LLM 故障，前端不该看到
                    # 红条错误。这里没有 status 要发，仅占位让 finally 跳过。
                    status_reported = True
                    break
                try:
                    assistant_message = ""
                    assistant_message_total = ""
                    guard_attempt = 0
                    # Telemetry：TTFT（首 token 延迟）。从这里到第一个 chunk 到达
                    # 的时长，D1 流失里"响应太慢"的关键信号。只记一次（首 attempt
                    # 首 chunk）；reroll 不重置，反映用户真实等待体感。
                    _ttft_start = time.time()
                    _ttft_recorded = False
                    while guard_attempt <= self.max_response_rerolls:
                        self._is_responding = True
                        assistant_message = ""           # 仅最后一段未持久化的 text，用于 final AIMessage append
                        assistant_message_total = ""     # 全轮累积，用于 _check_repetition / 长度 guard
                        is_first_chunk = True
                        pipe_count = 0  # 围栏：追踪 | 字符的出现次数
                        fence_triggered = False  # 围栏是否已触发
                        guard_triggered = False
                        discard_reason = None
                        length_guard_recovery_text = ""
                        length_guard_persisted_prefix = ""
                        length_guard_original_tokens = 0
                        chunk_usage = None
                        prefix_buffer = ""
                        prefix_checked = not bool(self._prefix_buffer_size)

                        # ── Summary-mode 状态机（仅 summary_mode_enabled 时生效）──
                        # 完整流转图，便于把散在 4 处（这里初始化 / 长度 trigger /
                        # emit fork / 流末 epilogue）的逻辑拼成一张图：
                        #
                        #   idle ──(_total 越过 budget)──▶ pending_cutover
                        #     │                                 │
                        #     │                  (找到 budget 之后的 terminator)
                        #     │                                 ▼
                        #     │                           cutover_done
                        #     │                          /     │      \
                        #     │           (tail 攒到乱码)/      │       \(stream 结束)
                        #     │                       /        │        \
                        #     │           gibberish_fallback   │   epilogue 决策：
                        #     │           (静默截断，          │   · final<budget+slack → abandon，tail 续 TTS
                        #     │            history 只留 prefix) │   · 否则 → 调小模型摘要续 TTS（失败则 abandon）
                        #     │                                 │
                        #     └─(stream 结束仍 idle / pending)──┴─▶ 常规：完整原文进 history
                        #
                        # 各状态的 emit 去向：
                        #   idle / pending_cutover  → UI + TTS（both）
                        #   cutover_done            → 仅 UI（tail 攒进 summary_tail_buffer）
                        # tool_round_persisted 触发时：先把 cutover 后的 tail abandon 给
                        # TTS（否则永远听不到），再把整套状态重置回 idle。
                        summary_state = 'idle'
                        summary_prefix_for_history = ""  # cutover 触发那一刻 assistant_message 的快照
                        summary_tail_buffer = ""        # post-cutover UI-only 文本
                        summary_next_gibberish_check = _SUMMARY_GIBBERISH_RECHECK_TOKENS
                        summary_trigger_tokens = 0     # 触发 summary 时的 _total（仅日志）
                        # 越界点 char offset：模型一口气 yield 一个超大 chunk 时
                        # （多个 sentence/clause），budget 之前的 terminator 不算数 ——
                        # 应该从这个点开始找 terminator。trigger chunk 之后立即
                        # 消费回 0，下一 chunk 从头扫（因为它整段已经在 budget 之后）。
                        summary_overflow_offset = 0

                        def _has_unpersisted_recovery_suffix(recovery_text: str) -> bool:
                            if not recovery_text:
                                return False
                            if not length_guard_persisted_prefix:
                                return True
                            if not recovery_text.startswith(length_guard_persisted_prefix):
                                return False
                            return bool(recovery_text[len(length_guard_persisted_prefix):].strip())

                        # Tool-aware streaming: ``_astream_with_tools`` runs
                        # the multi-turn tool loop inside (executing tools and
                        # appending results to ``_conversation_history`` IN
                        # PLACE). The yielded chunks are exactly the same
                        # shape as raw ``self.llm.astream``, so the existing
                        # prefix/fence/length-guard logic below is untouched.
                        # Focus 凝神: thinking_on threads ``extra_body=None``
                        # down to ``astream`` (per-call override) so this turn
                        # reasons freely; regular turns pass nothing → the
                        # instance's thinking-off extra_body applies. Routed
                        # through the visible (tool-leak-filtered) variant,
                        # which forwards **overrides to ``_astream_with_tools``.
                        # Also threads a ``max_completion_tokens`` bump
                        # (+FOCUS_THINKING_EXTRA_TOKENS) so reasoning gets its
                        # own headroom instead of eating the reply's budget;
                        # base is the live instance ceiling (already reflects
                        # summary-mode lift / vision switch), ``None`` stays
                        # uncapped.
                        _focus_overrides = self._focus_stream_overrides(
                            thinking_on, self.model,
                            base_max_tokens=self.llm.max_completion_tokens if self.llm else None,
                        )
                        # Focus 凝神: leak-prone models (qwen3.5/3.6/3.7 hybrids)
                        # stream their chain-of-thought into ``content`` ending in
                        # a lone ``</think>``; hold + drop it before TTS/UI ever
                        # see it. Clean providers (reasoning_content path) get no
                        # stripper, so their streaming stays byte-for-byte untouched.
                        # Gate on the SAME condition as _focus_stream_overrides
                        # (just ``thinking_on`` — vision turns now reason too).
                        from config.providers import leaks_thinking_in_content
                        think_stripper = (
                            ThinkingStreamStripper()
                            if thinking_on and leaks_thinking_in_content(self.model)
                            else None
                        )
                        async for chunk in self._astream_visible_with_tools(
                            self._conversation_history, **_focus_overrides,
                        ):
                            if not _ttft_recorded:
                                _ttft_recorded = True
                                try:
                                    from utils.instrument import histogram as _instr_h
                                    _instr_h("llm_ttft_ms", max(0.0, (time.time() - _ttft_start) * 1000.0))
                                except Exception:
                                    # 埋点 best-effort，绝不打断流式响应主路径。
                                    pass
                            if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                                chunk_usage = chunk.usage_metadata
                                logger.debug(f"🔍 [Usage] {chunk_usage}")
                            if hasattr(chunk, 'response_metadata') and chunk.response_metadata:
                                if 'token_usage' in chunk.response_metadata or 'usage' in chunk.response_metadata:
                                    logger.debug(f"🔍 [Meta] {chunk.response_metadata}")
                            # tool 轮 sentinel：``_astream_*_with_tools`` 已把
                            # pre-tool 文本 + tool_calls + tool result inline
                            # 写进 history。重置 final-segment buffer 防止
                            # 之后 append 的 AIMessage 把同一段 pre-tool 文本
                            # 第二次写进 history。``_total`` 不重置——重复检测
                            # / token 长度 guard 仍要看完整一轮的实际文本量。
                            if getattr(chunk, "tool_round_persisted", False):
                                length_guard_persisted_prefix = assistant_message_total
                                assistant_message = ""
                                # 重置围栏 / prefix buffer：下一段是新的语义
                                # 单元（模型基于 tool 结果重新出文本），不应
                                # 复用之前的 fence / prefix 状态。
                                pipe_count = 0
                                prefix_buffer = ""
                                prefix_checked = not bool(self._prefix_buffer_size)
                                if think_stripper is not None:
                                    # Flush before reset: if this pre-tool segment
                                    # never emitted </think>, the stripper is still
                                    # holding real answer text it withheld from
                                    # TTS/UI. The inner generator already persisted
                                    # that text to history (stripped), so emit it to
                                    # TTS/UI only here — dropping it (a bare reset)
                                    # would lose the pre-tool sentence. Then re-arm
                                    # for the post-tool segment (new semantic unit).
                                    _pretool_residual = think_stripper.flush()
                                    if _pretool_residual and _pretool_residual.strip() and self.on_text_delta:
                                        await self.on_text_delta(_pretool_residual, is_first_chunk)
                                        is_first_chunk = False
                                    think_stripper.reset()
                                # Summary 状态收尾：cutover 之后的 tail 已经 UI-only
                                # 发出去了，但 TTS 还没听到。tool 边界处不知道
                                # post-tool 段会有多长，没法走"final < max+slack"
                                # 那套判断，所以一律 abandon —— 把 tail 续给 TTS
                                # 当原文读完。`_astream_*_with_tools` 已经把含
                                # tail 的完整 pre-tool 文本写进 assistant.tool_calls.content
                                # 持久化到 _conversation_history，UI/TTS/history 这下
                                # 三家口径一致。然后再重置 state 让 post-tool 重新
                                # 走 idle 起点。
                                if (
                                    summary_mode_enabled
                                    and summary_state == 'cutover_done'
                                    and summary_tail_buffer
                                ):
                                    logger.info(
                                        "OmniOfflineClient summary: tool 边界 abandon "
                                        "(pre-tool tail %d chars 续给 TTS)",
                                        len(summary_tail_buffer),
                                    )
                                    if self.on_text_delta:
                                        await self.on_text_delta(
                                            summary_tail_buffer, False,
                                            ui_enabled=False, tts_enabled=True,
                                        )
                                summary_state = 'idle'
                                summary_prefix_for_history = ""
                                summary_tail_buffer = ""
                                summary_next_gibberish_check = _SUMMARY_GIBBERISH_RECHECK_TOKENS
                                summary_trigger_tokens = 0
                                summary_overflow_offset = 0
                                continue
                            if not self._is_responding:
                                break

                            if fence_triggered:
                                break

                            content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                            if think_stripper is not None and content:
                                # Holds CoT until the first </think>; returns "" while
                                # buffering so the empty-content guard below skips it.
                                content = think_stripper.feed(content)

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
                                    emit_content = truncated_content
                                    if self.enable_response_guard:
                                        # 长度 guard 看完整一轮（含 pre-tool）的 token 量。
                                        # 必须在 on_text_delta 前裁剪本 chunk，否则 UI/TTS
                                        # 会先收到超限尾巴，而 history 只保存截断文本。
                                        candidate_total = assistant_message_total + truncated_content
                                        current_length = count_tokens(candidate_total)
                                        if current_length > self.max_response_length:
                                            if summary_mode_enabled:
                                                # Summary 路径：长但可读 → 不 abort、不 inline truncate。
                                                # 第一次过线把 state 切到 pending_cutover；从这 chunk 起
                                                # 在每个 chunk 里找 terminator（含逗号），找到就走 cutover。
                                                # 不设 guard_triggered，让 stream 继续到自然终止/3000 cap，
                                                # 由 stream 结束后的 epilogue 决策 abandon / summarize。
                                                if summary_state == 'idle':
                                                    summary_state = 'pending_cutover'
                                                    summary_trigger_tokens = current_length
                                                    # 算 chunk 里"刚好越过 budget"的 char offset：
                                                    # truncate_to_tokens 把 candidate_total 砍到 budget，
                                                    # 差出来的长度就是这一 chunk 里的越线位置。供下面
                                                    # emit fork 从该 offset 起找 terminator，避免误把
                                                    # budget 之前的早期逗号当 cutover。
                                                    _capped = truncate_to_tokens(
                                                        candidate_total, self.max_response_length,
                                                    )
                                                    summary_overflow_offset = max(
                                                        0, len(_capped) - len(assistant_message_total),
                                                    )
                                                    logger.info(
                                                        "OmniOfflineClient summary: 长回复触发 "
                                                        "(%d tokens > %d，chunk 内越界 offset=%d)，"
                                                        "等待下一个 terminator",
                                                        current_length, self.max_response_length,
                                                        summary_overflow_offset,
                                                    )
                                                # emit_content 保持原 chunk，下面 emit-split 块继续走
                                            else:
                                                guard_triggered = True
                                                discard_reason = f"length>{self.max_response_length}"
                                                length_guard_original_tokens = current_length
                                                logger.info(f"OmniOfflineClient: 检测到长回复 ({current_length} tokens)，准备停止生成")
                                                self._is_responding = False
                                                emit_content = ""
                                                if not _is_gibberish_response(candidate_total):
                                                    capped = truncate_to_tokens(
                                                        candidate_total, self.max_response_length,
                                                    )
                                                    candidate_recovery = _truncate_to_last_sentence_end(capped)
                                                    if candidate_recovery:
                                                        if candidate_recovery.startswith(assistant_message_total):
                                                            recovery_suffix = candidate_recovery[len(assistant_message_total):]
                                                            if recovery_suffix.strip():
                                                                emit_content = recovery_suffix
                                                                length_guard_recovery_text = candidate_recovery
                                                        elif (
                                                            assistant_message_total
                                                            and _has_unpersisted_recovery_suffix(assistant_message_total)
                                                        ):
                                                            # 已流式发出的前缀无法撤回；保持 history 与
                                                            # UI/TTS 一致，避免可见文本和上下文分叉。
                                                            length_guard_recovery_text = assistant_message_total

                                    if emit_content and emit_content.strip():
                                        # Emit fork：summary 模式下要按 cutover 边界拆分
                                        # UI / TTS 路径。其余场景（含 summary_state == 'idle'
                                        # 与 'gibberish_fallback'）都走 both 默认路径。
                                        if (
                                            summary_mode_enabled
                                            and summary_state in ('pending_cutover', 'cutover_done')
                                        ):
                                            if summary_state == 'pending_cutover':
                                                # Trigger chunk 上 offset > 0 表示越界点在 chunk 中段，
                                                # terminator 搜索从越界点开始；后续 chunk 整段都在
                                                # budget 之后，offset 复位到 0 从头扫。一次性消费。
                                                search_from = summary_overflow_offset
                                                summary_overflow_offset = 0
                                                term_pos_in_slice = _find_summary_terminator(
                                                    emit_content[search_from:]
                                                )
                                                if term_pos_in_slice >= 0:
                                                    term_pos = search_from + term_pos_in_slice
                                                    pre = emit_content[:term_pos + 1]
                                                    post = emit_content[term_pos + 1:]
                                                    if pre:
                                                        assistant_message += pre
                                                        assistant_message_total += pre
                                                        if self.on_text_delta:
                                                            await self.on_text_delta(pre, is_first_chunk)
                                                        is_first_chunk = False
                                                    # 锁定 cutover：当前 assistant_message 即 prefix
                                                    summary_prefix_for_history = assistant_message
                                                    summary_state = 'cutover_done'
                                                    logger.info(
                                                        "OmniOfflineClient summary: cutover 完成 "
                                                        "(prefix_chars=%d, trigger=%d tokens)",
                                                        len(assistant_message), summary_trigger_tokens,
                                                    )
                                                    if post:
                                                        assistant_message += post
                                                        assistant_message_total += post
                                                        summary_tail_buffer += post
                                                        if self.on_text_delta:
                                                            await self.on_text_delta(
                                                                post, is_first_chunk,
                                                                ui_enabled=True, tts_enabled=False,
                                                            )
                                                        is_first_chunk = False
                                                else:
                                                    # 没找到 terminator → 整段走 both，state 不变
                                                    assistant_message += emit_content
                                                    assistant_message_total += emit_content
                                                    if self.on_text_delta:
                                                        await self.on_text_delta(emit_content, is_first_chunk)
                                                    is_first_chunk = False
                                            else:
                                                # cutover_done：UI only，并攒进 tail buffer
                                                assistant_message += emit_content
                                                assistant_message_total += emit_content
                                                summary_tail_buffer += emit_content
                                                if self.on_text_delta:
                                                    await self.on_text_delta(
                                                        emit_content, is_first_chunk,
                                                        ui_enabled=True, tts_enabled=False,
                                                    )
                                                is_first_chunk = False

                                            # cutover_done 后做一次 gibberish 重检（pending→done
                                            # 同 chunk 转换也算）：每 _SUMMARY_GIBBERISH_RECHECK_TOKENS
                                            # tail token 重检一次，命中就跳到 fallback 让 epilogue 走
                                            # RESPONSE_INVALID。
                                            if summary_state == 'cutover_done' and summary_tail_buffer:
                                                tail_tokens = count_tokens(summary_tail_buffer)
                                                if tail_tokens >= summary_next_gibberish_check:
                                                    if _is_gibberish_response(summary_tail_buffer):
                                                        summary_state = 'gibberish_fallback'
                                                        logger.warning(
                                                            "OmniOfflineClient summary: tail gibberish "
                                                            "命中 (%d tokens)，中止本轮生成",
                                                            tail_tokens,
                                                        )
                                                        self._is_responding = False
                                                    else:
                                                        summary_next_gibberish_check = (
                                                            tail_tokens + _SUMMARY_GIBBERISH_RECHECK_TOKENS
                                                        )
                                        else:
                                            assistant_message += emit_content
                                            assistant_message_total += emit_content
                                            if self.on_text_delta:
                                                await self.on_text_delta(emit_content, is_first_chunk)
                                            is_first_chunk = False

                                    if guard_triggered:
                                        break
                                    if summary_state == 'gibberish_fallback':
                                        # 不设 guard_triggered，让 epilogue 走 summary-fallback 路径
                                        break
                            elif content and not content.strip():
                                logger.debug(f"OmniOfflineClient: 过滤空白内容 - content_repr: {repr(content)[:100]}")

                        # 流结束后：先 flush thinking stripper 的残留。仅漏型
                        # provider 的 thinking_on 轮挂了它；若整轮没出现 </think>
                        # （模型本轮没思考），它一直 hold，这里把攒住的正文还回
                        # prefix_buffer，走下面的通用 emit/guard 路径，避免丢答案。
                        if think_stripper is not None:
                            _think_residual = think_stripper.flush()
                            if _think_residual:
                                prefix_buffer += _think_residual
                                # Force the unified flush below to run on this
                                # residual. When prefix checking is disabled
                                # (_prefix_buffer_size == 0) prefix_checked starts
                                # True, so `and not prefix_checked` would otherwise
                                # drop the held answer silently. Safe to clear: a
                                # non-empty residual means no </think> ever arrived,
                                # which only happens when the stripper held the whole
                                # stream → prefix_buffer was never filled by the live
                                # path, so prefix_checked carried no completed state.
                                prefix_checked = False
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
                                    emit_flush_text = flush_text
                                    if self.enable_response_guard:
                                        # 长度 guard 看整轮（含 pre-tool），与上方主累加块对偶。
                                        candidate_total = assistant_message_total + flush_text
                                        current_length = count_tokens(candidate_total)
                                        if current_length > self.max_response_length:
                                            if summary_mode_enabled:
                                                # 与主累加块对偶：summary 模式下不 abort，
                                                # 切到 pending_cutover，下面 emit-split 块处理。
                                                if summary_state == 'idle':
                                                    summary_state = 'pending_cutover'
                                                    summary_trigger_tokens = current_length
                                                    # 算 flush_text 内的越界 char offset（与主累加块对偶）
                                                    _capped = truncate_to_tokens(
                                                        candidate_total, self.max_response_length,
                                                    )
                                                    summary_overflow_offset = max(
                                                        0, len(_capped) - len(assistant_message_total),
                                                    )
                                                    logger.info(
                                                        "OmniOfflineClient summary: 长回复触发于 flush "
                                                        "(%d tokens > %d，flush 内越界 offset=%d)",
                                                        current_length, self.max_response_length,
                                                        summary_overflow_offset,
                                                    )
                                            else:
                                                guard_triggered = True
                                                discard_reason = f"length>{self.max_response_length}"
                                                length_guard_original_tokens = current_length
                                                emit_flush_text = ""
                                                if not _is_gibberish_response(candidate_total):
                                                    capped = truncate_to_tokens(
                                                        candidate_total, self.max_response_length,
                                                    )
                                                    candidate_recovery = _truncate_to_last_sentence_end(capped)
                                                    if candidate_recovery:
                                                        if candidate_recovery.startswith(assistant_message_total):
                                                            recovery_suffix = candidate_recovery[len(assistant_message_total):]
                                                            if recovery_suffix.strip():
                                                                emit_flush_text = recovery_suffix
                                                                length_guard_recovery_text = candidate_recovery
                                                        elif (
                                                            assistant_message_total
                                                            and _has_unpersisted_recovery_suffix(assistant_message_total)
                                                        ):
                                                            length_guard_recovery_text = assistant_message_total
                                    if emit_flush_text and emit_flush_text.strip():
                                        # Emit fork（与主累加块对偶）：summary 模式下按 cutover 拆 UI/TTS
                                        if (
                                            summary_mode_enabled
                                            and summary_state in ('pending_cutover', 'cutover_done')
                                        ):
                                            if summary_state == 'pending_cutover':
                                                # 与主累加块对偶：消费 trigger flush 的越界 offset
                                                search_from = summary_overflow_offset
                                                summary_overflow_offset = 0
                                                term_pos_in_slice = _find_summary_terminator(
                                                    emit_flush_text[search_from:]
                                                )
                                                if term_pos_in_slice >= 0:
                                                    term_pos = search_from + term_pos_in_slice
                                                    pre = emit_flush_text[:term_pos + 1]
                                                    post = emit_flush_text[term_pos + 1:]
                                                    if pre:
                                                        assistant_message += pre
                                                        assistant_message_total += pre
                                                        if self.on_text_delta:
                                                            await self.on_text_delta(pre, is_first_chunk)
                                                        is_first_chunk = False
                                                    summary_prefix_for_history = assistant_message
                                                    summary_state = 'cutover_done'
                                                    # 对偶 chunk-loop emit fork 的 cutover 日志：
                                                    # 用 summary_trigger_tokens（flush 入口写入的）
                                                    # 把"什么时候触发的"信息留在日志里。
                                                    logger.info(
                                                        "OmniOfflineClient summary: cutover 完成于 flush "
                                                        "(prefix_chars=%d, trigger=%d tokens)",
                                                        len(assistant_message), summary_trigger_tokens,
                                                    )
                                                    if post:
                                                        assistant_message += post
                                                        assistant_message_total += post
                                                        summary_tail_buffer += post
                                                        if self.on_text_delta:
                                                            await self.on_text_delta(
                                                                post, is_first_chunk,
                                                                ui_enabled=True, tts_enabled=False,
                                                            )
                                                        is_first_chunk = False
                                                else:
                                                    assistant_message += emit_flush_text
                                                    assistant_message_total += emit_flush_text
                                                    if self.on_text_delta:
                                                        await self.on_text_delta(emit_flush_text, is_first_chunk)
                                                    is_first_chunk = False
                                            else:
                                                assistant_message += emit_flush_text
                                                assistant_message_total += emit_flush_text
                                                summary_tail_buffer += emit_flush_text
                                                if self.on_text_delta:
                                                    await self.on_text_delta(
                                                        emit_flush_text, is_first_chunk,
                                                        ui_enabled=True, tts_enabled=False,
                                                    )
                                                is_first_chunk = False
                                        else:
                                            assistant_message += emit_flush_text
                                            assistant_message_total += emit_flush_text
                                            if self.on_text_delta:
                                                await self.on_text_delta(emit_flush_text, is_first_chunk)
                                            is_first_chunk = False

                        if guard_triggered:
                            guard_attempt += 1
                            reroll_count += 1
                            will_retry = guard_attempt <= self.max_response_rerolls

                            # max_attempts 报给前端的是**总尝试次数**而非
                            # rerolls 次数（rerolls 不含首次尝试）。前端 attempt
                            # / max_attempts 进度条要 1/2 → 2/2 才合理。
                            total_attempts = self.max_response_rerolls + 1

                            recovery_text = length_guard_recovery_text
                            if discard_reason and "length>" in discard_reason:
                                # 长回复若是正常可读文本，直接按已发出的截断文本
                                # 收尾，不 reroll，避免 UI/TTS 和 history 分叉。
                                if not recovery_text and not _is_gibberish_response(assistant_message_total):
                                    capped = truncate_to_tokens(
                                        assistant_message_total, self.max_response_length,
                                    )
                                    candidate_recovery = _truncate_to_last_sentence_end(capped)
                                    if _has_unpersisted_recovery_suffix(candidate_recovery):
                                        recovery_text = candidate_recovery

                            if recovery_text and _has_unpersisted_recovery_suffix(recovery_text):
                                history_recovery_text = assistant_message
                                original_tokens = length_guard_original_tokens or count_tokens(assistant_message_total)
                                logger.info(
                                    "OmniOfflineClient: 长回复已流式输出，停止生成并按最后句末入历史 "
                                    "(原 %d tokens → 截断后 %d tokens)",
                                    original_tokens, count_tokens(recovery_text),
                                )
                                if history_recovery_text:
                                    self._conversation_history.append(AIMessage(content=history_recovery_text))
                                await self._check_repetition(recovery_text)
                                assistant_message = history_recovery_text
                                guard_exhausted = True
                                break
                            recovery_text = ""

                            if will_retry:
                                # 还能 retry：发 will_retry 通知，循环继续。前端
                                # 收到 response_discarded(will_retry=True, message=None)
                                # 走 retry toast 路径。
                                await self._notify_response_discarded(
                                    discard_reason or "guard",
                                    guard_attempt,
                                    total_attempts,
                                    True,
                                    None,
                                )
                                logger.info(
                                    "OmniOfflineClient: 响应被丢弃（%s），第 %d/%d 次重试",
                                    discard_reason, guard_attempt, total_attempts,
                                )
                                continue

                            # Reroll 耗尽。length 超长有两类：
                            #   (a) 模型真的写得多但还在正常说话 → 截到最后一个
                            #       句末标点，作为 RESPONSE_LENGTH_TRUNCATED 回复
                            #       发给前端，placeholder 不进 history（截取版进）。
                            #   (b) 模型疯了（BPE 重复 / emoji 刷屏 / 没标点的
                            #       连续乱码）→ 不要试图截"句子"出来，直接 filter
                            #       走 RESPONSE_TOO_LONG（语义=故障），core 那边
                            #       会让前端显示故障 placeholder + 把 placeholder
                            #       写进 history（让下一轮 LLM 知道这一轮失败）。
                            #
                            # 触发 (b) 的条件：_is_gibberish_response（标点/符号
                            # 密度 < 2% 或 > 60%）或截不出句末（整段无 . ! ? 。 ！ ？ …）。
                            #
                            # 关键：(a) 路径要先把 assistant_message 硬截到
                            # max_response_length 再找句末，否则截出来的句末仍
                            # 可能在 token 上限之外（比如最后一个句号在 950 token
                            # 处但 cap 是 300）。
                            if discard_reason and "length>" in discard_reason:
                                # 整轮判定：gibberish / 截断必须看 _total，否则
                                # tool 轮 sentinel 把 final-segment 清空之后整段
                                # pre-tool 被忽略，明明很长却走 RESPONSE_TOO_LONG。
                                if not _is_gibberish_response(assistant_message_total):
                                    capped = truncate_to_tokens(
                                        assistant_message_total, self.max_response_length,
                                    )
                                    candidate_recovery = _truncate_to_last_sentence_end(capped)
                                    if _has_unpersisted_recovery_suffix(candidate_recovery):
                                        recovery_text = candidate_recovery

                            if recovery_text:
                                original_tokens = length_guard_original_tokens or count_tokens(assistant_message_total)
                                logger.info(
                                    "OmniOfflineClient: guard 重试耗尽，截断至最后句末 "
                                    "(原 %d tokens → 截断后 %d tokens)",
                                    original_tokens, count_tokens(recovery_text),
                                )
                                truncate_msg = json.dumps({
                                    "code": "RESPONSE_LENGTH_TRUNCATED",
                                    "text": recovery_text,
                                })
                                # 走 _notify_response_discarded（不能用
                                # on_status_message）：前端在 response_discarded
                                # 分支识别 RESPONSE_LENGTH_TRUNCATED 才能触发
                                # truncate UX（不回滚输入 + 把 truncate text
                                # 当 placeholder body）。
                                await self._notify_response_discarded(
                                    discard_reason or "guard",
                                    guard_attempt,
                                    total_attempts,
                                    False,
                                    truncate_msg,
                                )
                                status_reported = True
                                # _conversation_history 由 core.handle_response_discarded
                                # 在 RESPONSE_LENGTH_TRUNCATED 分支 append
                                # （self.session 即本 OmniOfflineClient，二者共享同一
                                # 个 _conversation_history 列表）。这里只维护内部
                                # 重复检测列表。
                                await self._check_repetition(recovery_text)
                                assistant_message = recovery_text
                                guard_exhausted = True
                                break

                            final_message = json.dumps(
                                {"code": "RESPONSE_TOO_LONG"}
                                if discard_reason and "length>" in discard_reason
                                else {"code": "RESPONSE_INVALID"}
                            )
                            await self._notify_response_discarded(
                                discard_reason or "guard",
                                guard_attempt,
                                total_attempts,
                                False,
                                final_message,
                            )
                            status_reported = True
                            # gibberish 或截不出句末 / 非 length 类 guard 失败 —
                            # 走故障 placeholder 路径，core 会用 locale "fault"
                            # 文案占住 history，避免下一轮 LLM 看到空助手轮次。
                            logger.warning(
                                "OmniOfflineClient: guard 重试耗尽 (reason=%s)，"
                                "filter 输出走故障 placeholder",
                                discard_reason,
                            )
                            assistant_message = ""
                            guard_exhausted = True
                            break

                        # ── Summary 模式 epilogue ──
                        # 走到这里 guard_triggered 一定是 False（summary 路径不设
                        # length 类 guard）。根据 summary_state 决定：
                        #   - gibberish_fallback：tail 被判定胡言乱语，静默截断
                        #     —— 不发 RESPONSE_INVALID，因为那会触发 core 端的
                        #     _clear_tts_pipeline 把还在队列里没读完的 prefix
                        #     音频也清掉，反而让"已经听到的话"被截断。这里只
                        #     log + commit prefix 到 history，TTS 自然把队列
                        #     里残余的 prefix 播完。UI 显示的 gibberish 尾巴
                        #     与 live ≠ reload 的设计分岔本来就允许。
                        #   - cutover_done + 最终长度 ≤ max+slack：太短没必要摘要，把
                        #     tail 直接续给 TTS 读完，history 留完整原文。
                        #   - cutover_done + 最终长度更长：调小模型摘要，TTS 续上摘要，
                        #     history 写 prefix+summary。摘要失败 fallback 到 tail 续读。
                        #   - pending_cutover：触发了但 stream 结束前没找到 terminator
                        #     (整段无标点)。tail 全在主路径里发出去了，相当于没摘要，
                        #     history 写完整原文。
                        #   - idle：从未触发，常规流程，啥也不用做。
                        if summary_mode_enabled and summary_state == 'gibberish_fallback':
                            logger.warning(
                                "OmniOfflineClient summary: gibberish fallback, "
                                "静默 commit prefix (%d chars) 到 history，TTS 残队列保留",
                                len(summary_prefix_for_history),
                            )
                            if summary_prefix_for_history:
                                self._conversation_history.append(
                                    AIMessage(content=summary_prefix_for_history)
                                )
                            # 重复检测只看 prefix（= 真正进 history / 被 TTS 读的部分）。
                            # 用 assistant_message_total 会把判定为乱码、已丢弃的 tail
                            # 也塞进 _recent_responses，污染后续重复判定。
                            if summary_prefix_for_history:
                                await self._check_repetition(summary_prefix_for_history)
                            assistant_message = ""
                            guard_exhausted = True
                            break

                        if summary_mode_enabled and summary_state == 'cutover_done':
                            final_tokens = count_tokens(assistant_message_total)
                            slack_threshold = self.max_response_length + _SUMMARY_LATE_FINISH_SLACK
                            if final_tokens < slack_threshold:
                                # 尾巴太短：放弃摘要，tail 直接续给 TTS。
                                logger.info(
                                    "OmniOfflineClient summary: 最终 %d tokens < %d，放弃摘要，"
                                    "tail (%d chars) 续给 TTS",
                                    final_tokens, slack_threshold, len(summary_tail_buffer),
                                )
                                if summary_tail_buffer and self.on_text_delta:
                                    await self.on_text_delta(
                                        summary_tail_buffer, False,
                                        ui_enabled=False, tts_enabled=True,
                                    )
                            else:
                                summary_text = await self._summarize_tail_for_tts(
                                    prefix=summary_prefix_for_history,
                                    tail=summary_tail_buffer,
                                )
                                if summary_text:
                                    logger.info(
                                        "OmniOfflineClient summary: 摘要成功 "
                                        "(tail=%d chars → summary=%d chars)",
                                        len(summary_tail_buffer), len(summary_text),
                                    )
                                    if self.on_text_delta:
                                        await self.on_text_delta(
                                            summary_text, False,
                                            ui_enabled=False, tts_enabled=True,
                                        )
                                    # history = prefix + summary，与 TTS 听到的对齐
                                    assistant_message = summary_prefix_for_history + summary_text
                                else:
                                    logger.info(
                                        "OmniOfflineClient summary: 摘要失败/为空，"
                                        "tail 续给 TTS 读完"
                                    )
                                    if summary_tail_buffer and self.on_text_delta:
                                        await self.on_text_delta(
                                            summary_tail_buffer, False,
                                            ui_enabled=False, tts_enabled=True,
                                        )
                                    # assistant_message 不动 → history 写完整原文

                        # Token usage 由 _AsyncStreamWrapper hook 在流结束时自动记录，
                        # 此处不再手动调用 TokenTracker.record() 避免双重计数。

                        if assistant_message:
                            # final AIMessage 只写未被 inline 持久化的最后一段
                            # （pre-tool 文本已经在前面 ``assistant.tool_calls.content``
                            # 里了，再 append 一次会双写历史）。
                            self._conversation_history.append(AIMessage(content=assistant_message))
                        # 重复检测看完整一轮文本（含 pre-tool），与人类用户感知
                        # 的"这一轮 AI 说了什么"一致。
                        if assistant_message_total:
                            await self._check_repetition(assistant_message_total)
                        break

                    if guard_exhausted:
                        break

                    # 整轮判定：本轮只要产生过任何文本（含 pre-tool）就算成功完成
                    # retry 循环；用 final-segment 会让"max_tool_iterations 用尽
                    # 时只剩 pre-tool 被持久化、没出 final 回复"的轮次被错误重试。
                    if assistant_message_total:
                        break

                except _llm_retry_error_types() as e:
                    from openai import InternalServerError

                    error_type = type(e).__name__
                    error_str_lower = str(e).lower()
                    is_internal_error = isinstance(e, InternalServerError)
                    logger.info(f"ℹ️ 捕获到 {error_type} 错误")

                    def _count_llm_error(api_key_rejected: bool = False):
                        # D1 失败诊断：typed API 错误（连接/认证/限流/欠费/配额/
                        # key 拒绝）的**终态**也要计入 llm_error。只在给上的 break
                        # 路径调，不在 retry-continue 调（重试中不算失败）。generic
                        # except 与本块互斥，不会双计（Codex）。
                        try:
                            from utils.instrument import counter as _ic
                            # before_first_loop：错误发生在用户体验到核心 loop 之前 =
                            # 首次体验障碍型流失（开了口但没收到回复）。true/false/unknown
                            # 低基数；区分"卡在首次体验"vs"用过之后才报错"两类 D1 流失。
                            try:
                                from utils.token_tracker import TokenTracker as _TT
                                _bfl = "false" if _TT.get_instance().has_completed_core_loop() else "true"
                            except Exception:
                                _bfl = "unknown"
                            _ic("llm_error", error_class=error_type[:48], before_first_loop=_bfl)
                            if api_key_rejected:
                                _ic("api_key_invalid", before_first_loop=_bfl)
                        except Exception:
                            # 埋点 best-effort，绝不影响错误上报 / 重试主流程。
                            pass

                    # 欠费/API Key 错误立即上报并终止；配额错误上报但继续重试
                    if '欠费' in error_str_lower or 'standing' in error_str_lower:
                        logger.error(f"OmniOfflineClient: 检测到欠费错误，直接上报: {e}")
                        _count_llm_error()
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_ARREARS"}))
                            status_reported = True
                        break
                    elif _is_api_key_rejected_error(e):
                        logger.error(f"OmniOfflineClient: 检测到 API Key 错误，直接上报: {e}")
                        _count_llm_error(api_key_rejected=True)
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
                        # 整轮判定：本轮是否吐过任何文本到前端 —— 用 _total 才能
                        # 覆盖 tool_round_persisted 已重置 final-segment 的场景。
                        # 否则 pre-tool 文本残留在前端但 notify_discarded 漏触发。
                        if assistant_message_total and self.on_response_discarded:
                            await self._notify_response_discarded(
                                f"api_error:{error_type}",
                                attempt + 1,
                                max_retries,
                                will_retry=True,
                                message=None,
                            )
                        assistant_message = ""
                        assistant_message_total = ""
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        error_msg = f"💥 LLM连接失败（{error_type}），已重试{max_retries}次: {e}"
                        logger.error(error_msg)
                        _count_llm_error()  # 重试耗尽 = 终态失败，计入 llm_error
                        if self.on_status_message:
                            if is_internal_error:
                                await self.on_status_message(json.dumps({"code": "LLM_UPSTREAM_ERROR"}))
                            else:
                                await self.on_status_message(json.dumps({"code": "LLM_CONNECTION_EXHAUSTED", "details": {"error_type": error_type, "max_retries": max_retries, "error": str(e)}}))
                            status_reported = True
                        break
                except Exception as e:
                    is_api_key_rejected = _is_api_key_rejected_error(e)
                    # Telemetry：D1 流失里 LLM 调用失败是大头。error_class 低基数
                    # （exception 类名）；api_key_invalid 单独计——首日配错 key
                    # 是源码版用户的常见流失坑。
                    try:
                        from utils.instrument import counter as _instr_counter
                        # before_first_loop 与 typed 错误路径（_count_llm_error）保持
                        # 同维度，避免 llm_error/api_key_invalid 混合标签拆裂 D1 分桶。
                        try:
                            from utils.token_tracker import TokenTracker as _TT
                            _bfl = "false" if _TT.get_instance().has_completed_core_loop() else "true"
                        except Exception:
                            _bfl = "unknown"
                        _instr_counter("llm_error", error_class=type(e).__name__[:48], before_first_loop=_bfl)
                        if is_api_key_rejected:
                            _instr_counter("api_key_invalid", before_first_loop=_bfl)
                    except Exception:
                        # 埋点 best-effort，绝不掩盖/打断原始 LLM 错误的处理路径。
                        pass
                    if is_api_key_rejected:
                        status_error_payload = {"code": "API_KEY_REJECTED"}
                        discard_error_payload = status_error_payload
                        error_msg = f"💥 文本生成异常: 检测到 API Key 被拒绝: {type(e).__name__}: {e}"
                    else:
                        status_error_payload = {
                            "code": "TEXT_GEN_ERROR",
                            "details": {
                                "error_type": type(e).__name__,
                                "error": str(e),
                            },
                        }
                        discard_error_payload = {
                            "code": "TEXT_GEN_ERROR_AFTER_PARTIAL",
                            "details": {
                                "error_type": type(e).__name__,
                                "error": str(e),
                            },
                        }
                        error_msg = f"💥 文本生成异常: {type(e).__name__}: {e}"
                    logger.error(error_msg)
                    # 如果本轮已经向前端吐过文本（典型场景：genai 路径在
                    # _astream_with_tools 已吐文本后再抛 transient/tools-
                    # unsupported，被显式 raise 上来），必须通知前端清空
                    # 那截半截气泡，否则用户会看到一段被中断的文本永远停
                    # 在那。和 (APIConnectionError 等) 分支语义对偶，但
                    # 这条路径已经决定不再重试（break 在下面），所以
                    # ``will_retry=False``，并附带可读的错误码到前端。
                    # 整轮判定：用 _total，覆盖 tool_round_persisted 已重置
                    # final-segment 但 pre-tool 文本仍在前端的场景。
                    if assistant_message_total and self.on_response_discarded:
                        try:
                            await self._notify_response_discarded(
                                f"text_gen_error:{type(e).__name__}",
                                attempt + 1,
                                max_retries,
                                will_retry=False,
                                message=json.dumps(discard_error_payload),
                            )
                            status_reported = True
                        except Exception as _notify_err:
                            logger.warning(
                                "通知 response_discarded(after partial) 失败: %s",
                                _notify_err,
                            )
                    if not status_reported and self.on_status_message:
                        await self.on_status_message(json.dumps(status_error_payload))
                        status_reported = True
                    break
        finally:
            self._is_responding = False

            if (
                history_replacement_text
                and 0 <= history_replacement_index < len(self._conversation_history)
                and self._conversation_history[history_replacement_index] is user_message
            ):
                self._conversation_history[history_replacement_index] = HumanMessage(
                    content=history_replacement_text
                )

            # 还原 summary 模式临时抬高的 API budget，别泄漏给 prompt_ephemeral。
            if _summary_prev_max_tokens is not None and getattr(self, "llm", None) is not None:
                self.llm.max_completion_tokens = _summary_prev_max_tokens

            # 整轮判定：所有重试都没产生过任何文本（包括 pre-tool）才算 LLM_NO_RESPONSE。
            # 用 final-segment 会让"tool 轮跑完了但模型没出 final 文本"的场景被错报。
            if not assistant_message_total and not guard_exhausted and not status_reported:
                # 把最后一次 attempt 的 finish_reason / block_reason / prompt_tokens
                # 拼进 warning。Gemini-via-OpenAI-compat 静默 empty 时（safety /
                # recitation / max_tokens / 上下文超限），这条 log 是日志里能拿到
                # 的唯一"为什么 empty"线索。
                logger.warning(
                    "OmniOfflineClient: 所有重试均未产生文本回复 "
                    "(finish_reason=%s block_reason=%s prompt_tokens=%s model=%s)",
                    getattr(self, "_last_finish_reason", None),
                    getattr(self, "_last_block_reason", None),
                    getattr(self, "_last_prompt_tokens", None),
                    getattr(self, "model", None),
                )
                if self.on_status_message:
                    finish_reason = getattr(self, "_last_finish_reason", None)
                    block_reason = getattr(self, "_last_block_reason", None)
                    prompt_tokens = getattr(self, "_last_prompt_tokens", None)
                    model = getattr(self, "model", None)
                    if _is_safety_violation_signal(finish_reason, block_reason):
                        await self.on_status_message(json.dumps({
                            "code": "API_POLICY_VIOLATION",
                            "details": {
                                "msg": "LLM completion was blocked by upstream safety policy.",
                                "finish_reason": finish_reason,
                                "block_reason": block_reason,
                                "prompt_tokens": prompt_tokens,
                                "model": model,
                            },
                        }))
                    else:
                        await self.on_status_message(json.dumps({"code": "LLM_NO_RESPONSE"}))

            # Call response done callback
            if self.on_response_done:
                await self.on_response_done()
