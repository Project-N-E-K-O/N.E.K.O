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

"""One familiar session: the loop wired to tools, context management and disk.

A session owns a harness from ``nekoclaw.runtime.loop`` and adds everything
around it:

* it is the harness's model provider, so it can manage the context before a
  request and retry once after a context overflow;
* it turns the loop's events into runtime events and appends every message to
  the session's JSONL transcript;
* it implements the commands (prompt, steer, follow-up, abort, close) and keeps
  a run going while its goal or queued notices call for it.

A session whose transcript file already exists continues that conversation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from config.prompts import prompts_nekoclaw as texts
from nekoclaw.runtime import protocol
from nekoclaw.runtime.context import (
    Compaction,
    ContextManager,
    ContextPolicy,
    RowKind,
    Transcript,
    kind_of,
)
from nekoclaw.runtime.guards import GoalRound, RepeatToolGuard, next_goal_round
from nekoclaw.runtime.host import HostBridge
from nekoclaw.runtime.loop.events import (
    AgentEvent,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from nekoclaw.runtime.loop.harness import AgentHarness, AgentHarnessConfig, SimpleCancellationToken
from nekoclaw.runtime.loop.loop import _provider_context
from nekoclaw.runtime.loop.messages import (
    AgentMessage,
    AssistantMessage,
    CustomMessage,
    ToolCall,
    Usage,
    UserMessage,
)
from nekoclaw.runtime.loop.provider import CancellationToken
from nekoclaw.runtime.loop.provider_events import (
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    TextDeltaEvent,
)
from nekoclaw.runtime.loop.session import (
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    JsonlSessionStorage,
    MessageEntry,
    SessionEntry,
    SessionInfoEntry,
    SessionState,
)
from nekoclaw.runtime.loop.session.entries import new_entry_id
from nekoclaw.runtime.loop.tool_history import repair_tool_history
from nekoclaw.runtime.loop.tools import AgentTool, AgentToolResult
from nekoclaw.runtime.loop.turns import AgentTurnContext, AgentTurnDecision
from nekoclaw.runtime.model import ContextOverflowError, LLMClientProvider, ModelRequestError
from nekoclaw.runtime.permissions import make_permission_hook
from nekoclaw.runtime.prompt import build_system_prompt
from nekoclaw.runtime.tools import build_tools
from nekoclaw.runtime.tools.base import SessionInbox, SpillStore, ToolContext
from nekoclaw.runtime.tools.goal import GOAL_NAMESPACE, GoalState
from nekoclaw.runtime.tools.jobs import (
    Job,
    JobRegistry,
    PendingNotice,
    bounded_label,
    completion_notice,
)
from nekoclaw.runtime.tools.todo import TodoState

logger = logging.getLogger(__name__)

TRANSCRIPT_FILE = "transcript.jsonl"
OUTPUTS_DIRECTORY = "outputs"
TODO_NAMESPACE = "nekoclaw.todos"
# After an abort the run is given this long to unwind before its task is cancelled.
ABORT_GRACE_SECONDS = 15.0
CLOSE_GRACE_SECONDS = 5.0
# Within one long run the runtime context is repeated after this much time.
RUNTIME_CONTEXT_INTERVAL_SECONDS = 1800.0
# Pauses before the second and third attempt at appending one transcript entry.
APPEND_RETRY_DELAYS: tuple[float, ...] = (0.2, 1.0)
_TAIL_STEP_BYTES = 65536
MAX_OUTPUT_CUT_OFF_NUDGES = 2
_INPUT_POLL_SECONDS = 0.1

EventSink = protocol.EventSink


class SessionError(Exception):
    """A session could not be created or a command could not be accepted."""


def _drive(coroutine: Any) -> Any:
    """Run a coroutine that never suspends; used to call storage from a thread."""
    try:
        coroutine.send(None)
    except StopIteration as stop:
        return stop.value
    coroutine.close()
    raise RuntimeError("the storage coroutine suspended unexpectedly")


class TranscriptStorage(JsonlSessionStorage):
    """The ported storage plus recovery from an append that was cut short."""

    def drop_torn_tail(self) -> bool:
        """Remove an incomplete last line; return whether the file was changed.

        Every entry is appended as one line that ends in a newline. A file that
        does not end in one was being written when its process died (or the
        write failed half way). Left alone, the fragment makes the transcript
        unreadable for good, and the next entry would be glued onto it. The
        fragment is an entry that was never completely written, so nothing
        that was saved is lost. A last line that is complete JSON only lacks
        its newline and is kept.
        """
        with self._locked(exclusive=True):
            try:
                with self.path.open("rb+") as file:
                    size = file.seek(0, os.SEEK_END)
                    if size == 0:
                        return False
                    file.seek(size - 1)
                    if file.read(1) == b"\n":
                        return False
                    file.seek(0)
                    data = file.read()
                    start = data.rfind(b"\n") + 1
                    try:
                        complete = isinstance(json.loads(data[start:].decode("utf-8")), dict)
                    except ValueError:
                        complete = False
                    if complete:
                        file.seek(0, os.SEEK_END)
                        file.write(b"\n")
                    else:
                        file.truncate(start)
                        logger.warning(
                            "dropped an incomplete last entry (%d bytes) from %s",
                            size - start,
                            self.path,
                        )
                    file.flush()
                    os.fsync(file.fileno())
            except FileNotFoundError:
                return False
            self._parsed = None
            return True

    def last_entry_id(self) -> str | None:
        """Return the id of the entry on the last line, or ``None`` if there is none."""
        with self._locked(exclusive=False):
            try:
                with self.path.open("rb") as file:
                    position = file.seek(0, os.SEEK_END)
                    tail = b""
                    # Read backwards until the start of the last line is in hand.
                    while position > 0:
                        step = min(_TAIL_STEP_BYTES, position)
                        position -= step
                        file.seek(position)
                        tail = file.read(step) + tail
                        body = tail.rstrip(b"\n")
                        cut = body.rfind(b"\n")
                        if cut != -1:
                            tail = body[cut + 1:]
                            break
                    else:
                        tail = tail.rstrip(b"\n")
            except FileNotFoundError:
                return None
        try:
            payload = json.loads(tail.decode("utf-8"))
        except ValueError:
            return None
        entry_id = payload.get("id") if isinstance(payload, dict) else None
        return entry_id if isinstance(entry_id, str) else None


class SessionRecorder:
    """Appends entries to the JSONL transcript in order, off the event loop.

    The storage ported with the loop does blocking file work inside its
    coroutines (lock, write, fsync). Each call is therefore driven in a worker
    thread, one at a time, by a single writer task.
    """

    def __init__(self, path: Path) -> None:
        self._storage = TranscriptStorage(path)
        self._queue: asyncio.Queue[SessionEntry] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None
        # Set when the file may end in a fragment: before the first read, and
        # after an append that failed.
        self._check_tail = True
        # Entries that never reached the disk: id -> the parent they had.
        self._lost: dict[str, str | None] = {}
        # True after an entry was given up on, until a write succeeds again.
        self._failing = False
        self.last_id: str | None = None

    def _read_blocking(self) -> list[SessionEntry]:
        if self._check_tail:
            self._storage.drop_torn_tail()
            self._check_tail = False
        return _drive(self._storage.read_all())

    def _is_saved_blocking(self, entry: SessionEntry) -> bool:
        """Settle what a failed write left behind; say whether ``entry`` is in the file.

        A fragment at the end is removed. The entry counts as saved when it is
        the last complete line: its write reached the file whole and only a
        later step (syncing it to disk) failed.
        """
        self._storage.drop_torn_tail()
        saved = self._storage.last_entry_id() == entry.id
        # Cleared only now: had either step failed, the next attempt must look
        # again instead of writing the entry a second time unseen.
        self._check_tail = False
        return saved

    def _append_blocking(self, entry: SessionEntry) -> None:
        # After a failed write the entry may be in the file already; writing
        # it a second time would give the transcript two entries with one id.
        if self._check_tail and self._is_saved_blocking(entry):
            return
        try:
            _drive(self._storage.append(entry))
        except BaseException:
            # The write may have stopped half way; look before the next one.
            self._check_tail = True
            raise

    async def read_all(self) -> list[SessionEntry]:
        return await asyncio.to_thread(self._read_blocking)

    def link(self, entry: SessionEntry) -> SessionEntry:
        """Chain ``entry`` behind the previous one and queue it for writing."""
        entry.parent_id = self.last_id
        self.last_id = entry.id
        self._queue.put_nowait(entry)
        if self._writer is None or self._writer.done():
            self._writer = asyncio.ensure_future(self._write())
        return entry

    async def _write(self) -> None:
        while True:
            entry = await self._queue.get()
            try:
                await self._append(entry)
            finally:
                self._queue.task_done()

    async def _append(self, entry: SessionEntry) -> None:
        """Write one entry, trying again after a failure before giving it up.

        A failed write must not stop the session, and it must not break the
        saved chain either: every entry names its parent, and a transcript in
        which a parent is missing cannot be loaded again. So an entry whose
        parent was given up on is attached to the nearest ancestor that did
        reach the disk.
        """
        while entry.parent_id in self._lost:
            entry.parent_id = self._lost[entry.parent_id]
        # While writes keep failing, later entries get one attempt each, so a
        # full disk does not hold up the session (or its closing) for long.
        delays = () if self._failing else APPEND_RETRY_DELAYS
        for delay in (*delays, None):
            try:
                await asyncio.to_thread(self._append_blocking, entry)
            except Exception:  # noqa: BLE001 - a failed write must not stop the session
                if delay is None:
                    logger.exception(
                        "could not append to the session transcript; the entry is dropped"
                    )
                    try:
                        saved = await asyncio.to_thread(self._is_saved_blocking, entry)
                    except Exception:  # noqa: BLE001 - the file cannot even be inspected
                        saved = False
                    if not saved:
                        self._lost[entry.id] = entry.parent_id
                    self._failing = True
                    return
                logger.warning("appending to the session transcript failed; trying again", exc_info=True)
                await asyncio.sleep(delay)
            else:
                self._failing = False
                return

    async def flush(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        await self.flush()
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)


class FamiliarHarness(AgentHarness):
    """The ported harness plus what a session needs to reach inside it."""

    @property
    def message_list(self) -> list[AgentMessage]:
        """The very list the loop appends to; rewritten in place by compaction."""
        return self._messages

    @property
    def current_signal(self) -> SimpleCancellationToken | None:
        return self._current_signal

    @property
    def cancelled(self) -> bool:
        signal = self._current_signal
        return signal is not None and signal.is_cancelled()

    def remove_queued(self, message: AgentMessage) -> bool:
        """Take one specific message object out of the queues, if it is still there."""
        for queue in (self._steering_queue, self._follow_up_queue):
            for index, queued in enumerate(queue):
                if queued is message:
                    del queue[index]
                    return True
        return False


class FamiliarSession(SessionInbox):
    """A running familiar. Create one with ``FamiliarSession.open``."""

    def __init__(
        self,
        spec: protocol.FamiliarSpec,
        host: HostBridge,
        emit: EventSink,
        *,
        model: LLMClientProvider | None = None,
        platform: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.spec = spec
        self.session_id = spec.session_id
        self._host = host
        self._emit_sink = emit
        self._model = model if model is not None else LLMClientProvider(spec.model)
        self._platform = platform if platform is not None else sys.platform
        self._clock = clock if clock is not None else (lambda: datetime.now().astimezone())
        self._seq = 0
        self._closing = False
        self._abort_requested = False
        self._run_task: asyncio.Task[None] | None = None
        self._abort_timer: asyncio.TimerHandle | None = None
        self._human_in_run = False
        self._goal_round: GoalRound | None = None
        self._last_assistant: AssistantMessage | None = None
        self._context_due = True
        self._context_injected_at = 0.0
        self._output_nudges = 0
        self._repeat = RepeatToolGuard()
        self._system = ""
        self._tools: list[AgentTool] = []
        # Set in _open.
        self._recorder: SessionRecorder
        self.harness: FamiliarHarness
        self.transcript: Transcript
        self.context: ContextManager
        self.jobs: JobRegistry
        self.todos: TodoState
        self.goals: GoalState

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    @classmethod
    async def open(
        cls,
        spec: protocol.FamiliarSpec,
        host: HostBridge,
        emit: EventSink,
        *,
        model: LLMClientProvider | None = None,
        platform: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> "FamiliarSession":
        """Create the session, continuing its transcript if one exists."""
        session = cls(spec, host, emit, model=model, platform=platform, clock=clock)
        await session._open()
        return session

    async def _open(self) -> None:
        spec = self.spec
        # Absolute from here on. The model is told where saved output lies and
        # hands such paths back to the tools, which resolve relative paths
        # against the working directory, not against this process's own.
        workdir = Path(os.path.abspath(spec.workdir))
        session_dir = Path(os.path.abspath(spec.session_dir))
        if not await asyncio.to_thread(workdir.is_dir):
            raise SessionError(f'workdir "{spec.workdir}" is not a directory')
        try:
            await asyncio.to_thread(session_dir.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            raise SessionError(f'session_dir "{spec.session_dir}" cannot be created: {exc}') from exc

        self._recorder = SessionRecorder(session_dir / TRANSCRIPT_FILE)
        try:
            entries = await self._recorder.read_all()
            state = SessionState.from_entries(entries) if entries else None
        except Exception as exc:  # noqa: BLE001 - a damaged transcript fails the start
            raise SessionError(f"the session transcript cannot be read: {exc}") from exc

        self.todos = TodoState(on_change=self._save_todos)
        self.goals = GoalState(
            default_rounds=spec.limits.goal_default_rounds,
            max_rounds=spec.limits.goal_max_rounds,
            on_change=self._save_goal,
        )
        self.jobs = JobRegistry(self._job_finished)
        spill = SpillStore(session_dir / OUTPUTS_DIRECTORY)
        tool_context = ToolContext(
            session_id=self.session_id,
            workdir=workdir,
            spill=spill,
            host=self._host,
            limits=spec.limits,
            supports_images=spec.model.supports_images,
            platform=self._platform,
            inbox=self,
            jobs=self.jobs,
            todos=self.todos,
            goals=self.goals,
            emit_report=self._emit_report,
            top_level=spec.top_level,
        )
        self._tools = build_tools(tool_context, disabled=spec.disabled_tools)
        self._system = build_system_prompt(
            tool_names=[tool.name for tool in self._tools],
            workdir=str(workdir),
            platform=self._platform,
            reply_language=spec.reply_language,
            instructions=spec.instructions,
        )

        self.harness = FamiliarHarness(
            AgentHarnessConfig(
                provider=self,
                model=spec.model.model,
                system=self._system,
                tools=self._tools,
                max_turns=spec.limits.max_turns,
                queue_mode="all",
                session_id=self.session_id,
                before_tool_call=make_permission_hook(
                    workdir=workdir,
                    host=self._host,
                    session_id=self.session_id,
                    is_cancelled=lambda: self.harness.cancelled,
                ),
                after_tool_call=self._after_tool_call,
                finish_turn=self._finish_turn,
            ),
            messages=state.messages if state is not None else (),
        )
        self.transcript = Transcript(self.harness.message_list, new_entry_id)
        if state is not None:
            self._restore(state)
        else:
            self._recorder.link(SessionInfoEntry(cwd=str(workdir)))
        self.harness.subscribe(self._on_harness_event)

        fixed_text = self._system + json.dumps(
            [[tool.name, tool.description, dict(tool.parameters)] for tool in self._tools],
            ensure_ascii=False,
        )
        from utils.tokenize import acount_tokens

        self.context = ContextManager(
            policy=ContextPolicy(
                window=spec.model.context_window, max_output=spec.model.max_output_tokens
            ),
            spill=spill,
            summarize=self._summarize,
            new_entry_id=new_entry_id,
            fixed_tokens=await acount_tokens(fixed_text),
            image_retention=dict(spec.image_retention),
        )
        if spec.goal is not None and self.goals.current is None:
            self.goals.create(spec.goal.objective, spec.goal.max_rounds)
        self._emit(
            protocol.StartedEvent(
                session_id=self.session_id,
                resumed=state is not None,
                tools=[tool.name for tool in self._tools],
            )
        )

    def _restore(self, state: SessionState) -> None:
        by_id = {entry.id: entry for entry in state.entries}
        for entry_id, message in zip(state.context_entry_ids, self.harness.message_list):
            entry = by_id.get(entry_id)
            kind: RowKind
            if entry is not None and entry.type == "compaction":
                kind = "summary"
            elif entry is not None and entry.type == "message":
                kind = kind_of(message)
            else:
                kind = "notice"
            self.transcript.register(message, entry_id, kind)
        for custom in state.custom_entries:
            if custom.namespace == TODO_NAMESPACE:
                self.todos.restore(custom.data.get("items"))
            elif custom.namespace == GOAL_NAMESPACE:
                self.goals.restore(custom.data)
        self._recorder.last_id = state.active_leaf_id

    # ------------------------------------------------------------------
    # Events and persistence
    # ------------------------------------------------------------------

    def _emit(self, event: Any) -> None:
        self._seq += 1
        event.seq = self._seq
        try:
            self._emit_sink(protocol.dump(event))
        except Exception:  # noqa: BLE001 - a broken sink must not break the session
            logger.exception("the event sink failed")

    def _emit_report(self, payload: dict[str, Any]) -> None:
        self._emit(protocol.ReportEvent(session_id=self.session_id, **payload))

    def _save_todos(self) -> None:
        self._recorder.link(CustomEntry(namespace=TODO_NAMESPACE, data={"items": self.todos.items}))

    def _save_goal(self) -> None:
        self._recorder.link(CustomEntry(namespace=GOAL_NAMESPACE, data=self.goals.to_json()))

    def _record(self, message: AgentMessage) -> None:
        """Append one message to the transcript and register it."""
        entry: SessionEntry
        if isinstance(message, CustomMessage):
            entry = CustomMessageEntry(
                custom_type=message.custom_type,
                content=message.content,
                display=message.display,
                details=message.details,
            )
            kind: RowKind = "notice"
        else:
            entry = MessageEntry(message=message)
            kind = kind_of(message)
        self._recorder.link(entry)
        self.transcript.register(message, entry.id, kind)

    def _on_harness_event(self, event: AgentEvent) -> None:
        sid = self.session_id
        if isinstance(event, MessageUpdateEvent):
            inner = event.assistant_message_event
            if isinstance(inner, TextDeltaEvent) and inner.delta:
                self._emit(protocol.TextEvent(session_id=sid, delta=inner.delta))
        elif isinstance(event, ToolExecutionStartEvent):
            self._emit(
                protocol.ToolStartEvent(
                    session_id=sid,
                    tool_call_id=event.tool_call_id,
                    tool_name=event.tool_name,
                    args=dict(event.args),
                )
            )
        elif isinstance(event, ToolExecutionUpdateEvent):
            text = event.partial_result.text
            if text:
                self._emit(
                    protocol.ToolUpdateEvent(
                        session_id=sid,
                        tool_call_id=event.tool_call_id,
                        tool_name=event.tool_name,
                        text=text,
                    )
                )
        elif isinstance(event, ToolExecutionEndEvent):
            text = event.result.text
            if event.is_error and not text.startswith("Error: "):
                text = f"Error: {text}"
            self._emit(
                protocol.ToolEndEvent(
                    session_id=sid,
                    tool_call_id=event.tool_call_id,
                    tool_name=event.tool_name,
                    is_error=event.is_error,
                    text=text,
                )
            )
        elif isinstance(event, MessageEndEvent):
            self._on_message(event.message)

    def _on_message(self, message: AgentMessage) -> None:
        if not self.transcript.is_registered(message):
            self._record(message)
        if isinstance(message, UserMessage):
            # Text from the user: it authorizes goal management for this run and
            # makes an earlier run of identical tool calls irrelevant.
            self._human_in_run = True
            self._repeat.reset()
        elif isinstance(message, AssistantMessage):
            self._last_assistant = message
            usage = message.usage
            prompt_tokens = usage.input + usage.cache_read + usage.cache_write
            if prompt_tokens or usage.output:
                # The loop appends the response right after this event.
                self.context.note_usage(usage, len(self.harness.message_list) + 1)
                self._emit(
                    protocol.UsageEvent(
                        session_id=self.session_id,
                        input=usage.input,
                        output=usage.output,
                        cache_read=usage.cache_read,
                        cache_write=usage.cache_write,
                        reasoning=usage.reasoning,
                        total_tokens=usage.total_tokens,
                        context_tokens=prompt_tokens + usage.output,
                        context_window=self.spec.model.context_window,
                    )
                )

    # ------------------------------------------------------------------
    # Model provider (the harness calls this for every model request)
    # ------------------------------------------------------------------

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        # The loop passes a copy of its history. Context management may rewrite
        # the history just before the request, so the request is built from the
        # live list instead.
        del messages, session_id
        return self._model_request(model, system, tools, signal)

    async def _model_request(
        self,
        model: str,
        system: str,
        tools: list[AgentTool],
        signal: CancellationToken | None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        if signal is None or not signal.is_cancelled():
            self._inject_runtime_context()
            await self._fit(force=False)
        retried = False
        while True:
            history = _provider_context(self.harness.message_list)
            # Held back until output follows, so a request that is retried after
            # an overflow does not announce two responses.
            held: AssistantStartEvent | None = None
            try:
                async for event in self._model.stream_response(
                    model=model, system=system, messages=history, tools=tools, signal=signal
                ):
                    if isinstance(event, AssistantStartEvent):
                        held = event
                        continue
                    if held is not None:
                        yield held
                        held = None
                    yield event
                return
            except ContextOverflowError as exc:
                if retried:
                    failure = AssistantMessage(
                        model=model,
                        stop_reason="error",
                        error_message=(
                            "the conversation does not fit the model's context window even "
                            f"after compaction: {exc}"
                        ),
                    )
                    yield AssistantErrorEvent(reason="error", error=failure)
                    return
                retried = True
                logger.warning("context overflow reported by the provider; compacting and retrying")
                await self._fit(force=True)

    async def _fit(self, *, force: bool) -> None:
        compactions = await self.context.fit(self.transcript, force=force)
        for compaction in compactions:
            self._persist_compaction(compaction)

    def _persist_compaction(self, compaction: Compaction) -> None:
        self._recorder.link(
            CompactionEntry(
                id=compaction.summary_row.entry_id,
                summary=compaction.stored_summary,
                replaces_entry_ids=list(compaction.replaced_entry_ids),
                tokens_before=compaction.tokens_before,
                usage=compaction.usage,
                model=self.spec.model.model,
            )
        )

    async def _summarize(self, messages: list[AgentMessage], max_tokens: int) -> tuple[str, Usage | None]:
        request = [*messages, UserMessage(content=texts.COMPACTION_INSTRUCTION)]
        repaired = list(repair_tool_history(tuple(request)).messages)
        signal = self.harness.current_signal
        try:
            reply = await self._model.complete(
                system=self._system,
                messages=repaired,
                tools=self._tools,
                max_tokens=max_tokens,
                signal=signal,
            )
        except ModelRequestError:
            if signal is not None and signal.is_cancelled():
                # An abort must not be mistaken for a failed summary, which would
                # drop the older part of the conversation.
                raise asyncio.CancelledError from None
            raise
        return reply.text, reply.usage

    def _inject_runtime_context(self) -> None:
        """Append the time, running jobs and open todos as a message when due."""
        loop_time = asyncio.get_running_loop().time()
        if not self._context_due and (
            loop_time - self._context_injected_at < RUNTIME_CONTEXT_INTERVAL_SECONDS
        ):
            return
        now = self._clock()
        text = texts.runtime_context_notice(
            f"{now.strftime('%Y-%m-%d %H:%M %z')} ({now.strftime('%A')})",
            [f"{job.id} ({job.kind}: {bounded_label(job.label)})" for job in self.jobs.running()],
            [(item["status"], item["content"]) for item in self.todos.open_items()],
        )
        message = CustomMessage(custom_type="notice:runtime_context", content=text, display=False)
        self.harness.message_list.append(message)
        self._record(message)
        self._context_due = False
        self._context_injected_at = loop_time

    # ------------------------------------------------------------------
    # Loop hooks
    # ------------------------------------------------------------------

    async def _after_tool_call(
        self, call: ToolCall, result: AgentToolResult, is_error: bool
    ) -> tuple[AgentToolResult, bool]:
        result = await self.context.cap_tool_result(call.name, call.id, result)
        reminder = self._repeat.observe(call.name, call.arguments)
        if reminder is not None:
            self.deliver_notice("repeat_tool", reminder)
        return result, is_error

    def _finish_turn(
        self, turn: AgentTurnContext, signal: CancellationToken | None
    ) -> AgentTurnDecision | None:
        message = turn.message
        cancelled = signal is not None and signal.is_cancelled()
        if message.stop_reason == "length" and not message.tool_calls and not cancelled:
            if self._output_nudges < MAX_OUTPUT_CUT_OFF_NUDGES:
                self._output_nudges += 1
                self.harness.steer_message(
                    CustomMessage(
                        custom_type="notice:output_cut_off",
                        content=texts.OUTPUT_CUT_OFF_NUDGE,
                        display=False,
                    )
                )
        else:
            self._output_nudges = 0
        return None

    # ------------------------------------------------------------------
    # SessionInbox (what tools may ask the session)
    # ------------------------------------------------------------------

    def has_pending_input(self) -> bool:
        return self.harness.has_queued_messages()

    def has_pending_user_steer(self) -> bool:
        return any(
            isinstance(message, UserMessage) for message in self.harness.queued_messages.steering
        )

    async def wait_for_input(self, timeout_seconds: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while not self.has_pending_input():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(_INPUT_POLL_SECONDS, remaining))
        return True

    def in_direct_human_turn(self) -> bool:
        return self._human_in_run

    def in_goal_round(self, goal_id: str, revision: int, round_number: int) -> bool:
        return self._goal_round == GoalRound(goal_id, revision, round_number)

    def deliver_notice(self, kind: str, text: str) -> object:
        message = CustomMessage(custom_type=f"notice:{kind}", content=text, display=False)
        if self._closing:
            return message
        if self.running:
            self.harness.steer_message(message)
        else:
            self._begin_run(message)
        return message

    def withdraw_notice(self, handle: object) -> bool:
        return isinstance(handle, CustomMessage) and self.harness.remove_queued(handle)

    def _job_finished(self, job: Job) -> None:
        if self._closing:
            return
        text, held = completion_notice(self.jobs, job)
        handle = self.deliver_notice("job", text)
        self.jobs.pending_notices[job.id] = PendingNotice(handle, held)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._run_task is not None

    @property
    def busy(self) -> bool:
        """True while a run is in progress or a background job is still running."""
        return self.running or bool(self.jobs.running())

    def prompt(self, text: str) -> None:
        if self._closing:
            raise SessionError("the session is closed")
        if self.running:
            raise SessionError("a run is in progress; use steer or follow_up to add to it")
        self._begin_run(UserMessage(content=text))

    def steer(self, text: str) -> None:
        if self._closing:
            raise SessionError("the session is closed")
        if self.running:
            self.harness.steer_message(UserMessage(content=text))
        else:
            self._begin_run(UserMessage(content=text))

    def follow_up(self, text: str) -> None:
        if self._closing:
            raise SessionError("the session is closed")
        if self.running:
            self.harness.follow_up_message(UserMessage(content=text))
        else:
            self._begin_run(UserMessage(content=text))

    def abort(self) -> bool:
        """Stop the current run and the session's background jobs.

        Returns False when there was nothing to stop. Background jobs go too:
        a job finishing later would otherwise wake a session that was told to
        stop.
        """
        stopped_jobs = self.jobs.stop_all("run aborted")
        task = self._run_task
        if task is None:
            return stopped_jobs > 0
        self._abort_requested = True
        self.harness.clear_queues()
        self.harness.cancel()
        if self._abort_timer is None:
            self._abort_timer = asyncio.get_running_loop().call_later(
                ABORT_GRACE_SECONDS, self._force_stop, task
            )
        return True

    def _force_stop(self, task: asyncio.Task[None]) -> None:
        self._abort_timer = None
        if self._run_task is task and not task.done():
            logger.warning("session %s did not stop after an abort; cancelling its run", self.session_id)
            task.cancel()

    async def close(self) -> None:
        """Stop everything this session owns and announce that it is gone."""
        if self._closing:
            return
        self._closing = True
        task = self._run_task
        if task is not None:
            self._abort_requested = True
            self.harness.clear_queues()
            self.harness.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), CLOSE_GRACE_SECONDS)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except Exception:  # noqa: BLE001 - the run already reported its own failure
                pass
        if self._abort_timer is not None:
            self._abort_timer.cancel()
            self._abort_timer = None
        self.jobs.close()
        await self.jobs.wait_closed()
        await self._recorder.close()
        await self._model.aclose()
        self._emit(protocol.EndedEvent(session_id=self.session_id, reason="closed"))

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def _begin_run(self, message: AgentMessage, goal_round: GoalRound | None = None) -> None:
        self._abort_requested = False
        self._run_task = asyncio.ensure_future(self._run(message, goal_round))

    async def _run(self, message: AgentMessage, goal_round: GoalRound | None) -> None:
        reason: protocol.EndReason = "completed"
        error: str | None = None
        self._last_assistant = None
        try:
            stream: AsyncIterator[AgentEvent] | None
            if self._abort_requested:
                # abort() came before this task first ran. The harness had no
                # cancellation token yet, so there was nothing to cancel: the
                # run must not start at all.
                reason = "aborted"
                stream = None
            else:
                stream = self.harness.prompt_message(message)
            while stream is not None:
                self._human_in_run = False
                self._goal_round = goal_round
                # Stated before the text that starts this stretch of work, so that
                # text stays the last thing the model reads.
                self._context_due = True
                self._inject_runtime_context()
                async for _event in stream:
                    pass
                last = self._last_assistant
                if self._abort_requested or (last is not None and last.stop_reason == "aborted"):
                    reason = "aborted"
                    break
                if last is not None and last.stop_reason == "error":
                    reason = "error"
                    error = last.error_message
                    break
                await self._recorder.flush()
                if self._abort_requested:
                    # Aborted during the flush, between two stretches of work:
                    # again there was no token to cancel.
                    reason = "aborted"
                    break
                if self.harness.has_queued_messages():
                    # A notice arrived while the run was ending: keep going.
                    stream = self.harness.continue_()
                    continue
                following = next_goal_round(self.goals)
                if following is None:
                    break
                prompt, goal_round = following
                stream = self.harness.prompt_message(
                    CustomMessage(custom_type="notice:goal_round", content=prompt, display=False)
                )
        except asyncio.CancelledError:
            if not (self._abort_requested or self._closing):
                self._run_task = None
                raise
            reason = "aborted"
        except Exception as exc:  # noqa: BLE001 - reported through the ended event
            logger.exception("session %s: the run failed", self.session_id)
            reason = "error"
            error = f"{type(exc).__name__}: {exc}"

        if self._abort_timer is not None:
            self._abort_timer.cancel()
            self._abort_timer = None
        if reason != "completed":
            self.goals.disarm()
            await self._recorder.flush()
        if reason == "aborted":
            self.harness.clear_queues()
        self._goal_round = None
        self._human_in_run = False
        self._run_task = None
        last = self._last_assistant
        self._emit(
            protocol.EndedEvent(
                session_id=self.session_id,
                reason=reason,
                stop_reason=last.stop_reason if last is not None else None,
                text=last.text if last is not None else "",
                error=error,
            )
        )
