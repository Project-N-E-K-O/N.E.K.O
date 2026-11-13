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

"""Starting and driving task agents: one commission, one session.

``TaskAgentManager`` is the only user of the runtime's driver interface
(``nekoclaw.runtime``). It

* starts the runtime when the first task agent is needed, never before;
* gives every commission a session of its own, whose id is the commission id;
* keeps at most ``TASK_AGENT_MAX_CONCURRENT`` task agents at work and leaves the
  rest ``queued`` until a place is free. Task agents started by other task agents
  count the same;
* turns the runtime's events into ledger rows and status changes, and into
  reports for her;
* serves the runtime's host callbacks: permissions, questions for the user,
  reports, lessons, web search and child task agents.

Status changes all go through ``Ledger.transition``. In particular a
commission that was stopped is ``cancelled`` before its session is told to
stop, so nothing the session still says can turn it into anything else.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from nekoclaw.host.capabilities import CapabilityRegistry
from nekoclaw.host.ledger import ACTIVE_STATUSES, Commission, Ledger
from nekoclaw.host.paths import HostPaths
from nekoclaw.host.permissions import CHOICES, PermissionAsk, PermissionGate
from nekoclaw.host.plugin_runs import PluginRunner
from nekoclaw.host.preferences import PreferenceStore, feedback_text
from nekoclaw.host.reports import CHANNEL_TASK_AGENT, Reporter

logger = logging.getLogger(__name__)

RUNTIME_BUILTIN = "builtin"
LEAD_NAME = "lead"
WEB_SEARCH_PLUGIN_ID = "web_search"
WEB_SEARCH_ENTRY_ID = "search"
_WEB_SEARCH_PARALLEL = 3
_SHUTDOWN_START_WAIT_SECONDS = 5.0
_SHUT_DOWN_REASON = "the host was shut down"
_QUOTA_REASON = "the free daily quota of the agent model is used up"
# A task agent started by a task agent does not wait in the queue: its parent
# keeps its own place while it waits, so with every place held by parents
# nothing would ever move.
_NO_PLACE_REASON = "no task agent place was free"
# Children that can still produce a result their parent should wait for.
_STILL_WORKING = frozenset({"queued", "working", "waiting_user"})
_REPORT_TO_STATUS = {"ready": "done", "blocked": "blocked", "failed": "failed"}
_STATUS_TO_REPORT = {"done": "completed", "blocked": "blocked", "failed": "failed", "cancelled": "cancelled"}
_REPLY_LANGUAGES = {
    "zh": "Simplified Chinese",
    "zh-CN": "Simplified Chinese",
    "zh-TW": "Traditional Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "es": "Spanish",
    "pt": "Portuguese",
}

# Returns the model endpoint for task agents as keyword arguments of
# ``nekoclaw.runtime.protocol.ModelConfig``, or None when none is configured.
ModelConfigProvider = Callable[[], Awaitable["dict[str, Any] | None"]]
# Builds a runtime from the event sink and the host callbacks.
RuntimeFactory = Callable[[Callable[[dict[str, Any]], None], Any], Any]


# Counts one model request of a task agent against whatever quota applies to
# its model. Returns False when no request is left; the commission then fails.
UsageMeter = Callable[[str], Awaitable[bool]]


class LessonSink(Protocol):
    """Receives what ``note_lesson`` files, except ``feedback``.

    The task agent space implements this; until it exists the default sink keeps
    the lessons as events of the commission.
    """

    async def submit(self, commission: Commission, lesson: dict[str, Any]) -> str | None:
        """Store one lesson; the returned text is told to the model."""


class LedgerLessonSink:
    """Keeps lessons as ``lesson`` events of the commission that filed them."""

    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger

    async def submit(self, commission: Commission, lesson: dict[str, Any]) -> str | None:
        await self._ledger.append_event(commission.id, "lesson", dict(lesson))
        return "recorded"


@dataclass(slots=True)
class _Session:
    commission_id: str
    # Text increments and tool output waiting to be written as one event row.
    text: list[str] = field(default_factory=list)
    text_size: int = 0
    updates: dict[str, list[str]] = field(default_factory=dict)
    update_names: dict[str, str] = field(default_factory=dict)
    # Set while a host callback waits for the user's answer.
    question: asyncio.Future[dict[str, Any]] | None = None
    ask_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # True after a ``waiting`` report: the answer becomes the session's next work.
    awaiting_prompt: bool = False
    # Whether the run that made that report has ended, and an answer that
    # arrived before it did.
    run_ended: bool = False
    held_answer: str | None = None
    # An answer sent as the session's next work that no event has yet shown
    # it took. Should the session close first, it is taken up again with it.
    untaken_answer: str | None = None
    # Set once the session has been given its first work. Until then, what is
    # passed on to it waits in the notes, which go to it right after that work.
    ready: bool = False
    closing: bool = False
    # Model requests already counted against the quota before they were
    # sent; that many usage reports are not counted again.
    prepaid_requests: int = 0
    # Set when the session is closed before its work was given: what was
    # passed on for that work is kept when the session is released.
    keep_notes: bool = False
    # What this start is to send that came from the user: what an earlier
    # failed start kept, the sentence it was given, what was passed on
    # while it was set up. Sending is not taking: it stays until an event
    # shows the session at work on it, and is kept for the next start when
    # this one fails before its prompt reached the session's transcript.
    carried: list[str] = field(default_factory=list)
    # Texts sent to the session (prompt and steer). A finished run that had
    # taken fewer is followed by a run that takes the rest; until that one
    # ends, the earlier one's end is held here and decides nothing.
    sent: int = 0
    held_end: dict[str, Any] | None = None


def _shows_work_taken(event: dict[str, Any], sent: int) -> bool:
    """Whether a session event shows the session took everything sent to it.

    A refusal does not, nor does the session closing. The end of a run that
    took fewer texts than were sent is an earlier run's end.
    """
    kind = event.get("type")
    if kind == "error":
        return False
    if kind == "ended":
        if event.get("reason") == "closed":
            return False
        inputs = event.get("inputs")
        return not isinstance(inputs, int) or inputs >= sent
    return True


def _shows_run_at_work(event: dict[str, Any]) -> bool:
    """Whether a session event shows a run at work on what it was given.

    ``started`` does not: it answers the start, before any work is sent.
    Nor does an error or an end other than a completed run.
    """
    kind = event.get("type")
    if kind == "ended":
        return event.get("reason") == "completed"
    return kind in ("text", "tool_start", "tool_update", "tool_end", "report", "usage", "host_request")


def default_runtime_factory(on_event: Callable[[dict[str, Any]], None], host: Any) -> Any:
    """Return the built-in runtime in a child process."""
    from nekoclaw.runtime import SubprocessRuntime

    return SubprocessRuntime(on_event, host=host)


def build_answers(questions: list[Any], text: str) -> dict[str, Any]:
    """Turn the user's reply into the answer shape ``ask_user_question`` expects.

    Every question gets the reply as its free-text answer; an option whose
    label occurs in the reply is selected as well.
    """
    lowered = text.lower()
    answers = []
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        labels = []
        for option in question.get("options") or []:
            label = option.get("label") if isinstance(option, dict) else option
            if isinstance(label, str) and label:
                labels.append(label)
        selected = [label for label in labels if label.lower() in lowered]
        if not question.get("multi_select"):
            selected = selected[:1]
        answers.append({"id": str(question.get("id", index)), "selected": selected, "custom": text})
    return {"answers": answers}


class TaskAgentManager:
    """Runs task agents for commissions. See the module docstring."""

    def __init__(
        self,
        *,
        ledger: Ledger,
        preferences: PreferenceStore,
        permissions: PermissionGate,
        runner: PluginRunner,
        registry: CapabilityRegistry,
        reporter: Reporter,
        paths: HostPaths,
        model_config: ModelConfigProvider,
        gateway: Any = None,
        runtime_factory: RuntimeFactory = default_runtime_factory,
        enabled: Callable[[], bool] | None = None,
        max_concurrent: int | None = None,
        lessons: LessonSink | None = None,
        meter: UsageMeter | None = None,
    ) -> None:
        self._meter = meter
        self._ledger = ledger
        self._preferences = preferences
        self._permissions = permissions
        self._runner = runner
        self._registry = registry
        self._reporter = reporter
        self._paths = paths
        self._model_config = model_config
        self._gateway = gateway
        self._runtime_factory = runtime_factory
        self._enabled = enabled
        self._max_concurrent = max_concurrent
        self._lessons: LessonSink = lessons or LedgerLessonSink(ledger)
        self._runtime: Any = None
        self._runtime_lock = asyncio.Lock()
        self._sessions: dict[str, _Session] = {}
        self._queue: deque[str] = deque()
        # Text passed on to a commission that has not started yet.
        self._notes: dict[str, list[str]] = {}
        # What a start given up for the quota was to send, kept for the
        # next start; it comes before what that start is told, being older.
        self._carried: dict[str, list[str]] = {}
        # What a commission taken up again is told: an addition to a blocked or
        # failed one, or the answer to one whose waiting session was closed.
        self._resume: dict[str, str] = {}
        # Commissions whose task agent is being started again right now.
        self._taking_up: set[str] = set()
        # Commissions whose closed session an answer is taking up again right
        # now; a second answer to the same question is refused meanwhile.
        self._answering_closed: set[str] = set()
        # Being queued to be taken up again, while that status is written.
        self._requeueing: set[str] = set()
        self._candidates: dict[str, tuple[str, ...]] = {}
        self._starting: set[asyncio.Task[Any]] = set()
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pump: asyncio.Task[None] | None = None
        self._closed = False
        permissions.set_asker(self.ask_permission)
        # A task agent started by another is covered by what the user allowed
        # for that one's session.
        permissions.set_parent_lookup(self._parent_of)

    # -- state ------------------------------------------------------------

    @property
    def limit(self) -> int:
        if self._max_concurrent is not None:
            return self._max_concurrent
        from config.nekoclaw_settings import TASK_AGENT_MAX_CONCURRENT

        return TASK_AGENT_MAX_CONCURRENT

    @property
    def running(self) -> list[str]:
        """Return the commissions that hold a session right now."""
        return list(self._sessions)

    @property
    def queued(self) -> list[str]:
        return list(self._queue)

    @property
    def runtime_started(self) -> bool:
        return self._runtime is not None

    async def available(self) -> tuple[bool, str]:
        """Return whether a task agent could be started, and if not, why."""
        if self._closed:
            return False, "disabled"
        if self._enabled is not None:
            try:
                if not self._enabled():
                    return False, "not_enabled"
            except Exception:  # noqa: BLE001 - an unreadable switch counts as off
                return False, "not_enabled"
        try:
            config = await self._model_config()
        except Exception as exc:  # noqa: BLE001 - no usable configuration
            logger.warning("the task agent model configuration cannot be read: %s", type(exc).__name__)
            return False, "not_enabled"
        if not config or not config.get("model"):
            return False, "not_enabled"
        return True, ""

    # -- runtime ----------------------------------------------------------

    def _callbacks(self) -> Any:
        from nekoclaw.runtime import HostCallbacks

        return HostCallbacks(
            decide_permission=self._cb_permission,
            ask_user=self._cb_ask_user,
            report=self._cb_report,
            note_lesson=self._cb_note_lesson,
            web_search=self._cb_web_search,
            subagent_start=self._cb_subagent_start,
            subagent_list=self._cb_subagent_list,
            subagent_send=self._cb_subagent_send,
            subagent_interrupt=self._cb_subagent_interrupt,
        )

    async def _ensure_runtime(self) -> Any:
        async with self._runtime_lock:
            if self._runtime is None:
                self._runtime = self._runtime_factory(self._events.put_nowait, self._callbacks())
                self._pump = asyncio.ensure_future(self._pump_events())
            return self._runtime

    async def _pump_events(self) -> None:
        """Handle runtime events one at a time, in the order they arrived."""
        while True:
            event = await self._events.get()
            try:
                await self._handle(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad event must not stop the rest
                logger.exception("handling runtime event %s failed", event.get("type"))
            finally:
                self._events.task_done()

    async def settle(self) -> None:
        """Wait until every event received so far has been handled."""
        await self._events.join()

    # -- starting ---------------------------------------------------------

    async def submit(self, commission: Commission, *, candidate_plugin_ids: Any = (), wait: bool = True) -> bool:
        """Hand a commission to a task agent: now if a place is free, else when one is.

        With ``wait`` False a commission that finds no free place is failed
        instead of queued. Returns whether it was started or queued.
        """
        self._candidates[commission.id] = tuple(str(item) for item in candidate_plugin_ids)
        if commission.route != "task_agent":
            await self._ledger.update(commission.id, route="task_agent")
        if self._closed:
            self._candidates.pop(commission.id, None)
            await self._ledger.transition(commission.id, "failed", reason=_SHUT_DOWN_REASON)
            return False
        if not self._take_place(commission.id):
            if not wait:
                self._candidates.pop(commission.id, None)
                await self._ledger.transition(commission.id, "failed", reason=_NO_PLACE_REASON)
                return False
            self._queue.append(commission.id)
            await self._ledger.append_event(commission.id, "queued", {"ahead": len(self._queue) - 1})
            return True
        # Started as a task of its own: whoever handed the commission over may
        # be cancelled (the host shutting down), and a session half set up
        # would then belong to nobody.
        await asyncio.shield(self._spawn(self._launch(commission.id)))
        return True

    def _spawn(self, work: Awaitable[Any]) -> asyncio.Task[Any]:
        """Run starting work as a task that shutting down waits for."""
        task = asyncio.ensure_future(work)
        self._starting.add(task)
        task.add_done_callback(self._starting.discard)
        return task

    def _take_place(self, commission_id: str) -> bool:
        """Take a place for a commission, or return False when none is free.

        Checking and taking happen with no await in between, so callers that
        run at the same time cannot together go over the limit. Nothing gets a
        place once the manager is closed.
        """
        if self._closed or commission_id in self._sessions or len(self._sessions) >= self.limit:
            return False
        self._sessions[commission_id] = _Session(commission_id)
        return True

    async def _brief(self, commission: Commission, *, with_gateway: bool) -> str:
        from config.nekoclaw_settings import TASK_AGENT_BRIEF_MAX_TOKENS
        from config.prompts import prompts_nekoclaw_brain as texts
        from utils.tokenize import truncate_to_tokens

        if commission.parent_id is not None:
            parts = [texts.TASK_AGENT_CHILD_BRIEF_TEMPLATE.format(description=commission.title)]
        else:
            parts = [
                texts.TASK_AGENT_BRIEF_TEMPLATE.format(
                    lanlan_name=commission.lanlan_name,
                    dispatch_text=commission.dispatch_text,
                    user_text=commission.user_text or commission.dispatch_text,
                )
            ]
        preferences = await self._preferences.render(f"{commission.dispatch_text}\n{commission.user_text}")
        if preferences:
            parts.append(f"{texts.TASK_AGENT_BRIEF_PREFERENCES_HEADER}\n{preferences}")
        if with_gateway:
            parts.append(texts.TASK_AGENT_BRIEF_GATEWAY_NOTE)
        return truncate_to_tokens("\n\n".join(parts), TASK_AGENT_BRIEF_MAX_TOKENS)

    async def _launch(self, commission_id: str, *, prompt: str | None = None) -> bool:
        """Start a commission whose place is taken; a place it leaves unused goes to the queue."""
        started = await self._start(commission_id, prompt=prompt)
        if not started:
            await self._start_next()
        return started

    async def _start(self, commission_id: str, *, prompt: str | None = None) -> bool:
        """Open the session of a commission and give it its work. Returns whether it started.

        The caller has taken the commission's place with ``_take_place``. When
        this returns False the place is given back, at once or, for a session
        that has to be closed again, once it is closed.
        """
        session = self._sessions.get(commission_id)
        if session is None:
            return False
        # The sentence this start was given is the user's from the outset:
        # a failure while it is set up keeps it too.
        session.carried = [prompt] if prompt is not None else []
        carried: list[str] = []
        try:
            commission = await self._ledger.get(commission_id)
            if commission is None or commission.status not in ("queued", "blocked", "failed", "waiting_user"):
                self._discard(commission_id)
                return False
            config = await self._model_config()
            if not config or not config.get("model"):
                raise RuntimeError("no model is configured for task agents")
            from nekoclaw.runtime import TaskAgentSpec, ModelConfig
            from nekoclaw.runtime.protocol import GatewaySpec

            session_dir = self._paths.session_dir(commission.id, commission.project)
            workdir_text = commission.workdir

            def prepare() -> tuple[str, str]:
                session_dir.mkdir(parents=True, exist_ok=True)
                from pathlib import Path

                workdir = Path(workdir_text) if workdir_text else self._paths.work_dir(commission.id, commission.project)
                workdir.mkdir(parents=True, exist_ok=True)
                return str(session_dir), str(workdir)

            session_path, workdir = await asyncio.to_thread(prepare)

            gateway_spec = None
            if self._gateway is not None and await self._registry.callable_by_task_agents():
                url = await self._gateway.start()
                token = self._gateway.issue(commission.id, candidate_plugin_ids=self._candidates.get(commission.id, ()))
                gateway_spec = GatewaySpec(url=url, token=token)

            spec = TaskAgentSpec(
                session_id=commission.id,
                workdir=workdir,
                session_dir=session_path,
                model=ModelConfig(**config),
                instructions=await self._brief(commission, with_gateway=gateway_spec is not None),
                reply_language=_REPLY_LANGUAGES.get(commission.lang or "", None),
                top_level=commission.parent_id is None,
                # A task agent started by a task agent starts none itself.
                disabled_tools=["subagent"] if commission.parent_id is not None else [],
                gateway=gateway_spec,
            )
            runtime = await self._ensure_runtime()
            moved = await self._ledger.transition(
                commission.id,
                "working",
                reason="a task agent took it up",
                runtime=RUNTIME_BUILTIN,
                route="task_agent",
                workdir=workdir,
            )
            if not moved.applied:
                # Stopped while it was being prepared.
                self._discard(commission.id)
                return False
            notes = self._notes.pop(commission.id, [])
            carried = self._carried.pop(commission.id, [])
            session.carried = [*carried, *([prompt] if prompt is not None else []), *notes]
            if prompt is not None:
                parts = [*carried, prompt]
            else:
                parts = [commission.dispatch_text, *carried]
            text = "\n\n".join([*parts, *notes])
            await runtime.start(spec)

            async def stopped_meanwhile() -> bool:
                current = await self._ledger.get(commission.id)
                return self._sessions.get(commission.id) is not session or current is None or current.status != "working"

            # Starting can take a while (the runtime process coming up). A stop
            # meanwhile found no session to close; this one must get no work.
            # The first request is paid for after that and before the prompt
            # that makes it: with the quota used up, a task agent sends
            # nothing at all, and a start that fails or is stopped costs
            # nothing. (Later requests are counted as their usage is reported.)
            # Counting takes a moment too, so a stop is looked for again; a
            # stop that lands then has paid for a request it does not make.
            if await stopped_meanwhile():
                session.closing = True
                await runtime.close(commission.id)
                return False
            if not await self._pay_first_request(session):
                # What the user added before and while it was being set up
                # stays for when it is taken up again, as it did when this
                # failed before anything was started. The session keeps its
                # place until its own close comes back, as every other close
                # does: a new session of the same commission started before
                # that would be released by it.
                self._keep_unsent(session)
                await self._fail(commission.id, _QUOTA_REASON)
                await self._close_session(session)
                # What was passed on while the failure was being written.
                self._keep_unsent(session)
                return False
            if await stopped_meanwhile():
                # The stop has closed the session already.
                await self._close_session(session)
                return False
            await self._send_text(runtime, session, "prompt", text)
            session.ready = True
            # What was passed on while the session was being set up. Like the
            # prompt, it is the user's until the run is seen at work.
            for note in self._notes.pop(commission.id, []):
                session.carried.append(note)
                await self._send_text(runtime, session, "steer", note)
            return True
        except asyncio.CancelledError:
            self._discard(commission_id)
            if not self._closed:
                # Whoever started it was cancelled; the queue still gets the place.
                self._spawn(self._start_next())
            else:
                # Shutting down gave up on this start, possibly after it was
                # recorded as working: with its session gone, the commission
                # ends here rather than be left working for nobody.
                await asyncio.shield(
                    self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)
                )
            raise
        except Exception as exc:  # noqa: BLE001 - the commission fails with the reason
            logger.warning("starting a task agent for %s failed: %s", commission_id, exc)
            # Kept before the session is let go: the user's next words may
            # take the commission up again while the failure is written.
            self._keep_unsent(session)
            self._discard(commission_id)
            await self._fail(commission_id, f"the task agent could not be started: {exc}")
            self._keep_unsent(session)
            return False

    async def _pay_first_request(self, session: _Session) -> bool:
        """Count a session's first model request before it is made; False when the quota is used up.

        A meter that cannot count does not stop the work.
        """
        if self._meter is None:
            return True
        try:
            allowed = await self._meter(session.commission_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a meter that cannot count does not stop the work
            logger.warning("counting a task agent's model request failed", exc_info=True)
            return True
        if allowed:
            session.prepaid_requests += 1
        return bool(allowed)

    @staticmethod
    async def _send_text(runtime: Any, session: _Session, command: str, text: str) -> None:
        """Send ``prompt`` or ``steer`` text to a session, counting it.

        Counted before it is sent: the end of a run that has not taken it may
        be handled while the command is on its way.
        """
        session.sent += 1
        # A steer to an idle session starts a new run. An answer arriving
        # meanwhile must then wait for that run to end (``held_answer``)
        # rather than be sent as a prompt the busy session refuses.
        woke = command == "steer" and session.run_ended
        if woke:
            session.run_ended = False
        try:
            await getattr(runtime, command)(session.commission_id, text)
        except BaseException:
            session.sent -= 1
            if woke:
                session.run_ended = True
            raise

    def _keep_unsent(self, session: _Session) -> None:
        """Keep what the user said that a failed start did not get into the session, for the next start.

        What this start was to send, then what was passed on since, in the
        order it came: the next start sends it ahead of what it is given.
        Called once the failure is written, so what came meanwhile is kept too.
        A session closed by a stop keeps nothing: its commission is over.
        """
        commission_id = session.commission_id
        if session.closing and not session.keep_notes:
            session.carried = []
            return
        session.keep_notes = True
        kept = [*session.carried, *self._notes.pop(commission_id, [])]
        session.carried = []
        if kept:
            self._carried[commission_id] = [*self._carried.get(commission_id, []), *kept]

    def _discard(self, commission_id: str) -> None:
        self._sessions.pop(commission_id, None)
        self._candidates.pop(commission_id, None)
        if self._gateway is not None:
            self._gateway.revoke(commission_id)

    async def _release(self, commission_id: str) -> None:
        """Forget a session that is gone and give its place to the next in the queue.

        Called while a runtime event is handled. The next session is started
        as a task of its own, which shutting down waits for: waiting here for
        it to come up would hold up every other event meanwhile.
        """
        if commission_id in self._queue or commission_id in self._requeueing:
            # Queued to be taken up again: what was said meanwhile goes along
            # with it, and so do its candidate plugins.
            self._sessions.pop(commission_id, None)
            if self._gateway is not None:
                self._gateway.revoke(commission_id)
        else:
            session = self._sessions.get(commission_id)
            self._discard(commission_id)
            if session is None or not session.keep_notes:
                self._notes.pop(commission_id, None)
        if self._queue and not self._closed:
            self._spawn(self._start_next())

    async def _start_next(self) -> None:
        """Give free places to queued commissions, in the order they were queued."""
        while not self._closed and len(self._sessions) < self.limit:
            # One whose old session is still on its way out keeps its turn;
            # it is started when that session has gone.
            commission_id = next((item for item in self._queue if item not in self._sessions), None)
            if commission_id is None:
                return
            self._queue.remove(commission_id)
            if self._take_place(commission_id):
                await self._start(commission_id, prompt=self._resume.pop(commission_id, None))

    # -- events -----------------------------------------------------------

    async def _flush(self, session: _Session) -> None:
        """Write buffered text and tool output as event rows."""
        from config.nekoclaw_settings import TASK_AGENT_EVENT_TEXT_MAX_CHARS

        rows: list[tuple[str, dict[str, Any]]] = []
        if session.text:
            text = "".join(session.text)
            session.text.clear()
            session.text_size = 0
            for start in range(0, len(text), TASK_AGENT_EVENT_TEXT_MAX_CHARS):
                rows.append(("text", {"text": text[start : start + TASK_AGENT_EVENT_TEXT_MAX_CHARS]}))
        for call_id, parts in session.updates.items():
            text = "".join(parts)
            # Of long output the end is what shows where the tool stands.
            rows.append(
                (
                    "tool_update",
                    {
                        "tool_call_id": call_id,
                        "tool_name": session.update_names.get(call_id, ""),
                        "text": text[-TASK_AGENT_EVENT_TEXT_MAX_CHARS:],
                    },
                )
            )
        session.updates.clear()
        session.update_names.clear()
        if rows:
            await self._ledger.append_events(session.commission_id, rows)

    @staticmethod
    def _compact(event: dict[str, Any]) -> dict[str, Any]:
        """Return the event without ids the row already has, long strings cut."""
        from config.nekoclaw_settings import TASK_AGENT_EVENT_TEXT_MAX_CHARS

        compact: dict[str, Any] = {}
        for key, value in event.items():
            if key in ("type", "session_id"):
                continue
            if isinstance(value, (dict, list)):
                dumped = json.dumps(value, ensure_ascii=False, default=str)
                if len(dumped) > TASK_AGENT_EVENT_TEXT_MAX_CHARS:
                    value = dumped[:TASK_AGENT_EVENT_TEXT_MAX_CHARS] + "…"
            elif isinstance(value, str) and len(value) > TASK_AGENT_EVENT_TEXT_MAX_CHARS:
                value = value[:TASK_AGENT_EVENT_TEXT_MAX_CHARS] + "…"
            compact[key] = value
        return compact

    async def _handle(self, event: dict[str, Any]) -> None:
        from config.nekoclaw_settings import TASK_AGENT_EVENT_TEXT_MAX_CHARS

        kind = event.get("type")
        session = self._sessions.get(str(event.get("session_id") or ""))
        if session is None:
            # Process-level messages, and events of sessions already released.
            return
        if session.untaken_answer is not None and _shows_work_taken(event, session.sent):
            session.untaken_answer = None
        if session.carried and session.sent > 0 and _shows_run_at_work(event):
            session.carried = []
        if kind == "text":
            delta = str(event.get("delta") or "")
            session.text.append(delta)
            session.text_size += len(delta)
            if session.text_size >= TASK_AGENT_EVENT_TEXT_MAX_CHARS:
                await self._flush(session)
            return
        if kind == "tool_update":
            call_id = str(event.get("tool_call_id") or "")
            session.updates.setdefault(call_id, []).append(str(event.get("text") or ""))
            session.update_names[call_id] = str(event.get("tool_name") or "")
            return
        await self._flush(session)
        await self._ledger.append_event(session.commission_id, str(kind), self._compact(event))
        if kind == "report":
            await self._on_report(session, event)
        elif kind == "ended":
            await self._on_ended(session, event)
        elif kind == "error":
            await self._on_error(session, event)
        elif kind == "usage":
            await self._on_usage(session)

    def _summary(self, text: str) -> str:
        from config.nekoclaw_settings import TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        return truncate_to_tokens(text, TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS)

    async def _on_report(self, session: _Session, event: dict[str, Any]) -> None:
        status = str(event.get("status") or "")
        summary = str(event.get("summary") or "")
        details = str(event.get("details") or "")
        if status == "progress":
            return
        commission_id = session.commission_id
        if status == "waiting":
            question = {"kind": "report", "question": summary, "details": details}
            moved = await self._ledger.transition(
                commission_id,
                "waiting_user",
                reason="the task agent needs an answer",
                pending_question=json.dumps(question, ensure_ascii=False),
            )
            if moved.applied and moved.commission is not None:
                session.awaiting_prompt = True
                session.run_ended = False
                session.held_answer = None
                await self._reporter.question(moved.commission, question)
            return
        target = _REPORT_TO_STATUS.get(status)
        if target is None:
            return
        text = f"{summary}\n{details}" if details else summary
        await self._end(
            commission_id,
            target,
            reason=f"the task agent reported {status}",
            text=text,
            suggested_delivery=event.get("delivery"),
            result_summary=self._summary(text),
        )

    async def _on_ended(self, session: _Session, event: dict[str, Any]) -> None:
        from nekoclaw.runtime.protocol import PROCESS_EXITED_ERROR

        commission_id = session.commission_id
        reason = event.get("reason")
        if session.closing and reason == "error" and event.get("error") == PROCESS_EXITED_ERROR:
            # A close was asked for, and the runtime process went away
            # before answering it: the close will not come back. It ends
            # as that close would have, an answer it held included; the
            # commission's status is the close's business, not an error's.
            # Any other error while closing is followed by the close's own end.
            reason = "closed"
        if reason == "closed":
            # An answer kept for a run that never came is not lost with the
            # session: it takes the commission up again from its transcript.
            kept, session.held_answer = session.held_answer, None
            # An answer already sent but not taken: the refusal that would
            # fail the commission now finds no session and is ignored, so
            # without this it would stay working for nobody.
            untaken, session.untaken_answer = session.untaken_answer, None
            claimed = (kept is not None or untaken is not None) and not self._closed
            if claimed:
                # Claimed for that answer before anything is awaited, so an
                # answer arriving before it is handed on is refused rather
                # than taking its place.
                self._answering_closed.add(commission_id)
            await self._release(commission_id)
            if not claimed:
                return
            if kept is not None:
                self._spawn(self._answer_closed(commission_id, kept, claimed=True))
            else:
                self._spawn(self._answer_untaken(commission_id, str(untaken)))
            return
        commission = await self._ledger.get(commission_id)
        if commission is None:
            return
        if reason == "completed":
            inputs = event.get("inputs")
            if isinstance(inputs, int) and inputs < session.sent:
                # Text sent after this run had finished (a result from a
                # task agent it started, or something the user said) started
                # another run. That run's end decides; this one's is kept in
                # case the text is refused and no such run comes.
                session.held_end = event
                return
            session.held_end = None
            if commission.status == "waiting_user" and session.awaiting_prompt:
                # It asked for an answer and stopped; the session stays for the answer.
                session.run_ended = True
                if session.held_answer is not None:
                    await self._deliver_answer(session, session.held_answer)
                return
            if commission.status == "working":
                # A blocked child has reported already and its session is
                # gone; it produces nothing more to wait for.
                if await self._ledger.select(parent_id=commission_id, statuses=_STILL_WORKING):
                    # Task agents it started are still at work. Their results
                    # arrive as messages and start its next run; it is not done.
                    return
                # The run ended without a closing report: its last words are the result.
                text = str(event.get("text") or "")
                await self._end(
                    commission_id, "done", reason="the run completed", text=text, result_summary=self._summary(text)
                )
        elif reason == "aborted":
            await self._end(commission_id, "cancelled", reason="the run was stopped", text="")
        elif reason == "error":
            if session.sent == 0:
                # Ended before its prompt was sent (the runtime process
                # went away meanwhile): what it was to send is kept, and
                # again once the failure is written, for what came meanwhile.
                self._keep_unsent(session)
                await self._fail(commission_id, str(event.get("error") or "the run failed"))
                self._keep_unsent(session)
            else:
                # A run takes its prompt into the session's transcript before
                # its first model request, and the next start goes on from
                # that transcript: what was kept is there, and is not sent again.
                session.carried = []
                await self._fail(commission_id, str(event.get("error") or "the run failed"))
        # It is over; what it started must not go on working for nobody.
        for child in await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
            await self._ledger.transition(child.id, "cancelled", reason="its parent finished")
            await self.abort(child.id)
        await self._close_session(session)

    async def _on_usage(self, session: _Session) -> None:
        """Count one model request; a commission whose quota is used up fails.

        The runtime reports usage once per request, after the request, so the
        request that uses the last unit still completes.
        """
        if self._meter is None or session.closing:
            return
        if session.prepaid_requests > 0:
            # Counted before it was sent.
            session.prepaid_requests -= 1
            return
        commission_id = session.commission_id
        try:
            allowed = await self._meter(commission_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a meter that cannot count does not stop the work
            logger.warning("counting a task agent's model request failed", exc_info=True)
            return
        if allowed:
            return
        await self._fail(commission_id, _QUOTA_REASON)
        for child in await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
            await self._ledger.transition(child.id, "cancelled", reason="its parent finished")
            await self.abort(child.id)
        await self._close_session(session)

    async def _on_error(self, session: _Session, event: dict[str, Any]) -> None:
        command = event.get("command")
        message = str(event.get("message") or "the runtime rejected the command")
        commission_id = session.commission_id
        if command in ("prompt", "steer", "follow_up") and session.sent > 0:
            # Refused, so the session never took it.
            session.sent -= 1
        if command == "steer":
            held = session.held_end
            if held is not None and isinstance(held.get("inputs"), int) and held["inputs"] >= session.sent:
                # The run the refused text was to start never came: the
                # earlier run's end stands after all.
                session.held_end = None
                await self._on_ended(session, held)
            elif held is None and session.awaiting_prompt and not session.run_ended:
                # The steer that was to wake a session waiting for an answer
                # was refused, so no run started: it is idle again, and an
                # answer kept for the end of that run is handed over now.
                session.run_ended = True
                if session.held_answer is not None:
                    answer, session.held_answer = session.held_answer, None
                    await self._deliver_answer(session, answer)
            return
        if command == "start":
            # The runtime reports a start it could not make here, not by
            # raising from ``start``.
            await self._fail(commission_id, f"the task agent could not be started: {message}")
            self._keep_unsent(session)
            await self._release(commission_id)
        elif command == "prompt":
            session.untaken_answer = None
            # Refused, so it is not in the transcript: what the user said
            # with it is kept, as when the start itself fails.
            await self._fail(commission_id, message)
            self._keep_unsent(session)
            await self._close_session(session)
        elif command == "close":
            # The session is gone already, for instance with its runtime process.
            await self._release(commission_id)

    async def _close_session(self, session: _Session) -> None:
        if session.closing or self._runtime is None:
            return
        session.closing = True
        await self._runtime.close(session.commission_id)

    async def _fail(self, commission_id: str, reason: str) -> None:
        await self._end(commission_id, "failed", reason=reason, text=reason, result_summary=self._summary(reason))

    async def _end(
        self,
        commission_id: str,
        target: str,
        *,
        reason: str,
        text: str,
        suggested_delivery: Any = None,
        **values: Any,
    ) -> bool:
        """Move a commission to a final status and tell whoever commissioned it.

        A report to her is stored in the same transaction as the status
        change, so a crash in between cannot leave a finished commission
        that is never reported. A parent task agent is told after the change.
        Returns whether the change was made.
        """
        commission = await self._ledger.get(commission_id)
        if commission is None:
            return False
        report = None
        if commission.parent_id is None and target != "cancelled":
            # Whoever stopped it knows; the task brain reports a stop when one is due.
            # The commission's own setting decides; the task agent's wish is the fallback.
            delivery = commission.delivery or (
                suggested_delivery if suggested_delivery in ("proactive", "passive", "silent") else "proactive"
            )
            report = self._reporter.task_result_report(
                commission,
                channel=CHANNEL_TASK_AGENT,
                success=target == "done",
                summary=text,
                detail=text,
                error_message=text if target == "failed" else "",
                status=_STATUS_TO_REPORT.get(target, target),
                source_kind="task_agent",
                source_name=commission.title,
                delivery_mode=delivery,
            )
        moved = await self._reporter.transition(commission_id, target, reason=reason, report=report, **values)
        if not moved.applied or moved.commission is None:
            return False
        if moved.commission.parent_id is not None:
            await self._tell_parent(moved.commission, _STATUS_TO_REPORT.get(target, target), text)
        return True

    async def _tell_parent(self, commission: Commission, status: str, text: str) -> None:
        from config.nekoclaw_settings import TASK_AGENT_CHILD_RESULT_MAX_TOKENS
        from config.prompts import prompts_nekoclaw_brain as texts
        from utils.tokenize import truncate_to_tokens

        parent_id = str(commission.parent_id)
        message = texts.TASK_AGENT_CHILD_RESULT_TEMPLATE.format(
            name=commission.name or commission.id,
            status=status,
            text=truncate_to_tokens(text, TASK_AGENT_CHILD_RESULT_MAX_TOKENS),
        )
        parent = self._sessions.get(parent_id)
        if parent is None:
            await self._wake_closed_parent(parent_id, message)
            return
        if not parent.ready:
            # Its session is being started (taken up again by an earlier
            # result, say): this one follows its first work.
            self._notes.setdefault(parent_id, []).append(message)
            return
        if self._runtime is None:
            return
        # Before the parent's next model request, so a parent that waits for
        # its task agents sees the result as soon as it looks; an idle parent is
        # started by it.
        await self._send_text(self._runtime, parent, "steer", message)

    async def _wake_closed_parent(self, parent_id: str, message: str) -> None:
        """Take up again a working parent whose idle session was closed, a child's result its next work.

        A parent that ended its run while its task agents worked is idle, and an
        idle runtime closes idle sessions after a while. The result then
        queues it to be started again where its transcript ends.
        """
        if self._closed:
            return
        if parent_id in self._queue or parent_id in self._requeueing or parent_id in self._taking_up:
            # Already on its way back: this result goes along with it.
            self._notes.setdefault(parent_id, []).append(message)
            return
        parent = await self._ledger.get(parent_id)
        if parent is None or parent.route != "task_agent" or parent.status != "working":
            return
        session = self._sessions.get(parent_id)
        if session is not None and session.ready and self._runtime is not None:
            # It came back, and was given its work, while the ledger was read.
            await self._send_text(self._runtime, session, "steer", message)
            return
        if session is not None or parent_id in self._queue or parent_id in self._requeueing:
            # It is on its way back: this goes along with its work.
            self._notes.setdefault(parent_id, []).append(message)
            return
        self._requeueing.add(parent_id)
        try:
            moved = await self._ledger.transition(
                parent_id, "queued", reason="its idle session was closed; a result takes it up again"
            )
        finally:
            self._requeueing.discard(parent_id)
        if not moved.applied:
            return
        if self._closed:
            await self._ledger.transition(parent_id, "failed", reason=_SHUT_DOWN_REASON)
            return
        self._resume[parent_id] = message
        if parent_id not in self._queue:
            self._queue.append(parent_id)
        if len(self._sessions) < self.limit:
            self._spawn(self._start_next())

    # -- driving ----------------------------------------------------------

    async def abort(self, commission_id: str) -> None:
        """Stop a commission's task agent, and the task agents it started.

        The caller has set the commission ``cancelled`` already.
        """
        for child in await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
            await self._ledger.transition(child.id, "cancelled", reason="its parent was stopped")
            await self.abort(child.id)
        if commission_id in self._queue:
            self._queue.remove(commission_id)
        self._notes.pop(commission_id, None)
        self._carried.pop(commission_id, None)
        self._resume.pop(commission_id, None)
        session = self._sessions.get(commission_id)
        if session is None:
            return
        if session.question is not None and not session.question.done():
            session.question.cancel()
        # Closing stops the run, kills its background jobs and frees the place.
        await self._close_session(session)

    async def tell(self, commission_id: str, text: str) -> bool:
        """Pass a sentence on to a commission's task agent. Returns whether it was delivered."""
        if self._closed:
            return False
        commission = await self._ledger.get(commission_id)
        if commission is None or commission.route != "task_agent" or self._closed:
            return False
        session = self._sessions.get(commission_id)
        if session is not None and self._runtime is not None and session.awaiting_prompt:
            return await self.answer(commission_id, text)
        if session is None and commission.status == "waiting_user":
            # Its session was closed while it waited (an idle runtime exits).
            return await self.answer(commission_id, text)
        if commission.status in ("blocked", "failed"):
            # Take it up again where its transcript ends.
            await self._ledger.append_event(commission_id, "told", {"text": text, "resumed": True})
            return await self._take_up_again(commission_id, text)
        if commission.status == "queued" or (session is not None and not session.ready and not session.closing):
            # Not started yet, or its session not yet given its work: it goes
            # along with the work when that is given.
            self._notes.setdefault(commission_id, []).append(text)
            await self._ledger.append_event(commission_id, "told", {"text": text, "queued": True})
            return True
        if session is not None and self._runtime is not None:
            if session.closing or commission.status not in ("working", "waiting_user"):
                return False
            await self._ledger.append_event(commission_id, "told", {"text": text})
            await self._send_text(self._runtime, session, "steer", text)
            return True
        return False

    async def _take_up_again(self, commission_id: str, text: str) -> bool:
        """Start a commission's task agent again where its transcript ends, ``text`` its next work.

        Its old session is gone or on its way out. Without a free place it is
        queued, which a stop can still end. Returns whether it was started or
        queued.
        """
        if self._closed:
            return False
        if commission_id in self._taking_up or commission_id in self._requeueing:
            # An earlier sentence is taking it up again or queueing it right
            # now: this one goes along with that rather than starting or
            # queueing a second time, which would then be refused.
            self._notes.setdefault(commission_id, []).append(text)
            return True
        if commission_id not in self._sessions and self._take_place(commission_id):
            # As in ``submit``: a task of its own that shutting down waits for,
            # so a caller cancelled meanwhile does not leave a half set-up
            # session behind a commission that says it is working.
            self._taking_up.add(commission_id)
            launch = self._spawn(self._launch(commission_id, prompt=text))
            launch.add_done_callback(lambda _done: self._taking_up.discard(commission_id))
            return await asyncio.shield(launch)
        # Its old session is still on its way out, or no place is free: it is
        # queued. That runs as a task shutting down waits for, so a shutdown
        # while the status is written still finds the commission afterwards.
        # Marked at once, before anything is awaited, so a sentence arriving
        # meanwhile joins this one.
        self._requeueing.add(commission_id)
        return await asyncio.shield(self._spawn(self._requeue(commission_id, text)))

    async def _requeue(self, commission_id: str, text: str) -> bool:
        # Marked (by the caller) while the status is written: the old
        # session's end meanwhile then keeps the candidate plugins and notes
        # for the retry.
        self._requeueing.add(commission_id)
        try:
            moved = await self._ledger.transition(commission_id, "queued", reason="taken up again; waiting for a place")
        finally:
            self._requeueing.discard(commission_id)
        if moved.applied and self._closed:
            # Shutting down emptied the queue while the status was written.
            await self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)
        if not moved.applied or self._closed:
            if commission_id not in self._sessions:
                self._candidates.pop(commission_id, None)
                self._notes.pop(commission_id, None)
            return False
        self._resume[commission_id] = text
        if commission_id not in self._queue:
            self._queue.append(commission_id)
        # A place may have come free while the status was written; the queue
        # was empty then, so nothing took it. Move it on now.
        if len(self._sessions) < self.limit:
            self._spawn(self._start_next())
        return True

    async def answer(self, commission_id: str, text: str, choice: str | None = None) -> bool:
        """Give the user's answer to a commission that waits for it."""
        session = self._sessions.get(commission_id)
        if session is None:
            return await self._answer_closed(commission_id, text)
        if session.question is not None and not session.question.done():
            session.question.set_result({"text": text, "choice": choice})
            return True
        if session.awaiting_prompt and self._runtime is not None:
            if not session.run_ended:
                # The run that asked is still winding down. The answer is kept
                # and handed over the moment it has ended. A question takes
                # one answer: a later one does not replace the kept one.
                if session.held_answer is not None or session.untaken_answer is not None:
                    return False
                session.held_answer = text
                return True
            return await self._deliver_answer(session, text)
        return False

    async def _answer_closed(self, commission_id: str, text: str, *, claimed: bool = False) -> bool:
        """Answer a ``waiting`` report whose session was closed meanwhile.

        A runtime with nothing to do exits after a while and closes its idle
        sessions, those waiting for an answer included. The answer then takes
        the commission up again where its transcript ends. ``claimed`` is
        True for an answer its session kept, claimed for it when the session
        closed.
        """
        if not claimed:
            if self._closed or commission_id in self._answering_closed:
                # Another answer is taking it up again: that one is its next work.
                return False
            self._answering_closed.add(commission_id)
        try:
            if self._closed:
                return False
            commission = await self._ledger.get(commission_id)
            if commission is None or commission.route != "task_agent" or commission.status != "waiting_user":
                return False
            question = commission.question() or {}
            # A question asked through a tool call is answered to the session
            # that waits for it; only the question of a report can be answered
            # this way.
            if question.get("kind") != "report":
                return False
            await self._ledger.append_event(commission_id, "answered", {"text": text, "resumed": True})
            return await self._take_up_again(commission_id, text)
        finally:
            self._answering_closed.discard(commission_id)

    async def _deliver_answer(self, session: _Session, text: str) -> bool:
        """Give an idle session the answer to its ``waiting`` report as its next work."""
        commission_id = session.commission_id
        if session.untaken_answer is not None:
            # Another answer is on its way to it: that one is its next work.
            return False
        # Marked before anything is awaited: a session that closes meanwhile
        # leaves the answer to its end, which takes the commission up again.
        session.untaken_answer = text
        moved = await self._ledger.transition(commission_id, "working", reason="the user answered")
        if not moved.applied:
            if session.untaken_answer == text:
                session.untaken_answer = None
            return False
        session.awaiting_prompt = False
        session.held_answer = None
        await self._ledger.append_event(commission_id, "answered", {"text": text})
        if self._sessions.get(commission_id) is not session or session.untaken_answer != text:
            # Closed while the status was written: its end has the answer.
            return True
        try:
            await self._send_text(self._runtime, session, "prompt", text)
        except BaseException:
            session.untaken_answer = None
            raise
        return True

    async def _answer_untaken(self, commission_id: str, text: str) -> None:
        """Take a commission up again with an answer its session closed before taking.

        The answer moved it to ``working``; it goes back to waiting for that
        answer, which then takes it up again as an answer to a closed session does.
        The commission was claimed for this answer when its session closed.
        """
        try:
            moved = await self._ledger.transition(
                commission_id, "waiting_user", reason="its session closed before it took the answer"
            )
            if moved.applied and not await self._take_up_again(commission_id, text) and self._closed:
                # Shutting down began while the status was written: nothing will
                # take the answer up, and neither the queue nor the sessions hold
                # it for shutting down to end. It ends here, as they do.
                await self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)
        finally:
            self._answering_closed.discard(commission_id)

    async def _ask(self, commission_id: str, question: dict[str, Any]) -> dict[str, Any]:
        """Put a question to the user and wait for the answer.

        The commission is ``waiting_user`` meanwhile and ``working`` again
        afterwards.
        """
        session = self._sessions.get(commission_id)
        if session is None:
            raise RuntimeError("the commission has no session to ask for")
        async with session.ask_lock:
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            moved = await self._ledger.transition(
                commission_id,
                "waiting_user",
                reason="waiting for the user",
                pending_question=json.dumps(question, ensure_ascii=False),
            )
            if not moved.applied or moved.commission is None:
                raise RuntimeError(f"the commission cannot wait for the user ({moved.refusal})")
            session.question = future
            try:
                await self._reporter.question(moved.commission, question)
                return await future
            finally:
                session.question = None
                # Back to working only from waiting: a commission stopped, or
                # failed (by shutting down, say) meanwhile, stays as it is.
                await asyncio.shield(
                    self._ledger.transition(
                        commission_id, "working", reason="the user answered", only_from=("waiting_user",)
                    )
                )

    async def ask_user(self, commission_id: str, question: dict[str, Any]) -> dict[str, Any]:
        """Put a question to the user for a commission and wait; returns ``{"text", "choice"}``.

        For the gateway: a plugin that wants the user's confirmation is not
        answered by the task agent that called it.
        """
        return await self._ask(commission_id, question)

    async def ask_permission(self, commission_id: str, ask: PermissionAsk) -> str:
        """Ask the user to approve something; the ``Asker`` of the permission gate."""
        answer = await self._ask(commission_id, ask.to_json())
        choice = answer.get("choice")
        # Anything that is not one of the four answers is a refusal. An
        # approval wider than the question offered is narrowed by the gate.
        return choice if choice in CHOICES else "deny"

    async def _parent_of(self, commission_id: str) -> str | None:
        """Return the commission that started this one; the permission gate's lookup."""
        commission = await self._ledger.get(commission_id)
        return commission.parent_id if commission is not None else None

    # -- host callbacks ---------------------------------------------------

    async def _cb_permission(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        detail = {key: payload.get(key) for key in ("tool", "path", "command", "cwd") if payload.get(key)}
        decision = await self._permissions.check(session_id, str(payload.get("category") or "other"), detail=detail)
        return decision.to_json()

    async def _cb_ask_user(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        questions = list(payload.get("questions") or [])
        answer = await self._ask(session_id, {"kind": "questions", "questions": questions})
        return build_answers(questions, str(answer.get("text") or ""))

    async def _cb_report(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        # The report arrives as an event as well; that is where it is acted on.
        del session_id, payload
        return None

    async def _cb_note_lesson(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        commission = await self._ledger.get(session_id)
        if commission is None:
            return None
        if payload.get("category") == "feedback":
            # A correction or approval from the user is a task preference. It is
            # an inference, so it takes effect once the same thing was seen twice.
            await self._preferences.observe(
                feedback_text(payload), lanlan_name=commission.lanlan_name, source_commission=commission.id
            )
            await self._ledger.append_event(commission.id, "feedback", dict(payload))
            return {"message": "filed as a task preference"}
        message = await self._lessons.submit(commission, payload)
        return {"message": message} if message else None

    async def _cb_web_search(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        commission = await self._ledger.get(session_id)
        if commission is None:
            raise RuntimeError("unknown commission")
        queries = [str(query) for query in payload.get("queries") or [] if str(query).strip()]
        # The search is a plugin entry: the plugin switch and the
        # ``plugin_entry`` permission apply as they do through the gateway.
        capability = f"plugin:{WEB_SEARCH_PLUGIN_ID}:{WEB_SEARCH_ENTRY_ID}"
        refusal = None
        if not self._registry.plugins_enabled():
            refusal = "web search is not available: plugins are switched off"
        else:
            decision = await self._permissions.check(
                commission.id,
                "plugin_entry",
                detail={"tool": "web_search", "capability": capability},
            )
            if not decision.allow:
                refusal = decision.reason or "permission denied"
        if refusal is not None:
            await self._ledger.append_event(
                commission.id, "capability_denied", {"capability": capability, "reason": refusal}
            )
            raise RuntimeError(refusal)
        gate = asyncio.Semaphore(_WEB_SEARCH_PARALLEL)

        async def one(query: str) -> dict[str, Any]:
            async with gate:
                outcome = await self._runner.run_entry(
                    commission_id=commission.id,
                    plugin_id=WEB_SEARCH_PLUGIN_ID,
                    entry_id=WEB_SEARCH_ENTRY_ID,
                    args={"query": query},
                    lanlan_name=commission.lanlan_name,
                    conversation_id=commission.conversation_id,
                    latest_user_request=commission.user_text,
                )
            if not outcome.success:
                raise RuntimeError(f"web search failed: {outcome.error or 'the web_search plugin is not available'}")
            data = outcome.data if isinstance(outcome.data, dict) else {}
            sources = []
            for item in data.get("results") or []:
                if isinstance(item, dict) and item.get("url"):
                    source = {"url": str(item["url"])}
                    if item.get("title"):
                        source["title"] = str(item["title"])
                    if item.get("snippet"):
                        source["snippet"] = str(item["snippet"])
                    sources.append(source)
            result: dict[str, Any] = {"query": query, "sources": sources}
            if isinstance(data.get("summary"), str) and data["summary"].strip():
                result["answer"] = data["summary"]
            return result

        return {"results": list(await asyncio.gather(*(one(query) for query in queries)))}

    # -- child task agents ----------------------------------------------------

    async def _team(self, session_id: str) -> tuple[Commission | None, list[Commission]]:
        """Return the lead of the caller's team and the task agents the lead started."""
        commission = await self._ledger.get(session_id)
        if commission is None:
            return None, []
        lead = commission
        if commission.parent_id is not None:
            lead = await self._ledger.get(commission.parent_id) or commission
        members = await self._ledger.select(parent_id=lead.id, oldest_first=True)
        return lead, members

    async def _member(self, session_id: str, name: str) -> Commission | None:
        lead, members = await self._team(session_id)
        if lead is None:
            return None
        if name == LEAD_NAME:
            return lead
        return next((member for member in members if member.name == name), None)

    async def _cb_subagent_start(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        parent = await self._ledger.get(session_id)
        if parent is None:
            raise RuntimeError("unknown commission")
        if parent.parent_id is not None:
            raise RuntimeError("a child task agent cannot start further task agents")
        _lead, members = await self._team(session_id)
        taken = {member.name for member in members}
        name = payload.get("name")
        if not isinstance(name, str) or not name or name in taken or name == LEAD_NAME:
            number = len(members) + 1
            while f"agent-{number}" in taken:
                number += 1
            name = f"agent-{number}"
        workdir = await self._child_workdir(parent, payload.get("cwd"))
        child, _created = await self._ledger.create(
            lanlan_name=parent.lanlan_name,
            title=str(payload.get("description") or name),
            dispatch_text=str(payload.get("prompt") or ""),
            user_text=parent.user_text,
            source="task_agent",
            route="task_agent",
            parent_id=parent.id,
            name=name,
            project=parent.project,
            workdir=workdir,
            conversation_id=parent.conversation_id,
            lang=parent.lang,
        )
        # A stop of the parent sets its status before it looks for children,
        # so a child filed after that look is caught here, and one filed
        # before it is stopped by the stop itself.
        current = await self._ledger.get(parent.id)
        if current is None or current.status not in ("working", "waiting_user"):
            await self._ledger.transition(child.id, "cancelled", reason="its parent was stopped")
            raise RuntimeError("the commission was stopped; no task agent is started")
        await self._ledger.append_event(parent.id, "subagent_started", {"name": name, "commission_id": child.id})
        # It starts now or not at all (see ``_NO_PLACE_REASON``); the parent
        # is told so and can do that part itself. Its result comes back to the
        # parent as a message.
        if not await self.submit(child, candidate_plugin_ids=self._candidates.get(parent.id, ()), wait=False):
            raise RuntimeError(f"{_NO_PLACE_REASON}; do this part yourself")
        return {"name": name}

    async def _child_workdir(self, parent: Commission, cwd: Any) -> str | None:
        """Return the working directory of a task agent the parent starts.

        Inside its working directory a task agent writes without being asked, so
        a child may only work inside its parent's. A directory elsewhere falls
        back to the parent's own: the child then has to ask before writing
        there, exactly as the parent has to.
        """
        from pathlib import Path

        from nekoclaw.runtime.permissions import is_inside

        if not isinstance(cwd, str) or not cwd.strip():
            return parent.workdir
        root = Path(parent.workdir) if parent.workdir else self._paths.work_dir(parent.id, parent.project)
        wanted = root / cwd
        if await asyncio.to_thread(is_inside, wanted, root):
            return str(wanted)
        logger.info("child task agent of %s asked for a folder outside its parent's; using the parent's", parent.id)
        return str(root)

    async def _cb_subagent_list(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        lead, members = await self._team(session_id)
        if lead is None:
            return {"agents": []}
        agents = [{"name": LEAD_NAME, "status": lead.status, "description": lead.title}]
        agents.extend(
            {"name": member.name or member.id, "status": member.status, "description": member.title}
            for member in members
        )
        return {"agents": agents}

    async def _cb_subagent_send(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        target = await self._member(session_id, str(payload.get("agent") or ""))
        if target is None:
            raise RuntimeError(f"no agent named {payload.get('agent')!r}")
        message = str(payload.get("message") or "")
        if not await self.tell(target.id, message):
            raise RuntimeError(f"agent {payload.get('agent')!r} is not running ({target.status})")
        return None

    async def _cb_subagent_interrupt(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        target = await self._member(session_id, str(payload.get("agent") or ""))
        if target is None or target.id == session_id:
            raise RuntimeError(f"no other agent named {payload.get('agent')!r}")
        await self._ledger.transition(target.id, "cancelled", reason="interrupted by another task agent")
        await self.abort(target.id)
        return None

    # -- shutdown ---------------------------------------------------------

    async def shutdown(self) -> None:
        """Stop the runtime and every session. Commissions under way end as failed."""
        self._closed = True
        queued = list(self._queue)
        self._queue.clear()
        for commission_id in queued:
            self._resume.pop(commission_id, None)
            # Refused for one that was stopped meanwhile, and rightly so.
            await self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)
        if self._starting:
            # Sessions on their way in are let in, so they are closed with the rest.
            _done, pending = await asyncio.wait(list(self._starting), timeout=_SHUTDOWN_START_WAIT_SECONDS)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        # Recorded first: what the closing sessions still say must not turn a
        # commission nobody stopped into a stopped one.
        for commission_id in list(self._sessions):
            await self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)
        if self._runtime is not None:
            # A parent whose idle session was closed while its task agents
            # worked holds no session and is in no queue, yet is this host's
            # and under way. (With no runtime ever started there is none, and
            # the ledger is not opened for nothing.)
            for item in await self._ledger.select(statuses={"working"}):
                if (
                    item.route == "task_agent"
                    and item.host_instance == self._ledger.instance
                    and item.id not in self._sessions
                ):
                    await self._ledger.transition(item.id, "failed", reason=_SHUT_DOWN_REASON)
        runtime, self._runtime = self._runtime, None
        if runtime is not None:
            try:
                await runtime.shutdown()
            except Exception:  # noqa: BLE001 - shutting down goes on regardless
                logger.exception("shutting the runtime down failed")
        for commission_id in list(self._sessions):
            self._discard(commission_id)
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
