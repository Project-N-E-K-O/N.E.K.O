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

"""Idle-session compaction and service-session teardown for ``TaskAgentManager``.

Method-only mixin: every instance attribute is initialized in
``TaskAgentManager.__init__`` (``nekopower.host.task_agents.manager``).
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from nekopower.host.ledger import Session
from nekopower.host.runtimes import RUNTIME_BUILTIN
from .types import logger, _IDLE_COMPACT_KINDS, _IDLE_TIMER_MARGIN_SECONDS, _in_builtin

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.task_agents`` via ``monkeypatch.setattr``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import task_agents as _task_agents_facade


class IdleCompactMixin:
    """Idle-session compaction methods (see module docstring)."""

    # -- summing up idle sessions -------------------------------------------

    def _drop_idle_timer(self, session_id: str) -> None:
        self._idle_schedule_gen[session_id] = self._idle_schedule_gen.get(session_id, 0) + 1
        timer = self._idle_timers.pop(session_id, None)
        if timer is not None:
            timer.cancel()

    async def _idle_model(self, session_id: str) -> dict[str, Any] | None:
        """Return what a session would be opened with now (``session_model``, looked at only); None when it has no model.

        One that is not on record: what a new session would be. (Nothing
        is summed up for it: it is looked up when the time comes.)
        """
        kept = await self._ledger.get_session(session_id)
        if kept is not None and kept.preset_told and kept.preset_told != kept.preset:
            # Given another preset since its task agent last started, and with it the model it
            # is sent to from its next start (``change_duty``): what is on record is
            # where it goes next, not where it last ran. Nothing of it goes there until the
            # user goes on with it, as below.
            return None
        if kept is not None and kept.model_was and kept.model_was != kept.model_ident:
            # The same where the preset it has is the one it was told of: given another and
            # then this one again, with what this one is sent to set to another model meanwhile.
            return None
        choice = await self.session_model(kept, record=False) if kept is not None else await self._new_session_model()
        config = choice.config
        if kept is not None and kept.model_ident and choice.ident != kept.model_ident:
            # Not where it last ran: nothing of it goes there until the
            # user goes on with it.
            return None
        return config if config and config.get("model") else None

    async def _idle_after(self, session_id: str) -> float:
        """Return how long a session is idle before it is summed up, in seconds.

        What the user set for the model the session is sent to, else the
        setting (``ModelCapabilityStore.idle_compact_after``).
        """
        from config.nekopower_settings import TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS

        if self._model_capabilities is not None:
            try:
                config = await self._idle_model(session_id)
                if config is not None:
                    return float(
                        await self._model_capabilities.idle_compact_after(config.get("base_url"), config.get("model"))
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - not knowing is the same as nothing set
                logger.warning("looking up how long a session is idle before it is summed up failed", exc_info=True)
        return float(TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS)

    async def _idle_compact_wanted(self, session_id: str) -> bool:
        """Return whether the user has idle sessions of the model a session is sent to summed up (``idle_compact_on``).

        True when it cannot be told: it is on unless switched off, and what
        decides which model a session is sent to is checked apart from this
        (``_task_model_key``).
        """
        if self._model_capabilities is None:
            return True
        try:
            config = await self._idle_model(session_id)
            if config is not None:
                return await self._model_capabilities.idle_compact_on(config.get("base_url"), config.get("model"))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not knowing is the same as nothing set
            logger.warning("looking up whether idle sessions are summed up failed", exc_info=True)
        return True

    @staticmethod
    def _model_key_of(config: dict[str, Any] | None) -> str | None:
        """Return the key (address and name) of the model a configuration names; None when it names none."""
        from nekopower.host.model_capabilities import model_key

        if not config or not config.get("model"):
            return None
        return model_key(config.get("base_url"), config.get("model"))

    async def _task_model_key(self, session_id: str) -> str | None:
        """Return the key of the model a session is sent to as things are now; None when that cannot be told."""
        try:
            return self._model_key_of(await self._idle_model(session_id))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not knowing is the same as no model
            logger.warning("looking up the model of a task agent session failed", exc_info=True)
            return None

    async def _schedule_idle_compact(
        self,
        session_id: str,
        *,
        after: float | None = None,
        idle_for: float | None = None,
        model: str | None = None,
    ) -> None:
        """Have a session summed up once nothing has been at work in it for the time the user set (``_idle_after``).

        Only while that is switched on (``TASK_AGENT_IDLE_COMPACT_ENABLED``).
        Whether it is worth it for this session is looked at when the
        time has come. A commission that takes the session before then
        drops the timer. ``idle_for`` is how long the session has to have
        been idle, read now when it is not given: the timer keeps it, and
        looks at the session by it when it comes due, whatever the user sets
        meanwhile (the next session that is let go gets the new time).
        ``model`` is the key of the model the session is sent to as the wait
        is set (``session_model``; read now when ``idle_for`` is): the timer
        keeps it, and nothing is summed up when the session is sent to
        another by the time it comes due (the
        user moved to another provider: what was said in the session is not
        sent there without the user going on with it). In memory only.
        ``after`` is how long from now, when that is not the whole time (the
        time had come by the timer, and not yet by what the ledger has of the
        session: ``_idle_compact``).
        """
        from config.nekopower_settings import TASK_AGENT_IDLE_COMPACT_ENABLED

        self._drop_idle_timer(session_id)
        if not TASK_AGENT_IDLE_COMPACT_ENABLED or self._closed:
            return
        if session_id in self._idle_stopped:
            # What was at work in it was stopped with everything else, and
            # nothing new has been asked for in it since: the host makes
            # no request for it by itself (``drop_idle_work``).
            return
        gen = self._idle_schedule_gen.get(session_id, 0) + 1
        self._idle_schedule_gen[session_id] = gen
        stops = self._idle_stops
        if idle_for is None:
            if not await self._idle_compact_wanted(session_id):
                # Switched off for this model by the user: no timer is set.
                return
            idle_for = await self._idle_after(session_id)
            model = await self._task_model_key(session_id)
            if model is None:
                # No model is configured, or it cannot be read: which provider the
                # wait is set under cannot be told, and a timer that checked nothing
                # would send the session to whichever one the user sets next.
                return
            # Looked at again after the wait: the session may have been taken
            # by a commission meanwhile (that drops its timer, and sets
            # another when it is over), everything stopped, or the host
            # shut down. If a transcript-preserving cleanup hold (outputs or
            # work_dir) claimed the session in the meantime, wait for it to
            # finish and re-verify before setting the timer.
            while True:
                if (
                    self._closed
                    or stops != self._idle_stops
                    or session_id in self._idle_stopped
                    or self._idle_schedule_gen.get(session_id) != gen
                ):
                    return
                if self._idle_now(session_id):
                    break
                clearing = self._clearing.get(session_id)
                if (
                    clearing is None
                    or session_id in self._clearing_drops_transcript
                    or self._in_use_excluding_clearing(session_id)
                ):
                    return
                await clearing.wait()
            self._drop_idle_timer(session_id)
        if after is None:
            after = idle_for

        def due() -> None:
            self._idle_timers.pop(session_id, None)
            if not self._closed:
                self._spawn(self._idle_compact(session_id, stops, idle_for, model))

        self._idle_timers[session_id] = asyncio.get_running_loop().call_later(max(0.0, float(after)), due)

    def drop_idle_work(self) -> None:
        """Give up everything the host was going to do by itself in idle sessions, and what it is doing there now.

        For when the user stops everything: no model request is made
        after that unless something is dispatched anew. A session comes to
        be summed up again once another commission has ended in it.

        That holds for the sessions in use right now as well: the
        commissions in them are stopped next, and let their sessions go
        after this. Letting go does not set a timer for a session that
        was in use when everything was stopped, until a commission is
        handed over or taken up again in it (``_new_work_in``).
        """
        self._idle_stops += 1
        in_hand = (*self._sessions, *self._queue, *self._taking_up, *self._requeueing)
        self._idle_stopped.update(self._held_by)
        self._idle_stopped.update(
            self._session_of[commission_id] for commission_id in in_hand if commission_id in self._session_of
        )
        for session_id in list(self._idle_timers):
            self._drop_idle_timer(session_id)
        for task in list(self._compacting.values()):
            self._cut_short(task)

    @staticmethod
    def _cut_short(task: "asyncio.Task[Any]") -> None:
        """Cancel a summing up once.

        Cancelled, it closes the session in the runtime and waits for
        that before it gives the session up. A second cancel would reach
        it while it waits, and have it leave: the runtime would still
        hold the session, the gateway its token, and the session would
        stay marked as being summed up.
        """
        if not task.done() and not task.cancelling():
            task.cancel()

    def _new_work_in(self, session_id: str | None, stops: int) -> None:
        """Note that something new is to be worked on in a session: it may be summed up again once that has ended.

        ``stops`` is how often everything had been stopped when whoever
        brings the work began to. If everything was stopped since, the
        work is stopped with it, and the session stays one that was in
        use when everything was stopped.
        """
        if not session_id:
            return
        if stops == self._idle_stops:
            self._idle_stopped.discard(session_id)
        else:
            self._idle_stopped.add(session_id)

    async def _stop_compacting(self, session_id: str) -> bool:
        """Cut short the summing up of an idle session, and wait until the runtime has let go of the session.

        Returns whether the session is free of it. The runtime gets
        ``_SERVICE_HANDOVER_SECONDS`` to say that it closed the session.
        When it does not, the session stays the host's (what the runtime
        says of that opening later is still taken there, and the
        session is given back then), and whoever asked goes without it.
        """
        task = self._compacting.get(session_id)
        if task is None or task is asyncio.current_task():
            return True
        self._cut_short(task)
        loop = asyncio.get_running_loop()
        until = loop.time() + _task_agents_facade._SERVICE_HANDOVER_SECONDS
        await asyncio.wait([task], timeout=_task_agents_facade._SERVICE_HANDOVER_SECONDS)
        over = self._idle_over.get(session_id)
        if over is not None:
            # The task left while the session was being given back.
            await asyncio.wait([over], timeout=max(0.0, until - loop.time()))
        return self._compacting.get(session_id) is not task

    def _idle_compact_allowed(self, kept: Session | None, now: float, after: float) -> bool:
        """Whether a session, as the ledger has it, is to be summed up while idle: by its kind, its length and its failures.

        ``after`` is how long it has to have been idle (``_idle_after``).
        That the session has not ended (``Ledger.session_alive``) is asked
        by whoever calls this: that is read from the ledger.
        """
        from config.nekopower_settings import TASK_AGENT_IDLE_COMPACT_MAX_FAILURES, TASK_AGENT_IDLE_COMPACT_MIN_TOKENS

        if kept is None or kept.status != "closed" or kept.kind not in _IDLE_COMPACT_KINDS:
            return False
        if kept.runtime != RUNTIME_BUILTIN or kept.context_tokens < TASK_AGENT_IDLE_COMPACT_MIN_TOKENS:
            # dsh sums up by itself; a short conversation costs more to sum up than it saves.
            return False
        if now - kept.last_active_at < after:
            return False
        # Summings up that got no whole summary, one after the other: no more
        # by itself once it was tried that often, until a summary is made
        # in the course of a commission.
        return self._idle_compact_failures.get(kept.id, 0) < TASK_AGENT_IDLE_COMPACT_MAX_FAILURES

    def _is_switched_on(self) -> bool:
        """Whether NekoPower is switched on, as far as this was told how to find out; a switch that cannot be read is off."""
        if self._switched_on is None:
            return True
        try:
            return bool(self._switched_on())
        except Exception:  # noqa: BLE001 - an unreadable switch counts as off
            return False

    def _idle_now(self, session_id: str) -> bool:
        """Whether nothing is at work in a session, or on its way to be, and nothing else has claimed it."""
        return not self._closed and not self._in_use(session_id)

    async def _idle_compact(
        self, session_id: str, stops: int | None = None, after: float | None = None, model: str | None = None
    ) -> bool:
        """Sum up the conversation of a session nothing has been at work in for long. Returns whether it was summed up.

        ``after`` is how long the session has to have been idle: the time
        its timer was set for (``_schedule_idle_compact``), so that what the
        user sets while a session waits does not change that wait; read now
        when it is not given. ``model`` is the key of the model the session
        was sent to when the wait was set: with it sent to another now, nothing is
        asked of it (no failure is counted and no wait is set again), so that
        what was said in the session does not go to a provider the user
        moved to meanwhile without the user going on with it.

        One model request, of the length of the conversation at the most,
        made by the host itself: for no commission, so it is counted with
        the session alone, against the daily quota of the agent model as
        one request, and against no commission's limit. Not made when
        NekoPower is switched off, when the quota is used up, or when the
        session, by the time this gets to it, is in use, has ended
        (``Ledger.session_alive``) or is not worth it
        (``_idle_compact_allowed``).

        The session is opened in the built-in runtime as a commission's
        would be (the system prompt and the tools it was opened with
        before, so what a provider kept of its requests is used), told to
        sum itself up, and closed. A commission that is to be worked on in
        it meanwhile cuts this short (``_start``) and waits only for the
        runtime to let go: nothing of the conversation changes unless the
        summary was written.

        There is no limit on how often or how many: what bounds it is that
        a timer is only set once a commission has ended in the session,
        that a session summed up holds nothing worth summing again until a
        request says how long it has grown (``Ledger.end_compacting``), and
        that one that got no whole summary (the request failed, came back
        empty or cut off by its length, or the model went for a tool) is
        not tried again by itself: the next time is when another
        commission has ended in it, and after ``TASK_AGENT_IDLE_COMPACT_MAX_FAILURES``
        such in a row not until it made a summary in the course of one.
        """
        from config.nekopower_settings import TASK_AGENT_IDLE_COMPACT_ENABLED, TASK_AGENT_IDLE_COMPACT_SECONDS
        from nekopower.runtime.protocol import GatewaySpec

        while True:
            if self._closed or not TASK_AGENT_IDLE_COMPACT_ENABLED:
                return False
            if stops is not None and stops != self._idle_stops:
                # Everything was stopped between the timer and this.
                return False
            clearing = self._clearing.get(session_id)
            if clearing is not None:
                if session_id in self._clearing_drops_transcript:
                    return False
                # Wait for a concurrent non-transcript file hold (e.g. outputs or work_dir cleanup)
                # before deciding whether the session can still be idle-compacted.
                await clearing.wait()
                continue
            if not self._idle_now(session_id) or not self._is_switched_on():
                return False
            if not await self._idle_compact_wanted(session_id):
                # Switched off since the timer was set: it comes due and does nothing.
                return False
            if self._closed or (stops is not None and stops != self._idle_stops):
                return False
            if session_id in self._clearing:
                # A file hold started while settings were queried; wait for it and re-verify.
                continue
            if not self._idle_now(session_id) or not self._is_switched_on():
                # Taken by a commission, or switched off, while that was looked up.
                return False
            break
        task = asyncio.current_task()
        assert task is not None
        # Claimed before anything is awaited: a commission that takes the
        # session from here on finds this, and cuts it short.
        self._compacting[session_id] = task
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        runtime: Any = None
        # What the gateway knows this opening of the session by: no
        # commission's name, so no commission's token is touched.
        grant = f"session:{session_id}"
        marked = opened = compacted = asked = cut_short = False
        # Set when the timer brought this here a moment early: how long to wait still.
        too_early: float | None = None
        try:
            if after is None:
                after = await self._idle_after(session_id)
            kept = await self._ledger.get_session(session_id)
            choice = await self.session_model(kept, record=False) if kept is not None else None
            config = choice.config if choice is not None else None
            if model is not None and self._model_key_of(config) != model:
                # The session's own model: where it is sent now is another
                # than where it was when the wait was set (the user moved
                # it, or what its slot follows, to another provider).
                logger.info("the model of session %s changed while it waited; it is not summed up", session_id)
                return False
            if (
                self._model_capabilities is not None
                and config
                and config.get("model")
                and not await self._model_capabilities.idle_compact_on(config.get("base_url"), config.get("model"))
            ):
                # Asked of the model it would go to, the one compared above.
                return False
            now = self._ledger._clock()
            kept = await self._ledger.get_session(session_id)
            # A session that has ended is not summed up, whatever else is so.
            alive = await self._ledger.session_alive(kept, now)
            if not alive or not self._idle_compact_allowed(kept, now, after):
                if alive and stops is not None and kept is not None:
                    # A timer set when the session was let go runs by the
                    # loop's clock; how long the session has been idle is
                    # told by what the ledger wrote down, by the clock on
                    # the wall, and that may have been written a moment
                    # after the timer was set. When nothing but that
                    # stands in the way, it is looked at again once the
                    # time is up by the ledger, and not given up.
                    # Asked as of when it is looked at again, the margin
                    # included: as of ``up`` itself, ``up - last_active_at``
                    # comes out a hair short of the idle time in floats
                    # (timestamps of the wall clock are large), and the
                    # session would never be found idle for long enough.
                    # For the same reason a clock that reads ``up`` exactly
                    # is still early here.
                    up = kept.last_active_at + after
                    if up >= now and self._idle_compact_allowed(kept, up + _IDLE_TIMER_MARGIN_SECONDS, after):
                        too_early = up - now
                return False
            last = await self._ledger.select(session_id=session_id, limit=1)
            if not last or not config or not config.get("model"):
                return False
            commission = last[0]
            place = dataclasses.replace(
                kept, workdir=kept.workdir or commission.workdir, project=kept.project or commission.project
            )

            def prepare() -> tuple[str, str, bool]:
                session_dir = self._session_dir_of(place)
                return str(session_dir), str(self._workdir_of(place, session_dir)), _in_builtin(session_dir)

            session_path, workdir, has_record = await asyncio.to_thread(prepare)
            if not has_record:
                return False
            # Before the ledger is asked: cancelled while it writes, the
            # status may be written all the same, and has to be taken
            # back (which changes nothing if it was not).
            marked = True
            if not await self._ledger.begin_compacting(session_id):
                return False
            model = await self._model_of(config)
            # What the session may do, as when it is started for a commission: its token for
            # the gateway calls nothing where its preset is without the tool that calls, and
            # is not left to the list of its tools to see to.
            template = await self._template_of(kept)
            gateway_spec = None
            if self._gateway is not None:
                # Make gateway tools available for the summary request. The next
                # real commission supplies its own workspace rules when it opens.
                url = await self._gateway.start()
                gateway_spec = GatewaySpec(
                    url=url,
                    token=self._gateway.issue(
                        grant, candidate_plugin_ids=(), supports_images=model.supports_images,
                        reads_only=kept.kind == "fork" or template.calls_nothing,
                    ),
                )
            spec = await self._spec(
                commission, kept, model, workdir=workdir, session_dir=session_path, gateway=gateway_spec,
                idle_compaction=True, template=template,
            )
            runtime = self._runtimes.builtin()
            if self._pump is None:
                self._pump = asyncio.ensure_future(self._pump_events())
            self._service[session_id] = events
            opened = True
            await runtime.start(spec)

            async def summed_up() -> None:
                nonlocal asked, compacted
                while True:
                    event = await events.get()
                    kind = event.get("type")
                    if kind == "started" and not asked:
                        if not self._is_switched_on():
                            # Switched off while the session was being
                            # opened: the host asks nothing of a model
                            # by itself, and nothing is charged.
                            return
                        # Charged here, when the request is about to be
                        # made, and not before the session was marked
                        # and opened: nothing is charged for a summing
                        # up that never got as far as asking.
                        if not await self._charge(choice is not None and choice.free, commission.id):
                            # The daily quota is used up: the host makes no request of its own.
                            return
                        if not self._is_switched_on():
                            # Switched off while the quota was looked at,
                            # which waits for something: nothing is asked
                            # of a model, though the request was paid for.
                            return
                        asked = True
                        await runtime.compact(session_id)
                    elif kind == "usage":
                        await self._note_usage(session_id, None, event)
                    elif kind == "compacted":
                        # Written, whatever becomes of this from here on.
                        compacted = True
                    elif kind in ("ended", "error"):
                        # The summing up is over, or was refused; or the
                        # session could not be opened at all.
                        return

            await asyncio.wait_for(summed_up(), TASK_AGENT_IDLE_COMPACT_SECONDS)
            return compacted
        except asyncio.TimeoutError:
            logger.warning("summing up idle session %s took too long; given up", session_id)
            return False
        except asyncio.CancelledError:
            # Cut short by a commission, a stop of everything, or the host
            # shutting down: that no summary was made is no failure of it.
            cut_short = True
            raise
        except Exception:  # noqa: BLE001 - the session is as it was; the next commission goes on from that
            logger.warning("summing up idle session %s failed", session_id, exc_info=True)
            return False
        finally:
            # Whatever became of it (also when a commission cut it short):
            # the runtime lets go of the session before anyone else has
            # it. In a task of its own, waited for here: cancelled while
            # it waits (this is where a stop or a commission finds a
            # summing up that is all but over), this leaves, and the
            # giving back is carried through all the same.
            # (Not a task shutting down gives up on, ``_spawn``: cut off
            # there, the gateway would keep its token and the ledger
            # would not be told that the session was summed up.)
            over = asyncio.ensure_future(
                self._idle_compact_over(
                    session_id, task, runtime=runtime, events=events, opened=opened, grant=grant,
                    marked=marked, compacted=compacted, too_early=too_early, stops=stops,
                    asked=asked, cut_short=cut_short, idle_for=after, model=model,
                )
            )
            self._idle_over[session_id] = over
            await asyncio.shield(over)

    def _note_idle_compact_result(self, session_id: str, *, compacted: bool, asked: bool, cut_short: bool) -> None:
        """Count a summing up of an idle session that was asked for and got no whole summary; a summary made ends the count.

        What counts as failed is that the host told the session to sum
        itself up, and no summary came of it: the request failed, came back
        empty or cut off by its length, the model went for a tool instead,
        or it took too long. Not that it was not asked (the quota was used
        up, NekoPower was switched off, a commission took the session), nor
        that it was cut short. A session is not tried again by itself after
        a failure, until another commission has ended in it
        (``_schedule_idle_compact`` is only called then), and, after
        ``TASK_AGENT_IDLE_COMPACT_MAX_FAILURES`` in a row, not until a
        summary is made in the course of one (``_handle``).
        """
        if compacted:
            self._idle_compact_failures.pop(session_id, None)
        elif asked and not cut_short:
            self._idle_compact_failures[session_id] = self._idle_compact_failures.get(session_id, 0) + 1

    async def _length_is_stale(self, session_id: str) -> None:
        """Have whatever was said of a session's length so far count as older than what is said from now on.

        For a session that was summed up (``Ledger.end_compacting`` writes
        its length as unknown): what a request made before the summary said
        of it, and that is owed because the ledger could not take it then,
        would be written afterwards as the newest and put the old length
        back. The usage owed stays owed; only its length no longer counts.
        """
        async with self._usage_locks.setdefault(session_id, asyncio.Lock()):
            self._context_written[session_id] = max(self._context_said, self._context_written.get(session_id, 0))

    async def _idle_compact_over(
        self,
        session_id: str,
        task: "asyncio.Task[Any]",
        *,
        runtime: Any,
        events: "asyncio.Queue[dict[str, Any]]",
        opened: bool,
        grant: str,
        marked: bool,
        compacted: bool,
        too_early: float | None,
        stops: int | None,
        asked: bool = False,
        cut_short: bool = False,
        idle_for: float | None = None,
        model: str | None = None,
    ) -> None:
        """Give a session back after the host had it to sum it up (``_idle_compact``).

        The runtime closes it, its token for the gateway is taken back,
        the ledger has it closed again, and it is no longer claimed.
        All of it, whatever becomes of the task that summed it up:
        ``_stop_compacting`` waits for this.
        """
        try:
            if await self._end_service(session_id, runtime, events, opened):
                # Said while the session was being closed.
                compacted = True
            self._note_idle_compact_result(session_id, compacted=compacted, asked=asked, cut_short=cut_short)
            if self._gateway is not None:
                self._gateway.revoke(grant)
            if marked and compacted:
                await self._length_is_stale(session_id)
            if marked:
                try:
                    await self._ledger.end_compacting(session_id, compacted=compacted)
                except Exception:  # noqa: BLE001 - the status is for showing
                    logger.warning("noting that a session is no longer being summed up failed", exc_info=True)
        finally:
            if self._compacting.get(session_id) is task:
                del self._compacting[session_id]
            if self._idle_over.get(session_id) is asyncio.current_task():
                del self._idle_over[session_id]
            # What the request that summed it up used, should the ledger not have taken it.
            self._settle_usage_of(session_id)
            if too_early is not None and stops == self._idle_stops and self._idle_now(session_id):
                # A little more than what is left, so that it is not a
                # moment early once more.
                await self._schedule_idle_compact(
                    session_id, after=too_early + _IDLE_TIMER_MARGIN_SECONDS, idle_for=idle_for, model=model
                )

    async def _end_service(
        self, session_id: str, runtime: Any, events: "asyncio.Queue[dict[str, Any]]", opened: bool
    ) -> bool:
        """Close a session the host opened by itself and wait until the runtime says it is gone.

        Returns whether the runtime said meanwhile that the conversation was summed up.
        """
        from nekopower.runtime.protocol import PROCESS_EXITED_ERROR

        summed_up = False
        try:
            if not opened or runtime is None:
                return False
            await runtime.close(session_id)

            async def gone() -> None:
                nonlocal summed_up
                while True:
                    event = await events.get()
                    kind = event.get("type")
                    if kind == "usage":
                        await self._note_usage(session_id, None, event)
                    elif kind == "compacted":
                        summed_up = True
                    elif kind == "ended" and event.get("reason") == "closed":
                        return
                    elif kind == "error" and event.get("command") in ("close", "start"):
                        # It was never there, or is gone already.
                        return
                    elif kind == "ended" and event.get("error") == PROCESS_EXITED_ERROR:
                        # The runtime process went away, and the session with it.
                        return

            # However long it takes, while the host is up. Given back
            # before the runtime said so, the session could be taken by
            # a commission, and what the runtime says of this opening of
            # it afterwards (that it is closed) would be taken for that
            # commission's. (A commission that is to be worked on in it
            # does not wait as long: ``_stop_compacting``.) A host that
            # shuts down stops its runtimes next and gives the session
            # to nobody: it waits as long as it waits for anything then,
            # and what is left to do for the session is done all the same.
            waiting = asyncio.ensure_future(gone())
            try:
                loop = asyncio.get_running_loop()
                asked = loop.time()
                noted = False
                done: set[Any] = set()
                while not done:
                    waited = loop.time() - asked
                    if self._closed and waited >= _task_agents_facade._SHUTDOWN_START_WAIT_SECONDS:
                        break
                    if not noted and waited >= _task_agents_facade._SERVICE_CLOSE_SECONDS:
                        noted = True
                        logger.warning(
                            "the runtime has not said that session %s is closed; still waited for", session_id
                        )
                    done, _pending = await asyncio.wait({waiting}, timeout=_task_agents_facade._SERVICE_CLOSE_POLL_SECONDS)
                if done:
                    # What the waiting failed with, if it did, is raised here.
                    waiting.result()
            finally:
                if not waiting.done():
                    waiting.cancel()
        except Exception:  # noqa: BLE001 - nothing more can be done for it
            logger.warning("closing a session the host had open failed", exc_info=True)
        finally:
            if self._service.get(session_id) is events:
                del self._service[session_id]
        return summed_up

    # -- pre-compaction before switching to a smaller-window model ----------

    async def _resolve_model_by_ident(self, target_ident: str) -> Any | None:
        """Resolve a live ``role_models.Choice`` matching ``target_ident`` across configured endpoints."""
        from nekopower.host.role_models import SlotUnavailable

        if not target_ident:
            return None
        candidates: list[Any] = []
        for loader in (self.role_models.agent, self.role_models.advanced):
            try:
                found = await loader()
            except (SlotUnavailable, Exception):  # noqa: BLE001 - best-effort lookup
                continue
            if found is not None and found.ident == target_ident and found.config and found.config.get("model"):
                return found
        slots = getattr(self.role_models, "slots", None)
        if slots is not None:
            try:
                listed = await slots.list()
            except (SlotUnavailable, Exception):  # noqa: BLE001 - best-effort lookup
                listed = []
            for item in listed:
                slot_id = str(getattr(item, "slot_id", "") or "")
                if not slot_id:
                    continue
                try:
                    found = await self.role_models.slot(slot_id)
                except (SlotUnavailable, Exception):  # noqa: BLE001 - skip unavailable slot
                    continue
                if found.ident == target_ident and found.config and found.config.get("model"):
                    candidates.append(found)
                    break
        return candidates[0] if candidates else None

    async def _maybe_precompact_for_model_switch(
        self,
        commission: Any,
        kept: Session,
        new_choice: Any,
        new_model: Any,
        *,
        workdir: str,
        session_path: str,
        template: Any,
    ) -> bool:
        """Pre-compact a resumed session with its previous larger-window model before switching.

        Triggered only when the session switched models, its recorded context
        tokens exceed ``TASK_AGENT_MODEL_SWITCH_PRECOMPACT_RATIO`` of the new
        model's context window, and the previous model is still resolvable with
        a strictly larger context window. Fail-open on any error so switching
        never blocks opening the session on the new model.
        """
        from config.nekopower_settings import (
            TASK_AGENT_IDLE_COMPACT_SECONDS,
            TASK_AGENT_MODEL_SWITCH_PRECOMPACT_RATIO,
        )
        from nekopower.runtime.protocol import GatewaySpec

        prev_ident = kept.model_was or (
            kept.model_ident if kept.model_ident and kept.model_ident != new_choice.ident else ""
        )
        if not prev_ident or prev_ident == new_choice.ident:
            return False
        new_window = int(getattr(new_model, "context_window", 0) or 0)
        if new_window <= 0 or kept.context_tokens <= int(new_window * TASK_AGENT_MODEL_SWITCH_PRECOMPACT_RATIO):
            return False
        if self._closed or not self._is_switched_on():
            return False
        prev_choice = await self._resolve_model_by_ident(prev_ident)
        if prev_choice is None or not prev_choice.config or not prev_choice.config.get("model"):
            return False
        prev_model = await self._model_of(prev_choice.config)
        if int(getattr(prev_model, "context_window", 0) or 0) <= new_window:
            return False

        session_id = kept.id
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        grant = f"switch:{session_id}"
        runtime: Any = None
        opened = compacted = asked = False
        try:
            gateway_spec = None
            if self._gateway is not None:
                url = await self._gateway.start()
                gateway_spec = GatewaySpec(
                    url=url,
                    token=self._gateway.issue(
                        grant,
                        candidate_plugin_ids=(),
                        supports_images=prev_model.supports_images,
                        reads_only=kept.kind == "fork" or template.calls_nothing,
                    ),
                )
            spec = await self._spec(
                commission,
                kept,
                prev_model,
                workdir=workdir,
                session_dir=session_path,
                gateway=gateway_spec,
                idle_compaction=True,
                template=template,
            )
            runtime = self._runtimes.builtin()
            if self._pump is None:
                self._pump = asyncio.ensure_future(self._pump_events())
            self._service[session_id] = events
            opened = True
            await runtime.start(spec)

            async def run_compact() -> None:
                nonlocal asked, compacted
                while True:
                    event = await events.get()
                    kind = event.get("type")
                    if kind == "started" and not asked:
                        if not self._is_switched_on():
                            return
                        if not await self._charge(bool(prev_choice.free), commission.id):
                            return
                        asked = True
                        await runtime.compact(session_id)
                    elif kind == "usage":
                        await self._note_usage(session_id, None, event)
                    elif kind == "compacted":
                        compacted = True
                    elif kind in ("ended", "error"):
                        return

            await asyncio.wait_for(run_compact(), TASK_AGENT_IDLE_COMPACT_SECONDS)
            return compacted
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - best-effort pre-compaction must never block opening the new model
            logger.warning(
                "pre-compacting session %s with previous model before model switch failed; continuing",
                session_id,
                exc_info=True,
            )
            return False
        finally:
            if await self._end_service(session_id, runtime, events, opened):
                compacted = True
            if self._gateway is not None:
                self._gateway.revoke(grant)
            if compacted:
                await self._length_is_stale(session_id)
                try:
                    await self._ledger.end_compacting(session_id, compacted=True)
                except Exception:  # noqa: BLE001 - best effort metadata sync
                    logger.debug("updating compacted_at after model-switch pre-compaction failed", exc_info=True)
            self._settle_usage_of(session_id)
