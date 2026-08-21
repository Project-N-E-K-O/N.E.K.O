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
    _IMAGE_ANALYSIS_PENDING_DESCRIPTION,
    Any,
    Callable,
    Dict,
    Optional,
    VisualDeliveryMode,
    asyncio,
    logger,
    response_arbiter_fail_open_enabled,
    time,
    uuid,
)

from config.prompts.prompts_proactive import (
    REALTIME_PROACTIVE_GENERAL_TRIGGER_PROMPTS,
    REALTIME_PROACTIVE_VISION_TRIGGER_PROMPTS,
    normalize_proactive_prompt_locale,
)
from config.prompts.prompts_sys import _loc

from ._response_arbiter import RealtimeResponseArbiter, ResponseTicket
from ._protocol_capabilities import STRICT_REALTIME_PROTOCOL_CAPABILITIES


# A missing response.done must fail conservatively instead of acknowledging a
# delivery that the provider may still reject. Normal proactive responses
# finish well inside this backstop; it primarily prevents a dead connection
# from leaving the scheduler request open forever.
_PROACTIVE_INJECT_DELIVERY_TIMEOUT_SECONDS = 30.0
_GEMINI_PROACTIVE_CANCEL_GRACE_SECONDS = 3.0
_PROACTIVE_TICKET_CANCEL_OBSERVE_TIMEOUT_SECONDS = 0.5
_GEMINI_PROACTIVE_SESSION_CLOSE_CANCEL = "Gemini session is closing"
_GEMINI_PROACTIVE_TASK_UNSET = object()


def _proactive_text_instruction(language: str, *, has_vision: bool) -> str:
    lang = normalize_proactive_prompt_locale(language or "en")
    prompts = (
        REALTIME_PROACTIVE_VISION_TRIGGER_PROMPTS
        if has_vision
        else REALTIME_PROACTIVE_GENERAL_TRIGGER_PROMPTS
    )
    return _loc(prompts, lang)


class _ResponseMixin:
    def _ensure_response_arbiter(self) -> RealtimeResponseArbiter:
        arbiter = getattr(self, "_response_arbiter", None)
        if arbiter is None:
            arbiter = RealtimeResponseArbiter(
                self.send_event,
                abort_transport=getattr(self, "_abort_failed_transport", None),
                fail_open=response_arbiter_fail_open_enabled(),
                on_stuck_release=getattr(self, "_on_arbiter_stuck_release", None),
                protocol_capabilities=getattr(
                    self,
                    "_realtime_protocol_capabilities",
                    STRICT_REALTIME_PROTOCOL_CAPABILITIES,
                ),
            )
            self._response_arbiter = arbiter
        return arbiter

    async def prime_context(self, text: str, skipped: bool = False) -> None:
        """Inject context during hot-swap.

        Behaviour depends on the skipped parameter and the provider:

        - ``skipped=True`` (or Qwen): appended to the system instructions
          via ``session.update``, without triggering a model response.
        - ``skipped=False`` (GPT/GLM/Step): injects a one-shot user message
          via ``create_response`` and triggers a model response (used for
          proactively reporting task results). Note: this path does not
          write to session instructions; the text is transient — do not
          change it to persist into instructions.
        - Gemini: injected via ``send_client_content`` regardless of
          skipped (SDK limitation, no session.update mechanism). When
          skipped=True the response is silently discarded via
          ``_skip_until_next_response``.

        Args:
            text: Context to inject (incremental cache + summary/ready).
            skipped: If True, only update instructions without triggering
                     a response. If False, also trigger model response.
        """
        if not text or not text.strip():
            logger.info("prime_context: skipping empty content")
            return

        if self._is_gemini:
            # Gemini Live API 没有 session.update 机制，只能通过
            # send_client_content 注入上下文（会创建 user turn）。
            # on_response_done 由 _handle_messages_gemini 自然触发。
            await self._create_response_gemini_with_skip_guard(
                text,
                skipped=skipped,
                raise_on_error=True,
            )
            return

        if not skipped and "qwen" not in self._model_lower:
            # skipped=False：需要模型主动响应（任务结果汇报）
            # 通过 create_response 注入 user 消息 + 触发响应
            # Qwen 不支持 conversation.item.create，走下方 update_session
            await self.create_response(text)
        else:
            # skipped=True 或 Qwen：仅追加到 session instructions
            lock = getattr(self, "_prime_context_lock", None)
            if lock is None:
                lock = asyncio.Lock()
                self._prime_context_lock = lock
            async with lock:
                current_instructions = str(self.instructions or "")
                next_instructions = (
                    current_instructions + "\n" + text
                    if current_instructions
                    else text
                )
                await self.update_session({"instructions": next_instructions})
                self.instructions = next_instructions
            logger.info("prime_context: updated session instructions")

    async def create_response(self, instructions: str, skipped: bool = False) -> None:
        """Inject a persistent user message and trigger an LLM response.

        Unlike ``prime_context`` (which appends to the system instructions),
        this method creates a user-role conversation message and triggers a
        model response. Suited to mid-conversation scenarios where an
        immediate model reply is needed.

        Note: requires that the session already contains a user message, or
        that the API in use supports ``conversation.item.create``; otherwise
        a 1007 error may be triggered.

        Behaviour varies by provider:
          - **OpenAI / GLM / Step**: ``conversation.item.create(role=user)``
            + ``response.create``
          - **Gemini**: ``send_client_content(role=user)``

        See ``prime_context()`` (session-start priming) and
        ``prompt_ephemeral()`` (guarded proactive text turn) for the other two
        injection channels.
        """
        # Gemini 使用 send_client_content 发送文本内容
        if self._is_gemini:
            if not instructions or not instructions.strip():
                logger.info("Gemini: skipping empty content in create_response")
                return
            await self._create_response_gemini_with_skip_guard(
                instructions,
                skipped=skipped,
                raise_on_error=True,
            )
            return

        # 跳过空内容的发送，避免触发 API 错误
        if not instructions or not instructions.strip():
            logger.info("Skipping empty content in create_response")
            return

        if skipped:
            self._skip_until_next_response = True

        item_event_id = f"event_user_item_{uuid.uuid4().hex}"
        response_event_id = f"event_user_response_{uuid.uuid4().hex}"
        item_id = f"item_neko_{uuid.uuid4().hex}"
        expected_item_id = item_id
        # 通过 conversation.item.create 添加用户消息，再触发响应。两步都
        # 进入全局仲裁器，直到 response.done 才释放下一次 create 的资格。
        item_event = {
            "type": "conversation.item.create",
            "event_id": item_event_id,
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
        if expected_item_id is not None:
            item_event["item"]["id"] = item_id
        logger.info("Creating response with user message")
        ticket = await self._ensure_response_arbiter().enqueue(
            source="create_response",
            events_before_response=(item_event,),
            response_event={
                "type": "response.create",
                "event_id": response_event_id,
            },
            ack_expected=True,
            expected_item_id=expected_item_id,
            expected_item_role="user",
        )
        await ticket.sent

    async def submit_external_text_turn(self, text: str, *, turn_id: str):
        """Persist one completed ASR turn and request a reply.

        The transcript is stored as a user conversation item, then a bare
        ``response.create`` is issued — the same pattern as
        ``create_response`` and the proactive inject path. Do not attach
        per-response ``response.instructions`` here: OpenAI Realtime and
        compatible protocols treat them as a replacement for the session
        instructions (the persona system prompt), not an addition. Item
        transport loss is covered by the arbiter's item-ack barrier. The
        caller must pass only a Smart Turn completion, never an ASR partial
        or segment final.
        """
        if getattr(self, "_is_gemini", False):
            raise RuntimeError(
                "external ASR text turns use the existing Gemini SDK path"
            )

        import hashlib

        clean = str(text or "").strip()
        if not clean:
            raise ValueError("external ASR turn must not be empty")
        if len(clean) > 8_000:
            raise ValueError("external ASR turn exceeds the 8000 character budget")
        stable_turn_id = str(turn_id or "").strip()
        if not stable_turn_id:
            raise ValueError("external ASR turn_id must not be empty")

        visual_record = getattr(self, "_external_visual_turns", {}).get(
            stable_turn_id
        )
        visual_description = await self._resolve_external_visual_turn(
            stable_turn_id
        )

        event_suffix = uuid.uuid4().hex
        item_id = f"item_neko_{uuid.uuid4().hex}"
        expected_item_id = item_id
        item_text = clean
        if visual_description:
            # Persist the observation and its owning transcript atomically. A
            # barge-in can now keep both or neither, never an orphaned visual
            # item that leaks into the next voice turn.
            item_text = (
                "[系统视觉感知结果，不是用户陈述]\n"
                f"当前画面：{visual_description}\n"
                "[用户语音转写]\n"
                f"{clean}"
            )
        item_event = {
            "type": "conversation.item.create",
            "event_id": f"event_asr_item_{event_suffix}",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": item_text}],
            },
        }
        if expected_item_id is not None:
            item_event["item"]["id"] = item_id
        response_event = {
            "type": "response.create",
            "event_id": f"event_asr_response_{event_suffix}",
        }
        text_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]
        logger.info(
            "external_turn queued turn=%s chars=%d hash=%s",
            stable_turn_id,
            len(clean),
            text_hash,
        )
        arbiter = self._ensure_response_arbiter()
        try:
            admission_check = None
            if visual_record is not None:
                admission_check = (
                    lambda: self._external_visual_turns.get(stable_turn_id)
                    is visual_record
                )
            ticket = await arbiter.enqueue(
                source="external_asr",
                events_before_response=(item_event,),
                response_event=response_event,
                ack_expected=True,
                expected_item_id=expected_item_id,
                expected_item_role="user",
                priority=0,
                admission_check=admission_check,
            )
        except BaseException:
            if (
                visual_record is not None
                and self._external_visual_turns.get(stable_turn_id)
                is visual_record
            ):
                self._external_visual_turns.pop(stable_turn_id, None)
            raise
        if visual_record is not None:
            if (
                self._external_visual_turns.get(stable_turn_id)
                is not visual_record
            ):
                await arbiter.cancel_ticket(ticket)
                raise asyncio.CancelledError
            visual_record["ticket"] = ticket
        # Speech-start pauses dispatch. Resume only after this priority-0 user
        # turn is present, so queued proactive work cannot win the race. An
        # older completed turn may still be ahead of a newer paused turn in
        # the serial transcript dispatcher; let that ticket through, then
        # restore the newer pause. Before selecting again, the arbiter
        # explicitly yields to this ticket's waiter; its post-selection gate
        # then returns any concurrently blocked work without charging the
        # fairness allowance.
        active_pause_id = getattr(self, "_external_voice_turn_pause_id", None)
        if active_pause_id == stable_turn_id:
            self._external_voice_turn_pause_id = None
        arbiter.resume_dispatch()
        try:
            await ticket.sent
        except asyncio.CancelledError:
            await arbiter.cancel_ticket(ticket)
            raise
        finally:
            # Re-arm the newer turn's pause on the failure path too: a
            # transport error (or a newer prepare's cancel_current) can fail
            # ``ticket.sent`` after the resume above already released that
            # newer turn's pause, and without this re-pause queued proactive
            # work could dispatch ahead of that turn's user text.
            if (
                active_pause_id is not None
                and active_pause_id != stable_turn_id
                and getattr(self, "_external_voice_turn_pause_id", None)
                == active_pause_id
            ):
                arbiter.pause_dispatch()
            if (
                visual_record is not None
                and self._external_visual_turns.get(stable_turn_id)
                is visual_record
            ):
                self._external_visual_turns.pop(stable_turn_id, None)
        return ticket

    async def _cancel_gemini_proactive_submit(
        self,
        *,
        session_closing: bool = False,
        submit_task: Any = _GEMINI_PROACTIVE_TASK_UNSET,
    ) -> None:
        """Cancel and join the task parked in Gemini's proactive SDK send."""

        if submit_task is _GEMINI_PROACTIVE_TASK_UNSET:
            submit_task = getattr(self, "_gemini_proactive_submit_task", None)
        if submit_task is None:
            return
        if submit_task is asyncio.current_task():
            return
        if not submit_task.done():
            if session_closing:
                submit_task.cancel(_GEMINI_PROACTIVE_SESSION_CLOSE_CANCEL)
            else:
                submit_task.cancel()
            await asyncio.gather(submit_task, return_exceptions=True)
        if getattr(self, "_gemini_proactive_submit_task", None) is submit_task:
            self._gemini_proactive_submit_task = None

    async def prepare_external_voice_turn(self, *, turn_id: str) -> bool:
        """Prepare one external ASR turn; report an in-place reconnect.

        Gemini quarantine may retire and replace the SDK connection on this
        same client instance.  The Core owner uses the returned flag to replace
        the receive task that captured the retired session.
        """

        stable_turn_id = str(turn_id or "").strip()
        if not stable_turn_id:
            raise ValueError("external voice turn_id must not be empty")
        connection_generation = self._connection_generation
        async with self._ensure_turn_admission_lock():
            if self._is_gemini:
                self._start_gemini_external_submit_quarantine()
                await self._await_gemini_external_quarantine()
                await self._cancel_gemini_proactive_submit()
                proactive_outcome = getattr(
                    self,
                    "_gemini_proactive_outcome",
                    None,
                )
                proactive_quarantine = getattr(
                    self,
                    "_gemini_proactive_quarantine_task",
                    None,
                )
                if (
                    proactive_outcome is not None
                    and (
                        proactive_quarantine is None
                        or proactive_quarantine.done()
                    )
                ):
                    # SDK-send completion is not a Gemini lifecycle terminal.
                    # Until first content / turn_complete / interrupted arrives,
                    # the accepted proactive turn still owns this unscoped SDK
                    # session. Quarantine that outcome before admitting an
                    # external-ASR successor, just as the in-flight cancellation
                    # path does.
                    proactive_quarantine = self._fire_task(
                        self._interrupt_and_quarantine_gemini_proactive_outcome(
                            proactive_outcome[0],
                            error_msg=(
                                "Gemini proactive turn was superseded by "
                                "external voice input"
                            ),
                        )
                    )
                    self._gemini_proactive_quarantine_task = proactive_quarantine
                await self._await_gemini_proactive_quarantine()
            self._begin_external_visual_turn(stable_turn_id)
            try:
                if not self._is_gemini:
                    arbiter = self._ensure_response_arbiter()
                    self._external_voice_turn_pause_id = stable_turn_id
                    arbiter.pause_dispatch()
                    await arbiter.cancel_current()
                await self.handle_interruption()
            except BaseException:
                self.abandon_external_voice_turn(stable_turn_id)
                raise
        return self._connection_generation != connection_generation

    def _settle_gemini_external_turn(self, token: object | None = None) -> None:
        """Release accepted external-ASR ownership at its terminal edge."""

        current = getattr(self, "_gemini_external_outcome_token", None)
        if token is not None and current is not token:
            return
        self._gemini_external_outcome_token = None

    def _start_gemini_external_submit_quarantine(
        self,
        submit_task: Optional[asyncio.Task] = None,
    ) -> None:
        """Retire a Gemini session whose external turn send was cancelled."""

        if submit_task is None:
            submit_task = getattr(self, "_gemini_external_submit_task", None)
        outcome_token = getattr(self, "_gemini_external_outcome_token", None)
        if (
            (submit_task is None or submit_task.done())
            and outcome_token is None
        ):
            return
        if submit_task is asyncio.current_task():
            return
        quarantine_task = getattr(
            self,
            "_gemini_external_quarantine_task",
            None,
        )
        if quarantine_task is not None and not quarantine_task.done():
            return
        self._gemini_external_quarantine_task = self._fire_task(
            self._quarantine_gemini_external_submit(submit_task, outcome_token)
        )

    async def _quarantine_gemini_external_submit(
        self,
        submit_task: Optional[asyncio.Task],
        outcome_token: object | None,
    ) -> None:
        """Join a cancelled SDK send, then close the ambiguous connection."""

        submit_was_inflight = submit_task is not None and not submit_task.done()
        if submit_was_inflight:
            submit_task.cancel()
            await asyncio.gather(submit_task, return_exceptions=True)
        if (
            submit_task is not None
            and getattr(self, "_gemini_external_submit_task", None) is submit_task
        ):
            self._gemini_external_submit_task = None
        if (
            not submit_was_inflight
            and outcome_token is not None
            and getattr(self, "_gemini_external_outcome_token", None)
            is not outcome_token
        ):
            # Its terminal event won the race with quarantine startup, so the
            # session no longer contains an ambiguous accepted turn.
            return
        self._settle_gemini_external_turn(outcome_token)
        # Cancellation only ends our await; Gemini may already have accepted
        # the turn. The connection is the smallest scope that can prove no late
        # transcript/response from that turn can cross into its successor.
        self._fatal_error_occurred = True
        await self._close_gemini()

    async def _await_gemini_external_quarantine(self) -> None:
        """Join external-submit quarantine and reconnect before a new turn."""

        quarantine_task = getattr(
            self,
            "_gemini_external_quarantine_task",
            None,
        )
        if quarantine_task is not None and quarantine_task is not asyncio.current_task():
            await asyncio.shield(quarantine_task)
            if (
                quarantine_task.done()
                and getattr(self, "_gemini_external_quarantine_task", None)
                is quarantine_task
            ):
                self._gemini_external_quarantine_task = None
        if getattr(self, "_gemini_session", None) is None:
            instructions = str(getattr(self, "instructions", "") or "")
            if instructions:
                await self.connect(
                    instructions,
                    native_audio=getattr(self, "_native_audio", True),
                )

    async def _await_gemini_proactive_quarantine(self) -> None:
        """Join stale Gemini proactive quarantine before opening a user turn."""

        quarantine_task = getattr(self, "_gemini_proactive_quarantine_task", None)
        if (
            quarantine_task is None
            or quarantine_task is asyncio.current_task()
        ):
            return
        try:
            await asyncio.shield(quarantine_task)
        except asyncio.CancelledError:
            if not quarantine_task.cancelled():
                raise
        finally:
            if (
                quarantine_task.done()
                and getattr(self, "_gemini_proactive_quarantine_task", None)
                is quarantine_task
            ):
                self._gemini_proactive_quarantine_task = None

        # A quarantine with no terminal lifecycle retires the old Gemini
        # session. Reconnect before admitting the user's turn so its transcript
        # cannot race the retired SDK context.
        if getattr(self, "_gemini_session", None) is None:
            instructions = str(getattr(self, "instructions", "") or "")
            if instructions:
                await self.connect(
                    instructions,
                    native_audio=getattr(self, "_native_audio", True),
                )

    def abandon_external_voice_turn(self, turn_id: str | None = None) -> None:
        """Release an external-ASR dispatch pause, optionally by turn key."""

        self._abandon_external_visual_turn(turn_id)
        if self._is_gemini:
            return
        current_turn_id = getattr(self, "_external_voice_turn_pause_id", None)
        if turn_id is not None and str(turn_id).strip() != current_turn_id:
            return
        self._external_voice_turn_pause_id = None
        arbiter = getattr(self, "_response_arbiter", None)
        if arbiter is None:
            if current_turn_id is None:
                return
            arbiter = self._ensure_response_arbiter()
        arbiter.resume_dispatch()

    async def submit_external_voice_turn(self, text: str, *, turn_id: str) -> None:
        """Submit external ASR text through the Provider-appropriate path."""

        if self._is_gemini:
            clean = str(text or "").strip()
            if not clean:
                raise ValueError("external ASR turn must not be empty")
            if len(clean) > 8_000:
                raise ValueError("external ASR turn exceeds the 8000 character budget")
            stable_turn_id = str(turn_id or "").strip()
            if not stable_turn_id:
                raise ValueError("external voice turn_id must not be empty")
            visual_record = getattr(self, "_external_visual_turns", {}).get(
                stable_turn_id
            )
            visual_description = await self._resolve_external_visual_turn(
                stable_turn_id
            )
            if (
                visual_record is not None
                and self._external_visual_turns.get(stable_turn_id)
                is not visual_record
            ):
                raise asyncio.CancelledError
            item_text = clean
            if visual_description:
                item_text = (
                    "[系统视觉感知结果，不是用户陈述]\n"
                    f"当前画面：{visual_description}\n"
                    "[用户语音转写]\n"
                    f"{clean}"
                )
            if visual_record is not None:
                visual_record["submit_task"] = asyncio.current_task()
            submit_task = asyncio.current_task()
            outcome_token = object()
            self._gemini_external_submit_task = submit_task
            self._gemini_external_outcome_token = outcome_token
            accepted = False
            try:
                await self.create_response(item_text)
                accepted = True
            finally:
                if getattr(self, "_gemini_external_submit_task", None) is submit_task:
                    self._gemini_external_submit_task = None
                if not accepted:
                    self._settle_gemini_external_turn(outcome_token)
                if (
                    visual_record is not None
                    and self._external_visual_turns.get(stable_turn_id)
                    is visual_record
                ):
                    self._external_visual_turns.pop(stable_turn_id, None)
            return
        await self.submit_external_text_turn(text, turn_id=turn_id)

    def is_active_response(self) -> bool:
        """Return True iff the realtime session is currently producing a response.

        Tracks ``response.created`` → ``response.done`` (OpenAI / GLM / Step /
        free / GPT) and Gemini's ``turn_complete`` lifecycle via the shared
        ``_is_responding`` flag, so callers can gate "manual inject + request
        response" against the realtime API's "one active response at a time"
        constraint.
        """
        return bool(self._is_responding or self._ensure_response_arbiter().is_busy)

    async def inject_text_and_request_response(
        self,
        text: str,
        *,
        events_before_text: tuple[Dict[str, Any], ...] = (),
        on_rejected: Optional[Callable[[str], None]] = None,
        on_completed: Optional[Callable[[], None]] = None,
    ) -> Optional[ResponseTicket]:
        """Inject a user-role text item and explicitly trigger a response.

        Used by the voice-mode proactive path (agent task callbacks /
        plugin push_message ai_behavior="respond") to surface a rendered
        instruction to the realtime model and have it speak the result
        immediately — without waiting for the next user turn (which is what
        the hot-swap pending_extra_replies channel does).

        Caller is responsible for gating against active-response races
        (see ``is_active_response``) — the realtime API only allows one
        in-flight response at a time and will reject a second
        ``response.create`` with ``response_already_active``.

        Server-side rejection (e.g. VAD races in between the caller's gate
        check and our ``response.create``) does not raise here because the
        server delivers it asynchronously via an ``error`` event. Pass
        ``on_rejected=cb(error_msg)`` to receive that rejection — the
        message loop will invoke it when ``error.event_id`` matches the
        client-side id we stamp on ``response.create``. The caller can use
        it to put the optimistically-pruned cb back in the queue.
        ``on_completed`` is fired only when this request's arbiter ticket
        reaches ``response.done``. It is deliberately not tied to the next
        global terminal event: another queued or active response may finish
        before this request is even dispatched.

        Returns the exact arbiter ticket for WebSocket providers so callers
        can target cancellation to this request. Gemini and empty-text paths
        return ``None``.

        Provider dispatch (all realtime providers supported — symmetric with
        ``create_response``):
          - **OpenAI / GLM / Step / free / GPT / Qwen / Grok**:
            ``conversation.item.create`` (role=user, input_text) +
            ``response.create``. Uses user role rather than system to avoid
            permanent drift of session instruction context — the rendered
            body already self-identifies as a system notification via its
            localized header wrapper. (Qwen included: the
            Aliyun doc claiming function_call_output-only is stale for
            qwen3.5-omni-flash-realtime; verified live.)
          - **Gemini Live**: ``send_client_content(turn_complete=True)`` via
            the shared ``_gemini_send_user_turn`` helper — Gemini's idiomatic
            inject+trigger. Synchronous send failures raise; successful sends
            remain pending until ``turn_complete`` or ``interrupted``.
        """
        if self._fatal_error_occurred:
            raise RuntimeError("realtime session has fatal_error_occurred set")
        if not text or not text.strip():
            return

        if self._is_gemini:
            # Symmetric with create_response → _create_response_gemini.
            # send_client_content(turn_complete=True) injects a user turn and
            # triggers a response. Delivery remains pending until Gemini's
            # turn_complete/interrupted lifecycle settles this exact inject;
            # SDK-send success alone is not response completion.
            if self._gemini_session is None:
                raise RuntimeError("Gemini session not available for proactive inject")
            gemini_text_parts: list[str] = []
            for event in events_before_text:
                if event.get("type") != "conversation.item.create":
                    raise ValueError("Gemini proactive prefix must be a text item")
                item = event.get("item")
                if not isinstance(item, dict) or item.get("role") != "user":
                    raise ValueError("Gemini proactive prefix must use user role")
                content = item.get("content")
                if not isinstance(content, list):
                    raise ValueError("Gemini proactive prefix content must be a list")
                for part in content:
                    if not isinstance(part, dict) or part.get("type") != "input_text":
                        raise ValueError("Gemini proactive prefix must contain input_text")
                    prefix_text = str(part.get("text") or "").strip()
                    if prefix_text:
                        gemini_text_parts.append(prefix_text)
            gemini_text_parts.append(text.strip())
            gemini_text = "\n".join(gemini_text_parts)
            outcome_token = f"gemini_inject_{uuid.uuid4().hex}"
            if on_rejected is not None or on_completed is not None:
                if getattr(self, "_gemini_proactive_outcome", None) is not None:
                    raise RuntimeError("another Gemini proactive inject is pending")
                self._gemini_proactive_outcome = (
                    outcome_token,
                    on_rejected,
                    on_completed,
                )
                self._proactive_inject_outcome_token = outcome_token
                self._proactive_inject_awaiting_outcome = True
            submit_task = asyncio.current_task()
            existing_submit_task = getattr(
                self,
                "_gemini_proactive_submit_task",
                None,
            )
            if (
                existing_submit_task is not None
                and existing_submit_task is not submit_task
                and not existing_submit_task.done()
            ):
                outcome = getattr(self, "_gemini_proactive_outcome", None)
                if outcome is not None and outcome[0] == outcome_token:
                    self._settle_gemini_proactive_inject(notify=False)
                raise RuntimeError("another Gemini proactive SDK send is pending")
            self._gemini_proactive_submit_task = submit_task
            try:
                await self._gemini_send_user_turn(gemini_text)
            except asyncio.CancelledError as exc:
                outcome = getattr(self, "_gemini_proactive_outcome", None)
                if outcome is not None and outcome[0] == outcome_token:
                    if (
                        exc.args
                        and exc.args[0]
                        == _GEMINI_PROACTIVE_SESSION_CLOSE_CANCEL
                    ):
                        # The owning SDK session is being retired immediately;
                        # no delayed quarantine may survive this close and later
                        # seize a replacement connection.
                        self._settle_gemini_proactive_inject(notify=False)
                    else:
                        # Cancellation can arrive after the SDK accepted the
                        # unscoped turn but before its await resumes. Suppress
                        # callbacks and quarantine until a terminal (or session
                        # retirement) makes retry correlation safe again.
                        self._gemini_proactive_outcome = (
                            outcome_token,
                            None,
                            None,
                        )
                        quarantine_task = self._fire_task(
                            self._interrupt_and_quarantine_gemini_proactive_outcome(
                                outcome_token,
                                error_msg="Gemini proactive SDK send was cancelled",
                            )
                        )
                        self._gemini_proactive_quarantine_task = quarantine_task
                raise
            except Exception:
                outcome = getattr(self, "_gemini_proactive_outcome", None)
                if outcome is not None and outcome[0] == outcome_token:
                    self._settle_gemini_proactive_inject(notify=False)
                raise
            finally:
                if (
                    getattr(self, "_gemini_proactive_submit_task", None)
                    is submit_task
                ):
                    self._gemini_proactive_submit_task = None
            if on_rejected is not None or on_completed is not None:
                self._fire_task(
                    self._expire_gemini_proactive_outcome(outcome_token, 60.0)
                )
            return
        # NOTE on Qwen: the Aliyun realtime doc states conversation.item.create
        # "currently only supports function_call_output items". That is stale
        # for qwen3.5-omni-flash-realtime — empirically it accepts a
        # ``role=user`` ``input_text`` message item and responds to it (no
        # error event), identical to OpenAI / GLM / Step. Verified live against
        # the dashscope realtime endpoint. So Qwen takes the same path below;
        # do NOT re-add a Qwen exclusion without re-checking the live API.
        if self.ws is None:
            raise RuntimeError("realtime websocket is not connected")

        # Role choice: ``user`` (not ``system``).
        # OpenAI Realtime persists conversation items as part of session
        # history. ``role="system"`` items are treated as high-priority
        # instructions that influence every subsequent turn — accumulating
        # several proactive callbacks under system role causes prompt
        # drift (model starts repeating meta-behavior or interpreting
        # stale callback text as standing orders for unrelated turns).
        # ``role="user"`` keeps the inject in dialog-weight context, and
        # ``_build_callback_instruction`` already wraps the body in a
        # ``======[系统通知] ...======`` header that makes the model
        # treat it as a one-shot system notification rather than user
        # speech. Matches the existing ``create_response`` precedent.
        # Stamp stable client event_ids on BOTH events so the server's
        # ``error.event_id`` can be matched back to this specific request
        # whichever event it rejects (the item itself, or the
        # ``response.create`` — e.g. ``response_already_active`` from a VAD
        # race). ``send_event()`` would otherwise overwrite a missing
        # event_id with its own timestamp-based string — fine for routing but
        # useless for rejection matching since the caller has no view of it.
        # A single ``_reject_once`` wrapper fires ``on_rejected`` at most once
        # even if both event_ids somehow error, and unregisters both handlers.
        item_event_id = f"event_inject_item_{uuid.uuid4().hex}"
        create_event_id = f"event_inject_resp_{uuid.uuid4().hex}"
        outcome_token = create_event_id
        item_id = f"item_neko_{uuid.uuid4().hex}"
        expected_item_id = item_id

        def _close_outcome_window() -> None:
            if (
                getattr(self, "_proactive_inject_outcome_token", None)
                == outcome_token
            ):
                self._proactive_inject_outcome_token = None
                self._proactive_inject_awaiting_outcome = False

        if on_rejected is not None or on_completed is not None:
            _fired = False

            def _remove_outcome_handlers() -> None:
                self._inject_rejection_handlers.pop(item_event_id, None)
                self._inject_rejection_handlers.pop(create_event_id, None)

            def _reject_once(error_msg: str) -> None:
                nonlocal _fired
                # Unregister both regardless so neither lingers.
                _remove_outcome_handlers()
                if _fired:
                    return
                _fired = True
                _close_outcome_window()
                if on_rejected is not None:
                    on_rejected(error_msg)

            def _complete_once() -> None:
                nonlocal _fired
                _remove_outcome_handlers()
                if _fired:
                    return
                _fired = True
                _close_outcome_window()
                if on_completed is not None:
                    on_completed()

            if on_rejected is not None:
                self._inject_rejection_handlers[item_event_id] = _reject_once
                self._inject_rejection_handlers[create_event_id] = _reject_once
            # The realtime API echoes our event_id on ``error`` but not on
            # ``response.created``. Completion is therefore observed from the
            # exact arbiter ticket below, never by sweeping callbacks on an
            # unrelated global ``response.done``.
            self._fire_task(
                self._expire_inject_rejection_handler(
                    item_event_id,
                    60.0,
                    outcome_token,
                )
            )
            self._fire_task(
                self._expire_inject_rejection_handler(
                    create_event_id,
                    60.0,
                    outcome_token,
                )
            )
            # Open the no-id content-fallback window for THIS inject. Closed
            # when its own arbiter ticket completes or is rejected.
            self._proactive_inject_outcome_token = outcome_token
            self._proactive_inject_awaiting_outcome = True

        item_event: Dict[str, Any] = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": text,
                    }
                ],
            },
        }
        if expected_item_id is not None:
            item_event["item"]["id"] = item_id
        item_event["event_id"] = item_event_id

        # send_event() silently returns when ws drops to None or fatal flag
        # flips mid-flight (it does not raise). Without the post-send checks,
        # a connection lost in the brief await window between the entry guard
        # and the actual send would look like a successful inject — caller
        # would prune the cb but nothing reached the model. Re-check after
        # each send and raise so the caller's exception branch keeps the cb
        # for retry. On any synchronous send failure, drop both rejection
        # handlers so the caller's ``except`` path is the single source of
        # truth and a late error event can't double-fire the re-queue.
        arbiter = self._ensure_response_arbiter()
        ticket: Optional[ResponseTicket] = None
        try:
            create_event: Dict[str, Any] = {"type": "response.create"}
            create_event["event_id"] = create_event_id
            ticket = await arbiter.enqueue(
                source="proactive",
                events_before_response=(*events_before_text, item_event),
                response_event=create_event,
                ack_expected=True,
                expected_item_id=expected_item_id,
                expected_item_role="user",
                priority=20,
            )
            await asyncio.shield(ticket.sent)
            if self._fatal_error_occurred or self.ws is None:
                raise RuntimeError(
                    "realtime connection lost after proactive response.create"
                )

            if on_rejected is not None or on_completed is not None:
                async def _observe_ticket_outcome() -> None:
                    try:
                        await asyncio.shield(ticket.done)
                    except Exception as exc:
                        _reject_once(str(exc))
                    else:
                        _complete_once()

                self._fire_task(_observe_ticket_outcome())
        except asyncio.CancelledError:
            ticket_completed = False
            if ticket is not None:
                try:
                    cancellation_requested = await asyncio.shield(
                        arbiter.cancel_ticket(ticket)
                    )
                except Exception as cancel_exc:
                    logger.warning(
                        "proactive inject cancellation cleanup failed: %s",
                        cancel_exc,
                    )
                else:
                    if not cancellation_requested:
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(ticket.done),
                                timeout=(
                                    _PROACTIVE_TICKET_CANCEL_OBSERVE_TIMEOUT_SECONDS
                                ),
                            )
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            pass
                        except Exception:
                            pass
                        else:
                            ticket_completed = True
                            if (
                                on_rejected is not None
                                or on_completed is not None
                            ):
                                _complete_once()
            if not ticket_completed:
                self._inject_rejection_handlers.pop(item_event_id, None)
                self._inject_rejection_handlers.pop(create_event_id, None)
                _close_outcome_window()
            raise
        except Exception:
            self._inject_rejection_handlers.pop(item_event_id, None)
            self._inject_rejection_handlers.pop(create_event_id, None)
            _close_outcome_window()
            raise
        return ticket

    def _settle_gemini_proactive_inject(
        self,
        *,
        error_msg: Optional[str] = None,
        notify: bool = True,
    ) -> None:
        """Settle the one pending Gemini proactive turn at its lifecycle edge."""
        outcome = getattr(self, "_gemini_proactive_outcome", None)
        if outcome is None:
            return
        token, on_rejected, on_completed = outcome
        self._gemini_proactive_outcome = None
        quarantine_task = getattr(self, "_gemini_proactive_quarantine_task", None)
        if (
            quarantine_task is not None
            and quarantine_task is not asyncio.current_task()
            and not quarantine_task.done()
        ):
            quarantine_task.cancel()
        if (
            quarantine_task is not None
            and quarantine_task is not asyncio.current_task()
            and quarantine_task.done()
        ):
            self._gemini_proactive_quarantine_task = None
        if getattr(self, "_proactive_inject_outcome_token", None) == token:
            self._proactive_inject_outcome_token = None
            self._proactive_inject_awaiting_outcome = False
        if not notify:
            return
        try:
            if error_msg is not None:
                if on_rejected is not None:
                    on_rejected(error_msg)
            elif on_completed is not None:
                on_completed()
        except Exception as cb_exc:
            logger.warning("Gemini proactive outcome handler raised: %s", cb_exc)

    async def _expire_gemini_proactive_outcome(
        self,
        token: str,
        ttl: float,
    ) -> None:
        """Fail closed if Gemini never emits turn_complete/interrupted."""
        try:
            await asyncio.sleep(ttl)
        except asyncio.CancelledError:
            return
        outcome = getattr(self, "_gemini_proactive_outcome", None)
        if outcome is not None and outcome[0] == token:
            await self._interrupt_and_quarantine_gemini_proactive_outcome(
                token,
                error_msg="Gemini proactive response lifecycle timed out",
            )

    async def _interrupt_and_quarantine_gemini_proactive_outcome(
        self,
        token: str,
        *,
        error_msg: str,
    ) -> None:
        """Interrupt an unscoped Gemini turn and retain its token until safe."""

        outcome = getattr(self, "_gemini_proactive_outcome", None)
        if outcome is None or outcome[0] != token:
            return
        # Gemini lifecycle events are not tagged with a response id. Do not
        # release this token while the original generation can still emit a
        # late terminal: that terminal could otherwise settle a newer retry.
        try:
            await self.cancel_response()
        except Exception as exc:
            logger.warning(
                "Gemini proactive interrupt failed while quarantining outcome: %s",
                exc,
            )
        await asyncio.sleep(_GEMINI_PROACTIVE_CANCEL_GRACE_SECONDS)
        outcome = getattr(self, "_gemini_proactive_outcome", None)
        if outcome is None or outcome[0] != token:
            return

        # No terminal followed the interrupt. Retire the whole Gemini session
        # before releasing the token so no event from the abandoned turn can
        # cross-talk with a future reconnect/retry.
        self._fatal_error_occurred = True
        try:
            await self._close_gemini()
        except Exception as exc:
            logger.warning(
                "Gemini proactive quarantine close failed: %s",
                exc,
            )
        outcome = getattr(self, "_gemini_proactive_outcome", None)
        if outcome is not None and outcome[0] == token:
            self._settle_gemini_proactive_inject(error_msg=error_msg)

    async def _expire_inject_rejection_handler(
        self,
        event_id: str,
        ttl: float,
        token: Optional[str] = None,
    ) -> None:
        """TTL backstop for a ticket whose terminal lifecycle never arrives."""
        try:
            await asyncio.sleep(ttl)
        except asyncio.CancelledError:
            return
        if token is None:
            # Standalone image rejection handlers share this TTL helper but
            # do not own the proactive response-outcome gate.
            self._inject_rejection_handlers.pop(event_id, None)
            return
        if self._proactive_inject_outcome_token != token:
            return
        self._inject_rejection_handlers.pop(event_id, None)
        if not self._inject_rejection_handlers:
            self._proactive_inject_awaiting_outcome = False
            self._proactive_inject_outcome_token = None

    @staticmethod
    def _looks_like_response_conflict(error_msg: str) -> bool:
        """Heuristic: does this ``error`` message look like the server
        rejecting a ``response.create`` because a response is already active?

        That ``response_already_active`` class is the ONLY async rejection a
        proactive inject can provoke (our inject is the only client-issued
        ``response.create`` on the voice path). Matching its content lets us
        route the rejection even when the provider's error doesn't echo our
        client ``event_id``. Kept deliberately broad across phrasings /
        providers but still scoped to response-conflict wording so unrelated
        errors (auth / quota / 503 / idle-timeout) don't trip it."""
        low = error_msg.lower()
        if "response_already_active" in low:
            return True
        return "response" in low and any(
            k in low for k in ("already", "active", "in progress", "in_progress", "exists", "ongoing")
        )

    def _route_inject_rejection(self, err_event_id, error_msg: str) -> None:
        """Deliver a server rejection to the matching proactive-inject
        ``on_rejected`` handler so the caller re-enqueues the cb.

        Two correlation paths:
          1. **By id (precise)** — OpenAI Realtime (and any provider that
             echoes the offending client ``event_id``): pop and fire the exact
             handler.
          2. **By content (fallback)** — ONLY when the provider omits a
             client-correlation id entirely (``err_event_id`` falsy): if the
             error looks like a response-conflict
             (``_looks_like_response_conflict``), fire only the handler keyed
             by the current proactive outcome token. Standalone callback-image
             handlers may remain in the shared map after an earlier successful
             text turn and must never be swept by a later no-id conflict.

        Critically, the content fallback is gated on ``err_event_id`` being
        absent. If the error DOES carry a client event_id that simply isn't
        ours, the rejection belongs to a different ``response.create`` (e.g.
        ``create_response`` hot-swap priming / tool-result continuation /
        ``signal_user_activity_end`` — all of which get a timestamp event_id
        from ``send_event``'s setdefault), NOT our inject. Firing our handlers
        on those would re-enqueue callbacks the model actually accepted →
        duplicate announcements. So a present-but-non-matching id means "not
        ours; do nothing"."""
        if not self._inject_rejection_handlers:
            return

        def _fire(handler) -> None:
            try:
                handler(error_msg)
            except Exception as cb_exc:
                logger.warning("proactive inject rejection handler raised: %s", cb_exc)

        if err_event_id:
            # Id present: fire ONLY on an exact match. A non-matching id
            # belongs to some other request's rejection — not ours.
            handler = self._inject_rejection_handlers.pop(err_event_id, None)
            if handler is not None:
                # Outcome-bound handlers close their own token window.
                # Standalone image handlers do not own that shared gate and
                # must not clear a newer text inject's state.
                _fire(handler)
            return

        # No client-correlation id at all — fall back to content matching,
        # but ONLY while a proactive inject is genuinely awaiting its outcome
        # (one-shot window). This excludes a no-id response-conflict raised by
        # a DIFFERENT response.create sender (create_response / tool-result /
        # signal_user_activity_end) from hitting a lingering, already-succeeded
        # proactive handler.
        if (
            self._proactive_inject_awaiting_outcome
            and self._looks_like_response_conflict(error_msg)
        ):
            outcome_event_id = getattr(
                self,
                "_proactive_inject_outcome_token",
                None,
            )
            handler = (
                self._inject_rejection_handlers.pop(outcome_event_id, None)
                if outcome_event_id
                else None
            )
            if handler is not None:
                self._proactive_inject_awaiting_outcome = False
                _fire(handler)

    async def prompt_ephemeral(
        self,
        instruction: str = "",
        *,
        language: str = "zh",
        user_turn_active: Optional[Callable[[], bool]] = None,
        session_owned: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """Inject a text turn and explicitly request proactive speech.

        Realtime providers now all accept text input, so proactive turns no
        longer synthesize and upload a fake user WAV to trip server VAD.  This
        avoids bogus ASR transcripts in the UI and removes the pacing/race
        surface of a multi-chunk audio injection.

        Pending native visual context is sent immediately before the text turn.
        Standard StepFun remains the only non-native realtime provider: when a
        VISION_MODEL description is available, it is injected as text first.
        """
        # ── Guard checks ──────────────────────────────────────────────
        if self._fatal_error_occurred:
            return False
        if self._proactive_inject_awaiting_outcome:
            logger.debug("prompt_ephemeral: skipped — another proactive inject is pending")
            return False
        if self._is_gemini:
            if self._gemini_session is None:
                return False
        elif self.ws is None:
            return False
        if self.is_active_response():
            logger.debug(
                "prompt_ephemeral: skipped — response arbiter is active or queued"
            )
            return False
        _now = time.time()
        # ── AI-speech guard（对称于 _user_recent_activity_time）─────────
        # _is_responding 已被 response.done / turn_complete flip False，但 AI 侧
        # content 流可能还在滴水：
        #   1. Gemini turn_complete 早于最后几帧音频送达
        #   2. Gemini 长回复 sub-turn 间的 False 瞬间
        #   3. response.created 到首 content chunk 的空窗（_is_responding 已 True
        #      覆盖这一条，但加这层冗余保险无害）
        # 3s 窗口覆盖上述抢跑 gap，避免主动文本踩着 AI 尾巴打断自己。
        if _now - self._ai_recent_activity_time < self._ai_recent_activity_window:
            logger.debug("prompt_ephemeral: skipped — AI recently active (%.2fs ago)",
                         _now - self._ai_recent_activity_time)
            return False
        # ── User-speech guards ───────────────────────────────────────
        # B: 先用独立的 _user_recent_activity_time 判定近期是否有语音帧；
        # 此信号不依赖 sustain，覆盖用户说话首 500ms 与句间停顿缝隙。
        # 适用所有 VAD 源（RNNoise / server-VAD / RMS），所以不再门控在
        # _rnnoise_vad_active 下 —— RMS 阈值 500 已较保守，误触可接受，
        # 相比主动搭话切断用户说话的体验损失值得。
        if _now - self._user_recent_activity_time < self._user_recent_activity_window:
            logger.debug("prompt_ephemeral: skipped — user recently active (%.2fs ago)",
                         _now - self._user_recent_activity_time)
            return False
        # A: 现有 _client_vad_active + grace 检查（sustained VAD 信号兜底）。
        # Grace 已从 2s 扩到 6s，覆盖自然停顿。
        # 门控条件：存在可靠 VAD 信号源。
        #   - server-VAD 后端（Qwen/OpenAI）：server 的 speech_started/stopped 可靠，
        #     不依赖 RNNoise。特别覆盖 16kHz 移动端长句 >8s 的场景（_user_recent_activity
        #     在 speech_started 打点后 8s 过期，而用户还在说，需要 _client_vad_active 兜底）。
        #   - RNNoise 客户端 VAD（48kHz 桌面 + Gemini/lanlan.app+free）
        # RMS-only 路径（16kHz 无 server-VAD）信号太噪，不信任，依赖 _user_recent_activity。
        if self._has_server_vad or self._rnnoise_vad_active:
            if self._client_vad_active:
                logger.debug("prompt_ephemeral: skipped — user speaking (VAD active)")
                return False
            if _now - self._client_vad_last_speech_time < self._client_vad_grace_period:
                logger.debug("prompt_ephemeral: skipped — VAD grace period")
                return False
        if callable(user_turn_active) and user_turn_active():
            logger.debug(
                "prompt_ephemeral: skipped — external user turn is active"
            )
            return False

        outcome_observed = asyncio.Event()
        delivery_rejected = False
        visual_delivery_rejected = False
        rejection_message = ""
        visual_event_id: str | None = None
        events_before_text: tuple[Dict[str, Any], ...] = ()
        external_visual_delivery = getattr(
            self,
            "_visual_delivery_mode",
            VisualDeliveryMode.NATIVE,
        ) == VisualDeliveryMode.EXTERNAL_DESCRIPTION

        def _on_rejected(error_msg: str) -> None:
            nonlocal delivery_rejected, rejection_message
            delivery_rejected = True
            rejection_message = error_msg
            if (
                not visual_delivery_rejected
                and self._looks_like_response_conflict(error_msg)
            ):
                # A response-conflict rejection happens only after the arbiter
                # has persisted every event preceding response.create,
                # including this snapshot's native image or Step description.
                # The competing response can already see that context, so do
                # not offer it to the next scheduled proactive turn. Exact
                # visual-event rejection still re-arms it via
                # _on_visual_rejected instead.
                _mark_snapshot_consumed_if_current()
            logger.warning("prompt_ephemeral: proactive text rejected: %s", error_msg)
            outcome_observed.set()

        def _on_visual_rejected(error_msg: str) -> None:
            nonlocal visual_delivery_rejected
            visual_delivery_rejected = True
            if (
                getattr(self, "_latest_image_generation", 0)
                == snapshot_image_generation
            ):
                self._proactive_image_consumed = False
            _on_rejected(error_msg)

        def _on_completed() -> None:
            outcome_observed.set()

        def _remove_visual_rejection_handler() -> None:
            if visual_event_id is not None:
                self._inject_rejection_handlers.pop(visual_event_id, None)

        # ── Resolve pending visual context ────────────────────────────
        # Native providers can consume an unconsumed raw frame immediately.
        # Standard StepFun cannot: stream_image() caches the frame before its
        # external VISION_MODEL analysis finishes. Defer that proactive
        # attempt until the matching description exists, or we would select a
        # screen-aware prompt without injecting any visual context and then
        # incorrectly mark the frame consumed.
        has_pending_frame = (
            self._latest_image_b64 is not None
            and not self._proactive_image_consumed
        )
        if (
            has_pending_frame
            and not external_visual_delivery
            and not self._supports_native_image
            and not self._image_recognized_this_turn
        ):
            logger.debug(
                "prompt_ephemeral: skipped — StepFun visual analysis is pending"
            )
            return False
        has_vision = self._image_recognized_this_turn or (
            (self._supports_native_image or external_visual_delivery)
            and has_pending_frame
        )
        # Snapshot the current image so concurrent stream_image() calls don't
        # cause us to mark a newer frame as consumed.
        snapshot_image_b64 = self._latest_image_b64 if has_vision else None
        snapshot_image_generation = (
            getattr(self, "_latest_image_generation", 0) if has_vision else None
        )

        def _mark_snapshot_consumed_if_current() -> None:
            if (
                has_vision
                and getattr(self, "_latest_image_generation", 0)
                == snapshot_image_generation
            ):
                self._proactive_image_consumed = True
                if not self._supports_native_image:
                    # The completed Step annotation belongs to the consumed
                    # generation. Rearm analysis only here; response.done may
                    # belong to an unrelated response while this frame is
                    # still waiting for its proactive delivery.
                    self._image_recognized_this_turn = False
                    self._image_description = _IMAGE_ANALYSIS_PENDING_DESCRIPTION

        # Text-triggered turns do not produce the server-VAD speech_stopped
        # event that normally starts a fresh external-TTS turn. Rotate before
        # persisting visual context so activity that wins while the callback
        # is blocked cannot consume an image from an abandoned proactive turn.
        if self._has_server_vad and self.on_sid_rotate is not None:
            try:
                await self.on_sid_rotate()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "prompt_ephemeral: SID rotation failed before visual inject: %s",
                    exc,
                )
                return False
            if (
                self.is_active_response()
                or self._client_vad_active
                or (callable(user_turn_active) and user_turn_active())
                or self._user_recent_activity_time > _now
                or self._ai_recent_activity_time > _now
            ):
                logger.info(
                    "prompt_ephemeral: skipped — activity started during SID rotation"
                )
                return False

            # SID rotation can yield long enough for Core to reconcile an
            # independent/blocked visual route. A snapshot captured under the
            # old mode must not cross that boundary into a different ASR turn;
            # retry from the new route instead of sending it as either raw
            # media or an external description.
            current_external_visual_delivery = getattr(
                self,
                "_visual_delivery_mode",
                VisualDeliveryMode.NATIVE,
            ) == VisualDeliveryMode.EXTERNAL_DESCRIPTION
            if current_external_visual_delivery != external_visual_delivery:
                logger.info(
                    "prompt_ephemeral: skipped — visual route changed during SID rotation"
                )
                return False
            external_visual_delivery = current_external_visual_delivery

        if (
            has_vision
            and not self._is_gemini
            and not external_visual_delivery
            and (
                (self._supports_native_image and snapshot_image_b64)
                or (
                    not self._supports_native_image
                    and self._image_recognized_this_turn
                    and self._image_description
                )
            )
        ):
            # WebSocket-native image events can be rejected asynchronously
            # after the write succeeds. Correlate that exact error with this
            # proactive delivery before deciding the frame was consumed.
            # Gemini uses its SDK instead of client event IDs, so synchronous
            # SDK send failures are handled by stream_image() directly.
            visual_event_id = f"event_inject_image_{uuid.uuid4().hex}"
            self._inject_rejection_handlers[visual_event_id] = _on_visual_rejected
            self._fire_task(
                self._expire_inject_rejection_handler(visual_event_id, 60.0)
            )

        if (
            has_vision
            and not external_visual_delivery
            and self._supports_native_image
            and snapshot_image_b64
        ):
            # ``bypass_rate_limit`` identifies this as one deliberate cue image.
            # stream_image also owns the provider-specific wire event, including
            # the dedicated free-service input_image_buffer.append route.
            try:
                stage_result = await self.stream_image(
                    snapshot_image_b64,
                    bypass_rate_limit=True,
                    cache_latest=False,
                    event_id=visual_event_id,
                )
            except asyncio.CancelledError:
                _remove_visual_rejection_handler()
                raise
            except Exception as exc:
                _remove_visual_rejection_handler()
                logger.warning(
                    "prompt_ephemeral: native image inject failed; keeping visual context for retry: %s",
                    exc,
                )
                return False
            if hasattr(stage_result, "accepted"):
                raw_stage_mode = getattr(stage_result, "mode", None)
                stage_mode = getattr(raw_stage_mode, "value", raw_stage_mode)
                if not bool(stage_result.accepted) or stage_mode != "native":
                    _remove_visual_rejection_handler()
                    logger.info(
                        "prompt_ephemeral: visual route changed during native image staging; keeping snapshot for retry"
                    )
                    return False
            if delivery_rejected:
                _remove_visual_rejection_handler()
                logger.info(
                    "prompt_ephemeral: native image rejected before proactive text inject"
                )
                return False
        elif has_vision and external_visual_delivery and snapshot_image_b64:
            try:
                stage_result = await self.stream_image(
                    snapshot_image_b64,
                    source="proactive",
                    request_id=f"proactive-{snapshot_image_generation}",
                    bypass_rate_limit=True,
                    cache_latest=False,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "prompt_ephemeral: external visual analysis failed: %s",
                    exc,
                )
                # A transient analysis failure is retryable. Keep the exact
                # generation unconsumed and do not downgrade this attempt to a
                # text-only nudge. Only an explicit empty analysis result below
                # is terminal for the selected frame.
                return False
            external_description = str(
                getattr(stage_result, "description", "") or ""
            ).strip()
            if external_description:
                if not self._is_gemini:
                    visual_event_id = f"event_inject_image_{uuid.uuid4().hex}"
                    self._inject_rejection_handlers[
                        visual_event_id
                    ] = _on_visual_rejected
                    self._fire_task(
                        self._expire_inject_rejection_handler(
                            visual_event_id,
                            60.0,
                        )
                    )
                visual_event = {
                    "type": "conversation.item.create",
                    "item": {
                        "id": f"item_neko_visual_{uuid.uuid4().hex}",
                        "type": "message",
                        "role": "user",
                        "content": [{
                            "type": "input_text",
                            "text": (
                                "[系统视觉感知结果，不是用户陈述]\n"
                                f"当前画面：{external_description}"
                            ),
                        }],
                    },
                }
                if visual_event_id is not None:
                    visual_event["event_id"] = visual_event_id
                events_before_text = (visual_event,)
            else:
                # The selected generation reached a terminal empty/error
                # analysis result. Retire that exact snapshot so a proactive
                # retry cannot repeatedly spend vision calls on the same stale
                # frame; the generation fence preserves any newer arrival.
                _mark_snapshot_consumed_if_current()
                has_vision = False
        elif (
            has_vision
            and self._image_recognized_this_turn
            and self._image_description
        ):
            # Only standard StepFun reaches this path. Queue the description
            # in the same arbiter ticket as the proactive text/response so its
            # event id participates in the delivery outcome instead of being
            # an uncorrelated fire-and-forget conversation item.
            events_before_text = ({
                "type": "conversation.item.create",
                "event_id": visual_event_id,
                "item": {
                    "id": f"item_neko_visual_{uuid.uuid4().hex}",
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": self._image_description}],
                },
            },)

        # Re-check activity after any image await. A user or AI turn that won
        # during the visual send must preempt this proactive response.create.
        # The manager can also replace this client during the await; a retired
        # session must never receive a nudge whose result would be discarded.
        if callable(session_owned) and not session_owned():
            _remove_visual_rejection_handler()
            logger.info(
                "prompt_ephemeral: skipped — session ownership changed during visual inject"
            )
            return False
        if (
            self.is_active_response()
            or (callable(user_turn_active) and user_turn_active())
            or self._user_recent_activity_time > _now
            or self._ai_recent_activity_time > _now
        ):
            if (
                has_vision
                and not external_visual_delivery
                and self._supports_native_image
                and snapshot_image_b64
            ):
                # The raw frame is already persistent provider context and may
                # be consumed by the turn that won this race. Account for it
                # now to avoid resending duplicate/stale visual context. Keep
                # the exact rejection handler alive so a late provider error
                # can re-arm the snapshot.
                _mark_snapshot_consumed_if_current()
            else:
                # Step's description is only queued below, so no visual event
                # was sent when activity won this pre-inject gate.
                _remove_visual_rejection_handler()
            logger.info("prompt_ephemeral: skipped — activity started during visual inject")
            return False

        text = instruction.strip() or _proactive_text_instruction(
            language,
            has_vision=has_vision,
        )

        proactive_ticket = None
        try:
            inject_kwargs = {
                "on_rejected": _on_rejected,
                "on_completed": _on_completed,
            }
            if events_before_text:
                inject_kwargs["events_before_text"] = events_before_text
            proactive_ticket = await self.inject_text_and_request_response(
                text,
                **inject_kwargs,
            )
        except asyncio.CancelledError:
            # inject_text_and_request_response may discover that cancellation
            # lost to this exact ticket's successful terminal state while it
            # was still awaiting ticket.sent. Its completion callback is the
            # authoritative signal that the snapshot was delivered.
            if outcome_observed.is_set() and not delivery_rejected:
                _mark_snapshot_consumed_if_current()
            else:
                _remove_visual_rejection_handler()
            raise
        except Exception as exc:
            _remove_visual_rejection_handler()
            logger.warning(
                "prompt_ephemeral: proactive text inject failed; keeping visual context for retry: %s",
                exc,
            )
            return False
        try:
            await asyncio.wait_for(
                outcome_observed.wait(),
                timeout=_PROACTIVE_INJECT_DELIVERY_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            # The inject itself has already returned, so cancellation here
            # must target the retained ticket rather than leaving a response
            # alive whose visual snapshot this coroutine can no longer consume.
            cancellation_requested = True
            ticket_completed = False
            try:
                if proactive_ticket is not None:
                    cancellation_requested = await asyncio.shield(
                        self._ensure_response_arbiter().cancel_ticket(
                            proactive_ticket
                        )
                    )
                elif not outcome_observed.is_set():
                    await asyncio.shield(self.cancel_response())
            except Exception as cancel_exc:
                logger.warning(
                    "prompt_ephemeral: cancellation cleanup failed: %s",
                    cancel_exc,
                )
            # The receive loop may have resolved this exact ticket just before
            # cancellation reached cancel_ticket(). Its terminal no-op is
            # authoritative: consume a successful delivery instead of
            # re-offering the same visual context to the scheduler.
            if proactive_ticket is not None and not cancellation_requested:
                ticket_done = getattr(proactive_ticket, "done", None)
                if ticket_done is not None:
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(ticket_done),
                            timeout=(
                                _PROACTIVE_TICKET_CANCEL_OBSERVE_TIMEOUT_SECONDS
                            ),
                        )
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                    except Exception:
                        pass
                    else:
                        ticket_completed = True
                        _mark_snapshot_consumed_if_current()
            if not ticket_completed:
                _remove_visual_rejection_handler()
            raise
        except asyncio.TimeoutError:
            # Gemini has no exact response ticket. Its turn_complete callback
            # may settle this outcome at the timeout boundary after wait_for
            # has already chosen the timeout branch. Observe that local gate
            # before issuing an unscoped client-content interruption, which
            # could otherwise cancel a newer turn.
            outcome_settled = outcome_observed.is_set()
            if outcome_settled:
                logger.info(
                    "prompt_ephemeral: proactive outcome settled at delivery timeout boundary"
                )
            ticket_completed = False
            # ``response.done`` can resolve the exact ticket at the timeout
            # boundary before its observer gets a turn to set
            # ``outcome_observed``. Treat that completed result as the
            # authoritative delivery outcome; otherwise we would preserve an
            # already-consumed visual snapshot and resend it on the next
            # scheduler attempt.
            ticket_done = getattr(proactive_ticket, "done", None)
            if (
                not outcome_settled
                and ticket_done is not None
                and ticket_done.done()
            ):
                try:
                    ticket_done.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                else:
                    ticket_completed = True
            if not outcome_settled and ticket_completed:
                logger.info(
                    "prompt_ephemeral: proactive ticket completed at delivery timeout boundary"
                )
            if not outcome_settled and not ticket_completed:
                # Keep this request quarantined until its own terminal
                # lifecycle arrives: clearing the shared maps here would let a
                # late response.done sweep handlers registered by a retry.
                # Ask the provider to terminate the hanging response so the
                # normal response.done/error path releases the gate promptly.
                # If even the cancel lifecycle never arrives, the existing 60s
                # TTL remains the conservative final backstop.
                cancellation_requested = True
                try:
                    if proactive_ticket is not None:
                        cancellation_requested = (
                            await self._ensure_response_arbiter().cancel_ticket(
                                proactive_ticket,
                                wait=False,
                            )
                        )
                    else:
                        await self.cancel_response()
                except Exception as cancel_exc:
                    logger.warning(
                        "prompt_ephemeral: timed-out response cancel failed; keeping inject quarantined: %s",
                        cancel_exc,
                    )
                # cancel_ticket() atomically reports a terminal/missing exact
                # request as a no-op. Its worker may still need one event-loop
                # turn to publish ticket.done, so consume that authoritative
                # result before classifying the timeout as failed.
                if proactive_ticket is not None and not cancellation_requested:
                    ticket_done = getattr(proactive_ticket, "done", None)
                    if ticket_done is not None:
                        try:
                            await asyncio.shield(ticket_done)
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            pass
                        else:
                            ticket_completed = True
                if ticket_completed:
                    logger.info(
                        "prompt_ephemeral: proactive ticket completed while timeout cancellation no-op'd"
                    )
                    # Fall through to the normal success path so the matching
                    # visual snapshot is consumed exactly once.
                else:
                    logger.warning(
                        "prompt_ephemeral: proactive text delivery timed out; keeping visual context for retry"
                    )
                    _remove_visual_rejection_handler()
                    return False
        if delivery_rejected:
            if visual_delivery_rejected and proactive_ticket is not None:
                try:
                    await self._ensure_response_arbiter().cancel_ticket(
                        proactive_ticket
                    )
                except Exception as cancel_exc:
                    logger.warning(
                        "prompt_ephemeral: rejected visual response cleanup failed: %s",
                        cancel_exc,
                    )
            _remove_visual_rejection_handler()
            logger.info(
                "prompt_ephemeral: proactive text delivery failed; keeping visual context for retry: %s",
                rejection_message,
            )
            return False
        _mark_snapshot_consumed_if_current()
        # Native image validation/filtering errors may arrive after the text
        # response has completed. Keep the exact image handler until rejection
        # or its TTL so a late error can re-arm this snapshot for retry.
        logger.info(
            "prompt_ephemeral: proactive text injected (%s)",
            "vision" if has_vision else "general",
        )
        return True

    async def cancel_response(self, *, wait: bool = False, timeout: float = 3.0) -> None:
        """Cancel the current response."""
        if self._is_gemini:
            if self._gemini_session is None:
                return
            # Gemini Live has no response.cancel event. Any client_content
            # interrupts current generation; leaving turn_complete false avoids
            # immediately starting a replacement model turn.
            await self._gemini_session.send_client_content(
                turns=None,
                turn_complete=False,
            )
            return
        if wait:
            await self._ensure_response_arbiter().cancel_current(timeout)
            return
        await self.send_event({"type": "response.cancel"})
