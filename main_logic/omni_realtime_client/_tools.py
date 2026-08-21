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

from dataclasses import dataclass
import hashlib

from ._shared import (
    Any,
    asyncio,
    Awaitable,
    Dict,
    List,
    OnToolCallCallback,
    Optional,
    ToolCall,
    ToolDefinition,
    ToolResult,
    logger,
    time,
)


@dataclass(frozen=True)
class _ToolTaskOwner:
    connection_generation: int
    scope_generation: int
    host_turn_id: str | None
    provider_session: Any



class _ToolingMixin:
    _TOOL_TASK_CANCEL_TIMEOUT_S = 0.5

    def set_tools(self, tool_definitions: Optional[List[ToolDefinition]]) -> None:
        """Replace the active tool list. Takes effect the next time the
        client builds its session config (next ``connect`` call). For an
        already-connected session, callers can also call
        ``apply_tools_to_session`` to push the new list mid-conversation
        (only providers whose protocol allows mid-session tool updates
        will honour it; OpenAI Realtime and Step accept ``session.update``
        with new ``tools``)."""
        self._tool_definitions = list(tool_definitions or [])

    def set_tool_call_handler(self, handler: Optional[OnToolCallCallback]) -> None:
        self.on_tool_call = handler

    def has_tools(self) -> bool:
        return bool(self._tool_definitions) and self.on_tool_call is not None

    def _capture_tool_task_owner(
        self,
        provider_session: Any,
        *,
        connection_generation: int | None = None,
    ) -> _ToolTaskOwner:
        return _ToolTaskOwner(
            connection_generation=(
                self._connection_generation
                if connection_generation is None
                else connection_generation
            ),
            scope_generation=getattr(self, "_tool_scope_generation", 0),
            host_turn_id=self._read_host_turn_id(),
            provider_session=provider_session,
        )

    def _tool_task_owner_is_current(self, owner: _ToolTaskOwner) -> bool:
        if owner.connection_generation != self._connection_generation:
            return False
        if owner.scope_generation != getattr(self, "_tool_scope_generation", 0):
            return False
        live_session = self._gemini_session if self._is_gemini else self.ws
        if owner.provider_session is not live_session:
            return False
        if owner.host_turn_id is None:
            return True
        # Providers without server VAD rotate the host speech id at the
        # function-calling response.done before the tool result is ready. That
        # ends a provider response, not the user turn that owns the tool. New
        # user inputs advance ``scope_generation`` explicitly; compare the
        # captured id only with the provider turn's start snapshot so a normal
        # end-of-response rotation cannot discard a legal result.
        provider_turn_host_id = getattr(self, "_current_turn_host_id", None)
        return (
            provider_turn_host_id is None
            or provider_turn_host_id == owner.host_turn_id
        )

    def _tool_task_connection_is_current(self, owner: _ToolTaskOwner) -> bool:
        """Keep cancellation on the captured connection, independent of turn scope."""

        live_session = self._gemini_session if self._is_gemini else self.ws
        return bool(
            owner.connection_generation == self._connection_generation
            and owner.provider_session is live_session
        )

    def _track_tool_task(
        self,
        task: asyncio.Task,
        *,
        call_ids: tuple[str, ...] = (),
    ) -> asyncio.Task:
        tool_tasks = getattr(self, "_tool_tasks", None)
        if tool_tasks is None:
            tool_tasks = set()
            self._tool_tasks = tool_tasks
        tasks_by_call_id = getattr(self, "_tool_tasks_by_call_id", None)
        if tasks_by_call_id is None:
            tasks_by_call_id = {}
            self._tool_tasks_by_call_id = tasks_by_call_id
        tool_tasks.add(task)
        tracked_ids = tuple(call_id for call_id in call_ids if call_id)
        for call_id in tracked_ids:
            tasks_by_call_id.setdefault(call_id, set()).add(task)

        def _done(completed: asyncio.Task) -> None:
            tool_tasks.discard(completed)
            for call_id in tracked_ids:
                tasks = tasks_by_call_id.get(call_id)
                if tasks is None:
                    continue
                tasks.discard(completed)
                if not tasks:
                    tasks_by_call_id.pop(call_id, None)
            if not completed.cancelled():
                error = completed.exception()
                if error is not None:
                    call_fingerprints = ",".join(
                        hashlib.sha256(call_id.encode("utf-8")).hexdigest()[:12]
                        for call_id in tracked_ids
                    )
                    logger.error(
                        "Realtime tool task failed task=%s "
                        "call_fingerprints=%s error_type=%s",
                        completed.get_name(),
                        call_fingerprints or "none",
                        type(error).__name__,
                    )

        task.add_done_callback(_done)
        return task

    def _create_tool_task(
        self,
        coro: Awaitable[Any],
        *,
        call_ids: tuple[str, ...] = (),
    ) -> asyncio.Task:
        return self._track_tool_task(
            asyncio.create_task(coro),
            call_ids=call_ids,
        )

    def has_inflight_tool_turn(self) -> bool:
        """Whether proactive injection must wait for the current tool turn.

        A tracked task covers callback execution and result submission. Raw
        realtime then has an exact arbiter ticket for the model continuation;
        Gemini has no response id, so it retains the captured owner until the
        next terminal event.
        """

        if any(
            not task.done()
            for task in tuple(getattr(self, "_tool_tasks", ()))
        ):
            return True
        if any(
            not continuation.done()
            for continuation in tuple(
                getattr(self, "_tool_continuation_futures", ())
            )
        ):
            return True
        owner = getattr(self, "_gemini_tool_continuation_owner", None)
        return bool(owner is not None and self._tool_task_owner_is_current(owner))

    def _track_raw_tool_continuation(self, completion: asyncio.Future) -> None:
        continuations = getattr(self, "_tool_continuation_futures", None)
        if continuations is None:
            continuations = set()
            self._tool_continuation_futures = continuations
        continuations.add(completion)
        completion.add_done_callback(continuations.discard)

    def _settle_gemini_tool_continuation(
        self,
        *,
        connection_generation: int,
        provider_session: Any,
    ) -> None:
        owner = getattr(self, "_gemini_tool_continuation_owner", None)
        if owner is None:
            return
        if (
            owner.connection_generation == connection_generation
            and owner.provider_session is provider_session
        ):
            self._gemini_tool_continuation_owner = None

    def _advance_tool_scope(self) -> tuple[asyncio.Task, ...]:
        """Retire every tool owned by the preceding user/connection scope."""

        self._tool_scope_generation = getattr(self, "_tool_scope_generation", 0) + 1
        getattr(self, "_cancelled_tool_call_ids", set()).clear()
        getattr(self, "_tool_continuation_futures", set()).clear()
        self._gemini_tool_continuation_owner = None
        current_task = asyncio.current_task()
        tasks = tuple(getattr(self, "_tool_tasks", ()))
        for task in tasks:
            if not task.done():
                task.cancel()
        return tuple(task for task in tasks if task is not current_task)

    def note_user_turn_started(self) -> None:
        """Invalidate tool work from the turn that the new user turn replaces."""

        self._advance_tool_scope()

    def _cancel_tool_call_ids(self, call_ids: List[str]) -> None:
        cancelled_ids = getattr(self, "_cancelled_tool_call_ids", None)
        if cancelled_ids is None:
            cancelled_ids = set()
            self._cancelled_tool_call_ids = cancelled_ids
        stable_call_ids = set(str(call_id) for call_id in call_ids if call_id)
        for call_id in stable_call_ids:
            cancelled_ids.add(
                (
                    self._connection_generation,
                    getattr(self, "_tool_scope_generation", 0),
                    call_id,
                )
            )
            tasks_by_call_id = getattr(self, "_tool_tasks_by_call_id", {})
            for task in tuple(tasks_by_call_id.get(call_id, ())):
                if not task.done():
                    task.cancel()

    def _tool_call_was_cancelled(
        self,
        owner: _ToolTaskOwner,
        call_id: str,
    ) -> bool:
        return (
            owner.connection_generation,
            owner.scope_generation,
            call_id,
        ) in getattr(self, "_cancelled_tool_call_ids", set())

    async def _await_retired_tool_tasks(
        self,
        tasks: tuple[asyncio.Task, ...],
    ) -> None:
        pending = tuple(task for task in tasks if not task.done())
        if not pending:
            return
        _, still_pending = await asyncio.wait(
            pending,
            timeout=self._TOOL_TASK_CANCEL_TIMEOUT_S,
        )
        if still_pending:
            logger.warning(
                "Realtime close: %d tool task(s) ignored cancellation; "
                "their retired owner will block any later result injection",
                len(still_pending),
            )

    def _start_raw_tool_call(
        self,
        call: ToolCall,
        owner: _ToolTaskOwner,
    ) -> asyncio.Task:
        async def _run() -> None:
            if not self._tool_task_owner_is_current(owner):
                return
            result = await self._execute_tool_call(call)
            if not self._tool_task_owner_is_current(owner):
                return
            await self._send_tool_result_openai_realtime(result, owner=owner)

        return self._create_tool_task(_run(), call_ids=(call.call_id,))

    def _start_gemini_tool_batch(
        self,
        calls: List[ToolCall],
        owner: _ToolTaskOwner,
    ) -> asyncio.Task:
        async def _execute(call: ToolCall) -> ToolResult | None:
            if not self._tool_task_owner_is_current(owner):
                return None
            return await self._execute_tool_call(call)

        call_tasks = [
            self._create_tool_task(_execute(call), call_ids=(call.call_id,))
            for call in calls
        ]

        async def _collect() -> None:
            outcomes = await asyncio.gather(*call_tasks, return_exceptions=True)
            if not self._tool_task_owner_is_current(owner):
                return
            results = [
                outcome
                for call, outcome in zip(calls, outcomes)
                if isinstance(outcome, ToolResult)
                and not self._tool_call_was_cancelled(owner, call.call_id)
            ]
            if results:
                await self._send_tool_result_gemini(
                    results,
                    provider_session=owner.provider_session,
                    owner=owner,
                )

        return self._create_tool_task(_collect())

    def _tools_for_openai_realtime(self) -> List[Dict[str, Any]]:
        """OpenAI Realtime / GLM Realtime schema — flat (type/name/
        description/parameters at the same level)."""
        return [t.to_openai_realtime() for t in self._tool_definitions] if self.has_tools() else []

    def _tools_for_step(self) -> List[Dict[str, Any]]:
        """StepFun Realtime schema — nested under ``function``."""
        return [t.to_openai_chat() for t in self._tool_definitions] if self.has_tools() else []

    def _tools_for_qwen(self) -> List[Dict[str, Any]]:
        """Qwen-Omni-Realtime schema — nested under ``function``, same shape
        as StepFun (see the example in the Aliyun client-events docs)."""
        return [t.to_openai_chat() for t in self._tool_definitions] if self.has_tools() else []

    async def apply_tools_to_session(self) -> None:
        """Push the current tools list to the connected session
        mid-conversation. Caller is responsible for calling this only
        after the session is connected."""
        if not self.ws and not self._gemini_session:
            return
        if self._is_gemini:
            # Gemini Live API does not support session.update mid-session;
            # tool list is fixed at connect time. Log + ignore.
            logger.info("apply_tools_to_session: Gemini Live does not support mid-session tools update — ignoring")
            return
        api = self._api_type.lower()
        if api == 'step' or api == 'free':
            # stepaudio-2.5-realtime 不再支持内置 web_search，与
            # update_session 初始化路径保持一致：只发 caller 注册的
            # function tools。
            tools_payload: List[Dict[str, Any]] = self._tools_for_step()
            await self.update_session({"tools": tools_payload})
        elif api == 'gpt':
            payload: Dict[str, Any] = {"tools": self._tools_for_openai_realtime()}
            if self.has_tools():
                payload["tool_choice"] = "auto"
            await self.update_session(payload)
        elif api == 'grok':
            # xAI Grok 走 OpenAI Realtime 协议，schema 与 GPT 同构。
            payload: Dict[str, Any] = {"tools": self._tools_for_openai_realtime()}
            if self.has_tools():
                payload["tool_choice"] = "auto"
            await self.update_session(payload)
        elif api == 'glm':
            # GLM 文档要求："ServerVAD 时更新 tools 需同时传入 turn_detection"。
            # 此方法的调用前提是已 connect()，连接时已把 turn_detection 设成
            # server_vad —— 这里复发同样的值即可，免得服务端 reset 成默认。
            await self.update_session({
                "tools": self._tools_for_openai_realtime(),
                "turn_detection": {"type": "server_vad"},
            })
        elif api == 'qwen':
            # Qwen-Omni-Realtime: tools 与 enable_search 互斥；当我们
            # 注册了自定义工具，强制关掉 enable_search 防止 server 拒绝。
            qwen_payload: Dict[str, Any] = {"tools": self._tools_for_qwen()}
            if self.has_tools():
                qwen_payload["enable_search"] = False
            await self.update_session(qwen_payload)
        else:
            logger.info("apply_tools_to_session: api_type=%s does not support custom tools — ignoring", api)

    async def _execute_tool_call(self, call: ToolCall) -> ToolResult:
        """Run the user-supplied ``on_tool_call`` callback and trap any
        exception so we still return a structured ``ToolResult`` the
        provider can ingest (model usually recovers from a tool error
        gracefully)."""
        if self.on_tool_call is None:
            msg = "no on_tool_call handler bound"
            return ToolResult(
                call_id=call.call_id, name=call.name,
                output={"error": msg}, is_error=True, error_message=msg,
            )

        # [ISSUE4c] Sliding-window tool-call flood guard. Count tool executions
        # in the last _TOOL_CALL_WINDOW_S; once it exceeds _TOOL_CALL_WINDOW_MAX,
        # do NOT execute — return a hard STOP warning as the function_call_output
        # so the model (which has no per-turn tool cap of its own) is told to
        # stop calling tools and respond by voice instead. The function_call and
        # this warning output both stay in the conversation via the normal
        # function_call_output path, so the model still "sees" that it tried.
        _TOOL_CALL_WINDOW_S = 15.0
        _TOOL_CALL_WINDOW_MAX = 4
        _now_tc = time.time()
        self._recent_tool_call_times = [
            t for t in self._recent_tool_call_times if _now_tc - t < _TOOL_CALL_WINDOW_S
        ]
        if len(self._recent_tool_call_times) >= _TOOL_CALL_WINDOW_MAX:
            logger.warning(
                "OmniRealtimeClient: tool-call flood guard tripped (%d calls in %.0fs) — "
                "refusing '%s', telling model to stop",
                len(self._recent_tool_call_times), _TOOL_CALL_WINDOW_S, call.name,
            )
            return ToolResult(
                call_id=call.call_id, name=call.name,
                output={
                    "stop": True,
                    "warning": (
                        f"本轮短时间内已调用工具 {len(self._recent_tool_call_times)} 次，已达上限。"
                        f"停止调用任何工具（包括 {call.name}），不要重试、不要换措辞再调。"
                        "直接用语音回应，等需要时再调用。本次未执行。"
                    ),
                },
                is_error=True, error_message="tool-call rate limit reached",
            )
        self._recent_tool_call_times.append(_now_tc)

        try:
            return await self.on_tool_call(call)
        except Exception as e:
            logger.exception("OmniRealtimeClient: on_tool_call '%s' raised", call.name)
            return ToolResult(
                call_id=call.call_id, name=call.name,
                output={"error": f"{type(e).__name__}: {e}"},
                is_error=True, error_message=str(e),
            )

    async def _send_tool_result_openai_realtime(
        self,
        result: ToolResult,
        *,
        owner: _ToolTaskOwner | None = None,
    ) -> None:
        """OpenAI Realtime / GLM Realtime / StepFun / Qwen / Free —
        send tool result via ``conversation.item.create`` of type
        ``function_call_output``, then ``response.create``.

        ⚠️ Provider differences:
        - OpenAI gpt / StepFun / Qwen / Free: ``call_id`` is required;
          the server uses it to bind the result back to the corresponding
          function_call.
        - GLM: the documented example shows function_call_output with
          **only an output field**, and the server's
          ``function_call_arguments.done`` carries no call_id either. The
          ``glm_<rid>_<idx>`` we synthesize at the done event is solely for
          internal registry tracking and must never be sent back to the
          server, or the request is likely to be rejected.
        """
        if owner is not None and not self._tool_task_owner_is_current(owner):
            return

        item: Dict[str, Any] = {
            "type": "function_call_output",
            "output": result.output_as_json_string(),
        }
        api = self._api_type.lower()
        if api == 'glm':
            # GLM 协议不接受 call_id。哪怕我们内部合成了，也不外传。
            pass
        elif result.call_id:
            item["call_id"] = result.call_id
        item_event = {
            "type": "conversation.item.create",
            "item": item,
        }
        arbiter = self._ensure_response_arbiter()

        async def _send_owned_event(event: Dict[str, Any]) -> None:
            is_cancel = event.get("type") == "response.cancel"
            await self.send_event(
                event,
                send_guard=(
                    (lambda: self._tool_task_connection_is_current(owner))
                    if is_cancel
                    else (lambda: self._tool_task_owner_is_current(owner))
                ),
            )

        ticket = await arbiter.enqueue(
            source="tool_result",
            events_before_response=(item_event,),
            response_event={"type": "response.create"},
            event_sender=_send_owned_event if owner is not None else None,
            priority=5,
        )
        self._track_raw_tool_continuation(ticket.done)
        try:
            if owner is not None and not self._tool_task_owner_is_current(owner):
                await arbiter.cancel_ticket(ticket, wait=False)
                return
            await asyncio.shield(ticket.sent)
            if owner is not None and not self._tool_task_owner_is_current(owner):
                await arbiter.cancel_ticket(ticket, wait=False)
        except asyncio.CancelledError:
            await asyncio.shield(arbiter.cancel_ticket(ticket, wait=False))
            raise
