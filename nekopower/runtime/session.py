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

"""One task agent session: the loop wired to tools, context management and disk.

A session owns a harness from ``nekopower.runtime.loop`` and adds everything
around it:

* it is the harness's model provider, so it can manage the context before a
  request and retry once after a context overflow;
* it turns the loop's events into runtime events and appends every message to
  the session's JSONL transcript;
* it implements the commands (prompt, steer, follow-up, abort, close) and keeps
  a run going while its goal or queued notices call for it.

A session whose transcript file already exists continues that conversation,
with the system prompt and the tool definitions it was opened with before
(``snapshot.py``): the head of its requests stays the same from one
commission to the next. They are put together anew after the conversation
was summed up, and not before: a tool that came since the session was first
opened is told of from then on.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path
from typing import Any

from config.nekopower_settings import BUILTIN_GROUP_BROWSER
from config.prompts import prompts_nekopower as texts
from nekopower.runtime import protocol
from nekopower.runtime import fork
from nekopower.runtime.context import (
    Compaction,
    ContextManager,
    ContextPolicy,
    Row,
    RowKind,
    SummaryCutShort,
    SummaryUsedToolError,
    Transcript,
    cap_parallel_batch_results,
    compact_ratio,
    estimate_tokens,
    kind_of,
    output_allowance,
)
from nekopower.runtime.guards import GoalRound, RepeatToolGuard, next_goal_round
from nekopower.runtime.host import HostBridge
from nekopower.runtime.loop.events import (
    AgentEvent,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
)
from nekopower.runtime.loop.harness import AgentHarness, AgentHarnessConfig, SimpleCancellationToken
from nekopower.runtime.loop.loop import _provider_context
from nekopower.runtime.loop.messages import (
    AgentMessage,
    AssistantMessage,
    CustomMessage,
    ImageContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from nekopower.runtime.loop.provider import CancellationToken
from nekopower.runtime.loop.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    TextDeltaEvent,
)
from nekopower.runtime.loop.session import (
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    JsonlSessionStorage,
    MessageEntry,
    SessionEntry,
    SessionInfoEntry,
    SessionState,
)
from nekopower.runtime.loop.session.entries import new_entry_id
from nekopower.runtime.loop.tool_history import repair_tool_history
from nekopower.runtime.loop.tools import AgentTool, AgentToolResult
from nekopower.runtime.loop.turns import AgentTurnContext, AgentTurnDecision
from nekopower.runtime.model import (
    WIRE_TEXT_PART_MAX_TOKENS,
    ContextOverflowError,
    LLMClientProvider,
    ModelRequestError,
    RequestRefusedError,
    parse_combined_overflow,
    window_from_overflow,
)
from nekopower.runtime.permissions import make_permission_hook
from nekopower.runtime.prompt import build_system_prompt
from nekopower.runtime.snapshot import (
    REASON_COMPACTED,
    REASON_MOVED,
    REASON_OPENED,
    REASON_PRESET,
    SystemSnapshot,
    ToolDefinition,
    definitions_of,
    read_snapshot,
    write_snapshot,
)
from nekopower.runtime.tools import build_tools
from nekopower.runtime.tools.base import SessionInbox, SpillStore, ToolContext, ToolError, define_tool
from nekopower.runtime.tools.goal import GOAL_NAMESPACE, GoalState
from nekopower.runtime.tools.jobs import (
    NOTICE_INLINE_BYTES,
    Job,
    JobRegistry,
    PendingNotice,
    bounded_label,
    completion_notice,
    preview_completion_notice,
)
from nekopower.runtime.tools.todo import TodoState

logger = logging.getLogger(__name__)

TRANSCRIPT_FILE = fork.TRANSCRIPT_FILE
OUTPUTS_DIRECTORY = fork.OUTPUTS_DIRECTORY
TODO_NAMESPACE = "nekopower.todos"
# Which entry holds the text that opened the commission under way (``prompt`` with ``opening``).
COMMISSION_NAMESPACE = "nekopower.commission"
# The smallest window a session is managed for; ``protocol.ModelConfig`` takes no less.
MIN_CONTEXT_WINDOW = protocol.MIN_CONTEXT_WINDOW
# After an abort the run is given this long to unwind before its task is cancelled.
ABORT_GRACE_SECONDS = 15.0
CLOSE_GRACE_SECONDS = 5.0
# Within one long run the runtime context is repeated after this much time.
RUNTIME_CONTEXT_INTERVAL_SECONDS = 1800.0
# Pauses before the second and third attempt at appending one transcript entry.
APPEND_RETRY_DELAYS: tuple[float, ...] = (0.2, 1.0)
_TAIL_STEP_BYTES = 65536
MAX_OUTPUT_CUT_OFF_NUDGES = 3
MAX_EMPTY_COMPLETION_NUDGES = 2
MAX_INVALID_TOOL_NUDGES = 2
_INPUT_POLL_SECONDS = 0.1
_REPLY_LANGUAGE_RE = re.compile(
    r"questions with their options, in ([^,.\n]+), whatever language",
)

EventSink = protocol.EventSink


class SessionError(Exception):
    """A session could not be created or a command could not be accepted."""


def _stand_in(definition: ToolDefinition) -> AgentTool:
    """Return a tool that answers for one the session was opened with and that is not there now.

    The model is still told of it (the tool definitions of a session stay
    as they were), so a call has to be answered: with what it can do about it.
    """

    async def unavailable(args: dict[str, Any], call: Any) -> str:
        del args, call
        raise ToolError(texts.TOOL_UNAVAILABLE.replace("<<NAME>>", definition.name))

    return define_tool(
        {
            "name": definition.name,
            "description": definition.description,
            # Anything is taken: the call is refused for what it is, not for how it was written.
            "parameters": {"type": "object", "properties": {}},
        },
        unavailable,
    )


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

    def sync(self) -> None:
        """Have what the file holds written to the disk."""
        with self._locked(exclusive=True):
            # Opened for writing: where a file is flushed through its handle,
            # one that only reads cannot be.
            with self.path.open("ab") as file:
                os.fsync(file.fileno())


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
        # What to call once an entry is in the file: id -> the call.
        self._saved: dict[str, Callable[[], None]] = {}
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
        later step (syncing it to disk) failed. That step is made up for
        here, before the entry is said to be saved: whoever is told so
        (``link``'s ``saved``) takes it to be on the disk.
        """
        self._storage.drop_torn_tail()
        saved = self._storage.last_entry_id() == entry.id
        if saved:
            self._storage.sync()
        # Cleared only now: had any step failed, the next attempt must look
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

    def link(self, entry: SessionEntry, *, saved: Callable[[], None] | None = None) -> SessionEntry:
        """Chain ``entry`` behind the previous one and queue it for writing.

        ``saved`` is called once the entry is in the file, and not at all
        when it had to be given up on.
        """
        entry.parent_id = self.last_id
        self.last_id = entry.id
        if saved is not None:
            self._saved[entry.id] = saved
        self._queue.put_nowait(entry)
        if self._writer is None or self._writer.done():
            self._writer = asyncio.ensure_future(self._write())
        return entry

    async def _write(self) -> None:
        while True:
            entry = await self._queue.get()
            try:
                await self._append(entry)
                saved = self._saved.pop(entry.id, None)
                if saved is not None and entry.id not in self._lost:
                    try:
                        saved()
                    except Exception:  # noqa: BLE001 - what is told of a write must not stop the writing
                        logger.exception("telling that a transcript entry is saved failed")
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

    def gave_up_on(self, entry_id: str | None) -> bool:
        """Whether an entry was given up on: it could not be written, and is not on the record."""
        return entry_id is not None and entry_id in self._lost

    async def close(self) -> None:
        await self.flush()
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)


class SessionHarness(AgentHarness):
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


class TaskAgentSession(SessionInbox):
    """A running task agent. Create one with ``TaskAgentSession.open``."""

    def __init__(
        self,
        spec: protocol.TaskAgentSpec,
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
        if isinstance(self._model, LLMClientProvider):
            # The provider may turn out to take no pictures; the host is told
            # so it can remember that for the sessions it starts later.
            self._model.on_images_refused = self._images_refused
        self._platform = platform if platform is not None else sys.platform
        self._clock = clock if clock is not None else (lambda: datetime.now().astimezone())
        self._seq = 0
        # Texts taken through prompt, steer and follow_up; see EndedEvent.inputs.
        self._inputs = 0
        # Those of them that are not in the conversation yet, each with its
        # number; see TakenEvent.
        self._given: list[tuple[int, AgentMessage]] = []
        # Those of them no person wrote (``human`` False), until they are in
        # the conversation.
        self._not_human: list[AgentMessage] = []
        self._closing = False
        self._abort_requested = False
        # True while what runs is the summing up asked for by ``compact``.
        self._compacting = False
        self._run_task: asyncio.Task[None] | None = None
        self._abort_timer: asyncio.TimerHandle | None = None
        self._human_in_run = False
        self._goal_round: GoalRound | None = None
        self._last_assistant: AssistantMessage | None = None
        self._context_due = True
        self._context_injected_at = 0.0
        self._output_nudges = 0
        self._empty_nudges = 0
        self._invalid_tool_nudges = 0
        self._turns_since_last_compaction = 0
        self._rapid_compactions = 0
        self._compaction_breaker_tripped = False
        self._compaction_breaker_notified = False
        self._turns_since_todo_write = 0
        self._turns_since_todo_nudge = 0
        self._image_hashes: dict[str, int] = {}
        self._reply_language_hint: str | None = None
        self._repeat = RepeatToolGuard()
        self._system = ""
        # Every tool a call can be carried out by: those there are now, and a
        # stand-in for each the session was opened with that is not.
        self._tools: list[AgentTool] = []
        # Those there are now.
        self._executors: list[AgentTool] = []
        # These gateway tools follow a commission, not its persistent session.
        # They belong to live requests but never to the shared system snapshot.
        self._commission_tools: list[AgentTool] = []
        # Fresh user input only. Consume it at the next summary, even when
        # it asked for the same device as the commission that opened here.
        self._wanted_device_groups: tuple[str, ...] = ()
        # A summary can run again after a provider refuses a request. Keep
        # this input's groups until an ordinary model request gets a reply.
        self._unanswered_device_groups: tuple[str, ...] = ()
        self._browser_click_context = False
        self._device_fold_due = False
        self._device_fold_at = 0
        # What the model is told of, as the snapshot has it.
        self._wire_tools: list[AgentTool] = []
        self._workdir = Path(".")
        self._session_dir = Path(".")
        # How often the conversation has been summed up, and whether what
        # follows a summary (a new snapshot) is still to be done.
        self._compactions = 0
        self._compaction_due = False
        self._tokens_before_compaction = 0
        # The entry of the latest summary made here, to ask the record whether it took it.
        self._compaction_entry_id: str | None = None
        # The entry of the text that opened the commission under way, which a
        # summary leaves as it is; and that text as a message, from the
        # moment it is handed over until it is in the transcript.
        self._opening_entry_id: str | None = None
        self._opening_message: AgentMessage | None = None
        # What the head of its requests was put together for (``SystemSnapshot.head_of``).
        self._head_of = ""
        # Whether the last request of the conversation the session was
        # opened with failed (``_restore``); looked at before its first one.
        self._last_request_failed = False
        # Whether the goal the session has was given with this start of it
        # (``spec.goal``): that one is the goal of what is opened next.
        self._goal_of_this_start = False
        # Whether the goal the session was started with has been made the
        # session's goal, or the commission it was for has been opened.
        self._goal_given = False
        # The window the conversation is managed for.
        self._window = spec.model.context_window
        # The output a request asks for: what the model is configured for,
        # less in a small window (``output_allowance``).
        self._max_output = output_allowance(spec.model.context_window, spec.model.max_output_tokens)
        self._gateway: Any = None
        # Set in _open.
        self._recorder: SessionRecorder
        self.harness: SessionHarness
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
        spec: protocol.TaskAgentSpec,
        host: HostBridge,
        emit: EventSink,
        *,
        model: LLMClientProvider | None = None,
        platform: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> "TaskAgentSession":
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
        # A session made from another by copying what was said in it
        # (``fork.py``) reads the tool output that one kept where it is:
        # what was said names those files.
        readable = [spill.directory]
        for custom in state.custom_entries if state is not None else ():
            if custom.namespace != fork.FORK_NAMESPACE:
                continue
            # Where the session it was made from kept its output, and
            # where the ones that one was made from kept theirs.
            behind = custom.data.get("outputs_behind")
            for kept_there in (custom.data.get("outputs"), *(behind if isinstance(behind, list) else ())):
                if isinstance(kept_there, str) and kept_there:
                    readable.append(Path(kept_there))
        from nekopower.runtime.execution import managed_tool

        builtins = build_tools(tool_context, disabled=spec.disabled_tools)
        executors = [
            managed_tool(tool, self._host, self.session_id, workdir, tuple(readable), self.jobs,
                         spill=spill, cap=lambda *args: self.context.cap_tool_result(*args)) for tool in builtins
        ]
        self._managed_tools = {wrapped.name for original, wrapped in zip(builtins, executors) if wrapped is not original}
        if spec.gateway is not None:
            # Capabilities the host serves (plugin entries, MCP tools) join the
            # built-in tools. The host decides permissions for them itself.
            from nekopower.runtime.tools.gateway import GatewayError, connect_gateway

            try:
                self._gateway, gateway_tools = await connect_gateway(
                    spec.gateway.url,
                    spec.gateway.token,
                    reserved={tool.name for tool in executors},
                    disabled=spec.disabled_tools,
                )
            except GatewayError as exc:
                raise SessionError(f"the capability gateway cannot be reached: {exc}") from exc
            self._commission_tools = [tool for tool in gateway_tools if tool.name in self._gateway.commission_tools]
            self._wanted_device_groups = tuple(sorted(set(self._gateway.device_groups.values())))
            self._unanswered_device_groups = self._wanted_device_groups
            focus = spec.gateway.opening_browser_context
            self._browser_click_context = (
                focus if focus is not None else self._wanted_device_groups == (BUILTIN_GROUP_BROWSER,)
            )
            executors = sorted([*executors, *gateway_tools], key=lambda tool: tool.name)
        self._workdir, self._session_dir = workdir, session_dir
        self._executors = executors
        # Known before the snapshot is looked at, which says how often the
        # conversation had been summed up when it was made.
        self._compactions = len(state.compaction_entries) if state is not None else 0
        # A session that goes on with a conversation begins its requests as
        # it did before, with the tools it was told of then. A tool that
        # came since is told of after the next summary, when the head of
        # the requests changes anyway. One that has nothing said yet is put
        # together from what there is now.
        snapshot = await asyncio.to_thread(read_snapshot, session_dir) if state is not None else None
        if snapshot is None or snapshot.workdir not in ("", str(workdir)):
            # A moved session's system prompt must name its new working directory.
            await self._renew_snapshot(REASON_OPENED if snapshot is None else REASON_MOVED)
        else:
            # An interrupted summary still owes the host a completion event.
            # Its recovery renews the snapshot before the first model request.
            self._compaction_due = snapshot.compactions is not None and snapshot.compactions != self._compactions
            if snapshot.refresh_on_resume and not spec.idle_compaction and not self._compaction_due:
                await self._renew_snapshot(REASON_COMPACTED)
            elif (
                spec.head_of and snapshot.head_of != spec.head_of
                and not spec.idle_compaction and not self._compaction_due
            ):
                # Put together for another preset than the one it is opened
                # as (also one that does not say what it was for): what it
                # is told of its role and the tools it has are this one's
                # from its first request. Told by what is kept with it, so
                # however the session came to be opened as another, and
                # whatever became of the start that made this. (Not for the
                # host's own summary of an idle session, which reads the
                # head the provider kept; nor before a summary that is
                # still owed, after which it is put together anew in any
                # case.)
                await self._renew_snapshot(REASON_PRESET)
            else:
                self._adopt(snapshot)
        # Fixed for as long as the session is open: a run looks its tools up
        # here by name. Every tool there is now, and a stand-in for each the
        # model is still told of that is not there. What the model is told
        # of (``_wire_tools``) changes after a summary, and never names a
        # tool that is not among these.
        there = {tool.name for tool in executors}
        self._tools = [*executors, *(tool for tool in self._wire_tools if tool.name not in there)]

        self.harness = SessionHarness(
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
                    # Reading back a result kept there is not reading
                    # outside the working directory.
                    readable=tuple(readable),
                    # Called, these are answered by what stands in for
                    # them, which does nothing: nobody is asked about them.
                    # Those the session was opened without, and any other
                    # the model is told of that is not there now. (Never
                    # one that is there after all.)
                    withheld=((frozenset(spec.disabled_tools) | {tool.name for tool in self._tools}) - there) | self._managed_tools,
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

        self.context = ContextManager(
            policy=ContextPolicy(
                window=spec.model.context_window,
                max_output=self._max_output,
                # The part of the window it is summed up from: kept when the
                # session comes to be managed for a smaller window.
                threshold_ratio=compact_ratio(spec.model.context_window, spec.model.compact_at),
            ),
            spill=spill,
            summarize=self._summarize,
            new_entry_id=new_entry_id,
            fixed_tokens=await self._fixed_tokens(),
            image_retention=dict(spec.image_retention),
            image_pools=dict(spec.image_pools),
            image_evict_at=dict(spec.image_evict_at),
            on_summary_overflow=self._learn_window,
        )
        if self._gateway is not None:
            self._gateway.compactions = self._compactions
        if spec.goal is not None and self.goals.current is None:
            self.goals.create(spec.goal.objective, spec.goal.max_rounds)
            self._goal_of_this_start = True
            self._goal_given = True
        self._emit(
            protocol.StartedEvent(
                session_id=self.session_id,
                resumed=state is not None,
                tools=[tool.name for tool in self._wire_tools],
            )
        )

    def _adopt(self, snapshot: SystemSnapshot) -> None:
        """Use the system prompt and the tool definitions the session was opened with before.

        A tool is carried out by what there is now and described as it was
        then. One that is not there now gets a stand-in, which says so when
        it is called.
        """
        by_name = {tool.name: tool for tool in self._executors}
        scoped = {tool.name for tool in self._commission_tools}
        wire: list[AgentTool] = []
        for definition in snapshot.tools:
            if definition.name in scoped:
                continue
            tool = by_name.get(definition.name) or _stand_in(definition)
            wire.append(
                dataclasses.replace(tool, description=definition.description, parameters=definition.parameters)
            )
        self._system = snapshot.system
        self._head_of = snapshot.head_of
        self._wire_tools = [*wire, *self._visible_commission_tools()]

    def _visible_commission_tools(self) -> list[AgentTool]:
        return [tool for tool in self._commission_tools if tool.name in self._gateway.commission_tools]

    async def _refresh_device_tools(self, *, compacted: bool = False) -> None:
        gateway = self._gateway
        if not self._commission_tools or not gateway.device_groups:
            return
        # Only a whole summary made while this commission held the session
        # may fold its tools. A disk replay or forced truncation may not.
        fold = (compacted and self._device_fold_at > 0) or self._device_fold_due
        if not (fold or gateway.device_list_dirty):
            return
        from nekopower.runtime.tools.gateway import GatewayError

        try:
            definitions = await gateway.list_tools(
                compactions=self._device_fold_at if fold else None,
                wanted=self._wanted_device_groups or self._unanswered_device_groups,
            )
        except GatewayError:
            # The host still controls every call. Keep the last usable list
            # and retry on the next request instead of losing the session.
            logger.warning("could not refresh device tools for session %s", self.session_id)
            self._device_fold_due = fold
            return
        self._device_fold_due = False
        gateway.device_list_dirty = False
        if fold:
            self._wanted_device_groups = ()
            self._device_fold_at = 0
        by_name = {tool.name: tool for tool in self._commission_tools}
        # Keep all original executors. This list alone changes what the next
        # model request describes and removes the group's guidance with it.
        for definition in definitions:
            name = definition["name"]
            if name in by_name and name in gateway.commission_tools:
                by_name[name] = dataclasses.replace(
                    by_name[name], description=definition["description"], parameters=definition["inputSchema"]
                )
        self._commission_tools = list(by_name.values())
        self._wire_tools = [tool for tool in self._wire_tools if tool.name not in by_name] + self._visible_commission_tools()
        if not compacted:
            self.context.fixed_tokens = await self._fixed_tokens()
            self.context.invalidate()

    async def _renew_snapshot(self, reason: str) -> None:
        """Put the system prompt and the tool definitions together from what there is now, and keep them."""
        spec = self.spec
        scoped = {tool.name for tool in self._commission_tools}
        if spec.idle_compaction and reason == REASON_COMPACTED:
            # Keep the same prompt and tools the summary request used.
            baseline = [tool for tool in self._wire_tools if tool.name not in scoped]
        else:
            baseline = [tool for tool in self._executors if tool.name not in scoped]
            self._system = build_system_prompt(
                tool_names=[tool.name for tool in baseline],
                workdir=str(self._workdir),
                platform=self._platform,
                instructions=spec.instructions,
            )
            self._wire_tools = [*baseline, *self._visible_commission_tools()]
            # What it is put together for from here on. (Kept as it was above:
            # the head is the one the summary request used.)
            self._head_of = spec.head_of
        snapshot = SystemSnapshot(
            self._system,
            definitions_of(baseline),
            reason,
            time.time(),
            workdir=str(self._workdir),
            compactions=self._compactions,
            refresh_on_resume=spec.idle_compaction or spec.workspace_brief_unavailable,
            head_of=self._head_of,
        )
        try:
            await asyncio.to_thread(write_snapshot, self._session_dir, snapshot)
        except Exception:  # noqa: BLE001 - the session works without the file
            # Opened again, it finds the snapshot from before or none, and
            # goes on with that or with one put together then.
            logger.warning("the system snapshot of session %s could not be written", self.session_id, exc_info=True)

    async def _fixed_tokens(self) -> int:
        """Return the size of what every request begins with: the system prompt and the tool definitions."""
        from utils.tokenize import acount_tokens

        fixed_text = self._system + json.dumps(
            [[tool.name, tool.description, dict(tool.parameters)] for tool in self._wire_tools],
            ensure_ascii=False,
        )
        return await acount_tokens(fixed_text)

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
            elif custom.namespace == COMMISSION_NAMESPACE:
                # The last one counts: the commission the session was on.
                entry_id = custom.data.get("entry_id")
                self._opening_entry_id = entry_id if isinstance(entry_id, str) and entry_id else None
            elif custom.namespace == fork.FORK_NAMESPACE:
                # Everything before this was said in another session, and
                # copied: its to-do list and its goal were that one's. (The
                # entries after it are this session's own.)
                self.todos.restore(None)
                self.goals.restore(None)
        self._compactions = len(state.compaction_entries)
        self._recorder.last_id = state.active_leaf_id
        replies = [message for message in self.harness.message_list if isinstance(message, AssistantMessage)]
        self._last_request_failed = bool(replies) and replies[-1].stop_reason == "error"

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

    def _images_refused(self, said: str = "", about_model: bool = True) -> None:
        """The provider takes no pictures (at all, or not this session's); the session goes on without them."""
        self._emit(
            protocol.ModelCapabilityEvent(
                session_id=self.session_id, supports_images=False, reason=str(said)[:300], lasting=bool(about_model)
            )
        )

    def _emit_report(self, payload: dict[str, Any]) -> None:
        self._emit(protocol.ReportEvent(session_id=self.session_id, **payload))

    def _save_todos(self) -> None:
        self._recorder.link(CustomEntry(namespace=TODO_NAMESPACE, data={"items": self.todos.items}))

    def _save_goal(self) -> None:
        self._recorder.link(CustomEntry(namespace=GOAL_NAMESPACE, data=self.goals.to_json()))

    def _record(self, message: AgentMessage, *, saved: Callable[[], None] | None = None) -> None:
        """Append one message to the transcript and register it; ``saved`` is called once it is in the file."""
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
        self._recorder.link(entry, saved=saved)
        self.transcript.register(message, entry.id, kind)
        if message is self._opening_message:
            # Noted in the transcript, so that a session opened later knows
            # which text a summary is to leave alone.
            self._opening_message = None
            self._opening_entry_id = entry.id
            self._recorder.link(CustomEntry(namespace=COMMISSION_NAMESPACE, data={"entry_id": entry.id}))

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
        if isinstance(message, CustomMessage) and message.custom_type == "notice:pending_job":
            # Resolve the wake after budget and context checks. Only the
            # approved view belongs in the transcript or in a fork.
            for notice in self.jobs.pending_notices.values():
                if notice.handle is message:
                    notice.admitted = True
                    break
            self.harness.message_list[:] = [item for item in self.harness.message_list if item is not message]
            return
        taken: Callable[[], None] | None = None
        opening = message is self._opening_message
        for index, (number, given) in enumerate(self._given):
            if given is message:
                # One of the texts the session was given is in its
                # conversation from here on. That is told once it is in the
                # record too, where a session opened again finds it: told
                # before, a process that ends meanwhile would leave a text
                # that is nowhere and that nobody sends again.
                del self._given[index]
                taken = functools.partial(
                    self._emit, protocol.TakenEvent(session_id=self.session_id, inputs=number)
                )
                break
        if not self.transcript.is_registered(message):
            self._record(message, saved=taken)
        elif taken is not None:
            taken()
        if isinstance(message, UserMessage):
            written_by_nobody = any(message is other for other in self._not_human)
            if written_by_nobody:
                # What another session wrote, or what the host says of a
                # time that has come: it is in the conversation like any
                # text, and is no word of the user's. The run is not one
                # the user spoke in on its account.
                self._not_human = [other for other in self._not_human if other is not message]
            else:
                # Text from the user: it authorizes goal management for this run
                # and resets the automatic-compaction circuit breaker.
                self._human_in_run = True
                self._rapid_compactions = 0
                self._compaction_breaker_tripped = False
                self._compaction_breaker_notified = False
                if opening:
                    lang_match = _REPLY_LANGUAGE_RE.search(message.text)
                    if lang_match is not None:
                        self._reply_language_hint = lang_match.group(1).strip()
                gateway = self._gateway
                if taken is not None and not opening and self._commission_tools and gateway.device_groups:
                    from nekopower.device_intent import classify_device_intent

                    # All steering messages drained together share the next
                    # reply. An earlier, answered batch contributes nothing.
                    selected, self._browser_click_context = classify_device_intent(
                        message.text, browser_context=self._browser_click_context
                    )
                    requested = set(selected).intersection(gateway.device_groups.values())
                    self._unanswered_device_groups = tuple(sorted(requested.union(self._unanswered_device_groups)))
                    self._wanted_device_groups = self._unanswered_device_groups
                    hidden = gateway.device_groups.keys() - gateway.commission_tools
                    if any(gateway.device_groups[name] in self._wanted_device_groups for name in hidden):
                        gateway.device_list_dirty = True
            # Either makes an earlier run of identical tool calls irrelevant.
            self._repeat.reset()
        elif isinstance(message, AssistantMessage):
            self._last_assistant = message
            self._turns_since_last_compaction += 1
            if self._gateway is not None:
                # The tool calls that follow were written in this reply.
                self._gateway.next_reply()
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
                        context_window=self._window,
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
        # live list instead. Likewise the system prompt and the tools: what
        # the model is told of is what the session holds (``_wire_tools``),
        # which is put together anew after a summary.
        del messages, session_id, system, tools
        return self._model_request(model, signal)

    async def _model_request(
        self,
        model: str,
        signal: CancellationToken | None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        from config.nekopower_settings import TASK_AGENT_UNSURE_REFUSAL_TOKENS

        if signal is None or not signal.is_cancelled():
            if await self._budget_spent(signal):
                # The host has ended what this session works on, for what
                # it used: no further request is made.
                stopped = AssistantMessage(
                    model=model, stop_reason="aborted", error_message="stopped: the usage allowed for this work is spent"
                )
                yield AssistantErrorEvent(reason="aborted", error=stopped)
                return
            if signal is not None and signal.is_cancelled():
                # Stopped while the host was waited for (the user was
                # asked). It ends here: nothing more is said to the
                # session, its conversation is not summed up (a request
                # as long as all of it, after the stop), and what is due
                # after an earlier summary is left to the next run.
                stopped = AssistantMessage(model=model, stop_reason="aborted", error_message="stopped")
                yield AssistantErrorEvent(reason="aborted", error=stopped)
                return
            await self._refresh_device_tools()
            await self._spill_oversized_user_messages()
            await self._cap_parallel_tool_batch()
            self._inject_runtime_context()
            failed_before, self._last_request_failed = self._last_request_failed, False
            long = failed_before and await self._size() > TASK_AGENT_UNSURE_REFUSAL_TOKENS
            if long:
                # The session was opened again with a conversation whose
                # last request failed, and it is long. Sent as it is, the
                # request would fail the same way if that was about its
                # length, and so would every commission after this one.
                logger.warning("session %s: its last request failed and it is long; summed up first", self.session_id)
                # Fitted to a window of its own length, so that the request
                # that sums it up is shorter than what failed; and managed
                # for the window it had from then on. Why the last request
                # failed is not known (it may have been a timeout): nothing
                # is concluded of the model's window from it.
                window, max_output, policy = self._window, self._max_output, self.context.policy
                self._assume_window(await self._size())
                try:
                    await self._fit(force=True)
                finally:
                    self._window, self._max_output, self.context.policy = window, max_output, policy
            elif self._compaction_breaker_tripped:
                # Auto-compaction tripped its rapid-refill circuit breaker: keep
                # running lightweight image retention and tool pruning without
                # burning extra summary requests, and emit a diagnostic notice once.
                await self._fit(force=False)
                if not self._compaction_breaker_notified:
                    self._compaction_breaker_notified = True
                    diagnostic = CustomMessage(
                        custom_type="notice:compaction_breaker",
                        content=texts.AUTO_COMPACT_CIRCUIT_BREAKER_NOTICE.replace(
                            "<<TOP_ITEMS>>", self._top_context_consumers()
                        ),
                        display=False,
                    )
                    self.harness.message_list.append(diagnostic)
                    self._record(diagnostic)
            else:
                await self._fit(force=False)
        retried = False
        clamped_output = False
        request_max_output: int | None = None
        # What the session was managed for before a window was assumed for
        # a retry (after a refusal that did not say it was about length),
        # to go back to when the retry does not go through.
        before: tuple[int, int, ContextPolicy] | None = None

        def back() -> None:
            nonlocal before
            if before is not None:
                self._window, self._max_output, self.context.policy = before
            before = None

        try:
            while True:
                for _again in range(2):
                    if not self._compaction_due:
                        break
                    await self._after_compaction()
                    # What every request begins with was put together anew, and
                    # may be longer than what the conversation was just fitted
                    # next to (more to tell, more tools): fitted again, which
                    # sums up further only if it no longer fits.
                    await self._fit(force=False)
                if (
                    self._context_due
                    and self.harness.message_list
                    and isinstance(self.harness.message_list[-1], ToolResultMessage)
                ):
                    # After mid-run compaction on tool turns, immediately refresh
                    # runtime context (todos, active goal, reply language, time)
                    # without displacing a trailing user prompt.
                    self._inject_runtime_context()
                # How much is about to be sent, as the conversation is measured.
                sent = await self._size()
                if (signal is None or not signal.is_cancelled()) and self.jobs.pending_notices:
                    reserve = await self._job_notice_room(self.context.policy.threshold - sent)
                    if reserve:
                        await self._fit(force=False, reserve_tokens=reserve)
                        if self._compaction_due:
                            continue
                        sent = await self._size()
                    if signal is None or not signal.is_cancelled():
                        sent += await self._deliver_job_notices()
                history = _provider_context(self.harness.message_list)
                # Held back until output follows, so a request that is retried after
                # an overflow does not announce two responses.
                held: AssistantStartEvent | None = None
                answered = False
                try:
                    # Said only when it is less than the model is configured
                    # for, which is what a request asks for unless told otherwise.
                    effective_max_output = (
                        request_max_output if request_max_output is not None else self._max_output
                    )
                    less = request_max_output is not None or effective_max_output < self.spec.model.max_output_tokens
                    async for event in self._model.stream_response(
                        model=model,
                        system=self._system,
                        messages=history,
                        tools=self._wire_tools,
                        signal=signal,
                        **({"max_tokens": effective_max_output} if less else {}),
                    ):
                        if isinstance(event, AssistantStartEvent):
                            held = event
                            continue
                        if held is not None:
                            yield held
                            held = None
                        if isinstance(event, AssistantDoneEvent):
                            self._unanswered_device_groups = ()
                            answered = True
                        yield event
                    if answered:
                        # Shorter, it went through. That does not show it
                        # was too long: summing up rewrites what was said,
                        # and a refusal for something in it (a turn the
                        # provider does not take, words it screens out)
                        # goes away with that as well. So the session keeps
                        # the smaller window for as long as it is open,
                        # which is all it needs to go on, and the host is
                        # told nothing: it would manage every session with
                        # the model for that window for a month, and tell
                        # the user that the model's window is smaller than
                        # was set.
                        before = None
                    # (Not answered: the request ended in an error of
                    # another kind, or was stopped. The stream ends without
                    # raising then.)
                    return
                except RequestRefusedError as exc:
                    if retried or sent <= TASK_AGENT_UNSURE_REFUSAL_TOKENS:
                        # Not long enough to be about length, or refused again
                        # after it was made shorter: reported as it came.
                        logger.warning("model request failed: %s", exc)
                        failure = AssistantMessage(model=model, stop_reason="error", error_message=str(exc))
                        yield AssistantErrorEvent(reason="error", error=failure)
                        return
                    retried = True
                    logger.warning(
                        "a request of about %d tokens was refused in words that were not recognized; "
                        "taken for too long, summed up and sent once more: %s",
                        sent,
                        exc,
                    )
                    managed = (self._window, self._max_output, self.context.policy)
                    if self._assume_window(sent):
                        before = managed
                    await self._fit(force=True)
                except ContextOverflowError as exc:
                    # Before compacting or shrinking the session window, check
                    # whether the provider only rejected input + max_tokens > limit.
                    combined = parse_combined_overflow(str(exc)) if not clamped_output and not retried else None
                    if combined is not None:
                        input_tokens, limit = combined
                        available = limit - input_tokens - 1_000
                        if available >= 3_000:
                            clamped_output = True
                            request_max_output = min(self._max_output, available)
                            logger.info(
                                "session %s: combined input+output overflow (%d / %d); retrying once with max_tokens=%d",
                                self.session_id,
                                input_tokens,
                                limit,
                                request_max_output,
                            )
                            continue
                    # What the provider says of its window now stands in the
                    # place of one that was only assumed.
                    back()
                    request_max_output = None
                    self._learn_window(str(exc), sent)
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
        finally:
            # However this ended without an answer to the request that was
            # sent once more (refused again, an error of another kind, a
            # stop, also one while the conversation was summed up for it):
            # the window that was assumed was not shown to be anything, and
            # the session is managed for the one it had.
            back()

    async def _budget_spent(self, signal: CancellationToken | None) -> bool:
        """Ask the host, before a model request, whether what this session works on may use more; True to stop.

        A host that keeps count of what its sessions use (``budget`` among
        its callbacks) is asked before every request of a run: between two
        steps, when no tool call of the run is under way. It answers at
        once while there is room. When there is none it asks the user,
        which may take as long as the user does, and the request waits.
        Its answer may carry a ``notice``: said to the model in front of
        the request (the user allowed no more, and what there is has to be
        handed over). ``stop`` ends the run without the request.

        A host that cannot answer does not hold the work up.
        """
        if "budget" not in self._host.capabilities:
            return False
        asking = asyncio.ensure_future(self._host.call(self.session_id, "budget", {}))
        try:
            while True:
                done, _pending = await asyncio.wait({asking}, timeout=_INPUT_POLL_SECONDS)
                if done:
                    break
                if signal is not None and signal.is_cancelled():
                    # Stopped while it waited: the request that follows says so itself.
                    return False
        finally:
            if not asking.done():
                asking.cancel()
                await asyncio.gather(asking, return_exceptions=True)
        try:
            verdict = asking.result()
        except asyncio.CancelledError:
            # The question was withdrawn: what it was asked for is over.
            return True
        except Exception:  # noqa: BLE001 - counting must not stop the work
            logger.warning("session %s: the host could not say whether more may be used", self.session_id, exc_info=True)
            return False
        if signal is not None and signal.is_cancelled():
            # Stopped by the time the answer is looked at: the run is over,
            # and nothing more is said to its session. The request that
            # follows says so itself.
            return False
        notice = verdict.get("notice")
        if isinstance(notice, str) and notice.strip():
            message = CustomMessage(custom_type="notice:usage", content=notice, display=False)
            self.harness.message_list.append(message)
            self._record(message)
        return bool(verdict.get("stop"))

    async def _spill_oversized_user_messages(self) -> None:
        """Spill any oversized human text message to disk with a head/tail preview and anti-guess notice."""
        rows = self.transcript.rows()
        # Only spill inputs that exceed the single-part wire limit or the
        # context compaction threshold itself; smaller long opening prompts
        # stay intact for normal pinning / compaction semantics.
        cap = min(WIRE_TEXT_PART_MAX_TOKENS, max(self.context.policy.threshold, 16_000))
        candidates = [
            index
            for index, row in enumerate(rows)
            if isinstance(row.message, UserMessage)
            and isinstance(row.message.content, str)
            and len(row.message.content.encode("utf-8", errors="replace")) > cap
        ]
        if not candidates:
            return

        def _spill_batch() -> list[Row] | None:
            from utils.tokenize import count_tokens, truncate_head_tail_tokens

            out = list(rows)
            changed = False
            for index in candidates:
                row = out[index]
                message = row.message
                assert isinstance(message, UserMessage) and isinstance(message.content, str)
                raw = message.content
                if count_tokens(raw) <= cap:
                    continue
                path = self.context.spill.save(f"user-{row.entry_id}", raw)
                preview_half = min(2_000, max(256, (cap - 128) // 2))
                kept = truncate_head_tail_tokens(raw, preview_half, preview_half, separator=texts.SPILL_GAP)
                omitted = max(
                    0,
                    len(raw.encode("utf-8", errors="replace"))
                    - len(kept.replace(texts.SPILL_GAP, "", 1).encode("utf-8", errors="replace")),
                )
                if path is not None:
                    notice = texts.user_input_spill_notice(omitted, str(path))
                else:
                    notice = (
                        f"(Omitted {omitted} bytes from an oversized user message; "
                        "do NOT guess or fabricate the omitted middle.)"
                    )
                out[index] = Row(
                    row.entry_id,
                    row.kind,
                    message.model_copy(update={"content": f"{kept}\n\n{notice}"}),
                )
                changed = True
            return out if changed else None

        rewritten = await asyncio.to_thread(_spill_batch)
        if rewritten is not None:
            self.transcript.rewrite(rewritten)
            self.context.invalidate()

    async def _cap_parallel_tool_batch(self) -> None:
        """Enforce a combined token budget on a parallel batch of fresh tool results."""
        from config.nekopower_settings import TASK_AGENT_BATCH_TOOL_RESULT_MAX_TOKENS

        rows = self.transcript.rows()
        budget = min(TASK_AGENT_BATCH_TOOL_RESULT_MAX_TOKENS, max(4_096, self._window // 3))
        rewritten = await asyncio.to_thread(
            cap_parallel_batch_results,
            rows,
            budget=budget,
            spill=self.context.spill,
        )
        if rewritten is not None:
            self.transcript.rewrite(rewritten)
            self.context.invalidate()

    def _top_context_consumers(self, limit: int = 3) -> str:
        """Return a short bullet list of the largest messages currently in context."""
        scored: list[tuple[int, str]] = []
        for row in self.transcript.rows():
            tokens = estimate_tokens(row.message, self.context.policy.image_tokens)
            if isinstance(row.message, ToolResultMessage):
                label = f"tool `{row.message.tool_name}` ({row.message.tool_call_id})"
            elif isinstance(row.message, AssistantMessage):
                label = "assistant turn"
            elif isinstance(row.message, UserMessage):
                label = "summary checkpoint" if row.kind == "summary" else "user message"
            else:
                label = row.kind
            scored.append((tokens, f"- {label}: ~{tokens} tokens"))
        scored.sort(key=lambda item: item[0], reverse=True)
        return "\n".join(line for _, line in scored[:limit]) or "- (no items)"

    async def _fit(
        self,
        *,
        force: bool,
        tight: bool | None = None,
        or_drop: bool = True,
        reserve_tokens: int = 0,
    ) -> None:
        # Each summary is saved the moment it is applied, not after fit()
        # returns: an abort during a later summary ends fit() with the earlier
        # one already in the conversation, and later entries refer to its id.
        await self.context.fit(
            self.transcript,
            force=force,
            tight=tight,
            on_compaction=self._persist_compaction,
            # The commission under way stays as it was handed over.
            pinned=self._pinned(),
            or_drop=or_drop,
            reserve_tokens=reserve_tokens,
            summarize=force or not self._compaction_breaker_tripped,
        )

    def _pinned(self) -> tuple[str, ...]:
        """Return the rows a summary leaves word for word: the text that opened the commission under way, and what the host said about usage.

        What the host said about usage (that no more may be used, and
        that what there is has to be handed over) is said for the run
        it was said in, and taken out when that is over
        (``_drop_usage_notices``). Summed up on the way, it would be
        part of the summary, which is told to keep such limits, and
        every later run of the session would be told to stop.

        The text that opened the commission:
        Not when it is longer than half of what a conversation may fill of
        the window: kept whole next to what every request begins with, it
        would leave a summary nothing to shorten, and the request would be
        refused as too long however often it was summed up. (What a task
        agent hands to one it starts has no cap, and a window may turn out
        small.) It is then cut down and summed up like what the user said
        before it.
        """
        rows = self.transcript.rows()
        pinned = [
            row.entry_id
            for row in rows
            if isinstance(row.message, CustomMessage) and row.message.custom_type == "notice:usage"
        ]
        opening = self._opening_entry_id
        row = next((row for row in rows if row.entry_id == opening), None) if opening else None
        if row is not None:
            policy = self.context.policy
            room = (policy.threshold - self.context.fixed_tokens) // 2
            if estimate_tokens(row.message, policy.image_tokens) <= room:
                pinned.append(row.entry_id)
        return tuple(pinned)

    def _persist_compaction(self, compaction: Compaction) -> None:
        from config.nekopower_settings import (
            TASK_AGENT_AUTO_COMPACT_BREAKER_COUNT,
            TASK_AGENT_AUTO_COMPACT_BREAKER_TURNS,
        )

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
        self._compactions += 1
        if not self._compacting:
            if self._compactions > 1 and self._turns_since_last_compaction < TASK_AGENT_AUTO_COMPACT_BREAKER_TURNS:
                self._rapid_compactions += 1
            else:
                self._rapid_compactions = 1
            self._turns_since_last_compaction = 0
            if self._rapid_compactions >= TASK_AGENT_AUTO_COMPACT_BREAKER_COUNT:
                self._compaction_breaker_tripped = True
                logger.warning(
                    "session %s: auto-compaction circuit breaker tripped after %d rapid compactions",
                    self.session_id,
                    self._rapid_compactions,
                )
        if compaction.complete:
            self._device_fold_at = self._compactions
        self._compaction_entry_id = compaction.summary_row.entry_id
        self._tokens_before_compaction = compaction.tokens_before
        # (What the request that wrote it used was told when it came back:
        # ``_summarize``.)
        # What follows a summary is done before the next request, not here:
        # this is called while the conversation is still being fitted.
        self._compaction_due = True
        if self._gateway is not None:
            # From the next call on the gateway knows that what it handed
            # over before (a group of tools and how they are used) may be gone.
            self._gateway.compactions = self._compactions

    async def _after_compaction(self, *, tell: bool = True) -> None:
        """Do what follows a summary of the conversation, before the next request.

        The head of the request changes with a summary anyway, so this is
        when the system prompt and the tool definitions are put together
        anew: what the host's instructions hold by now, and the tools there
        are now, take effect.

        ``tell`` is whether the host is told that the conversation was
        summed up. Not when this is only made up for (the summary was
        made before the session was last closed) by a summing up that
        made none itself: the host takes the word for that one.
        """
        await self._refresh_device_tools(compacted=True)
        await self._renew_snapshot(REASON_COMPACTED)
        self.context.fixed_tokens = await self._fixed_tokens()
        self.context.invalidate()
        # Re-arm runtime context so todos, running jobs, active goal, and reply
        # language are re-injected on the next turn after compaction.
        self._context_due = True
        self._image_hashes.clear()
        self._turns_since_todo_nudge = 0
        # Only now: stopped while the snapshot was written, the next run
        # does all of this again, and measures against what it sends.
        self._compaction_due = False
        if not tell:
            return
        self._emit(
            protocol.CompactedEvent(
                session_id=self.session_id,
                count=self._compactions,
                tokens_before=self._tokens_before_compaction,
            )
        )

    async def _size(self) -> int:
        """Return how long the conversation is, with what every request begins with."""
        return await self.context.measure(self.transcript.messages)

    def _learn_window(self, said: str, sent: int) -> None:
        """Manage the conversation for the window a refusal of a request as too long tells of.

        The window the provider stated, if it stated one. If it stated
        none, the length of the request it refused (``sent``): the window
        is smaller than that, whatever it is, and without a number the
        conversation would go on being summed up for the window the session
        was started with, in requests as long as the one just refused.

        Only for a window smaller than the session's: the request is then
        fitted to it and sent again, the host is told, and sessions it
        starts for this model later are given that window from the start.
        """
        from config.nekopower_settings import TASK_AGENT_INFERRED_WINDOW_MIN_TOKENS

        stated = window_from_overflow(said)
        if stated is None:
            if sent < TASK_AGENT_INFERRED_WINDOW_MIN_TOKENS:
                return
            stated = sent
        if self._assume_window(stated):
            self._tell_window(self._window, said)

    def _assume_window(self, tokens: int) -> bool:
        """Manage the conversation for a window of ``tokens``, if that is smaller than the one it is managed for."""
        window = max(int(tokens), MIN_CONTEXT_WINDOW)
        if window >= self._window:
            return False
        logger.info("session %s: managed for a window of %d tokens now", self.session_id, window)
        self._window = window
        # The output asked for shrinks with it: the provider counts it
        # against the window too, and what the conversation is fitted to
        # (``ContextPolicy``) is the window less the output.
        self._max_output = min(self._max_output, output_allowance(window, self.spec.model.max_output_tokens))
        self.context.policy = dataclasses.replace(self.context.policy, window=window, max_output=self._max_output)
        return True

    def _tell_window(self, window: int, said: str) -> None:
        """Tell the host the window this session came to be managed for, and what the provider said."""
        self._emit(
            protocol.ModelCapabilityEvent(
                session_id=self.session_id, context_window=window, reason=" ".join(said.split())[:300]
            )
        )

    async def _summarize(
        self,
        messages: list[AgentMessage],
        max_tokens: int,
        *,
        no_tools: bool = False,
        retry_note: str | None = None,
    ) -> tuple[str, Usage | None]:
        instruction = (
            f"{texts.COMPACTION_INSTRUCTION}\n\n{retry_note}"
            if retry_note
            else texts.COMPACTION_INSTRUCTION
        )
        request = [*messages, UserMessage(content=instruction)]
        repaired = list(repair_tool_history(tuple(request)).messages)
        signal = self.harness.current_signal
        try:
            reply = await self._model.complete(
                system=self._system,
                messages=repaired,
                # Keep the same tool list on the first attempt so provider prompt
                # caches match; drop tools on the Tier-2 no-tools retry.
                tools=[] if no_tools else self._wire_tools,
                max_tokens=max_tokens,
                signal=signal,
                skip_cache_write=True,
                strip_images=True,
            )
        except (ModelRequestError, RequestRefusedError, ContextOverflowError):
            if signal is not None and signal.is_cancelled():
                # An abort must not be mistaken for a failed summary, which would
                # drop the older part of the conversation. (A request that is
                # refused outright, or as too long, raises from the stream
                # itself: looked at here like any other failure.)
                raise asyncio.CancelledError from None
            raise
        self._tell_summary_usage(reply.usage)
        if reply.stop_reason == "toolUse":
            # It called a tool instead of writing the summary (the request
            # offers the tools, as every request of the session does).
            # What it wrote before that leads up to the call and sums
            # nothing up: a summary that failed, not one that was cut
            # short.
            raise SummaryUsedToolError("the model called a tool instead of writing the summary")
        if reply.stop_reason == "length":
            # Ended at its limit: the last sections are missing, or cut off.
            raise SummaryCutShort(reply.text)
        return reply.text, reply.usage

    def _tell_summary_usage(self, usage: Usage | None) -> None:
        """Tell what a request that sums the conversation up used, whether or not its summary is taken."""
        if usage is not None and (usage.input + usage.cache_read + usage.cache_write or usage.output):
            # The request that wrote the summary is one the session made:
            # counted with what it used, apart from those of its runs.
            self._emit(
                protocol.UsageEvent(
                    session_id=self.session_id,
                    input=usage.input,
                    output=usage.output,
                    cache_read=usage.cache_read,
                    cache_write=usage.cache_write,
                    reasoning=usage.reasoning,
                    total_tokens=usage.total_tokens,
                    context_window=self._window,
                    summary=True,
                )
            )

    def _inject_runtime_context(self) -> None:
        """Append the time, active goal, reply language, running jobs and open todos as a message when due."""
        loop_time = asyncio.get_running_loop().time()
        if not self._context_due and (
            loop_time - self._context_injected_at < RUNTIME_CONTEXT_INTERVAL_SECONDS
        ):
            return
        now = self._clock()
        goal = self.goals.current
        active_goal = (
            f"{goal.objective} (round {goal.rounds_started}/{goal.max_goal_rounds})"
            if goal is not None and goal.phase == "active"
            else None
        )
        text = texts.runtime_context_notice(
            f"{now.strftime('%Y-%m-%d %H:%M %z')} ({now.strftime('%A')})",
            [f"{job.id} ({job.kind}: {bounded_label(job.label)})" for job in self.jobs.running() if not job.origin],
            [(item["status"], item["content"]) for item in self.todos.open_items()],
            active_goal=active_goal,
            reply_language=self._reply_language_hint,
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
        # Managed results, including tool errors, were capped under their source receipt.
        if call.name not in self._managed_tools:
            result = await self.context.cap_tool_result(call.name, call.id, result)
        if call.name == "todo_write" and not is_error:
            self._turns_since_todo_write = 0
            self._turns_since_todo_nudge = 0
        for block in result.content:
            if isinstance(block, ImageContent) and block.data:
                digest = hashlib.sha1(
                    block.data.encode("ascii", errors="replace"), usedforsecurity=False
                ).hexdigest()
                count = self._image_hashes.get(digest, 0) + 1
                self._image_hashes[digest] = count
                if count >= 3 and count % 3 == 0:
                    self.deliver_notice("repeated_image", texts.REPEATED_IMAGE_REMINDER)
                    break
        reminder = self._repeat.observe(call.name, call.arguments)
        if reminder is not None:
            self.deliver_notice("repeat_tool", reminder)
        return result, is_error

    def _finish_turn(
        self, turn: AgentTurnContext, signal: CancellationToken | None
    ) -> AgentTurnDecision | None:
        from config.nekopower_settings import TASK_AGENT_TODO_NUDGE_TURNS

        message = turn.message
        cancelled = signal is not None and signal.is_cancelled()
        self._turns_since_todo_write += 1
        self._turns_since_todo_nudge += 1

        if message.stop_reason == "length" and not message.tool_calls and not cancelled:
            self._empty_nudges = 0
            self._invalid_tool_nudges = 0
            if self._output_nudges < MAX_OUTPUT_CUT_OFF_NUDGES:
                self._output_nudges += 1
                dropped_tools = getattr(self._model, "last_dropped_truncated_tools", ())
                if dropped_tools:
                    nudge = texts.OUTPUT_CUT_OFF_TOOL_NUDGE.replace("<<TOOLS>>", ", ".join(dropped_tools))
                elif not message.text.strip() and message.thinking_text.strip():
                    nudge = texts.OUTPUT_CUT_OFF_THINKING_NUDGE
                elif not self.spec.top_level:
                    nudge = texts.OUTPUT_CUT_OFF_SUBAGENT_NUDGE
                else:
                    nudge = texts.OUTPUT_CUT_OFF_NUDGE
                self.harness.steer_message(
                    CustomMessage(
                        custom_type="notice:output_cut_off",
                        content=nudge,
                        display=False,
                    )
                )
            else:
                # Exhausted recovery nudges: fail explicitly instead of silently
                # reporting a truncated turn as completed.
                message.stop_reason = "error"
                message.error_message = (
                    "the response hit the output token limit repeatedly and could not be completed"
                )
            return None

        self._output_nudges = 0
        if message.stop_reason == "stop" and not message.tool_calls and not cancelled:
            if getattr(self._model, "last_dropped_unnamed_tool_calls", False):
                if self._invalid_tool_nudges < MAX_INVALID_TOOL_NUDGES:
                    self._invalid_tool_nudges += 1
                    self.harness.steer_message(
                        CustomMessage(
                            custom_type="notice:invalid_tool_turn",
                            content=texts.INVALID_TOOL_TURN_NUDGE,
                            display=False,
                        )
                    )
                    return None
            elif not message.text.strip():
                after_tool = len(turn.messages) >= 2 and isinstance(turn.messages[-2], ToolResultMessage)
                suspicious_empty = (
                    bool(message.thinking_text.strip())
                    or message.usage.output > 0
                    or after_tool
                )
                if suspicious_empty and self._empty_nudges < MAX_EMPTY_COMPLETION_NUDGES:
                    self._empty_nudges += 1
                    self.harness.steer_message(
                        CustomMessage(
                            custom_type="notice:empty_completion",
                            content=texts.EMPTY_COMPLETION_NUDGE,
                            display=False,
                        )
                    )
                    return None
        else:
            self._empty_nudges = 0
            self._invalid_tool_nudges = 0

        if (
            not cancelled
            and message.tool_calls
            and self.todos.open_items()
            and self._turns_since_todo_write >= TASK_AGENT_TODO_NUDGE_TURNS
            and self._turns_since_todo_nudge >= TASK_AGENT_TODO_NUDGE_TURNS
        ):
            self._turns_since_todo_nudge = 0
            self.harness.steer_message(
                CustomMessage(
                    custom_type="notice:todo_reminder",
                    content=texts.TODO_IDLE_NUDGE,
                    display=False,
                )
            )
        return None

    # ------------------------------------------------------------------
    # SessionInbox (what tools may ask the session)
    # ------------------------------------------------------------------

    def has_pending_input(self) -> bool:
        return self.harness.has_queued_messages()

    def has_pending_user_steer(self) -> bool:
        # Not what another session wrote, or the host says of a time that has
        # come: that waits its turn in the queue, and ends no wait as the
        # user's own words do.
        return any(
            isinstance(message, UserMessage) and not any(message is other for other in self._not_human)
            for message in self.harness.queued_messages.steering
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

    def notice_queued(self, handle: object) -> bool:
        queue = self.harness.queued_messages
        return any(message is handle for message in (*queue.steering, *queue.follow_up))

    def withdraw_notice(self, handle: object) -> bool:
        return isinstance(handle, CustomMessage) and self.harness.remove_queued(handle)

    async def _job_notice_room(self, available: int) -> int:
        """Reserve space for notices without putting private text in a summary."""
        pending = [(job_id, notice) for job_id, notice in self.jobs.pending_notices.items()
                   if notice.admitted and isinstance(notice.handle, CustomMessage)]
        # With room for even one token per inline byte, avoid reading output twice.
        if sum(NOTICE_INLINE_BYTES + estimate_tokens(notice.handle) for _, notice in pending) <= available:
            return 0
        sizes = []
        for job_id, notice in pending:
            job = self.jobs.get(job_id)
            text, _, _ = preview_completion_notice(self.jobs, job)
            preview = CustomMessage(custom_type="notice:job", content=text, display=False)
            sizes.append((job, estimate_tokens(preview), estimate_tokens(notice.handle)))
        if sum(full for _, full, _ in sizes) <= available:
            return 0
        # Only near the context limit, avoid compressing history for a revoked notice.
        total = 0
        for job, full, minimal in sizes:
            try:
                ruling = await self._host.call(self.session_id, "execution_action", {
                    "phase": "job_notice_live", "receipt": job.origin,
                })
            except Exception:
                ruling = {}
            total += full if ruling.get("allow") is True else minimal
        return total if total > available else 0

    async def _deliver_job_notices(self) -> int:
        # The queue carries only the wake. Record the final approved view
        # once, at the same boundary as its delivery to the model.
        added_tokens = 0
        async with AsyncExitStack() as locks:
            pending = []
            for job_id, notice in list(self.jobs.pending_notices.items()):
                if not notice.admitted or not isinstance(notice.handle, CustomMessage):
                    continue
                job = self.jobs.get(job_id)
                if not job.origin:
                    continue
                await locks.enter_async_context(job.output_lock)
                if self.jobs.pending_notices.get(job_id) is notice:
                    pending.append((job, notice))
            if not pending or self._abort_requested or self._closing:
                return 0
            origins = [job.origin for job, _ in pending]
            one = len(origins) == 1
            payload = {"receipt": origins[0]} if one else {"receipts": origins}
            try:
                ruling = await self._host.call(self.session_id, "execution_action", {"phase": "job_notice", **payload})
            except Exception:
                ruling = {}
            if self._abort_requested or self._closing:
                return 0
            if one:
                allowed = set(origins) if ruling.get("allow") is True else set()
            else:
                approved = ruling.get("origins")
                allowed = {r for r in approved if isinstance(r, str)} if isinstance(approved, list) else set()
            for job, notice in pending:
                self.jobs.pending_notices.pop(job.id, None)
                saved = None
                if job.origin in allowed:
                    notice.handle.content, inline, cursors = preview_completion_notice(self.jobs, job)
                    if inline is not None:
                        saved = functools.partial(self.jobs.commit, job, cursors)
                        job.output_write = self._recorder.flush
                notice.handle.custom_type = "notice:job"
                self.harness.message_list.append(notice.handle)
                self._record(notice.handle, saved=saved)
                added_tokens += estimate_tokens(notice.handle)
        return added_tokens

    async def _job_finished(self, job: Job) -> None:
        if job.command_finished is not None:
            await job.command_finished.wait()
        async with job.output_lock:
            if job.origin:
                try:
                    decision = await asyncio.wait_for(self._host.call(self.session_id, "execution_action", {
                        "phase": "job_notice_live", "receipt": job.origin,
                    }), CLOSE_GRACE_SECONDS)
                except Exception:
                    return
                if decision.get("allow") is not True:
                    return
            if self._closing or job.notice_suppressed or job.waiters or job.id in self.jobs.pending_notices:
                return
            if job.origin:
                text, held = f"Background job {job.id} finished.", None
            else:
                text, held = completion_notice(self.jobs, job)
            handle = self.deliver_notice("pending_job" if job.origin else "job", text)
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
        return self.running or bool(self.jobs.running()) or bool(self.jobs.waking())

    def prompt(self, text: str, *, opening: bool = False, human: bool = True) -> None:
        """Hand the session work. ``opening`` marks the text that opens a commission.

        A session takes several commissions, one after the other. The text
        that opens the one under way stays in the conversation word for
        word when the rest is summed up: what the commission is, what was
        said of it and what was picked for it is all in that text.

        ``human`` False says that no person wrote the text (another
        session did, or the host): the run it starts is not one the user
        spoke in, for what only the user's own words allow.
        """
        if self._closing:
            raise SessionError("the session is closed")
        if self.running:
            raise SessionError("a run is in progress; use steer or follow_up to add to it")
        message = self._give(text, human)
        if opening:
            # Its entry is known once the run has taken it (``_record``).
            self._opening_message = message
            self._leave_behind()
        self._begin_run(message)

    def _leave_behind(self) -> None:
        """Drop what the commission before this one left unfinished: its to-do list and its goal.

        A session is opened again with both as they were, which is right
        for a commission that goes on. One that is opened now is another
        piece of work: told of the other's open items before its first
        request, it would take them for its own, and the other's goal would
        stand in the way of one of its own.
        """
        self.todos.clear()
        self._image_hashes.clear()
        self._turns_since_todo_write = 0
        self._turns_since_todo_nudge = 0
        self._rapid_compactions = 0
        self._compaction_breaker_tripped = False
        self._compaction_breaker_notified = False
        self._reply_language_hint = None
        if not self._goal_of_this_start:
            self.goals.drop()
            goal = self.spec.goal
            if goal is not None and not self._goal_given:
                # The session was started with a goal, and had one from
                # before that stood in its way: this is the commission the
                # goal was given for.
                self.goals.create(goal.objective, goal.max_rounds)
        self._goal_of_this_start = False
        self._goal_given = True

    def _give(self, text: str, human: bool) -> UserMessage:
        """Count a text the session is given and return the message it becomes."""
        self._inputs += 1
        message = UserMessage(content=text)
        self._given.append((self._inputs, message))
        if not human:
            self._not_human.append(message)
        return message

    def steer(self, text: str, *, human: bool = True) -> None:
        if self._closing:
            raise SessionError("the session is closed")
        if self._compacting:
            # Not a run that would take it: it would be counted as taken and never read.
            raise SessionError("the conversation is being summed up; nothing can be added to it now")
        message = self._give(text, human)
        if self.running:
            self.harness.steer_message(message)
        else:
            self._begin_run(message)

    def follow_up(self, text: str, *, human: bool = True) -> None:
        if self._closing:
            raise SessionError("the session is closed")
        if self._compacting:
            raise SessionError("the conversation is being summed up; nothing can be added to it now")
        message = self._give(text, human)
        if self.running:
            self.harness.follow_up_message(message)
        else:
            self._begin_run(message)

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
        if self._compacting:
            # Not a run of the loop: there is no token for it to look at.
            task.cancel()
            return True
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
        # Background commands are told to stop before anything is waited for.
        # Should the caller give up half way (a runtime that has to exit within
        # a time limit), they are at least no longer running.
        self.jobs.close()
        task = self._run_task
        if task is not None:
            self._abort_requested = True
            self.harness.clear_queues()
            self.harness.cancel()
            if self._compacting:
                task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), CLOSE_GRACE_SECONDS)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if not task.cancelled() or (current is not None and current.cancelling()):
                    raise
                # The summing up was stopped before it took a step: it is
                # over, and the rest of the closing goes on.
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except Exception:  # noqa: BLE001 - the run already reported its own failure
                pass
        if self._abort_timer is not None:
            self._abort_timer.cancel()
            self._abort_timer = None
        # Once more, for a command the run started while it was unwinding.
        self.jobs.close()
        await self.jobs.wait_closed()
        await asyncio.to_thread(self.context.spill.close)
        await self._recorder.close()
        await self._model.aclose()
        if self._gateway is not None:
            await self._gateway.aclose()
        self._emit(protocol.EndedEvent(session_id=self.session_id, reason="closed"))

    async def flush(self) -> None:
        """Wait until everything said so far is written to the transcript."""
        await self._recorder.flush()

    def compact(self) -> None:
        """Sum the conversation up now, whatever its length; its end is told like that of a run.

        For a session nothing is at work in, a background command
        included: what was said is replaced by a summary and the newest
        part of it, as before a request that would not fit. A ``compacted`` event follows when a summary was
        made, and an ``ended`` event either way.
        """
        if self._closing:
            raise SessionError("the session is closed")
        if self.running:
            raise SessionError("a run is in progress")
        if self.jobs.running() or self.jobs.waking():
            # What a background command says when it ends would come while
            # the conversation is summed up: it would reach no run, and
            # wait for whatever is said to the session next.
            raise SessionError("a background command is still running")
        self._abort_requested = False
        self._compacting = True
        task = asyncio.ensure_future(self._compact())
        self._run_task = task
        task.add_done_callback(self._compact_never_ran)

    def _compact_never_ran(self, task: "asyncio.Task[None]") -> None:
        """End a summing up that was stopped before it took its first step.

        A task that is cancelled before it has run never enters its own
        code: nothing in ``_compact`` gets to say that it is over. Left
        like that, the session would count as at work for good.
        """
        if self._run_task is not task:
            # It ran, and ended itself.
            return
        self._compacting = False
        self._run_task = None
        if not self._closing:
            self._emit(protocol.EndedEvent(session_id=self.session_id, reason="aborted", inputs=self._inputs))

    async def _compact(self) -> None:
        reason: protocol.EndReason = "completed"
        error: str | None = None
        # The conversation as it stands before anything is done to it: what
        # it goes back to when the record does not take the summary.
        as_it_was: list[Any] | None = None
        before, due_before = self._compactions, self._compaction_due
        try:
            # As much is kept word for word as a summary before a request
            # keeps: this is not the conversation that was refused as too
            # long. Nor does anything have to go: without a whole summary
            # the conversation stays as it is. What the host said about
            # usage in a run that is over is not part of what is summed up.
            self._drop_usage_notices()
            as_it_was = self.transcript.rows()
            await self._fit(force=True, tight=False, or_drop=False)
            made = await self._summary_on_record(before, due_before, as_it_was)
            if self._compaction_due:
                # Also when no summary was made now and one from before the
                # session was last closed still waits for what follows it:
                # that is done, and the host is not told of a summing up,
                # there having been none this time.
                await self._after_compaction(tell=made)
            await self._recorder.flush()
        except asyncio.CancelledError:
            if not (self._abort_requested or self._closing):
                self._compacting = False
                self._run_task = None
                raise
            reason = "aborted"
            if as_it_was is not None:
                # Stopped, and a summary may be on its way to the record
                # this moment. Whether the record takes it is settled
                # before this is over: the next run goes on from the
                # conversation as it is then. (No longer than the record
                # tries to write an entry.) In a task of its own, and
                # waited for however often this one is stopped again
                # meanwhile (a second abort, a close that ran out of
                # time): over before then, the next run would go on
                # from a summary the record may still refuse, with
                # nothing left that would undo it.
                settling = asyncio.ensure_future(self._summary_on_record(before, due_before, as_it_was))
                while not settling.done():
                    try:
                        await asyncio.shield(settling)
                    except asyncio.CancelledError:
                        continue
                    except Exception:  # noqa: BLE001 - told below
                        break
                if not settling.cancelled() and settling.exception() is not None:
                    logger.warning(
                        "session %s: whether the record took a summary could not be settled: %s",
                        self.session_id,
                        settling.exception(),
                    )
        except Exception as exc:  # noqa: BLE001 - reported through the ended event
            logger.exception("session %s: summing the conversation up failed", self.session_id)
            reason = "error"
            error = f"{type(exc).__name__}: {exc}"
        self._compacting = False
        self._run_task = None
        self._emit(protocol.EndedEvent(session_id=self.session_id, reason=reason, error=error, inputs=self._inputs))

    async def _summary_on_record(self, before: int, due_before: bool, as_it_was: list[Any]) -> bool:
        """Wait for what was said to be written, and return whether a summary was made since ``before`` and is on the record.

        One the record did not take (the disk refused it, after the
        retries) is undone: the conversation goes back to ``as_it_was``,
        which is what the record has. Left with the summary, a later
        one would stand for it and not for what it had replaced, and a
        session opened from the record would be handed that later
        summary and the old replies both. Nor is it counted, here, in
        the snapshot or with the gateway, or told to the host.
        """
        await self._recorder.flush()
        if self._compactions <= before:
            return False
        if not self._recorder.gave_up_on(self._compaction_entry_id):
            return True
        self.transcript.rewrite(as_it_was)
        self.context.invalidate()
        self._compactions, self._compaction_due = before, due_before
        if self._device_fold_at > before:
            self._device_fold_at = 0
        if self._gateway is not None:
            # Nor is the gateway to take what it handed over before (a
            # group of tools and how they are used) for gone: the
            # conversation that is back holds it.
            self._gateway.compactions = before
        return False

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def _drop_usage_notices(self) -> None:
        """Take what the host said about usage in earlier runs out of the conversation.

        That no more may be used, and that what there is has to be handed
        over, was said for the run it was said in. The host is asked
        again before the first request of every run, and says it again
        if it still holds. Left in, it would have the next commission of
        the session, or this one after the user allowed more, hand over
        at once. Only in memory, like other cuts: the saved transcript
        keeps it, and a session opened from it does this again.
        """
        rows = self.transcript.rows()
        kept = [
            row
            for row in rows
            if not (isinstance(row.message, CustomMessage) and row.message.custom_type == "notice:usage")
        ]
        if len(kept) != len(rows):
            self.transcript.rewrite(kept)
            self.context.invalidate()

    def _begin_run(self, message: AgentMessage, goal_round: GoalRound | None = None) -> None:
        self._drop_usage_notices()
        self._abort_requested = False
        self._run_task = asyncio.ensure_future(self._run(message, goal_round))

    async def _run(self, message: AgentMessage, goal_round: GoalRound | None) -> None:
        reason: protocol.EndReason = "completed"
        error: str | None = None
        self._last_assistant = None
        # Model requests made since something from outside set this off: the
        # text or notice the run began with, or a notice that came in as it
        # was ending. The limit on them (``RuntimeLimits.max_turns``) holds
        # for all the rounds of a goal that follow: counted for each round
        # alone, a goal would multiply the limit by its rounds.
        requests = 0
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
                async for event in stream:
                    if isinstance(event, TurnEndEvent):
                        requests += 1
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
                    # It came from outside, as what begins a run does, and
                    # has the limit to itself as a run has: it would have
                    # begun one had it come a moment later. Held to what is
                    # left, a notice that comes in as the last request
                    # allowed is answered would get no request at all, and
                    # the answer that was written would end as an error.
                    requests = 0
                    stream = self.harness.continue_()
                    continue
                following = next_goal_round(self.goals)
                if following is None:
                    break
                prompt, goal_round = following
                stream = self.harness.prompt_message(
                    CustomMessage(custom_type="notice:goal_round", content=prompt, display=False),
                    turns_taken=requests,
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
            # What was given and never got into the conversation went with
            # the queues: it is not kept to be looked for.
            self._given.clear()
            self._not_human.clear()
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
                inputs=self._inputs,
                jobs=len(self.jobs.running()),
                waking_jobs=len(self.jobs.waking()),
            )
        )
