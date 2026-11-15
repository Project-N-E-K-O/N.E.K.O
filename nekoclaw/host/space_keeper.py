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

"""What is done for the task agent space in the background.

``SpaceKeeper`` listens to the ledger. When a commission a task agent worked
on has ended, it

* writes a **review** of it with one call of the summary model: what was
  asked, what was done, how it ended. The same call names the entries worth
  keeping and the project the commission belongs to. At most one review is
  made per commission. One that is done or was stopped is reviewed right
  away; a failed one only when it was left failed for a day, because a failed
  commission is often taken up again and the review would miss how it ended.
  A commission with next to no tool calls is not reviewed at all, nor is one
  a plugin entry did, nor one a task agent started for itself (its work is
  part of its parent's).
* then, at most every few hours, **tidies**: archives projects nothing was
  commissioned for in a month (one model call for the summary), removes what
  was said in sessions that ended a month ago (never a working directory),
  and makes the reviews that are due late.

None of it is on the way of anything: it starts after the report to her has
been stored, holds no task agent place, and a failure is recorded and not
tried again. Nothing runs at startup; without a commission ending, nothing
runs at all.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nekoclaw.host.decision import TIER_ARCHIVE, TIER_REVIEW
from nekoclaw.host.ledger import ACTIVE_STATUSES, ENDED_STATUSES, Commission, Ledger, LedgerEvent
from nekoclaw.host.paths import DEFAULT_PROJECT, TRANSCRIPT_NAME, safe_segment
from nekoclaw.host.space import ENTRY_TYPES, Project, SpaceError, TaskAgentSpace, normalize_name

logger = logging.getLogger(__name__)

# Statuses in which a commission is over for good, or left as it is.
_REVIEWED_AT_ONCE = frozenset({"done", "cancelled"})
_REVIEWABLE = frozenset({"done", "cancelled", "failed"})
# Tool calls that say nothing about how much work a commission was.
_NOT_WORK = frozenset({"report", "note_lesson", "todo_write"})
_OUTPUTS_NAME = "outputs"
# Statuses in which what was said in a session is no longer needed once it is old.
_CLEANABLE = frozenset({"done", "failed", "cancelled"})
# What became of one session that was looked at.
_REMOVED, _NOTHING, _NOT_REMOVED = "removed", "nothing", "not_removed"
_EVENT_PAGE = 500
_CLEAN_BATCH = 200
# Most sessions whose files could not be removed that are remembered to be tried again.
_RETRY_KEPT = 500
_LANGUAGES = {
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


def reviewable(commission: Commission) -> bool:
    """Whether a commission is one that gets a review: her own, worked on by a task agent."""
    return commission.route == "task_agent" and commission.parent_id is None


def _cut(text: Any, max_tokens: int) -> str:
    from utils.tokenize import truncate_to_tokens

    return truncate_to_tokens(" ".join(str(text or "").split()), max_tokens)


def _parse_object(raw: str) -> dict[str, Any] | None:
    """Return the JSON object a model answered with, or None."""
    from utils.file_utils import robust_json_loads

    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = robust_json_loads(text[start : end + 1])
    except Exception:  # noqa: BLE001 - whatever the parser raises, there is no object
        return None
    return value if isinstance(value, dict) else None


def tool_calls(events: list[LedgerEvent]) -> int:
    """Return how many tool calls of a commission were work (not reports, lessons or the todo list)."""
    return sum(
        1
        for event in events
        if event.type == "tool_start" and str(event.payload.get("tool_name") or "") not in _NOT_WORK
    )


def account(events: list[LedgerEvent], max_tokens: int, line_max_tokens: int) -> str:
    """Return what a task agent did, one line per step, within ``max_tokens``.

    Of a longer account the beginning (a third of the room) and the end are
    kept: how it set out, and how it ended.
    """
    from config.prompts.prompts_nekoclaw_space import TASK_AGENT_REVIEW_PROCESS_GAP
    from utils.tokenize import count_tokens

    half = max(8, line_max_tokens // 2)
    arguments: dict[str, str] = {}
    lines: list[str] = []
    said: list[str] = []

    def flush_said() -> None:
        if said:
            lines.append(f"said: {_cut(''.join(said), line_max_tokens)}")
            said.clear()

    for event in events:
        payload = event.payload
        kind = event.type
        if kind == "text":
            said.append(str(payload.get("text") or ""))
            continue
        if kind in ("tool_update", "usage", "status", "created", "queued", "decision", "lesson", "feedback"):
            continue
        flush_said()
        if kind == "tool_start":
            args = payload.get("args")
            if not isinstance(args, str):
                args = json.dumps(args or {}, ensure_ascii=False, default=str)
            arguments[str(payload.get("tool_call_id") or "")] = args
        elif kind == "tool_end":
            name = str(payload.get("tool_name") or "tool")
            if name in _NOT_WORK:
                continue
            args = arguments.pop(str(payload.get("tool_call_id") or ""), "")
            outcome = "error" if payload.get("is_error") else "ok"
            lines.append(f"{name}({_cut(args, half)}) -> {outcome}: {_cut(payload.get('text'), half)}")
        elif kind == "report":
            lines.append(f"report {payload.get('status')}: {_cut(payload.get('summary'), line_max_tokens)}")
        elif kind in ("told", "answered"):
            lines.append(f"user: {_cut(payload.get('text'), line_max_tokens)}")
        elif kind == "error":
            lines.append(f"error: {_cut(payload.get('message'), line_max_tokens)}")
        elif kind == "subagent_started":
            lines.append(f"started task agent {payload.get('name')}")
        elif kind == "capability_denied":
            lines.append(f"denied: {_cut(payload.get('capability'), half)}")
    flush_said()
    if not lines:
        return ""
    sizes = [count_tokens(line) + 1 for line in lines]
    if sum(sizes) <= max_tokens:
        return "\n".join(lines)
    head_room, used, head = max_tokens // 3, 0, 0
    while head < len(lines) and used + sizes[head] <= head_room:
        used += sizes[head]
        head += 1
    tail_room, used, tail = max_tokens - head_room - 12, 0, len(lines)
    while tail > head and used + sizes[tail - 1] <= tail_room:
        used += sizes[tail - 1]
        tail -= 1
    gap = TASK_AGENT_REVIEW_PROCESS_GAP.format(count=tail - head)
    return "\n".join([*lines[:head], gap, *lines[tail:]])


class SpaceKeeper:
    """Reviews finished commissions and tidies the space. See the module docstring.

    ``llm`` is a ``DecisionLLM`` that knows ``TIER_REVIEW`` and
    ``TIER_ARCHIVE``; without one nothing is reviewed and projects are
    archived without a summary. ``session_dir_of`` returns where a
    commission's session is kept (blocking; ``TaskAgentManager.session_dir_of``).
    ``hold_session`` and ``release_session`` are the manager's: while a
    session is held for cleaning, its commission is not taken up again, and
    one that is in use is not held.
    """

    def __init__(
        self,
        *,
        space: TaskAgentSpace,
        ledger: Ledger,
        llm: Any = None,
        session_dir_of: Callable[[Commission], Path] | None = None,
        hold_session: Callable[[str], bool] | None = None,
        release_session: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.time,
        review_delay: float | None = None,
    ) -> None:
        self._space = space
        self._ledger = ledger
        self._llm = llm
        self._session_dir_of = session_dir_of
        self._hold_session = hold_session
        self._release_session = release_session
        self._clock = clock
        self._review_delay = review_delay
        self._tasks: set[asyncio.Task[Any]] = set()
        # One model call of the space at a time.
        self._working: asyncio.Lock | None = None
        self._tidying = False
        self._last_upkeep: float | None = None
        self._closed = False

    # -- listening --------------------------------------------------------

    def watch(self, commission: Commission, previous: str | None) -> None:
        """Note a change of a commission; a ``Ledger.subscribe`` listener.

        Runs on the event loop after the change is stored. It only starts a
        task: nothing here is awaited by whoever changed the commission.
        """
        if self._closed or previous is None or commission.parent_id is not None:
            return
        if commission.status not in ENDED_STATUSES:
            return
        review = reviewable(commission) and commission.status in _REVIEWED_AT_ONCE
        self._spawn(self._after_end(commission.id, review=review))

    def _spawn(self, work: Any) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _after_end(self, commission_id: str, *, review: bool) -> None:
        from config.nekoclaw_settings import TASK_AGENT_REVIEW_DELAY_SECONDS

        try:
            delay = TASK_AGENT_REVIEW_DELAY_SECONDS if self._review_delay is None else self._review_delay
            if delay > 0:
                await asyncio.sleep(delay)
            if review:
                await self.review(commission_id)
            await self.tidy()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - background work; the next commission tries again
            logger.exception("work for the task agent space after a commission failed")

    async def settle(self) -> None:
        """Wait until everything started so far is done. For tests."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        """Stop what is under way. A review cut off here is made by a later round of tidying."""
        self._closed = True
        tasks, self._tasks = list(self._tasks), set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _lock(self) -> asyncio.Lock:
        if self._working is None:
            self._working = asyncio.Lock()
        return self._working

    # -- reviews ----------------------------------------------------------

    async def _events(self, commission_id: str) -> list[LedgerEvent]:
        events: list[LedgerEvent] = []
        while True:
            page = await self._ledger.events(commission_id, after=events[-1].seq if events else 0, limit=_EVENT_PAGE)
            events.extend(page)
            if len(page) < _EVENT_PAGE:
                return events

    async def review(self, commission_id: str) -> str:
        """Review one commission, unless it was reviewed or is not to be.

        Returns what became of it: ``written``, ``skipped`` (too little was
        done), ``failed`` (the model gave nothing usable), ``already``,
        ``not_due`` (not ended, or not a commission that is reviewed),
        ``no_model`` or ``stale``. The first three are recorded as a
        ``review`` event of the commission, and a commission with such an
        event is never reviewed again.

        ``stale``: a failed commission was taken up again while the model
        was writing. What came back is about an end that is no longer the
        commission's; it is thrown away and nothing is recorded, so the end
        the commission comes to is the one that is reviewed.
        """
        from config.nekoclaw_settings import TASK_AGENT_REVIEW_MIN_TOOL_CALLS

        async with self._lock():
            commission = await self._ledger.get(commission_id)
            if commission is None or not reviewable(commission) or commission.status not in _REVIEWABLE:
                return "not_due"
            events = await self._events(commission_id)
            if any(event.type == "review" for event in events):
                return "already"
            calls = tool_calls(events)
            if calls < TASK_AGENT_REVIEW_MIN_TOOL_CALLS:
                await self._ledger.append_event(
                    commission_id, "review", {"status": "skipped", "reason": "short", "tool_calls": calls}
                )
                return "skipped"
            if self._llm is None:
                return "no_model"
            data: dict[str, Any] | None = None
            problem = ""
            try:
                data = _parse_object(
                    await self._llm.complete(TIER_REVIEW, await self._review_messages(commission, events))
                )
                problem = "" if data is not None else "unreadable"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - recorded, and not tried again
                logger.warning("reviewing commission %s failed (%s)", commission_id, type(exc).__name__)
                problem = type(exc).__name__
            # A commission that is done or was stopped stays so. A failed one
            # can be taken up again at any moment: it is held against that
            # while the review is kept, and looked up again once it is held.
            holding = False
            if commission.status not in _REVIEWED_AT_ONCE and self._hold_session is not None:
                if not self._hold_session(commission_id):
                    return "stale"
                holding = True
            try:
                if commission.status not in _REVIEWED_AT_ONCE:
                    current = await self._ledger.get(commission_id)
                    same = current is not None and (current.status, current.updated_at) == (
                        commission.status,
                        commission.updated_at,
                    )
                    if not same:
                        return "stale"
                if problem or data is None:
                    outcome = {"status": "failed", "reason": problem or "unreadable"}
                else:
                    try:
                        outcome = await self._keep_review(commission, data)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - recorded, and not tried again
                        logger.warning("keeping the review of %s failed (%s)", commission_id, type(exc).__name__)
                        outcome = {"status": "failed", "reason": type(exc).__name__}
                await self._ledger.append_event(commission_id, "review", outcome)
                return str(outcome["status"])
            finally:
                if holding and self._release_session is not None:
                    self._release_session(commission_id)

    async def _review_messages(self, commission: Commission, events: list[LedgerEvent]) -> list[dict[str, str]]:
        from config.nekoclaw_settings import (
            DISPATCH_TEXT_MAX_TOKENS,
            DISPATCH_USER_TEXT_MAX_TOKENS,
            TASK_AGENT_REVIEW_INDEX_MAX_TOKENS,
            TASK_AGENT_REVIEW_INPUT_MAX_TOKENS,
            TASK_AGENT_REVIEW_MAX_ENTRIES,
            TASK_AGENT_REVIEW_PROCESS_LINE_MAX_TOKENS,
            TASK_AGENT_REVIEW_PROCESS_MAX_TOKENS,
            TASK_AGENT_REVIEW_PROJECTS_MAX_TOKENS,
            TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS,
        )
        from config.prompts import prompts_nekoclaw_space as texts
        from utils.tokenize import count_tokens, truncate_to_tokens

        nothing = texts.TASK_AGENT_REVIEW_NOTHING
        project = commission.project or DEFAULT_PROJECT
        filed = [
            f"- {_cut(event.payload.get('title'), 24)}"
            for event in events
            if event.type in ("lesson", "feedback") and event.payload.get("title")
        ]
        projects = [
            f"- {item.name}: {item.description}" if item.description else f"- {item.name}"
            for item in await self._space.list_projects()
            if item.name != DEFAULT_PROJECT
        ]
        system = texts.TASK_AGENT_REVIEW_SYSTEM_PROMPT.format(
            language=_LANGUAGES.get(commission.lang or "", "the language of the commission"),
            max_entries=TASK_AGENT_REVIEW_MAX_ENTRIES,
        )
        user = texts.TASK_AGENT_REVIEW_USER_TEMPLATE.format(
            dispatch_text=truncate_to_tokens(commission.dispatch_text, DISPATCH_TEXT_MAX_TOKENS),
            user_text=truncate_to_tokens(commission.user_text or nothing, DISPATCH_USER_TEXT_MAX_TOKENS),
            status=commission.status,
            result=truncate_to_tokens(commission.result_summary or nothing, TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS),
            project=nothing if project == DEFAULT_PROJECT else project,
            process=account(events, TASK_AGENT_REVIEW_PROCESS_MAX_TOKENS, TASK_AGENT_REVIEW_PROCESS_LINE_MAX_TOKENS)
            or nothing,
            filed=truncate_to_tokens("\n".join(filed), 120) or nothing,
            projects=truncate_to_tokens("\n".join(projects), TASK_AGENT_REVIEW_PROJECTS_MAX_TOKENS) or nothing,
            global_index=truncate_to_tokens(
                (await self._space.read_index(None)).strip(), TASK_AGENT_REVIEW_INDEX_MAX_TOKENS
            )
            or nothing,
            project_index=truncate_to_tokens(
                (await self._space.read_index(project)).strip(), TASK_AGENT_REVIEW_INDEX_MAX_TOKENS
            )
            or nothing,
        )
        # The parts have caps of their own that add up to less; this is what holds whatever they are.
        room = TASK_AGENT_REVIEW_INPUT_MAX_TOKENS - count_tokens(system)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": truncate_to_tokens(user, max(room, 0))},
        ]

    async def _target_project(self, commission: Commission, data: dict[str, Any]) -> str:
        """Return the project a review is kept under, making or restoring the project it names.

        A commission that was filed under a project when it started stays
        there. One of ``_daily`` goes where the review says, if it names a
        project; a name that cannot be a directory name is ignored.
        """
        from config.nekoclaw_settings import SPACE_PROJECT_NAME_MAX_TOKENS

        current = commission.project or DEFAULT_PROJECT
        if current != DEFAULT_PROJECT:
            return current
        named = data.get("project")
        if not isinstance(named, str):
            return current
        name = _cut(named, SPACE_PROJECT_NAME_MAX_TOKENS).rstrip(" .")
        if not name or name.startswith(("_", ".")):
            # Names that begin so are the host's own (``_daily``) or hidden.
            return current
        try:
            safe_segment(name, "project")
        except ValueError:
            return current
        wanted = normalize_name(name)
        for known in await self._space.list_projects():
            if normalize_name(known.name) == wanted:
                name = known.name
                break
        project = await self._space.open_project(
            name,
            description=str(data.get("project_description") or ""),
            workdir=commission.workdir,
            at=commission.ended_at,
        )
        return project.name

    async def _keep_review(self, commission: Commission, data: dict[str, Any]) -> dict[str, Any]:
        """Keep what the model answered: the review, its entries, the project. Returns the ``review`` event."""
        from config.nekoclaw_settings import TASK_AGENT_REVIEW_MAX_ENTRIES, TASK_AGENT_REVIEW_TEXT_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        text = truncate_to_tokens(str(data.get("review") or "").strip(), TASK_AGENT_REVIEW_TEXT_MAX_TOKENS)
        if not text:
            return {"status": "failed", "reason": "empty"}
        project = await self._target_project(commission, data)
        kept: list[str] = []
        entries = data.get("entries")
        for item in (entries if isinstance(entries, list) else [])[:TASK_AGENT_REVIEW_MAX_ENTRIES]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "").strip().lower()
            everywhere = str(item.get("scope") or "").strip().lower() == "global" and kind != "progress"
            try:
                entry, _created = await self._space.file_entry(
                    None if everywhere else project,
                    title=str(item.get("title") or ""),
                    content=str(item.get("content") or ""),
                    kind=kind if kind in ENTRY_TYPES else "lesson",
                    description=str(item.get("description") or ""),
                    source=commission.id,
                )
            except (SpaceError, OSError) as exc:
                logger.warning("an entry of a review could not be kept (%s)", type(exc).__name__)
                continue
            kept.append(entry.id if everywhere else f"{project}/{entry.id}")
        await self._space.write_review(project, commission, text, entries=tuple(kept))
        return {"status": "written", "project": project, "entries": kept}

    def _unreviewed(self, conn: sqlite3.Connection, since: float, before: float, limit: int) -> list[str]:
        rows = conn.execute(
            "SELECT c.id FROM commissions c WHERE c.route = 'task_agent' AND c.parent_id IS NULL "
            "AND c.status IN ('done', 'failed', 'cancelled') AND c.updated_at >= ? AND c.updated_at <= ? "
            "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.commission_id = c.id AND e.type = 'review') "
            "ORDER BY c.updated_at DESC LIMIT ?",
            (since, before, limit),
        ).fetchall()
        return [str(row["id"]) for row in rows]

    async def _late_reviews(self, now: float, since: float) -> None:
        """Review commissions that ended a day ago and have no review: failed ones, and ones a shutdown cut off."""
        from config.nekoclaw_settings import (
            SPACE_TRANSCRIPT_KEPT_SECONDS,
            TASK_AGENT_REVIEW_FAILED_AFTER_SECONDS,
            TASK_AGENT_REVIEWS_PER_UPKEEP,
        )

        if self._llm is None:
            return
        earliest = max(since, now - SPACE_TRANSCRIPT_KEPT_SECONDS)
        before = now - TASK_AGENT_REVIEW_FAILED_AFTER_SECONDS
        if before < earliest:
            return
        due = await self._ledger.run(
            lambda conn: self._unreviewed(conn, earliest, before, TASK_AGENT_REVIEWS_PER_UPKEEP)
        )
        for commission_id in due:
            await self.review(commission_id)

    # -- tidying ----------------------------------------------------------

    async def tidy(self, *, force: bool = False) -> bool:
        """Run one round of tidying if one is due. Returns whether it ran."""
        from config.nekoclaw_settings import SPACE_UPKEEP_INTERVAL_SECONDS

        if self._closed or self._tidying:
            return False
        self._tidying = True
        try:
            now = self._clock()
            state = await self._space.read_state()
            if self._last_upkeep is None:
                stored = state.get("upkeep_at")
                self._last_upkeep = float(stored) if isinstance(stored, (int, float)) else 0.0
            if not force and now - self._last_upkeep < SPACE_UPKEEP_INTERVAL_SECONDS:
                return False
            self._last_upkeep = now
            since = state.get("since")
            if not isinstance(since, (int, float)):
                # The first round ever: commissions that ended before it are
                # not reviewed after the fact.
                since = now
            await self._space.write_state(upkeep_at=now, since=since)
            for name, step in (
                ("late reviews", lambda: self._late_reviews(now, float(since))),
                ("archiving", lambda: self._archive_idle(now)),
                ("old sessions", lambda: self._clean_sessions(now, state)),
            ):
                try:
                    await step()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - the other steps still run
                    logger.exception("tidying the task agent space failed at: %s", name)
            return True
        finally:
            self._tidying = False

    async def _archive_messages(self, project: Project) -> list[dict[str, str]]:
        from config.nekoclaw_settings import (
            SPACE_ARCHIVE_INPUT_MAX_TOKENS,
            TASK_AGENT_REVIEW_INDEX_MAX_TOKENS,
            TASK_AGENT_REVIEW_MAX_ENTRIES,
        )
        from config.prompts import prompts_nekoclaw_space as texts
        from nekoclaw.host.space import _date
        from utils.tokenize import count_tokens, truncate_to_tokens

        nothing = texts.TASK_AGENT_REVIEW_NOTHING
        reviews = []
        for item in (await self._space.list_reviews(project.name))[:12]:
            review = await self._space.read_review(item.commission_id)
            if review is not None:
                reviews.append(f"- {review.title} ({review.status}): {_cut(review.content, 150)}")
        system = texts.SPACE_ARCHIVE_SYSTEM_PROMPT.format(
            language="the language its index and reviews are written in",
            max_entries=min(2, TASK_AGENT_REVIEW_MAX_ENTRIES),
        )
        user = texts.SPACE_ARCHIVE_USER_TEMPLATE.format(
            name=project.name,
            description=project.description or nothing,
            workdir=project.workdir or nothing,
            commissions=project.commissions,
            last=_date(project.last_commission_at),
            index=truncate_to_tokens((await self._space.read_index(project.name)).strip(), 600) or nothing,
            reviews=truncate_to_tokens("\n".join(reviews), 1500) or nothing,
            global_index=truncate_to_tokens(
                (await self._space.read_index(None)).strip(), TASK_AGENT_REVIEW_INDEX_MAX_TOKENS
            )
            or nothing,
        )
        room = SPACE_ARCHIVE_INPUT_MAX_TOKENS - count_tokens(system)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": truncate_to_tokens(user, max(room, 0))},
        ]

    async def _summarize(self, project: Project) -> dict[str, Any] | None:
        """Return the model's summary of a project, or None when it gave nothing usable."""
        async with self._lock():
            try:
                data = _parse_object(await self._llm.complete(TIER_ARCHIVE, await self._archive_messages(project)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - counted, and tried again by a later round
                logger.warning("summarising project %s failed (%s)", project.name, type(exc).__name__)
                return None
        if data is None or not str(data.get("summary") or "").strip():
            return None
        return data

    async def _archive_idle(self, now: float) -> None:
        """Archive the projects nothing was commissioned for in a while."""
        from config.nekoclaw_settings import (
            SPACE_ARCHIVE_MAX_ATTEMPTS,
            SPACE_ARCHIVES_PER_UPKEEP,
            SPACE_PROJECT_DESCRIPTION_MAX_TOKENS,
            SPACE_PROJECT_IDLE_SECONDS,
        )
        from utils.tokenize import truncate_to_tokens

        idle = [
            project
            for project in await self._space.list_projects(include_archived=False)
            if project.name != DEFAULT_PROJECT
            and project.last_commission_at > 0
            and now - project.last_commission_at >= SPACE_PROJECT_IDLE_SECONDS
        ]
        if not idle:
            return

        async def busy() -> set[Any]:
            return {item.project for item in await self._ledger.select(statuses=ACTIVE_STATUSES)}

        in_use = await busy()
        archived = 0
        for project in idle:
            if project.name in in_use or archived >= SPACE_ARCHIVES_PER_UPKEEP:
                continue
            archived += 1
            data = None
            if self._llm is not None and project.archive_attempts < SPACE_ARCHIVE_MAX_ATTEMPTS:
                data = await self._summarize(project)
                if data is None and await self._space.note_archive_attempt(project.name) < SPACE_ARCHIVE_MAX_ATTEMPTS:
                    # Left where it is; a later round asks again.
                    continue
            summary = truncate_to_tokens(str((data or {}).get("summary") or "").strip(), 400)
            line = _cut((data or {}).get("line"), SPACE_PROJECT_DESCRIPTION_MAX_TOKENS)
            entries = (data or {}).get("entries")
            for item in (entries if isinstance(entries, list) else [])[:2]:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("type") or "").strip().lower()
                try:
                    await self._space.file_entry(
                        None,
                        title=str(item.get("title") or ""),
                        content=str(item.get("content") or ""),
                        kind=kind if kind in ENTRY_TYPES and kind != "progress" else "lesson",
                        description=str(item.get("description") or ""),
                    )
                except (SpaceError, OSError) as exc:
                    logger.warning("an entry of a project's summary could not be kept (%s)", type(exc).__name__)
            # The summary took a while. A commission taken up again meanwhile
            # shows in the ledger; one filed under the project meanwhile
            # shows in the project's own record, which the space looks at
            # under the same lock it moves the directory under.
            if project.name in await busy():
                continue
            moved = await self._space.archive_project(
                project.name, summary=summary, description=line, unless_touched_since=project.last_commission_at
            )
            if moved is not None:
                logger.info("archived an idle project of the task agent space (summary: %s)", bool(summary))

    def _ended_long_ago(self, conn: sqlite3.Connection, after: float, until: float) -> list[sqlite3.Row]:
        return conn.execute(
            "SELECT * FROM commissions WHERE route = 'task_agent' AND status IN ('done', 'failed', 'cancelled') "
            "AND updated_at > ? AND updated_at <= ? ORDER BY updated_at ASC LIMIT ?",
            (after, until, _CLEAN_BATCH),
        ).fetchall()

    @staticmethod
    def _clean_session(directory: Path, commission_id: str) -> str:
        """Remove what was said in a session; its working directory is not touched. Blocking.

        Only in a directory that is a session's by its place and name, and
        only the two things the runtime writes there. Returns ``removed``,
        ``nothing`` (there was nothing to remove) or ``not_removed``
        (something is still there: held open by another program, say).
        """
        if directory.name != commission_id or directory.parent.name != "sessions":
            return _NOTHING
        removed = failed = False
        transcript = directory / TRANSCRIPT_NAME
        try:
            if transcript.is_file() and not transcript.is_symlink():
                transcript.unlink()
                removed = True
        except OSError:
            failed = True
            logger.warning("an old transcript could not be removed", exc_info=True)
        outputs = directory / _OUTPUTS_NAME
        try:
            if outputs.is_dir() and not outputs.is_symlink():
                shutil.rmtree(outputs)
                removed = True
        except OSError:
            failed = True
            logger.warning("old tool output could not be removed", exc_info=True)
        if failed:
            return _NOT_REMOVED
        return _REMOVED if removed else _NOTHING

    async def _clean_one(self, commission: Commission, until: float) -> str:
        """Clean the session of one commission the query found, if it is still to be cleaned.

        Held against being taken up again for as long as its files are
        removed, and looked up again once it is held: a commission that was
        taken up between the query and now is at work on that very transcript.
        """
        if self._hold_session is not None and not self._hold_session(commission.id):
            # In use, or on its way to be: it comes round again once it has ended and aged.
            return _NOTHING
        try:
            current = await self._ledger.get(commission.id)
            if current is None or current.status not in _CLEANABLE or current.updated_at > until:
                return _NOTHING
            directory = await asyncio.to_thread(self._session_dir_of, current)
            return await asyncio.to_thread(self._clean_session, Path(directory), current.id)
        except (OSError, ValueError):
            logger.warning("the session of an old commission could not be looked at", exc_info=True)
            return _NOT_REMOVED
        finally:
            if self._release_session is not None:
                self._release_session(commission.id)

    async def _clean_sessions(self, now: float, state: dict[str, Any]) -> None:
        """Remove what was said in sessions whose commission ended long ago."""
        from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS
        from nekoclaw.host.ledger import _commission

        if self._session_dir_of is None:
            return
        stored = state.get("sessions_cleaned_until")
        after = float(stored) if isinstance(stored, (int, float)) else 0.0
        until = now - SPACE_TRANSCRIPT_KEPT_SECONDS
        # Looked at is not cleaned. A session whose files could not be
        # removed (held open by another program) is remembered by itself
        # and tried again every round, while the scan moves on: holding the
        # scan back at it would keep every later session waiting behind a
        # file that may never let go.
        remembered = state.get("sessions_to_retry")
        waiting = [str(item) for item in remembered if isinstance(item, str)] if isinstance(remembered, list) else []
        still: list[str] = []

        async def clean(commission: Commission) -> None:
            outcome = await self._clean_one(commission, until)
            if outcome == _REMOVED:
                await self._ledger.append_event(commission.id, "session_cleaned", {})
            elif outcome == _NOT_REMOVED:
                still.append(commission.id)

        for commission_id in waiting:
            commission = await self._ledger.get(commission_id)
            if commission is not None:
                await clean(commission)
        reached = after
        if until > after:
            rows = await self._ledger.run(
                lambda conn: [_commission(row) for row in self._ended_long_ago(conn, after, until)]
            )
            commissions = [item for item in rows if item is not None and item.id not in waiting]
            for commission in commissions:
                await clean(commission)
            # A full batch leaves the rest to the next round, which goes on where this one stopped.
            reached = rows[-1].updated_at if len(rows) >= _CLEAN_BATCH and rows[-1] is not None else until
        if reached != after or still != waiting:
            await self._space.write_state(sessions_cleaned_until=reached, sessions_to_retry=still[-_RETRY_KEPT:])
