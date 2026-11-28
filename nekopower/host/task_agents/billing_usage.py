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

"""Model tier resolution, free-quota metering, token usage accounting, and budget enforcement.

Method-only mixin: every instance attribute is initialized in
``TaskAgentManager.__init__`` (``nekopower.host.task_agents.manager``).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from collections.abc import AsyncIterator
from typing import Any

from nekopower.host.ledger import Commission, Session
from nekopower.host.paths import DEFAULT_PROJECT
from nekopower.host.permissions import OPEN_SESSION, USAGE_LIMIT
from .types import (
    logger,
    _QUOTA_RUN_REASON,
    _COPY_CHAIN_MAX,
    _LAST_REQUESTS_KEPT,
    _USAGE_REASON,
    _until_tomorrow,
    _Session,
    _UsageCut,
)

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.task_agents`` via ``monkeypatch.setattr``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import task_agents as _task_agents_facade


class BillingUsageMixin:
    """Model selection, quota metering, usage accounting, and budget methods."""

    async def change_duty(
        self, session_id: str, preset: str, *, by: str, commission_id: str | None = None
    ) -> dict[str, Any]:
        """Change the preset of a session: what its task agent may do and is told of its role, from its next start.

        ``by`` says who changes it (``ledger.PRESET_CHANGERS``), and is
        always given: left to a default, a caller that is not the user
        would pass for the user, who alone is not asked.

        For a member of a project and a session of one commission, to
        any preset but the leader's (whether a session leads a project
        or works in it is not its preset's to say). A run under way goes
        on with the tools it was opened with; the session's next start
        puts the head of its requests together anew, for the preset it
        has then, and tells it of the change (``_start``). The model it
        is sent to is settled anew, by the preset it has now
        (``_model_as``), written with the preset: from its next start too.

        To a preset that can change files from one that cannot
        (``Template.changes_files``), the user is asked first as for a
        new member that can (``OPEN_SESSION``), under ``commission_id``,
        unless it is the user who changes it: that is their word
        already. A member is then given its place in the project as a
        new one that changes files would be (``seat_of_new_member``):
        beside one that works in the project's directory it works in a
        git worktree of its own. One that no longer changes files is
        taken off those the project's other members wait for. While a
        worktree of it holds work that is not landed, it is given no
        preset that cannot change files: what clashes when that work is
        landed goes back to it to be merged.

        Returns ``{"session_id", "preset", "was", "seat", "changed",
        "widened", "at_work"}``; ``at_work`` says a run of it is under
        way, which the change does not reach. Raises ``LookupError`` for
        a session that is not on record, ``PermissionError`` when the
        user did not allow it, and ``ValueError`` with a code:
        ``not_changeable`` (its kind), ``ended`` (it takes no work any
        more), ``bad_preset`` (none of that name that can be used, or
        the leader's), ``tree_not_landed``, ``no_commission`` (nothing
        to ask the user under) and ``changed_meanwhile``.
        """
        from config.prompts.prompts_nekopower_presets import PRESET_CHANGE_NOT_ALLOWED
        from nekopower.host.ledger import PRESET_CHANGED_KINDS, PRESET_OF_KIND, TREE_STATES_PENDING
        from nekopower.host.templates import SHADOW

        kept = await self._ledger.get_session(session_id)
        if kept is None:
            raise LookupError(session_id)
        if kept.kind not in PRESET_CHANGED_KINDS:
            raise ValueError("not_changeable")
        if not await self._ledger.session_alive(kept):
            raise ValueError("ended")
        if not isinstance(preset, str) or not preset.strip() or preset in (PRESET_OF_KIND["project"], SHADOW):
            raise ValueError("bad_preset")
        at_work = self._held_by.get(kept.id) is not None
        if preset == kept.preset:
            return {
                "session_id": kept.id, "preset": kept.preset, "was": kept.preset, "seat": kept.seat,
                "changed": False, "widened": False, "at_work": at_work,
            }
        try:
            after = await self._template_of(dataclasses.replace(kept, preset=preset))
        except RuntimeError:
            raise ValueError("bad_preset") from None
        try:
            before = bool((await self._template_of(kept)).changes_files)
        except RuntimeError:
            # One whose preset cannot be used is not opened: it changes nothing as it stands.
            before = False
        widens = bool(after.changes_files) and not before
        narrows = before and not after.changes_files
        # Whatever it can do as it stands: one whose preset cannot be read just now (its
        # template was removed, or is wrong) may have a worktree all the same, from before.
        reads = not after.changes_files
        if reads and await self._ledger.trees(session_id=kept.id, states=sorted(TREE_STATES_PENDING)):
            raise ValueError("tree_not_landed")
        if widens and by != "user":
            if not commission_id:
                raise ValueError("no_commission")
            decision = await self._permissions.check(
                commission_id,
                OPEN_SESSION,
                detail={"tool": "change_duty", "title": kept.duty or kept.name, "preset": preset},
            )
            if not decision.allow:
                raise PermissionError(PRESET_CHANGE_NOT_ALLOWED.format(reason=decision.reason or "declined"))
        seat = None
        if kept.kind == "line" and kept.seat != "own" and (widens or narrows or (reads and kept.seat == "tree")):
            # (Before it is written: it is not yet one of those the project's directory is worked in by.)
            # One that cannot change files has no worktrees made for it, whatever it could do before:
            # a start of it that is under way, and read its seat before this, makes none after it
            # (``Ledger.add_tree``).
            in_project = bool(kept.project) and kept.project != DEFAULT_PROJECT
            # (Not by itself: one whose preset cannot be read counts as one that changes files.)
            seat = await self.seat_of_new_member(str(kept.project), but=kept.id) if widens and in_project else ""
        # Where it is sent from its next start, by the preset it is given: chosen before that is
        # written and written with it, so that a start that reads its row meanwhile reads the
        # preset and the model of one and the same.
        model = await self._model_as(kept, preset)
        # (Its worktrees are looked for once more as it is written: one may have been made since.)
        changed, was, _on = await self._ledger.change_preset(
            kept.id, preset, by=by, was=kept.preset, seat=seat, without_trees=reads, model=model
        )
        if changed is None:
            raise LookupError(session_id)
        if changed.seat == "tree" and at_work and kept.id not in self._tree_sessions:
            # A run of it is under way in the project's directory, whatever seat it has from
            # here on: the seat is for the commissions it starts after this. Until this one is
            # over it counts as one that writes there (``_works_in_tree``), and whoever waits
            # for it goes on waiting.
            self._direct_now.add(kept.id)
        # Whether it changes files in its project is looked at again: one that does no more is
        # waited for no longer (a run of it under way keeps what it was opened with, and is
        # looked at when it is over), and what waited for it may start.
        await self._note_writer(kept.id)
        return {
            "session_id": changed.id, "preset": changed.preset, "was": was, "seat": changed.seat,
            "changed": changed.preset != was, "widened": widens, "at_work": at_work,
        }

    async def _template_of(self, kept: Session) -> Any:
        """Return the template a session is opened by (``templates.Template``); raises when its preset cannot be used.

        A copy is opened as the session it was copied from (and without
        the tools every copy is without, ``_spec``): its preset is no
        template. One whose template cannot be used is not opened by
        another in its place: what it would then be allowed is not what
        its user set down.
        """
        from config.prompts.prompts_nekopower_presets import (
            PRESET_COPIED_TOO_OFTEN,
            PRESET_OF_STARTER_UNUSABLE,
            PRESET_UNUSABLE,
        )
        from nekopower.host.ledger import PRESET_OF_KIND
        from nekopower.host.templates import LEADER_WITHHELD, SHADOW, SOURCE_BUILTIN, TemplateError, builtin_templates

        source = kept
        for _ in range(_COPY_CHAIN_MAX):
            if source.kind != "fork" or not source.forked_from:
                break
            origin = await self._ledger.get_session(source.forked_from)
            if origin is None:
                break
            source = origin
        else:
            if source.kind == "fork" and source.forked_from:
                # Still a copy of something after as many steps as are
                # followed: the session it began with is there, and what
                # its preset withholds is not dropped for not looking further.
                raise RuntimeError(PRESET_COPIED_TOO_OFTEN.format(most=_COPY_CHAIN_MAX))
        if source.kind == "fork" or source.preset == SHADOW:
            # A copy whose origin is not on record any more: nothing to take
            # after. It has what every copy has, and no role of its own.
            return builtin_templates()["general"]
        try:
            template = await asyncio.to_thread(self.templates.resolve, source.preset, source.project)
        except TemplateError as exc:
            raise RuntimeError(PRESET_UNUSABLE.format(name=source.preset, reason=exc)) from exc
        if source.kind == "line" and source.preset == PRESET_OF_KIND["project"] and template.source == SOURCE_BUILTIN:
            # The one session a project had in a build without leaders: it
            # did the work under the leader's preset, and goes on doing it
            # as one that works. Under a template of the user's that stands
            # in the leader's place it keeps that one, and what it withholds.
            try:
                template = await asyncio.to_thread(self.templates.resolve, PRESET_OF_KIND["line"], source.project)
            except TemplateError as exc:
                raise RuntimeError(PRESET_UNUSABLE.format(name=PRESET_OF_KIND["line"], reason=exc)) from exc
        if source.kind == "project":
            # A leader does none of the work, whatever template stands in
            # the built-in one's place: what it hands out is done by those
            # it hands it to.
            missing = tuple(tool for tool in LEADER_WITHHELD if tool not in template.withheld())
            if missing:
                template = dataclasses.replace(template, withhold_own=(*template.withhold_own, *missing))
        if source.kind in ("child", "line") and source.parent_id:
            # (A member a leader handed a commission to keeps what the
            # leader is without itself only: ``Template.under``.)
            # A task agent another one started is that one's hands: it works
            # in the same directory, on the same commission, with what the
            # user allowed that commission. What the starter's preset keeps
            # from it, its task agent does not hand back. (No built-in preset
            # both withholds a tool and starts task agents; a template of the
            # user's may.) A starter that is not on record any more withholds
            # nothing that can be told.
            starter = await self._ledger.get_session(source.parent_id)
            if starter is not None:
                try:
                    over = await self._template_of(starter)
                except RuntimeError as exc:
                    # Said as what it is: the preset named is the starter's,
                    # not the one this task agent was started with.
                    raise RuntimeError(PRESET_OF_STARTER_UNUSABLE.format(reason=exc)) from exc
                template = template.under(over)
        return template

    # -- which model a session is sent to ----------------------------------

    async def model_default_of(self, preset: str, project: str | None) -> str:
        """Return a preset's default model for a project, as its template says (``Template.model``)."""
        from nekopower.host.role_models import INHERIT
        from nekopower.host.templates import TemplateError, builtin_templates

        try:
            template = await asyncio.to_thread(self.templates.resolve, preset, project)
        except TemplateError:
            # A session of it is not opened (``_template_of``); what it
            # would be sent to is said as the built-in preset of the name has it.
            found = builtin_templates().get(preset)
            return found.model if found is not None else INHERIT
        return template.model

    async def session_model(self, kept: Session, *, record: bool = True) -> Any:
        """Return the model a session's task agent is sent to now (``role_models.Choice``).

        Settled when the session is made: by the setting of its preset,
        else its default, and written on its row (with ``record``; a look
        that is only a look leaves the row alone). A session that was
        settled is sent to the slot it was settled on, which is read anew
        (an address or a key the user changed there is taken); when that
        slot cannot be used any more, to its preset's default, with why
        on its row. A copy is sent where the session it was copied from is.
        """
        return await self._session_model(kept, record=record, depth=0)

    async def _session_model(self, kept: Session, *, record: bool, depth: int) -> Any:
        from nekopower.host.role_models import LASTING_REASONS, SlotUnavailable, dataclass_replace, recorded

        if depth > _COPY_CHAIN_MAX:
            # Started by, or copied from, more sessions in a row than are
            # followed: as if there were none to follow.
            return await self.role_models.agent_default()
        if kept.kind == "fork" and kept.forked_from:
            origin = await self._ledger.get_session(kept.forked_from)
            if origin is not None:
                choice = await self._session_model(origin, record=record, depth=depth + 1)
                await self._keep_model(kept, choice, record)
                return choice
        if recorded(kept):
            try:
                choice = dataclass_replace(
                    await self.role_models.again(kept.model_slot, kept.model_ident, kept.model_tier),
                    fallback=kept.model_fallback,
                )
            except SlotUnavailable as exc:
                logger.info(
                    "the model slot session %s was settled on cannot be used (%s); its preset's default is",
                    kept.id,
                    exc.code,
                )
                choice = await self._choose_model(kept, record=record, depth=depth, use_setting=False, fallback=exc.code)
                if exc.code not in LASTING_REASONS:
                    # Perhaps only for now: sent to the default this time,
                    # and what it was settled on stays.
                    return choice
        elif record:
            return await self._settle_first(kept, depth=depth)
        else:
            choice = await self._choose_model(kept, record=record, depth=depth)
        await self._keep_model(kept, choice, record)
        return choice

    async def _settle_first(self, kept: Session, *, depth: int) -> Any:
        """Settle a session that was not settled, one settling of it at a time; see ``session_model``.

        Whoever comes second finds it settled by the first, and goes by that.
        """
        from nekopower.host.role_models import recorded

        lock, users = self._settle_locks.get(kept.id, (asyncio.Lock(), 0))
        self._settle_locks[kept.id] = (lock, users + 1)
        try:
            async with lock:
                fresh = await self._ledger.get_session(kept.id) or kept
                if recorded(fresh):
                    return await self._session_model(fresh, record=True, depth=depth)
                choice = await self._choose_model(fresh, record=True, depth=depth)
                await self._keep_model(fresh, choice, True)
                return choice
        finally:
            # Kept while anyone else holds or waits for it: one that came
            # now would otherwise make another, and settle beside them.
            held, left = self._settle_locks[kept.id]
            if left <= 1:
                del self._settle_locks[kept.id]
            else:
                self._settle_locks[kept.id] = (held, left - 1)

    async def _choose_model(
        self, kept: Session, *, record: bool, depth: int, use_setting: bool = True, fallback: str = ""
    ) -> Any:
        """Return the model a session is settled on by its preset (``RoleModels.choose``)."""

        async def inherited() -> Any:
            # The session that started it; else, for one that no session
            # started, the lead of its project.
            if kept.parent_id:
                lead = await self._ledger.get_session(kept.parent_id)
            elif kept.kind != "project":
                lead = await self._ledger.lead_session(kept.lanlan_name, kept.project)
            else:
                lead = None
            if lead is None:
                return None
            return await self._session_model(lead, record=record, depth=depth + 1)

        return await self.role_models.choose(
            kept.preset, kept.project, inherited, use_setting=use_setting, fallback=fallback
        )

    async def _keep_model(self, kept: Session, choice: Any, record: bool) -> None:
        """Write on a session's row what it is sent to, where that is not what it says already."""
        from nekopower.host.role_models import recorded, same_as_kept

        if not choice.ident and recorded(kept):
            # No model is configured where it goes just now: what it was
            # settled on stays, to be read again the next time.
            return
        if record and not same_as_kept(kept, choice):
            try:
                # As chosen for the preset it was read with: for one it has no longer, it is not
                # written over what was settled for the one it has (``change_duty``).
                await self._ledger.keep_model(kept.id, kept.preset, **choice.columns())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a session settled before goes on; it is written the next time
                if not recorded(kept):
                    # Not settled before: opened now, it could be opened by
                    # another model next time, its conversation and all.
                    raise
                logger.warning("writing down the model of session %s failed", kept.id)

    async def model_of_commission(self, commission_id: str) -> dict[str, Any] | None:
        """Return what the session of a commission would be opened with now (looked at, not settled); None without a model.

        A commission that has no session yet: what a new one of hers would
        be. One at work in an open session: the model that session was
        opened with.
        """
        live = self._sessions.get(commission_id)
        if live is not None and live.model is not None:
            return {"base_url": live.model[0], "model": live.model[1]}
        commission = await self._ledger.get(commission_id)
        kept = await self._ledger.get_session(commission.session_id) if commission is not None else None
        choice = await self.session_model(kept, record=False) if kept is not None else await self._new_session_model()
        return choice.config

    async def _model_as(self, kept: Session, preset: str) -> dict[str, Any]:
        """Return which model a session is sent to as another preset, as its row says it (``Choice.columns``).

        As for one that is just made of that preset: its setting, else
        its default, which may be to follow the session that started it
        or the lead of its project (``_choose_model``). Whoever gives it
        the preset writes this with it (``Ledger.change_preset``), in
        place of what it was settled on; a run under way goes on with
        the model it was opened with, and the next start is sent to this
        one. Empty where it stays where it was: it is sent there
        already, or none can be told just now (the settings cannot be
        read, no model is configured there). (Nothing sets the model of
        one session by hand yet; one that was so set would keep it
        here.)
        """
        from nekopower.host.role_models import same_as_kept

        try:
            choice = await self._choose_model(dataclasses.replace(kept, preset=preset), record=True, depth=0)
            if choice.ident and not same_as_kept(kept, choice):
                return dict(choice.columns())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it goes on with the model it has
            logger.warning("choosing the model of session %s as another preset failed", kept.id, exc_info=True)
        return {}

    async def settle_model(self, session: Session) -> None:
        """Settle the model of a session that was just made (``session_model``), unless it is settled.

        One that cannot be settled now is settled when it is first opened.
        """
        from nekopower.host.role_models import recorded

        if recorded(session):
            return
        try:
            await self.session_model(session)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - settled when it is opened instead
            logger.warning("settling the model of session %s failed; it is settled when it is opened", session.id)

    async def _goes_free(self, kept: Session | None) -> bool:
        """Return whether what a session would send now goes with the built-in free key.

        By where it is sent as things are now (``session_model``, looked
        at): its slot may have been given a key of the user's own since it
        last ran. None (a new one), by what it would be settled on. Not
        known: as if it did, so it is counted as before there were settings.
        """
        try:
            choice = await (self.session_model(kept, record=False) if kept is not None else self._new_session_model())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - counted, as it was before
            logger.warning("whether a session goes with the free key could not be told; it is counted")
            return True
        return bool(choice.free)

    async def _charge(self, free: bool, key: str) -> bool:
        """Count a model request against the free daily quota, for a session whose requests go with the free key.

        Returns whether it may be made. One that goes with another key
        is not counted and always may.
        """
        if not free or self._meter is None:
            return True
        return bool(await self._meter(key))

    async def _pay_first_request(self, session: _Session) -> bool:
        """Count a session's first model request before it is made; False when the quota is used up.

        A meter that cannot count does not stop the work. One that was
        counted before the commission was woken (``_count_ahead``) is not
        counted again.
        """
        if self._meter is None:
            return True
        if session.commission_id in self._prepaid_starts:
            self._prepaid_starts.discard(session.commission_id)
            session.prepaid_requests += 1
            return True
        if not session.free:
            # Not sent with the free key: nothing is counted.
            return True
        try:
            allowed = await self._meter(session.commission_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a meter that cannot count does not stop the work
            logger.warning("counting a task agent's model request failed", exc_info=True)
            return True
        self._note_quota(bool(allowed))
        if allowed:
            session.prepaid_requests += 1
        return bool(allowed)

    def _note_quota(self, allowed: bool, *, asked_again: bool = False) -> None:
        """Remember whether the quota had a request left; what waited for it is tried again when it has.

        ``asked_again``: it was asked though it was known to be used up
        (the time to ask again had come), so a refusal sets that time anew.
        """
        if allowed and self._quota_out:
            self._quota_out = False
            self._kick_waker()
        elif not allowed and (asked_again or not self._quota_out):
            self._quota_out = True
            # The quota is for a day: asked again after the next midnight,
            # and not on every occasion until then.
            now = self._ledger._clock()
            self._quota_asked_again_at = now + _until_tomorrow(now)

    async def _note_usage(self, session_id: str, commission_id: str | None, event: dict[str, Any]) -> None:
        """Add what one model request used to its session and to the commission it was made for, if for one.

        As the runtime reports it, which is as the provider counted it:
        what was sent that no cache had, what was read from a cache and
        written to one, and what came back. A provider that does not say
        how much it read from a cache has it all counted as sent; nothing
        is estimated. A ledger that cannot be written does not stop the work.
        """

        def number(key: str) -> int:
            value = event.get(key)
            return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

        # Nothing is said of the conversation's length by the request that
        # summed it up.
        context = number("context_tokens") or None
        said = 0
        if context is not None:
            self._context_said += 1
            said = self._context_said
        await self._write_usage(
            (session_id, commission_id),
            (1, number("input"), number("output"), number("cache_read"), number("cache_write")),
            context,
            said,
        )

    async def _write_usage(
        self,
        key: tuple[str, str | None],
        amounts: tuple[int, int, int, int, int],
        context: int | None,
        said: int,
    ) -> None:
        """Write what requests used, with what the ledger did not take before for the same session and commission.

        The account catches up once the ledger takes it. The writing is
        a task of its own, carried through whatever becomes of whoever
        waits for it: the ledger writes in a thread, which a
        cancellation does not stop, so only what sees how the writing
        ended can tell whether it is still owed. Put down as owed by
        whoever was cancelled, what was written all the same would be
        written a second time.
        """
        if self._usage_reading and not self._closed:
            # The account of a commission is being read: put down as
            # owed, where whoever reads finds it, and written once the
            # reading is over (``_usage_read``). Begun now, it would be
            # in neither place for a moment.
            self._owe(key, amounts, context, said)
            for late in self._usage_late:
                self._owe(key, amounts, context, said, into=late)
            self._usage_put_off.add(key)
            return
        owed = self._usage_owed.pop(key, None)
        if owed is not None:
            amounts = (
                amounts[0] + owed[0],
                amounts[1] + owed[1],
                amounts[2] + owed[2],
                amounts[3] + owed[3],
                amounts[4] + owed[4],
            )
            if context is None:
                # As of the latest request that said how long it was.
                context, said = owed[5], owed[6]
        # Not a task shutting down gives up on (``_spawn``): cut off while
        # the ledger writes, it could no longer say that the writing
        # failed, and what was used would be neither written nor owed.
        writing = asyncio.ensure_future(self._usage_written(key, amounts, context, said))
        self._usage_writes.add(writing)
        writing.add_done_callback(self._usage_writes.discard)
        await asyncio.shield(writing)

    async def _usage_written(
        self,
        key: tuple[str, str | None],
        amounts: tuple[int, int, int, int, int],
        context: int | None,
        said: int,
    ) -> None:
        # One at a time for a session: another write of it that is under
        # way has to be through (taken, or not) before this one looks at
        # what the ledger has of the conversation's length.
        async with self._usage_locks.setdefault(key[0], asyncio.Lock()):
            # How long the conversation was, only when nothing said later
            # of the same session is written already: a request that is
            # written late is counted, and what it said of the length is
            # the older.
            newest = context is not None and said > self._context_written.get(key[0], 0)
            try:
                await self._ledger.add_usage(
                    session_id=key[0],
                    commission_id=key[1],
                    requests=amounts[0],
                    tokens_in=amounts[1],
                    tokens_out=amounts[2],
                    cache_read=amounts[3],
                    cache_write=amounts[4],
                    context_tokens=context if newest else None,
                )
            except Exception:  # noqa: BLE001 - what was used is for showing and for the limit; the work goes on
                logger.warning("writing down what a model request used failed; written later", exc_info=True)
                # With what came to be owed meanwhile, whichever of the
                # two said later how long the conversation was.
                self._owe(key, amounts, context, said)
            else:
                if newest:
                    self._context_written[key[0]] = max(said, self._context_written.get(key[0], 0))

    def _owe(
        self,
        key: tuple[str, str | None],
        amounts: tuple[int, int, int, int, int],
        context: int | None,
        said: int,
        *,
        into: dict[Any, Any] | None = None,
    ) -> None:
        """Put what requests used down as owed, with what is owed of the same session and commission already.

        ``into``: noted there instead, in the same way.
        """
        owed = self._usage_owed if into is None else into
        before = owed.get(key, (0, 0, 0, 0, 0, None, 0))
        # How long the conversation was, as of whichever said so later.
        length = (context, said) if context is not None and said > before[6] else (before[5], before[6])
        owed[key] = (
            amounts[0] + before[0],
            amounts[1] + before[1],
            amounts[2] + before[2],
            amounts[3] + before[3],
            amounts[4] + before[4],
            *length,
        )

    @contextlib.asynccontextmanager
    async def _usage_read(self) -> AsyncIterator[None]:
        """Keep what was used where it is while the account of a commission is read.

        What was used is in the ledger or owed, and while it is being
        written in neither: taken from what is owed, and not in the
        ledger yet. So the writes under way are waited for first (each
        ends in the ledger or owed again), and none begins until the
        reading is over: what is to be written meanwhile is put down
        as owed (``_write_usage``), and written right after. Whoever
        reads the ledger and what is owed in here reads each thing
        that was used once.
        """
        self._usage_reading += 1
        try:
            await self._usage_at_rest()
            yield
        finally:
            self._usage_read_over()

    async def _usage_at_rest(self) -> None:
        """Wait until no write of usage is under way: each has ended in the ledger, or as owed again."""
        while self._usage_writes:
            await asyncio.wait(list(self._usage_writes))

    def _usage_read_over(self) -> None:
        """Let usage be written again, now that an account is read; what was put off is written."""
        self._usage_reading -= 1
        if not self._usage_reading and self._usage_put_off:
            # What was put off is written now, and that only: what was
            # owed before this, because the ledger did not take it, is
            # tried again when it always was, and not with every
            # reading. With the writes of usage, which shutting down
            # waits for and does not cut off.
            put_off, self._usage_put_off = self._usage_put_off, set()
            if not self._closed:
                writing = asyncio.ensure_future(self._write_put_off(put_off))
                self._usage_writes.add(writing)
                writing.add_done_callback(self._usage_writes.discard)

    async def _write_put_off(self, keys: set[tuple[str, str | None]]) -> None:
        """Write what was put off while the account of a commission was read, each once."""
        for key in keys:
            if key in self._usage_owed:
                await self._write_usage(key, (0, 0, 0, 0, 0), None, 0)

    async def _settle_usage(self) -> None:
        """Write what is owed of usage before the host is gone, as far as that can be done in the time a start gets.

        What is owed is tried once more, each once. Writes under way
        are waited for and not cancelled (how each ends is for it to
        tell), and what one of them turns out to have failed with
        while this goes on is tried as well. What is still owed after
        that, or still being written, is lost with the host.
        """
        loop = asyncio.get_running_loop()
        until = loop.time() + _task_agents_facade._SHUTDOWN_START_WAIT_SECONDS
        tried: set[tuple[str, str | None]] = set()

        def being_written(session_id: str) -> bool:
            lock = self._usage_locks.get(session_id)
            return lock is not None and lock.locked()

        while loop.time() < until:
            # What is owed first, and not what a write under way holds up
            # (one write of a session at a time): that is looked at again
            # once the write is through.
            owed = [key for key in self._usage_owed if key not in tried and not being_written(key[0])]
            if owed:
                tried.update(owed)
                for key in owed:
                    if key not in self._usage_owed:
                        continue
                    try:
                        await asyncio.wait_for(
                            self._write_usage(key, (0, 0, 0, 0, 0), None, 0), max(0.01, until - loop.time())
                        )
                    except asyncio.TimeoutError:
                        break
                continue
            if not self._usage_writes:
                break
            await asyncio.wait(
                list(self._usage_writes),
                timeout=max(0.0, until - loop.time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
        if self._usage_owed or self._usage_writes:
            logger.warning("what model requests used could not all be written before the host shut down")

    def _settle_usage_of(self, session_id: str) -> None:
        """Have what is owed of a session's usage written, now that nothing is at work in it.

        Without this it would be written only with the next request of
        the same session and commission, and the last request of a
        commission has none after it. Tried once; what the ledger does
        not take this time either stays owed, and the host tries once
        more when it shuts down.
        """
        if not self._closed and any(key[0] == session_id for key in self._usage_owed):
            self._spawn(self._write_owed_usage(session_id))

    async def _write_owed_usage(self, session_id: str | None = None) -> None:
        """Try once to write what is owed of one session's usage, or of every session's."""
        for key in [key for key in self._usage_owed if session_id is None or key[0] == session_id]:
            if key in self._usage_owed:
                await self._write_usage(key, (0, 0, 0, 0, 0), None, 0)

    async def _on_usage(self, session: _Session, event: dict[str, Any]) -> None:
        """Count one model request; a commission whose quota is used up fails.

        What it used is added to the session and the commission. Against
        the daily quota it is counted as one request, unless it is the
        request that summed the conversation up, which is none of the
        run's own and was never counted there. The runtime reports usage
        once per request, after the request, so the request that uses the
        last unit still completes.
        """
        if not event.get("summary"):
            # A copy begins its requests as the session it was copied from
            # does: what a provider keeps of the one is the other's too.
            from config.nekopower_settings import TASK_AGENT_FORK_CONTINUE_HOT_SECONDS

            now = asyncio.get_running_loop().time()
            if len(self._last_request_at) > _LAST_REQUESTS_KEPT:
                # One made longer ago than that says as much as none.
                for kept, at in list(self._last_request_at.items()):
                    if now - at > TASK_AGENT_FORK_CONTINUE_HOT_SECONDS:
                        del self._last_request_at[kept]
            self._last_request_at[session.session_id] = now
            if session.carries_on_for is not None:
                self._last_request_at[session.carries_on_for] = now
        await self._note_usage(session.session_id, session.commission_id, event)
        if self._meter is None or not session.free or session.closing or event.get("summary"):
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
        self._note_quota(bool(allowed))
        if allowed:
            return
        # After requests of its own: some of the work may be done.
        await self._fail(commission_id, _QUOTA_RUN_REASON, said="quota_run")
        await self._end_what_it_started(commission_id)
        await self._close_session(session)

    # -- what a commission may use ----------------------------------------

    async def _owed_tokens(self, root: str, owed_at: dict[Any, Any] | None = None) -> int:
        """Return what the work a commission belongs to used that the ledger has not taken yet.

        Of the commission no task agent started (``root``) and of those
        that descend from it. Reported by a runtime, and not in what
        the ledger says was used: left out, the work would go past
        what it may use without the user being asked.
        """
        total = 0
        # (``owed_at``: what was owed at some point, in place of what is now.)
        for (_session_id, commission_id), owed in list((self._usage_owed if owed_at is None else owed_at).items()):
            if commission_id is None:
                # A request the host made by itself: no commission's.
                continue
            if commission_id != root:
                try:
                    standing = await self._ledger.usage_standing(commission_id)
                except Exception:  # noqa: BLE001 - whose work it is cannot be told: not counted
                    continue
                if standing is None or standing.root != root:
                    continue
            total += owed[1] + owed[2] + owed[3] + owed[4]
        return total

    def _usage_settled(self, commission: Commission) -> None:
        """Forget what was noted of the usage of a commission that is over: whether more was refused for it."""
        self._winding_up.pop(commission.id, None)
        if commission.parent_id is None:
            self._refused_more.discard(commission.id)
            self._usage_asks.pop(commission.id, None)

    async def _usage_stopped_text(self, commission: Commission) -> str:
        """Return what she is told of a commission that was put down for what it used, in its language."""
        from config.prompts.prompts_nekopower_brain import USAGE_LIMIT_STOPPED_TEXT
        from nekopower.host.reports import tokens_text
        from nekopower.host.task_brain import _texts

        standing = await self._ledger.usage_standing(commission.id)
        used = standing.used if standing is not None else commission.tokens
        return _texts(USAGE_LIMIT_STOPPED_TEXT, commission.lang or "en").format(used=tokens_text(used))

    async def _cb_budget(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Say, before a model request of a session, whether the work it belongs to may use more.

        The built-in runtime asks this before every request of a run,
        between two steps (``runtime/host.py``): nothing of the run is
        under way then, so nothing is cut off. What is looked at is what
        the commission no task agent started has used, the task agents it
        started counted in (``Ledger.usage_standing``), against what it
        may: the limit (``_usage_limit``: what the user set, else
        ``TASK_AGENT_USAGE_LIMIT_TOKENS``), or what the user allowed
        since. Looked at once everything the session said before it asked
        is handled (``_caught_up``): what its last request used is written
        down by then, and a report that ended the commission has ended it.
        Only a commission that is at work is asked about.

        With room left, the answer is at once and empty. With none, the
        user is asked, as for a permission (the commission waits for the
        user meanwhile, and holds its session): how much was used, and
        that as much again is asked for. Allowed, that much more may be
        used from there. Refused, or with nobody to ask, the task agent
        is told to hand over what it has (``notice``), and has a few
        requests left for that; when those are used and it has not
        reported, the commission is put down as stuck and the run ends
        (``stop``). Every task agent of the same commission is told the
        same, and none of them asks the user a second time.
        """
        from config.nekopower_settings import TASK_AGENT_USAGE_WIND_UP_REQUESTS
        from config.prompts.prompts_nekopower_brain import TASK_AGENT_USAGE_WIND_UP
        from nekopower.host.reports import tokens_text

        del payload
        # Nothing of the run is under way here: where what another task agent
        # changed is being merged into the directory this one writes in, it
        # waits until that is through (``tree_work.py``).
        await self._pause_for_landing(session_id)
        limit = await self._usage_limit()
        commission_id = self._held_by.get(session_id)
        session = self._sessions.get(commission_id or "")
        if session is not None and session.carries_on_for is not None and not session.closing:
            # A copy that a commission is carried on in: one that called a
            # tool which changes something is handed back (``_watch_copy``,
            # seen once what the session said before it asked is handled).
            await self._caught_up()
            if self._sessions.get(commission_id or "") is session and session.hand_back is not None and not session.closing:
                verdict = await self._hand_back_step(session)
                if verdict is not None:
                    return verdict
                # (A request it has for writing down what it found, or one
                # whose commission ended meanwhile: what it may use is
                # looked at as for any.)
        if limit <= 0 or commission_id is None or session is None or session.closing:
            return None
        # Always, however far from the line the account stands: what is
        # still on its way from the runtimes has no bound to go by (a
        # window can be of any size, and a commission has several task
        # agents), so nothing is concluded from what is written down so
        # far alone.
        await self._caught_up()
        if self._sessions.get(commission_id) is not session or session.closing:
            # Over meanwhile: its run ends by itself.
            return None
        if any(key[0] == session_id for key in self._usage_owed):
            # What requests of this session used that the ledger did not
            # take: tried once more before the account is looked at.
            await self._write_owed_usage(session_id)
            if self._sessions.get(commission_id) is not session or session.closing:
                return None
        if session.wind_up_left is None and commission_id in self._winding_up:
            # More of a round in which it was told to hand over, in a
            # session opened anew (the one it was told in was closed
            # while it waited for an answer): with what it had left.
            session.wind_up_left = self._winding_up[commission_id]
            session.wind_up_told_again = False
        if session.wind_up_left is not None:
            return await self._wind_up_step(session)

        async def standing_over() -> Any:
            # What stands against it, when nothing more may be used; else None.
            commission = await self._ledger.get(commission_id)
            if commission is None or commission.status != "working":
                # It has reported how it ended, or waits for the user as it is.
                return None
            # With what was used and is not in the ledger yet (it did not
            # take it): used all the same, and counted once, here or there.
            # Read with no write of usage under way or beginning, which
            # would have it in neither place (a session that was let go
            # of has what it owes written by a task of its own).
            async with self._usage_read():
                standing = await self._ledger.usage_standing(commission_id)
                if standing is None:
                    return None
                owed = await self._owed_tokens(standing.root)
            if owed:
                standing = dataclasses.replace(standing, used=standing.used + owed)
            allowed = standing.allowed if standing.allowed is not None else limit
            return standing if standing.used >= allowed else None

        standing = await standing_over()
        if standing is None:
            return None
        if standing.root not in self._refused_more:
            async with self._usage_asks.setdefault(standing.root, asyncio.Lock()):
                # Looked at again: another task agent of the commission
                # may have asked meanwhile, and been answered.
                standing = await standing_over()
                if standing is None:
                    return None
                if standing.root not in self._refused_more:
                    # As much again, if the user allows it, from what had
                    # been used when they said so: begun the moment the
                    # answer is in (``_UsageCut``), and not when this
                    # gets to go on, which is a while later (the
                    # commission is put back to work first).
                    # Of this question's answer, and of no other's: one
                    # that was asked for the commission before it (by
                    # something it called, say) is answered first.
                    cut = _UsageCut(self)
                    try:
                        decision = await self._permissions.ask_each_time(
                            commission_id,
                            USAGE_LIMIT,
                            detail={"used": tokens_text(standing.used), "more": tokens_text(limit)},
                            at_answer=cut.begin,
                        )
                        if decision.allow:
                            # (Answered by a way that does not come by the
                            # session: it ends here, then.)
                            cut.begin()
                            # What was used before the answer and is not
                            # in the ledger is allowed on top: left out,
                            # that much of what was allowed would be
                            # gone the moment it is written.
                            owed = await cut.owed(standing.root)
                            await self._ledger.allow_usage(commission_id, limit + owed)
                    finally:
                        cut.end()
                    if decision.allow:
                        # What the ledger did not take before is tried
                        # once more (the user may have taken a while).
                        await self._write_owed_usage()
                        return None
                    self._refused_more.add(standing.root)
        if self._sessions.get(commission_id) is not session or session.closing:
            # Stopped while the user was asked.
            return {"stop": True}
        # The request this is asked before is the first of those it has left.
        session.wind_up_left = max(0, TASK_AGENT_USAGE_WIND_UP_REQUESTS - 1)
        self._winding_up[commission_id] = session.wind_up_left
        try:
            await self._ledger.append_event(
                commission_id, "usage_refused", {"used": standing.used, "requests_left": TASK_AGENT_USAGE_WIND_UP_REQUESTS}
            )
        except Exception:  # noqa: BLE001 - the note is for the record; the task agent is told all the same
            # Left untold, it would go on working on the requests it has
            # left, and be stopped without ever having been told to hand
            # over what it has.
            logger.warning("noting that more usage was refused failed", exc_info=True)
        return {"notice": TASK_AGENT_USAGE_WIND_UP.format(requests=TASK_AGENT_USAGE_WIND_UP_REQUESTS)}

    async def _wind_up_step(self, session: _Session) -> dict[str, Any] | None:
        """Let a task agent that is handing over what it has make one more request, or end its commission.

        It was told how many requests it has for that. When they are used
        and it has reported nothing that ended the commission, the
        commission is put down as stuck with the reason, what it started
        is stopped, and its session is closed.
        """
        if session.wind_up_left:
            left = session.wind_up_left
            session.wind_up_left -= 1
            self._winding_up[session.commission_id] = session.wind_up_left
            if not session.wind_up_told_again:
                # The first request of another run of the same round: what
                # it was told went with the run it was told in, and without
                # it the task agent would go on working on what it is to
                # hand over, on the requests it has left for that.
                from config.prompts.prompts_nekopower_brain import TASK_AGENT_USAGE_WIND_UP

                session.wind_up_told_again = True
                return {"notice": TASK_AGENT_USAGE_WIND_UP.format(requests=left)}
            return None
        commission_id = session.commission_id
        commission = await self._ledger.get(commission_id)
        if commission is not None and commission.status in ("working", "waiting_user"):
            text = await self._usage_stopped_text(commission)
            # Before its run is stopped: what the run says as it stops
            # must not turn it into a stopped one.
            session.wound_up = True
            await self._end(commission_id, "blocked", reason=_USAGE_REASON, text=text, result_summary=self._summary(text))
            await self._end_what_it_started(commission_id)
        await self._close_session(session)
        return {"stop": True}

    async def _usage_limit(self) -> int:
        """Return how many tokens the work on one commission may use before the user is asked; 0 for no limit.

        What the user set where they set something, else
        ``TASK_AGENT_USAGE_LIMIT_TOKENS``. Asked for each time it is looked
        at: a change holds from the next request on. A setting that cannot
        be read is none, and the default holds.
        """
        from config.nekopower_settings import TASK_AGENT_USAGE_LIMIT_TOKENS

        if self.usage_limit_of is not None:
            try:
                chosen = await self.usage_limit_of()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the default holds, as with nothing set
                logger.warning("reading the usage limit the user set failed", exc_info=True)
                chosen = None
            if chosen is not None and chosen > 0:
                return int(chosen)
        return int(TASK_AGENT_USAGE_LIMIT_TOKENS or 0)

    async def usage_view(self, commission: Any, standing: Any, kept: Any) -> dict[str, Any]:
        """Return ``usage_of`` from what was read of the ledger: the commission, ``Ledger.usage_standing`` of it, its session."""

        def counted(row: Any) -> dict[str, int]:
            return {
                "requests": row.requests,
                "input": row.tokens_in,
                "output": row.tokens_out,
                "cache_read": row.tokens_cache_read,
                "cache_write": row.tokens_cache_write,
                "tokens": row.tokens,
            }

        limit = await self._usage_limit()
        allowed = standing.allowed if standing is not None and standing.allowed is not None else limit
        return {
            "commission": counted(commission),
            # For a commission no task agent started, its own count as it
            # was read: one reading, so the two numbers agree.
            "used": standing.used if standing is not None and standing.root != commission.id else commission.tokens,
            "limit": allowed if limit > 0 else None,
            "session": ({**counted(kept), "context_tokens": kept.context_tokens} if kept is not None else None),
        }

    async def usage_of(self, commission_id: str) -> dict[str, Any] | None:
        """Return what a commission has used and what it may, for a display; None for an unknown one.

        ``commission`` is the commission's own count (the task agents it
        started included); ``limit`` what the work it belongs to may use
        before the user is asked, and ``used`` what that work has used
        (the same numbers, for a commission no task agent started);
        ``session`` what its session used over all its commissions, with
        the length of its conversation as of its last request. What the
        model's window is and where a conversation is summed up is told
        by ``ModelCapabilityStore.describe_window``.
        """
        commission = await self._ledger.get(commission_id)
        if commission is None:
            return None
        standing = await self._ledger.usage_standing(commission_id)
        kept = await self._ledger.get_session(commission.session_id)
        return await self.usage_view(commission, standing, kept)
