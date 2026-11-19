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

"""``DshRuntime``: task agent sessions that run in dsh, driven over ACP.

It satisfies ``nekoclaw.runtime.core.AgentRuntime`` and sends the events of
``nekoclaw.runtime.protocol``, so a host uses it where it uses the built-in
runtime. What a host can count on, and where dsh makes it differ:

* **One dsh process holds many sessions**, as the built-in runtime's child
  does. It is started by the first ``start``, never earlier, and ended when
  all its sessions have been idle for a while, which closes them
  (``ended`` / ``closed``); a session started again later resumes.
* **A session's dsh session id is not the host's.** dsh makes its own; the
  driver keeps the pair in the session directory (``SESSION_FILE``) and
  resumes from it.
* **Text cannot be put into a turn that is under way.** ACP takes one
  prompt at a time. ``steer`` and ``follow_up`` both wait for the turn to
  end and are then sent as the next one, without an ``ended`` in between;
  ``ended`` comes when nothing is left, with ``inputs`` counting every text
  taken. A session that calls ``wait_agent`` gets what is waiting as that
  call's result.
* **Text arrives a reply at a time**, not in increments: one ``text`` event
  per reply. Tool calls have a start and an end and no progress in between.
* **Permissions are decided by the host**: dsh asks before every call of a
  tool but a few that need none (``profile.py``), and the answer is the
  host's ``decide_permission``. For dsh's file and command tools the
  request is classified as in the built-in runtime. Two kinds of request
  only this driver makes (``nekoclaw.runtime.host``): ``gateway`` for a
  tool of the host's gateway, asked before the call because dsh ends such
  a call after a minute, and ``other`` for a tool the driver does not know
  (one a dsh plugin brought).
* **What the host tells a session** (``TaskAgentSpec.instructions``, and
  that the language to write to the user in is named by each commission's
  opening message) is the head of the session's first message; dsh
  has one system prompt for all its sessions. A session takes several
  commissions, one after the other, and is resumed for each: what it was
  told at first is in dsh's record of it and is not told again, as a
  session of the built-in runtime keeps the system prompt it was opened
  with. What a commission is, is the message that opens it, here as there.
* **dsh sums a conversation up on its own and does not say so.** So
  nothing is put together anew after a summary, and nothing is kept from
  one: what the session was told at first and the message that opened the
  commission under way may go into the summary with the rest (``prompt``
  takes ``opening`` and makes nothing of it), and a group of built-in
  tools the gateway handed over before a summary is not handed over anew
  (dsh names no count of summaries with its calls).
* A model endpoint that refuses a request as too long and states its window
  is reported (``model_capability``), for the sessions the host starts
  later; the session itself stays in its dsh process, which was started
  for the window it has.
* ``usage`` reports what the model endpoint reported for each request
  (``forward.py``).
* ``TaskAgentSpec.goal``, ``limits``, ``image_retention`` and
  ``image_pools`` have no counterpart in dsh and are not applied.

A session whose model cannot be served (``forward.unsupported_reason``), a dsh that
is not installed, a process that does not come up or whose permission guard
did not load: each is a rejected ``start`` (an ``error`` event), as with the
built-in runtime, and the host decides what to do about it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nekoclaw.dsh import profile
from nekoclaw.dsh.acp import AcpClosed, AcpConnection, AcpError
from nekoclaw.dsh.endpoint import LocalEndpoint
from nekoclaw.dsh.forward import ModelForwarder, unsupported_reason
from nekoclaw.dsh.host_tools import ON_ANSWERED, HostTools
from nekoclaw.dsh.profile import SESSION_FILE, DshLaunch, DshUnavailable, Shape
from nekoclaw.runtime import protocol
from nekoclaw.runtime.host import HostBridge, NoHost
from nekoclaw.runtime.permissions import GATEWAY_CALL_CATEGORY, HOST_TOOL_CALL_CATEGORY
from nekoclaw.runtime.protocol import EventSink
from utils.asyncio_executor import to_thread_through_cancellation

logger = logging.getLogger(__name__)

ACP_PROTOCOL_VERSION = 1
# The smallest window a session is managed for; ``protocol.ModelConfig`` takes no less.
_MIN_CONTEXT_WINDOW = protocol.MIN_CONTEXT_WINDOW
_INVALID_PARAMS = -32602
_IDLE_POLL_SECONDS = 1.0
# How long ``shutdown`` lets sessions on their way in finish starting before it gives them up.
_SHUTDOWN_OPENING_WAIT_SECONDS = 5.0
_ALLOW = {"outcome": {"outcome": "selected", "optionId": "allow-once"}}
_REJECT = {"outcome": {"outcome": "selected", "optionId": "reject-once"}}
_CANCELLED = {"outcome": {"outcome": "cancelled"}}


# Returns where dsh is installed; raises ``DshUnavailable`` when it is not. Blocking.
LaunchProvider = Callable[[], DshLaunch]


@dataclass(slots=True)
class _Process:
    """One dsh process and the sessions it holds."""

    shape: Shape
    connection: AcpConnection
    home: Path
    # What this process sends as the key of its model requests.
    token: str
    free_slots: list[int]
    guard_ready: asyncio.Event = field(default_factory=asyncio.Event)
    sessions: dict[str, "_Session"] = field(default_factory=dict)
    # dsh session id -> session.
    by_dsh_id: dict[str, "_Session"] = field(default_factory=dict)
    # No new session is given to it: it is being ended, or has gone.
    retiring: bool = False
    idle_since: float | None = None
    watcher: asyncio.Task[None] | None = None


class _Given(str):
    """A text a session was given, and which one it is, counted from 1 in the order they were given."""

    number: int

    def __new__(cls, text: str, number: int) -> "_Given":
        made = super().__new__(cls, text)
        made.number = number
        return made


def _as_given(texts: list[str]) -> list[str]:
    """Return texts that are queued again with those that wait, in the order they were given.

    Whichever of several answers is heard of first: what is queued again
    was given before what waits, and what one call was handed before
    what the next was.
    """
    return sorted(texts, key=lambda text: text.number)


def _numbers_of(texts: list[str]) -> list[int]:
    return [text.number for text in texts if isinstance(text, _Given)]


@dataclass(slots=True)
class _LeftOpen:
    """An answer a turn stopped waiting for: the numbers of the texts it carried, and what becomes of them."""

    numbers: list[int]
    # What the turn began with, where the turn failed before it was known
    # that dsh had begun it: told of as taken with the texts above, should
    # their answer turn out to have got there. The same list, and not a
    # copy of it, for all the answers the turn left open: whichever says
    # so first tells of it and empties it, so that it is told of once. (A
    # copy each would tell of it once for every answer.)
    began: list[int] = field(default_factory=list)
    # Whether they are queued again where the answer went nowhere: not
    # those of a run that was stopped.
    again: bool = True


@dataclass(slots=True)
class _Session:
    spec: protocol.TaskAgentSpec
    # Done once the attempt to create the session is over.
    opening: asyncio.Future[None]
    # The task that creates it, while it does.
    opener: asyncio.Task[tuple[bool, list[str]]] | None = None
    process: _Process | None = None
    dsh_id: str = ""
    slot: int = -1
    seq: int = 0
    # Texts waiting to be sent: a prompt, and what was added since. Each
    # is a ``_Given``: it knows its number.
    queue: list[str] = field(default_factory=list)
    # How many texts the session was given (those it queued); the last one's number.
    given: int = 0
    # The number of the last text the session has taken, for ``ended.inputs``:
    # how many it took, where none was dropped on the way.
    taken: int = 0
    # The numbers of the texts a call of the turn under way was handed
    # (``_take_input``), until that turn is over.
    awaited: list[int] = field(default_factory=list)
    # Of those, the ones whose answer is still on its way to dsh: whether
    # it got there (set by whoever serves the call), and their numbers.
    answering: list[tuple[asyncio.Future[bool], list[int]]] = field(default_factory=list)
    # Those a turn stopped waiting for (``_answers_known``): not told of as
    # taken, and dealt with when it is known after all.
    unsettled: dict[asyncio.Future[bool], _LeftOpen] = field(default_factory=dict)
    # Texts an earlier run's call was handed, whose answer was heard to
    # have gone nowhere while the run under way was being stopped: queued
    # again once the stop is through (``_give_back``).
    kept_back: list[str] = field(default_factory=list)
    # Counts the runs that are over. A call dsh makes belongs to the run
    # under way as it is made, and is handed nothing once that run is over.
    runs: int = 0
    # Counts the turns (``session/prompt`` requests) that are over, likewise:
    # a call is handed nothing once the turn it was made in is over, though
    # the run goes on (what came meanwhile is the next turn's).
    turns: int = 0
    # Whether a turn is with dsh right now: a call that comes in between two
    # turns is a late one of the turn before, and is handed nothing either.
    in_turn: bool = False
    # False until dsh took the first message, which carries the host's instructions.
    briefed: bool = False
    # Whether it is offered the subagent tool, which its first message then tells of.
    subagents: bool = False
    # dsh sessions its directory had before, which dsh could not resume.
    previous: tuple[str, ...] = ()
    # Whether dsh took up the session it had of this one: what was said in it is there.
    resumed: bool = False
    run: asyncio.Task[None] | None = None
    aborting: bool = False
    closing: asyncio.Future[None] | None = None
    # Tool calls dsh announced: id -> (name as dsh has it, arguments).
    tool_calls: dict[str, tuple[str, Any]] = field(default_factory=dict)
    # Permission requests being decided right now.
    asks: set[asyncio.Task[Any]] = field(default_factory=set)
    # Set whenever a text is queued; ``wait_agent`` waits on it.
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    # Text of the last reply of the run, and whether the run has said anything.
    last_text: str = ""
    last_message_id: str = ""
    spoke: bool = False
    # The windows its model endpoint stated that the host was told of.
    windows_told: set[int] = field(default_factory=set)
    # The next reply is the endpoint's own word after a closing report
    # (``forward.py``), not something the task agent said.
    local_reply_due: bool = False

    @property
    def id(self) -> str:
        return self.spec.session_id

    @property
    def busy(self) -> bool:
        return self.run is not None or bool(self.asks) or not self.opening.done() or self.closing is not None


def shape_of(model: protocol.ModelConfig) -> Shape:
    from nekoclaw.runtime.context import compact_ratio

    return Shape(
        context_window=model.context_window,
        max_output_tokens=model.max_output_tokens,
        supports_images=model.supports_images,
        compact_ratio=round(compact_ratio(model.context_window, model.compact_at), 4),
    )


def _shape_tag(shape: Shape) -> str:
    text = f"{shape.context_window}:{shape.max_output_tokens}:{int(shape.supports_images)}:{shape.compact_ratio}"
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:8]


def _read_session_file(session_dir: str) -> dict[str, Any] | None:
    """Blocking: return what the session file says, or None."""
    try:
        data = json.loads(Path(session_dir, SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_session_file(
    session_dir: str,
    dsh_id: str,
    workdir: str,
    *,
    slot: int = -1,
    briefed: bool = False,
    previous: tuple[str, ...] = (),
) -> None:
    """Blocking: note which dsh session belongs to a session directory.

    ``slot`` is the slot its model requests were last made under; see
    ``DshRuntime._open`` for why a resumed session is given it again.

    ``briefed`` says that dsh took the session's first message, the one
    that carries what the host tells the commission: a session resumed
    without it is told again. ``previous`` are dsh sessions the directory
    had before this one, which dsh could not resume; they are kept so that
    what dsh may still hold of them is removed with the rest
    (``DshInstallation.forget_session``).
    """
    from config.nekoclaw_settings import DSH_VERSION

    directory = Path(session_dir)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"{SESSION_FILE}.tmp"
    note = {
        "session": dsh_id,
        "workdir": workdir,
        "version": DSH_VERSION,
        "slot": slot,
        "briefed": briefed,
        "previous": list(previous),
    }
    temporary.write_text(json.dumps(note, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, directory / SESSION_FILE)


def _gone(error: AcpError) -> bool:
    """Whether dsh's refusal to resume a session says that it has no such session.

    That is one of its refusals of the parameters (the others: the session
    is active already, its working directory is another). Anything else, an
    error inside dsh or a service it cannot reach, says nothing about the
    session, which may well be there.
    """
    return error.code == _INVALID_PARAMS and "not resumable" in error.detail


def _well_formed(name: str, arguments: Any) -> bool:
    """Whether a call of one of dsh's file or shell tools says what it is about to touch.

    dsh would refuse one that does not; should it not, nothing would tell
    what the call reads, writes or runs, and it is not for the host to
    guess that it stays inside the working directory.
    """
    if not isinstance(arguments, dict):
        return False
    if name in ("pwsh", "bash"):
        command, directory = arguments.get("command"), arguments.get("workdir")
        return isinstance(command, str) and bool(command.strip()) and (directory is None or isinstance(directory, str))
    if name in ("glob", "grep"):
        # Without a path they search the working directory.
        path = arguments.get("path")
        return path is None or isinstance(path, str)
    path = arguments.get("file_path")
    return isinstance(path, str) and bool(path.strip())


def _same_directory(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


class DshRuntime:
    """``AgentRuntime`` whose sessions live in dsh processes."""

    def __init__(
        self,
        on_event: EventSink,
        host: HostBridge | None = None,
        *,
        launch: LaunchProvider,
        idle_exit_seconds: float | None = None,
        environment: Mapping[str, str] | None = None,
        model_transport: Any = None,
    ) -> None:
        from config.prompts.prompts_nekoclaw_dsh import HOST_TOOL_PREFIX

        self._on_event = on_event
        self._host: HostBridge = host if host is not None else NoHost()
        self._launch = launch
        self._idle_exit_seconds = idle_exit_seconds
        self._environment = environment
        self._tools = HostTools(self._host)
        self._forwarder = ModelForwarder(
            self._on_usage,
            report_tool=f"{HOST_TOOL_PREFIX}report",
            transport=model_transport,
            on_local_reply=self._on_local_reply,
            on_window=self._on_window,
        )
        self._endpoint = LocalEndpoint(forwarder=self._forwarder, tools=self._tools)
        self._sessions: dict[str, _Session] = {}
        self._processes: dict[Shape, _Process] = {}
        self._process_lock = asyncio.Lock()
        # Calls of ``shutdown`` at work right now; see ``RuntimeCore``.
        self._shutdowns = 0
        # Sessions being started, also those dropped meanwhile; shutting down waits for them.
        self._starting: list[_Session] = []
        # Keeps the tasks alive that tell dsh to drop a session nobody got.
        self._closing_quietly: set[asyncio.Task[None]] = set()
        # Processes being ended for having been idle; shutting down waits for them.
        self._retiring: set[asyncio.Task[None]] = set()

    # -- state ------------------------------------------------------------

    @property
    def session_ids(self) -> list[str]:
        return list(self._sessions)

    def resumed(self, session_id: str) -> bool:
        """Whether an open session goes on in the dsh session it had: what was said in it is there.

        False for one that dsh began anew, having nothing to take up or no
        longer being able to (its log is gone, the working directory is
        another): whatever the session was told before is not in it.
        """
        session = self._sessions.get(session_id)
        return session is not None and session.resumed

    @property
    def process_ids(self) -> list[int]:
        """Process ids of the dsh processes running right now."""
        return [pid for pid in (process.connection.pid for process in self._processes.values()) if pid is not None]

    @property
    def endpoint(self) -> LocalEndpoint:
        return self._endpoint

    # -- events -----------------------------------------------------------

    def _deliver(self, message: dict[str, Any]) -> None:
        try:
            self._on_event(message)
        except Exception:  # noqa: BLE001 - a broken sink must not stop the driver
            logger.exception("the event sink failed")

    def _emit(self, session: _Session, event: Any) -> None:
        event.seq = session.seq
        session.seq += 1
        self._deliver(protocol.dump(event))

    def _reject(self, session_id: str, command: str, message: str, serial: int | None = None) -> None:
        self._deliver(
            protocol.dump(
                protocol.ErrorEvent(session_id=session_id, command=command, message=message, serial=serial)
            )
        )

    def _on_usage(self, session_id: str, metadata: dict[str, Any]) -> None:
        from nekoclaw.runtime.model import usage_from_metadata

        session = self._sessions.get(session_id)
        if session is None:
            return
        usage = usage_from_metadata(metadata)
        prompt_tokens = usage.input + usage.cache_read + usage.cache_write
        if not (prompt_tokens or usage.output):
            return
        self._emit(
            session,
            protocol.UsageEvent(
                session_id=session.id,
                input=usage.input,
                output=usage.output,
                cache_read=usage.cache_read,
                cache_write=usage.cache_write,
                reasoning=usage.reasoning,
                total_tokens=usage.total_tokens,
                context_tokens=prompt_tokens + usage.output,
                context_window=session.spec.model.context_window,
            ),
        )

    def _on_local_reply(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.local_reply_due = True

    def _on_window(self, session_id: str, model: Any, tokens: int, said: str) -> None:
        """A session's model endpoint refused a request as too long and stated its window.

        Told to the host when the window is smaller than the one the
        session was started with, once for each number: the host starts
        sessions for this model with that window from then on. This
        session stays in its dsh process, which was started for the window
        it has and sums the conversation up as dsh does.

        ``model`` is the one the refused request went to. A request can
        outlast its session (one that did not close in time is forgotten,
        its requests are not called off), and under the id there may be
        another session by then, with another model: what that endpoint
        said of its window is not about this one's, and the host would put
        it down for the wrong model.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        asked = session.spec.model
        if (getattr(model, "base_url", None), getattr(model, "model", None)) != (asked.base_url, asked.model):
            return
        stated = max(int(tokens), _MIN_CONTEXT_WINDOW)
        if stated >= session.spec.model.context_window or stated in session.windows_told:
            return
        session.windows_told.add(stated)
        self._emit(
            session,
            protocol.ModelCapabilityEvent(session_id=session.id, context_window=stated, reason=said[:300]),
        )

    def _on_report(self, session: _Session, payload: dict[str, Any]) -> None:
        if self._sessions.get(session.id) is not session:
            # From a call of a session that is gone: under its id there may be
            # another session by now, whose report this is not.
            return
        self._emit(
            session,
            protocol.ReportEvent(
                session_id=session.id,
                status=payload["status"],
                summary=str(payload.get("summary") or ""),
                details=payload.get("details"),
                delivery=payload.get("delivery"),
            ),
        )

    # -- processes --------------------------------------------------------

    async def prepare(self, model: protocol.ModelConfig) -> None:
        """Make dsh ready for a session with this model; raises ``DshUnavailable`` when it cannot be.

        Brings the dsh process up, if none is running that fits, and
        confirms that it loaded the permission guard. For a host that wants
        to know whether dsh can take a commission before it gives it one; a
        ``start`` does the same and reports a failure as an ``error`` event.
        """
        if self._shutdowns:
            raise DshUnavailable("the runtime is shutting down")
        reason = unsupported_reason(model)
        if reason is not None:
            raise DshUnavailable(reason)
        await self._ensure_process(shape_of(model))

    async def _ensure_process(self, shape: Shape) -> _Process:
        async with self._process_lock:
            process = self._processes.get(shape)
            if process is not None and process.connection.alive and not process.retiring:
                return process
            process = await self._start_process(shape)
            self._processes[shape] = process
            return process

    async def _start_process(self, shape: Shape) -> _Process:
        from config.nekoclaw_settings import (
            DSH_CLOSE_SECONDS,
            DSH_GUARD_READY_SECONDS,
            DSH_READY_SECONDS,
            DSH_SESSION_SLOTS,
            DSH_STDERR_LINES_KEPT,
        )

        launch = await asyncio.to_thread(self._launch)
        await self._endpoint.start()
        patch = profile.build_patch(
            shape=shape, endpoint=self._endpoint.model_url, slots=DSH_SESSION_SLOTS, home=launch.home
        )

        def prepare() -> tuple[Path, Path]:
            return profile.write_profile(launch.home, patch, tag=_shape_tag(shape)), profile.start_directory(launch.home)

        patch_path, start_directory = await asyncio.to_thread(prepare)
        holder: list[_Process] = []
        connection = AcpConnection(
            on_notification=lambda method, params: self._on_notification(holder[0], method, params),
            on_request=lambda method, params: self._on_request(holder[0], method, params),
            on_stderr=lambda line: self._on_stderr(holder[0], line),
            on_closed=lambda: self._on_process_gone(holder[0]),
            stderr_lines_kept=DSH_STDERR_LINES_KEPT,
        )
        process = _Process(
            shape=shape,
            connection=connection,
            home=launch.home,
            token=self._endpoint.issue_process_token(),
            free_slots=list(range(DSH_SESSION_SLOTS)),
        )
        holder.append(process)
        base = self._environment if self._environment is not None else os.environ
        environment = profile.child_environment(base, home=launch.home, token=process.token)
        try:
            await connection.open(
                profile.launch_arguments(launch.executable, patch_path, launch.leading),
                cwd=str(start_directory),
                env=environment,
            )
        except BaseException as exc:
            self._endpoint.revoke_process_token(process.token)
            if isinstance(exc, OSError):
                raise DshUnavailable(f"dsh could not be started: {exc}") from exc
            raise
        try:
            answer = await connection.request(
                "initialize",
                {
                    "protocolVersion": ACP_PROTOCOL_VERSION,
                    "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                    "clientInfo": {"name": "nekoclaw", "version": "1"},
                },
                timeout=DSH_READY_SECONDS,
            )
            capabilities = answer.get("agentCapabilities") if isinstance(answer, dict) else None
            capabilities = capabilities if isinstance(capabilities, dict) else {}
            sessions = capabilities.get("sessionCapabilities")
            if not isinstance(sessions, dict) or "resume" not in sessions or "close" not in sessions:
                raise DshUnavailable("this dsh cannot resume or close sessions over ACP")
            if not (capabilities.get("mcpCapabilities") or {}).get("http"):
                raise DshUnavailable("this dsh cannot reach the host's tools over HTTP")
            try:
                await asyncio.wait_for(process.guard_ready.wait(), DSH_GUARD_READY_SECONDS)
            except asyncio.TimeoutError as exc:
                # Without it dsh's tools would act without asking: not used at all.
                raise DshUnavailable("dsh did not load the permission guard") from exc
        except BaseException as exc:
            process.retiring = True
            await asyncio.shield(connection.close(DSH_CLOSE_SECONDS))
            if isinstance(exc, DshUnavailable):
                raise
            if isinstance(exc, asyncio.TimeoutError):
                raise DshUnavailable(f"dsh did not answer in time{self._stderr_note(connection)}") from exc
            if isinstance(exc, AcpError):
                raise DshUnavailable(f"dsh could not be started: {exc.detail}{self._stderr_note(connection)}") from exc
            raise
        process.watcher = asyncio.ensure_future(self._watch_idle(process))
        return process

    @staticmethod
    def _stderr_note(connection: AcpConnection) -> str:
        tail = " | ".join(list(connection.stderr_tail)[-3:])
        return f" ({tail[:400]})" if tail else ""

    def _on_stderr(self, process: _Process, line: str) -> None:
        if profile.GUARD_READY in line:
            process.guard_ready.set()
        else:
            logger.debug("dsh: %s", line[:500])

    def _on_process_gone(self, process: _Process) -> None:
        """The dsh process ended: its sessions end with it."""
        process.retiring = True
        self._endpoint.revoke_process_token(process.token)
        if self._processes.get(process.shape) is process:
            del self._processes[process.shape]
        if process.watcher is not None:
            process.watcher.cancel()
        orphans, process.sessions = list(process.sessions.values()), {}
        process.by_dsh_id.clear()
        for session in orphans:
            self._forget(session)
            if not session.opening.done():
                # Its start is waiting for dsh's answer and reports the failure itself.
                continue
            if session.closing is not None and not session.closing.done():
                # Its close is waiting for dsh's answer and announces the end itself.
                continue
            self._emit(
                session,
                protocol.EndedEvent(session_id=session.id, reason="error", error=protocol.PROCESS_EXITED_ERROR),
            )

    async def _watch_idle(self, process: _Process) -> None:
        """End a process whose sessions have all been idle for a while."""
        from config.nekoclaw_settings import DSH_IDLE_EXIT_SECONDS

        limit = self._idle_exit_seconds if self._idle_exit_seconds is not None else DSH_IDLE_EXIT_SECONDS
        loop = asyncio.get_running_loop()
        while not process.retiring:
            await asyncio.sleep(min(_IDLE_POLL_SECONDS, max(0.05, limit / 4)))
            if any(session.busy for session in process.sessions.values()):
                process.idle_since = None
                continue
            if process.idle_since is None:
                process.idle_since = loop.time()
                continue
            if loop.time() - process.idle_since < limit:
                continue
            # From here on it takes no new session; one asked for meanwhile
            # goes to a new process.
            process.retiring = True
            if self._processes.get(process.shape) is process:
                del self._processes[process.shape]
            task = asyncio.ensure_future(self._retire(process))
            self._retiring.add(task)
            task.add_done_callback(self._retiring.discard)
            return

    async def _retire(self, process: _Process) -> None:
        """Close a process's sessions and end it."""
        from config.nekoclaw_settings import DSH_CLOSE_SECONDS

        process.retiring = True
        if process.watcher is not None and process.watcher is not asyncio.current_task():
            process.watcher.cancel()
        closes = [self._close_session(session) for session in list(process.sessions.values())]
        try:
            if closes:
                # Shielded: whoever waits here being cancelled must not cancel the closes.
                await asyncio.shield(asyncio.gather(*closes, return_exceptions=True))
        finally:
            # Also when this was cancelled: nobody else ends the process.
            await asyncio.shield(process.connection.close(DSH_CLOSE_SECONDS))

    # -- dsh speaks -------------------------------------------------------

    @staticmethod
    def _public_name(name: str) -> str:
        """Return a tool's name as the built-in runtime knows it."""
        from config.prompts.prompts_nekoclaw_dsh import HOST_TOOL_PREFIX

        for prefix in (HOST_TOOL_PREFIX, f"mcp__{profile.GATEWAY_SERVICE}__"):
            if name.startswith(prefix):
                return name[len(prefix) :]
        return name

    def _on_notification(self, process: _Process, method: str, params: dict[str, Any]) -> None:
        from config.prompts.prompts_nekoclaw_dsh import DSH_AFTER_REPORT_REPLY

        if method != "session/update":
            return
        session = process.by_dsh_id.get(str(params.get("sessionId") or ""))
        update = params.get("update")
        if session is None or not isinstance(update, dict):
            return
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content")
            text = str(content.get("text") or "") if isinstance(content, dict) and content.get("type") == "text" else ""
            if not text:
                return
            if session.local_reply_due:
                session.local_reply_due = False
                if text == DSH_AFTER_REPORT_REPLY:
                    # Put into dsh's record so that its turn ends; the built-in
                    # runtime ends a run at a closing report without a word.
                    return
            message_id = str(update.get("messageId") or "")
            same_reply = bool(message_id) and message_id == session.last_message_id
            session.last_text = session.last_text + text if same_reply else text
            session.last_message_id = message_id
            # Replies arrive whole; a blank line keeps two of them apart where
            # the host joins the increments of a run.
            delta = text if same_reply or not session.spoke else f"\n\n{text}"
            session.spoke = True
            self._emit(session, protocol.TextEvent(session_id=session.id, delta=delta))
        elif kind == "tool_call":
            call_id = str(update.get("toolCallId") or "")
            name = str(update.get("title") or "")
            arguments = update.get("rawInput")
            session.tool_calls[call_id] = (name, arguments)
            self._emit(
                session,
                protocol.ToolStartEvent(
                    session_id=session.id,
                    tool_call_id=call_id,
                    tool_name=self._public_name(name),
                    args=arguments if isinstance(arguments, dict) else {"input": arguments},
                ),
            )
        elif kind == "tool_call_update":
            status = update.get("status")
            if status not in ("completed", "failed"):
                return
            call_id = str(update.get("toolCallId") or "")
            # Over: nothing is asked about it any more, and what it was
            # called with (a whole file, for a write) need not be kept.
            name = session.tool_calls.pop(call_id, ("", None))[0]
            parts = []
            for item in update.get("content") or []:
                inner = item.get("content") if isinstance(item, dict) else None
                if isinstance(inner, dict) and inner.get("type") == "text":
                    parts.append(str(inner.get("text") or ""))
            self._emit(
                session,
                protocol.ToolEndEvent(
                    session_id=session.id,
                    tool_call_id=call_id,
                    tool_name=self._public_name(name),
                    is_error=status == "failed",
                    text="\n".join(parts),
                ),
            )

    async def _on_request(self, process: _Process, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if method != "session/request_permission":
            raise AcpError(-32601, f"{method} is not supported")
        session = process.by_dsh_id.get(str(params.get("sessionId") or ""))
        call = params.get("toolCall")
        call_id = str(call.get("toolCallId") or "") if isinstance(call, dict) else ""
        if session is None or call_id not in session.tool_calls:
            # Nothing is known of what it would allow.
            return _REJECT
        task = asyncio.current_task()
        assert task is not None
        session.asks.add(task)
        try:
            return _ALLOW if await self._decide(session, *session.tool_calls[call_id]) else _REJECT
        except asyncio.CancelledError:
            if session.aborting or session.closing is not None:
                # The run was stopped while the user was being asked.
                return _CANCELLED
            raise
        except Exception:  # noqa: BLE001 - a failing approver must fail closed
            logger.warning("deciding a permission for a dsh session failed", exc_info=True)
            return _REJECT
        finally:
            session.asks.discard(task)

    async def _decide(self, session: _Session, name: str, arguments: Any) -> bool:
        """Return whether the host allows one call of a dsh tool."""
        from nekoclaw.runtime.permissions import classify, default_permission_decision

        from config.prompts.prompts_nekoclaw_dsh import HOST_TOOL_PREFIX

        process = session.process
        given = arguments if isinstance(arguments, dict) else {}
        gateway_prefix = f"mcp__{profile.GATEWAY_SERVICE}__"
        if name in profile.GATED_TOOLS:
            if not _well_formed(name, arguments):
                return False
            # What dsh saved of this session's own long results is read like
            # a file of the working directory; another session's is not.
            readable = (profile.session_spill_dir(process.home, session.dsh_id),) if process is not None else ()
            request = classify(name, given, Path(session.spec.workdir), readable=readable)
            payload = {**request.to_json(), "default": default_permission_decision(request)}
        elif name.startswith(HOST_TOOL_PREFIX):
            # One of the host's own tools the guard asks about
            # (``profile.ASKED_HOST_TOOLS``): the host asks the user now what
            # it would ask in the call, where dsh would not wait for the answer.
            payload = {
                "tool": name[len(HOST_TOOL_PREFIX) :],
                "category": HOST_TOOL_CALL_CATEGORY,
                "arguments": given,
                "workdir": session.spec.workdir,
                "default": "allow",
            }
        elif name.startswith(gateway_prefix):
            # The gateway rules on its own tools. It is asked now, before the
            # call, and is not to ask again when the call arrives.
            payload = {
                "tool": name[len(gateway_prefix) :],
                "category": GATEWAY_CALL_CATEGORY,
                "arguments": given,
                "workdir": session.spec.workdir,
                "default": "allow",
            }
        else:
            # A tool the driver does not know: one a dsh plugin brought.
            payload = {
                "tool": name,
                "category": "other",
                "arguments": given,
                "workdir": session.spec.workdir,
                "default": "allow",
            }
        if "decide_permission" not in self._host.capabilities:
            return payload["default"] == "allow"
        reply = await self._host.call(session.id, "decide_permission", payload)
        return reply.get("allow") is True

    # -- sessions ---------------------------------------------------------

    def _forget(self, session: _Session, *, slot_free: bool = True) -> None:
        """Give back what a session held. Its dsh process is not told.

        With ``slot_free`` False its slot is not given to another session:
        dsh did not confirm that it let the session go, and what that
        session may still ask of the model under the slot's name must not be
        taken for the next session's. (The requests find no route and fail.)
        """
        if self._sessions.get(session.id) is session:
            del self._sessions[session.id]
            # By id: only while it is the session of that id. Given back once
            # already, another of the same id may hold them by now.
            self._tools.close(session.id)
            self._endpoint.revoke(session.id)
        process = session.process
        if process is not None and session.slot >= 0:
            self._forwarder.release(process.token, profile.slot_model(session.slot))
            if process.sessions.get(session.id) is session:
                del process.sessions[session.id]
            process.by_dsh_id.pop(session.dsh_id, None)
            if slot_free and session.slot not in process.free_slots:
                process.free_slots.append(session.slot)
            session.slot = -1
        for task in list(session.asks):
            task.cancel()
        session.arrived.set()

    def _abandon(self, session: _Session) -> None:
        """Give up a session whose creation failed part way: dsh is told if it knows of it."""
        process = session.process
        if process is not None and session.dsh_id and process.connection.alive:
            # Its slot is given to another session only once dsh let it go; see ``_forget``.
            slot = session.slot
            self._forget(session, slot_free=False)
            task = asyncio.ensure_future(self._close_quietly(process, session.dsh_id, slot))
            self._closing_quietly.add(task)
            task.add_done_callback(self._closing_quietly.discard)
            return
        self._forget(session)

    @staticmethod
    async def _close_quietly(process: _Process, dsh_id: str, slot: int) -> None:
        from config.nekoclaw_settings import DSH_CLOSE_SECONDS

        try:
            await process.connection.request("session/close", {"sessionId": dsh_id}, timeout=DSH_CLOSE_SECONDS)
        except (AcpError, asyncio.TimeoutError):
            # dsh may still hold the session: the slot stays out of use.
            return
        if slot >= 0 and slot not in process.free_slots:
            process.free_slots.append(slot)

    async def _session(self, session_id: str, command: str, serial: int | None = None) -> _Session | None:
        session = self._sessions.get(session_id)
        if session is not None and not session.opening.done():
            # A command sent right after ``start`` waits for the session.
            await asyncio.shield(session.opening)
            session = self._sessions.get(session_id)
        if session is None or session.closing is not None:
            self._reject(session_id, command, f'unknown session "{session_id}"', serial)
            return None
        return session

    async def start(self, spec: protocol.TaskAgentSpec) -> None:
        session_id = spec.session_id
        while True:
            if self._shutdowns:
                self._reject(session_id, "start", "the runtime is shutting down")
                return
            current = self._sessions.get(session_id)
            if current is None:
                break
            if current.closing is None:
                self._reject(session_id, "start", f'session "{session_id}" already exists')
                return
            # Its predecessor is closing: dsh must have let go of the session
            # before it is resumed, and ``closed`` precedes the new ``started``.
            # Looked at again afterwards: another start may have waited for
            # the same close, and taken the id first.
            await asyncio.shield(current.closing)
        # Nothing is awaited between the look above and taking the id.
        session = _Session(spec=spec, opening=asyncio.get_running_loop().create_future())
        self._sessions[session_id] = session
        self._starting.append(session)
        session.opener = asyncio.ensure_future(self._open(session))
        try:
            resumed, tools = await session.opener
            session.resumed = resumed
            process = session.process
            if self._sessions.get(session_id) is not session or process is None or not process.connection.alive:
                # The process ended while the session's note was being
                # written, say: the session went with it, unannounced.
                raise DshUnavailable("the dsh process ended while the session was being started")
        except asyncio.CancelledError:
            self._abandon(session)
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            # Given up by ``shutdown``, not by whoever started it.
            self._reject(session_id, "start", "the runtime is shutting down")
        except DshUnavailable as exc:
            self._abandon(session)
            self._reject(session_id, "start", str(exc))
        except AcpError as exc:
            self._abandon(session)
            self._reject(session_id, "start", f"dsh refused the session: {exc.detail}")
        except Exception as exc:  # noqa: BLE001 - reported to the host, never raised
            logger.exception("could not start dsh session %s", session_id)
            self._abandon(session)
            self._reject(session_id, "start", f"{type(exc).__name__}: {exc}")
        else:
            if session.process is not None:
                # It is not busy until its work arrives, which takes the
                # host a moment: the process is not to be ended for having
                # been idle before this session came.
                session.process.idle_since = None
            self._emit(session, protocol.StartedEvent(session_id=session_id, resumed=resumed, tools=tools))
        finally:
            self._starting = [other for other in self._starting if other is not session]
            session.opener = None
            session.opening.set_result(None)

    async def _open(self, session: _Session) -> tuple[bool, list[str]]:
        """Create or resume a session's dsh session; returns whether it was resumed, and its host tools."""
        from config.nekoclaw_settings import DSH_REQUEST_SECONDS
        from config.prompts.prompts_nekoclaw_dsh import HOST_SERVICE

        spec = session.spec
        reason = unsupported_reason(spec.model)
        if reason is not None:
            raise DshUnavailable(reason)
        known = await asyncio.to_thread(_read_session_file, spec.session_dir)
        resumable = bool(
            known and isinstance(known.get("session"), str) and _same_directory(str(known.get("workdir") or ""), spec.workdir)
        )
        # Nothing is awaited between getting the process and putting the
        # session into it: a process without a session may be ended for
        # being idle at any moment.
        process = await self._ensure_process(shape_of(spec.model))
        if not process.free_slots:
            raise DshUnavailable("the dsh process holds as many sessions as it can")
        # A session that is resumed gets the slot it had, if no other session
        # has it now. dsh's record of a session names the model of its last
        # request, and should it sum the session up before the next one, it
        # asks under that name: under another session's, that request would
        # go to the other's model address, with its key, and be counted for it. (When the slot is
        # taken that can still happen, once; it takes a session resumed with
        # its context nearly full.)
        had = known.get("slot") if resumable and known is not None else None
        session.slot = had if isinstance(had, int) and had in process.free_slots else process.free_slots[0]
        process.free_slots.remove(session.slot)
        session.process = process
        process.sessions[session.id] = session
        # A slot is this process's: another process has slots of the same names.
        self._forwarder.bind(process.token, profile.slot_model(session.slot), session.id, spec.model)
        tools = self._tools.open(
            session.id,
            workdir=spec.workdir,
            session_dir=spec.session_dir,
            top_level=spec.top_level,
            disabled=tuple(spec.disabled_tools),
            take_input=lambda seconds: self._take_input(session, seconds),
            emit_report=lambda payload: self._on_report(session, payload),
        )
        session.subagents = "subagent" in tools
        servers: list[dict[str, Any]] = []
        if tools:
            servers.append(
                {
                    "type": "http",
                    "name": HOST_SERVICE,
                    "url": self._endpoint.tools_url,
                    "headers": [{"name": "Authorization", "value": f"Bearer {self._endpoint.issue(session.id)}"}],
                }
            )
        if spec.gateway is not None:
            servers.append(
                {
                    "type": "http",
                    "name": profile.GATEWAY_SERVICE,
                    "url": spec.gateway.url,
                    "headers": [{"name": "Authorization", "value": f"Bearer {spec.gateway.token}"}],
                }
            )
        connection = process.connection
        resumed = False
        if known is not None:
            session.previous = tuple(item for item in known.get("previous") or () if isinstance(item, str))
        if resumable and known is not None:
            try:
                await connection.request(
                    "session/resume",
                    {"sessionId": known["session"], "cwd": spec.workdir, "mcpServers": servers},
                    timeout=DSH_REQUEST_SECONDS,
                )
            except AcpClosed:
                raise
            except AcpError as exc:
                if not _gone(exc):
                    # dsh may well have the session: starting another would
                    # leave that one behind for good, with what was said in it.
                    raise
                # Its log is gone (dsh was installed anew, say): it starts
                # over. The id is kept, for whatever dsh still holds of it.
                logger.info("dsh session of %s cannot be resumed (%s); starting a new one", session.id, exc.message)
                session.previous = (*session.previous, known["session"])
            else:
                session.dsh_id = known["session"]
                resumed = True
        elif known is not None and isinstance(known.get("session"), str):
            # Its working directory is another now: that session is left behind.
            session.previous = (*session.previous, known["session"])
        if not resumed:
            answer = await connection.request(
                "session/new", {"cwd": spec.workdir, "mcpServers": servers}, timeout=DSH_REQUEST_SECONDS
            )
            dsh_id = answer.get("sessionId") if isinstance(answer, dict) else None
            if not isinstance(dsh_id, str) or not dsh_id:
                raise DshUnavailable("dsh created a session without an id")
            session.dsh_id = dsh_id
        process.by_dsh_id[session.dsh_id] = session
        # Which session a model request belongs to is told by the model name it carries.
        await connection.request(
            "session/set_config_option",
            {"sessionId": session.dsh_id, "configId": "model", "value": profile.model_option(session.slot)},
            timeout=DSH_REQUEST_SECONDS,
        )
        # Told what the host tells this commission when it began, if dsh took
        # its first message then. It may not have: the session can have been
        # closed before any work was given to it.
        session.briefed = resumed and known is not None and known.get("briefed") is True
        # Noted only now that the session is ready: one whose start fails
        # part way is closed and leaves no note behind.
        await self._note(session)
        return resumed, tools

    # -- texts ------------------------------------------------------------

    def _first_message(self, session: _Session, work: str) -> str:
        from config.prompts.prompts_nekoclaw import USER_LANGUAGE_GUIDANCE
        from config.prompts.prompts_nekoclaw_dsh import DSH_FIRST_MESSAGE, DSH_SUBAGENT_SECTION

        spec = session.spec
        parts = []
        if spec.instructions and spec.instructions.strip():
            parts.append(spec.instructions.strip())
        if session.subagents:
            # Not in the system prompt, which every session of the process shares.
            parts.append(DSH_SUBAGENT_SECTION)
        if not parts:
            return work
        # As in the built-in runtime's system prompt: the language itself is
        # named in the message that opens each commission (``work`` here, for
        # the first one), since a session's commissions need not share one.
        parts.append(USER_LANGUAGE_GUIDANCE)
        return DSH_FIRST_MESSAGE.replace("<<INSTRUCTIONS>>", "\n\n".join(parts)).replace("<<WORK>>", work)

    async def _note(self, session: _Session) -> None:
        """Write the note that ties a session directory to its dsh session."""
        spec = session.spec
        # Waited for also when this is cancelled (``shutdown`` giving up a
        # start): a note written late would replace what came after it.
        await to_thread_through_cancellation(
            _write_session_file,
            spec.session_dir,
            session.dsh_id,
            spec.workdir,
            slot=session.slot,
            briefed=session.briefed,
            previous=session.previous,
        )

    async def _note_briefed(self, session: _Session) -> None:
        try:
            await self._note(session)
        except OSError:
            # Resumed, it is told once more.
            logger.warning("the note of dsh session %s could not be written", session.id, exc_info=True)

    def _queue(self, session: _Session, text: str) -> None:
        # Numbered as it is given, like the host numbers what it sends: a
        # text that is dropped before dsh has it leaves its number unused.
        session.given += 1
        session.queue.append(_Given(text, session.given))
        session.arrived.set()
        if session.run is None:
            session.aborting = False
            session.run = asyncio.ensure_future(self._run(session))

    async def _take_input(self, session: _Session, seconds: float) -> list[str]:
        """Hand a waiting session the texts queued for it; see ``host_tools.TakeInput``."""
        # The run the call belongs to: the one under way as it is made. And
        # the turn: a call is made while dsh is at one. One that comes in
        # while none is (dsh answered the request, and the run writes things
        # down before it sends the next) is a late call of the turn before:
        # what is queued by now is the next turn's.
        if not session.in_turn:
            return []
        of_run = session.runs if session.run is not None else None
        of_turn = session.turns
        waited_out = False
        if not session.queue:
            session.arrived.clear()
            try:
                await asyncio.wait_for(session.arrived.wait(), max(0.0, seconds))
            except asyncio.TimeoutError:
                waited_out = True
        if (
            of_run is None
            or session.runs != of_run
            or session.turns != of_turn
            or session.run is None
            or session.aborting
        ):
            # Its run is over, or is being stopped, or the turn it was made
            # in is (dsh answered the request: what the run does next is
            # write things down and send the next turn), and dsh has not
            # called the call off yet. What is queued by now starts the
            # next run or turn, or is that one's: handed to this call, it
            # would go nowhere, and the end of that turn would tell of it
            # as taken.
            return []
        # With nothing in it where the call's time ran out, or where it was
        # woken with nothing left for it (another call of the turn took
        # what arrived).
        texts: list[str] = []
        if not waited_out:
            texts, session.queue = session.queue, []
        numbers = _numbers_of(texts)
        session.taken = max([session.taken, *numbers])
        # They go back as the result of a call dsh made. Whether dsh took
        # that result is not known here (the call may have been called off
        # meanwhile): they are told of as taken when the turn the call
        # belongs to ends as a turn does that was not cut short.
        session.awaited.extend(numbers)
        on_answered = ON_ANSWERED.get()
        if on_answered is not None:
            # Whoever serves the call says whether its answer reached dsh.
            # The end of the turn waits for that (``_run``): where it did
            # not (dsh hung up on this one call, and its turn goes on),
            # they are queued again, ahead of what came since, and are not
            # told of as taken. An answer with no text in it is heard of
            # too, for what it shows where it gets there: that dsh is at
            # work on the turn (``_answers_known``). There is nothing of
            # it to queue again.
            outcome: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            session.answering.append((outcome, numbers))
            on_answered.append(lambda delivered: self._answered(session, texts, of_run, outcome, delivered))
        return texts

    def _answered(
        self, session: _Session, texts: list[str], of_run: int, outcome: asyncio.Future[bool], delivered: bool
    ) -> None:
        """Take note of whether the answer that carried some texts reached dsh; queue them again where it did not.

        Heard of after the turn ended without it (``unsettled``), the
        texts were not told of as taken. Where the answer got there after
        all, they are told of now; where it did not, they are queued
        again, and start a turn if none is under way: left at that, a
        session that stays open (its commission waits for task agents)
        would never be given them. Not those of a run that was stopped:
        they went with the stop, whenever this is heard of.
        """
        if not outcome.done():
            outcome.set_result(delivered)
        left = session.unsettled.pop(outcome, None)
        late = left is not None
        if not delivered:
            # (Whatever becomes of the texts next, queued again or left to
            # a stop that is under way, they are not told of as taken: the
            # end of the turn looks at how each answer fared.)
            if left is None or left.again:
                self._give_back(session, texts, of_run, late=late)
        elif late and self._sessions.get(session.id) is session and session.closing is None:
            # Said as of a run that is over, and of which one: what is
            # told of them is what that run came to. Where the run went on
            # after the turn of the call and is still under way, its end
            # is yet to be announced, and tells of them as of any text it
            # took.
            over = session.runs != of_run
            # (With what its turn began with, where that turn failed
            # before this was known: the answer shows that dsh had begun
            # the turn.)
            numbers = [*left.began, *_numbers_of(texts)]
            left.began.clear()
            self._say_taken(session, numbers, late=of_run if over else None)

    async def _answers_settled(self, session: _Session) -> None:
        """Wait, a bounded while, until it is known of every answer that carried texts whether it reached dsh.

        Called as a turn ends, before what its calls were handed is told
        of as taken. One whose answer is still not known to have got there
        is not told of: whoever sent it finds it untaken, and sends it
        again in time.
        """
        from config.nekoclaw_settings import DSH_ANSWER_SETTLE_SECONDS

        open_ones = [outcome for outcome, _numbers in session.answering if not outcome.done()]
        if open_ones:
            await asyncio.wait(open_ones, timeout=DSH_ANSWER_SETTLE_SECONDS)
        self._answers_known(session)

    def _answers_known(self, session: _Session, began: list[int] | None = None) -> bool:
        """Leave of what a turn's calls were handed (``awaited``) the texts dsh is known to have; stop waiting for the rest.

        Returns whether the answer of any call of the turn is known to
        have got to dsh, with a text in it or with none (a call whose
        time ran out, or that was woken with nothing left for it): dsh
        was at work on the turn then.

        Each text by the answer that carried it last: one whose answer
        went nowhere was queued again, and a later call of the turn may
        have been handed it and got it there. Left out: one not known to
        have got there, or known not to have (a stop that came meanwhile
        kept it from being queued again; it is not in dsh's conversation
        for that). An answer not known of yet is dealt with when it is
        (``_answered``); ``began`` goes with it, for a turn that failed
        (``_LeftOpen.began``).
        """
        got_there: dict[int, bool] = {}
        for outcome, numbers in session.answering:
            for number in numbers:
                got_there[number] = outcome.done() and outcome.result()
        reached = any(outcome.done() and outcome.result() for outcome, _numbers in session.answering)
        session.awaited = [number for number in session.awaited if got_there.get(number, True)]
        session.unsettled.update(
            (outcome, _LeftOpen(numbers, began if began is not None else []))
            for outcome, numbers in session.answering
            if not outcome.done()
        )
        session.answering = []
        return reached

    def _give_back(self, session: _Session, texts: list[str], of_run: int, *, late: bool = False) -> None:
        """Queue again the texts a call was handed whose answer did not reach dsh.

        ``late`` says that the turn of that call ended without knowing
        (``_answered``): they were not told of as taken, and go with a
        turn that is under way or begin one.
        """
        if not texts:
            # The answer carried none: nothing to queue again, and no run
            # to begin for it (one would end at once, with nothing done,
            # and its end would be announced).
            return
        if self._sessions.get(session.id) is not session or session.closing is not None:
            return
        if self._shutdowns:
            # The runtime is shutting down: no turn is begun behind its
            # back. (Whoever sent it and never heard that it was taken has
            # it handed over again.)
            return
        if session.aborting:
            # Being stopped: what the run under way was handed is dealt
            # with as the stop deals with it. What an earlier run was
            # handed, heard of only now, is not this run's to lose: it
            # waits until the stop is through, and is queued again then
            # (``_run``), as it would have been a moment later.
            if late and of_run != session.runs:
                session.kept_back.extend(texts)
            return
        if not late and (session.runs != of_run or session.run is None):
            # The run is over: what it was handed is dealt with as its end
            # dealt with it.
            return
        numbers = set(_numbers_of(texts))
        session.awaited = [number for number in session.awaited if number not in numbers]
        session.queue = _as_given([*texts, *session.queue])
        session.arrived.set()
        if session.run is None:
            session.aborting = False
            session.run = asyncio.ensure_future(self._run(session))

    def _say_taken(self, session: _Session, numbers: list[int], *, late: int | None = None) -> None:
        """Say of the texts with these numbers that dsh has them in its conversation; one ``taken`` each.

        ``late`` is the run that took them, where that run has ended
        already (the number its ``ended`` event carried).
        """
        for number in numbers:
            self._emit(
                session,
                protocol.TakenEvent(session_id=session.id, inputs=number, late=late is not None, run=late),
            )

    async def _run(self, session: _Session) -> None:
        """Send what is queued, turn after turn, until nothing is left; then announce the end."""
        process = session.process
        assert process is not None
        reason: protocol.EndReason = "completed"
        error: str | None = None
        stop_reason: str | None = None
        session.spoke = False
        session.last_text = ""
        session.last_message_id = ""
        session.local_reply_due = False
        # The answers earlier runs left open; what is left open besides
        # from here on is this run's.
        earlier = set(session.unsettled)
        try:
            while session.queue and not session.aborting and session.closing is None:
                texts, session.queue = session.queue, []
                sent = _numbers_of(texts)
                session.taken = max([session.taken, *sent])
                work = "\n\n".join(texts)
                first = not session.briefed
                if first:
                    work = self._first_message(session, work)
                try:
                    session.in_turn = True
                    try:
                        answer = await process.connection.request(
                            "session/prompt",
                            {"sessionId": session.dsh_id, "prompt": [{"type": "text", "text": work}]},
                        )
                    finally:
                        # The turn is over however it ended: no call of it
                        # is handed anything from here on. (The answers its
                        # calls were handed are settled below all the same.)
                        session.in_turn = False
                        session.turns += 1
                except AcpClosed:
                    # The process is gone; that is announced where it was noticed.
                    return
                except AcpError as exc:
                    reason, error = "error", exc.detail
                    if not session.aborting:
                        # Cut short by an error, and not by a stop: what
                        # its calls were handed and dsh is known to have
                        # (the answer got there) is in its conversation,
                        # and is told of as taken. With what the turn
                        # began with, where a call of it was answered
                        # (with a text or with none): the turn was under
                        # way by then, and not refused at the door. An
                        # answer still on its way is not
                        # waited for, here or at the end of a later turn,
                        # and is dealt with when it is known: where it
                        # got there, that shows the same of what the turn
                        # began with, which is told of with it then,
                        # unless it is told of here.
                        began = list(sent)
                        if self._answers_known(session, began):
                            self._say_taken(session, sent)
                            began.clear()
                        self._say_taken(session, session.awaited)
                    # (Asked to stop before dsh answered, with an error as
                    # it happens: what the turn's calls were handed goes
                    # with the stop, as where dsh says that the turn was
                    # cancelled, below.)
                    session.awaited = []
                    session.answering = []
                    break
                # dsh took them: said only now, and not when they were sent.
                # A process that went before it had them, or a request it
                # refused, leaves them untold, so that whoever sent them
                # knows they may have to be sent again.
                self._say_taken(session, sent)
                stopped = isinstance(answer, dict) and str(answer.get("stopReason") or "") == "cancelled"
                if not (session.aborting or stopped):
                    # And what a call of the turn was handed meanwhile: the
                    # turn ran to its end with it. Once it is known that
                    # the answers of those calls got to dsh, that is: one
                    # that did not has its texts queued again by then, and
                    # they go with the next turn.
                    await self._answers_settled(session)
                    self._say_taken(session, session.awaited)
                session.awaited = []
                session.answering = []
                if first:
                    # dsh took it, so it is in the session's record. Until
                    # then the next message carries it (again, at worst):
                    # told twice does no harm, never told loses the commission.
                    session.briefed = True
                    await self._note_briefed(session)
                stop_reason = str(answer.get("stopReason") or "") if isinstance(answer, dict) else ""
                if session.aborting or stop_reason == "cancelled":
                    reason = "aborted"
                    break
        finally:
            session.run = None
            # Over: a call of it that is still waiting is not taken for
            # one of the run that starts next.
            session.runs += 1
        if session.closing is not None or self._sessions.get(session.id) is not session:
            return
        session.tool_calls.clear()
        if session.aborting and reason == "completed":
            # Stopped between two turns, or before the first one was sent.
            reason = "aborted"
        # What this run leaves open. Of those texts it may still be said
        # that the run took them, and of no others: its end names them.
        left = [session.unsettled[outcome] for outcome in session.unsettled.keys() - earlier]
        if reason == "aborted" or session.aborting:
            # Stopped, whatever dsh answered the stop with (an error
            # leaves the reason at that). What was queued when it was
            # stopped is dropped with it. Nor is what its calls were
            # handed queued again, whenever it is heard that an answer of
            # theirs went nowhere: the session is not being stopped any
            # more by then, and the text would begin the turn the stop
            # was to prevent.
            session.queue.clear()
            for item in left:
                item.again = False
        session.aborting = False
        self._emit(
            session,
            protocol.EndedEvent(
                session_id=session.id,
                reason=reason,
                stop_reason=stop_reason or None,
                text=session.last_text if reason == "completed" else "",
                error=error,
                inputs=session.taken,
                # (Counted on as the run ended, above.)
                run=session.runs - 1,
                open_inputs=sorted({number for item in left for number in (*item.began, *item.numbers)}),
            ),
        )
        if session.kept_back:
            # An earlier run's, heard of while this one was being stopped:
            # queued again now that the stop is through, in the order given.
            back, session.kept_back = session.kept_back, []
            session.queue = _as_given([*back, *session.queue])
        if session.queue:
            # Arrived while the end was being announced: an idle session is started by it.
            session.run = asyncio.ensure_future(self._run(session))

    async def prompt(
        self, session_id: str, text: str, *, opening: bool = False, human: bool = True, serial: int | None = None
    ) -> None:
        # ``opening`` is for a runtime that sums the conversation up itself
        # and can leave that text as it is; dsh sums up on its own. ``human``
        # is for one that keeps something for the user's own words; dsh
        # asks the host before every call that needs asking. ``serial`` is
        # said again where the text is refused.
        del opening, human
        session = await self._session(session_id, "prompt", serial)
        if session is None:
            return
        if self._shutdowns:
            self._reject(session_id, "prompt", "the runtime is shutting down", serial)
        elif session.run is not None:
            self._reject(session_id, "prompt", "a run is in progress", serial)
        else:
            self._queue(session, text)

    async def steer(self, session_id: str, text: str, *, human: bool = True, serial: int | None = None) -> None:
        del human
        await self._add(session_id, "steer", text, serial)

    async def follow_up(self, session_id: str, text: str, *, human: bool = True, serial: int | None = None) -> None:
        del human
        await self._add(session_id, "follow_up", text, serial)

    async def _add(self, session_id: str, command: str, text: str, serial: int | None) -> None:
        session = await self._session(session_id, command, serial)
        if session is None:
            return
        if self._shutdowns:
            self._reject(session_id, command, "the runtime is shutting down", serial)
            return
        self._queue(session, text)

    async def abort(self, session_id: str) -> None:
        session = await self._session(session_id, "abort")
        if session is None:
            return
        session.queue.clear()
        if session.run is None or session.process is None:
            return
        session.aborting = True
        for task in list(session.asks):
            task.cancel()
        session.arrived.set()
        session.process.connection.notify("session/cancel", {"sessionId": session.dsh_id})

    async def close(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None and not session.opening.done():
            await asyncio.shield(session.opening)
            session = self._sessions.get(session_id)
        if session is None:
            self._reject(session_id, "close", f'unknown session "{session_id}"')
            return
        # Not waited for, as in the built-in runtime: the host calls this
        # from the one task that takes in every session's events, and dsh
        # may take its time. The end is announced when it is closed.
        self._close_session(session)

    async def fork(
        self, session_id: str, *, session_dir: str, source_session_id: str, source_dir: str, upto: str | None = None
    ) -> None:
        """Refuse: dsh keeps what was said in its sessions itself and has no command that copies one."""
        del session_dir, source_session_id, source_dir, upto
        self._reject(session_id, "fork", "a session that runs in dsh cannot be copied: dsh has no command for it")

    async def compact(self, session_id: str) -> None:
        """Refuse: dsh sums a conversation up by itself, when it sees fit, and takes no command for it."""
        self._reject(session_id, "compact", "dsh sums a conversation up by itself and takes no command for it")

    def _close_session(self, session: _Session) -> asyncio.Future[None]:
        """Start closing a session, or join the close already under way."""
        if session.closing is None:
            session.closing = asyncio.get_running_loop().create_future()
            asyncio.ensure_future(self._do_close(session, session.closing))
        return session.closing

    async def _do_close(self, session: _Session, closed: asyncio.Future[None]) -> None:
        from config.nekoclaw_settings import DSH_CLOSE_SECONDS

        let_go = True
        try:
            session.queue.clear()
            session.aborting = True
            for task in list(session.asks):
                task.cancel()
            session.arrived.set()
            process = session.process
            if process is not None and process.connection.alive and session.dsh_id:
                try:
                    # Closing stops the turn, its commands and its jobs, and
                    # writes the session's log.
                    await process.connection.request(
                        "session/close", {"sessionId": session.dsh_id}, timeout=DSH_CLOSE_SECONDS
                    )
                except (AcpError, asyncio.TimeoutError) as exc:
                    # dsh may still hold the session; see ``_forget``.
                    let_go = False
                    logger.warning("closing dsh session %s failed: %s", session.id, exc)
            run = session.run
            if run is not None:
                await asyncio.wait({run}, timeout=DSH_CLOSE_SECONDS)
                if not run.done():
                    run.cancel()
        except Exception:  # noqa: BLE001 - a failing close still removes the session
            logger.exception("closing dsh session %s failed", session.id)
        finally:
            self._forget(session, slot_free=let_go)
            self._emit(session, protocol.EndedEvent(session_id=session.id, reason="closed"))
            closed.set_result(None)

    async def shutdown(self) -> None:
        """Close every session and end every dsh process. Afterwards the runtime can be used again."""
        self._shutdowns += 1
        try:
            # Sessions on their way in are closed as soon as they exist. Those
            # that take too long are given up: none is to start, nor to start
            # a dsh process, after this returned.
            starting = list(self._starting)
            if starting:
                await asyncio.wait([session.opening for session in starting], timeout=_SHUTDOWN_OPENING_WAIT_SECONDS)
                late = [session for session in starting if not session.opening.done()]
                for session in late:
                    if session.opener is not None:
                        session.opener.cancel()
                if late:
                    await asyncio.wait([session.opening for session in late])
            async with self._process_lock:
                processes, self._processes = list(self._processes.values()), {}
            if processes:
                await asyncio.gather(*(self._retire(process) for process in processes), return_exceptions=True)
            # And those no longer listed: being ended for having been idle,
            # or told to let go of a session whose start failed.
            leaving = [*self._retiring, *self._closing_quietly]
            if leaving:
                await asyncio.gather(*leaving, return_exceptions=True)
            for session in list(self._sessions.values()):
                # One whose process never came up, or was gone before it was closed.
                await asyncio.shield(self._close_session(session))
            await self._endpoint.stop()
            await self._forwarder.aclose()
        finally:
            self._shutdowns -= 1
