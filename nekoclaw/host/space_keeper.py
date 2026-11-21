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
  a plugin entry did, nor one a task agent started for itself: what that
  one did is told in the review of the commission that started it, its
  steps among the steps of its parent, and what it filed is its parent's.
  Reviews can be switched off in the settings; nothing of this paragraph
  happens then.
* then, at most every few hours, **tidies**: archives projects nothing was
  commissioned for in a month (one model call for the summary), removes what
  was said in sessions that ended a month ago (never a working directory),
  and makes the reviews that are due late. Last, it has a model look over
  the entries of a layer (the space as a whole, or one project) in which an
  entry was added or changed since it was last looked over, one call a
  layer: the model names entries that say the same thing, have gone out of
  date, are easily confused, or say "yesterday", and the space makes each
  change that holds (``TaskAgentSpace.tidy_entries``).

None of it is on the way of anything: it starts after the report to her has
been stored, holds no task agent place, and a failure is recorded and not
tried again. Nothing runs at startup; without a commission ending, nothing
runs at all.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nekoclaw.host import call_audit
from nekoclaw.host.decision import TIER_ARCHIVE, TIER_CONSOLIDATE, TIER_REVIEW
from nekoclaw.host.ledger import ACTIVE_STATUSES, ENDED_STATUSES, Commission, Ledger, LedgerEvent
from nekoclaw.host.paths import DEFAULT_PROJECT, SYSTEM_SNAPSHOT_NAME, TRANSCRIPT_NAME, safe_segment
from nekoclaw.host.space import ENTRY_EXPIRED, ENTRY_PENDING, ENTRY_TYPES, Layer, Project, SpaceError, TaskAgentSpace

logger = logging.getLogger(__name__)

# Statuses in which a commission is over for good, or left as it is.
_REVIEWED_AT_ONCE = frozenset({"done", "cancelled"})
_REVIEWABLE = frozenset({"done", "cancelled", "failed"})
_OUTPUTS_NAME = "outputs"
# What became of one session that was looked at. ``_SKIPPED``: left alone
# because a commission that is worked on in it is still under way (to the
# scan the same as nothing to remove; to whoever asked for this one session
# to be cleared, not).
_REMOVED, _NOTHING, _NOT_REMOVED, _SKIPPED = "removed", "nothing", "not_removed", "skipped"
_EVENT_PAGE = 500
_CLEAN_BATCH = 200
# Reviews that had a model write: the ones a round of tidying makes so many of.
_MODEL_WROTE = frozenset({"written", "failed"})
# Sorts after every commission id (ids are hex digits).
_AFTER_EVERY_ID = "~"
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


# Where a sentence ends: its mark, and what closes a quotation or a bracket
# after it. A full stop counts when no letter or digit follows it, so not the
# one inside a number or a file name.
_SENTENCE_END = re.compile(r"""(?:[。！？!?…]+|\.+(?![0-9A-Za-z_]))["'”’」』）)\]]*""")
# Put at the end of a text that was cut where no sentence ends, and the
# tokens left free for it.
_CUT_MARK = "…"
_CUT_MARK_ROOM = 2


def _whole_sentences(text: Any, max_tokens: int) -> str:
    """Return a text within a cap on its tokens; one that is cut ends where a sentence ends.

    What is kept of a review stays in the space and is read by later task
    agents. Cut off in the middle of a sentence it would say something its
    writer did not, and nothing would show that it was cut. A text in whose
    second half no sentence ends is cut where the cap falls, and marked.
    """
    from utils.tokenize import count_tokens, truncate_to_tokens

    whole = str(text or "").strip()
    if count_tokens(whole) <= max_tokens:
        return whole
    cut = truncate_to_tokens(whole, max_tokens)
    # As far as the two agree: the last character of what was cut may be half of one.
    limit = next((at for at, (one, other) in enumerate(zip(cut, whole, strict=False)) if one != other), len(cut))
    end = 0
    for match in _SENTENCE_END.finditer(whole):
        if match.end() > limit:
            break
        end = match.end()
    if end * 2 >= limit > 0:
        return whole[:end]
    return truncate_to_tokens(whole, max(max_tokens - _CUT_MARK_ROOM, 0)).rstrip() + _CUT_MARK


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


def _salvage(raw: str, text_key: str, more: tuple[str, ...] = ()) -> dict[str, Any] | None:
    """Return what is whole of an answer that was cut off, or None when its text is not.

    An answer that reaches the cap on what the model may write ends in the
    middle of the object, and is no JSON. Its text (``text_key``) comes
    first and is usually whole; so are the entries before the one that was
    being written. Kept, they are worth more than nothing, and it is the
    commissions with the most to say whose answers are cut off.
    """
    text = str(raw or "")
    decoder = json.JSONDecoder()

    def string_of(key: str) -> str | None:
        found = re.search(rf'"{re.escape(key)}"\s*:\s*(?=")', text)
        if found is None:
            return None
        try:
            value, _end = decoder.raw_decode(text, found.end())
        except ValueError:
            return None
        return value if isinstance(value, str) else None

    main = string_of(text_key)
    if main is None or not main.strip():
        return None
    data: dict[str, Any] = {text_key: main, "entries": _whole_items(text, "entries") or []}
    for key in more:
        value = string_of(key)
        if value is not None:
            data[key] = value
    return data


def _whole_items(text: str, key: str) -> list[dict[str, Any]] | None:
    """Return the objects of the list ``key`` of an answer that may be cut off, as far as they are whole.

    None when the answer has no such list.
    """
    listed = re.search(rf'"{re.escape(key)}"\s*:\s*\[', text)
    if listed is None:
        return None
    decoder = json.JSONDecoder()
    items: list[dict[str, Any]] = []
    position = listed.end()
    while position < len(text):
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
        if position >= len(text) or text[position] != "{":
            break
        try:
            item, position = decoder.raw_decode(text, position)
        except ValueError:
            break
        if isinstance(item, dict):
            items.append(item)
    return items


# The changes a model may ask for when it looks over a layer, and the fields
# of each that name entries (``TaskAgentSpace.tidy_entries``).
_CHANGES: dict[str, tuple[str, ...]] = {
    "merge": ("from", "into"),
    "outdated": ("entry", "by"),
    "retitle": ("entry", "apart_from"),
    "date": ("entry",),
}


def _changes_of(raw: str) -> list[dict[str, Any]] | None:
    """Return the changes a model asked for when it looked over a layer; None when it answered with no list of them.

    Of an answer that was cut off at the cap on what the model may
    write, the changes that are whole.
    """
    data = _parse_object(raw)
    if data is not None:
        listed = data.get("operations")
        return [item for item in listed if isinstance(item, dict)] if isinstance(listed, list) else None
    return _whole_items(str(raw or ""), "operations")


def _named(change: dict[str, Any], handles: dict[str, str]) -> dict[str, Any] | None:
    """Return a change with the entries it names by the ids of their files; None when it names one not shown.

    The model is shown the entries as ``n1``, ``n2``, ...: it names no
    file, and cannot name one it was not shown.
    """
    kind = str(change.get("op") or "").strip().lower()
    fields = _CHANGES.get(kind)
    if fields is None:
        return None
    named: dict[str, Any] = {**change, "op": kind}
    for field_name in fields:
        entry_id = handles.get(str(change.get(field_name) or "").strip())
        if entry_id is None:
            return None
        named[field_name] = entry_id
    return named


def _layer_key(project: str | None) -> str:
    """Return how a layer is kept in what the space remembers about tidying: empty for the space as a whole."""
    return "" if project is None else project


# Cap on one line of the list of entries a model looks over: a title and a
# description at their caps, with what is said of how it stands.
_LIST_LINE_MAX_TOKENS = 100


def _consolidate_messages(
    look: Layer, changed: list[str], now: float
) -> tuple[list[dict[str, str]], dict[str, str], set[str]]:
    """Return what the model is sent to look over a layer, the entries it may name, and the ones it was shown whole. Blocking.

    The model names entries by ``n1``, ``n2``, ... Every entry the host
    made that has not expired is listed, one line each, and the ones the
    user wrote, which it may not name. When they do not all fit, the new
    and changed ones go first, then the pending ones, then the newest.
    The full texts are of the new and changed ones, then of the pending
    ones, as many as fit. Shown whole is one whose text was given, and
    one that is listed and no news: a new or changed one whose text did
    not fit is news to the next round as well.
    """
    from config.nekoclaw_settings import (
        SPACE_CONSOLIDATE_INPUT_MAX_TOKENS,
        SPACE_CONSOLIDATE_LIST_MAX_TOKENS,
        SPACE_CONSOLIDATE_MAX_OPERATIONS,
        SPACE_CONSOLIDATE_TEXT_MAX_TOKENS,
        SPACE_CONSOLIDATE_TEXTS_MAX_TOKENS,
    )
    from config.prompts import prompts_nekoclaw_space as texts
    from nekoclaw.host.space import _date
    from utils.tokenize import count_tokens, truncate_to_tokens

    nothing = texts.TASK_AGENT_REVIEW_NOTHING
    fresh = set(changed)
    hosts = [entry for entry in look.entries if entry.id in look.made and entry.status != ENTRY_EXPIRED]
    # The newest first, then the pending ones before the rest, then the new and changed ones before all.
    hosts.sort(key=lambda entry: (entry.updated, entry.created), reverse=True)
    hosts.sort(key=lambda entry: (entry.id not in fresh, entry.status != ENTRY_PENDING))
    handles: dict[str, str] = {}
    lines: dict[str, str] = {}
    used = 0
    for entry in hosts:
        handle = f"n{len(handles) + 1}"
        about = f" — {entry.description}" if entry.description else ""
        line = _cut(
            f"- {handle}: {entry.title}{about} ({entry.type}; {entry.confidence or 'not said how it is known'}; "
            f"seen {entry.seen}; {entry.created} to {entry.updated})",
            _LIST_LINE_MAX_TOKENS,
        )
        cost = count_tokens(line) + 1
        if used + cost > SPACE_CONSOLIDATE_LIST_MAX_TOKENS:
            break
        used += cost
        handles[handle] = entry.id
        lines[entry.id] = line
    users: list[str] = []
    for entry in look.entries:
        if entry.id in look.made:
            continue
        about = f" — {entry.description}" if entry.description else ""
        line = _cut(f"- {entry.title}{about}", _LIST_LINE_MAX_TOKENS)
        cost = count_tokens(line) + 1
        if used + cost > SPACE_CONSOLIDATE_LIST_MAX_TOKENS:
            break
        used += cost
        users.append(line)
    shown = [entry for entry in hosts if entry.id in lines]
    handle_of = {entry_id: handle for handle, entry_id in handles.items()}
    full: list[str] = []
    # What the model is shown of each entry as much as there is to see:
    # its full text, or its line where it is no news.
    seen = {entry.id for entry in shown if entry.id not in fresh}
    used = 0
    for entry in shown:
        if entry.id not in fresh and entry.status != ENTRY_PENDING:
            continue
        block = (
            f"[{handle_of[entry.id]}] {entry.title} (created {entry.created}, updated {entry.updated}):\n"
            f"{truncate_to_tokens(entry.content, SPACE_CONSOLIDATE_TEXT_MAX_TOKENS)}"
        )
        cost = count_tokens(block) + 2
        if used + cost > SPACE_CONSOLIDATE_TEXTS_MAX_TOKENS:
            continue
        used += cost
        full.append(block)
        seen.add(entry.id)
    system = texts.SPACE_CONSOLIDATE_SYSTEM_PROMPT.format(max_operations=SPACE_CONSOLIDATE_MAX_OPERATIONS)
    layer = (
        texts.SPACE_CONSOLIDATE_LAYER_GLOBAL
        if look.project is None
        else texts.SPACE_CONSOLIDATE_LAYER_PROJECT.format(project=look.project)
    )
    user = texts.SPACE_CONSOLIDATE_USER_TEMPLATE.format(
        today=_date(now),
        layer=layer,
        active="\n".join(lines[entry.id] for entry in shown if entry.status != ENTRY_PENDING) or nothing,
        pending="\n".join(lines[entry.id] for entry in shown if entry.status == ENTRY_PENDING) or nothing,
        user="\n".join(users) or nothing,
        texts="\n\n".join(full) or nothing,
    )
    # The parts have caps of their own that add up to less; this is what holds whatever they are.
    room = SPACE_CONSOLIDATE_INPUT_MAX_TOKENS - count_tokens(system)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": truncate_to_tokens(user, max(room, 0))},
    ]
    return messages, handles, seen


def tool_calls(events: list[LedgerEvent]) -> int:
    """Return how many tool calls of a commission were work (not reports, lessons or the todo list)."""
    from config.nekoclaw_settings import TASK_AGENT_REVIEW_UNCOUNTED_TOOLS

    return sum(
        1
        for event in events
        if event.type == "tool_start"
        and str(event.payload.get("tool_name") or "") not in TASK_AGENT_REVIEW_UNCOUNTED_TOOLS
    )


def account(
    events: list[LedgerEvent],
    max_tokens: int,
    line_max_tokens: int,
    children: dict[str, list[LedgerEvent]] | None = None,
) -> str:
    """Return what a task agent did, one line per step, within ``max_tokens``.

    Of a longer account the beginning (a third of the room) and the end are
    kept: how it set out, and how it ended. ``children`` are the events of
    the task agents it started, by commission: the steps of each follow the
    line that says it was started, marked as its own.
    """
    from config.prompts.prompts_nekoclaw_space import TASK_AGENT_REVIEW_PROCESS_GAP
    from utils.tokenize import count_tokens

    lines = _steps(events, line_max_tokens, children or {})
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


def _steps(events: list[LedgerEvent], line_max_tokens: int, children: dict[str, list[LedgerEvent]]) -> list[str]:
    """Return the steps of one commission, one line each; see ``account``."""
    from config.nekoclaw_settings import TASK_AGENT_REVIEW_UNCOUNTED_TOOLS

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
            if name in TASK_AGENT_REVIEW_UNCOUNTED_TOOLS:
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
            name = payload.get("name")
            lines.append(f"started task agent {name}")
            # What it did, where it was started. One it started in turn is
            # only named: a task agent's task agents are its own business.
            for line in _steps(children.get(str(payload.get("commission_id") or ""), []), line_max_tokens, {}):
                lines.append(f"  [{name}] {line}")
        elif kind == "capability_denied":
            lines.append(f"denied: {_cut(payload.get('capability'), half)}")
    flush_said()
    return lines


class SpaceKeeper:
    """Reviews finished commissions and tidies the space. See the module docstring.

    ``llm`` is a ``DecisionLLM`` that knows ``TIER_REVIEW``,
    ``TIER_ARCHIVE`` and ``TIER_CONSOLIDATE``; without one nothing is
    reviewed, projects are archived without a summary, and no layer is
    looked over. ``session_dir_of`` returns where a
    session is kept (blocking; ``TaskAgentManager.session_dir_of``).
    ``hold_session`` and ``release_session`` are the manager's: while a
    commission is held for its review, it is not taken up again, and one
    that is in use is not held. ``hold_files`` and ``release_files`` are
    the manager's too, and about a session: while its files are held for
    cleaning, no commission is started in it, and a session in use is not
    held. ``forget_dsh_session`` removes what dsh kept of the session a
    session directory belongs to (blocking;
    ``DshInstallation.forget_session``): for a session that ran in dsh,
    what was said is there and not in a transcript.
    """

    def __init__(
        self,
        *,
        space: TaskAgentSpace,
        ledger: Ledger,
        llm: Any = None,
        session_dir_of: Callable[[Any], Path] | None = None,
        hold_session: Callable[[str], bool] | None = None,
        release_session: Callable[[str], None] | None = None,
        hold_files: Callable[[str], bool] | None = None,
        release_files: Callable[[str], None] | None = None,
        forget_dsh_session: Callable[[Path], bool] | None = None,
        clock: Callable[[], float] = time.time,
        review_delay: float | None = None,
    ) -> None:
        self._space = space
        self._ledger = ledger
        self._llm = llm
        self._session_dir_of = session_dir_of
        self._hold_session = hold_session
        self._release_session = release_session
        self._hold_files = hold_files
        self._release_files = release_files
        self._forget_dsh_session = forget_dsh_session
        self._clock = clock
        self._review_delay = review_delay
        # From when on commissions are looked after: the first round of
        # tidying ever takes this, not its own time, which is a moment after
        # the end of the commission that started it.
        self._watching_since = clock()
        self._tasks: set[asyncio.Task[Any]] = set()
        # What closing did not wait for to its end; kept referenced until done.
        self._lingering: set[asyncio.Task[Any]] = set()
        # One model call of the space at a time.
        self._working: asyncio.Lock | None = None
        self._tidying = False
        self._last_upkeep: float | None = None
        self._closed = False
        # Projects whose record could not be written when a summary that
        # came to nothing was to be counted. Left alone until the host is
        # started again: asked about in every round otherwise, with nothing
        # to say when to stop.
        self._archive_unasked: set[str] = set()
        # What a model wrote for a project that could not be moved then:
        # (the last_commission_at and the number of commissions it was
        # written at, the summary, the line). The space keeps that in the
        # project's record; this is for when the record cannot be written
        # either, so that the model is still asked once and not once a round.
        self._summaries: dict[str, tuple[float, int, str, str]] = {}

    # -- listening --------------------------------------------------------

    def watch(self, commission: Commission, previous: str | None) -> None:
        """Note a change of a commission; a ``Ledger.subscribe`` listener.

        Runs on the event loop after the change is stored. It only starts a
        task: nothing here is awaited by whoever changed the commission.
        """
        from config.nekoclaw_settings import TASK_AGENT_REVIEW_ENABLED

        if self._closed or previous is None or commission.parent_id is not None:
            return
        if commission.status not in ENDED_STATUSES:
            return
        review = TASK_AGENT_REVIEW_ENABLED and reviewable(commission) and commission.status in _REVIEWED_AT_ONCE
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
            if self._closed:
                # The host began to shut down meanwhile.
                return
            if review:
                await self.review(commission_id)
            await self.tidy()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - background work; the next commission tries again
            logger.exception("work for the task agent space after a commission failed")

    async def stand_down(self) -> None:
        """Start nothing more. The first step of shutting the host down; ``aclose`` is a later one.

        Shutting down ends the commissions that are at work, as failed, and
        every one of those ends would start work here that the rest of the
        shutdown then cuts off: a round of tidying that counts as made and
        did nothing, with a model call sent for it.
        """
        self._closed = True

    async def settle(self) -> None:
        """Wait until everything started so far is done. For tests."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        """Stop what is under way. A review cut off here is made by a later round of tidying.

        Waits ``SPACE_KEEPER_CLOSE_WAIT_SECONDS`` at most. What is not done
        by then is a thread still removing the files of an old session; it
        goes on by itself, and the session stays claimed until it is done.
        """
        from config.nekoclaw_settings import SPACE_KEEPER_CLOSE_WAIT_SECONDS

        self._closed = True
        tasks, self._tasks = list(self._tasks), set()
        for task in tasks:
            task.cancel()
        if not tasks:
            return
        _done, pending = await asyncio.wait(tasks, timeout=SPACE_KEEPER_CLOSE_WAIT_SECONDS)
        for task in pending:
            self._lingering.add(task)
            task.add_done_callback(self._lingering.discard)
        if pending:
            logger.warning("closing the task agent space did not wait for %d task(s) still removing files", len(pending))

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
        from config.nekoclaw_settings import TASK_AGENT_REVIEW_ENABLED, TASK_AGENT_REVIEW_MIN_TOOL_CALLS

        if not TASK_AGENT_REVIEW_ENABLED:
            return "not_due"
        async with self._lock():
            commission = await self._ledger.get(commission_id)
            if commission is None or not reviewable(commission) or commission.status not in _REVIEWABLE:
                return "not_due"
            events = await self._events(commission_id)
            if any(event.type == "review" for event in events):
                return "already"
            # The task agents it started are part of it: what they did
            # counts as its work and is told in its review.
            children = {
                child.id: await self._events(child.id) for child in await self._ledger.select(parent_id=commission_id)
            }
            calls = tool_calls(events) + sum(tool_calls(theirs) for theirs in children.values())
            if calls < TASK_AGENT_REVIEW_MIN_TOOL_CALLS:

                async def skipped() -> dict[str, Any]:
                    return {"status": "skipped", "reason": "short", "tool_calls": calls}

                return await self._conclude(commission, skipped)
            if self._llm is None:
                return "no_model"
            data: dict[str, Any] | None = None
            problem = ""
            try:
                raw = await self._llm.complete(TIER_REVIEW, await self._review_messages(commission, events, children))
                data = _parse_object(raw)
                if data is None:
                    # Cut off at the cap on what the model may write, or not
                    # the object that was asked for. What is whole of it is
                    # kept: the commission is not reviewed again.
                    data = _salvage(raw, "review")
                    logger.warning(
                        "the review of commission %s could not be read as it came (%d characters); %s",
                        commission_id,
                        len(str(raw)),
                        "what was whole of it is kept" if data is not None else "it is recorded as failed",
                    )
                    if data is None:
                        problem = "unreadable"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - recorded, and not tried again
                logger.warning("reviewing commission %s failed (%s)", commission_id, type(exc).__name__)
                problem = type(exc).__name__

            async def reviewed() -> dict[str, Any]:
                if problem or data is None:
                    return {"status": "failed", "reason": problem or "unreadable"}
                try:
                    return await self._keep_review(commission, data, tuple(children))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - recorded, and not tried again
                    logger.warning("keeping the review of %s failed (%s)", commission_id, type(exc).__name__)
                    return {"status": "failed", "reason": type(exc).__name__}

            return await self._conclude(commission, reviewed)

    async def _conclude(self, commission: Commission, outcome_of: Callable[[], Any]) -> str:
        """Write down what became of a commission's review, if the commission is still as it was read.

        Whatever the conclusion (written, failed, skipped), it is final: a
        commission with a ``review`` event is not reviewed again. A
        commission that is done or was stopped stays so. A failed one can
        be taken up again at any moment, so it is held against that while
        the conclusion is written, and looked up again once it is held: one
        that was taken up, or ended anew, since it was read gets nothing
        written (``stale``), and the end it comes to is reviewed.
        """
        holding = False
        if commission.status not in _REVIEWED_AT_ONCE and self._hold_session is not None:
            if not self._hold_session(commission.id):
                return "stale"
            holding = True
        try:
            if commission.status not in _REVIEWED_AT_ONCE:
                current = await self._ledger.get(commission.id)
                same = current is not None and (current.status, current.updated_at) == (
                    commission.status,
                    commission.updated_at,
                )
                if not same:
                    return "stale"
            outcome = await outcome_of()
            await self._ledger.append_event(commission.id, "review", outcome)
            return str(outcome["status"])
        finally:
            if holding and self._release_session is not None:
                self._release_session(commission.id)

    @staticmethod
    def _entries_allowed(commission: Commission) -> int:
        """Return how many entries the review of a commission may file."""
        from config.nekoclaw_settings import TASK_AGENT_REVIEW_ENTRIES_WHEN_STOPPED, TASK_AGENT_REVIEW_MAX_ENTRIES

        if commission.status == "cancelled":
            return min(TASK_AGENT_REVIEW_ENTRIES_WHEN_STOPPED, TASK_AGENT_REVIEW_MAX_ENTRIES)
        return TASK_AGENT_REVIEW_MAX_ENTRIES

    async def _review_messages(
        self,
        commission: Commission,
        events: list[LedgerEvent],
        children: dict[str, list[LedgerEvent]] | None = None,
    ) -> list[dict[str, str]]:
        from config.nekoclaw_settings import (
            DISPATCH_TEXT_MAX_TOKENS,
            DISPATCH_USER_TEXT_MAX_TOKENS,
            SPACE_ENTRY_TITLE_MAX_TOKENS,
            TASK_AGENT_REVIEW_FILED_MAX_TOKENS,
            TASK_AGENT_REVIEW_INDEX_MAX_TOKENS,
            TASK_AGENT_REVIEW_INPUT_MAX_TOKENS,
            TASK_AGENT_REVIEW_PROCESS_LINE_MAX_TOKENS,
            TASK_AGENT_REVIEW_PROCESS_MAX_TOKENS,
            TASK_AGENT_REVIEW_PROJECTS_MAX_TOKENS,
            TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS,
        )
        from config.prompts import prompts_nekoclaw_space as texts
        from utils.tokenize import count_tokens, truncate_to_tokens

        children = children or {}
        nothing = texts.TASK_AGENT_REVIEW_NOTHING
        project = commission.project or DEFAULT_PROJECT
        # What it and its task agents filed and the space kept. A note that
        # was not kept is not on file: the review may still make it one.
        filed = [
            f"- {_cut(event.payload.get('title'), SPACE_ENTRY_TITLE_MAX_TOKENS)}"
            for event in [*events, *(event for theirs in children.values() for event in theirs)]
            if event.type in ("lesson", "feedback") and event.payload.get("title") and event.payload.get("kept", True)
        ]
        # The ones in use only: the name of an archived project is not
        # taken from a review, so it is not offered to one.
        projects = [
            f"- {item.name}: {item.description}" if item.description else f"- {item.name}"
            for item in await self._space.list_projects(include_archived=False)
            if item.name != DEFAULT_PROJECT
        ]
        system = texts.TASK_AGENT_REVIEW_SYSTEM_PROMPT.format(
            language=_LANGUAGES.get(commission.lang or "", "the language of the commission"),
            max_entries=self._entries_allowed(commission),
        )
        # Every step is cut by tokens: off the event loop, which a long
        # commission would otherwise hold while she is waiting to dispatch.
        process = await asyncio.to_thread(
            account, events, TASK_AGENT_REVIEW_PROCESS_MAX_TOKENS, TASK_AGENT_REVIEW_PROCESS_LINE_MAX_TOKENS, children
        )
        user = texts.TASK_AGENT_REVIEW_USER_TEMPLATE.format(
            dispatch_text=truncate_to_tokens(commission.dispatch_text, DISPATCH_TEXT_MAX_TOKENS),
            user_text=truncate_to_tokens(commission.user_text or nothing, DISPATCH_USER_TEXT_MAX_TOKENS),
            status=commission.status,
            result=truncate_to_tokens(commission.result_summary or nothing, TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS),
            project=nothing if project == DEFAULT_PROJECT else project,
            process=process or nothing,
            filed=truncate_to_tokens("\n".join(filed), TASK_AGENT_REVIEW_FILED_MAX_TOKENS) or nothing,
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
        """Return the project a review is kept under, making the project it names when that is new.

        A commission that was filed under a project when it started stays
        there. One of ``_daily`` goes where the review says, if it names a
        project; a name that cannot be a directory name is ignored. So is
        the name of an archived project: what a model answered does not
        bring a project back from the archive, only naming it in a
        commission does.
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
        # Whether the name is an archived project's is looked at by the
        # space, under its lock: a listing read here could be of a moment
        # before the project was archived. A commission the user stopped
        # joins a project that is there and makes none: it was often stopped
        # because it went wrong, and the line a new project gets in the index
        # of all projects would be what a model made of that.
        project = await self._space.open_project(
            name,
            description=str(data.get("project_description") or ""),
            workdir=commission.workdir,
            at=commission.ended_at,
            unless_archived=True,
            unless_new=commission.status == "cancelled",
        )
        return project.name if project is not None else current

    async def _keep_review(
        self, commission: Commission, data: dict[str, Any], children: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Keep what the model answered: the review, its entries, the project. Returns the ``review`` event.

        ``children`` are the commissions of the task agents it started.
        """
        from config.nekoclaw_settings import (
            TASK_AGENT_REVIEW_ENTRY_CONTENT_MAX_TOKENS,
            TASK_AGENT_REVIEW_TEXT_MAX_TOKENS,
        )
        text = _whole_sentences(data.get("review"), TASK_AGENT_REVIEW_TEXT_MAX_TOKENS)
        if not text:
            return {"status": "failed", "reason": "empty"}
        current = commission.project or DEFAULT_PROJECT
        try:
            project = await self._target_project(commission, data)
        except (SpaceError, OSError, ValueError) as exc:
            # The project it names cannot be opened (its record cannot be
            # read, say). The review is not lost over that: it is kept
            # where the commission is.
            logger.warning("the project a review names could not be opened (%s); it stays where it is", type(exc).__name__)
            project = current
        refiled: list[str] = []
        if project != current:
            # It belongs to a project after all. What it and its task agents
            # filed while it was one of ``_daily`` goes with it: the model
            # was told not to repeat that, so it would be in no index of the
            # project.
            try:
                refiled = await self._space.refile_entries(commission.id, current, project, also=children)
            except (SpaceError, OSError) as exc:
                logger.warning("what a commission filed could not be moved to its project (%s)", type(exc).__name__)
        kept: list[str] = []
        # The pending entries what it made went into, by where they are.
        pending: list[tuple[str | None, str]] = []
        entries = data.get("entries")
        bases = await self._space.evidence_bases(commission.workdir, project)
        for item in (entries if isinstance(entries, list) else [])[: self._entries_allowed(commission)]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "").strip().lower()
            everywhere = str(item.get("scope") or "").strip().lower() == "global" and kind != "progress"
            waits: list[str] = []
            try:
                # Judged like a note of a task agent: the model read an
                # account and checked nothing, so what it made for the
                # project takes effect when a file it gives, or one its
                # words name, exists where the commission worked, and waits
                # to be seen again otherwise. A global one waits whatever
                # files it names, which are kept for reference only
                # (``propose_entry``).
                entry, _created = await self._space.propose_entry(
                    None if everywhere else project,
                    title=str(item.get("title") or ""),
                    content=_whole_sentences(item.get("content"), TASK_AGENT_REVIEW_ENTRY_CONTENT_MAX_TOKENS),
                    kind=kind if kind in ENTRY_TYPES else "lesson",
                    description=str(item.get("description") or ""),
                    source=commission.id,
                    observer=commission.parent_id or commission.id,
                    bases=bases,
                    evidence=item.get("evidence") or (),
                    waits=waits,
                )
            except (SpaceError, OSError) as exc:
                logger.warning("an entry of a review could not be kept (%s)", type(exc).__name__)
                continue
            if entry is None:
                pending.extend((None if everywhere else project, name) for name in waits)
                continue
            kept.append(entry.id if everywhere else f"{project}/{entry.id}")
        # As it stands now: a later entry of the same review may have had
        # an earlier one take effect (the same note, with a file this time).
        in_effect = set(kept)
        maybe = {
            (layer, name) for layer, name in pending if (name if layer is None else f"{layer}/{name}") not in in_effect
        }
        await self._space.write_review(project, commission, text, entries=tuple(kept))
        # Or it went into another entry, and is no file any more: only
        # what still waits is counted. One by one, by its own file: one
        # that is known to wait no longer is left out, and one that
        # cannot be read just now is counted as it was noted. Counted
        # once the review is written: writing it waits its turn, and a
        # guess may take effect while it does.
        still: set[tuple[str | None, str]] = set()
        for layer in {layer for layer, _name in maybe}:
            names = {name for at, name in maybe if at == layer}
            try:
                there = await self._space.still_pending(layer, names)
            except (SpaceError, OSError):
                there = names
            still.update((layer, name) for name in there)
        waiting = len(still)
        outcome: dict[str, Any] = {"status": "written", "project": project, "entries": kept}
        if waiting:
            # Made by the review and not in effect yet: pending entries, in no index and not among its entries.
            outcome["waiting"] = waiting
        if refiled:
            outcome["refiled"] = [f"{project}/{entry_id}" for entry_id in refiled]
        return outcome

    def _unreviewed(self, conn: sqlite3.Connection, since: float, before: float, limit: int) -> list[str]:
        """Return the oldest commissions that ended between two times and have no review. Blocking.

        It reads every commission once and, of each one that ended between
        the two times, its events until a review is found. That is as much
        as a round of tidying reads anyway, it is done once a round and not
        on the event loop, and the times are no further apart than sessions
        are kept. So it is left without an index of its own.
        """
        rows = conn.execute(
            "SELECT c.id FROM commissions c WHERE c.route = 'task_agent' AND c.parent_id IS NULL "
            "AND c.status IN ('done', 'failed', 'cancelled') AND c.updated_at >= ? AND c.updated_at <= ? "
            "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.commission_id = c.id AND e.type = 'review') "
            "ORDER BY c.updated_at ASC LIMIT ?",
            (since, before, limit),
        ).fetchall()
        return [str(row["id"]) for row in rows]

    async def _late_reviews(self, now: float, since: float) -> None:
        """Review commissions that ended a day ago and have no review: failed ones, and ones a shutdown cut off."""
        from config.nekoclaw_settings import (
            SPACE_TRANSCRIPT_KEPT_SECONDS,
            TASK_AGENT_REVIEW_ENABLED,
            TASK_AGENT_REVIEW_FAILED_AFTER_SECONDS,
            TASK_AGENT_REVIEWS_LOOKED_AT_PER_UPKEEP,
            TASK_AGENT_REVIEWS_PER_UPKEEP,
        )

        if self._llm is None or not TASK_AGENT_REVIEW_ENABLED:
            return
        earliest = max(since, now - SPACE_TRANSCRIPT_KEPT_SECONDS)
        before = now - TASK_AGENT_REVIEW_FAILED_AFTER_SECONDS
        if before < earliest:
            return
        # The oldest first: they are the ones that would otherwise be too
        # old for a review by the next round. A round looks at more of them
        # than it has a model write about, because one that is too short to
        # review is done with at no cost; taken with the newest first and
        # counted alike, a day on which many commissions failed at once
        # would keep the ones worth a review waiting until they are too old.
        due = await self._ledger.run(
            lambda conn: self._unreviewed(conn, earliest, before, TASK_AGENT_REVIEWS_LOOKED_AT_PER_UPKEEP)
        )
        written = 0
        for commission_id in due:
            if written >= TASK_AGENT_REVIEWS_PER_UPKEEP:
                break
            if await self.review(commission_id) in _MODEL_WROTE:
                written += 1

    # -- tidying ----------------------------------------------------------

    async def tidy(self, *, force: bool = False) -> bool:
        """Run one round of tidying if one is due. Returns whether it ran."""
        from config.nekoclaw_settings import SPACE_UPKEEP_INTERVAL_SECONDS

        if self._closed or self._tidying:
            return False
        self._tidying = True
        try:
            now = self._clock()
            try:
                state = await self._space.read_state()
            except OSError:
                # Not begun from nothing: that would forget how far the
                # cleaning got and from when on commissions are reviewed.
                logger.warning("what the task agent space keeps about its upkeep cannot be read; no tidying this time")
                return False
            if self._last_upkeep is None:
                stored = state.get("upkeep_at")
                self._last_upkeep = float(stored) if isinstance(stored, (int, float)) else 0.0
            if self._last_upkeep > now:
                # The clock was ahead when that was written and has been set
                # back since. Waited out, nothing would be tidied until the
                # time catches up with it.
                self._last_upkeep = 0.0
            if not force and now - self._last_upkeep < SPACE_UPKEEP_INTERVAL_SECONDS:
                return False
            since = state.get("since")
            if not isinstance(since, (int, float)):
                # The first round ever: commissions that ended before this
                # host was started are not reviewed after the fact. The ones
                # it saw end are, among them the one this round follows.
                since = self._watching_since
            try:
                await self._space.write_state(upkeep_at=now, since=since)
            except OSError:
                # Read a moment ago, not now. The round is not counted as
                # done: the next commission that ends starts one.
                logger.warning("what the task agent space keeps about its upkeep cannot be written; no tidying this time")
                return False
            self._last_upkeep = now
            for name, step in (
                # First, and with no model: what is archived and reviewed
                # after it reads indexes that no longer list what expired.
                ("call audit", lambda: self._ledger.run(lambda conn: call_audit.reclaim(conn, now))),
                ("evidence", self._check_evidence),
                ("late reviews", lambda: self._late_reviews(now, float(since))),
                ("archiving", lambda: self._archive_idle(now)),
                ("old sessions", lambda: self._clean_sessions(now, state)),
                # Last: it looks over what the steps before left.
                ("entries", lambda: self._consolidate(now, state)),
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

    async def _check_evidence(self) -> None:
        """Have the space look whether the files its entries rest on are still there; see ``TaskAgentSpace.check_evidence``."""
        outcome = await self._space.check_evidence()
        if any(outcome.values()):
            logger.info(
                "task agent space: %d entries expired, %d are in effect again, %d pending ones were removed",
                outcome.get("expired", 0),
                outcome.get("restored", 0),
                outcome.get("dropped", 0),
            )

    async def _archive_messages(self, project: Project) -> list[dict[str, str]]:
        from config.nekoclaw_settings import (
            SPACE_ARCHIVE_INDEX_MAX_TOKENS,
            SPACE_ARCHIVE_INPUT_MAX_TOKENS,
            SPACE_ARCHIVE_MAX_ENTRIES,
            SPACE_ARCHIVE_REVIEW_MAX_TOKENS,
            SPACE_ARCHIVE_REVIEWS,
            SPACE_ARCHIVE_REVIEWS_MAX_TOKENS,
            TASK_AGENT_REVIEW_INDEX_MAX_TOKENS,
        )
        from config.prompts import prompts_nekoclaw_space as texts
        from nekoclaw.host.space import _date
        from utils.tokenize import count_tokens, truncate_to_tokens

        nothing = texts.TASK_AGENT_REVIEW_NOTHING
        reviews = [
            f"- {review.title} ({review.status}): {_cut(review.content, SPACE_ARCHIVE_REVIEW_MAX_TOKENS)}"
            for review in await self._space.list_reviews(project.name, with_content=True, limit=SPACE_ARCHIVE_REVIEWS)
        ]
        system = texts.SPACE_ARCHIVE_SYSTEM_PROMPT.format(
            language="the language its index and reviews are written in",
            max_entries=SPACE_ARCHIVE_MAX_ENTRIES,
        )
        user = texts.SPACE_ARCHIVE_USER_TEMPLATE.format(
            name=project.name,
            description=project.description or nothing,
            workdir=project.workdir or nothing,
            commissions=project.commissions,
            last=_date(project.last_commission_at),
            index=truncate_to_tokens((await self._space.read_index(project.name)).strip(), SPACE_ARCHIVE_INDEX_MAX_TOKENS)
            or nothing,
            reviews=truncate_to_tokens("\n".join(reviews), SPACE_ARCHIVE_REVIEWS_MAX_TOKENS) or nothing,
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
                raw = await self._llm.complete(TIER_ARCHIVE, await self._archive_messages(project))
                # Of an answer that was cut off, what is whole is kept.
                data = _parse_object(raw) or _salvage(raw, "summary", ("line",))
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
            SPACE_ARCHIVE_MAX_ENTRIES,
            SPACE_ARCHIVE_SUMMARY_MAX_TOKENS,
            SPACE_ARCHIVES_PER_UPKEEP,
            SPACE_PROJECT_DESCRIPTION_MAX_TOKENS,
            SPACE_PROJECT_IDLE_SECONDS,
            TASK_AGENT_REVIEW_ENTRY_CONTENT_MAX_TOKENS,
        )
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
        # A round asks for so many summaries and moves so many projects. The
        # two are counted apart: a project that is asked about and then
        # cannot be moved has used a summary, not a move, and one whose
        # summary is there already uses neither until it moves, so it does
        # not keep the ones after it from their turn.
        asked = moved = 0
        for project in idle:
            if moved >= SPACE_ARCHIVES_PER_UPKEEP:
                break
            if project.name in in_use or project.name in self._archive_unasked:
                continue
            # A summary written in an earlier round, when the project could
            # not be moved (a file in it was held open): the space kept it
            # and uses it, so the model is asked once, not once a round.
            kept = project.kept_summary() is not None
            held = self._summaries.get(project.name)
            if held is not None and (kept or held[:2] != (project.last_commission_at, project.commissions)):
                # In the record after all, or the project was used since: a
                # commission filed late can leave the time as it was, so the
                # number of commissions is looked at as well.
                del self._summaries[project.name]
                held = None
            ask = (
                not kept
                and held is None
                and self._llm is not None
                and project.archive_attempts < SPACE_ARCHIVE_MAX_ATTEMPTS
            )
            if ask and asked >= SPACE_ARCHIVES_PER_UPKEEP:
                continue
            data = None
            if ask:
                asked += 1
                data = await self._summarize(project)
                if data is None:
                    try:
                        attempts = await self._space.note_archive_attempt(project.name)
                    except (SpaceError, OSError) as exc:
                        # This project's trouble is not the next one's.
                        logger.warning("an attempt to archive a project could not be counted (%s)", type(exc).__name__)
                        self._archive_unasked.add(project.name)
                        continue
                    if attempts < SPACE_ARCHIVE_MAX_ATTEMPTS:
                        # Left where it is; a later round asks again.
                        continue
            summary = _whole_sentences((data or {}).get("summary"), SPACE_ARCHIVE_SUMMARY_MAX_TOKENS)
            line = _cut((data or {}).get("line"), SPACE_PROJECT_DESCRIPTION_MAX_TOKENS)
            if held is not None:
                _written_at, _commissions, summary, line = held
            elif summary:
                self._summaries[project.name] = (project.last_commission_at, project.commissions, summary, line)
            # What the summary says holds beyond the project is filed when
            # the summary is written, whether or not the project then moves:
            # a later try uses the summary that was kept and asks nothing,
            # so nothing is filed twice and nothing the model gave is lost.
            entries = (data or {}).get("entries")
            for item in (entries if isinstance(entries, list) else [])[:SPACE_ARCHIVE_MAX_ENTRIES]:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("type") or "").strip().lower()
                try:
                    # Judged as what a review makes is; see ``_keep_review``.
                    # Nobody is named as having seen it: the summary is
                    # made from what the project's own commissions did,
                    # so it says again what one of them found and is no
                    # second sighting of it. It is global, so it waits
                    # whatever files it names, which are kept for
                    # reference only (``propose_entry``).
                    await self._space.propose_entry(
                        None,
                        title=str(item.get("title") or ""),
                        content=_whole_sentences(item.get("content"), TASK_AGENT_REVIEW_ENTRY_CONTENT_MAX_TOKENS),
                        kind=kind if kind in ENTRY_TYPES and kind != "progress" else "lesson",
                        description=str(item.get("description") or ""),
                        bases=[project.workdir] if project.workdir else [],
                        evidence=item.get("evidence") or (),
                    )
                except (SpaceError, OSError) as exc:
                    logger.warning("an entry of a project's summary could not be kept (%s)", type(exc).__name__)
            # The summary took a while. A commission taken up again meanwhile
            # shows in the ledger; one filed under the project meanwhile
            # shows in the project's own record, which the space looks at
            # under the same lock it moves the directory under.
            if project.name in await busy():
                continue
            try:
                archived = await self._space.archive_project(
                    project.name,
                    summary=summary,
                    description=line,
                    unless_touched_since=project.last_commission_at,
                    commissions=project.commissions,
                )
            except (SpaceError, OSError) as exc:
                # This project's trouble is not the next one's.
                logger.warning("archiving a project of the task agent space failed (%s)", type(exc).__name__)
                continue
            if archived is None:
                # In use after all, or it could not be moved; the space
                # kept the summary for the next try.
                continue
            moved += 1
            self._summaries.pop(project.name, None)
            logger.info("archived an idle project of the task agent space (summary: %s)", bool(summary) or kept)

    async def _consolidate(self, now: float, state: dict[str, Any]) -> None:
        """Have a model look over the layers in which an entry was added or changed since they were last looked over.

        A layer is looked over when it has ``SPACE_CONSOLIDATE_MIN_ENTRIES``
        entries the host made that have not expired, and one of those says
        something else than when it was last looked over (or was not there
        then): a new one, a pending one, one that was added to. One that
        only expired or came back has not changed what it says, and one
        that expired is not shown to the model at all. At most
        ``SPACE_CONSOLIDATE_LAYERS_PER_UPKEEP`` a round, the ones looked
        over longest ago first; the rest are looked over by a later round.

        What the entries said is remembered once a layer was looked over
        (``consolidated`` among what the space keeps about its upkeep): of
        each entry the model was shown, what it said then; of each one a
        change was made to, what it says after that, so the change is not
        taken for news by the next round. An entry that was not shown (the
        list was full) or that changed while the model looked keeps what
        was remembered of it before, and is news to the next round. So it
        is when the model's answer cannot be read: what it was shown is
        not asked about again. A layer the model cannot be asked about just
        now is asked about by a later round, after the other layers that
        wait had their turn.
        """
        from config.nekoclaw_settings import (
            SPACE_CONSOLIDATE_ENABLED,
            SPACE_CONSOLIDATE_LAYERS_PER_UPKEEP,
            SPACE_CONSOLIDATE_MIN_ENTRIES,
        )

        if self._llm is None or not SPACE_CONSOLIDATE_ENABLED:
            return
        layers = await self._space.tidy_layers()
        kept_before = state.get("consolidated")
        # Of the layers there are: an archived project is looked over no more.
        keys = {_layer_key(layer) for layer in layers}
        kept: dict[str, Any] = {
            key: value
            for key, value in (kept_before.items() if isinstance(kept_before, dict) else ())
            if key in keys and isinstance(value, dict)
        }
        due: list[tuple[float, int, Layer, list[str], dict[str, str]]] = []
        for layer in layers:
            try:
                # With no model: what earlier tidying could not finish.
                await self._space.settle_tidied(layer)
            except (SpaceError, OSError, ValueError) as exc:
                logger.warning("what earlier tidying left could not be finished (%s)", type(exc).__name__)
            look = await self._layer_or_none(layer)
            if look is None:
                continue
            live = sorted(entry.id for entry in look.entries if entry.id in look.made and entry.status != ENTRY_EXPIRED)
            if len(live) < SPACE_CONSOLIDATE_MIN_ENTRIES:
                continue
            record = kept.get(_layer_key(layer), {})
            said = record.get("said")
            before = {key: value for key, value in said.items() if isinstance(value, str)} if isinstance(said, dict) else {}
            changed = [entry_id for entry_id in live if before.get(entry_id) != look.said.get(entry_id)]
            if not changed:
                continue
            # When it was last looked over, or last tried: a layer the model
            # cannot be asked about does not keep the others from their turn.
            times = [record.get(name) for name in ("at", "tried")]
            at = max((float(value) for value in times if isinstance(value, (int, float))), default=0.0)
            due.append((at, len(due), look, changed, before))
        for _at, _order, look, changed, before in sorted(due, key=lambda item: item[:2])[:SPACE_CONSOLIDATE_LAYERS_PER_UPKEEP]:
            if self._closed:
                return
            key = _layer_key(look.project)
            seen = await self._look_over(look, changed, now)
            after = await self._layer_or_none(look.project) if seen is not None else None
            if seen is None or after is None:
                # Asked again by a later round, after the others had their turn.
                kept[key] = {**kept.get(key, {}), "tried": now}
            else:
                shown, written = seen
                remembered = {**before, **{entry_id: look.said[entry_id] for entry_id in shown if entry_id in look.said}}
                # What a change wrote, while the file still says just that:
                # one saved over since is news to the next round.
                remembered.update(
                    {
                        entry_id: after.said[entry_id]
                        for entry_id, mark in written.items()
                        if mark is not None and after.marks.get(entry_id) == mark
                    }
                )
                for entry_id, mark in written.items():
                    if mark is None:
                        # Merged into another and still there (its file could
                        # not be removed), or named by a change that failed
                        # half way: news to the next round, which makes the
                        # change once more.
                        remembered.pop(entry_id, None)
                kept[key] = {
                    "at": now,
                    # Of the entries that are there.
                    "said": {entry_id: value for entry_id, value in remembered.items() if entry_id in after.said},
                }
            try:
                await self._space.write_state(consolidated=kept)
            except OSError:
                logger.warning("what tidying looked over could not be kept; it is looked over again", exc_info=True)

    async def _layer_or_none(self, project: str | None) -> Layer | None:
        """Return a layer as tidying reads it (``TaskAgentSpace.read_layer``); None when it cannot be read.

        One layer that cannot be read (a project directory whose name is
        no name the space takes) does not keep the others from being
        looked over.
        """
        try:
            return await self._space.read_layer(project)
        except (SpaceError, OSError, ValueError) as exc:
            logger.warning("a layer of the task agent space could not be read for tidying (%s)", type(exc).__name__)
            return None

    async def _look_over(self, look: Layer, changed: list[str], now: float) -> tuple[set[str], dict[str, str | None]] | None:
        """Have the model look over one layer and make the changes it asks for that hold.

        Returns the entries the model was shown, and of the ones a change
        was made to the mark of what was written last (None for one that
        went, or was written again where that mark is not known); None
        when the model could not be asked. Each change is
        checked by the space, against the entries as they were shown to
        the model (``TaskAgentSpace.tidy_entries``); one that does not hold
        is not made, and why is logged.
        """
        from config.nekoclaw_settings import SPACE_CONSOLIDATE_MAX_OPERATIONS

        async with self._lock():
            messages, handles, shown = await asyncio.to_thread(_consolidate_messages, look, changed, now)
            try:
                raw = await self._llm.complete(TIER_CONSOLIDATE, messages)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - asked again by a later round
                logger.warning("looking over the entries of the task agent space failed (%s)", type(exc).__name__)
                return None
        written: dict[str, str | None] = {}
        changes = _changes_of(raw)
        if changes is None:
            logger.warning("what the model answered on looking over the entries could not be read (%d characters)", len(str(raw)))
            return shown, written
        marks = dict(look.marks)
        made = 0
        for change in changes[:SPACE_CONSOLIDATE_MAX_OPERATIONS]:
            named = _named(change, handles)
            if named is None:
                logger.info("tidying was asked for a change of no kind it makes, or of an entry it did not show")
                continue
            try:
                outcome, now_marks = await self._space.tidy_entries(look.project, named, marks)
            except (SpaceError, OSError) as exc:
                logger.warning("a change tidying was asked for could not be made (%s)", type(exc).__name__)
                # It may have been made in part (a file could not be written
                # half way): the entries it names are news to the next round.
                for field_name in _CHANGES[named["op"]]:
                    written[named[field_name]] = None
                    marks.pop(named[field_name], None)
                continue
            if outcome != "made":
                logger.info("tidying did not make a change (%s): %s", named["op"], outcome)
                continue
            made += 1
            written.update(now_marks)
            # The next change is checked against the entries as this one wrote them.
            for entry_id, mark in now_marks.items():
                if mark is None:
                    marks.pop(entry_id, None)
                else:
                    marks[entry_id] = mark
        logger.info(
            "task agent space: looked over %d entries, %d of %d changes made",
            len(shown),
            made,
            len(changes),
        )
        return shown, written

    def _closed_long_ago(
        self, conn: sqlite3.Connection, after: float, after_id: str, until: float
    ) -> list[sqlite3.Row]:
        """Return a batch of old sessions nothing is going on in that come after ``(after, after_id)``, in that order.

        A session is as old as the last change of any commission in it
        (``last_active_at``), so one that a commission was worked on in
        lately keeps everything said in it, also by the commissions before
        that one. Nothing is going on in it when none of its commissions is
        under way or blocked (a blocked one is taken up again where it
        stopped): told by the commissions, as it always was, and not by
        what the session's own status says. One that was cleared already
        has nothing left. The id is part of the order and of where the
        scan stands: with the time alone, sessions that share one time and
        do not fit in one batch would be skipped by the next.
        """
        return conn.execute(
            "SELECT * FROM sessions WHERE status != 'cleared' "
            "AND (last_active_at > ? OR (last_active_at = ? AND id > ?)) AND last_active_at <= ? "
            "AND NOT EXISTS (SELECT 1 FROM commissions WHERE commissions.session_id = sessions.id "
            "AND commissions.status IN ('queued', 'working', 'waiting_user', 'blocked')) "
            "ORDER BY last_active_at ASC, id ASC LIMIT ?",
            (after, after, after_id, until, _CLEAN_BATCH),
        ).fetchall()

    @staticmethod
    def _clean_session(directory: Path, session_id: str) -> str:
        """Remove what was said in a session; its working directory is not touched. Blocking.

        Only in a directory that is a session's by its place and name, and
        only what the runtime writes there: the transcript, the tool
        output it kept, and the system prompt the session was opened with.
        Returns ``removed``, ``nothing`` (there was nothing to remove) or
        ``not_removed`` (something is still there: held open by another
        program, say).
        """
        if directory.name != session_id or directory.parent.name != "sessions":
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
        snapshot = directory / SYSTEM_SNAPSHOT_NAME
        try:
            # Goes with the transcript, and only once that is gone: a session
            # that still has what was said goes on with the system prompt it
            # was opened with. Of no use without the transcript: a session
            # with nothing said is put together anew.
            gone = not transcript.exists() and not transcript.is_symlink()
            if gone and snapshot.is_file() and not snapshot.is_symlink():
                snapshot.unlink()
                removed = True
        except OSError:
            failed = True
            logger.warning("the system snapshot of an old session could not be removed", exc_info=True)
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

    def _clean(self, directory: Path, session_id: str) -> str:
        """Remove what was said in a session, wherever it is kept. Blocking; returns what ``_clean_session`` does.

        The built-in runtime keeps it in the session directory
        (``_clean_session``). For a session that ran in dsh it is in the home
        of dsh, which never removes it: the host does, here.
        """
        outcome = self._clean_session(directory, session_id)
        forget = self._forget_dsh_session
        if forget is None or directory.name != session_id or directory.parent.name != "sessions":
            return outcome
        try:
            if forget(directory) and outcome == _NOTHING:
                return _REMOVED
        except OSError:
            logger.warning("what dsh kept of an old session could not be removed", exc_info=True)
            return _NOT_REMOVED
        return outcome

    async def _clean_one(self, session_id: str, until: float) -> str:
        """Clean one session the query found, if it is still to be cleaned.

        Held against a commission being started in it for as long as its
        files are removed, and looked up again once it is held: a session
        that a commission was taken up in between the query and now is at
        work on that very transcript. Once what was said is gone the
        session is ``cleared``: it can still be gone on with, from nothing
        but its working directory.
        """
        from utils.asyncio_executor import to_thread_through_cancellation

        if self._hold_files is not None and not self._hold_files(session_id):
            # In use, or on its way to be. Not passed over: a session whose
            # commission fails to be taken up before its status changes is
            # as old as it was, and the scan does not come by it again. It
            # is looked at again next round; one that went on to work and
            # was closed anew is dropped then, being young.
            return _NOT_REMOVED
        try:
            current = await self._ledger.get_session(session_id)
            if current is None or current.status == "cleared" or current.last_active_at > until:
                return _NOTHING
            if await self._ledger.select(session_id=session_id, statuses=ACTIVE_STATUSES, limit=1):
                return _SKIPPED
            # What was copied from it goes with it, and first: a session
            # made from this one (``fork_session``) holds what was said
            # here, is of no use once this one starts over from nothing,
            # and reads what this one kept of long tool output where it
            # is. While a copy is in use this session stays as it is, and
            # is looked at again next round.
            if not await self._clean_copies(current.id):
                return _NOT_REMOVED
            directory = await asyncio.to_thread(self._session_dir_of, current)
            # A thread cannot be stopped. Cancelled (the host shutting down),
            # this still waits for the removing to finish before the hold
            # below is given back and before shutting down goes on: nothing
            # opens the session while its files are still being removed.
            outcome = await to_thread_through_cancellation(self._clean, Path(directory), current.id)
            if outcome == _REMOVED:
                # With the session, and with each commission of it.
                await asyncio.shield(self._ledger.mark_session_cleared(current.id))
            return outcome
        except (OSError, ValueError):
            logger.warning("an old session could not be looked at", exc_info=True)
            return _NOT_REMOVED
        finally:
            if self._release_files is not None:
                self._release_files(session_id)

    async def _clean_copies(self, session_id: str) -> bool:
        """Remove what was said in the sessions copied from a session. False while one of them is in use."""
        for copy in await self._ledger.forks_of(session_id):
            if copy.status == "cleared":
                continue
            # By what became of it just now, not by asking again whether a
            # commission is under way in it: that may have changed since.
            if await self._clean_one(copy.id, float("inf")) in (_NOT_REMOVED, _SKIPPED):
                return False
        return True

    async def clear_session(self, session_id: str) -> bool:
        """Remove what was said in a session now, however lately it was in use. Returns whether nothing of it is left.

        For a session whose owner is done with it (a copy that was made to
        ask something). As when an old session is cleaned: only what the
        runtime wrote into the session's own directory, never its working
        directory; not while a commission is at work in it or waits for
        it; and the session is ``cleared`` afterwards.
        """
        if self._session_dir_of is None:
            return False
        # Removed, or nothing was there. Not: in use, or left alone for a
        # commission that is still under way in it.
        return await self._clean_one(session_id, float("inf")) in (_REMOVED, _NOTHING)

    @staticmethod
    def _confirmed_line(state: dict[str, Any], now: float, due: float) -> tuple[float | None, bool]:
        """Return up to when sessions may be cleaned in this round (None: nothing yet), and whether to note anew.

        What is removed here cannot be brought back, and whether a session
        is old is read off the clock of the computer. A clock that jumps a
        month ahead (the user sets the date, or the board's battery is
        flat) makes every session look old at once. So one reading is not
        enough: a round only notes what is old by its clock, and a later
        round, one interval of tidying after it at the least, removes what
        is old by both. A clock that was wrong for a moment is right again
        by then, and what it called old is not; one that went back is seen
        by the note being of a time still to come, and the note is not used.

        A note that is too new to be used stays as it is, so that rounds
        in quick succession do not keep putting off the one that uses it.
        """
        from config.nekoclaw_settings import SPACE_UPKEEP_INTERVAL_SECONDS

        noted, seen_at = state.get("sessions_due_until"), state.get("sessions_due_seen_at")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (noted, seen_at)):
            return None, True
        if seen_at > now:
            # By a clock that was ahead of this one.
            return None, True
        if seen_at > now - SPACE_UPKEEP_INTERVAL_SECONDS:
            return None, False
        return min(float(noted), due), True

    async def _clean_sessions(self, now: float, state: dict[str, Any]) -> None:
        """Remove what was said in sessions nothing was worked on in for a long time."""
        from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

        if self._session_dir_of is None:
            return
        stored = state.get("sessions_cleaned_until")
        after = float(stored) if isinstance(stored, (int, float)) else 0.0
        # Where the scan stands among commissions of that very time; empty
        # when all of them are done with.
        stored_id = state.get("sessions_cleaned_id")
        after_id = stored_id if isinstance(stored_id, str) else ""
        due = now - SPACE_TRANSCRIPT_KEPT_SECONDS
        until, note = self._confirmed_line(state, now, due)
        if note:
            # What is old by the clock of this round, for a later one to confirm.
            await self._space.write_state(sessions_due_until=due, sessions_due_seen_at=now)
        if until is None:
            return
        if after > until:
            # The scan stands beyond what is old by now: it got there by a
            # clock that was ahead (or sessions are kept longer than they
            # were). Sessions that were closed since, by the clock as it is
            # now, lie before where it stands and would never be come by.
            # So it begins anew; what it comes by a second time has nothing
            # left to remove.
            after, after_id = 0.0, ""
        # Looked at is not cleaned. A session whose files could not be
        # removed (held open by another program) is remembered by itself
        # and tried again every round, while the scan moves on: holding the
        # scan back at it would keep every later session waiting behind a
        # file that may never let go.
        remembered = state.get("sessions_to_retry")
        waiting = [str(item) for item in remembered if isinstance(item, str)] if isinstance(remembered, list) else []
        # Every one of them stays remembered until its files are gone; a
        # round tries a batch of them, and those that fail again go to the
        # back, so the others get their turn.
        still: list[str] = waiting[_CLEAN_BATCH:]

        async def clean(session_id: str) -> None:
            if await self._clean_one(session_id, until) == _NOT_REMOVED:
                still.append(session_id)

        for session_id in waiting[:_CLEAN_BATCH]:
            if await self._ledger.get_session(session_id) is not None:
                await clean(session_id)
        reached, reached_id = after, after_id
        if until >= after:
            found = await self._ledger.run(
                lambda conn: [
                    (str(row["id"]), float(row["last_active_at"]))
                    for row in self._closed_long_ago(conn, after, after_id, until)
                ]
            )
            for session_id, _last_active_at in found:
                if session_id not in waiting:
                    await clean(session_id)
            if len(found) >= _CLEAN_BATCH:
                # A full batch leaves the rest to the next round, which goes
                # on right after the last one of this round.
                reached_id, reached = found[-1]
            else:
                # Everything up to ``until`` is done with. The largest id
                # there can be keeps what is of exactly that time, and was
                # looked at, from being found again.
                reached, reached_id = until, _AFTER_EVERY_ID
        if (reached, reached_id) != (after, after_id) or still != waiting:
            await self._space.write_state(
                sessions_cleaned_until=reached, sessions_cleaned_id=reached_id, sessions_to_retry=still
            )
