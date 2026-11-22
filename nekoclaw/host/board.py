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

"""The board of task agent sessions: what a page that shows them is given.

``Board.view`` returns the sessions grouped by project, one card each::

    {"revision": "...",
     "projects": [{"name", "description", "description_by", "workdir", "archived", "done_today",
                   "members": [card, ...]}],
     "loose": [card, ...]}           # sessions that belong to no project

    card = {"id", "name", "kind", "lanlan_name", "runtime", "workdir",
            "preset", "avatar", "label", "description",
            "title", "title_by", "duty", "duty_by", "summary", "summary_by",
            "doing", "state", "leader", "parent", "team", "model",
            "usage", "context_tokens", "last_active_at", "tree",
            "commission": {"id", "title", "status", "started_at", "ask"} | None}

Read from the ledger, the project records and the template files; no
model is asked and no runtime. Not every session is on it (see
``Ledger.board_rows``): a board is for what is going on.

``state`` says how the session stands right now, one of ``STATES``:

* ``compacting``   - the host is having its conversation summed up;
* ``waiting_user`` - the commission in hand waits for the user;
* ``working``      - the commission in hand is being worked on;
* ``idle``         - open in a runtime with nothing to do;
* ``asleep``       - no runtime has it open.

The state is the session's; how the commission in hand stands is said by
``commission.status``. So a session whose commission rests until a time it
set, or waits for its turn, is ``asleep`` with a commission whose status is
``resting`` or ``queued``: nobody is at it, and nobody has to do anything.

``doing`` is put together from the ledger: the title of the commission in
hand, the item of its last todo list that is in progress, and the tool it
called last with what that was called on. Empty without a commission in
hand.

``revision`` stands for everything on the board: two boards with the same
revision are the same. A page asks every couple of seconds with the
revision it has (``known_revision``) and is told ``{"revision", "unchanged":
True}`` while nothing changed. That costs one count read from the ledger and
the dates of the project and template files; only when one of those moved
is the board put together again, and its revision compared.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import PurePath
from typing import Any

from nekoclaw.host.ledger import BoardRows, Commission, Ledger, Session
from nekoclaw.host.paths import DEFAULT_PROJECT, SpacePaths
from nekoclaw.host.space import PROJECT_ARCHIVED, Project, TaskAgentSpace
from nekoclaw.host.templates import SHADOW, Listed, TemplateStore

logger = logging.getLogger(__name__)

STATES: tuple[str, ...] = ("working", "waiting_user", "idle", "asleep", "compacting")

# Tool calls that say nothing of what a session is at: its reports, and the
# todo list, which is read for the item in progress instead.
_NOT_A_STEP: frozenset[str] = frozenset({"report", "todo_write"})
# What a tool was called on, by the argument that says so, the first that is there.
_OBJECT_KEYS: tuple[str, ...] = (
    "file_path", "path", "command", "pattern", "url", "query", "name", "description", "session", "agent",
)
_PATH_KEYS: frozenset[str] = frozenset({"file_path", "path"})
_USAGE: tuple[str, ...] = ("requests", "tokens_in", "tokens_out", "tokens_cache_read", "tokens_cache_write")


def state_of(session: Session, commission: Commission | None) -> str:
    """Return how a session stands, one of ``STATES``; see the module docstring."""
    if session.status == "compacting":
        return "compacting"
    if commission is not None and commission.status == "waiting_user":
        return "waiting_user"
    if commission is not None and commission.status == "working" and not commission.resting:
        return "working"
    if session.status == "running" and commission is None:
        # Its commission ended a moment ago and the session is not let go of yet.
        return "working"
    if session.status == "idle":
        return "idle"
    return "asleep"


def _cut(text: Any, max_tokens: int) -> str:
    from utils.tokenize import truncate_to_tokens

    return truncate_to_tokens(" ".join(str(text or "").split()), max_tokens)


def _arguments(call: dict[str, Any]) -> dict[str, Any]:
    args = call.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return _read_cut(args)
    return args if isinstance(args, dict) else {}


def _read_cut(text: str) -> dict[str, Any]:
    """Return what can still be read of tool arguments that were cut off where they were stored.

    Arguments longer than an event row holds are kept as the beginning of
    their JSON text (``TaskAgentManager._compact``): a file written whole,
    a long passage replaced, a long todo list. That is no JSON any more,
    and it is just then that the user wants to see which file it is. The
    short fields that say what a tool was called on usually stand at the
    beginning, whole: each is read by its name. Of a todo list, the items
    that are whole.
    """
    decoder = json.JSONDecoder()
    found: dict[str, Any] = {}
    for key in _OBJECT_KEYS:
        at = re.search(rf'"{key}"\s*:\s*(?=")', text)
        if at is None:
            continue
        try:
            value, _end = decoder.raw_decode(text, at.end())
        except ValueError:
            continue  # the value itself is where it was cut
        found[key] = value
    listed = re.search(r'"todos"\s*:\s*\[', text)
    if listed is not None:
        items: list[Any] = []
        at = listed.end()
        while True:
            at = re.compile(r"[\s,]*").match(text, at).end()
            try:
                item, at = decoder.raw_decode(text, at)
            except ValueError:
                break
            items.append(item)
        found["todos"] = items
    return found


def _step(call: dict[str, Any]) -> str:
    """Return a tool call in a few words: the tool, and what it was called on when its arguments say."""
    name = str(call.get("tool_name") or "")
    args = _arguments(call)
    for key in _OBJECT_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            if key in _PATH_KEYS:
                # The end of a path says which file; its beginning is the same for all.
                value = "/".join(PurePath(value.replace("\\", "/")).parts[-2:])
            return f"{name}: {' '.join(value.split())}"
    return name


def doing_of(commission: Commission | None, calls: tuple[dict[str, Any], ...]) -> str:
    """Return what a session is doing right now, in one line; empty without a commission in hand.

    ``calls`` are the latest tool calls of the commission, the newest
    first, as the ledger has them.
    """
    from config.nekoclaw_settings import BOARD_DOING_MAX_TOKENS, BOARD_DOING_PART_MAX_TOKENS

    if commission is None:
        return ""
    parts = [_cut(commission.title, BOARD_DOING_PART_MAX_TOKENS)]
    todos = next((call for call in calls if call.get("tool_name") == "todo_write"), None)
    if todos is not None:
        listed = _arguments(todos).get("todos")
        for item in listed if isinstance(listed, list) else []:
            if isinstance(item, dict) and item.get("status") == "in_progress" and item.get("content"):
                parts.append(_cut(item["content"], BOARD_DOING_PART_MAX_TOKENS))
                break
    # (A call that names no tool is no step either: the one before it is the last.)
    last = next(
        (call for call in calls if (name := str(call.get("tool_name") or "")) and name not in _NOT_A_STEP), None
    )
    if last is not None:
        parts.append(_cut(_step(last), BOARD_DOING_PART_MAX_TOKENS))
    return _cut(" · ".join(part for part in parts if part), BOARD_DOING_MAX_TOKENS)


def _commission_of(commission: Commission | None) -> dict[str, Any] | None:
    if commission is None:
        return None
    return {
        "id": commission.id,
        "title": commission.title,
        "status": "resting" if commission.resting else commission.status,
        "started_at": commission.started_at,
        # What it asks, while it waits for the user: a question about a
        # permission as the gate put it (its category, what exactly, the
        # answers on offer), any other in the words it was asked in.
        "ask": commission.question() if commission.status == "waiting_user" else None,
    }


def card_of(
    session: Session,
    commission: Commission | None,
    calls: tuple[dict[str, Any], ...],
    template: Listed | None,
    model: str | None,
) -> dict[str, Any]:
    """Return the card of one session."""
    found = template.template if template is not None else None
    return {
        "id": session.id,
        "name": session.name,
        "kind": session.kind,
        "lanlan_name": session.lanlan_name,
        "runtime": session.runtime,
        "workdir": session.workdir,
        "preset": session.preset,
        # A copy is drawn as a copy; a preset whose template cannot be used, plainly.
        "avatar": SHADOW if session.preset == SHADOW else (found.avatar if found is not None else "general"),
        "label": found.label if found is not None else "",
        "description": found.description if found is not None else "",
        "title": session.title,
        "title_by": session.title_by,
        "duty": session.duty,
        "duty_by": session.duty_by,
        "summary": session.summary,
        "summary_by": session.summary_by,
        "doing": doing_of(commission, calls),
        "state": state_of(session, commission),
        "leader": session.kind == "project",
        # Who started it, or of which session it is a copy.
        "parent": session.parent_id or session.forked_from,
        "team": None,
        # The name of the model and nothing else of it: never its address or key.
        "model": model,
        "usage": {**{name: getattr(session, name) for name in _USAGE}, "tokens": session.tokens},
        "context_tokens": session.context_tokens,
        "last_active_at": session.last_active_at,
        "tree": None,
        "commission": _commission_of(commission),
    }


def _day_start(now: float) -> float:
    """Return when the day ``now`` is in began, by the clock of this computer."""
    day = time.localtime(now)
    return time.mktime((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0, 0, 0, -1))


class Board:
    """Puts the board together and says whether it changed; see the module docstring.

    ``model`` returns the name of the model task agents use, or None.
    """

    def __init__(
        self,
        *,
        ledger: Ledger,
        space: TaskAgentSpace,
        templates: TemplateStore,
        paths: Callable[[], SpacePaths],
        model: Callable[[], Awaitable[str | None]],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ledger = ledger
        self._space = space
        self._templates = templates
        self._paths = paths
        self._model = model
        self._clock = clock
        # By what was asked for (project, character): the marks the board
        # was last put together at, when, and the revision it had.
        self._kept: dict[tuple[str | None, str | None], tuple[Any, float, str]] = {}

    def _files_mark(self) -> tuple[Any, ...]:
        """Return what changes when a project's record or a template file does. Blocking.

        The names of the project directories with the date and size of
        each one's record, and the template files of the space and of
        each project (``TemplateStore.signature``).
        """
        paths = self._paths()
        marks: list[Any] = [self._templates.signature(None)]
        try:
            directories = sorted(item for item in paths.projects_dir().iterdir() if item.is_dir())
        except OSError:
            directories = []
        for directory in directories:
            try:
                stat = (directory / SpacePaths.PROJECT_FILE).stat()
                record: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                record = None
            marks.append((directory.name, record, self._templates.project_signature(directory.name)))
        return tuple(marks)

    async def _mark(self) -> tuple[Any, ...]:
        """Return the cheap marks of a change: the ledger's count of writes, and the dates of the files."""
        return (self._ledger.instance, await self._ledger.changes(), await asyncio.to_thread(self._files_mark))

    async def view(
        self, project: str | None = None, lanlan_name: str | None = None, known_revision: str | None = None
    ) -> dict[str, Any]:
        """Return the board, or ``{"revision", "unchanged": True}`` when it is as ``known_revision`` had it."""
        from config.nekoclaw_settings import BOARD_REVISION_KEPT_SECONDS

        key = (project, lanlan_name)
        now = self._clock()
        mark = await self._mark()
        kept = self._kept.get(key)
        if (
            kept is not None
            and kept[0] == mark
            and 0 <= now - kept[1] < BOARD_REVISION_KEPT_SECONDS
            and kept[2] == known_revision
        ):
            return {"revision": known_revision, "unchanged": True}
        board, whole = await self._build(project, lanlan_name, now)
        revision = hashlib.sha256(
            json.dumps(board, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        if whole:
            # The marks as they were read before the board was: a write that
            # came in between shows as a change the next time, and costs one
            # more look, not a board that is taken to be unchanged.
            self._kept[key] = (mark, now, revision)
        else:
            # Something of it could not be read (the projects, the model, the
            # templates) and it is shown without: put together again the next
            # time, whatever the marks say, so what was missing is there as
            # soon as it can be read.
            self._kept.pop(key, None)
        if known_revision is not None and known_revision == revision:
            return {"revision": revision, "unchanged": True}
        return {"revision": revision, **board}

    async def _projects(self) -> tuple[list[Project], set[str]] | None:
        """Return the projects on the board, and the names of the archived ones that are left off it; None when they cannot be read."""
        from config.nekoclaw_settings import BOARD_ARCHIVED_PROJECTS_MAX

        try:
            found = await self._space.list_projects(include_archived=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the sessions are shown all the same, under the names they have
            logger.warning("listing the projects for the board failed", exc_info=True)
            return None
        active = [item for item in found if item.status != PROJECT_ARCHIVED and item.name != DEFAULT_PROJECT]
        in_use = {item.name for item in active}
        # Of a name archived more than once, and of one in use again, the latest stands for it.
        archived: list[Project] = []
        for item in sorted(
            (item for item in found if item.status == PROJECT_ARCHIVED),
            key=lambda item: item.archived_at or 0.0,
            reverse=True,
        ):
            if item.name not in in_use and item.name not in {other.name for other in archived}:
                archived.append(item)
        left_off = {item.name for item in archived[BOARD_ARCHIVED_PROJECTS_MAX:]}
        return [*active, *archived[:BOARD_ARCHIVED_PROJECTS_MAX]], left_off

    async def _build(self, project: str | None, lanlan_name: str | None, now: float) -> tuple[dict[str, Any], bool]:
        """Return the board, and whether all of it could be read."""
        from config import nekoclaw_settings as settings

        rows: BoardRows = await self._ledger.board_rows(
            lanlan_name=lanlan_name,
            project=project,
            recent_since=now - settings.BOARD_RECENT_SECONDS,
            child_since=now - settings.BOARD_CHILD_RECENT_SECONDS,
            day_start=_day_start(now),
            loose_max=settings.BOARD_LOOSE_MAX,
            children_max=settings.BOARD_CHILDREN_MAX,
            events_each=settings.BOARD_DOING_EVENTS,
        )
        whole = True
        read = await self._projects()
        if read is None:
            whole, read = False, ([], set())
        found, left_off = read
        projects = [item for item in found if project is None or item.name == project]
        try:
            model = await self._model()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the board is shown without it
            logger.warning("reading the model of the task agents for the board failed", exc_info=True)
            whole, model = False, None

        by_id = {session.id: session for session in rows.sessions}

        def lead_of(session: Session) -> Session:
            # The session a task agent or a copy is shown under: its starter,
            # or the session it is a copy of. (Only those of the sessions on
            # the board are read, so that one is among them.)
            return by_id.get(session.parent_id or session.forked_from or "", session)

        # Which templates each session's project goes by, read once a project.
        wanted = {session.project if session.project != DEFAULT_PROJECT else None for session in rows.sessions}

        def listings() -> dict[str | None, dict[str, Listed]]:
            return {name: {item.name: item for item in self._templates.listing(name)} for name in wanted}

        try:
            templates = await asyncio.to_thread(listings)
        except Exception:  # noqa: BLE001 - the cards are shown without what their templates say
            logger.warning("reading the templates for the board failed", exc_info=True)
            whole, templates = False, {}

        def card(session: Session) -> dict[str, Any]:
            commission = rows.in_hand.get(session.id)
            scope = session.project if session.project != DEFAULT_PROJECT else None
            return card_of(
                session,
                commission,
                rows.tool_calls.get(commission.id, ()) if commission is not None else (),
                templates.get(scope, {}).get(session.preset),
                model,
            )

        # Every lead with what is shown under it, in the order they were read.
        under: dict[str, list[Session]] = {}
        for session in rows.sessions:
            if session.kind in ("child", "fork"):
                under.setdefault(lead_of(session).id, []).append(session)

        def cards(leads: list[Session], most: int) -> list[dict[str, Any]]:
            # Every lead has its place before what is shown under any of
            # them: a second lead is not crowded out by the first one's task
            # agents. Each still stands in front of its own.
            room = max(most - len(leads), 0)
            members: list[dict[str, Any]] = []
            for lead in leads:
                members.append(card(lead))
                shown = under.get(lead.id, [])[:room]
                room -= len(shown)
                members.extend(card(item) for item in shown)
            return members[:most]

        leads_of: dict[str, list[Session]] = {}
        loose: list[Session] = []
        for session in rows.sessions:
            if session.kind == "project" and session.project:
                leads_of.setdefault(session.project, []).append(session)
            elif session.kind == "single":
                loose.append(session)
        known = {item.name for item in projects}
        listed = [
            {
                "name": item.name,
                "description": item.description,
                "description_by": item.description_by,
                "workdir": item.workdir,
                "archived": item.status == PROJECT_ARCHIVED,
                "done_today": rows.done_today.get(item.name, 0),
                "members": cards(leads_of.get(item.name, []), settings.BOARD_MEMBERS_MAX),
            }
            for item in projects
        ]
        # A session whose project has no record that could be read is shown
        # under the name it has: it is at work all the same.
        for name, leads in leads_of.items():
            if name in known or name in left_off or (project is not None and name != project):
                continue
            listed.append(
                {
                    "name": name,
                    "description": "",
                    "description_by": "",
                    "workdir": leads[0].workdir,
                    "archived": False,
                    "done_today": rows.done_today.get(name, 0),
                    "members": cards(leads, settings.BOARD_MEMBERS_MAX),
                }
            )
        return {"projects": listed, "loose": cards(loose, settings.BOARD_LOOSE_CARDS_MAX)}, whole
