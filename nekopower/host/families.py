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

"""Families and leaders: where a new commission of hers goes, and what is set up for it.

``orchestrator.py`` says where a sentence goes from what it is given; this
gives it what is there (the projects of the task agent space, the sessions
of the ledger), asks the small model where the layers before it could not
tell, and then writes down what follows:

* a commission goes on in a task agent that is there, or gets a new one of
  its own, as before;
* loose task agents that did the same job before become a project (a
  family): a project is made with a directory of its own, and they are its
  members from then on, each where it worked;
* a project gets a leader: when it is in a git repository, when a third
  member would be opened, when its one member has too much waiting for it,
  or when the user asks for one. From then on the project's commissions
  come to the leader, which hands them out and does none of the work.

Every decision is put on record with the commission (``orchestrated``):
which layer settled it, among which candidates, where it went, how long it
took. No sentence is kept there, only names and numbers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from config import nekopower_settings as settings
from nekopower.host import orchestrator as orch
from nekopower.host.ledger import PROJECT_KINDS, UNDER_WAY_STATUSES, Commission, Ledger, Session
from nekopower.host.orchestrator import Agent, Bars, Done, ProjectView, Standing, Verdict
from nekopower.host.paths import DEFAULT_PROJECT

logger = logging.getLogger(__name__)

# Asked with the request that says which candidate a sentence belongs with;
# returns what the small model answered, None when it could not be asked.
Judge = Callable[[list[dict[str, Any]]], Awaitable["str | None"]]

# Why a project got its leader.
LEADER_GIT = "git"
LEADER_THIRD = "third_member"
LEADER_BUSY = "busy"
LEADER_USER = "user"
LEADER_REASONS = (LEADER_GIT, LEADER_THIRD, LEADER_BUSY, LEADER_USER)

_OVER = ("done", "blocked", "failed")
_NAME_FORBIDDEN = '\\/:*?"<>|\r\n\t'


def family_is_busy(waiting: int) -> bool:
    """Say whether a project without a leader has enough under way to get one.

    ``waiting`` is how many commissions are under way in the member a new
    one would go to: the one it is at, and those that wait for it. A rule
    for now, by a number of the settings; whatever judges this later is
    given the same and answers the same question.
    """
    return waiting >= settings.LEADER_WHEN_UNDER_WAY


class Families:
    """Decides where her commissions go and keeps the projects' members and leaders; one per ``TaskAgentManager``."""

    def __init__(
        self,
        ledger: Ledger,
        space: Any,
        paths: Any,
        *,
        clock: Callable[[], float] = time.time,
        in_git: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._ledger = ledger
        self._space = space
        self._paths = paths
        self._clock = clock
        self._judge: Judge | None = None
        self._probe = in_git
        self._worktrees: Any = None
        # By directory: when it was looked at, and whether it is in a git repository.
        self._git: dict[str, tuple[float, bool]] = {}

    def set_judge(self, judge: Judge | None) -> None:
        """Set what asks the small model; without one, what the layers before it leave open is a new loose commission."""
        self._judge = judge

    # -- what is there ----------------------------------------------------

    async def _projects(self) -> dict[str, Any]:
        """Return the projects that are not archived, by name."""
        if self._space is None:
            return {}
        try:
            listed = await self._space.list_projects(include_archived=False)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - decided without them: a new loose commission at worst
            logger.warning("the projects could not be listed for the orchestrator", exc_info=True)
            return {}
        return {item.name: item for item in listed if item.name != DEFAULT_PROJECT}

    async def standing(self, commission: Commission, projects: dict[str, Any] | None = None) -> Standing:
        """Return what a commission is held against: the projects, and the task agents of its character."""
        known = await self._projects() if projects is None else projects
        rows = await self._ledger.find_sessions(
            lanlan_name=commission.lanlan_name,
            kinds=(*PROJECT_KINDS, "single"),
            limit=settings.ORCHESTRATOR_SESSIONS_LOOKED_AT,
            commissions_each=settings.ORCHESTRATOR_COMMISSIONS_EACH,
        )
        now = self._clock()
        leaders: dict[str, str] = {}
        agents: list[Agent] = []
        for row in rows:
            session = row.session
            in_project = session.kind in PROJECT_KINDS and bool(session.project) and session.project != DEFAULT_PROJECT
            if in_project and session.project not in known and session.project != commission.project:
                # Its project is archived: only naming it brings it back.
                continue
            if session.kind == "project" and in_project:
                leaders.setdefault(str(session.project), session.id)
            elif session.status == "cleared":
                # What was said in it was removed, being old: nothing to go on with.
                continue
            theirs = [item for item in row.commissions if item.id != commission.id and item.parent_id is None]
            under_way = any(item.status in UNDER_WAY_STATUSES for item in theirs)
            agents.append(
                Agent(
                    id=session.id,
                    project=session.project if in_project else None,
                    preset=session.preset,
                    title=session.title,
                    duty=session.duty,
                    summary=session.summary,
                    idle_seconds=0.0 if under_way else max(0.0, now - session.last_active_at),
                    done=tuple(
                        Done(
                            title=item.title,
                            result=item.result_summary or "",
                            age_seconds=max(0.0, now - (item.ended_at or item.updated_at)),
                            filed=item.relation or orch.NEW,
                        )
                        for item in theirs
                        if item.status in _OVER
                    ),
                )
            )
        names = list(known)
        if commission.project and commission.project != DEFAULT_PROJECT and commission.project not in known:
            names.append(commission.project)
        views = tuple(
            ProjectView(
                name=name,
                description=getattr(known.get(name), "description", "") or "",
                leader=leaders.get(name),
            )
            for name in names
        )
        return Standing(projects=views, agents=tuple(agents))

    async def in_git(self, directory: str | None) -> bool:
        """Whether a directory is in a git repository; looked at anew after a while. False when it cannot be told."""
        if not directory:
            return False
        now = self._clock()
        kept = self._git.get(directory)
        if kept is not None and now - kept[0] < settings.LEADER_GIT_PROBE_SECONDS:
            return kept[1]
        try:
            if self._probe is not None:
                found = bool(await self._probe(directory))
            else:
                if self._worktrees is None:
                    from nekopower.host.worktrees import WorktreeManager

                    self._worktrees = WorktreeManager()
                # Looked at only: no work tree is made.
                found = (await self._worktrees.probe(directory)).repo_root is not None
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not told: it is treated as no repository
            logger.warning("whether a project's directory is in a git repository could not be told", exc_info=True)
            return False
        self._git[directory] = (now, found)
        return found

    # -- where it goes ----------------------------------------------------

    async def decide(self, commission: Commission) -> Verdict:
        """Return where a commission of hers that has no session goes. Writes nothing but what naming a project always did."""
        started = time.perf_counter()
        bars = Bars()
        named = None
        if self._space is not None and commission.project is None:
            try:
                # The project its words name, an archived one moved back: as ever.
                named = await self._space.assign_project(commission)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - it is looked at as one that names none
                logger.warning("looking for the project a commission names failed", exc_info=True)
        known = await self._projects()
        standing = await self.standing(commission, known)
        project = named.name if named is not None else commission.project
        if project and project != DEFAULT_PROJECT:
            verdict = orch.settle(orch.PROJECT_KEY + project, orch.FOLLOW_UP, standing, bars, reason="named")
            if verdict.action == orch.SUMMON_STRAY:
                # Not among what was read (its record cannot be read, say): the project's all the same.
                verdict = Verdict(action=orch.SUMMON_IN_PROJECT, project=project, preset=orch.WORKING_PRESET,
                                  relation=orch.FOLLOW_UP, reason="named")
        else:
            verdict = orch.by_rules(commission.dispatch_text, commission.user_text, standing, bars)
            if verdict is None:
                verdict, matches = orch.by_retrieval(commission.dispatch_text, commission.user_text, standing, bars)
                if verdict is None:
                    verdict = await self._ask(commission, standing, matches, bars)
        if (
            verdict.project
            and verdict.action in (orch.CONTINUE, orch.SUMMON_IN_PROJECT)
            and await self.in_git(getattr(known.get(verdict.project), "workdir", None))
        ):
            # A project in a git repository has a leader from its first commission on.
            verdict = replace(verdict, action=orch.TO_LEADER, agent=None, preset=None, reason=LEADER_GIT)
        verdict.seconds = round(time.perf_counter() - started, 4)
        return verdict

    async def _ask(self, commission: Commission, standing: Standing, matches: list[Any], bars: Bars) -> Verdict:
        """Ask the small model which of the best matches a commission belongs with; anything unreadable is none of them."""
        from utils.tokenize import truncate_to_tokens

        def settled(key: str | None, relation: str, reason: str, calls: int) -> Verdict:
            return orch.settle(
                key, relation, standing, bars, matches=matches, layer=orch.LAYER_MODEL, model_calls=calls, reason=reason
            )

        sentence = orch.sentence_of(commission.dispatch_text, commission.user_text)
        keys = orch.shown_keys(matches, bars, sentence, standing)
        if not keys or self._judge is None:
            clear, best = orch.clear_match(matches, bars)
            if clear and best is not None:
                # A clear match that nobody can be asked about: it goes on
                # there, and no project is made of it.
                return orch.settle(
                    best.key, orch.FOLLOW_UP, standing, bars, matches=matches, layer=orch.LAYER_RETRIEVAL, reason="clear match"
                )
            return settled(None, orch.NEW, "not asked", 0)
        messages = orch.question(
            truncate_to_tokens(commission.dispatch_text, settings.ORCHESTRATOR_REQUEST_MAX_TOKENS),
            truncate_to_tokens(commission.user_text or "", settings.ORCHESTRATOR_REQUEST_MAX_TOKENS),
            keys,
            standing,
            line_max_tokens=settings.ORCHESTRATOR_CANDIDATE_MAX_TOKENS,
        )
        try:
            raw = await self._judge(messages)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - not told: a new loose commission
            logger.warning("asking where a commission belongs failed: %s", type(exc).__name__)
            raw = None
        answer = orch.read_answer(raw or "", keys)
        if answer is None:
            return settled(None, orch.NEW, "unreadable", 1)
        return settled(answer[0], answer[1], "model", 1)

    # -- setting it up ----------------------------------------------------

    async def place(self, commission: Commission, verdict: Verdict) -> tuple[Commission, Session]:
        """Give a commission the session a verdict sends it to, making what that takes; returns both.

        What is made: a project and its members, for a family; a leader,
        for a project that is to have one; a new member, or a new loose
        task agent. The decision and what was made are put on record with
        the commission.
        """
        events: list[tuple[str, dict[str, Any]]] = [("orchestrated", orch.as_record(verdict))]
        action, project, agent = verdict.action, verdict.project, verdict.agent
        known = await self._projects()
        session: Session | None = None

        if action == orch.FORM_FAMILY:
            made = await self._form_family(verdict.members)
            if made is None:
                action = orch.CONTINUE if agent else orch.SUMMON_STRAY
            else:
                project = made.name
                known[project] = made
                events.append(("family_formed", {"project": project, "members": list(verdict.members)}))
                action = orch.CONTINUE if agent else orch.SUMMON_IN_PROJECT

        if action == orch.CONTINUE:
            session = await self._ledger.get_session(agent)
            if session is None or not await self._ledger.session_alive(session):
                # Gone, or over since it was read: a new one in its stead.
                session, action = None, (orch.SUMMON_IN_PROJECT if project else orch.SUMMON_STRAY)
            elif session.kind == "line" and session.project and await self._busy(session):
                # Its project's one member has too much waiting: the project gets a leader.
                session, project, action = None, session.project, orch.TO_LEADER
                verdict = replace(verdict, reason=LEADER_BUSY)
            else:
                in_project = session.project if session.kind in PROJECT_KINDS else None
                commission = await self._file(commission, in_project, known, session_id=session.id)

        if action == orch.SUMMON_IN_PROJECT and project:
            members = await self._ledger.members(commission.lanlan_name, project)
            if len(members) + 1 >= settings.LEADER_AT_MEMBERS:
                # The third task agent of a project is its leader's to start.
                action = orch.TO_LEADER
                verdict = replace(verdict, reason=LEADER_THIRD)
            else:
                commission = await self._file(commission, project, known)
                commission, session = await self._ledger.give_session(
                    commission.id,
                    kind="line",
                    name=project,
                    preset=orch.WORKING_PRESET,
                    duty=await self._about(project) or commission.title,
                )

        if action == orch.TO_LEADER and project:
            had = await self._ledger.lead_session(commission.lanlan_name, project)
            commission = await self._file(commission, project, known)
            commission, session = await self._ledger.give_session(
                commission.id,
                kind="project",
                name=project,
                duty=await self._about(project) or commission.title,
            )
            if had is None:
                reason = verdict.reason if verdict.reason in LEADER_REASONS else LEADER_GIT
                events.append(("leader_derived", {"project": project, "reason": reason, "session_id": session.id}))

        if session is None:
            from utils.tokenize import truncate_to_tokens

            commission, session = await self._ledger.give_session(
                commission.id,
                kind="single",
                name=truncate_to_tokens(commission.title, settings.TASK_AGENT_SESSION_NAME_MAX_TOKENS),
                duty=commission.title,
            )
        try:
            if commission.relation != verdict.relation:
                commission = await self._ledger.update(commission.id, relation=verdict.relation) or commission
            await self._ledger.append_events(commission.id, events)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the notes are for the record; it has its session
            logger.warning("noting where commission %s went failed", commission.id, exc_info=True)
        return commission, session

    async def _file(self, commission: Commission, project: str | None, known: dict[str, Any], **values: Any) -> Commission:
        """Write a commission's project (with where that works, unless it has a directory) and whatever else is given."""
        if not project or project == DEFAULT_PROJECT:
            if not values:
                return commission
            return await self._ledger.update(commission.id, **values) or commission
        workdir = getattr(known.get(project), "workdir", None)
        if not commission.workdir and workdir and await asyncio.to_thread(Path(workdir).is_dir):
            values.setdefault("workdir", workdir)
        return await self._ledger.file_under_project(commission.id, project, **values) or commission

    async def _about(self, project: str) -> str:
        """Return what a project is for, as its record says; empty when it says nothing or cannot be read."""
        if self._space is None:
            return ""
        try:
            found = await self._space.get_project(project)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the session is made all the same, with its first commission's title
            logger.warning("reading what a project is for failed", exc_info=True)
            return ""
        return found.description if found is not None else ""

    async def _busy(self, session: Session) -> bool:
        """Whether a member of a project without a leader has enough under way for the project to get one."""
        if await self._ledger.lead_session(session.lanlan_name, session.project) is not None:
            return False
        under_way = await self._ledger.select(session_id=session.id, statuses=UNDER_WAY_STATUSES)
        return family_is_busy(len(under_way))

    # -- families ---------------------------------------------------------

    async def _form_family(self, member_ids: tuple[str, ...]) -> Any:
        """Make a project of loose task agents that did the same job; returns the project, None when none was made.

        A project with a directory of its own, where whoever is started in
        it works; its members stay where they worked. Named as a review
        of one of them suggested, else after the card of the one last in
        use.
        """
        if self._space is None:
            return None
        members = [item for item in [await self._ledger.get_session(one) for one in member_ids] if item is not None]
        members = [item for item in members if item.kind == "single"]
        if not members:
            return None
        members.sort(key=lambda item: item.last_active_at, reverse=True)
        try:
            name, description = await self._family_name(members)
            workdir = self._paths.project_work_dir(name)
            await asyncio.to_thread(workdir.mkdir, parents=True, exist_ok=True)
            project = await self._space.open_project(
                name, description=description, workdir=str(workdir), unless_archived=True, description_by="creator"
            )
            if project is None:
                return None
            await self._ledger.adopt([item.id for item in members], project.name)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - no family: the commission goes on as a loose one
            logger.warning("making a project of loose task agents failed", exc_info=True)
            return None
        return project

    async def _family_name(self, members: list[Session]) -> tuple[str, str]:
        """Return the name and the description a family's project gets; a name no project has."""
        from nekopower.host.paths import safe_segment
        from nekopower.host.space import normalize_name
        from utils.tokenize import truncate_to_tokens

        suggested: tuple[str, str] | None = None
        for member in members:
            last = await self._ledger.last_commission(member.id)
            payload = await self._ledger.last_event(last.id, "project_suggested") if last is not None else None
            if payload and str(payload.get("name") or "").strip():
                suggested = (str(payload["name"]), str(payload.get("description") or ""))
                break
        lead = members[0]
        description = (suggested[1] if suggested else "") or lead.duty or lead.title or lead.name
        wanted = suggested[0] if suggested else (lead.title or lead.duty or lead.name)
        cleaned = "".join(" " if char in _NAME_FORBIDDEN else char for char in wanted)
        cleaned = truncate_to_tokens(" ".join(cleaned.split()), settings.SPACE_PROJECT_NAME_MAX_TOKENS).strip(" .")
        try:
            if not cleaned or cleaned.startswith(("_", ".")):
                raise ValueError(cleaned)
            safe_segment(cleaned, "project")
        except ValueError:
            cleaned = f"family-{lead.id[:6]}"
        taken = {normalize_name(item.name) for item in await self._space.list_projects()}
        name, number = cleaned, 1
        while normalize_name(name) in taken:
            number += 1
            name = f"{cleaned} {number}"
        return name, description

    # -- leaders ----------------------------------------------------------

    async def derive_leader(self, lanlan_name: str, project: str) -> tuple[Session, bool]:
        """Give a project a leader for one character, as the user asks; returns it, and whether it was made now.

        The project's commissions come to it from then on. What it is told
        of the project as it stands is said with the first one it is
        opened for (``leader_notes``).
        """
        known = await self._projects()
        if project not in known:
            raise LookupError(project)
        record = known[project]
        return await self._ledger.project_session(
            lanlan_name, project, workdir=record.workdir, duty=record.description or project
        )

    async def leader_notes(self, leader: Session, skip: str) -> tuple[str, list[str]]:
        """Return what a leader is told as a commission opens in it, and the commissions it is told the outcome of.

        Put together from what is on record, with no model asked: what
        came of the commissions it handed over since it was last told,
        and the members of its project, each with its card and where it
        stands. The first time it is opened, more of each: what a member
        did lately and has in hand, within a larger cap.
        """
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.templates import model_name
        from utils.tokenize import truncate_to_tokens

        first = await self._ledger.opened_last(leader.id) is None
        rows = await self._ledger.find_sessions(
            lanlan_name=leader.lanlan_name,
            project=leader.project,
            kind="line",
            limit=settings.LEADER_ROSTER_MEMBERS,
            commissions_each=settings.ORCHESTRATOR_COMMISSIONS_EACH,
        )
        lines: list[str] = []
        for row in rows:
            member = row.session
            theirs = [item for item in row.commissions if item.id != skip and item.parent_id is None]
            under_way = [item for item in theirs if item.status in UNDER_WAY_STATUSES]
            if any(item.status == "waiting_user" for item in under_way):
                state = "waiting"
            elif under_way:
                state = "working"
            elif await self._ledger.session_alive(member):
                state = "idle"
            else:
                state = "over"
            duty = truncate_to_tokens(" ".join((member.duty or member.title).split()), settings.LEADER_ROSTER_LINE_MAX_TOKENS)
            lines.append(
                texts.LEADER_ROSTER_ITEM.format(
                    name=member.name, preset=model_name(member.preset), state=texts.LEADER_MEMBER_STATES[state], duty=duty
                )
            )
            if member.workdir and leader.workdir and not _same(member.workdir, leader.workdir):
                lines.append(texts.LEADER_ROSTER_WORKDIR.format(workdir=member.workdir))
            if not first:
                continue
            if member.summary.strip():
                lines.append(
                    texts.LEADER_ROSTER_SUMMARY.format(
                        summary=truncate_to_tokens(" ".join(member.summary.split()), settings.LEADER_ROSTER_SUMMARY_MAX_TOKENS)
                    )
                )
            for item in under_way:
                lines.append(texts.LEADER_ROSTER_HAS.format(title=item.title, status=item.status))
            for item in [one for one in theirs if one.status in _OVER][: settings.LEADER_ROSTER_RESULTS_EACH]:
                result = truncate_to_tokens(
                    " ".join((item.result_summary or item.status_reason or item.status).split()),
                    settings.LEADER_ROSTER_LINE_MAX_TOKENS,
                )
                lines.append(texts.LEADER_ROSTER_DID.format(title=item.title, status=item.status, result=result))
        parts: list[str] = []
        if first:
            parts.append(texts.LEADER_FIRST_OPENING)
        cap = settings.LEADER_FIRST_ROSTER_MAX_TOKENS if first else settings.LEADER_ROSTER_MAX_TOKENS
        roster = "\n".join([texts.LEADER_ROSTER_HEADER, *lines]) if lines else texts.LEADER_ROSTER_NONE
        parts.append(truncate_to_tokens(roster, cap))

        handed = [
            item
            for item in await self._ledger.handed_work(leader.id, limit=settings.LEADER_HANDED_WORK_MAX_ITEMS)
            if item.id != skip
        ]
        if handed:
            names = {row.session.id: row.session.name for row in rows}
            items = []
            for item in handed:
                result = truncate_to_tokens(
                    " ".join((item.result_summary or item.status_reason or item.status).split()),
                    settings.LEADER_HANDED_WORK_RESULT_MAX_TOKENS,
                )
                items.append(
                    texts.LEADER_HANDED_WORK_ITEM.format(
                        title=item.title, outcome=item.status, member=names.get(str(item.session_id), "a member"), result=result
                    )
                )
            parts.insert(1 if first else 0, texts.LEADER_HANDED_WORK.format(items="\n".join(items)))
        return "\n\n".join(parts), [item.id for item in handed]

    async def member_notes(self, member: Session) -> str:
        """Return what a member is told of where others of its project worked; empty when all work where it does.

        The members of a family stay where they worked before they were
        one: whoever works in the project later is told where that is.
        """
        from config.prompts import prompts_nekopower_brain as texts
        from utils.tokenize import truncate_to_tokens

        if not member.project:
            return ""
        others = await self._ledger.members(member.lanlan_name, member.project)
        places: list[str] = []
        for other in others:
            if other.id == member.id or not other.workdir or _same(other.workdir, member.workdir):
                continue
            if other.workdir not in places:
                places.append(other.workdir)
        if not places:
            return ""
        return truncate_to_tokens(
            texts.MEMBER_EARLIER_WORK.format(places="; ".join(places)), settings.MEMBER_EARLIER_WORK_MAX_TOKENS
        )


def _same(first: str | None, second: str | None) -> bool:
    """Whether two working directories on record are one."""
    import os

    if not first or not second:
        return False
    return os.path.normcase(os.path.normpath(first)) == os.path.normcase(os.path.normpath(second))
