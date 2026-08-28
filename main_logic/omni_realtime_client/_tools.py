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

from dataclasses import dataclass, field
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
    canonical_realtime_dialect,
    logger,
    time,
)


@dataclass(frozen=True)
class _ToolTaskOwner:
    connection_generation: int
    scope_generation: int
    host_turn_id: str | None
    provider_turn_host_id: str | None
    provider_session: Any


@dataclass
class _ToolBatchEntry:
    """One function call of a batch, plus the task computing its result."""

    call: ToolCall
    owner: _ToolTaskOwner
    task: asyncio.Task
    # Set when something decided this call can no longer be answered with its
    # real result -- today only the Gemini proactive settle timeout. Kept on
    # the ENTRY rather than in a task registry on purpose: the retired-task
    # set drops a task the moment it completes, so a marker living there loses
    # the race against a tool that finishes right after being abandoned and
    # its result would still be collected.
    abandoned: bool = False


@dataclass
class _ToolBatch:
    """The calls one provider response issued, answered as one unit.

    Gemini hands the whole list over in a single ``tool_call`` event, so its
    batch is complete the moment it is built. The raw protocols emit one
    ``response.function_call_arguments.done`` per call instead, so entries are
    appended as they arrive and the collector re-reads ``entries`` on every
    round -- a sibling announced while it waits still joins.
    """

    registry_key: Any = None
    entries: List[_ToolBatchEntry] = field(default_factory=list)
    collector: Optional[asyncio.Task] = None
    sealed: bool = False
    # Whether every call of this batch is known. True from construction for
    # Gemini (one event carries the whole list) and for a raw call with no
    # response identity (nothing can be proven a sibling of it). A raw batch
    # keyed by a response id stays open until that response terminates.
    closed: bool = False
    # Pulsed when an entry joins or the batch closes, so a collector that has
    # run out of work can wake on either instead of sleeping out a poll.
    changed: asyncio.Event = field(default_factory=asyncio.Event)


# Payload of the ``function_call_output`` sent for a call the client gave up
# on. English on purpose: it goes to the model, not to the user.
_ABANDONED_TOOL_CALL_REASON = (
    "This tool call was abandoned by the client before it produced a result "
    "and must not be waited for. Continue without its output."
)



class _ToolingMixin:
    _TOOL_TASK_CANCEL_TIMEOUT_S = 0.5
    # Ceiling for the collector's re-poll interval. The poll only exists to
    # notice a provider cancellation that its target refuses to honour, so it
    # starts at the cancel timeout and backs off: a batch still running after
    # several seconds is not one where another few seconds of ordering latency
    # matters, and a tool that never returns should not hold a 2Hz wakeup for
    # the rest of the user turn.
    _TOOL_BATCH_POLL_CEILING_S = 5.0

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
            # Snapshotted WITH the live read above, not re-read at result
            # time: see ``_tool_task_owner_is_current``.
            provider_turn_host_id=getattr(self, "_current_turn_host_id", None),
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
        # user inputs advance ``scope_generation`` explicitly; this clause only
        # has to catch a host rotation that happened INSIDE the provider
        # response that issued the call -- so it compares the two ids this
        # owner sampled together and never re-reads the live snapshot.
        #
        # Re-reading it here is what made both directions wrong.
        # ``_current_turn_host_id`` is re-stamped by every ``response.created``,
        # including the one a tool result itself triggers: a parallel batch's
        # faster sibling then moved the snapshot and orphaned the slower one.
        # Pinning it for the whole tool scope traded that for the mirror-image
        # bug, because sequential batches inside ONE user turn legitimately run
        # under later, rotated snapshots. Only the owner's own pair is stable
        # across both shapes.
        return (
            owner.provider_turn_host_id is None
            or owner.provider_turn_host_id == owner.host_turn_id
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
            self._retired_tool_task_registry().discard(completed)
            self._tool_batch_entries_by_task().pop(completed, None)
            self._tool_batch_by_collector_task().pop(completed, None)
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

    def _tool_batch_entries_by_task(self) -> Dict[asyncio.Task, _ToolBatchEntry]:
        registry = getattr(self, "_tool_batch_entry_by_task", None)
        if registry is None:
            registry = {}
            self._tool_batch_entry_by_task = registry
        return registry

    def _tool_batch_by_collector_task(self) -> Dict[asyncio.Task, _ToolBatch]:
        """Which batch a collector task speaks for.

        A collector owns no call of its own, so without this it is invisible
        to anything reasoning about calls. That gap had teeth: when a tool
        finished just before the proactive settle deadline but its collector
        had not flushed yet, the only unsettled task was the collector --
        retiring it answered nothing and did not stop it from sending the
        real result straight into the proactive turn.
        """

        registry = getattr(self, "_tool_batch_collector_tasks", None)
        if registry is None:
            registry = {}
            self._tool_batch_collector_tasks = registry
        return registry

    def _register_tool_batch_collector(
        self,
        batch: _ToolBatch,
        task: asyncio.Task,
    ) -> asyncio.Task:
        batch.collector = task
        self._tool_batch_by_collector_task()[task] = batch
        return task

    def _tool_batch_registry(self) -> Dict[Any, _ToolBatch]:
        """Open raw batches, keyed by the provider response that issued them."""

        registry = getattr(self, "_open_tool_batches", None)
        if registry is None:
            registry = {}
            self._open_tool_batches = registry
        return registry

    def _register_tool_batch_entry(
        self,
        batch: _ToolBatch,
        call: ToolCall,
        owner: _ToolTaskOwner,
        task: asyncio.Task,
    ) -> _ToolBatchEntry:
        entry = _ToolBatchEntry(call=call, owner=owner, task=task)
        batch.entries.append(entry)
        self._tool_batch_entries_by_task()[task] = entry
        batch.changed.set()
        return entry

    def close_raw_tool_batch(self, response_id: str | None) -> None:
        """Mark the batch a provider response issued as complete.

        Called from the receive loop when that response terminates: no further
        ``function_call_arguments.done`` can name it, so its collector may
        answer as soon as its own calls settle. Without this the collector had
        to guess, and a tool that finished before the receive loop had read
        its sibling's event made it guess wrong -- it sealed a batch of one,
        and the sibling opened a second batch, so the provider got two
        continuations each missing the other's parallel output.
        """

        if not response_id:
            return
        batch = self._tool_batch_registry().get(
            (
                self._connection_generation,
                getattr(self, "_tool_scope_generation", 0),
                str(response_id),
            )
        )
        if batch is None:
            return
        batch.closed = True
        batch.changed.set()

    def _raw_tool_batch_response_is_open(self, batch: _ToolBatch) -> bool:
        """Whether the response that issued this batch can still add to it.

        Evidence, not a timer: the batch is keyed by a response id, so it can
        only grow while that response is the one this connection is tracking.
        A provider that never announced a response leaves the tracked id None
        and gets no grace at all -- which is the pre-batch behaviour, and the
        right answer, since nothing there can prove two calls are siblings.
        """

        key = batch.registry_key
        if key is None:
            return False
        tracked = self._current_response_id
        return tracked is not None and str(tracked) == key[2]

    def _seal_tool_batch(self, batch: _ToolBatch) -> None:
        """Stop a batch from accepting siblings once its answer is going out.

        Called with no await between the collector deciding that nothing is
        pending and this, so a straggler cannot slip into a batch that has
        already been answered -- it opens a new one under the same response id
        instead, which is exactly the one-result-one-create shape this
        replaced. Degrading to that is the point: the alternative is holding
        every result until the issuing response terminates, and a provider
        that drops that terminal would then never get any answer at all.
        """

        batch.sealed = True
        if batch.registry_key is not None:
            registry = self._tool_batch_registry()
            if registry.get(batch.registry_key) is batch:
                registry.pop(batch.registry_key, None)

    def _advance_tool_scope(self) -> tuple[asyncio.Task, ...]:
        """Retire every tool owned by the preceding user/connection scope."""

        self._tool_scope_generation = getattr(self, "_tool_scope_generation", 0) + 1
        getattr(self, "_cancelled_tool_call_ids", set()).clear()
        # Batches are keyed by the scope they were opened in, so the entries
        # below could never be matched again anyway; dropping them keeps the
        # registry from growing for the life of the connection. Their
        # collectors are tool tasks and are cancelled with everything else.
        getattr(self, "_open_tool_batches", {}).clear()
        current_task = asyncio.current_task()
        tasks = tuple(getattr(self, "_tool_tasks", ()))
        retired = self._retired_tool_task_registry()
        for task in tasks:
            if not task.done():
                task.cancel()
                # Marked HERE. The cancelled-id set is cleared one line up, so
                # a handler that ignores cancellation would otherwise carry no
                # retirement marker at all and read as live forever.
                retired.add(task)
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
            retired = self._retired_tool_task_registry()
            for task in tuple(tasks_by_call_id.get(call_id, ())):
                if not task.done():
                    task.cancel()
                    retired.add(task)

    def _retired_tool_task_registry(self) -> set:
        registry = getattr(self, "_retired_tool_task_set", None)
        if registry is None:
            registry = set()
            self._retired_tool_task_set = registry
        return registry

    def _retired_tool_tasks(self) -> set:
        """Tool tasks that can no longer produce a usable result.

        RECORDED at each retirement point, not re-derived from the cancelled
        call ids. Two things retire a call -- a provider cancellation and a
        scope advance (a new user turn or a replacement connection) -- and the
        second CLEARS the cancelled-id set as it goes, so reconstructing from
        ids can only ever see the first. A handler that swallows
        CancelledError then sits in ``_tool_tasks`` with no marker at all, and
        every later wait for tool work to settle spends its whole budget on an
        answer that cannot come. Recording it where the retirement happens is
        correct for both routes, and for any third one added later.

        Results are still filtered by call id at send time. That asks a
        different question -- may this payload go out -- and stays keyed to
        the call rather than the task.
        """

        registry = self._retired_tool_task_registry()
        if registry:
            registry.difference_update(
                {task for task in registry if task.done()}
            )
        return registry

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

    def _retire_tool_tasks_as_abandoned(
        self,
        tasks,
    ) -> List[tuple[_ToolTaskOwner, List[ToolResult]]]:
        """Give up on unsettled tool calls and build their abandoned replies.

        Retired, deliberately NOT cancelled. Retirement is what stops the
        batch collector from sending the real result later -- it would land in
        a turn the call no longer belongs to, referencing a function call the
        provider has already dropped. Cancelling on top of that would abort a
        side effect the user asked for halfway through, and the caller here is
        a proactive notification: it has no business killing the user's tool.

        Grouped by owner because a reply must go out under the fences the call
        was captured with; the send path re-checks them.
        """

        entries_by_task = self._tool_batch_entries_by_task()
        batches_by_collector = self._tool_batch_by_collector_task()
        retired = self._retired_tool_task_registry()
        grouped: List[tuple[_ToolTaskOwner, List[ToolResult]]] = []
        slot_by_owner: Dict[int, int] = {}
        giving_up: List[_ToolBatchEntry] = []
        for task in tasks:
            if task.done():
                continue
            retired.add(task)
            entry = entries_by_task.get(task)
            if entry is not None:
                giving_up.append(entry)
            batch = batches_by_collector.get(task)
            if batch is not None and not batch.sealed:
                # A pending collector gives up its WHOLE batch, including
                # calls that already finished but whose result it has not
                # flushed yet. Retiring the collector alone answers nothing --
                # it owns no call -- and does not stop it from sending that
                # finished result, which after the inject below would land in
                # the proactive turn: exactly the cross-turn injection this
                # path exists to prevent. A sealed batch is already committed
                # to its own answer and is left alone.
                giving_up.extend(batch.entries)
        for entry in giving_up:
            if entry.abandoned:
                continue
            entry.abandoned = True
            slot = slot_by_owner.get(id(entry.owner))
            if slot is None:
                slot = len(grouped)
                slot_by_owner[id(entry.owner)] = slot
                grouped.append((entry.owner, []))
            grouped[slot][1].append(
                ToolResult(
                    call_id=entry.call.call_id,
                    name=entry.call.name,
                    output={"abandoned": True, "error": _ABANDONED_TOOL_CALL_REASON},
                    is_error=True,
                    error_message=_ABANDONED_TOOL_CALL_REASON,
                )
            )
        return grouped

    async def _collect_tool_batch(
        self,
        batch: _ToolBatch,
        owner: _ToolTaskOwner,
    ) -> List[ToolResult]:
        """Wait for a batch to settle, then return the results that may be sent.

        Deliberately not one ``gather``. A call the provider cancelled is
        dropped from the results below anyway, so continuing to WAIT for it
        buys nothing -- and a handler that swallows CancelledError (or sits in
        cancellation-resistant I/O) would otherwise hold every sibling's
        ``function_call_output`` hostage and stall the whole provider turn.
        Re-check the retired set on the same bound the close path already
        allows a tool to ignore cancellation for.

        Keyed by POSITION, not call_id. Gemini may omit ids, and the ingestion
        path normalizes a missing one to "" -- so keying by call_id collapses
        an anonymous parallel batch into a single entry, sending the last
        result twice and dropping the other function's real response. The
        batch is positional; keep it that way, exactly as the blanket gather
        this replaced was.
        """

        collected: Dict[int, ToolResult] = {}
        poll_interval = self._TOOL_TASK_CANCEL_TIMEOUT_S
        waited_for_closure = False
        while True:
            retired_tasks = self._retired_tool_tasks()
            pending = []
            # Re-enumerated every round: on the raw protocols a sibling can
            # still be announced while this waits, and it belongs to the same
            # provider response.
            for index, entry in enumerate(batch.entries):
                if index in collected or entry.abandoned:
                    continue
                task = entry.task
                if task.done():
                    if not task.cancelled() and task.exception() is None:
                        value = task.result()
                        if isinstance(value, ToolResult):
                            collected[index] = value
                elif task not in retired_tasks:
                    pending.append(task)
            if not pending:
                if (
                    batch.closed
                    or waited_for_closure
                    or not self._raw_tool_batch_response_is_open(batch)
                ):
                    break
                # Every known call has settled, but the provider response that
                # issued them has not terminated, so a sibling may still be
                # announced. ONE grace round, not the backoff ramp: an event
                # already sitting in the socket buffer arrives in microseconds,
                # while a provider that never terminates its responses must not
                # be able to hold every tool result for seconds. Falling
                # through answers what is in hand -- the pre-batch shape.
                waited_for_closure = True
                batch.changed.clear()
                try:
                    await asyncio.wait_for(
                        batch.changed.wait(),
                        timeout=self._TOOL_TASK_CANCEL_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    # The grace expired with no sibling and no terminal.
                    # Answering what is in hand is the intended fallback, so
                    # there is nothing to handle -- fall through and send.
                    pass
                continue
            # Deliberately NOT a total budget. Bailing out after a fixed
            # deadline would discard the result of a legitimately slow tool --
            # a long web lookup is not a stuck one, and dropping its output
            # leaves the provider with an unanswered call, which is the
            # failure this collector exists to prevent. Bound the polling COST
            # instead of the wait.
            await asyncio.wait(
                pending,
                timeout=poll_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
            poll_interval = min(
                poll_interval * 2, self._TOOL_BATCH_POLL_CEILING_S
            )
        self._seal_tool_batch(batch)
        if not self._tool_task_owner_is_current(owner):
            return []
        return [
            collected[index]
            for index, entry in enumerate(batch.entries)
            if index in collected
            and not entry.abandoned
            and not self._tool_call_was_cancelled(entry.owner, entry.call.call_id)
        ]

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

    def _open_raw_tool_batch(
        self,
        owner: _ToolTaskOwner,
        response_id: str | None,
    ) -> _ToolBatch:
        """The batch a raw call joins, opening one if its response has none.

        Keyed by the provider response, because that is the unit the model
        asked in: it emitted several parallel function calls and will not
        continue correctly until every one of them has an output. Without a
        response id there is no way to prove two calls are siblings, so each
        gets a batch of its own -- the pre-batch behaviour, kept rather than
        guessed at.
        """

        registry = self._tool_batch_registry()
        key = None
        if response_id:
            key = (owner.connection_generation, owner.scope_generation, response_id)
            open_batch = registry.get(key)
            if open_batch is not None and not open_batch.sealed:
                return open_batch
        batch = _ToolBatch(registry_key=key, closed=key is None)
        if key is not None:
            registry[key] = batch
        return batch

    def _start_raw_tool_call(
        self,
        call: ToolCall,
        owner: _ToolTaskOwner,
        *,
        response_id: str | None = None,
    ) -> asyncio.Task:
        """Run one raw function call, answered together with its siblings.

        One task per call, as before -- the tools themselves still run in
        parallel and start the moment their arguments land. What changed is
        the REPLY: a batch collector waits for every sibling of the issuing
        response and submits all the ``conversation.item.create`` items plus a
        single ``response.create``.

        Sending per result could not be made safe by ordering alone. Each
        result enqueued its own continuation, so when the first of two
        parallel calls finished, the arbiter sent its output and immediately
        started a new response while the sibling's ticket was still queued --
        the provider saw a continuation with a required parallel-call output
        missing, and answered without it.
        """

        async def _run() -> ToolResult | None:
            if not self._tool_task_owner_is_current(owner):
                return None
            return await self._execute_tool_call(call)

        task = self._create_tool_task(_run(), call_ids=(call.call_id,))
        batch = self._open_raw_tool_batch(owner, response_id)
        self._register_tool_batch_entry(batch, call, owner, task)
        if batch.collector is None:
            self._register_tool_batch_collector(
                batch,
                self._create_tool_task(self._answer_raw_tool_batch(batch, owner)),
            )
        return task

    async def _answer_raw_tool_batch(
        self,
        batch: _ToolBatch,
        owner: _ToolTaskOwner,
    ) -> None:
        results = await self._collect_tool_batch(batch, owner)
        if results:
            await self._send_tool_results_openai_realtime(results, owner=owner)

    def _start_gemini_tool_batch(
        self,
        calls: List[ToolCall],
        owner: _ToolTaskOwner,
    ) -> asyncio.Task:
        async def _execute(call: ToolCall) -> ToolResult | None:
            if not self._tool_task_owner_is_current(owner):
                return None
            return await self._execute_tool_call(call)

        # Gemini delivers the whole parallel batch in one ``tool_call`` event,
        # so this is complete on construction and never grows.
        batch = _ToolBatch(closed=True)
        for call in calls:
            self._register_tool_batch_entry(
                batch,
                call,
                owner,
                self._create_tool_task(_execute(call), call_ids=(call.call_id,)),
            )

        async def _collect() -> None:
            results = await self._collect_tool_batch(batch, owner)
            if results:
                await self._send_tool_result_gemini(
                    results,
                    provider_session=owner.provider_session,
                    owner=owner,
                )

        return self._register_tool_batch_collector(
            batch,
            self._create_tool_task(_collect()),
        )

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
        api = canonical_realtime_dialect(self._api_type)
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
        """Single-result form of :meth:`_send_tool_results_openai_realtime`."""

        await self._send_tool_results_openai_realtime([result], owner=owner)

    async def _send_tool_results_openai_realtime(
        self,
        results: List[ToolResult],
        *,
        owner: _ToolTaskOwner | None = None,
    ) -> None:
        """OpenAI Realtime / GLM Realtime / StepFun / Qwen / Free —
        send every tool result of one provider response as
        ``conversation.item.create`` items of type ``function_call_output``,
        then ONE ``response.create``.

        One ticket, not one per result: the arbiter serializes tickets, so a
        per-result ticket let the first output's continuation start while a
        sibling's was still queued, and the model answered a parallel call
        whose output had not arrived yet.

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

        api = canonical_realtime_dialect(self._api_type)
        item_events: List[Dict[str, Any]] = []
        for result in results:
            item: Dict[str, Any] = {
                "type": "function_call_output",
                "output": result.output_as_json_string(),
            }
            if api == 'glm':
                # GLM 协议不接受 call_id。哪怕我们内部合成了，也不外传。
                pass
            elif result.call_id:
                item["call_id"] = result.call_id
            item_events.append({
                "type": "conversation.item.create",
                "item": item,
            })
        if not item_events:
            return
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
            events_before_response=tuple(item_events),
            response_event={"type": "response.create"},
            event_sender=_send_owned_event if owner is not None else None,
            priority=5,
        )
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
