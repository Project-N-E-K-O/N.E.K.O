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

"""Starting and driving familiars: one commission, one session.

``FamiliarManager`` is the only user of the runtime's driver interface
(``nekoclaw.runtime``). It

* starts the runtime when the first familiar is needed, never before;
* gives every commission a session of its own, whose id is the commission id;
* keeps at most ``FAMILIAR_MAX_CONCURRENT`` familiars at work and leaves the
  rest ``queued`` until a place is free. Familiars started by other familiars
  count the same;
* turns the runtime's events into ledger rows and status changes, and into
  reports for her;
* serves the runtime's host callbacks: permissions, questions for the user,
  reports, lessons, web search and sub-familiars.

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
from nekoclaw.host.permissions import PermissionAsk, PermissionGate
from nekoclaw.host.plugin_runs import PluginRunner
from nekoclaw.host.preferences import PreferenceStore, feedback_text
from nekoclaw.host.reports import CHANNEL_FAMILIAR, Reporter

logger = logging.getLogger(__name__)

RUNTIME_BUILTIN = "builtin"
LEAD_NAME = "lead"
WEB_SEARCH_PLUGIN_ID = "web_search"
WEB_SEARCH_ENTRY_ID = "search"
_WEB_SEARCH_PARALLEL = 3
_SHUTDOWN_START_WAIT_SECONDS = 5.0
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

# Returns the model endpoint for familiars as keyword arguments of
# ``nekoclaw.runtime.protocol.ModelConfig``, or None when none is configured.
ModelConfigProvider = Callable[[], Awaitable["dict[str, Any] | None"]]
# Builds a runtime from the event sink and the host callbacks.
RuntimeFactory = Callable[[Callable[[dict[str, Any]], None], Any], Any]


class LessonSink(Protocol):
    """Receives what ``note_lesson`` files, except ``feedback``.

    The familiar space implements this; until it exists the default sink keeps
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
    lanlan_name: str
    parent_id: str | None
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
    closing: bool = False


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


class FamiliarManager:
    """Runs familiars for commissions. See the module docstring."""

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
    ) -> None:
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
        # What a blocked or failed commission is told when it is taken up again.
        self._resume: dict[str, str] = {}
        self._candidates: dict[str, tuple[str, ...]] = {}
        self._starting: set[asyncio.Task[bool]] = set()
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pump: asyncio.Task[None] | None = None
        self._closed = False
        permissions.set_asker(self.ask_permission)

    # -- state ------------------------------------------------------------

    @property
    def limit(self) -> int:
        if self._max_concurrent is not None:
            return self._max_concurrent
        from config.nekoclaw_settings import FAMILIAR_MAX_CONCURRENT

        return FAMILIAR_MAX_CONCURRENT

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
        """Return whether a familiar could be started, and if not, why."""
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
            logger.warning("the familiar model configuration cannot be read: %s", type(exc).__name__)
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

    async def submit(self, commission: Commission, *, candidate_plugin_ids: Any = ()) -> None:
        """Hand a commission to a familiar: now if a place is free, else when one is."""
        self._candidates[commission.id] = tuple(str(item) for item in candidate_plugin_ids)
        if commission.route != "familiar":
            await self._ledger.update(commission.id, route="familiar")
        if len(self._sessions) >= self.limit:
            self._queue.append(commission.id)
            await self._ledger.append_event(commission.id, "queued", {"ahead": len(self._queue) - 1})
            return
        # Started as a task of its own: whoever handed the commission over may
        # be cancelled (the host shutting down), and a session half set up
        # would then belong to nobody.
        task = asyncio.ensure_future(self._start(commission.id))
        self._starting.add(task)
        task.add_done_callback(self._starting.discard)
        await asyncio.shield(task)

    async def _brief(self, commission: Commission, *, with_gateway: bool) -> str:
        from config.nekoclaw_settings import FAMILIAR_BRIEF_MAX_TOKENS
        from config.prompts import prompts_nekoclaw_brain as texts
        from utils.tokenize import truncate_to_tokens

        if commission.parent_id is not None:
            parts = [texts.FAMILIAR_CHILD_BRIEF_TEMPLATE.format(description=commission.title)]
        else:
            parts = [
                texts.FAMILIAR_BRIEF_TEMPLATE.format(
                    lanlan_name=commission.lanlan_name,
                    dispatch_text=commission.dispatch_text,
                    user_text=commission.user_text or commission.dispatch_text,
                )
            ]
        preferences = await self._preferences.render(f"{commission.dispatch_text}\n{commission.user_text}")
        if preferences:
            parts.append(f"{texts.FAMILIAR_BRIEF_PREFERENCES_HEADER}\n{preferences}")
        if with_gateway:
            parts.append(texts.FAMILIAR_BRIEF_GATEWAY_NOTE)
        return truncate_to_tokens("\n\n".join(parts), FAMILIAR_BRIEF_MAX_TOKENS)

    async def _start(self, commission_id: str, *, prompt: str | None = None) -> bool:
        """Open the session of a commission and give it its work. Returns whether it started."""
        commission = await self._ledger.get(commission_id)
        if commission is None or commission.status not in ("queued", "blocked", "failed"):
            return False
        # Reserve the place before anything is awaited.
        session = _Session(commission.id, commission.lanlan_name, commission.parent_id)
        self._sessions[commission.id] = session
        try:
            config = await self._model_config()
            if not config or not config.get("model"):
                raise RuntimeError("no model is configured for familiars")
            from nekoclaw.runtime import FamiliarSpec, ModelConfig
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
            if self._gateway is not None and await self._registry.callable_by_familiars():
                url = await self._gateway.start()
                token = self._gateway.issue(commission.id, candidate_plugin_ids=self._candidates.get(commission.id, ()))
                gateway_spec = GatewaySpec(url=url, token=token)

            spec = FamiliarSpec(
                session_id=commission.id,
                workdir=workdir,
                session_dir=session_path,
                model=ModelConfig(**config),
                instructions=await self._brief(commission, with_gateway=gateway_spec is not None),
                reply_language=_REPLY_LANGUAGES.get(commission.lang or "", None),
                top_level=commission.parent_id is None,
                # A familiar started by a familiar starts none itself.
                disabled_tools=["subagent"] if commission.parent_id is not None else [],
                gateway=gateway_spec,
            )
            runtime = await self._ensure_runtime()
            moved = await self._ledger.transition(
                commission.id,
                "working",
                reason="a familiar took it up",
                runtime=RUNTIME_BUILTIN,
                route="familiar",
                workdir=workdir,
            )
            if not moved.applied:
                # Stopped while it was being prepared.
                self._discard(commission.id)
                return False
            notes = self._notes.pop(commission.id, [])
            text = prompt if prompt is not None else commission.dispatch_text
            if notes:
                text = "\n\n".join([text, *notes])
            await runtime.start(spec)
            await runtime.prompt(commission.id, text)
            # What was passed on while the session was being set up.
            for note in self._notes.pop(commission.id, []):
                await runtime.steer(commission.id, note)
            return True
        except asyncio.CancelledError:
            self._discard(commission_id)
            raise
        except Exception as exc:  # noqa: BLE001 - the commission fails with the reason
            logger.warning("starting a familiar for %s failed: %s", commission_id, exc)
            self._discard(commission_id)
            await self._fail(commission_id, f"the familiar could not be started: {exc}")
            await self._start_next()
            return False

    def _discard(self, commission_id: str) -> None:
        self._sessions.pop(commission_id, None)
        self._candidates.pop(commission_id, None)
        if self._gateway is not None:
            self._gateway.revoke(commission_id)

    async def _release(self, commission_id: str) -> None:
        """Forget a session that is gone and give its place to the next in the queue."""
        self._discard(commission_id)
        self._notes.pop(commission_id, None)
        await self._start_next()

    async def _start_next(self) -> None:
        while self._queue and len(self._sessions) < self.limit:
            commission_id = self._queue.popleft()
            if await self._start(commission_id, prompt=self._resume.pop(commission_id, None)):
                return

    # -- events -----------------------------------------------------------

    async def _flush(self, session: _Session) -> None:
        """Write buffered text and tool output as event rows."""
        from config.nekoclaw_settings import FAMILIAR_EVENT_TEXT_MAX_CHARS

        rows: list[tuple[str, dict[str, Any]]] = []
        if session.text:
            text = "".join(session.text)
            session.text.clear()
            session.text_size = 0
            for start in range(0, len(text), FAMILIAR_EVENT_TEXT_MAX_CHARS):
                rows.append(("text", {"text": text[start : start + FAMILIAR_EVENT_TEXT_MAX_CHARS]}))
        for call_id, parts in session.updates.items():
            text = "".join(parts)
            # Of long output the end is what shows where the tool stands.
            rows.append(
                (
                    "tool_update",
                    {
                        "tool_call_id": call_id,
                        "tool_name": session.update_names.get(call_id, ""),
                        "text": text[-FAMILIAR_EVENT_TEXT_MAX_CHARS:],
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
        from config.nekoclaw_settings import FAMILIAR_EVENT_TEXT_MAX_CHARS

        compact: dict[str, Any] = {}
        for key, value in event.items():
            if key in ("type", "session_id"):
                continue
            if isinstance(value, (dict, list)):
                dumped = json.dumps(value, ensure_ascii=False, default=str)
                if len(dumped) > FAMILIAR_EVENT_TEXT_MAX_CHARS:
                    value = dumped[:FAMILIAR_EVENT_TEXT_MAX_CHARS] + "…"
            elif isinstance(value, str) and len(value) > FAMILIAR_EVENT_TEXT_MAX_CHARS:
                value = value[:FAMILIAR_EVENT_TEXT_MAX_CHARS] + "…"
            compact[key] = value
        return compact

    async def _handle(self, event: dict[str, Any]) -> None:
        from config.nekoclaw_settings import FAMILIAR_EVENT_TEXT_MAX_CHARS

        kind = event.get("type")
        session = self._sessions.get(str(event.get("session_id") or ""))
        if session is None:
            # Process-level messages, and events of sessions already released.
            return
        if kind == "text":
            delta = str(event.get("delta") or "")
            session.text.append(delta)
            session.text_size += len(delta)
            if session.text_size >= FAMILIAR_EVENT_TEXT_MAX_CHARS:
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

    def _summary(self, text: str) -> str:
        from config.nekoclaw_settings import FAMILIAR_RESULT_SUMMARY_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        return truncate_to_tokens(text, FAMILIAR_RESULT_SUMMARY_MAX_TOKENS)

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
                reason="the familiar needs an answer",
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
        moved = await self._ledger.transition(
            commission_id, target, reason=f"the familiar reported {status}", result_summary=self._summary(text)
        )
        if moved.applied and moved.commission is not None:
            await self._finished(moved.commission, text, suggested_delivery=event.get("delivery"))

    async def _on_ended(self, session: _Session, event: dict[str, Any]) -> None:
        commission_id = session.commission_id
        reason = event.get("reason")
        if reason == "closed":
            await self._release(commission_id)
            return
        commission = await self._ledger.get(commission_id)
        if commission is None:
            return
        if reason == "completed":
            if commission.status == "waiting_user" and session.awaiting_prompt:
                # It asked for an answer and stopped; the session stays for the answer.
                session.run_ended = True
                if session.held_answer is not None:
                    await self._deliver_answer(session, session.held_answer)
                return
            if commission.status == "working":
                if await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
                    # Familiars it started are still at work. Their results
                    # arrive as messages and start its next run; it is not done.
                    return
                # The run ended without a closing report: its last words are the result.
                text = str(event.get("text") or "")
                moved = await self._ledger.transition(
                    commission_id, "done", reason="the run completed", result_summary=self._summary(text)
                )
                if moved.applied and moved.commission is not None:
                    await self._finished(moved.commission, text)
        elif reason == "aborted":
            moved = await self._ledger.transition(commission_id, "cancelled", reason="the run was stopped")
            if moved.applied and moved.commission is not None:
                await self._finished(moved.commission, "")
        elif reason == "error":
            await self._fail(commission_id, str(event.get("error") or "the run failed"))
        # It is over; what it started must not go on working for nobody.
        for child in await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
            await self._ledger.transition(child.id, "cancelled", reason="its parent finished")
            await self.abort(child.id)
        await self._close_session(session)

    async def _on_error(self, session: _Session, event: dict[str, Any]) -> None:
        command = event.get("command")
        message = str(event.get("message") or "the runtime rejected the command")
        commission_id = session.commission_id
        if command == "start":
            await self._fail(commission_id, f"the familiar could not be started: {message}")
            await self._release(commission_id)
        elif command == "prompt":
            await self._fail(commission_id, message)
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
        moved = await self._ledger.transition(
            commission_id, "failed", reason=reason, result_summary=self._summary(reason)
        )
        if moved.applied and moved.commission is not None:
            await self._finished(moved.commission, reason)

    async def _finished(self, commission: Commission, text: str, *, suggested_delivery: Any = None) -> None:
        """Tell whoever commissioned it how it ended: the parent familiar, or her."""
        status = _STATUS_TO_REPORT.get(commission.status, commission.status)
        if commission.parent_id is not None:
            await self._tell_parent(commission, status, text)
            return
        if commission.status == "cancelled":
            # Whoever stopped it knows; the task brain reports a stop when one is due.
            return
        # The commission's own setting decides; the familiar's wish is the fallback.
        delivery = commission.delivery or (
            suggested_delivery if suggested_delivery in ("proactive", "passive", "silent") else "proactive"
        )
        await self._reporter.task_result(
            commission,
            channel=CHANNEL_FAMILIAR,
            success=commission.status == "done",
            summary=text,
            detail=text,
            error_message=text if commission.status == "failed" else "",
            status=status,
            source_kind="familiar",
            source_name=commission.title,
            delivery_mode=delivery,
        )

    async def _tell_parent(self, commission: Commission, status: str, text: str) -> None:
        from config.nekoclaw_settings import FAMILIAR_CHILD_RESULT_MAX_TOKENS
        from config.prompts import prompts_nekoclaw_brain as texts
        from utils.tokenize import truncate_to_tokens

        parent_id = str(commission.parent_id)
        if parent_id not in self._sessions or self._runtime is None:
            return
        message = texts.FAMILIAR_CHILD_RESULT_TEMPLATE.format(
            name=commission.name or commission.id,
            status=status,
            text=truncate_to_tokens(text, FAMILIAR_CHILD_RESULT_MAX_TOKENS),
        )
        # Before the parent's next model request, so a parent that waits for
        # its familiars sees the result as soon as it looks; an idle parent is
        # started by it.
        await self._runtime.steer(parent_id, message)

    # -- driving ----------------------------------------------------------

    async def abort(self, commission_id: str) -> None:
        """Stop a commission's familiar, and the familiars it started.

        The caller has set the commission ``cancelled`` already.
        """
        for child in await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
            await self._ledger.transition(child.id, "cancelled", reason="its parent was stopped")
            await self.abort(child.id)
        if commission_id in self._queue:
            self._queue.remove(commission_id)
        self._notes.pop(commission_id, None)
        self._resume.pop(commission_id, None)
        session = self._sessions.get(commission_id)
        if session is None:
            return
        if session.question is not None and not session.question.done():
            session.question.cancel()
        # Closing stops the run, kills its background jobs and frees the place.
        await self._close_session(session)

    async def tell(self, commission_id: str, text: str) -> bool:
        """Pass a sentence on to a commission's familiar. Returns whether it was delivered."""
        commission = await self._ledger.get(commission_id)
        if commission is None or commission.route != "familiar":
            return False
        session = self._sessions.get(commission_id)
        if session is not None and self._runtime is not None and session.awaiting_prompt:
            return await self.answer(commission_id, text)
        if commission.status in ("blocked", "failed"):
            # Take it up again where its transcript ends.
            await self._ledger.append_event(commission_id, "told", {"text": text, "resumed": True})
            if session is not None or len(self._sessions) >= self.limit:
                # Its old session is still on its way out, or no place is
                # free: it is taken up again as soon as that has changed.
                self._resume[commission_id] = text
                if commission_id not in self._queue:
                    self._queue.append(commission_id)
                return True
            return await self._start(commission_id, prompt=text)
        if commission.status == "queued":
            # Not started yet: it goes along with the work when it starts.
            self._notes.setdefault(commission_id, []).append(text)
            await self._ledger.append_event(commission_id, "told", {"text": text, "queued": True})
            return True
        if session is not None and self._runtime is not None:
            if session.closing or commission.status not in ("working", "waiting_user"):
                return False
            await self._ledger.append_event(commission_id, "told", {"text": text})
            await self._runtime.steer(commission_id, text)
            return True
        return False

    async def answer(self, commission_id: str, text: str, choice: str | None = None) -> bool:
        """Give the user's answer to a commission that waits for it."""
        session = self._sessions.get(commission_id)
        if session is None:
            return False
        if session.question is not None and not session.question.done():
            session.question.set_result({"text": text, "choice": choice})
            return True
        if session.awaiting_prompt and self._runtime is not None:
            if not session.run_ended:
                # The run that asked is still winding down. The answer is kept
                # and handed over the moment it has ended.
                session.held_answer = text
                return True
            return await self._deliver_answer(session, text)
        return False

    async def _deliver_answer(self, session: _Session, text: str) -> bool:
        """Give an idle session the answer to its ``waiting`` report as its next work."""
        commission_id = session.commission_id
        moved = await self._ledger.transition(commission_id, "working", reason="the user answered")
        if not moved.applied:
            return False
        session.awaiting_prompt = False
        session.held_answer = None
        await self._ledger.append_event(commission_id, "answered", {"text": text})
        await self._runtime.prompt(commission_id, text)
        return True

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
                # Refused, and rightly so, when the commission was stopped meanwhile.
                await asyncio.shield(self._ledger.transition(commission_id, "working", reason="the user answered"))

    async def ask_permission(self, commission_id: str, ask: PermissionAsk) -> str:
        """Ask the user to approve something; the ``Asker`` of the permission gate."""
        answer = await self._ask(commission_id, ask.to_json())
        choice = answer.get("choice")
        return choice if choice in ask.options else "deny"

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

    # -- sub-familiars ----------------------------------------------------

    async def _team(self, session_id: str) -> tuple[Commission | None, list[Commission]]:
        """Return the lead of the caller's team and the familiars the lead started."""
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
            raise RuntimeError("a sub-familiar cannot start further familiars")
        _lead, members = await self._team(session_id)
        taken = {member.name for member in members}
        name = payload.get("name")
        if not isinstance(name, str) or not name or name in taken or name == LEAD_NAME:
            number = len(members) + 1
            while f"agent-{number}" in taken:
                number += 1
            name = f"agent-{number}"
        child, _created = await self._ledger.create(
            lanlan_name=parent.lanlan_name,
            title=str(payload.get("description") or name),
            dispatch_text=str(payload.get("prompt") or ""),
            user_text=parent.user_text,
            source="familiar",
            route="familiar",
            parent_id=parent.id,
            name=name,
            project=parent.project,
            workdir=str(payload["cwd"]) if payload.get("cwd") else parent.workdir,
            conversation_id=parent.conversation_id,
            lang=parent.lang,
        )
        await self._ledger.append_event(parent.id, "subagent_started", {"name": name, "commission_id": child.id})
        # Registered; it starts now or when a place is free. Its result comes
        # back to the parent as a message.
        await self.submit(child, candidate_plugin_ids=self._candidates.get(parent.id, ()))
        return {"name": name}

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
        await self._ledger.transition(target.id, "cancelled", reason="interrupted by another familiar")
        await self.abort(target.id)
        return None

    # -- shutdown ---------------------------------------------------------

    async def shutdown(self) -> None:
        """Stop the runtime and every session. Commissions under way end as failed."""
        self._closed = True
        self._queue.clear()
        if self._starting:
            # Sessions on their way in are let in, so they are closed with the rest.
            _done, pending = await asyncio.wait(list(self._starting), timeout=_SHUTDOWN_START_WAIT_SECONDS)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        # Recorded first: what the closing sessions still say must not turn a
        # commission nobody stopped into a stopped one.
        for commission_id in list(self._sessions):
            await self._ledger.transition(commission_id, "failed", reason="the host was shut down")
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
