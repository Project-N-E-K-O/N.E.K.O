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
    Callable,
    HumanMessage,
    Optional,
    SystemMessage,
    _is_api_key_rejected_error,
    _llm_retry_error_types,
    _strip_nonverbal_directives,
    asyncio,
    json,
    logger,
    set_call_type,
)


class _LifecycleMixin:
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

    async def prompt_ephemeral(
        self,
        instruction: str,
        *,
        images: Optional[list] = None,
        completion_mode: str = "proactive",
        persist_response: bool = True,
        on_committed: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Send a fire-and-forget instruction to the LLM and stream the response.

        The *instruction* (typically wrapped in ``======...======`` delimiters)
        is appended as a temporary HumanMessage for this single LLM call
        but is **not** persisted to ``_conversation_history``.  The
        AI's natural-language response (AIMessage) is kept in history only
        when ``persist_response`` is True.

        This is the correct channel for agent task notifications, greeting
        nudges, and any scenario where the AI should respond to a stage
        direction that must not pollute long-term context.

        Unlike ``prime_context`` (appends to system prompt, session start)
        and ``create_response`` (persistent HumanMessage), the instruction
        here is truly ephemeral — it exists only for the duration of this
        single LLM inference call.

        Completion behaviour is caller-selectable:

        - ``completion_mode="proactive"``:
          Uses ``on_proactive_done(content_committed)`` when available.
          This keeps the existing lightweight proactive / agent-callback
          completion path while exposing whether any content was actually
          emitted.
        - ``completion_mode="response"``:
          Uses ``on_response_done()`` so the reply goes through the
          regular user-visible completion path while still keeping the
          injected instruction itself ephemeral.
        - ``on_committed``:
          Called after visible text is confirmed but before completion
          callbacks flush proactive state.

        Returns True if any user-visible text was generated, False if aborted
        or only nonverbal directives were emitted.
        """
        if not instruction or not instruction.strip():
            return False

        # 临时注入：instruction 已由调用方用 ======== 格式封装，作为 HumanMessage 发送，
        # 不持久化到 _conversation_history，避免污染长期上下文。
        # Proactive media is passed EXPLICITLY via ``images`` (per-callback,
        # carried on cb.media_images by the caller) — it is NOT pulled from
        # self._pending_images. _pending_images is the USER's screen/camera
        # staging queue for the next stream_text; consuming it here would steal
        # the user's pending frame into this proactive/greeting turn and rob the
        # user's next message of its visual context (Codex P2). When proactive
        # images are present we switch to the vision model exactly like
        # stream_text does (一旦带图就永久切 vision — 既定设计；vision model 也能跑
        # 后续纯文本轮). The instruction itself stays ephemeral (not persisted).
        if images:
            # 一旦带图就永久切到 vision model（既定设计，见上）。vision model 也能
            # 跑后续纯文本轮，且凝神不再因 vision 而关闭思考。
            if self.vision_model and self.vision_model != self.model:
                logger.info(
                    f"🖼️ prompt_ephemeral: switching to vision model {self.vision_model} (from {self.model}) for proactive media"
                )
                await self.switch_model(self.vision_model, use_vision_config=True)
            _ephemeral_content: list = []
            for img_b64 in images:
                _ephemeral_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                })
            _ephemeral_content.append({"type": "text", "text": instruction})
            logger.info(f"prompt_ephemeral: attaching {len(images)} proactive image(s)")
            _ephemeral_msg = HumanMessage(content=_ephemeral_content)
        else:
            _ephemeral_msg = HumanMessage(content=instruction)
        messages_to_send = self._conversation_history + [_ephemeral_msg]

        # Retry 策略与 stream_text 对偶（max_retries=3, [1, 2]s 间隔）。
        # 但主动搭话语义不同：用户没在等回复，retry 用尽时**静默吞掉**，
        # 不发任何 status_message 给前端 —— 失败 = 这一轮 AI 根本没想说话。
        # 唯一例外：欠费 / API Key / 配额这类账户级错误必须上报，否则用户
        # 永远不知道为什么主动搭话不工作。
        max_retries = 3
        retry_delays = [1, 2]
        assistant_message = ""
        # Empty-completion 诊断重置：与 stream_text 对偶。
        self._last_finish_reason = None
        self._last_block_reason = None
        self._last_prompt_tokens = None
        # Open a new reasoning-pulse scope like stream_text does and capture the
        # ownership token: the finally clear below must fire ONLY for this turn's
        # own pulse, never for a newer user stream_text that interleaved and
        # re-pulsed under a fresher seq (Codex P2).
        _reasoning_owner_seq = self._begin_reasoning_stream()

        try:
            self._is_responding = True
            set_call_type("proactive")
            for attempt in range(max_retries):
                # 每次 attempt 重置流式状态（assistant_message / prefix /
                # is_first_chunk 全部归零）。
                assistant_message = ""
                is_first_chunk = True
                prefix_buffer = ""
                prefix_checked = not bool(self._prefix_buffer_size)
                emitted_any = False  # 本 attempt 是否已经向前端 emit 过文本

                # close() 是唯一会把 self.llm 设为 None 的路径。它若在前一次
                # APIConnectionError 后的 retry sleep 期间触发（用户切模式 /
                # 断连 / session 熔断），不再做这次 attempt —— 否则会对 None
                # 调 .astream 触发 AttributeError，且就算重试 client 也已不在。
                # 用 hasattr 守卫：单元测试用 __new__ 绕过 __init__ 不会设这个
                # 属性，但真实代码 __init__ 必设。
                if (hasattr(self, "llm") and self.llm is None) or not self._is_responding:
                    break

                try:
                    # 主动搭话同样走 tool-aware streaming —— agent 注入的 stage
                    # direction 也可能让模型决定调用工具（比如 "讲一下今天天气"）。
                    async for chunk in self._astream_visible_with_tools(messages_to_send):
                        if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                            logger.debug(f"🔍 [Usage-Proactive] {chunk.usage_metadata}")
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
                            emitted_any = True

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
                            emitted_any = True

                    break  # 流正常结束，跳出 retry 循环

                except _llm_retry_error_types() as e:
                    error_type = type(e).__name__
                    error_str_lower = str(e).lower()
                    logger.info(f"ℹ️ prompt_ephemeral 捕获到 {error_type} 错误")

                    # 账户级错误必须上报：欠费 / API Key 直接放弃 retry，
                    # 配额错误上报后继续 retry（与 stream_text 对偶）。
                    if '欠费' in error_str_lower or 'standing' in error_str_lower:
                        logger.error(f"prompt_ephemeral: 检测到欠费错误，直接上报: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_ARREARS"}))
                        assistant_message = ""
                        return False
                    elif _is_api_key_rejected_error(e):
                        logger.error(f"prompt_ephemeral: 检测到 API Key 错误，直接上报: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_KEY_REJECTED"}))
                        assistant_message = ""
                        return False
                    elif 'quota' in error_str_lower or 'time limit' in error_str_lower:
                        logger.warning(f"prompt_ephemeral: 检测到配额错误，上报前端: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_QUOTA_TIME"}))

                    # 已经吐过文本就不能再 retry —— 否则前端会拼出"半截 + 重新生成"
                    # 的怪异回复。直接 break 让半截文本走 finally 的 persist 路径。
                    if emitted_any:
                        logger.info(
                            "prompt_ephemeral: %s 发生时已 emit 文本，放弃 retry",
                            error_type,
                        )
                        break

                    if attempt < max_retries - 1:
                        wait_time = retry_delays[attempt]
                        logger.warning(
                            "prompt_ephemeral: LLM 调用失败 (尝试 %d/%d)，%d 秒后重试: %s",
                            attempt + 1, max_retries, wait_time, error_type,
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    # Retry 用尽：B 部分语义 —— 静默放弃。主动搭话失败用户
                    # 不需要知道，只 log 一条 warning（截断 str(e) 防 HTML
                    # 错误页淹没日志）。
                    logger.warning(
                        "prompt_ephemeral: %s 重试 %d 次后仍失败，静默放弃: %s",
                        error_type, max_retries, str(e)[:200],
                    )
                    assistant_message = ""
                    return False
        except Exception as e:
            if _is_api_key_rejected_error(e):
                logger.error(f"prompt_ephemeral: 检测到 API Key 错误，直接上报: {e}")
                if self.on_status_message:
                    await self.on_status_message(json.dumps({"code": "API_KEY_REJECTED"}))
                assistant_message = ""
                return False
            # 兜底：非 API 错误（编程错误 / 数据异常）静默吞掉，截断错误文本
            # 防 HTML 错误页之类淹没日志。和上方 (APIConnectionError 等) 分支
            # 语义对偶 —— 都不向前端发 status_message。
            logger.error(
                "OmniOfflineClient.prompt_ephemeral 未分类异常 %s: %s",
                type(e).__name__, str(e)[:200],
                exc_info=True,
            )
            assistant_message = ""
            return False
        finally:
            self._is_responding = False
            # Clear the thinking bubble if this proactive/greeting/avatar turn
            # pulsed it but committed no visible text — unlike stream_text, there
            # is no external unconditional clear bracketing this call (Codex P2).
            # Passing the owner seq suppresses the clear when a newer user turn
            # interleaved and re-pulsed. No-op when nothing pulsed or it was
            # already cleared on the first visible token (idempotent).
            await self._notify_reasoning_done(_reasoning_owner_seq)
            # Token usage 由 _AsyncStreamWrapper hook 在流结束时自动记录，
            # 此处不再手动调用 TokenTracker.record() 避免双重计数。
            committed_text = _strip_nonverbal_directives(assistant_message).strip()
            content_committed = bool(committed_text)
            # 一条可见的 ephemeral 回复（greeting / agent 回调 / 戳头像的 quip）是
            # 用户接下来要回应的「新一条 AI 轮」，它让之前为「下一条用户回复」暂存的
            # 屏幕截图过时——清掉它。persist_response=False 的回复（如头像 quip）不进
            # 历史、历史长度不变，stream_text 的 history-len marker 看不到，必须在这条
            # ephemeral 回复的 choke point 清（Codex P2）。只在真吐了可见文本时清，
            # 半途 abort / 无文本的尝试不丢一张仍有效的暂存屏。
            if content_committed:
                self._proactive_image_to_inject = None
                self._proactive_image_staged_at = 0.0
                self._proactive_image_history_len = 0
            # Empty-completion 诊断：和 stream_text 的兜底 warning 对偶。
            # 主动搭话语义上是"静默放弃"，所以不发 status_message，但 INFO
            # 一行 finish_reason 让日志能复盘——上次出问题就是因为没法区分
            # "trigger_greeting 静默失败 = LLM 被 safety 拦" vs "LLM 真的觉得
            # 这一轮不该说话"。
            if not content_committed:
                logger.info(
                    "OmniOfflineClient.prompt_ephemeral: 无可提交文本 "
                    "(finish_reason=%s block_reason=%s prompt_tokens=%s model=%s "
                    "completion_mode=%s)",
                    getattr(self, "_last_finish_reason", None),
                    getattr(self, "_last_block_reason", None),
                    getattr(self, "_last_prompt_tokens", None),
                    getattr(self, "model", None),
                    completion_mode,
                )
            elif on_committed:
                try:
                    on_committed()
                except Exception:
                    logger.exception("prompt_ephemeral on_committed callback failed")
            if content_committed and persist_response:
                self._conversation_history.append(AIMessage(content=assistant_message))
                # 防复读 corpus：只录常规 reply（completion_mode == "response"）。
                # proactive 路径已经在 ``core.finish_proactive_delivery`` 上录，
                # 这里再录会双写——这两条路径都接得到同一段 assistant 文本。
                if completion_mode == "response":
                    try:
                        from memory.anti_repeat import get_anti_repeat_corpus
                        get_anti_repeat_corpus().record_output(
                            self.lanlan_name, committed_text, is_proactive=False,
                        )
                    except Exception as _exc:  # pragma: no cover
                        logger.debug(
                            "[AntiRepeat] record reply skipped: %s", _exc,
                        )
            if completion_mode == "response":
                if self.on_response_done:
                    await self.on_response_done()
            else:
                proactive_done_cb = getattr(self, "on_proactive_done", None)
                if proactive_done_cb:
                    await proactive_done_cb(content_committed)
                elif self.on_response_done:
                    await self.on_response_done()

        return content_committed

    async def cancel_response(self) -> None:
        """Cancel the current response if possible"""
        self._is_responding = False

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
        self._proactive_image_to_inject = None
        self._proactive_image_staged_at = 0.0
        self._proactive_image_history_len = 0
        if self.llm:
            try:
                await self.llm.aclose()
            except Exception as e:
                logger.warning(f"OmniOfflineClient.close: aclose failed: {e}")
            self.llm = None
        # 同 switch_model：genai.Client 持有 httpx 连接池，关掉它的
        # 同步 close()（SDK 没暴露 aclose，放 to_thread 不阻事件循环）。
        if self._genai_client is not None and hasattr(self._genai_client, "close"):
            try:
                await asyncio.to_thread(self._genai_client.close)
            except Exception as e:
                logger.warning(f"OmniOfflineClient.close: genai client close failed: {e}")
            self._genai_client = None
        self._genai_tools_unsupported = False
        logger.info("OmniOfflineClient closed")
