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

"""Leader hand-over, child task agents, session/file holds, and working-directory resolution.

Method-only mixin: every instance attribute is initialized in
``TaskAgentManager.__init__`` (``nekopower.host.task_agents.manager``).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from nekopower.host.ledger import Commission, Session
from nekopower.host.permissions import OPEN_SESSION, PermissionDecision
from .types import (
    logger,
    LEAD_NAME,
    _NO_PLACE_REASON,
    _STILL_WORKING,
    _Session,
    _Found,
    _has_transcript,
    _has_files,
)


class DelegationMixin:
    """Leader hand-over, child task agent, and session-hold methods."""

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

    async def _may_open(
        self, commission_id: str, detail: dict[str, Any], *, ahead: bool = False
    ) -> PermissionDecision:
        """Return whether another task agent that changes files may be opened for a commission (``OPEN_SESSION``).

        ``ahead`` of the call that opens it (``_rule_on_host_tool``), what
        is decided is kept for that call, which is answered by it where it
        asks about the same and does not ask again; kept as long as what
        the gateway rules on ahead of a call is, and for one call.
        """
        from config.nekopower_settings import GATEWAY_CLEARED_CALL_SECONDS

        ruled = self._open_rulings.pop(commission_id, None)
        if not ahead and ruled is not None and ruled[0] == detail and asyncio.get_running_loop().time() < ruled[2]:
            return ruled[1]
        decision = await self._permissions.check(commission_id, OPEN_SESSION, detail=detail)
        if ahead and commission_id in self._sessions:
            until = asyncio.get_running_loop().time() + GATEWAY_CLEARED_CALL_SECONDS
            self._open_rulings[commission_id] = (detail, decision, until)
        return decision

    async def _cb_hand_over(self, session_id: str, payload: dict[str, Any], *, ahead: bool = False) -> dict[str, Any]:
        """Hand the commission a leader is at over to a member of its project (the ``hand_over`` tool).

        To a member that is named, or to a new one of the preset named (a
        worker when none is). A new one that can change files is opened
        only when the user allows it (``OPEN_SESSION``): asked first by
        default, and not asked about one that reads only. Nothing moves
        here: the session is marked, and once its run is over it is
        closed and the commission goes on in the other one
        (``_handed_over``), which reports to her as any task agent does.

        ``ahead`` of the call (``_rule_on_host_tool``) the user is asked
        what the call would ask, and nothing is made or marked.
        """
        from config.prompts import prompts_nekopower_brain as texts

        commission_id = self._at_work_in(session_id)
        if commission_id in self._handing_claims:
            # Another call for it is at work beside this one (two in one turn): one hands it over.
            raise RuntimeError(texts.HAND_OVER_TWICE)
        self._handing_claims.add(commission_id)
        try:
            return await self._hand_over(session_id, commission_id, payload, ahead=ahead)
        finally:
            self._handing_claims.discard(commission_id)

    async def _hand_over(
        self, session_id: str, commission_id: str, payload: dict[str, Any], *, ahead: bool = False
    ) -> dict[str, Any]:
        """Do what ``_cb_hand_over`` says, for the one call that is let in for a commission at a time."""
        from config.nekopower_settings import HAND_OVER_BRIEF_MAX_TOKENS
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.templates import TemplateError, model_name
        from utils.tokenize import truncate_to_tokens

        live = self._sessions.get(commission_id)
        commission = await self._ledger.get(commission_id)
        leader = await self._ledger.get_session(session_id)
        if (
            live is None
            or commission is None
            or leader is None
            or leader.kind != "project"
            or not leader.project
            or commission.parent_id is not None
            or commission.session_id != leader.id
        ):
            raise RuntimeError(texts.HAND_OVER_NOT_A_LEADER)
        if live.handing_over is not None:
            raise RuntimeError(texts.HAND_OVER_TWICE)
        if commission.status != "working":
            raise RuntimeError("the commission is not at work; nothing is handed over")
        if await self._ledger.select(parent_id=commission_id, statuses=_STILL_WORKING):
            # What they hand back would wake the commission where it is by then: with a member that did not start them.
            raise RuntimeError(texts.HAND_OVER_AGENTS_AT_WORK)
        brief = truncate_to_tokens(str(payload.get("brief") or ""), HAND_OVER_BRIEF_MAX_TOKENS)
        duty = " ".join(str(payload.get("description") or "").split()) or commission.title
        members = await self._ledger.members(leader.lanlan_name, leader.project)
        named = payload.get("to")
        made, changes = False, False
        if isinstance(named, str) and named.strip():
            wanted = named.strip()
            # By its id, else by its name. Members made here have names of their own
            # (``Ledger.member_name``); of those that were sessions of their own before and
            # share one, the one last in use that still takes work.
            found = [member for member in members if member.id == wanted] or [
                member for member in members if member.name == wanted
            ]
            if not found:
                names = ", ".join(member.name for member in members) or "none"
                raise RuntimeError(texts.HAND_OVER_NO_MEMBER.format(name=wanted, names=names))
            target = None
            for member in found:
                if await self._ledger.session_alive(member):
                    target = member
                    break
            if target is None:
                raise RuntimeError(texts.HAND_OVER_MEMBER_OVER.format(name=wanted))
        else:
            from config.nekopower_settings import PROJECT_MEMBERS_MAX

            preset = await self._preset_wanted(commission, payload.get("preset")) or "worker"
            try:
                template = (await asyncio.to_thread(self.templates.resolve, preset, leader.project)).under(
                    await self._template_of(leader)
                )
            except TemplateError as exc:
                raise RuntimeError(f"the preset {preset!r} cannot be used: {exc}") from exc
            taking = [member for member in members if await self._ledger.session_alive(member)]
            if len(taking) >= PROJECT_MEMBERS_MAX:
                # As many as a leader is told of: one more would be started beside members it cannot see.
                raise RuntimeError(
                    texts.HAND_OVER_TOO_MANY_MEMBERS.format(
                        most=PROJECT_MEMBERS_MAX, names=", ".join(member.name for member in taking)
                    )
                )
            # Beside a member that works in the project's directory, one that changes files
            # has a git worktree made for each commission, where one can be made.
            # (One that only reads is put on record as that with the session itself: what the
            # user is asked when it changes files later does not hang on this hand-over going through.)
            seat = await self.seat_of_new_member(leader.project) if template.changes_files else "reads"
            if template.changes_files:
                # Another task agent that can change files (through the shell as well): the user's to allow.
                detail = {"tool": "hand_over", "title": f"{duty} <- {commission.title}", "preset": preset}
                if seat == "tree" and await self.tree_possible(leader.project, leader.workdir):
                    # Said in what is asked: it changes files in a copy of its own, merged back when it is done.
                    detail["tree"] = True
                decision = await self._may_open(commission_id, detail, ahead=ahead)
                if not decision.allow:
                    raise RuntimeError(texts.HAND_OVER_NOT_ALLOWED.format(reason=decision.reason or "declined"))
                # The user may have taken a while: looked at once more.
                live = self._sessions.get(commission_id)
                commission = await self._ledger.get(commission_id)
                if live is None or live.closing or commission is None or commission.status != "working":
                    raise RuntimeError("the commission is not at work any more; nothing is handed over")
                if live.handing_over is not None:
                    raise RuntimeError(texts.HAND_OVER_TWICE)
            if ahead:
                return {}
            target = await self._ledger.create_session(
                name=await self._ledger.member_name(
                    leader.lanlan_name, leader.project, f"{leader.project}/{model_name(preset)}"
                ),
                lanlan_name=leader.lanlan_name,
                kind="line",
                project=leader.project,
                workdir=leader.workdir,
                parent_id=leader.id,
                preset=preset,
                duty=duty,
                seat=seat,
            )
            made = True
            changes = bool(template.changes_files)
            # The model it is sent to, by its preset: the leader's unless the user set another.
            await self.settle_model(target)
        if ahead:
            # To a member that is there: nothing is asked.
            return {}
        if self._sessions.get(commission_id) is not live or live.closing or live.handing_over is not None:
            # It went while the member was looked for or made (stopped, say): nothing is marked,
            # and nothing awaited between this look and the mark. (A member made for it stays, idle.)
            raise RuntimeError("the commission is not at work any more; nothing is handed over")
        # (That it was handed over is put on record once the other task agent has it: ``_handed_over``.)
        # With whether a member made for it was made as one that changes files, which
        # the user was asked about then: None for a member that was there.
        live.handing_over = (target.id, target.name, brief, target.preset, made, changes if made else None)
        self._said_while_handed[commission_id] = []
        return {"message": texts.HAND_OVER_DONE.format(name=target.name)}

    async def _ask_about_member(self, commission: Commission, member: Session) -> None:
        """Ask the user about a member that starts with tools that change files and was made as one that only reads; raises when refused.

        A leader's new member is asked about when it is made, if it can
        change files then (``_hand_over``). One made as a reader is not;
        should its template say otherwise by the time it starts (the
        user changed it since), it is asked about now, for the
        commission it starts with, and noted as allowed so that it is
        asked about once. Refused, it does not start, and is asked
        about again the next time. What it was made as is on record
        with the session (``seat``, ``reads``), from when it is made:
        a member made before that was kept is not asked about.
        """
        from config.prompts import prompts_nekopower_brain as texts

        if member.seat != "reads":
            return
        decision = await self._permissions.check(
            commission.id,
            OPEN_SESSION,
            detail={
                "tool": "hand_over",
                "title": f"{member.duty or member.name} <- {commission.title}",
                "preset": member.preset,
            },
        )
        if not decision.allow:
            raise RuntimeError(texts.HAND_OVER_NOT_ALLOWED.format(reason=decision.reason or "declined"))
        # One that changes files in the project's directory from here on, and not asked about again.
        await self._ledger.update_session(member.id, seat="")
        await self._ledger.append_event(commission.id, "member_made", {"session": member.id, "changes_files": True})

    async def _close_leader_later(self, session: _Session) -> None:
        """Ask again to close a leader that hands its commission over, as for a copy that hands one back.

        Its close could not be asked for. Nothing comes by to ask again
        once its run is over, so the close is asked a few more times, a
        while apart; given up on, the commission fails as one whose run
        failed and lets its place go.
        """
        from config.nekopower_settings import REST_LOOK_RETRY_DELAYS

        commission_id = session.commission_id
        for delay in REST_LOOK_RETRY_DELAYS:
            await asyncio.sleep(delay)
            if self._closed or self._sessions.get(commission_id) is not session or session.closing:
                # Gone, or closing after all: its end takes it from there.
                return
            try:
                await self._close_session(session)
                return
            except Exception:  # noqa: BLE001 - asked again, and given up after the last
                logger.warning("closing a leader's session to hand its commission over failed again", exc_info=True)
        if self._sessions.get(commission_id) is session and not session.closing and not self._closed:
            session.handing_over = None
            self._said_while_handed.pop(commission_id, None)
            await self._fail(commission_id, "the lead's session could not be closed to hand it over", said="run")
            self._discard(commission_id)

    async def _handed_over(self, session: _Session) -> None:
        """Go on, in the task agent it was handed to, with a commission its project's leader handed over, the leader's session being closed.

        The leader lets go of its place and its session, and is free for
        the next commission of the project. The ledger has the other
        session as the commission's from here on, and which leader handed
        it over (the leader is told what came of it with the next
        commission that opens in it, ``Families.leader_notes``). The
        commission is queued for that session (``_wake_working``) with
        the leader's brief, framed as no word of the user's; the
        commission itself is what opens it there.
        """
        import secrets

        from config.prompts import prompts_nekopower_brain as texts
        from config.prompts.prompts_nekopower_sessions import frame

        commission_id, leader_id = session.commission_id, session.session_id
        assert session.handing_over is not None
        to, name, brief, preset, made, changes = session.handing_over
        said_meanwhile = self._said_while_handed.setdefault(commission_id, [])
        candidates = self._candidates.get(commission_id)
        excerpt = self._excerpts.get(commission_id)
        # What was sent to the leader's session and not seen taken goes back
        # with it, as when any session goes (``_release``): it is pending
        # again, for the leader.
        back = [wake for wakes in session.handed.values() for wake in wakes]
        session.handed = {}
        self._discard(commission_id)
        if self._held_by.get(leader_id) == commission_id:
            # Let go of here: the commission is not over, and the look at
            # the hold ``_discard`` has made would leave it held.
            del self._held_by[leader_id]
        if candidates is not None:
            self._candidates[commission_id] = candidates
        if excerpt is not None:
            # What was said just before it was dispatched: the session it
            # goes to holds nothing to tell what it refers to.
            self._excerpts[commission_id] = excerpt
        if self._closed:
            self._said_while_handed.pop(commission_id, None)
            return
        try:
            if back:
                try:
                    await self._put_back(back)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - the commission is handed over all the same
                    logger.warning("giving back what a leader's session had not taken failed", exc_info=True)
            await self._ledger.update_session(leader_id, status="closed")
            stands = await self._looked(
                lambda: self._ledger.get(commission_id), what="for a commission a leader hands over"
            )
            if stands is not None and stands.status == "working":
                await self._ledger.update(commission_id, session_id=to, handed_by=leader_id)
                if made:
                    # How the member was made, on record before it starts: for where it starts
                    # with other tools than it had then (``_ask_about_member``).
                    await self._ledger.append_event(
                        commission_id, "member_made", {"session": to, "changes_files": bool(changes)}
                    )
                self._session_of[commission_id] = to
                await self._note_writer(to)
                nonce = secrets.token_hex(8)
                message = _Found(frame(texts.TASK_AGENT_HANDED_OVER.format(nonce=nonce), brief, nonce))
                if await self._wake_working(
                    commission_id, message, reason="handed over by the lead of its project", handed=True
                ):
                    while said_meanwhile:
                        # What the user said to it meanwhile, in the user's own words, after the brief:
                        # at once, with nothing awaited between, so that it goes along with the brief
                        # before the commission starts in the other task agent. (Whatever is said from
                        # here on finds it queued, and goes along with it.)
                        await self._wake_working(
                            commission_id, said_meanwhile.pop(0), reason="said while it was handed over", handed=True
                        )
                    # Nothing more is kept here from this moment, with nothing awaited since the last
                    # look: what is said while the record below is written finds the commission
                    # queued, or at work in the other task agent, and is said to it there (``tell``).
                    self._said_while_handed.pop(commission_id, None)
                    try:
                        # On record once it waits for the other task agent: one whose leader could
                        # not be closed, or that could not be queued, was not handed over.
                        await self._ledger.append_event(
                            commission_id,
                            "handed_over",
                            {"from": leader_id, "to": to, "name": name, "preset": preset, "new": made},
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - the note is for the record; it is handed over all the same
                        logger.warning("noting that a leader handed its commission over failed", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the commission fails with the reason
            logger.warning("handing commission %s over failed: %s", commission_id, exc)
            await self._fail(
                commission_id, f"it could not be handed over to a member of its project: {exc}", said="run", detail=str(exc)
            )
        finally:
            unsaid = self._said_while_handed.pop(commission_id, None)
            if unsaid:
                # Not handed over after all (stopped meanwhile, or it could not be queued): what was
                # said to it meanwhile reaches nobody. (What the user said is on record with it,
                # ``told``; what a task agent it started handed back is with that one's commission.)
                logger.warning(
                    "commission %s was not handed over; %d thing(s) said or handed to it meanwhile were not passed on",
                    commission_id,
                    len(unsaid),
                )
            # The leader is free: what waits for it goes on.
            if self._queue and len(self._sessions) < self.limit and not self._closed:
                self._spawn(self._start_next())

    async def _cb_subagent_start(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = self._at_work_in(session_id)
        live = self._sessions.get(session_id)
        if live is not None and live.handing_over is not None:
            from config.prompts.prompts_nekopower_brain import HANDED_OVER_ALREADY

            raise RuntimeError(HANDED_OVER_ALREADY)
        parent = await self._ledger.get(session_id)
        if parent is None:
            raise RuntimeError("unknown commission")
        if parent.parent_id is not None:
            raise RuntimeError("a child task agent cannot start further task agents")
        workdir = await self._child_workdir(parent, payload.get("cwd"))
        preset = await self._preset_wanted(parent, payload.get("preset"))
        place = await self._ledger.get_session(parent.session_id) if parent.session_id else None
        if place is not None and place.kind == "project":
            from config.nekopower_settings import TEAM_MAX_WORKING
            from config.prompts.prompts_nekopower_brain import START_TEAM_TOO_MANY

            await self._leader_starts_readers(place, preset)
            # As many at work for one commission of a leader as a team has, however they were started.
            working = len(await self._ledger.select(parent_id=parent.id, statuses=_STILL_WORKING))
            if working >= TEAM_MAX_WORKING:
                raise RuntimeError(START_TEAM_TOO_MANY.format(most=TEAM_MAX_WORKING, working=working))
        name, _child = await self._start_child(
            parent,
            name=payload.get("name"),
            description=str(payload.get("description") or ""),
            prompt=str(payload.get("prompt") or ""),
            preset=preset,
            workdir=workdir,
        )
        return {"name": name}

    async def _start_child(
        self,
        parent: Commission,
        *,
        name: Any,
        description: str,
        prompt: str,
        preset: str | None,
        workdir: str | None,
        chosen: dict[str, Any] | None = None,
    ) -> tuple[str, Commission]:
        """File and start a task agent for a commission (``subagent``, ``start_team``); returns its name and its commission.

        ``chosen`` is what goes on record with it beside its preset: for
        one of a leader's team, that it is one, where it works
        (``seat``) and whether what it changes is checked before it
        lands. Raises when the parent was stopped meanwhile, or no place
        is free for it.
        """
        session_id = parent.id
        async with self._naming_members:
            # Read and filed with no other member of the team named between
            # (a question it asks is named under the same hold): one started
            # at once would read the names before this one is filed, and may
            # take the same.
            _lead, members = await self._team(session_id)
            taken = {member.name for member in members}
            if not isinstance(name, str) or not name or name in taken or name == LEAD_NAME:
                number = len(members) + 1
                while f"agent-{number}" in taken:
                    number += 1
                name = f"agent-{number}"
            # Which preset it is to be opened by, on record with it from
            # the start: read when it is given its session, now or at a
            # later try (``_preset_chosen``).
            said: dict[str, Any] = dict(chosen or {})
            if preset is not None:
                said["preset"] = preset
            child, _created = await self._ledger.create(
                lanlan_name=parent.lanlan_name,
                title=str(description or name),
                dispatch_text=prompt,
                user_text=parent.user_text,
                source="task_agent",
                route="task_agent",
                parent_id=parent.id,
                name=name,
                project=parent.project,
                workdir=workdir,
                conversation_id=parent.conversation_id,
                lang=parent.lang,
                events=[("preset", said)] if said else [],
            )
        # A stop of the parent sets its status before it looks for children,
        # so a child filed after that look is caught here, and one filed
        # before it is stopped by the stop itself.
        current = await self._ledger.get(parent.id)
        if current is None or current.status not in ("working", "waiting_user"):
            await self._ledger.transition(child.id, "cancelled", reason="its parent was stopped")
            raise RuntimeError("the commission was stopped; no task agent is started")
        started = {"name": name, "commission_id": child.id}
        if preset is not None:
            started["preset"] = preset
        await self._ledger.append_event(parent.id, "subagent_started", started)
        # It starts now or not at all (see ``_NO_PLACE_REASON``); the parent
        # is told so and can do that part itself. Its result comes back to the
        # parent as a message.
        if not await self.submit(child, candidate_plugin_ids=self._candidates.get(parent.id, ()), wait=False):
            raise RuntimeError(f"{_NO_PLACE_REASON}; do this part yourself")
        return name, child

    async def _leader_starts_readers(self, leader: Session, preset: str | None) -> None:
        """Raise unless the task agent a leader starts is of a preset that changes no file.

        A leader does none of the work, and not through a task agent it
        starts either: work that changes files, with a tool or through
        the shell, is a member's, handed over, where one does so in the
        project at a time and the user is asked about a new one
        (``_cb_hand_over``). What a leader starts itself looks or plans.
        Told by the preset as the task agent would have it
        (``Template.under``, ``Template.changes_files``); one that
        cannot be read counts as one that changes files.
        """
        from config.prompts.prompts_nekopower_brain import LEADER_SUBAGENT_WRITES
        from nekopower.host.ledger import PRESET_OF_KIND
        from nekopower.host.templates import NOT_FOR_CHILDREN, model_name

        wanted = preset or PRESET_OF_KIND["child"]
        over = await self._template_of(leader)
        listed = await asyncio.to_thread(self.templates.listing, leader.project)
        reads = [
            item.name
            for item in listed
            if item.template is not None and item.name not in NOT_FOR_CHILDREN and not item.template.under(over).changes_files
        ]
        if wanted not in reads:
            raise RuntimeError(
                LEADER_SUBAGENT_WRITES.format(preset=model_name(wanted), names=", ".join(model_name(name) for name in reads))
            )

    async def _preset_wanted(self, parent: Commission, named: Any) -> str | None:
        """Return the preset a task agent names for one it starts, or None when it names none; raises for one that cannot be used.

        By the name a model reads (``templates.model_name``) or the
        preset's own. Only one a task agent can be started with: not the
        lead of a project, nor a session of its own. The error lists the
        ones that can.
        """
        from config.prompts.prompts_nekopower_presets import PRESET_UNKNOWN, PRESET_UNUSABLE
        from nekopower.host.templates import NOT_FOR_CHILDREN, model_name

        if named is None:
            return None
        wanted = str(named).strip()
        listed = await asyncio.to_thread(self.templates.listing, parent.project)
        for item in listed:
            if item.name in NOT_FOR_CHILDREN or wanted not in (item.name, model_name(item.name)):
                continue
            if item.template is None:
                raise RuntimeError(PRESET_UNUSABLE.format(name=wanted, reason=item.error))
            return item.name
        names = ", ".join(model_name(item.name) for item in listed if item.valid and item.name not in NOT_FOR_CHILDREN)
        raise RuntimeError(PRESET_UNKNOWN.format(name=wanted, names=names))

    def session_dir_of(self, session: Any) -> Any:
        """Return where a session is kept (``session`` is a ``ledger.Session``). Blocking; see ``_session_dir_of``."""
        return self._session_dir_of(session)

    def hold_files(self, session_id: str, *, drop_idle_timer: bool = True) -> bool:
        """Claim a session's files for removal. False when the session is in use.

        In use means a commission holds it, or one that is worked on in it
        has a place, waits for one, or is being taken up again. Looking and
        claiming happen with no await in between. Until ``release_files``,
        a commission that is to be started in the session waits instead of
        opening files that are being removed under it. Pass
        ``drop_idle_timer=False`` for holds that leave the transcript intact
        (such as working-directory cleanup) so idle compaction stays scheduled.
        """
        if self._in_use(session_id):
            return False
        self._clearing[session_id] = asyncio.Event()
        if drop_idle_timer:
            self._clearing_drops_transcript.add(session_id)
            # Not summed up later either: there is nothing left to sum up.
            self._drop_idle_timer(session_id)
        return True

    def _in_use_excluding_clearing(self, session_id: str) -> bool:
        """Whether anything other than a ``hold_files`` clearing lock currently uses ``session_id``."""
        if session_id in self._held_by or session_id in self._compacting or session_id in self._copying_from:
            return True
        in_hand = (*self._sessions, *self._queue, *self._taking_up, *self._requeueing)
        return any(self._session_of.get(commission_id) == session_id for commission_id in in_hand)

    def _in_use(self, session_id: str) -> bool:
        """Whether something is at work in a session or on its way into it, or the host has claimed it for itself.

        A commission holds it; its files are being removed; it is being
        summed up; a copy of it is being made (looked at again later);
        or a commission that is worked on in it has a place, waits for
        one, or is being taken up again.
        """
        return session_id in self._clearing or self._in_use_excluding_clearing(session_id)

    def _in_use_session_ids(self) -> set[str]:
        """Return every session id currently considered in use in memory."""
        ids: set[str] = set(self._held_by) | set(self._clearing) | set(self._compacting) | set(self._copying_from)
        for commission_id in (*self._sessions, *self._queue, *self._taking_up, *self._requeueing):
            sid = self._session_of.get(commission_id)
            if sid:
                ids.add(sid)
        return ids

    def release_files(self, session_id: str) -> None:
        """Give back what ``hold_files`` claimed; a start that waited goes on."""
        self._clearing_drops_transcript.discard(session_id)
        clearing = self._clearing.pop(session_id, None)
        if clearing is not None:
            clearing.set()

    @staticmethod
    def _dirs_overlap(a: str, b: str) -> bool:
        """Return whether two normalized directory paths are equal or nested inside each other."""
        if a == b:
            return True
        pa = a if a.endswith(os.sep) else a + os.sep
        pb = b if b.endswith(os.sep) else b + os.sep
        return a.startswith(pb) or b.startswith(pa)

    def hold_workdir(self, workdir: Any) -> str | None:
        """Claim a working directory for removal. Returns the hold key, or None if overlapping work is active."""
        norm = os.path.normcase(os.path.normpath(str(workdir)))
        if any(self._dirs_overlap(norm, held) for held in self._clearing_workdirs):
            return None
        if any(self._dirs_overlap(norm, active) for active in self._starting_workdirs.values()):
            return None
        self._clearing_workdirs[norm] = asyncio.Event()
        self._clearing_workdirs_snapshot = tuple(self._clearing_workdirs)
        return norm

    def release_workdir(self, key: str) -> None:
        """Release a working-directory hold claimed via ``hold_workdir``."""
        ev = self._clearing_workdirs.pop(key, None)
        self._clearing_workdirs_snapshot = tuple(self._clearing_workdirs)
        if ev is not None:
            ev.set()

    def is_workdir_clearing(self, workdir: Any) -> bool:
        """Thread-safely report whether an overlapping directory is currently held for removal."""
        snapshot = self._clearing_workdirs_snapshot
        if not snapshot:
            return False
        norm = os.path.normcase(os.path.normpath(str(workdir)))
        return any(self._dirs_overlap(norm, held) for held in snapshot)

    async def wait_workdir_clear(self, workdir: Any) -> None:
        """Wait until no cleanup holds a directory overlapping ``workdir``."""
        norm = os.path.normcase(os.path.normpath(str(workdir)))
        while True:
            ev = next(
                (event for held, event in self._clearing_workdirs.items() if self._dirs_overlap(norm, held)),
                None,
            )
            if ev is None:
                return
            await ev.wait()

    async def _claim_workdir_for_start(self, commission_id: str, workdir: Any) -> bool:
        """Claim ``workdir`` for a starting commission, waiting if an overlapping directory is being cleared."""
        norm = os.path.normcase(os.path.normpath(str(workdir)))
        ev = next(
            (event for held, event in self._clearing_workdirs.items() if self._dirs_overlap(norm, held)),
            None,
        )
        if ev is not None:
            await ev.wait()
            return False
        self._starting_workdirs[commission_id] = norm
        return True

    def hold_session(self, commission_id: str) -> bool:
        """Claim a commission for writing its review down. False when the commission is in use.

        In use means it holds a session, waits for a place, or is being
        taken up again. Looking and claiming happen with no await in
        between. Until ``release_session``, the commission is not taken up
        again: what is written is about the commission as it was.
        """
        if (
            commission_id in self._sessions
            or commission_id in self._queue
            or commission_id in self._taking_up
            or commission_id in self._requeueing
            or commission_id in self._held
        ):
            return False
        self._held[commission_id] = asyncio.Event()
        return True

    def release_session(self, commission_id: str) -> None:
        """Give back what ``hold_session`` claimed; a start that waited goes on."""
        held = self._held.pop(commission_id, None)
        if held is not None:
            held.set()

    def _session_dir_of(self, commission: Any) -> Any:
        """Return where a session is kept. Blocking: it looks at files.

        ``commission`` is whatever has the session's ``id``, ``project``,
        ``session_dir`` and ``workdir`` on record: a ``ledger.Session``,
        with the working directory its first commission brings while it
        has none itself. (Named for what it was when a commission and its
        session were one; what follows says commission in that sense.)

        Where it was when the commission was first taken up, when that is on
        record: the data root may have been moved since through the storage
        settings, and the sessions do not move with it. Worked out from the
        root of now, a commission taken up again after such a move would
        find an empty directory and start from nothing instead of going on.

        Unless nothing was ever said there, and something was below the root
        of now: the user moved the whole data folder by hand and pointed the
        application at it. The sessions came along then; the place on record
        is where they used to be, and would be made anew, empty.
        """
        current = self._paths.session_dir(commission.id, commission.project)
        recorded = self._recorded_session_dir(commission)
        if recorded is None:
            return current
        if not _has_transcript(recorded) and _has_transcript(current):
            return current
        if not recorded.exists() and _has_files(current):
            # Moved by hand as above, and what was said there has been
            # removed since, being old (``SpaceKeeper``): the session is
            # still where its working directory is, not at the place on
            # record, which is gone.
            return current
        return recorded

    @staticmethod
    def _recorded_session_dir(commission: Commission) -> Any:
        """Return where the ledger has a commission's session, or None when it does not say."""
        from pathlib import Path

        if commission.session_dir:
            return Path(commission.session_dir)
        if commission.workdir:
            # Taken up by a build from before the place was recorded: its
            # working directory was, and is by default the ``work`` directory
            # of its session.
            workdir = Path(commission.workdir)
            if workdir.name == "work" and workdir.parent.name == commission.id:
                return workdir.parent
        return None

    def _workdir_of(self, commission: Commission, session_dir: Any) -> Any:
        """Return a commission's working directory, its session being kept in ``session_dir``.

        The one on record. When the sessions are no longer where the record
        has them (``_session_dir_of``: the data folder was moved by hand), a
        working directory that was among them went along with them: the
        ``work`` directory of its own session, or a directory inside that of
        the commission it was started by. It is that directory where the
        sessions are now; at the place on record there would be an empty one,
        made anew, and none of what the task agent had written. A directory
        elsewhere (a project of the user's) is where it was.
        """
        from pathlib import Path

        if not commission.workdir:
            return self._paths.work_dir(commission.id, commission.project)
        workdir = Path(commission.workdir)
        recorded = self._recorded_session_dir(commission)
        if recorded is None:
            return workdir
        try:
            among = workdir.relative_to(recorded.parent)
        except ValueError:
            return workdir
        # (The directory it was, when the session is where the record has it.)
        return session_dir.parent / among

    async def _child_workdir(self, parent: Commission, cwd: Any) -> str | None:
        """Return the working directory of a task agent the parent starts.

        Inside its working directory a task agent writes without being asked, so
        a child may only work inside its parent's. A directory elsewhere falls
        back to the parent's own: the child then has to ask before writing
        there, exactly as the parent has to.
        """
        from pathlib import Path

        from nekopower.runtime.permissions import is_inside

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
        lead, members = await self._team(self._at_work_in(session_id))
        if lead is None:
            return {"agents": []}
        agents = [{"name": LEAD_NAME, "status": lead.status, "description": lead.title}]
        agents.extend(
            {"name": member.name or member.id, "status": member.status, "description": member.title}
            for member in members
        )
        return {"agents": agents}

    async def _cb_subagent_send(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        target = await self._member(self._at_work_in(session_id), str(payload.get("agent") or ""))
        if target is None:
            raise RuntimeError(f"no agent named {payload.get('agent')!r}")
        message = str(payload.get("message") or "")
        if not await self.tell(target.id, message):
            raise RuntimeError(f"agent {payload.get('agent')!r} is not running ({target.status})")
        return None

    async def _cb_subagent_interrupt(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        session_id = self._at_work_in(session_id)
        target = await self._member(session_id, str(payload.get("agent") or ""))
        if target is None or target.id == session_id:
            raise RuntimeError(f"no other agent named {payload.get('agent')!r}")
        await self._ledger.transition(target.id, "cancelled", reason="interrupted by another task agent")
        await self.abort(target.id)
        return None
