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

from typing import Sequence

from main_logic.proactive_delivery import (
    TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
    fit_images_to_turn_budget,
)

from ._turn_context import _mark as _mark_turn_context
from ._shared import (
    _cancelled_turn_end,
    _find_by_identity,
    _same_route,
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

from ._media import (
    _FRAME_SOURCE_PLUGIN,
    _FRAME_SOURCE_SCREEN,
    _FRAME_SOURCE_UNKNOWN,
    _FRAME_SOURCE_USER,
)

from ._genai_support import (
    _should_use_genai_sdk,
)
from ._lifecycle import (
    _close_genai_client,
    _retire_replaced_clients,
    _tracked_reply_call,
    _with_dialog_slop,
)


def _strip_route_bound_tool_call_extras(history) -> int:
    """Drop ``tool_calls[].extra_content`` from a history that is about to be
    replayed on a DIFFERENT endpoint. Returns how many were dropped.

    ``extra_content`` is a vendor-private blob (today: Gemini's
    ``thought_signature``) that only the endpoint which minted it understands.
    It is stored so the same route can replay it — but the history outlives the
    route: ``switch_model(vision_model, use_vision_config=True)`` re-points
    ``self.llm`` at a separately configured provider for the rest of the session
    and deliberately keeps the history. openai-python forwards unknown keys
    inside ``messages[].tool_calls[]`` into the request body verbatim, so
    without this the blob is POSTed to a provider that never issued it — and
    endpoints that validate message objects strictly reject the request, which
    would break every remaining turn of the session.

    Dropping degrades that history to its pre-signature form (exactly what the
    endpoint would have received before signatures were stored at all), so the
    worst case is the old behaviour rather than a hard failure.

    Scope note: the sibling ``reasoning_content`` field on the same assistant
    turn has the same route-bound nature and predates this helper. It is left
    alone deliberately — it has its own provider contract (thinking endpoints
    require it echoed back) and its own failure mode, and changing it belongs
    in its own change rather than riding along here.
    """
    stripped = 0
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        for tool_call in (msg.get("tool_calls") or []):
            if isinstance(tool_call, dict) and tool_call.pop("extra_content", None) is not None:
                stripped += 1
    return stripped


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
                # Normal budget; summary's 3000 increase applies temporarily inside stream_text only.
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
        # As in realtime: release close()'s bus-copy latch here; otherwise reusing this instance
        # and reconnecting would stop all frames/conversation from reaching the bus.
        self._bus_copies_closed = False
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

    async def switch_model(
        self,
        new_model: str,
        use_vision_config: bool = False,
        abandon_if: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """
        Temporarily switch to a different model (e.g., vision model).
        This allows dynamic model switching for vision tasks.

        Args:
            new_model: The model to switch to
            use_vision_config: If True, use vision_base_url and vision_api_key
            abandon_if: Asked right before the new client replaces the
                current one, after the awaits that build it. When it returns
                True the switch is dropped and the current client stays:
                ``prompt_ephemeral`` passes its own decline check, so a reply
                that began meanwhile is not moved onto another client.

        Returns False only when ``abandon_if`` dropped the switch.

        The replaced clients are closed once no reply call is in flight
        (``_retire_replaced_clients``), never under a reply still streaming
        on one.
        """
        lock = getattr(self, "_model_switch_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._model_switch_lock = lock

        async with lock:
            if not new_model:
                return True

            # Select API configuration.
            if use_vision_config:
                base_url = self.vision_base_url
                api_key = self.vision_api_key if self.vision_api_key and self.vision_api_key != '' else None
                provider_type = getattr(self, "vision_provider_type", None)
            else:
                base_url = self.base_url
                api_key = self.api_key
                provider_type = getattr(self, "provider_type", None)

            # Matching model IDs alone cannot return early: vision and conversation may share an ID but use
            # different URL / key / protocol (e.g. a local Ollama multimodal host, or an Anthropic-style
            # interface at the same gateway). Comparing IDs alone keeps screenshots hitting a text-only endpoint.
            # Only unchanged IDs and routes constitute a true no-op.
            if new_model == self.model and _same_route(
                base_url, api_key, provider_type,
                self.base_url, self.api_key, getattr(self, "provider_type", None),
            ):
                return True

            logger.info(f"Switching model from {self.model} to {new_model}")

            # Create the new client first, then atomically replace after success to avoid partial switching.
            # Set max_completion_tokens in sync with current max_response_length
            # (as in __init__).
            _derived_max_tokens = _budget_to_max_tokens(self.max_response_length)
            # But max_response_length alone is insufficient: this function runs mid-turn
            # (switching to vision for tool-returned images), after stream_text may have raised
            # the current client's max_completion_tokens to summary tier or added
            # focused-turn token headroom. Recomputing from max_response_length loses that tier,
            # prematurely truncating follow-ups/final answers at the provider; turn finally then writes
            # the old client's saved value onto this new client.
            #
            # Inherit upward only: an old value below baseline is residue from a reverted increase, not to revive.
            _live_max_tokens = getattr(
                getattr(self, "llm", None), "max_completion_tokens", None
            )
            # None is the unlimited sentinel (_budget_to_max_tokens returns None for unlimited
            # budgets, omitting the field entirely). Already the highest tier, it has nothing to inherit;
            # replacing it with a finite live value is a downgrade. Without this guard, the comparison
            # below is int > None, raising TypeError on the very path this fix protects.
            if (
                _derived_max_tokens is not None
                and isinstance(_live_max_tokens, int)
                and _live_max_tokens > _derived_max_tokens
            ):
                _derived_max_tokens = _live_max_tokens
            new_llm = await create_chat_llm_async(
                new_model, base_url, api_key,
                streaming=True, max_retries=0,
                max_completion_tokens=_derived_max_tokens,
                timeout=DIALOG_LLM_STREAM_TIMEOUT_SECONDS,  # hang-guard; generous so normal/long replies aren't truncated
                provider_type=provider_type,
            )
            # Nothing below awaits until the swap, so this answer still holds
            # when the new client goes in.
            if abandon_if is not None and abandon_if():
                logger.info("switch_model: switch to %s dropped by the caller", new_model)
                try:
                    await new_llm.aclose()
                except Exception as e:
                    logger.warning(f"switch_model: unused client aclose failed: {e}")
                return False
            # Did the endpoint really change? Only changing endpoint/account requires clearing historical
            # vendor-private fields understood only by their issuer. Changing models at the same endpoint
            # (conversation → vision with one vendor) must retain them: that is where signatures
            # are needed; clearing would reintroduce the 400 this fixes.
            #
            # Normalize empty strings to None before comparison: vision above already does,
            # conversation does not. Different empty representations would misclassify an unchanged endpoint
            # as rerouted, clearing too much precisely in the target scenario.
            # Compare URLs via same_endpoint too: trailing slash, host case, explicit default port
            # do not indicate another vendor. Protocol is excluded: signatures bind to issuing endpoint/account only.
            route_changed = not _same_route(
                base_url, api_key, None,
                self.base_url, self.api_key, None,
            )
            old_llm = self.llm
            self.llm = new_llm
            self.model = new_model
            # ⚠️ Sync self.base_url / self.api_key; otherwise subsequent _astream_with_tools
            # recalculates _use_genai_sdk from old conversation config, incorrectly routing
            # vision's Gemini endpoint to OpenAI-compatible protocol (or vice versa).
            self.base_url = base_url
            self.api_key = api_key
            # Sync protocol with route too: next switch_model no-op check and conversation branch
            # both read it; stale values make an already-switched session appear unswitched.
            self.provider_type = provider_type
            if route_changed:
                # Defensive getattr matches this file: test stubs using __new__ to bypass __init__
                # lack this field, and the helper is also a no-op for None.
                dropped = _strip_route_bound_tool_call_extras(
                    getattr(self, "_conversation_history", None)
                )
                if dropped:
                    logger.info(
                        "switch_model: dropped %d route-bound tool_call extra_content "
                        "entry/entries before replaying history on %s",
                        dropped, base_url or "(default endpoint)",
                    )
            # Refresh routing flags and discard old _genai_client (invalid if api_key changed).
            # genai.Client owns an httpx connection pool; setting None and relying on GC is not
            # a leak, but closing early releases connections promptly. Responses use its .aio half,
            # which synchronous close() cannot close; close both halves (_close_genai_client).
            old_genai = self._genai_client
            self._use_genai_sdk = _should_use_genai_sdk(self.model, self.base_url)
            self._genai_client = None
            self._genai_tools_unsupported = False
            # Give tools another chance after switching: tools rejection was the prior model's capability limit.
            self._openai_tools_unsupported = False
            self._openai_tools_unsupported_with_images = False
            # Closed once no reply call can still be streaming on them.
            closers = [lambda: old_llm.aclose()]
            if old_genai is not None:
                closers.insert(0, lambda: _close_genai_client(old_genai))
            await _retire_replaced_clients(self, closers)
        return True

    def _commit_cancelled_reply(
        self, anchor, reply, generation: int, *, turn_history=None,
    ) -> None:
        """Commit the visible part of a cancelled reply to its own turn.

        ``anchor`` is the last message this turn owns in history (its user
        message, or for ``prompt_ephemeral`` the last non-tool-round message
        it saw; ``None`` when there was none) and ``generation`` the one it
        streamed under. A turn that began after the cancellation may already
        have saved its user message or a proactive reply, so the reply goes
        where this turn ends (``_cancelled_turn_end``) instead of at the end.
        Tool-round messages are dicts and end no turn: a round starts none,
        and stepping over it keeps it next to its replies. ``turn_history`` is
        the history list the turn began in: once history was replaced on
        purpose (the repetition reset, ``close()``) the reply is dropped. An
        anchor gone from the same list (trimmed in place) falls back to
        appending, and an empty reply is never written: some providers reject
        an empty assistant message.
        """
        if not str(getattr(reply, "content", "") or "").strip():
            return
        history = self._conversation_history
        if turn_history is not None and history is not turn_history:
            return
        start = -1 if anchor is None else _find_by_identity(history, -1, anchor)
        position = len(history)
        if anchor is None or start >= 0:
            position = _cancelled_turn_end(
                history, start, generation,
                round_generation=self._proactive_round_generation,
            )
        history.insert(position, reply)


    def _commit_reply(
        self, anchor, text: str, generation: int, *, turn_history=None,
    ) -> bool:
        """Write a reply's final ``text`` to history; False once it is no
        longer live.

        Every await before a commit (the end-of-stream prefix flush, the tail
        or summary sent to TTS) is a cancellation point, so each commit decides
        where to write at the moment it writes: a live or guard-paused reply is
        appended, a cancelled or displaced one goes through
        ``_commit_cancelled_reply`` and never lands after a turn that began
        after it. A caller keeps a reply that is no longer live out of the
        repetition check too.
        """
        if self._active_response_generation != generation:
            self._commit_cancelled_reply(
                anchor, AIMessage(content=text), generation, turn_history=turn_history,
            )
            return False
        if text:
            self._conversation_history.append(AIMessage(content=text))
        return True

    @staticmethod
    def _mark_reply_tail_completed(reply_owner, live: bool, shown: str) -> None:
        """Let the reply-tail images of ``reply_owner`` follow its reply.

        Every exit that keeps this attempt's shown text as the reply (the
        regular commit, the long-reply recovery, the summary gibberish
        fallback) marks it. A retry or discard leaves it incomplete, and the
        reroll exhausted ``RESPONSE_LENGTH_TRUNCATED`` recovery is marked by
        the core once it has published the recovered body.
        """
        tail = getattr(reply_owner, "reply_tail", None)
        if tail is not None and live and shown:
            tail.completed = True

    def _trim_cancelled_round_text(self, shown: str, rounds) -> None:
        """Make a kept, cancelled tool round hold only the text that was shown.

        The tool loop writes its own stream buffer into the round, which also
        holds what this turn deliberately withheld (the name-prefix buffer, a
        think residual, a summary tail). A cancelled turn never emits that,
        so the next request must not see it either. ``rounds`` are this
        turn's own rounds (see ``_last_tool_round_of``).
        """
        kept = self._last_tool_round_of(rounds)
        if kept is not None:
            kept["content"] = shown

    def _last_tool_round_of(self, rounds):
        """The tool round (assistant ``tool_calls`` turn) this turn appended
        last, while it is still in history; None if it has none or that round
        is gone.

        ``rounds`` lists, in order, the assistant turns this turn's tool loop
        appended (``_run_tool_round``). They are matched by identity, never by
        position: a reply still live in this turn's setup window can reach
        its tool round after this turn's user message was saved, and this
        turn's own round can follow the user message of a turn whose setup
        window it ran in. A round that left history (no call ran, or it went
        with its turn) is not looked for elsewhere: an earlier round never
        held the text shown after it.
        """
        if not rounds:
            return None
        latest = rounds[-1]
        if _find_by_identity(self._conversation_history, -1, latest) < 0:
            return None
        return latest

    def _keep_shown_text_of_cut_stream(
        self, anchor, reply, segment_round, generation: int, rounds,
        *, turn_history=None,
    ) -> None:
        """Keep what a reply had shown when it was cut before it committed.

        A task cancelled in the stream loop, the end-of-stream flush or the
        summary epilogue never reaches the commit it was heading for, so
        this does what that commit, or the tool round sentinel before it,
        would have done with ``reply`` (holding the text shown since the
        last persisted tool round). A round of this turn's own
        ``rounds`` kept after ``segment_round`` (the one the last sentinel
        reported) already holds that text, its sentinel lost to the
        cancellation: it is trimmed to it. Otherwise the reply is committed
        as a cancelled reply, in order. ``prompt_ephemeral`` commits a
        cancelled reply through here as well.
        """
        kept = self._last_tool_round_of(rounds)
        if kept is not None and kept is not segment_round:
            kept["content"] = reply.content
            return
        self._commit_cancelled_reply(
            anchor, reply, generation, turn_history=turn_history,
        )

    async def _check_repetition(self, response: str) -> bool:
        """
        Check whether the reply is highly repetitive of recent replies.
        Returns True and triggers the callback if 3 consecutive turns are highly repetitive.
        Never fires while ``repetition_reset_enabled`` is off.
        """
        if not self.repetition_reset_enabled:
            return False

        # Compare similarity with recent responses.
        high_similarity_count = 0
        for recent in self._recent_responses:
            similarity = calculate_text_similarity(response, recent)
            if similarity >= self._repetition_threshold:
                high_similarity_count += 1

        # Add to recent response list.
        self._recent_responses.append(response)
        if len(self._recent_responses) > self._max_recent_responses:
            self._recent_responses.pop(0)

        # Trigger detection when highly repetitive against both prior turns (third repetition).
        if high_similarity_count >= 2:
            logger.warning(f"OmniOfflineClient: 检测到连续{high_similarity_count + 1}轮高重复度对话")

            # Clear conversation history, retaining system instructions.
            if self._conversation_history and isinstance(self._conversation_history[0], SystemMessage):
                self._conversation_history = [self._conversation_history[0]]
            else:
                self._conversation_history = []

            # Clear repetition-detection cache.
            self._recent_responses.clear()

            # Trigger callback.
            if self.on_repetition_detected:
                await self.on_repetition_detected()

            return True

        return False

    async def _notify_response_discarded(
        self,
        reason: str,
        attempt: int,
        max_attempts: int,
        will_retry: bool,
        message: Optional[str] = None,
        *,
        callback: Optional[
            Callable[[str, int, int, bool, Optional[str]], Awaitable[None]]
        ] = None,
    ) -> None:
        """
        Notify the upper layer that the current reply was discarded, so the frontend bubble can be cleared / the user informed
        """
        discard_callback = callback or self.on_response_discarded
        if discard_callback:
            try:
                await discard_callback(reason, attempt, max_attempts, will_retry, message)
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
        informative), with the session locale breaking the Simplified / Traditional
        tie the detector cannot see.
        """
        if not (tail and tail.strip()):
            return None

        # Each provider in config/api_providers.json has an emotion configuration
        # `emotion_model` field; config_manager returns the current provider's
        # emotion subconfiguration (model/base_url/api_key).
        try:
            from utils.config_manager import get_config_manager  # Lazy import avoids circular dependencies.
            cfg_mgr = get_config_manager()
            emotion_config = await cfg_mgr.aget_model_api_config('emotion') if cfg_mgr else None
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
            from utils.language_utils import detect_prompt_language
            # detect_language alone reports script families, so a Traditional tail
            # comes back as 'zh' and the zh-TW row of LONG_RESPONSE_TAIL_SUMMARY_PROMPT
            # is unreachable. detect_prompt_language refines that with the session's
            # own locale, which is the only signal separating the two (issue #2500 step 2).
            ui_language = None
            provider = getattr(self, "_user_language_provider", None)
            if provider:
                ui_language = provider()
            lang = detect_prompt_language(tail, default='zh', ui_language=ui_language) or 'zh'
        except Exception:
            lang = 'zh'

        try:
            from config.prompts.prompts_response import get_long_response_tail_summary_prompts
            templates = get_long_response_tail_summary_prompts(lang)
        except Exception as e:
            logger.warning("summary: prompt 模板取失败: %s", e)
            return None

        # Persona-agnostic system template has no placeholders; use directly. Fill only prefix/tail in user template.
        system_text = templates['system']
        try:
            user_text = templates['user_template'].format(
                prefix=prefix or '',
                tail=tail,
            )
        except KeyError as e:
            logger.warning("summary: prompt 占位符缺失: %s", e)
            return None

        # Attribute token usage to "long_response_summary", separate from emotion.
        set_call_type("long_response_summary")
        messages = [
            SystemMessage(content=system_text),
            HumanMessage(content=user_text),
        ]
        # Omit temperature: project policy delegates to provider defaults
        # (enforced by scripts/check_no_temperature.py). Emotion-tier models already have
        # suitable temperature and need no caller intervention.
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
        # Fallback: strip model-added quotes / meta prefixes. Emotion-tier models occasionally write
        # "总之，xxx" / "总结：xxx" despite prompt prohibitions; here
        # strip outer quotes and common meta prefixes only, treating the rest as natural character speech.
        if summary and summary[0] in '“”"\'「『' and summary[-1] in '“”"\'」』':
            summary = summary[1:-1].strip()
        if not summary:
            return None
        # Hard stop: emotion-tier models may produce 3–4 sentences despite prompt limits;
        # feeding all to TTS defeats the brief-ending goal. Keep at most two sentence-end
        # segments, then hard-truncate to ``_SUMMARY_HARD_TOKEN_CAP`` tokens. Apply both limits:
        # split on sentence ends first, then token fallback; either can shorten output. Token accounting
        # matches budget and behaves consistently across languages (character counts overpenalize CJK).
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
            # Discard any tail the model added beyond two sentences.
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

    @_tracked_reply_call
    @_with_dialog_slop
    async def stream_text(
        self,
        text: str,
        *,
        system_prefix: str | None = None,
        system_prefix_images: Optional[list[str]] = None,
        turn_images: Optional[Sequence[str]] = None,
        # Capture channel for this turn's turn_images ("screen" / "camera"). Independent ASR frames
        # arrive here as base64 strings with no visible screen/camera distinction; without metadata,
        # they become ordinary "user" attachments, misleading source-filtering plugins.
        # This is the offline counterpart to realtime's MultimodalTurn.source.
        turn_source: Optional[str] = None,
        # Only the first images in turn_images belong to this channel. Independent ASR submissions append
        # user-dragged attachments after sampled frames (see _media.py ordering explanation); attachments
        # are user-provided and must not inherit this turn's capture channel.
        turn_source_count: Optional[int] = None,
        turn_id: Optional[str] = None,
        on_turn_committed: Optional[Callable[[], None]] = None,
        thinking_on: bool = False,
        input_transcript_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        history_replacement_text: str | None = None,
        response_discarded_callback: Optional[
            Callable[[str, int, int, bool, Optional[str]], Awaitable[None]]
        ] = None,
        response_done_callback: Optional[Callable[[], Awaitable[None]]] = None,
        reply_owner: Any = None,
        # Additional turn context (``_turn_context.TurnContext``): resident knowledge blocks
        # anchor after this turn's user message and survive the provider response in later session requests;
        # ephemeral corpus/memory blocks enter this request only. Neither enters history.
        turn_context: Any = None,
    ) -> None:
        """
        Send a text message to the API and stream the response.
        If there are pending images, temporarily switch to vision model for this turn.
        Uses langchain ChatOpenAI for streaming.

        ``thinking_on`` (Focus mode, docs/design/focus-truename-mode.md):
        when True, this single turn drops the auto-resolved thinking-off
        ``extra_body`` so the provider runs its default reasoning (\"reason freely\").
        It is a per-call override (``extra_body=None`` threaded into
        ``astream``) — the session LLM is NOT rebuilt and the next regular
        turn falls straight back to thinking-off. Applies to the
        OpenAI-compat path (where the thinking-off knob lives); the native
        google-genai path is already thinking-capable by default, so the
        override is a no-op there.

        Purpose of ``system_prefix``: the caller (typically SessionManager rendering a
        passive agent callback into watermarked ``======[系统通知] xxx======`` text)
        splices this neutral system-notice text **in place, as a prefix to this turn's
        user message content** — the LLM treats it as \"extra context attached at the
        moment the user spoke\" and mentions it naturally within the same turn, without
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

        ``response_discarded_callback`` binds discard ownership to this invocation.
        It avoids re-reading mutable session-level request state after a later text
        request has already started.

        ``response_done_callback`` does the same for the completion, which runs
        in place of ``on_response_done``. It still runs whenever the session
        callback would, including for a reply cut by ``close()``; the caller
        decides what a late completion may still touch.

        ``reply_owner`` is an opaque token for the caller's own record of this
        reply. When the reply's close is taken over (an interruption, or a
        displacing begin), ``InterruptedReply.owner`` hands it back, so the
        caller closes this reply and not whatever its shared state holds then.

        ``system_prefix_images`` binds passive callback media to the same
        invocation as ``system_prefix``.  Unlike ``_pending_images``, this list
        cannot be consumed by a concurrently scheduled text request while Core
        awaits its Focus decision.

        ``turn_id`` identifies an independent-ASR utterance and travels no
        further than the plugin frame bus, where it is what lets a plugin see
        that several frames were sampled from one utterance. Ordinary text
        turns have no such identity and leave it None.
        """  # noqa: DOCSTRING_CJK
        prefix_images = list(system_prefix_images or [])
        # User frames supplied by this turn (independent ASR start/middle/end samples). Deliberately bypass
        # _pending_images: that session-wide next-consumer queue can receive one-shot attachments
        # (dragged/chat images) between staging and consumption, accidentally consuming them with this
        # turn and pairing images with the wrong utterance. Invocation-local storage removes that race.
        own_images = [image for image in (turn_images or []) if image]
        # A turn with its own frames (independent ASR) never consumes the shared attachment queue.
        # This session-wide next-consumer queue could receive attachments during this turn's awaits
        # (switch_model / provider request), clearing them, mispairing utterances, and losing them for
        # their own follow-up. submit already consumed a snapshot; here leave the live queue untouched.
        # Consume and copy in one synchronous step with no await: if switch_model() yields below,
        # another concurrent plain-text request can copy the same attachments, sending the same images twice;
        # later prefix deletion could then remove someone else's queue entries.
        if own_images:
            attachment_images = []
        else:
            attachment_images = list(self._pending_images)
            del self._pending_images[:len(attachment_images)]
        if not text or not text.strip():
            # If only images without text, use a default prompt
            if (
                attachment_images
                or prefix_images
                or own_images
                or getattr(self, "_pending_plugin_images", None)
            ):
                text = "请分析这些图片。"
            else:
                return

        # Fresh stream: open a new reasoning-pulse scope (bump the ownership seq)
        # so this turn's first reasoning chunk re-pulses the bubble. stream_text
        # does not clear via _notify_reasoning_done — core's inline finally clears
        # unconditionally — so it only needs the bump, not an owner token.
        self._begin_reasoning_stream()
        discard_callback = response_discarded_callback or self.on_response_discarded

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
        # A few lightweight callers/tests intentionally construct the client via
        # ``__new__`` and wire only the fields needed for one streaming turn.
        # Treat an absent staging slot exactly like an empty slot so the optional
        # proactive-vision feature does not break those legacy construction paths.
        proactive_image = getattr(self, "_proactive_image_to_inject", None)
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
        # Plugin `read` frames sit in their own quota-bounded list. Instances
        # built via __new__ (tests, legacy callers) never ran __init__, so read
        # it the same defensive way the proactive slot is read above.
        #
        # Only test presence here; read content alongside the user list below: vision-model switching
        # awaits, so a snapshot now misses plugin images arriving during switching, then clears them,
        # losing them from every turn (Codex P2).
        has_images = (
            bool(proactive_image)
            or bool(prefix_images)
            or bool(own_images)
            or bool(attachment_images)
            or len(getattr(self, "_pending_plugin_images", None) or []) > 0
        )
        # Inject system_prefix in place, before the user content's text segment (its watermark
        # distinguishes it without another separator). Callback text persists in history with HumanMessage,
        # symmetrically with voice-mode user-role injection.
        _user_text = text.strip()
        _prefix_clean = (system_prefix or "").strip()
        _user_text_with_prefix = (
            f"{_prefix_clean}\n\n{_user_text}" if _prefix_clean else _user_text
        )

        # Prepare user message content
        if has_images:
            # Switch to vision model permanently for this session
            # (cannot switch back because image data remains in conversation history).
            # Do not require vision_model != model: the same id on a different
            # vision URL/key must still switch; switch_model itself is a no-op
            # when both the id and the endpoint already match.
            if self.vision_model:
                logger.info(f"🖼️ Temporarily switching to vision model: {self.vision_model} (from {self.model})")
                try:
                    await self.switch_model(self.vision_model, use_vision_config=True)
                except BaseException:
                    # Attachments were atomically dequeued above. This is the sole await between dequeue
                    # and message assembly: vision switching creates an LLM client, so network faults /
                    # invalid keys can raise. Without restoration the user's selected image is neither sent nor
                    # queued. Use the same failure-rollback criterion as submit_multimodal_turn.
                    if attachment_images:
                        self._pending_images[0:0] = attachment_images
                    raise

            # Multi-modal message: images + text
            content = []

            # Read after the model-switch await, with no suspension before cleanup below,
            # ensuring attached and cleared batches match (in-place iteration of the user list
            # naturally provides this property).
            plugin_images = list(getattr(self, "_pending_plugin_images", None) or [])
            # Take ownership in place: clear immediately after reading, with no await (otherwise plugin
            # images arriving during the model call are also erased, disappearing from every turn). Once taken,
            # these bytes belong to this turn and can safely undergo asynchronous compression/sampling.
            _plugin_pending = getattr(self, "_pending_plugin_images", None)
            if _plugin_pending is not None:
                _plugin_pending.clear()
            # Order is both chronological and relevance-based (#2964 × #2835 merge):
            #   1. Proactive screenshot: screen content before the user spoke, most distant;
            #   2. Plugin-provided context;
            #   3. Passive callback images (also context, not captured by the user now);
            #   4. This turn's sampled frames (independent ASR start/middle/end);
            #   5. Explicit user attachments: closest to their associated text.
            # Put 4/5 last so the model does not mistake older screens for newly captured user images.
            _ordered_images = (
                ([proactive_image] if proactive_image else [])
                + plugin_images
                + list(prefix_images)
                + list(own_images)
                + list(attachment_images)
            )
            # Source labels aligned positionally with the list above serve the frame bus only (model sees
            # identical bytes; labels never enter content). Calculate here rather than infer at publishing:
            # each image's bucket is certain now; after fitting only a string list remains.
            # Channel covers the first turn_source_count images only; missing count retains old semantics
            # (the whole section is turn frames, or the whole section is user attachments).
            _channelled = (
                0 if not turn_source
                else (
                    len(own_images) if turn_source_count is None
                    else max(0, min(int(turn_source_count), len(own_images)))
                )
            )
            _own_image_sources = (
                [turn_source] * _channelled
                + [_FRAME_SOURCE_USER] * (len(own_images) - _channelled)
            )
            _ordered_sources = (
                ([_FRAME_SOURCE_SCREEN] if proactive_image else [])
                + [_FRAME_SOURCE_PLUGIN] * len(plugin_images)
                + [_FRAME_SOURCE_PLUGIN] * len(prefix_images)
                # own_images are turn-supplied frames. In independent ASR handoff, first k images are
                # screen/camera captures, the rest merged user attachments: split labels by position
                # here instead of applying one channel to the entire section.
                + _own_image_sources
                + [_FRAME_SOURCE_USER] * len(attachment_images)
            )
            # Each source has separate image-count quotas (cannot spend another's allowance), but all enter
            # one HumanMessage, so the provider sees their sum: exceeding its per-request cap rejects
            # the whole request, rather than discarding a few images. Trim from the front: images nearest text
            # are what the user wants discussed.
            # Over-budget handling is staged, never silently discarding: first sample start/middle/end,
            # then compress if still oversized, then drop oldest only as last resort; notify users at every stage.
            # Providers reject oversized requests entirely, so something must yield; the order is
            # reduce redundancy, then quality, and only finally content.
            def _restore_consumed_queues() -> None:
                """Put both queues back when this turn dies before committing.

                The user's attachments were dequeued atomically above and the
                plugin list was cleared at read time, so from here until the
                message reaches history nothing else owns those bytes. A
                teardown landing on one of the awaits below would otherwise
                lose them from every future turn.
                """
                if attachment_images:
                    self._pending_images[0:0] = attachment_images
                if plugin_images:
                    _queue = getattr(self, "_pending_plugin_images", None)
                    if _queue is not None:
                        _queue[0:0] = plugin_images

            try:
                _attached_images, _budget_notice = await fit_images_to_turn_budget(
                    _ordered_images,
                    TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
                )
            except BaseException:
                _restore_consumed_queues()
                raise
            # Frame-bus labels correspond by index and remain valid only with unchanged counts: rung 0
            # normalization and recompression map images one-to-one; sampling/dropping change counts,
            # and outputs do not reveal their original indices. Fall back to unknown then: labeling
            # a plugin image "screen" is much worse than leaving its source unspecified.
            _attached_sources = (
                _ordered_sources
                if len(_attached_images) == len(_ordered_images)
                else [_FRAME_SOURCE_UNKNOWN] * len(_attached_images)
            )
            if _budget_notice:
                # Severity follows actual content loss, matching popup criteria. Rung 0 is unconditional,
                # so a dragged phone photo produces a notice each turn; warnings for all cases obscure
                # the difference between slightly smaller images and entire images not sent,
                # whereas the latter must stand out immediately during diagnosis.
                _budget_log = (
                    logger.warning
                    if _budget_notice.get("user_visible")
                    else logger.info
                )
                _budget_log(
                    "Turn images fitted for the %d-byte budget: %d -> %d image(s) "
                    "(normalized=%s sampled=%s compressed=%s dropped=%d)",
                    TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
                    _budget_notice["original_count"],
                    _budget_notice["final_count"],
                    _budget_notice.get("normalized"),
                    _budget_notice["sampled"],
                    _budget_notice["compressed"],
                    _budget_notice["dropped"],
                )
                # Log every case; popups only when whole images disappear, including dropping and sampling:
                # sampling keeps start/middle/end only, discarding whole intermediate images; users cannot
                # distinguish that from dropping. No popup for normalization/recompression: image remains smaller; rung 0
                # runs on almost every image turn; popping up for every notice would flood users with
                # "image adjusted" messages despite no meaningful loss in most cases.
                if _budget_notice.get("user_visible") and self.on_status_message:
                    try:
                        await self.on_status_message(json.dumps({
                            "code": "TURN_IMAGES_TRIMMED",
                            "details": _budget_notice,
                        }))
                    except asyncio.CancelledError:
                        _restore_consumed_queues()
                        raise
                    except Exception as _notice_error:
                        logger.warning(
                            "could not report the image trim to the user: %s",
                            _notice_error,
                        )
            for img_b64 in _attached_images:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_b64}"
                    }
                })

            # Add text (already includes system_prefix watermark when present).
            content.append({
                "type": "text",
                "text": _user_text_with_prefix,
            })

            user_message = HumanMessage(content=content)
            # Report what was ATTACHED, not what was staged. The trim above
            # takes a prefix, and the proactive screenshot is that prefix's
            # first element, so it survives exactly when nothing was dropped.
            _img_count = len(_attached_images)
            # Directly test membership in the final attached batch. Previously checking whether anything
            # was dropped approximated this only for dropping; the budget ladder now samples start/middle/end,
            # which can also omit the queue head. Membership is the accurate test.
            _proactive_attached = (
                bool(proactive_image)
                and proactive_image in _attached_images
            )
            logger.info(
                f"Sending multi-modal message with {_img_count} image(s)"
                f"{' (incl. proactive screen)' if _proactive_attached else ''}"
            )

            # Clear pending images after using them (content already holds the
            # data urls). The proactive screenshot is one-shot: consumed by this
            # reply, then cleared so it never re-injects into later turns.
            # User attachments were already atomically dequeued when snapshotted above; do not clear
            # the entire queue again, which would consume arrivals during the model call (the race this PR fixes).
            # The plugin list was likewise atomically cleared when read above (no await allowed between
            # reading and clearing); do not repeat here.
            # Clear the one-shot proactive screenshot here too.
            self._proactive_image_to_inject = None
            self._proactive_image_staged_at = 0.0
            self._proactive_image_history_len = 0
        else:
            # Text-only message (already includes system_prefix watermark when present).
            user_message = HumanMessage(content=_user_text_with_prefix)

        self._conversation_history.append(user_message)
        # The list this turn's user message went into: a cancelled reply is
        # dropped once history was replaced since (_commit_cancelled_reply).
        turn_history = self._conversation_history
        # Invocation-local committed flag. Callers cannot infer this from global history length:
        # concurrent text requests or finishing responses also append; growth does not prove this turn committed.
        if callable(on_turn_committed):
            on_turn_committed()
        history_replacement_index = len(self._conversation_history) - 1
        history_replacement_text = (
            str(history_replacement_text).strip()
            if history_replacement_text is not None
            else ""
        )
        if history_replacement_text and _prefix_clean:
            history_replacement_text = f"{_prefix_clean}\n\n{history_replacement_text}"
        # Pending frame-bus snapshot; None means no frames (text-only turn) or already published.
        _pending_bus_frames = None
        if has_images:
            self._evict_old_images()
            # Store actual outgoing frames here first, then copy to the plugin bus only after confirmed delivery.
            # Entering _conversation_history does not prove provider receipt: input transcript callbacks
            # can raise, users can cancel before sending, and all three attempts may fail,
            # delivering no bytes to the provider. Publishing here would falsely announce
            # delivery that never happened. Actual publishing occurs below at first chunk arrival,
            # symmetrically with realtime's ``if sent:``.
            #
            # Store _attached_images: post-fit bytes, not original caller images.
            # Normalization re-encodes almost every turn; bus images must match what the model actually saw.
            _pending_bus_frames = (_attached_images, _attached_sources)

        # Callback for user input
        transcript_callback = input_transcript_callback or self.on_input_transcript
        if transcript_callback:
            await transcript_callback(text.strip())

        # Retry policy: two retries, after 1 second and 2 seconds
        max_retries = 3
        retry_delays = [1, 2]
        assistant_message = ""        # Only final unpersisted text segment (final-segment).
        assistant_message_total = ""  # Whole-turn accumulation (including pre-tool); whole-turn decisions use this.
        status_reported = False
        guard_exhausted = False
        # The task itself was cancelled (close(), a torn-down transcript
        # worker) with the generation still live: the model was cut off, not
        # silent, so no LLM_NO_RESPONSE.
        task_cancelled = False
        # Set while a cancellation of this task would skip a cancelled-reply
        # commit: from the stream loop through the end-of-stream flush and the
        # summary epilogue (the summary call, the tail or summary sent to
        # TTS), each of which emits text before this turn commits. Cleared
        # right before every commit, so an await after one (the repetition
        # check) never writes the reply twice, and before every guard
        # decision, whose text is either committed there or discarded. Every
        # await while it is set leaves in ``assistant_message`` the text shown
        # so far, which is what this turn would commit after it; a summary
        # replaces the UI-only tail only once its TTS send has returned. See
        # the except below. ``segment_round`` is the tool round the last
        # sentinel reported.
        cut_keeps_shown = False
        segment_round = None
        # Reset empty-completion diagnostics per turn, avoiding stale values from prior turns.
        self._last_finish_reason = None
        self._last_block_reason = None
        self._last_prompt_tokens = None

        # Unit tests mock OmniOfflineClient directly via __new__, bypassing __init__, so
        # ``enable_long_response_summary`` may not exist. Take one local snapshot here
        # to avoid repeated getattr and prevent external runtime property changes partially activating
        # the state machine.
        summary_mode_enabled = bool(getattr(self, "enable_long_response_summary", False))

        # Summary mode temporarily raises API budget to _SUMMARY_API_BUDGET_FLOOR, letting the model
        # finish and providing a post-cutover tail to summarize (normal budgets stop the model
        # near budget, leaving no tail). Bump only within stream_text and restore in finally,
        # avoiding leakage to prompt_ephemeral sharing self.llm (proactive lacks a length guard
        # and would produce very long replies at 3000). Snapshot and exactly restore the original,
        # supporting runtime update_max_response_length budget changes.
        _summary_prev_max_tokens = None
        if summary_mode_enabled and getattr(self, "llm", None) is not None:
            _summary_prev_max_tokens = self.llm.max_completion_tokens
            self.llm.max_completion_tokens = _budget_to_max_tokens(
                self.max_response_length, summary_mode=True,
            )

        response_generation = self._begin_response_generation(owner=reply_owner)
        # This turn's tool-image slots survive attempts. See _astream_visible_with_tools:
        # releasing in its inner finally would replace pixels with placeholders after a retryable failure,
        # yet retries reuse the same history.
        _turn_tool_image_slots: list = []
        # Same rationale for the other half: staged tool frames awaiting bus copies must survive attempts,
        # otherwise a successful retry lets the model see images that plugins cannot read.
        _turn_tool_bus_frames: list = []
        # The tool rounds this turn appended, across attempts, so a cancelled
        # turn trims only its own (``_last_tool_round_of``).
        _turn_tool_rounds: list = []
        # This turn's resident blocks initially belong only to it, surviving for later turns after provider response;
        # bind before try so finally can access them.
        _resident_entry = None
        _ephemeral_context = getattr(turn_context, "ephemeral", None)
        if _ephemeral_context is not None:
            # Internal marker (not sent to provider): request views must not treat this as the user's turn.
            _mark_turn_context(_ephemeral_context, "ephemeral")
        try:
            _resident_entry = self._register_resident_context(user_message, turn_context)
            # A displaced reply's frontend notice goes out before this reply
            # sends anything (see _begin_response_generation).
            await self._run_displaced_followup()
            reroll_count = 0
            set_call_type("conversation")

            # Defensive check: ensure conversation history contains at least one user message.
            has_user_message = any(isinstance(msg, HumanMessage) for msg in self._conversation_history)
            if not has_user_message:
                error_msg = "对话历史中没有用户消息，无法生成回复"
                logger.error(f"OmniOfflineClient: {error_msg}")
                if self.on_status_message:
                    await self.on_status_message(json.dumps({"code": "NO_USER_MESSAGE"}))
                    status_reported = True
                return
            for attempt in range(max_retries):
                # close() alone sets self.llm to None. If it runs during retry sleep after an earlier
                # APIConnectionError (user changes mode / disconnects / session circuit breaker trips),
                # stop retrying; otherwise the reroll while below resets _is_responding to True,
                # then calls .astream on None,
                # raising NoneType.astream AttributeError.
                # Likewise, cancel_response() / handle_interruption() may set _is_responding=False
                # during retry sleep (user interruption); do not start another attempt,
                # since reroll while unconditionally resets True and
                # silently ignores cancellation. Match prompt_ephemeral's guard.
                # Guard with hasattr: unit tests bypassing __init__ via __new__ lack this property,
                # while production __init__ always sets it; distinguish uninitialized test stubs
                # from closed production instances.
                if (
                    (hasattr(self, "llm") and self.llm is None)
                    or not self._response_generation_is_active(response_generation)
                ):
                    logger.info("OmniOfflineClient.stream_text: client 已 close 或响应已被取消，终止 retry")
                    # Mark status_reported to suppress finally's LLM_NO_RESPONSE fallback:
                    # user cancel/close is not an LLM failure and must not display a red frontend error.
                    # No status to send here; the flag only tells finally to skip.
                    status_reported = True
                    break
                try:
                    assistant_message = ""
                    assistant_message_total = ""
                    guard_attempt = 0
                    # Telemetry: TTFT (time to first token), measured from here to first chunk arrival,
                    # a key signal for "too slow" D1 churn. Record once (first attempt,
                    # first chunk); rerolls do not reset it, reflecting actual user waiting time.
                    _ttft_start = time.time()
                    _ttft_recorded = False
                    while guard_attempt <= self.max_response_rerolls:
                        if not self._resume_response_generation(response_generation):
                            status_reported = True
                            break
                        tail = getattr(reply_owner, "reply_tail", None)
                        if tail is not None:
                            tail.discard_attempt()
                        assistant_message = ""           # Final unpersisted text segment only, used for final AIMessage append.
                        assistant_message_total = ""     # Whole-turn accumulation for _check_repetition / length guard.
                        is_first_chunk = True
                        pipe_count = 0  # Fence: count occurrences of | characters.
                        fence_triggered = False  # Whether the fence triggered.
                        guard_triggered = False
                        discard_reason = None
                        length_guard_recovery_text = ""
                        length_guard_persisted_prefix = ""
                        length_guard_original_tokens = 0
                        chunk_usage = None
                        prefix_buffer = ""
                        prefix_checked = not bool(self._prefix_buffer_size)

                        # ── Summary-mode state machine (active only when summary_mode_enabled) ──
                        # Complete transition diagram combines logic spread over four locations (initialization here /
                        # length trigger / emit fork / stream-end epilogue):
                        #
                        #   idle ──(_total exceeds budget)──▶ pending_cutover
                        #     │                                 │
                        #     │                  (find terminator after budget)
                        #     │                                 ▼
                        #     │                           cutover_done
                        #     │                          /     │      \
                        #     │           (tail accumulates gibberish)/      │       \(stream ends)
                        #     │                       /        │        \
                        #     │           gibberish_fallback   │   epilogue decision:
                        #     │           (silent truncation,  │   · final<budget+slack → abandon, continue tail TTS
                        #     │            history keeps prefix only) │   · otherwise → small-model summary TTS (abandon on failure)
                        #     │                                 │
                        #     └─(stream ends idle / pending)──┴─▶ normal: full original text enters history
                        #
                        # Emit destinations per state:
                        #   idle / pending_cutover  → UI + TTS（both）
                        #   cutover_done            → UI only (accumulate tail in summary_tail_buffer)
                        # When tool_round_persisted triggers, first abandon the post-cutover tail to
                        # TTS (otherwise it is never heard), then reset all states to idle.
                        summary_state = 'idle'
                        summary_prefix_for_history = ""  # assistant_message snapshot at cutover trigger.
                        summary_tail_buffer = ""        # Post-cutover UI-only text.
                        summary_next_gibberish_check = _SUMMARY_GIBBERISH_RECHECK_TOKENS
                        summary_trigger_tokens = 0     # _total when summary triggers (logging only).
                        # Character offset of budget crossing: if the model yields one huge chunk
                        # (multiple sentences/clauses), terminators before budget do not count;
                        # search for terminators from this point. Consume/reset to zero immediately after
                        # the trigger chunk; scan subsequent chunks from their start (entirely past budget).
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
                        # Focus mode: thinking_on threads ``extra_body=None``.
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
                            base_max_tokens=(
                                getattr(getattr(self, "llm", None), "max_completion_tokens", None)
                            ),
                        )
                        # Focus mode: leak-prone models (qwen3.5/3.6/3.7 hybrids).
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
                        # Tool images published on the bus carry this turn's turn_id, grouping them with
                        # its user frames; ordinary text turns lack turn_id, so it is None there.
                        _focus_overrides["_tool_frames_turn_id"] = turn_id
                        cut_keeps_shown = True
                        async for chunk in self._astream_visible_with_tools(
                            self._conversation_history,
                            _tool_image_slots=_turn_tool_image_slots,
                            _tool_bus_frames=_turn_tool_bus_frames,
                            _tool_rounds=_turn_tool_rounds,
                            _response_generation=response_generation,
                            _instruction=_ephemeral_context,
                            **_focus_overrides,
                        ):
                            # The empty chunk a cancelled tool loop hands up is
                            # not a token: it must not set the TTFT metric.
                            if not _ttft_recorded and not getattr(chunk, "_answered_ack", False):
                                _ttft_recorded = True
                                try:
                                    from utils.instrument import histogram as _instr_h
                                    _instr_h("llm_ttft_ms", max(0.0, (time.time() - _ttft_start) * 1000.0))
                                except Exception:
                                    # Best-effort telemetry must never interrupt the main streaming response path.
                                    pass
                            # Frame bus: provider output proves receipt of this turn's messages and images.
                            # This is the earliest point in this function where that can be asserted:
                            # astream is lazy and sends the request only on first __anext__;
                            # publishing earlier would be a guess. Clear the flag before await: publish once per turn,
                            # never copying the same images again across attempt retries or rerolls.
                            # At this same point, provider acceptance makes this turn's resident knowledge
                            # blocks survive in subsequent requests of this session.
                            if _resident_entry is not None and not _resident_entry.delivered:
                                self._settle_resident_context(_resident_entry, delivered=True)
                            if _pending_bus_frames is not None:
                                _bus_images, _bus_sources = _pending_bus_frames
                                _pending_bus_frames = None
                                # Publish without waiting. This hop may cross loops (see
                                # _fire_bus_task), and this is the user's first response chunk:
                                # a stalled bridge must not produce a silent turn.
                                # Bytes and labels to copy were already frozen above;
                                # the task reads no live state.
                                self._fire_bus_task(
                                    self._publish_provider_frames(
                                        _bus_images,
                                        _bus_sources,
                                        turn_id=turn_id,
                                    )
                                )
                            if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                                chunk_usage = chunk.usage_metadata
                                logger.debug(f"🔍 [Usage] {chunk_usage}")
                            if hasattr(chunk, 'response_metadata') and chunk.response_metadata:
                                if 'token_usage' in chunk.response_metadata or 'usage' in chunk.response_metadata:
                                    logger.debug(f"🔍 [Meta] {chunk.response_metadata}")
                            # Tool-turn sentinel: ``_astream_*_with_tools`` has already
                            # written pre-tool text + tool_calls + tool result inline
                            # to history. Reset final-segment buffer so
                            # the later appended AIMessage does not write the same pre-tool text
                            # to history twice. Do not reset ``_total``: repetition detection
                            # and token-length guard still need the full turn's actual text volume.
                            if getattr(chunk, "tool_round_persisted", False):
                                # A cancelled round still reports what it kept
                                # (see _settle_unfinished_tool_round); reset the
                                # state below but emit nothing further, and keep
                                # only the text that reached UI/TTS in it. A
                                # round whose turn left history went with it,
                                # its text included.
                                _round_cancelled = (
                                    self._active_response_generation != response_generation
                                )
                                if _round_cancelled:
                                    self._trim_cancelled_round_text(
                                        assistant_message, _turn_tool_rounds,
                                    )
                                length_guard_persisted_prefix = assistant_message_total
                                assistant_message = ""
                                segment_round = self._last_tool_round_of(
                                    _turn_tool_rounds,
                                )
                                # Reset fence / prefix buffer: the next segment is a new semantic
                                # unit (model text based on tool results) and must not
                                # reuse the preceding fence / prefix state.
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
                                    if (
                                        not _round_cancelled
                                        and _pretool_residual and _pretool_residual.strip()
                                        and self.on_text_delta
                                    ):
                                        await self.on_text_delta(_pretool_residual, is_first_chunk)
                                        is_first_chunk = False
                                    think_stripper.reset()
                                # Finalize summary state: post-cutover tail was emitted UI-only,
                                # not yet heard by TTS. At a tool boundary, post-tool segment length
                                # is unknown, so the "final < max+slack" decision
                                # cannot apply. Always abandon, sending the tail to TTS
                                # to finish reading the original. `_astream_*_with_tools` already persisted
                                # full pre-tool text including tail in assistant.tool_calls.content
                                # within _conversation_history, aligning UI/TTS/history.
                                # Then reset state so the post-tool segment
                                # starts again from idle.
                                if (
                                    # Re-read: the residual send above may have cut it.
                                    self._active_response_generation == response_generation
                                    and summary_mode_enabled
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
                            if not self._response_generation_is_active(response_generation):
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

                                # ── Prefix detection: buffer initial output and detect character-name prefixes ──
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
                                            self._pause_response_generation(response_generation)
                                            break
                                        elif lanlan_match:
                                            logger.info(f"OmniOfflineClient: 剥离角色名前缀 '{prefix_buffer[:lanlan_match]}'")
                                            truncated_content = prefix_buffer[lanlan_match:]
                                        else:
                                            truncated_content = prefix_buffer
                                        # Prefix parsing complete; pass result into the generic emit/guard path below.
                                        if not (truncated_content and truncated_content.strip()):
                                            continue
                                    else:
                                        continue  # Buffer not full; await more chunks.

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
                                        # Length guard counts tokens for the entire turn, including pre-tool.
                                        # Trim this chunk before on_text_delta; otherwise UI/TTS receive the over-budget
                                        # tail first, while history saves only truncated text.
                                        candidate_total = assistant_message_total + truncated_content
                                        current_length = count_tokens(candidate_total)
                                        if current_length > self.max_response_length:
                                            if summary_mode_enabled:
                                                # Summary path: long but readable → no abort or inline truncation.
                                                # First crossing changes state to pending_cutover; starting with this chunk,
                                                # search every chunk for a terminator (including commas), then cut over when found.
                                                # Leave guard_triggered unset; stream continues to natural completion/3000 cap,
                                                # with stream-end epilogue deciding abandon / summarize.
                                                if summary_state == 'idle':
                                                    summary_state = 'pending_cutover'
                                                    summary_trigger_tokens = current_length
                                                    # Calculate the character offset just past budget within this chunk:
                                                    # truncate_to_tokens trims candidate_total to budget;
                                                    # the length difference gives the crossing position in this chunk. The emit fork
                                                    # below searches for terminators from that offset, avoiding
                                                    # early pre-budget commas as cutover points.
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
                                                # Keep emit_content as the original chunk; continue through emit-split below.
                                            else:
                                                guard_triggered = True
                                                discard_reason = f"length>{self.max_response_length}"
                                                length_guard_original_tokens = current_length
                                                logger.info(f"OmniOfflineClient: 检测到长回复 ({current_length} tokens)，准备停止生成")
                                                self._pause_response_generation(response_generation)
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
                                                            # Already streamed prefixes cannot be retracted; align history with
                                                            # UI/TTS to avoid divergent visible text and context.
                                                            length_guard_recovery_text = assistant_message_total

                                    if emit_content and emit_content.strip():
                                        # Emit fork: summary mode splits UI / TTS paths at
                                        # the cutover boundary. All other cases (including summary_state == 'idle'
                                        # and 'gibberish_fallback') use the default both path.
                                        if (
                                            summary_mode_enabled
                                            and summary_state in ('pending_cutover', 'cutover_done')
                                        ):
                                            if summary_state == 'pending_cutover':
                                                # offset > 0 in the trigger chunk means crossing occurred mid-chunk;
                                                # terminator search starts there. Later chunks lie entirely past
                                                # budget; reset offset to zero and scan from the start. Consume once.
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
                                                    # Lock cutover: current assistant_message is the prefix.
                                                    summary_prefix_for_history = assistant_message
                                                    summary_state = 'cutover_done'
                                                    logger.info(
                                                        "OmniOfflineClient summary: cutover 完成 "
                                                        "(prefix_chars=%d, trigger=%d tokens)",
                                                        len(assistant_message), summary_trigger_tokens,
                                                    )
                                                    # The await before it may have cut the reply:
                                                    # its remaining half is never shown.
                                                    if post and self._active_response_generation == response_generation:
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
                                                    # No terminator found → emit entire segment to both, state unchanged.
                                                    assistant_message += emit_content
                                                    assistant_message_total += emit_content
                                                    if self.on_text_delta:
                                                        await self.on_text_delta(emit_content, is_first_chunk)
                                                    is_first_chunk = False
                                            else:
                                                # cutover_done: UI only, accumulating in tail buffer.
                                                assistant_message += emit_content
                                                assistant_message_total += emit_content
                                                summary_tail_buffer += emit_content
                                                if self.on_text_delta:
                                                    await self.on_text_delta(
                                                        emit_content, is_first_chunk,
                                                        ui_enabled=True, tts_enabled=False,
                                                    )
                                                is_first_chunk = False

                                            # Recheck gibberish after cutover_done (including pending→done
                                            # within the same chunk): every _SUMMARY_GIBBERISH_RECHECK_TOKENS
                                            # tail tokens, recheck; on detection switch to fallback for epilogue handling.
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
                                                        self._pause_response_generation(response_generation)
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
                                        # Leave guard_triggered unset so epilogue takes the summary-fallback path.
                                        break
                            elif content and not content.strip():
                                logger.debug(f"OmniOfflineClient: 过滤空白内容 - content_repr: {repr(content)[:100]}")

                        # A guard pause still owns this generation. Cancellation
                        # or replacement does not: discard every un-emitted
                        # buffer (name prefix, think residual, summary epilogue)
                        # but keep what already reached UI/TTS, or the next
                        # request would not see what the user just saw.
                        if self._active_response_generation != response_generation:
                            cut_keeps_shown = False
                            self._commit_cancelled_reply(
                                user_message, AIMessage(content=assistant_message),
                                response_generation, turn_history=turn_history,
                            )
                            break

                        # After stream ends, flush thinking-stripper residue first. Only thinking_on turns
                        # for leaky providers install it; if no </think> appears all turn
                        # (model did not think), it holds everything. Return buffered body to
                        # prefix_buffer for generic emit/guard below, avoiding answer loss.
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
                        # After stream ends: flush unprocessed prefix buffer through generic emit/guard.
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
                                        # Length guard counts the whole turn (including pre-tool), symmetric with accumulation above.
                                        candidate_total = assistant_message_total + flush_text
                                        current_length = count_tokens(candidate_total)
                                        if current_length > self.max_response_length:
                                            if summary_mode_enabled:
                                                # Symmetric with main accumulation: summary mode does not abort;
                                                # switch to pending_cutover for emit-split below.
                                                if summary_state == 'idle':
                                                    summary_state = 'pending_cutover'
                                                    summary_trigger_tokens = current_length
                                                    # Compute crossing character offset in flush_text, symmetric with main accumulation.
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
                                        # Emit fork (symmetric with main accumulation): summary mode splits UI/TTS at cutover.
                                        if (
                                            summary_mode_enabled
                                            and summary_state in ('pending_cutover', 'cutover_done')
                                        ):
                                            if summary_state == 'pending_cutover':
                                                # Symmetric with main accumulation: consume crossing offset from trigger flush.
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
                                                    # Symmetric with chunk-loop emit fork's cutover log:
                                                    # use summary_trigger_tokens (set at flush entry)
                                                    # to retain when the trigger occurred in logs.
                                                    logger.info(
                                                        "OmniOfflineClient summary: cutover 完成于 flush "
                                                        "(prefix_chars=%d, trigger=%d tokens)",
                                                        len(assistant_message), summary_trigger_tokens,
                                                    )
                                                    # The await before it may have cut the reply:
                                                    # its remaining half is never shown.
                                                    if post and self._active_response_generation == response_generation:
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

                        # The flush's sends are cancellation points too: a reply
                        # cut there sends no tail or summary under the turn
                        # that cut it, and keeps what was shown, like the check
                        # before the flush.
                        if self._active_response_generation != response_generation:
                            cut_keeps_shown = False
                            self._commit_cancelled_reply(
                                user_message, AIMessage(content=assistant_message),
                                response_generation, turn_history=turn_history,
                            )
                            break

                        if guard_triggered:
                            # Every way out of here either commits the
                            # recovery or discards the text (a retry, or a
                            # placeholder written by the caller).
                            cut_keeps_shown = False
                            guard_attempt += 1
                            reroll_count += 1
                            will_retry = guard_attempt <= self.max_response_rerolls

                            # Frontend max_attempts reports total attempts,
                            # not rerolls (which exclude initial attempt). Frontend attempt
                            # / max_attempts progress should advance 1/2 → 2/2.
                            total_attempts = self.max_response_rerolls + 1

                            recovery_text = length_guard_recovery_text
                            if discard_reason and "length>" in discard_reason:
                                # For long but readable responses, finalize using already emitted truncated text,
                                # without reroll, avoiding divergence between UI/TTS and history.
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
                                # The recovery may first be emitted by the
                                # end-of-stream flush above, a cancellation point.
                                _live_at_commit = self._commit_reply(
                                    user_message, history_recovery_text, response_generation,
                                    turn_history=turn_history,
                                )
                                self._mark_reply_tail_completed(
                                    reply_owner, _live_at_commit, recovery_text,
                                )
                                if _live_at_commit:
                                    await self._check_repetition(recovery_text)
                                assistant_message = history_recovery_text
                                guard_exhausted = True
                                break
                            recovery_text = ""

                            if will_retry:
                                # Retry remains: notify will_retry and continue. Frontend receives
                                # response_discarded(will_retry=True, message=None)
                                # and takes the retry-toast path.
                                await self._notify_response_discarded(
                                    discard_reason or "guard",
                                    guard_attempt,
                                    total_attempts,
                                    True,
                                    None,
                                    callback=discard_callback,
                                )
                                logger.info(
                                    "OmniOfflineClient: 响应被丢弃（%s），第 %d/%d 次重试",
                                    discard_reason, guard_attempt, total_attempts,
                                )
                                continue

                            # Rerolls exhausted. Excessive length has two cases:
                            #   (a) Verbose but normal speech → trim to the last
                            #       sentence-ending punctuation, send as RESPONSE_LENGTH_TRUNCATED
                            #       to frontend; history stores truncated text, not placeholder.
                            #   (b) Model malfunction (BPE repetitions / emoji flooding / continuous
                            #       unpunctuated gibberish) → do not extract a "sentence"; filter directly
                            #       as RESPONSE_TOO_LONG (failure semantics). Core makes frontend
                            #       display a failure placeholder and writes it
                            #       to history so the next LLM turn knows this turn failed.
                            #
                            # Case (b) triggers on _is_gibberish_response (punctuation/symbol
                            # density < 2% or > 60%) or no sentence ending (none of . ! ? 。 ！ ？ …).
                            #
                            # Crucial: case (a) must first hard-truncate assistant_message to
                            # max_response_length, then find sentence endings; otherwise the chosen ending
                            # can still exceed the token cap (e.g. final period at 950 tokens
                            # with a cap of 300).
                            if discard_reason and "length>" in discard_reason:
                                # Whole-turn decision: gibberish/truncation must inspect _total, otherwise
                                # the tool-turn sentinel clearing final-segment causes all pre-tool text
                                # to be ignored, incorrectly yielding RESPONSE_TOO_LONG despite a long turn.
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
                                # Use _notify_response_discarded, not
                                # on_status_message: frontend must recognize RESPONSE_LENGTH_TRUNCATED
                                # in the response_discarded branch to activate
                                # truncation UX (no input rollback; truncated text
                                # becomes placeholder body).
                                await self._notify_response_discarded(
                                    discard_reason or "guard",
                                    guard_attempt,
                                    total_attempts,
                                    False,
                                    truncate_msg,
                                    callback=discard_callback,
                                )
                                status_reported = True
                                # core.handle_response_discarded appends to _conversation_history
                                # in its RESPONSE_LENGTH_TRUNCATED branch
                                # (self.session is this OmniOfflineClient; both share the same
                                # _conversation_history list). Maintain only the internal
                                # repetition-detection list here.
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
                                callback=discard_callback,
                            )
                            status_reported = True
                            # Gibberish, no sentence ending, or non-length guard failure:
                            # take failure-placeholder path; core stores localized "fault"
                            # text in history, avoiding an empty assistant turn for the next LLM request.
                            logger.warning(
                                "OmniOfflineClient: guard 重试耗尽 (reason=%s)，"
                                "filter 输出走故障 placeholder",
                                discard_reason,
                            )
                            assistant_message = ""
                            guard_exhausted = True
                            break

                        # ── Summary mode epilogue ──
                        # guard_triggered must be False here (summary path never sets
                        # length guards). Decide from summary_state:
                        #   - gibberish_fallback: tail classified as gibberish; silently truncate.
                        #     Do not emit RESPONSE_INVALID: that would trigger core's
                        #     _clear_tts_pipeline, clearing unread prefix audio still queued
                        #     and truncating speech already underway. Here only
                        #     log and commit prefix to history; TTS naturally finishes
                        #     queued prefix audio. UI's gibberish tail is permitted
                        #     by the existing live ≠ reload design divergence.
                        #   - cutover_done + final length ≤ max+slack: too short to warrant summarizing;
                        #     continue tail directly through TTS and retain full original history.
                        #   - cutover_done + longer final length: small-model summary continues TTS;
                        #     history stores prefix+summary. Summary failure falls back to reading tail.
                        #   - pending_cutover: triggered but no terminator before stream end
                        #     (no punctuation). Main path already emitted all tail, effectively no summary;
                        #     store full original text in history.
                        #   - idle: never triggered; normal flow, nothing to do.
                        if summary_mode_enabled and summary_state == 'gibberish_fallback':
                            logger.warning(
                                "OmniOfflineClient summary: gibberish fallback, "
                                "静默 commit prefix (%d chars) 到 history，TTS 残队列保留",
                                len(summary_prefix_for_history),
                            )
                            # Repetition detection checks prefix only (what actually enters history / TTS).
                            # assistant_message_total would add the discarded gibberish tail
                            # to _recent_responses, contaminating later repetition decisions.
                            cut_keeps_shown = False
                            _live_at_commit = self._commit_reply(
                                user_message, summary_prefix_for_history, response_generation,
                                turn_history=turn_history,
                            )
                            self._mark_reply_tail_completed(
                                reply_owner, _live_at_commit, assistant_message_total,
                            )
                            if _live_at_commit and summary_prefix_for_history:
                                await self._check_repetition(summary_prefix_for_history)
                            assistant_message = ""
                            guard_exhausted = True
                            break

                        if summary_mode_enabled and summary_state == 'cutover_done':
                            final_tokens = count_tokens(assistant_message_total)
                            slack_threshold = self.max_response_length + _SUMMARY_LATE_FINISH_SLACK
                            if final_tokens < slack_threshold:
                                # Tail too short: abandon summary and continue tail directly to TTS.
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
                                # The summary call is a cancellation point of its
                                # own: a turn cancelled while it ran must not
                                # reach TTS or commit prefix + summary. Keep what
                                # the UI already shows, like the check above.
                                if self._active_response_generation != response_generation:
                                    cut_keeps_shown = False
                                    self._commit_cancelled_reply(
                                        user_message, AIMessage(content=assistant_message),
                                        response_generation, turn_history=turn_history,
                                    )
                                    break
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
                                    # history = prefix + summary, matching TTS output.
                                    # Only once the send has returned: a cut while
                                    # it waits (for the TTS cache lock) has not
                                    # queued the summary, and keeps the shown text
                                    # like a cut in the summary call.
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
                                    # Leave assistant_message unchanged → history stores full original text.

                        # _AsyncStreamWrapper hook automatically records token usage at stream end;
                        # do not manually call TokenTracker.record() here and double count.

                        # Final AIMessage stores only the final segment not already persisted inline
                        # (pre-tool text is already in ``assistant.tool_calls.content``;
                        # appending again would duplicate history).
                        cut_keeps_shown = False
                        _live_at_commit = self._commit_reply(
                            user_message, assistant_message, response_generation,
                            turn_history=turn_history,
                        )
                        self._mark_reply_tail_completed(
                            reply_owner, _live_at_commit, assistant_message_total,
                        )
                        # Repetition detection checks all turn text (including pre-tool), matching
                        # the human user's perception of what the AI said this turn.
                        if assistant_message_total and _live_at_commit:
                            await self._check_repetition(assistant_message_total)
                        break

                    if guard_exhausted:
                        break

                    # Whole-turn decision: any generated text (including pre-tool) completes
                    # the retry loop successfully. final-segment would incorrectly retry turns where
                    # max_tool_iterations exhausted after persisting pre-tool text without a final reply.
                    if assistant_message_total:
                        break

                except _llm_retry_error_types() as e:
                    # A failed stream's text is discarded below, never kept.
                    cut_keeps_shown = False
                    from openai import InternalServerError

                    error_type = type(e).__name__
                    error_str_lower = str(e).lower()
                    is_internal_error = isinstance(e, InternalServerError)
                    logger.info(f"ℹ️ 捕获到 {error_type} 错误")

                    def _count_llm_error(api_key_rejected: bool = False):
                        # D1 failure diagnostics: terminal typed API errors (connection/auth/rate limit/billing/quota/
                        # rejected key) also count as llm_error. Call only on break paths above,
                        # never retry-continue (ongoing retries are not failures). Generic
                        # except is mutually exclusive with this block, avoiding double counting (Codex).
                        try:
                            from utils.instrument import counter as _ic
                            # before_first_loop: error before the user experiences the core loop means
                            # initial-experience barrier churn (spoke but got no reply). Low-cardinality true/false/unknown
                            # distinguishes first-experience blockage from errors after prior usage in D1 churn.
                            try:
                                from utils.token_tracker import TokenTracker as _TT
                                _bfl = "false" if _TT.get_instance().has_completed_core_loop() else "true"
                            except Exception:
                                _bfl = "unknown"
                            _ic("llm_error", error_class=error_type[:48], before_first_loop=_bfl)
                            if api_key_rejected:
                                _ic("api_key_invalid", before_first_loop=_bfl)
                        except Exception:
                            # Best-effort telemetry must never affect error reporting / retry flow.
                            pass

                    # Report billing/API-key errors immediately and terminate; report quota errors but keep retrying.
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
                        # Whole-turn decision: whether any text reached frontend this turn. Use _total to
                        # cover tool_round_persisted resetting final-segment;
                        # otherwise pre-tool text remains visible without triggering notify_discarded.
                        if assistant_message_total and discard_callback:
                            await self._notify_response_discarded(
                                f"api_error:{error_type}",
                                attempt + 1,
                                max_retries,
                                will_retry=True,
                                message=None,
                                callback=discard_callback,
                            )
                        assistant_message = ""
                        assistant_message_total = ""
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        error_msg = f"💥 LLM连接失败（{error_type}），已重试{max_retries}次: {e}"
                        logger.error(error_msg)
                        _count_llm_error()  # Retries exhausted = terminal failure, count as llm_error.
                        if self.on_status_message:
                            if is_internal_error:
                                await self.on_status_message(json.dumps({"code": "LLM_UPSTREAM_ERROR"}))
                            else:
                                await self.on_status_message(json.dumps({"code": "LLM_CONNECTION_EXHAUSTED", "details": {"error_type": error_type, "max_retries": max_retries, "error": str(e)}}))
                            status_reported = True
                        break
                except Exception as e:
                    cut_keeps_shown = False
                    is_api_key_rejected = _is_api_key_rejected_error(e)
                    # Telemetry: LLM call failures drive much D1 churn. Low-cardinality error_class
                    # uses exception class names; count api_key_invalid separately: misconfigured keys
                    # on day one commonly lose source-build users.
                    try:
                        from utils.instrument import counter as _instr_counter
                        # Match before_first_loop dimensions with typed-error path (_count_llm_error),
                        # preventing mixed llm_error/api_key_invalid labels from splitting D1 buckets.
                        try:
                            from utils.token_tracker import TokenTracker as _TT
                            _bfl = "false" if _TT.get_instance().has_completed_core_loop() else "true"
                        except Exception:
                            _bfl = "unknown"
                        _instr_counter("llm_error", error_class=type(e).__name__[:48], before_first_loop=_bfl)
                        if is_api_key_rejected:
                            _instr_counter("api_key_invalid", before_first_loop=_bfl)
                    except Exception:
                        # Best-effort telemetry never masks/interrupts original LLM error handling.
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
                    # If this turn already emitted text to frontend (typically genai path
                    # _astream_with_tools emits text, then raises transient/tools-unsupported
                    # explicitly to this level), notify frontend to clear
                    # the partial bubble, or interrupted text remains visible
                    # forever. Symmetric with APIConnectionError and similar branches, but
                    # this path already decided against retrying (break below), so
                    # ``will_retry=False``, with a readable frontend error code.
                    # Whole-turn decision: _total covers tool_round_persisted resetting
                    # final-segment while pre-tool text remains in frontend.
                    if assistant_message_total and discard_callback:
                        try:
                            await self._notify_response_discarded(
                                f"text_gen_error:{type(e).__name__}",
                                attempt + 1,
                                max_retries,
                                will_retry=False,
                                message=json.dumps(discard_error_payload),
                                callback=discard_callback,
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
        except asyncio.CancelledError:
            # The independent-ASR child task is cancelled outright: by
            # handle_interruption once it has taken this reply over, or with
            # the reply still live by close() or a cancelled voice turn (its
            # completion then still runs). Either way the commit this turn
            # was heading for (after the stream loop, the end-of-stream flush
            # or the summary epilogue) never runs: keep what was shown, as it
            # would have, but never a summary its send had not yet queued.
            if cut_keeps_shown:
                self._keep_shown_text_of_cut_stream(
                    user_message, AIMessage(content=assistant_message), segment_round,
                    response_generation, _turn_tool_rounds,
                    turn_history=turn_history,
                )
            task_cancelled = True
            raise
        finally:
            # Before other cleanup, remove base64 from history so subsequent requests do not
            # resend it (token counter uses short image placeholders, invisible to truncation).
            response_cancelled = self._active_response_generation != response_generation
            interrupter_owned = self._take_interrupter_ownership(response_generation)
            self._release_tool_image_slots(_turn_tool_image_slots)
            self._finish_response_generation(response_generation)
            # Do not retain resident blocks without provider response: request unsent, interrupted
            # before text, or displaced by a newer message. A tool round proves provider response.
            if _resident_entry is not None and not _resident_entry.delivered:
                self._settle_resident_context(
                    _resident_entry, delivered=bool(_turn_tool_rounds),
                )

            if history_replacement_text:
                # The index is a hint: a concurrent turn's cancelled tool round
                # or reply commit may have shifted this message. Identity is
                # the only proof it is still ours.
                _history = self._conversation_history
                _replace_at = _find_by_identity(
                    _history, history_replacement_index, user_message,
                )
                if _replace_at >= 0:
                    _history[_replace_at] = HumanMessage(content=history_replacement_text)
                    # This turn's resident knowledge blocks follow the replacement message.
                    self._rebind_resident_anchor(user_message, _history[_replace_at])

            # Restore API budget temporarily raised by summary mode, preventing leakage to prompt_ephemeral.
            if _summary_prev_max_tokens is not None and getattr(self, "llm", None) is not None:
                self.llm.max_completion_tokens = _summary_prev_max_tokens

            # The status await below is the one window in which an interruption
            # (or a displacing user reply) can claim this finished turn's
            # completion. Mark it only for that window and always take the mark
            # back, or a raising status send leaves it behind for the next,
            # unrelated interruption to claim. A turn taken over mid-reply
            # never marks, so its take reports it as taken over too.
            if not interrupter_owned:
                self._mark_completion_pending(response_generation, owner=reply_owner)
            try:
                # Whole-turn decision: LLM_NO_RESPONSE only when all attempts produced no text, including pre-tool.
                # final-segment would falsely report tool turns completed without final model text.
                if (
                    not response_cancelled
                    and not task_cancelled
                    and not assistant_message_total
                    and not guard_exhausted
                    and not status_reported
                ):
                    # Include the last attempt's finish_reason / block_reason / prompt_tokens
                    # in warning. When Gemini-via-OpenAI-compatible silently returns empty (safety /
                    # recitation / max_tokens / context overflow), this log provides the only
                    # available clue explaining why it was empty.
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
            finally:
                completion_taken_over = not self._take_completion(response_generation)

            # Call response done callback (the caller's bound one, else the
            # session's). Skipped exactly when someone else took the close
            # over: an interrupter (mid-reply, or a claim during the status
            # await above) or a user reply that displaced this one (see
            # _begin_response_generation). A close() still runs it.
            done_callback = response_done_callback or self.on_response_done
            if not completion_taken_over and done_callback:
                await done_callback()
