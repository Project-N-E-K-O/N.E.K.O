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

"""Cross-session messaging, resting commissions, scheduled wakes, and cross-session read/ask guards.

Method-only mixin: every instance attribute is initialized in
``TaskAgentManager.__init__`` (``nekopower.host.task_agents.manager``).
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Iterable
from typing import Any

from nekopower.host.ledger import (
    COMMISSION_OVER_REASON,
    ENDED_STATUSES,
    Commission,
    MessageLimits,
    Session,
    SessionRow,
    WakeRequest,
)
from nekopower.host.paths import DSH_SESSION_NAME, TRANSCRIPT_NAME
from nekopower.host.permissions import DENIED_BY_SETTINGS, PermissionDecision
from nekopower.host.runtimes import RUNTIME_DSH
from .types import (
    logger,
    _STOPPED_MEANWHILE_REASON,
    _WOKEN_REASON,
    _ASK_NOT_HANDED_OVER_REASON,
    _ASK_NOT_ASKED_REASON,
    _CLOSING_RETRY_SECONDS,
    _ADDRESSED_KINDS,
    _SESSION_ENDED_REASON,
    _NO_PLACE_REASON,
    _STILL_WORKING,
    Handover,
    _clock_time,
    ForkRefused,
    _SessionAction,
    _Session,
    _number,
    _ends_kept,
    _ended_as,
    _not_refused,
    _Handed,
    _has_second_name,
    _in_builtin,
    _in_dsh,
)

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.task_agents`` via ``monkeypatch.setattr``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import task_agents as _task_agents_facade


class MessagingMixin:
    """Cross-session messaging, resting, and wake-scheduling methods."""

    async def _rests(self, session: _Session, commission: Commission, event: dict[str, Any]) -> bool:
        """Let a commission whose run ended rest, if something it asked to be woken for is still to come.

        Returns whether it is left ``working``. Its session is closed in
        the runtime, which gives its place back and lets go of the session
        (``_settle_hold``); what it waits for takes it up again from what
        was said (``hand_over``). With background jobs of the session
        still running that tell the session when they finish, the session
        stays open instead, idle, and keeps its place: closing it would end
        them, and the one that finishes starts the next run by itself. Never a task agent another one
        started: its result is what the other waits for.
        """
        if commission.parent_id is not None:
            return False
        looked = self._looked
        try:
            # That the ledger cannot be read this moment does not say that
            # nothing waits: taken for that, the commission would end, and
            # a time it set would be dropped with it. Asked again first.
            pending = await looked(lambda: self._ledger.wakes_pending(commission_id=commission.id))
            if not pending:
                return False
            inputs = event.get("inputs")

            def went_on() -> bool:
                # Something was sent to the session since the run ended (the
                # user said more of it, a task agent handed a result back):
                # a run is under way again, and its end is the one to look at.
                return isinstance(inputs, int) and inputs < session.sent

            if went_on():
                # Its end decides. This one's is kept, as where it is seen
                # at the outset (``_act_on_end``): should what was sent be
                # refused, no such run comes, and this end is looked at
                # again (``_on_error``). Not kept, the commission would stay
                # at work with nobody at it, and keep its place.
                session.held_end = event
                return True
            # Jobs whose end starts the next run. One that is being stopped
            # tells nobody when it is gone, and closing the session does it
            # no harm: it does not keep the session open, and its place taken.
            if (_number(event.get("waking_jobs")) or 0) > 0:
                session.idle = True
                return True
            resting = await looked(lambda: self._ledger.rest(commission.id))
            if resting is None:
                # What it waited for came, or was dropped, this moment.
                return False
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it ends as a run without a report does
            logger.warning("looking whether a commission rests failed", exc_info=True)
            return False
        if went_on():
            # Sent while the rest was being written down: the session is not
            # closed under the run that started, and the commission does not
            # rest, being at work (told otherwise, a query would say that it
            # rests, and a host started anew would leave it waiting). That
            # run's end looks again, and finds it resting or not as it
            # stands then.
            try:
                await self._ledger.rest_over(commission.id, resting.since)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the run goes on all the same; its end writes it anew
                logger.warning("taking back that a commission rests failed", exc_info=True)
            # (Kept for where what was sent is refused; see above.)
            session.held_end = event
            return True
        # (That it rests is no change of status, and is told to nobody.)
        session.stands = dataclasses.replace(session.stands or commission, resting_since=resting.since)
        await self._close_session(session)
        return True

    async def _put_back(self, wake_ids: Iterable[int], *, kick: bool = True) -> None:
        """Make wake requests pending again whose text did not get into a session.

        It went into one that closed before taking it, or it was not sent
        after all. Kept here until the ledger has it: where it cannot be
        written this moment, whoever asked goes on all the same, and it
        is tried again after a growing pause. (What is still kept when the
        host ends is on record as on its way, which the next process puts
        right.) ``kick`` False is for the round that is handing over
        itself: it says by what it returns when to look again.
        """
        self._unreturned.update(int(item) for item in wake_ids)
        if await self._return_unreturned(kick=kick):
            return
        if self._returning is None or self._returning.done():
            self._returning = self._spawn(self._return_later())

    async def _return_untaken(self, commission_id: str, by: float) -> None:
        """A commission is over: what was handed to it and never reached a session is not left on record as handed over.

        What was kept to be said to it when it got a session (it waited
        for a place, or was being started) is forgotten with it, and no
        session closes to give it back. The notes among it are pending
        again, for whatever their session is at next; a stop of everything
        that is ending the commission has them dropped instead. What a
        session of it was sent and may yet take stays with that session,
        which gives it back when it closes.

        ``by`` is the ledger's mark of what had been handed over when it
        ended (``Ledger.fired_mark``). Only what it was handed by then
        goes back: this may run a while later (the ledger could not be
        read), and the commission may be under way again by then, with
        notes of its own that are about to be said to it.
        """
        try:
            await self._confirm_taken()
            on_their_way = await self._ledger.wakes_on_their_way(commission_id, by)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - kept, and looked up again
            # No session closes to give them back, and nothing else looks
            # for them in this process: left at that, they would stay on
            # record as on their way until the next one.
            logger.warning(
                "looking for what a commission that is over never took failed; it is looked up again", exc_info=True
            )
            self._ended_unlooked[commission_id] = max(by, self._ended_unlooked.get(commission_id, by))
            if self._returning is None or self._returning.done():
                self._returning = self._spawn(self._return_later())
            return
        session = self._sessions.get(commission_id)
        held = {wake for wakes in session.handed.values() for wake in wakes} if session is not None else set()
        back = [wake for wake in on_their_way if wake not in held and wake not in self._unconfirmed]
        if back:
            # Their text is not kept to be said to this commission should it
            # be taken up again (what a start that failed left behind is):
            # they go to another commission from here.
            going = set(back)

            def stays(text: Any) -> bool:
                return not going.intersection(getattr(text, "wakes", ()))

            for kept in (self._notes, self._carried):
                if commission_id in kept:
                    kept[commission_id] = [text for text in kept[commission_id] if stays(text)]
            if commission_id in self._resume and not stays(self._resume[commission_id]):
                del self._resume[commission_id]
            await self._put_back(back)

    async def _return_unreturned(self, *, kick: bool = True) -> bool:
        """Write what is kept to be put back; returns whether nothing is left to write."""
        ids = sorted(self._unreturned)
        if not ids:
            return True
        try:
            await self._unfire(ids)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - tried again; the session is let go all the same
            logger.warning("putting back what a closed session did not take failed; it is tried again", exc_info=True)
            return False
        self._unreturned.difference_update(ids)
        if kick:
            self._kick_waker()
        return True

    async def _return_later(self) -> None:
        from nekopower.host.outbox import retry_delay

        failures = 1
        # Looked at again after every pause, and after the last write: what
        # was added while one of them was awaited is not left behind.
        while (
            self._unreturned or self._unconfirmed or self._turns_owed or self._ended_unlooked
        ) and not self._closed:
            await asyncio.sleep(retry_delay(failures))
            written = await self._return_unreturned()
            # The turns before what was taken since: a note a turn took is
            # told of with that turn, and with nothing a later one says.
            written = await self._tell_turns_owed() and written
            written = await self._confirm_taken() and written
            written = await self._look_up_ended() and written
            failures = 1 if written else failures + 1

    async def _settle_owed(self) -> None:
        """Write what turns that are over owe, and what their sessions took; neither raises."""
        await self._tell_turns_owed()
        await self._confirm_taken()

    async def _look_up_ended(self) -> bool:
        """Look once more for what commissions that are over never took; returns whether none is left to look up."""
        ended, self._ended_unlooked = self._ended_unlooked, {}
        for commission_id, by in sorted(ended.items()):
            # (Kept again by the look itself where it fails again.)
            await self._return_untaken(commission_id, by)
        return not self._ended_unlooked

    async def _tell_turns_owed(self) -> bool:
        """Write what the senders of notes are owed for turns that ended while the ledger could not be written."""
        owed, self._turns_owed = self._turns_owed, []
        for index, (commission_id, text, state, took, taken) in enumerate(owed):
            try:
                written = await self._ledger.turn_over(
                    commission_id, word=lambda _note, text=text: text, state=state, taken=taken, only=took
                )
                self._unconfirmed.difference_update(taken)
            except asyncio.CancelledError:
                self._turns_owed = [*owed[index:], *self._turns_owed]
                raise
            except Exception:  # noqa: BLE001 - kept, and tried again
                logger.warning("telling the senders of notes that a turn is over failed again", exc_info=True)
                self._turns_owed = [*owed[index:], *self._turns_owed]
                return False
            if written:
                self._kick_waker()
        return True

    async def _confirm_taken(self) -> bool:
        """Write what is kept to be put on record as taken; returns whether nothing is left to write."""
        ids = sorted(self._unconfirmed)
        if not ids:
            return True
        try:
            await self._ledger.wakes_taken(ids)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - kept, and tried again
            logger.warning("putting on record that a session took what it was handed failed; it is tried again", exc_info=True)
            return False
        self._unconfirmed.difference_update(ids)
        return True

    async def _on_taken(self, session: _Session, event: dict[str, Any]) -> None:
        """Note that a text sent to a session is in its conversation: what it brought is handed over for good."""
        number = _number(event.get("inputs"))
        if number is None:
            return
        # Taken, so it is not refused any more.
        _not_refused(session, number)
        if session.started_as is not None and number >= session.started_as_with:
            # The text that tells it of a change of its preset is in its conversation: it knows
            # itself as that preset from here on, whatever becomes of the run that took it.
            await self._note_started_as(session)
        receipt = session.answer_receipts.pop(number, None)
        if receipt is not None:
            self._settle_call_soon(receipt[0], "completed")
        session.handed_told.pop(number, None)
        told = session.copies_told.pop(number, None)
        if told:
            await self._copies_taken(session, told)
        wakes = session.handed.pop(number, None)
        if not wakes:
            return
        # What the end of the run that took it tells the senders of.
        session.took.setdefault(number, []).extend(wakes)
        # Kept until the ledger has it. Where it cannot be written this
        # moment it is tried again, and the end of the turn writes it with
        # what it tells the senders (``_turn_over``): a note the session
        # took is then not passed over as one it did not. (What is still
        # kept when the host ends is handed over once more by the next
        # process, at worst.)
        self._unconfirmed.update(wakes)
        ended = _ended_as(session, event, number) if event.get("late") is True else None
        if ended is not None:
            # The run that took it has ended, and told its senders nothing
            # of it: it was not known to be taken then. They are told now,
            # with what that run came to. Left to the end of the next run,
            # they would be told what that one says, which is about
            # something else.
            # Of this text alone: what the session took of other texts
            # meanwhile is of the run that took it, and is told as that
            # run ends.
            # And before the ledger is asked for anything else (that the
            # text was taken is written with what is told): stopped there
            # as the host shuts down, it is kept as owed with that run's
            # words, and not left with the session for whatever its last
            # end is told with.
            await self._turn_over(session, ended[0], number, as_told=ended, only=number)
        elif not await self._confirm_taken() and (self._returning is None or self._returning.done()):
            self._returning = self._spawn(self._return_later())
        _ends_kept(session)

    async def _unfire(self, wake_ids: list[int]) -> None:
        """Make wake requests pending again that were not handed on after all; what a stop dropped meanwhile stays dropped.

        A stop of everything drops the notes that wait. One that was on
        record as handed over just then, and was not taken, is not made
        pending behind the stop's back: written before the stop, it is
        dropped as the stop would have dropped it.
        """
        from nekopower.host.wakes import STOPPED_REASON

        stopped_upto = getattr(self._waker, "stopped_upto", None)
        await self._ledger.wakes_unfire(wake_ids, stopped_upto=stopped_upto, reason=STOPPED_REASON)

    @staticmethod
    def _told(said: str, commission: Commission | None) -> tuple[str, str]:
        """Return what the senders of the notes a turn took are told: what it ended on, and how its commission stood.

        What it ended on is its last words, or what was reported where
        it ended with none.
        """
        from config.nekopower_settings import SESSION_NOTICE_TEXT_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        words = said.strip()
        if not words and commission is not None:
            words = (commission.result_summary or "").strip()
        words = truncate_to_tokens(words, SESSION_NOTICE_TEXT_MAX_TOKENS)
        if commission is None:
            return words, "over"
        return words, "resting" if commission.resting else commission.status

    def _owe_turn(self, session: _Session, said: str, upto: int | None) -> None:
        """Keep what the senders of the notes a run took are to be told, where it cannot be told now.

        For a run whose end was cut off as the host shuts down: written
        by ``_settle_at_close``, with how the commission stood as the
        ledger last told of it.
        """
        numbers = sorted(number for number in session.took if upto is None or number <= upto)
        took = [wake for number in numbers for wake in session.took.pop(number)]
        if took:
            self._turns_owed.append(
                (session.commission_id, *self._told(said, session.stands), took, sorted(self._unconfirmed))
            )

    async def _turn_over(
        self,
        session: _Session,
        said: str,
        upto: int | None = None,
        *,
        as_told: tuple[str, str] | None = None,
        only: int | None = None,
    ) -> None:
        """Tell the sessions that wrote to a commission and asked to hear back that its turn is over.

        With what the turn ended on (its last words, or, where it ended
        with a report and no words after it, what was reported) and how
        the commission stood then: done, resting, waiting for its user.
        Written to the ledger and handed over like any note; nothing waits
        for it here, and a failure tells nobody and costs the turn nothing.

        Of the notes this turn took, by the wake requests that brought
        them (``took``), and of no others: where the ledger cannot be
        written now, the turn is told of later, and by then the session
        may have taken the notes of another turn. ``upto`` is the number
        of the last text the session had taken when the run ended: what
        a later text brought is a later run's.
        """
        commission_id = session.commission_id
        # This turn's from here on: what the session takes while the ledger
        # is asked is the next one's. (``only``: of the one text that is
        # said to be taken late, and of none the session took besides.)
        if only is not None:
            numbers = [only] if only in session.took else []
        else:
            numbers = sorted(number for number in session.took if upto is None or number <= upto)
        took = [wake for number in numbers for wake in session.took.pop(number)]
        if not took:
            # It took no note: nobody waits to hear of it.
            return
        def told(commission: Commission | None) -> tuple[str, str]:
            # ``as_told``: what was kept of a run that is over, for a text
            # that is said to be taken after it. The commission is not
            # asked about again: it may stand otherwise by now.
            return as_told if as_told is not None else self._told(said, commission)

        def cut_off(commission: Commission | None) -> None:
            # Cut off before it was written: the host is shutting down, and
            # whoever handles the events was stopped. Kept where shutting
            # down writes it before the ledger is closed
            # (``_settle_at_close``): the session is let go by then, and
            # what was left with it would be told to nobody. (Written
            # after all by the thread that was at it, it is not told
            # twice: a note that was answered is passed over.) Cut off for
            # any other reason, it is the session's again, as if this had
            # not begun.
            if self._closed:
                self._turns_owed.append((commission_id, *told(commission), took, sorted(self._unconfirmed)))
            else:
                session.took[numbers[0]] = [*took, *session.took.get(numbers[0], [])]

        commission = session.stands
        if as_told is None:
            try:
                commission = await self._ledger.get(commission_id)
            except asyncio.CancelledError:
                cut_off(session.stands)
                raise
            except Exception:  # noqa: BLE001 - told with how it was last seen to stand
                # How it stands cannot be read this moment. What is told,
                # now or once the ledger can be written, is what this turn
                # came to as the ledger last told it here (it tells of
                # every change of status), and not what a later turn makes
                # of the commission.
                logger.warning("reading how a commission stands at the end of its turn failed", exc_info=True)
                commission = session.stands
        text, state = told(commission)
        try:
            # With what its session took and the ledger does not have yet.
            taken = sorted(self._unconfirmed)
            written = await self._ledger.turn_over(
                commission_id, word=lambda _note: text, state=state, taken=taken, only=took
            )
            self._unconfirmed.difference_update(taken)
        except asyncio.CancelledError:
            cut_off(commission)
            raise
        except Exception:  # noqa: BLE001 - kept, and told when the ledger can be written again
            logger.warning("telling the senders of notes that a turn is over failed; it is tried again", exc_info=True)
            self._turns_owed.append((commission_id, text, state, took, taken))
            if self._returning is None or self._returning.done():
                self._returning = self._spawn(self._return_later())
            return
        if written:
            self._kick_waker()

    # -- other sessions and wakes -----------------------------------------
    #
    # Sessions write to one another, look into what another left, and ask
    # to be woken later. Every turn is some commission's all the same: a
    # place, the quota, permissions, the gateway token, reports and events
    # are a commission's, and nothing here runs a session outside one.
    #
    # * What a commission asked for itself (a time it set, word of a note
    #   it sent) is that commission's: handed to it alone, and dropped when
    #   it is over. While it waits for that with its run ended it rests
    #   (``_rests``), and is woken through ``_wake_working``.
    # * What is for a session (a note another session wrote) goes to the
    #   commission the session is at: steered into the one at work, waking
    #   the one that rests, kept back while one waits for the user. With no
    #   commission under way, one is filed for it in that session
    #   (``_file_for_notes``), which follows up the session's last.

    async def hand_over(
        self,
        session_id: str,
        owner: str | None,
        wakes: list[WakeRequest],
        *,
        still_wanted: Callable[[], bool] | None = None,
    ) -> Handover:
        """Hand a session what waits for it: the wake requests of one owner, due now. See ``Handover``.

        ``owner`` is the commission that asked for them, or None for what
        is for the session, whatever it is at. Called by ``SessionWakes``,
        one call at a time. ``still_wanted`` says whether a stop of
        everything has come since these were read: a commission is not
        filed after one.
        """
        from config.nekopower_settings import WAKE_HELD_RETRY_SECONDS

        ids = [wake.id for wake in wakes]
        if self._closed or not ids:
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        target = await self._ledger.get_session(session_id)
        if target is None:
            await self._ledger.wakes_void(ids, "its session is not on record")
            return Handover("dropped")
        if not await self._ledger.session_alive(target):
            # Notes written to it, a time it set for itself that came when
            # nothing was at work in it: nothing wakes or opens a session
            # that has ended. (A commission that is under way in it keeps
            # it alive, so what it set for itself does not come here.)
            await self._ledger.wakes_void(ids, _SESSION_ENDED_REASON)
            return Handover("dropped")
        if owner is not None:
            commission = await self._ledger.get(owner)
            if commission is None or commission.status in ENDED_STATUSES:
                # Dropped with the commission as a rule; these were written
                # as it ended.
                await self._ledger.wakes_void(ids, COMMISSION_OVER_REASON)
                return Handover("dropped")
            return await self._hand_to(commission, target, wakes, still_wanted)
        holder = self._held_by.get(session_id)
        commission = await self._ledger.get(holder) if holder is not None else None
        if commission is None or commission.status in ENDED_STATUSES:
            # Nobody holds the session, or one that is over and on its way
            # out: what the session is at is then the commission that rests
            # in it, or the one that waits for a place to start in it.
            waiting = next((item for item in self._queue if self._session_of.get(item) == session_id), None)
            commission = await self._ledger.get(waiting) if waiting is not None else None
            if commission is None or commission.status in ENDED_STATUSES:
                commission = await self._ledger.resting_in(session_id)
            elif self._session_of.get(waiting) != session_id:
                # Gone to another member while it was read (``_beside``): what is for this session
                # is not its to take along. Looked for again in a moment.
                return Handover("held", _CLOSING_RETRY_SECONDS)
        if commission is not None:
            return await self._hand_to(commission, target, wakes, still_wanted)
        return await self._file_for_notes(target, wakes, still_wanted)

    async def _hand_to(
        self,
        commission: Commission,
        target: Session,
        wakes: list[WakeRequest],
        still_wanted: Callable[[], bool] | None = None,
    ) -> Handover:
        """Hand what waits for a session to the commission that is under way in it.

        In this order, so that nothing is handed on that is no longer to
        be, and nothing is lost that was. The requests are put on record
        as carried out first: only those still pending are (one that was
        dropped by a stop of everything, or with its commission, or in
        favour of word that came after all, is not among them), and one
        that is on record is not handed on a second time by a round that
        tries again. The text is made from those. Then, with nothing
        awaited in between, it is looked at once more whether the session
        can take it, and it is handed on. Where it cannot after all, the
        requests are pending again.
        """
        from config.nekopower_settings import WAKE_HELD_RETRY_SECONDS

        ids = [wake.id for wake in wakes]
        commission_id = commission.id
        if commission.status == "waiting_user":
            # Not in between its question and the answer, and not so that
            # it gets to work with its question open. It goes once the
            # commission no longer waits (``_on_commission_change``).
            return Handover("held", WAKE_HELD_RETRY_SECONDS)

        def stopped() -> bool:
            # Everything was stopped since these were read: the stop is
            # ending this commission, and nothing more is said to it.
            return still_wanted is not None and not still_wanted()

        def in_the_way() -> bool:
            # Its runtime session is on its way out, the end of its run is
            # being looked at (it may be closed to rest this moment), or it
            # has come to wait for the user. What is sent now would be
            # lost, or would set it to work with its question open. So it
            # is while a landing writes how the commission ends, whether
            # or not a session of it is open in a runtime just then.
            live = self._sessions.get(commission_id)
            return commission_id in self._landing_ends or live is not None and (
                live.closing
                or live.settling
                or live.awaiting_prompt
                or (live.question is not None and not live.question.done())
                # It refused what it was last handed, a moment ago.
                or live.refused_until > asyncio.get_running_loop().time()
                # It hands over what it has, the user having allowed no
                # more to be used: a note would end with that, and its
                # sender be told what it stopped with. Once it is over, a
                # commission of its own is filed for the note.
                or live.wind_up_left is not None
                # Its commission is over on record, and its session is
                # about to be closed (a moment before that is begun).
                or (live.stands is not None and live.stands.status in ENDED_STATUSES)
            )

        def way() -> str:
            # How it reaches the commission as things stand: steered into
            # its open session, along with the work it is about to be
            # given, or by taking the commission up again.
            live = self._sessions.get(commission_id)
            if live is not None and live.ready and self._runtime_of(live) is not None:
                return "steer"
            return "along" if self._in_hand(commission_id) else "wake"

        if stopped() or in_the_way():
            return Handover("held", _CLOSING_RETRY_SECONDS)
        chosen = way()
        # The session it waits for: another member may be made for it while this is awaited
        # (``_beside``), and what was for this session is then not its to take along.
        waits_in = self._session_of.get(commission_id)
        session = self._sessions.get(commission_id)
        # Whether the switch and the quota were asked about a turn this starts.
        counted = False
        if chosen == "wake" and commission.status != "working":
            # Queued in the ledger and in nobody's hands here: not this
            # process's to start.
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        if chosen == "wake" or (session is not None and session.idle):
            # It starts a turn: in a session that is closed (the commission
            # rests, or waits for task agents it started), or in one that
            # is open with its run over (it waits for background jobs, or
            # for task agents). No turn is started with NekoPower switched
            # off or the quota used up; its first request is counted here,
            # ahead of it.
            held = await self._may_start(
                commission_id,
                session=await self._ledger.get_session(commission.session_id),
            )
            if held is not None:
                return held
            counted = True
        fired = await self._ledger.wakes_fire(ids, into=commission_id)
        if not fired:
            # Dropped while this looked: there is nothing to say.
            return Handover("dropped")
        try:
            text = await self._wake_text(
                target, [wake for wake in wakes if wake.id in fired], answerable=commission.parent_id is None
            )
            if not text:
                # What they were to hand over is gone from the record:
                # dropped with the reason, and not left as handed over, which
                # the next process would take for something on its way.
                await self._ledger.wakes_drop(fired, "what it was to hand over is gone from the record")
                return Handover("dropped")
            # Looked at after everything that was awaited; nothing is
            # awaited from here to where it is handed on.
            if (
                stopped()
                or in_the_way()
                or way() != chosen
                or self._sessions.get(commission_id) is not session
                or self._session_of.get(commission_id) != waits_in
            ):
                await self._put_back(fired, kick=False)
                return Handover("held", _CLOSING_RETRY_SECONDS)
            if counted and not self._is_switched_on():
                # Switched off while this was awaited: no turn is started.
                await self._put_back(fired, kick=False)
                return Handover("held", WAKE_HELD_RETRY_SECONDS)
            if chosen == "steer" and session is not None and session.idle and not counted:
                # Its run ended while this was awaited (it waits for task
                # agents it started, say): steered in now, the text would
                # start a turn that the switch and the quota were not
                # asked about. Taken back; the next time it is asked first.
                await self._put_back(fired, kick=False)
                return Handover("held", _CLOSING_RETRY_SECONDS)
            if any(wake.reason == "message" and wake.id in fired for wake in wakes):
                # What another session wrote is about to stand in this
                # commission's conversation. A note carries nobody's
                # permissions, and that is not left to the model alone:
                # what the user allowed "for this session", or once for
                # the run, was allowed for the commission as the user knew
                # it, and is asked again for whatever it does from here on.
                # (A time the commission set for itself brings none of
                # another's words.)
                self._permissions.received_external_input(commission_id)
            if chosen == "steer":
                assert session is not None
                if commission_id in self._prepaid_starts:
                    # Counted above, for the turn this starts.
                    self._prepaid_starts.discard(commission_id)
                    session.prepaid_requests += 1
                # Before its next model request; an idle session is started
                # by it. The requests are on their way until the session has
                # the text in its conversation: should it close first, they
                # are put back.
                await self._send_text(self._runtime_of(session), session, "steer", _Handed(text, fired))
            elif chosen == "along":
                # Being started, or waiting for a place: it goes along with
                # the work it is about to be given.
                self._notes.setdefault(commission_id, []).append(_Handed(text, fired))
            elif not await self._wake_working(commission_id, _Handed(text, fired), reason=_WOKEN_REASON):
                # It rests, or waits for task agents with its session
                # closed, and could not be taken up this moment. (The
                # request that was counted for it stays counted, for when
                # it can be.)
                await self._put_back(fired, kick=False)
                return Handover("held", WAKE_HELD_RETRY_SECONDS)
        except BaseException:
            # Not handed on after all. Kept until the ledger has it: where
            # it cannot be written this moment either, a request that
            # stayed on record as handed over would be read by no round.
            await asyncio.shield(self._put_back(fired, kick=False))
            raise
        return Handover("handed")

    async def _may_start(
        self, key: str, *, mark: str | None = None, session: Session | None = None
    ) -> Handover | None:
        """Say why a turn must not be started for what waits right now, or None when it may.

        NekoPower switched off, or no model to work with: it stays where it
        is and is tried again later. Otherwise the first model request of
        the turn is counted here, ahead of it: with the quota used up
        nothing is woken (the commission would fail on its way up, and
        what it waited for would be lost), and it is tried again when the
        quota has a request left, at the latest after the next midnight.

        ``mark`` is what the counted request is kept under, where that
        is not ``key``: one counted for a session with no commission yet
        (``_session_mark``). ``session`` is the session the turn is
        started in: whether there is a model to work with, and whether its
        requests are counted at all, is told by the model it is sent to
        (``session_model``); only those that go with the free key are.
        One whose run is over and that is still open in a runtime goes on
        with the model and the key it was opened with: by those.
        """
        from config.nekopower_settings import WAKE_HELD_RETRY_SECONDS

        mark = key if mark is None else mark
        if not self._is_switched_on():
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        kept = session
        live = self._sessions.get(key)
        if live is not None and not live.idle:
            live = None
        if live is not None:
            available = not self._switched_off()
        else:
            available, _reason = await self.available(kept)
        if not available:
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        if self._meter is None or mark in self._prepaid_starts:
            return None
        if not (live.free if live is not None else await self._goes_free(kept)):
            return None
        now = self._ledger._clock()
        if self._quota_out and now < self._quota_asked_again_at:
            # Known to be used up, and nothing was counted since.
            return Handover("held", self._quota_asked_again_at - now)
        try:
            allowed = await self._meter(key)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a meter that cannot count does not stop the work
            logger.warning("counting a task agent's model request failed", exc_info=True)
            return None
        # Refused again where it was known to be used up: the next asking
        # is a day on.
        self._note_quota(bool(allowed), asked_again=True)
        if not allowed:
            return Handover("held", self._quota_asked_again_at - now)
        self._prepaid_starts.add(mark)
        return None

    @staticmethod
    def _session_mark(session_id: str) -> str:
        """Return what a request counted for a session, with no commission to have it yet, is kept under.

        Not the session's id alone: a session and its first commission
        have one id, and that commission, taken up again meanwhile,
        would take the session's counted request for its own.
        """
        return f"session:{session_id}"

    async def _file_for_notes(
        self, target: Session, wakes: list[WakeRequest], still_wanted: Callable[[], bool] | None
    ) -> Handover:
        """File a commission in a session for the notes that wait for it, nothing being under way there.

        The commission follows up the last one of the session, is the
        character's the session works for, and is about the notes: it
        takes a place, counts against the quota, asks the user where it
        has to, and reports to that character like any other. Nothing of
        what the user allowed an earlier commission "for this session"
        comes with it: a note carries nobody's permissions.
        """
        from config.nekopower_settings import COMMISSION_TITLE_MAX_TOKENS, DISPATCH_TEXT_MAX_TOKENS, WAKE_HELD_RETRY_SECONDS
        from config.prompts.prompts_nekopower_sessions import SESSION_COMMISSION_TITLE
        from nekopower.host.task_brain import _texts
        from utils.tokenize import truncate_to_tokens

        ids = [wake.id for wake in wakes]
        messages = await self._ledger.messages_of(ids)
        notes = [messages[wake.id] for wake in wakes if wake.id in messages and messages[wake.id].kind == "note"]
        if not notes:
            # Only a note has a commission filed for it.
            await self._ledger.wakes_void(ids, COMMISSION_OVER_REASON)
            return Handover("dropped")
        if target.kind not in _ADDRESSED_KINDS or target.status == "cleared":
            reason = (
                "its session's record was removed" if target.kind in _ADDRESSED_KINDS else "its session is a helper's"
            )
            await self._ledger.wakes_void(ids, reason)
            return Handover("dropped")

        def stopped() -> bool:
            return still_wanted is not None and not still_wanted()

        if stopped():
            # A stop of everything is dropping these this moment.
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        counted_for = self._session_mark(target.id)
        held = await self._may_start(target.id, mark=counted_for, session=target)
        if held is not None:
            return held
        # A request was counted just now, for the session (not where no
        # meter counts, nor where the meter could not be asked). It stays
        # counted for the session until a commission is filed that has
        # it: where nothing is filed after all (the ledger cannot be read
        # just now, the notes were dropped), the next try does not count
        # a second one.
        # The one the session was at last, which is the one whose opening
        # message it took last: not the one filed last, where an older one
        # was taken up again after it.
        opened = await self._ledger.opened_last(target.id)
        last = (await self._ledger.get(opened) if opened else None) or await self._ledger.last_commission(target.id)
        sender = await self._ledger.get_session(notes[0].from_session)
        lang = (last.lang if last is not None else None) or "en"
        title = _texts(SESSION_COMMISSION_TITLE, lang).format(name=sender.name if sender is not None else "?")
        # Looked at once more, and nothing awaited from here to the write
        # that files it. Nothing held the session while this waited, and
        # what was said in an old one may have been removed meanwhile, or
        # is being removed this moment: a commission filed now would be at
        # work on the notes without the conversation they were written to.
        # (One that is filed is seen by a removal that begins after this.)
        current = await self._ledger.get_session(target.id)
        if current is None or current.status == "cleared":
            await self._ledger.wakes_void(ids, "its session's record was removed")
            return Handover("dropped")
        if not await self._ledger.session_alive(current):
            # Over while this waited (``hand_over`` looked before).
            await self._ledger.wakes_void(ids, _SESSION_ENDED_REASON)
            return Handover("dropped")
        if target.id in self._clearing:
            # Dropped the next time, should the removal go through.
            return Handover("held", _CLOSING_RETRY_SECONDS)
        if not self._is_switched_on():
            # Switched off while this waited: nothing is filed now. (The
            # request counted for the session stays counted, for then.)
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        # Filed and given its notes together, or not at all: a commission
        # without its notes would wait for nobody, and the notes would have
        # another one filed for them.
        commission = await self._ledger.create_for_wakes(
            ids,
            wanted=still_wanted,
            lanlan_name=target.lanlan_name,
            title=truncate_to_tokens(title, COMMISSION_TITLE_MAX_TOKENS),
            dispatch_text=truncate_to_tokens(notes[0].body, DISPATCH_TEXT_MAX_TOKENS),
            source="session",
            route="task_agent",
            session_id=target.id,
            continues=last.id if last is not None else None,
            project=target.project,
            workdir=target.workdir,
            conversation_id=last.conversation_id if last is not None else None,
            lang=last.lang if last is not None else None,
            chain_depth=max(note.depth for note in notes),
        )
        if commission is None:
            # The notes were dropped while this looked (everything was
            # stopped): no commission is filed for nothing. Or it is wanted
            # no longer (the host is shutting down), and they stay on
            # record as they are. (The request that was counted stays
            # counted, for what the session is next handed.)
            return Handover("held", _CLOSING_RETRY_SECONDS) if stopped() else Handover("dropped")
        if stopped():
            # Everything was stopped while it was being filed, and the stop
            # looked for commissions before this one was there: it is
            # stopped here, as it would have been there, and its notes are
            # dropped as the stop dropped the others. Or the host is
            # shutting down: nothing is started in it, and the notes wait
            # on record for the next process.
            await self._put_back(ids, kick=False)
            await self._ledger.transition(commission.id, "cancelled", reason=_STOPPED_MEANWHILE_REASON)
            return Handover("dropped")
        if counted_for in self._prepaid_starts:
            # Only one that was counted, and is still the session's, is
            # the commission's first, counted.
            self._prepaid_starts.discard(counted_for)
            self._prepaid_starts.add(commission.id)
        await self.submit(commission)
        return Handover("handed")

    async def _whose(self, other: Session | None, lanlan_name: str) -> str:
        """Say whom a session works for, as a frame says it to a task agent that works for ``lanlan_name``."""
        from config.prompts import prompts_nekopower_sessions as words

        if other is None:
            return words.SESSION_WHOSE.format(lanlan_name="?")
        template = words.SESSION_WHOSE if other.lanlan_name == lanlan_name else words.SESSION_WHOSE_OTHER
        return template.format(lanlan_name=other.lanlan_name)

    async def _framed_note(self, note: Any, lanlan_name: str, *, answerable: bool) -> str:
        """Return a note from another session as its receiver reads it: framed, with a marker made for this once."""
        import secrets

        from config.prompts import prompts_nekopower_sessions as words

        nonce = secrets.token_hex(8)
        sender = await self._ledger.get_session(note.from_session)
        intro = (words.SESSION_NOTE_FRAME if answerable else words.SESSION_NOTE_FRAME_NO_ANSWER).format(
            name=sender.name if sender is not None else note.from_session,
            whose=await self._whose(sender, lanlan_name),
            # As ``list_sessions`` shows it. Should a session be called
            # just that, both are meant, and the task agent is told to
            # name the one by its whole id (``_session_named``).
            address=note.from_session[:8],
            nonce=nonce,
        )
        return words.frame(intro, note.body, nonce)

    async def _wake_text(self, target: Session, wakes: list[WakeRequest], *, answerable: bool) -> str:
        """Return what a session is told for the wake requests handed to it now, each thing in a frame of its own."""
        import secrets

        from config.nekopower_settings import SESSION_NOTICE_WAIT_SECONDS
        from config.prompts import prompts_nekopower_sessions as words

        messages = await self._ledger.messages_of([wake.id for wake in wakes])
        parts: list[str] = []
        for wake in wakes:
            nonce = secrets.token_hex(8)
            message = messages.get(wake.id)
            if wake.reason == "timer":
                intro = words.WAKE_TIMER_NOTICE.format(set_at=_clock_time(wake.created_at), nonce=nonce)
                parts.append(words.frame(intro, wake.note, nonce))
            elif wake.reason == "notice_expiry" and message is not None:
                other = await self._ledger.get_session(message.to_session)
                parts.append(
                    words.SESSION_EXPIRED_NOTICE.format(
                        name=other.name if other is not None else message.to_session,
                        hours=round(SESSION_NOTICE_WAIT_SECONDS / 3600),
                    )
                )
            elif wake.reason == "message" and message is not None and message.kind == "note":
                parts.append(await self._framed_note(message, target.lanlan_name, answerable=answerable))
            elif wake.reason == "message" and message is not None and message.kind == "done":
                other = await self._ledger.get_session(message.from_session)
                # How the commission stood when the turn ended, as it was
                # written down then: by now it may be at something else.
                intro = words.SESSION_DONE_FRAME.format(
                    name=other.name if other is not None else message.from_session,
                    state=wake.note or "over",
                    nonce=nonce,
                )
                parts.append(words.frame(intro, message.body or words.SESSION_DONE_NOTHING_SAID, nonce))
        return "\n\n".join(parts)

    async def _caller(self, session_id: str) -> Commission:
        """Return the commission a callback comes from; raises when there is none."""
        commission = await self._ledger.get(self._at_work_in(session_id))
        if commission is None or not commission.session_id:
            raise RuntimeError("unknown commission")
        return commission

    async def _session_named(self, name: str) -> Session:
        """Return the one session a task agent means by a name or an id; raises with what to do otherwise."""
        from config.prompts.prompts_nekopower_sessions import MESSAGE_REFUSALS

        found = await self._ledger.sessions_named(name)
        if not found:
            raise RuntimeError(MESSAGE_REFUSALS["unknown"].format(name=name))
        if len(found) > 1:
            ids = ", ".join(f"{item.id} ({item.name!r} of {item.lanlan_name})" for item in found[:5])
            raise RuntimeError(MESSAGE_REFUSALS["ambiguous"].format(name=name, ids=ids))
        return found[0]

    async def _state_of(self, session: Session) -> str:
        """Say where a session stands right now, as ``list_sessions`` tells it. No runtime is asked."""
        from config.prompts.prompts_nekopower_sessions import SESSION_STATE_WORDS as words

        holder = self._held_by.get(session.id)
        if holder is not None:
            commission = await self._ledger.get(holder)
            if commission is not None and commission.status == "waiting_user":
                return words["waiting"]
            if commission is not None and commission.status not in ENDED_STATUSES:
                live = self._sessions.get(holder)
                return words["at_work"] if live is None or live.ready else words["starting"]
        resting = await self._ledger.resting_in(session.id)
        if resting is not None:
            pending = await self._ledger.wakes_pending(commission_id=resting.id)
            if pending:
                return words["resting"].format(time=_clock_time(pending[0].due_at))
        if session.status == "cleared":
            return words["cleared"]
        return words["closed"] if await self._ledger.session_alive(session) else words["ended"]

    async def _cb_session_list(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any]:
        from config.nekopower_settings import (
            CARD_DUTY_MAX_TOKENS,
            SESSION_LIST_COMMISSIONS,
            SESSION_LIST_LOOKED_AT,
            SESSION_LIST_ROW_MAX_TOKENS,
            SESSION_LIST_ROWS,
        )
        from config.prompts import prompts_nekopower_sessions as words
        from nekopower.host.selection import similarity
        from nekopower.host.templates import model_name
        from utils.tokenize import truncate_to_tokens

        caller = await self._caller(session_id)
        if action is not None:
            action.require()
            action.record.scope_ref = caller.session_id
        project = payload.get("project")
        # A task agent another one started is that one's business, as it is
        # in every list she is shown: it is reached through its lead. Left
        # out where the sessions are read, so that the many sessions of
        # such task agents take up none of the rows that are looked at.
        rows = await self._ledger.find_sessions(
            project=str(project) if project else None,
            kinds=_ADDRESSED_KINDS,
            limit=SESSION_LIST_LOOKED_AT,
            commissions_each=SESSION_LIST_COMMISSIONS,
        )
        query = " ".join(str(payload.get("query") or "").split())
        if query:
            folded = query.casefold()

            def score(row: SessionRow) -> float:
                texts = [row.session.name, row.session.project or "", *(item.title for item in row.commissions)]
                if any(folded in text.casefold() for text in texts):
                    return 1.0
                return max((similarity(query, text) for text in texts if text), default=0.0)

            scored = [(score(row), index, row) for index, row in enumerate(rows)]
            # The best match first; among equals, the one last in use.
            rows = [row for value, _index, row in sorted(scored, key=lambda item: (-item[0], item[1])) if value > 0]
        if not rows:
            return {"text": words.SESSION_LIST_NONE}
        pages = (len(rows) + SESSION_LIST_ROWS - 1) // SESSION_LIST_ROWS
        try:
            page = min(max(int(payload.get("page") or 1), 1), pages)
        except (TypeError, ValueError):
            page = 1
        header = words.SESSION_LIST_HEADER_FOUND if query else words.SESSION_LIST_HEADER
        lines = [header.format(page=page, pages=pages, total=len(rows))]
        for row in rows[(page - 1) * SESSION_LIST_ROWS : page * SESSION_LIST_ROWS]:
            session = row.session
            line = words.SESSION_LIST_ROW.format(
                name=session.name,
                id=session.id[:8],
                whose=await self._whose(session, caller.lanlan_name),
                project=session.project or words.SESSION_LIST_NO_PROJECT,
                # Which preset it is, and what its card says it takes: what
                # a task agent that looks for whom to ask goes by.
                preset=model_name(session.preset),
                duty=truncate_to_tokens(session.duty, CARD_DUTY_MAX_TOKENS) or words.SESSION_LIST_NO_DUTY,
                state=await self._state_of(session),
                title=row.commissions[0].title if row.commissions else words.SESSION_LIST_NO_COMMISSION,
                active=_clock_time(session.last_active_at),
            )
            line = truncate_to_tokens(line, SESSION_LIST_ROW_MAX_TOKENS)
            lines.append(line + (words.SESSION_LIST_THIS if session.id == caller.session_id else ""))
        return {"text": "\n".join(lines)}

    async def _cb_session_read(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any]:
        import secrets

        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_sessions as words
        from nekopower.host import records
        from utils.tokenize import truncate_to_tokens

        caller = await self._caller(session_id)
        if await self._permissions.forbidden("outside_read"):
            # What another session left lies outside this one's working
            # directory, whichever tool reads it: a user who forbade
            # reading there has forbidden this too.
            raise RuntimeError(DENIED_BY_SETTINGS)
        target = await self._session_named(str(payload.get("session") or ""))
        if action is not None:
            action.require()
            action.record.scope_ref = target.id
        what = str(payload.get("what") or "results")
        nonce = secrets.token_hex(8)

        async def framed(of: str, body: str) -> str:
            intro = words.SESSION_RECORD_FRAME.format(
                name=target.name, whose=await self._whose(target, caller.lanlan_name), what=of, nonce=nonce
            )
            return words.frame(intro, body, nonce)

        if what == "results":
            blocks: list[str] = []
            for item in await self._ledger.select(session_id=target.id, limit=settings.SESSION_RESULTS_COMMISSIONS):
                standing = "resting" if item.resting else item.status
                lines = [f'"{item.title}" ({standing}, last changed {_clock_time(item.updated_at)})']
                outcome = item.result_summary or item.status_reason
                if outcome:
                    lines.append(f"Outcome: {outcome}")
                review = None
                if self._space is not None:
                    try:
                        review = await self._space.read_review(item.id)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - told without its review
                        logger.warning("reading the review of a commission for another session failed", exc_info=True)
                if review is not None and review.content.strip():
                    lines.append(f"Review: {review.content.strip()}")
                blocks.append("\n".join(lines))
            body = truncate_to_tokens("\n\n".join(blocks), settings.SESSION_RESULTS_MAX_TOKENS)
            return {"text": await framed(words.RECORD_WHAT_RESULTS, body or "(no commission was worked on in it)")}

        def place() -> Any:
            # Where what was said is, and whether the built-in runtime keeps it there.
            session_dir = self._session_dir_of(target)
            return session_dir / TRANSCRIPT_NAME, _in_builtin(session_dir), _in_dsh(session_dir)

        path, in_builtin, in_dsh = await asyncio.to_thread(place)
        if not in_builtin and in_dsh:
            return {"text": words.RECORD_IN_DSH}
        try:
            if what == "search":
                query = str(payload.get("query") or "")
                hits, total = await asyncio.to_thread(
                    records.search_record,
                    path,
                    query,
                    hits=settings.SESSION_SEARCH_HITS,
                    excerpt_tokens=settings.SESSION_SEARCH_EXCERPT_MAX_TOKENS,
                    max_bytes=settings.SESSION_RECORD_MAX_BYTES,
                )
                if not hits:
                    return {"text": words.RECORD_SEARCH_NONE}
                body = "\n".join(f"#{hit.position} {hit.label}: {hit.excerpt}" for hit in hits)
                of = words.RECORD_WHAT_SEARCH.format(query=" ".join(query.split()), shown=len(hits), total=total)
                tail = words.RECORD_SEARCH_NEXT + (" " + words.RECORD_SEARCH_MORE if total > len(hits) else "")
                return {"text": await framed(of, body) + "\n" + tail}
            at = payload.get("at")
            stretch = await asyncio.to_thread(
                records.read_record,
                path,
                int(at) if at is not None else None,
                segment_tokens=settings.SESSION_READ_SEGMENT_MAX_TOKENS,
                entry_tokens=settings.SESSION_READ_ENTRY_MAX_TOKENS,
                max_bytes=settings.SESSION_RECORD_MAX_BYTES,
            )
        except records.RecordError as exc:
            raise RuntimeError(str(exc)) from exc
        of = words.RECORD_WHAT_STRETCH.format(first=stretch.first, last=stretch.last, entries=stretch.entries)
        tail = (
            words.RECORD_NEXT.format(following=stretch.following) if stretch.following is not None else words.RECORD_END
        )
        return {"text": await framed(of, stretch.text) + "\n" + tail}

    async def _cb_session_send(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any]:
        import math

        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_sessions as words
        from utils.tokenize import acount_tokens

        refusals = words.MESSAGE_REFUSALS
        caller = await self._caller(session_id)
        if caller.parent_id is not None:
            raise RuntimeError(refusals["from_child"])
        available, _reason = await self.available(await self._ledger.get_session(caller.session_id))
        if not available:
            raise RuntimeError(refusals["switched_off"])
        target = await self._session_named(str(payload.get("to") or ""))
        # What is written is worked on in the other session, by its own model.
        available, _reason = await self.available(target)
        if not available:
            raise RuntimeError(refusals["switched_off"])
        if action is not None:
            action.require()
            action.record.scope_ref = target.id
        if target.id == caller.session_id:
            raise RuntimeError(refusals["self"])
        if target.kind == "fork":
            raise RuntimeError(refusals["fork"].format(name=target.name))
        if target.kind not in _ADDRESSED_KINDS:
            raise RuntimeError(refusals["child"].format(name=target.name))
        if target.status == "cleared":
            raise RuntimeError(refusals["cleared"].format(name=target.name))
        if not await self._ledger.session_alive(target):
            raise RuntimeError(
                refusals["ended"].format(name=target.name, days=settings.SESSION_SINGLE_LIFETIME_DAYS)
            )
        body = str(payload.get("message") or "").strip()
        tokens = await acount_tokens(body)
        if tokens > settings.SESSION_MESSAGE_MAX_TOKENS:
            raise RuntimeError(refusals["too_long"].format(tokens=tokens, limit=settings.SESSION_MESSAGE_MAX_TOKENS))
        depth = int(caller.chain_depth or 0) + 1
        if depth > settings.SESSION_MESSAGE_MAX_DEPTH:
            raise RuntimeError(refusals["too_deep"])
        notify = bool(payload.get("notify_when_done"))
        if getattr(self._waker, "stopping", False):
            # Everything is being stopped, the caller with it. What it writes
            # now would be written after the stop has dropped what waits,
            # and be handed over once the stop is through: work the stop
            # knows nothing of. Looked at with nothing awaited before the
            # write: what was on its way to the ledger before the stop began
            # is dropped by the stop as it ends.
            raise RuntimeError(refusals["over"])
        if action is not None:
            action.require()
        message, refusal, wait = await self._ledger.message_add(
            from_session=str(caller.session_id),
            from_commission=caller.id,
            to_session=target.id,
            body=body,
            depth=depth,
            notify_when_done=notify,
            notice_after=settings.SESSION_NOTICE_WAIT_SECONDS,
            limits=MessageLimits(
                target_interval=settings.SESSION_MESSAGE_TARGET_INTERVAL_SECONDS,
                per_hour=settings.SESSION_MESSAGES_PER_HOUR,
                queue_max=settings.SESSION_MESSAGE_QUEUE_MAX,
                dedup_seconds=settings.SESSION_MESSAGE_DEDUP_SECONDS,
            ),
        )
        if message is None:
            raise RuntimeError(
                refusals[refusal].format(
                    name=target.name,
                    wait=math.ceil(wait),
                    interval=round(settings.SESSION_MESSAGE_TARGET_INTERVAL_SECONDS),
                    limit=settings.SESSION_MESSAGES_PER_HOUR,
                )
            )
        # (Whoever hands notes over is told by the ledger that one was
        # written: ``Ledger.subscribe_notes``.)
        holder = self._held_by.get(target.id)
        at = await self._ledger.get(holder) if holder is not None else None
        waiting = at is not None and at.status == "waiting_user"
        said = (words.MESSAGE_QUEUED_WAITING if waiting else words.MESSAGE_QUEUED).format(name=target.name)
        if notify:
            said += words.MESSAGE_QUEUED_NOTIFY.format(hours=round(settings.SESSION_NOTICE_WAIT_SECONDS / 3600))
        return {"message": said}

    async def _cb_wake_schedule(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any]:
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_sessions as words
        from utils.tokenize import truncate_to_tokens

        caller = await self._caller(session_id)
        if action is not None:
            action.require()
            action.record.scope_ref = caller.session_id
        if caller.parent_id is not None:
            raise RuntimeError(words.WAKE_REFUSALS["from_child"])
        try:
            asked = float(payload.get("delay_seconds") or 0)
        except (TypeError, ValueError):
            asked = 0.0
        delay = min(max(asked, settings.WAKE_MIN_DELAY_SECONDS), settings.WAKE_MAX_DELAY_SECONDS)
        note = truncate_to_tokens(" ".join(str(payload.get("note") or "").split()), settings.WAKE_NOTE_MAX_TOKENS)
        due_at = self._ledger._clock() + delay
        wake, refusal = await self._ledger.wake_add(
            session_id=str(caller.session_id),
            commission_id=caller.id,
            note=note,
            due_at=due_at,
            depth=int(caller.chain_depth or 0),
            pending_max=settings.WAKE_PENDING_MAX,
            daily_max=settings.WAKE_DAILY_MAX,
        )
        if wake is None:
            limit = settings.WAKE_PENDING_MAX if refusal == "too_many_pending" else settings.WAKE_DAILY_MAX
            raise RuntimeError(words.WAKE_REFUSALS[refusal].format(limit=limit))
        self._kick_waker()
        said = words.WAKE_SET.format(at=_clock_time(due_at), minutes=round(delay / 60))
        if asked < delay:
            said += words.WAKE_SET_MOVED.format(seconds=round(settings.WAKE_MIN_DELAY_SECONDS))
        elif asked > delay:
            said += words.WAKE_SET_CAPPED.format(days=round(settings.WAKE_MAX_DELAY_SECONDS / 86400))
        return {"message": said}

    async def _cb_session_ask(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any]:
        """Have a copy of another session answer a question; the answer comes back as a message.

        The other session must have a commission under way (one at work,
        resting, or waiting for its user): one that is done is read with
        ``read_session``. A copy of what was said in it is made
        (``fork_session``), and a commission is filed in the copy under
        the one that asks, the way a task agent another one starts is:
        it starts now or not at all, what it uses counts with the one that
        asks, it is stopped with it, and what it answers comes back to it
        framed (``_tell_parent``). The copy has none of the tools that
        write, run commands or call capabilities, nor those that reach
        other sessions, and makes no more than ``SESSION_ASK_MAX_REQUESTS``
        requests. What a session may ask is counted on its own, apart
        from the notes it writes (``Ledger.ask_add``).
        """
        import math

        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_sessions as words
        from nekopower.host.task_brain import _texts
        from utils.tokenize import acount_tokens

        refusals = words.ASK_REFUSALS
        caller = await self._caller(session_id)
        if caller.parent_id is not None:
            raise RuntimeError(refusals["from_child"])
        if await self._permissions.forbidden("outside_read"):
            # The copy answers from the other session's record, which lies
            # outside this one's working directory: a user who forbade
            # reading there has forbidden this too, as for ``read_session``.
            raise RuntimeError(DENIED_BY_SETTINGS)
        available, _reason = await self.available(await self._ledger.get_session(caller.session_id))
        if not available:
            raise RuntimeError(refusals["switched_off"])
        target = await self._session_named(str(payload.get("session") or ""))
        # A copy of the other session answers, by that session's model.
        available, _reason = await self.available(target)
        if not available:
            raise RuntimeError(refusals["switched_off"])
        if action is not None:
            action.require()
            action.record.scope_ref = target.id
        if target.id == caller.session_id:
            raise RuntimeError(refusals["self"])
        if target.kind not in _ADDRESSED_KINDS:
            raise RuntimeError(refusals["child"].format(name=target.name))
        if target.status == "cleared":
            raise RuntimeError(refusals["cleared"].format(name=target.name))
        if not await self._ledger.session_alive(target):
            raise RuntimeError(
                refusals["ended"].format(name=target.name, days=settings.SESSION_SINGLE_LIFETIME_DAYS)
            )
        if target.runtime == RUNTIME_DSH:
            raise RuntimeError(refusals["dsh"].format(name=target.name))
        if not await self._ledger.select(session_id=target.id, statuses=_STILL_WORKING, limit=1):
            raise RuntimeError(refusals["not_at_work"].format(name=target.name))
        if self._closed or len(self._sessions) >= self.limit:
            # The copy would find no place to answer in: neither copied nor
            # counted. (A place taken while it is copied fails the question
            # there; the copy is removed with the other session's in time.)
            raise RuntimeError(refusals["no_place"])
        question = str(payload.get("question") or "").strip()
        tokens = await acount_tokens(question)
        if tokens > settings.SESSION_MESSAGE_MAX_TOKENS:
            raise RuntimeError(refusals["too_long"].format(tokens=tokens, limit=settings.SESSION_MESSAGE_MAX_TOKENS))
        if getattr(self._waker, "stopping", False):
            # Everything is being stopped, the caller with it (see
            # ``_cb_session_send``): nothing is started for it now.
            raise RuntimeError(refusals["over"])
        filed: list[Commission] = []
        copies: list[Session] = []

        async def hand_over() -> str:
            # Counted, copied, filed, given the copy and handed over in a
            # task of its own, which whoever asked does not cut short (a
            # tool call of dsh that is given up is cancelled, the copy may
            # take a minute): a question counted and then left would count
            # against what may be asked with nothing asked; a commission
            # left queued here would be started by nobody, and the one that
            # asked would wait for its answer for ever; a copy left on its
            # own would wait to be removed with old sessions. Where a step
            # fails, the commission that was filed is ended and the copy
            # removed. One that was not asked does not count against what
            # may be asked. Returns why it was not asked, as whoever asked
            # is told; nothing where it was handed over.
            if action is not None:
                action.require()
            asked, refusal, wait = await self._ledger.ask_add(
                from_session=str(caller.session_id),
                from_commission=caller.id,
                to_session=target.id,
                body=question,
                asks_per_hour=settings.SESSION_ASKS_PER_HOUR,
            )
            if asked is None:
                return refusals[refusal].format(name=target.name, wait=math.ceil(wait), limit=settings.SESSION_ASKS_PER_HOUR)

            async def not_asked() -> None:
                # Counted against nothing: it was not asked.
                try:
                    await self._ledger.message_void(asked.id, _ASK_NOT_ASKED_REASON)
                except Exception:  # noqa: BLE001 - it counts for the hour, which is all that is lost
                    logger.warning("taking a question that was not asked out of the count failed", exc_info=True)

            try:
                try:
                    if action is not None:
                        action.require()
                    copy = await self.fork_session(target.id)
                except ForkRefused as exc:
                    await not_asked()
                    return refusals["no_copy"].format(name=target.name, reason=str(exc))
                copies.append(copy)
                async with self._naming_members:
                    # Read and filed with no other member of a team named
                    # between: one started at once would read the names
                    # before this one is filed, and may take the same.
                    _lead, members = await self._team(caller.id)
                    taken = {member.name for member in members}
                    number = 1
                    while f"ask-{number}" in taken:
                        number += 1
                    child, _created = await self._ledger.create(
                        lanlan_name=caller.lanlan_name,
                        title=_texts(words.SESSION_ASK_TITLE, caller.lang).format(name=target.name),
                        dispatch_text=question,
                        user_text="",
                        source="ask",
                        route="task_agent",
                        parent_id=caller.id,
                        name=f"ask-{number}",
                        project=copy.project,
                        workdir=copy.workdir,
                        # Worked on in the copy: filed with it, so that it
                        # names the session it answers for whatever happens
                        # next.
                        session_id=copy.id,
                        conversation_id=caller.conversation_id,
                        lang=caller.lang,
                    )
                filed.append(child)
                # As for a task agent it starts (``_cb_subagent_start``):
                # one that was stopped meanwhile starts nothing.
                current = await self._ledger.get(caller.id)
                if current is None or current.status not in ("working", "waiting_user"):
                    await self._ledger.transition(child.id, "cancelled", reason="its parent was stopped")
                    await self._discard_copy(copy)
                    await not_asked()
                    return refusals["over"].format(name=target.name)
                await self._ledger.append_event(
                    caller.id, "subagent_started", {"name": child.name, "commission_id": child.id, "asks": target.id}
                )
                if action is not None:
                    action.require()
                if await self.submit(child, wait=False, still_wanted=action.active if action is not None else None):
                    return ""
                if action is not None:
                    action.require()
                # Not started, and failed by ``submit``: no place was free
                # (it was taken while the copy was made), the host is
                # shutting down, or the copy could not be given to it. Whoever
                # asked is told why by this call. Of the last it is told by
                # ``submit`` as well (``_fail``, the way it is told of a
                # question that fails later on), in the same words.
                ended = await self._ledger.get(child.id)
                await self._discard_copy(copy)
                await not_asked()
                why = ended.status_reason if ended is not None else ""
                if not why or why == _NO_PLACE_REASON:
                    return refusals["no_place"].format(name=target.name)
                return refusals["not_started"].format(name=target.name, reason=why)
            except BaseException:
                if filed:
                    try:
                        # Ended the way any of its task agents ends: the one
                        # that asked is told (``_tell_parent``), so that one
                        # that ended its turn to wait for the answer is not
                        # left waiting for it. One that was stopped is not
                        # woken by this.
                        await self._end(
                            filed[0].id,
                            "failed",
                            reason=_ASK_NOT_HANDED_OVER_REASON,
                            text=_ASK_NOT_HANDED_OVER_REASON,
                        )
                    except Exception:  # noqa: BLE001 - as much as can be done; the error is raised below
                        logger.warning("ending a question that could not be handed over failed", exc_info=True)
                if copies:
                    try:
                        await self._discard_copy(copies[0])
                    except Exception:  # noqa: BLE001 - removed with the other session's in time; the error is raised below
                        logger.warning("removing the copy made for a question that was not handed over failed", exc_info=True)
                await not_asked()
                raise

        if action is not None and action.handing_over is not None:
            action.handing_over.set()
            refused = await hand_over()
        else:
            refused = await asyncio.shield(self._spawn(hand_over()))
        if refused:
            raise RuntimeError(refused)
        return {"message": words.ASK_QUEUED.format(name=target.name)}

    async def _discard_copy(self, copy: Session) -> None:
        """Remove a copy that was made for a question that was not handed over, at once.

        Nothing was said in it, and nobody will: it would only wait to be
        removed with the session it was copied from. Its files are claimed
        as any removal claims them (``hold_files``); one that is in use
        after all is left to that. It is put on record as cleared only once
        its files are seen to be gone: one whose files could not be removed
        is left as it is, for the removal of old sessions, which passes over
        none that is not on record as cleared.
        """
        import os
        import shutil

        if not self.hold_files(copy.id):
            if copy.id in self._held_by:
                # A rejected ask can already be closing in the runtime.
                self._unused_ask_copies[copy.id] = copy
            return
        try:
            target = copy.session_dir

            def removed() -> bool:
                if not target:
                    return True
                shutil.rmtree(target, True)
                return not os.path.lexists(str(target))

            if await asyncio.to_thread(removed):
                await self._ledger.mark_session_cleared(copy.id)
            else:
                logger.warning("the copy %s made for a question could not be removed; it is removed with old sessions", copy.id)
        finally:
            self.release_files(copy.id)

    async def _asked_name(self, commission: Commission) -> str:
        """Return the name of the session a copy answers for (``_cb_session_ask``)."""
        copy = await self._ledger.get_session(commission.session_id) if commission.session_id else None
        source = await self._ledger.get_session(copy.forked_from) if copy is not None and copy.forked_from else None
        if source is not None:
            return source.name
        return copy.name if copy is not None else commission.name or commission.id

    async def _rule_on_other_session(
        self, commission_id: str, path: str, *, writes: bool, names_only: bool = False, inside: bool = False
    ) -> dict[str, Any] | None:
        """Rule on a path that lies in a place of another session; None when it does not, or when the usual rules apply.

        What another session left is read without asking: the files in
        its working directory, and what it kept in its own directory (the
        output of its tools). Only places the ledger has on record for a
        session count, so this opens no path the host does not know as a
        session's. Its record itself is not read as a file: it is long,
        and ``read_session`` hands it over a stretch at a time. The files
        a session keeps of itself are told by where they are (in its own
        directory, under their names) and by what a path leads to: a
        link under another name is the file it points at, and a file of
        the same name among the files it works on is a file like any
        other. A second name for a record is refused wherever it lies,
        among the files the caller itself works on too (``inside``: there
        that is all that is looked at, a folder several sessions work in
        being the caller's as much as theirs). Nor is it read
        by way of the directory it is in: a search
        of a directory that holds a session's own directory would open
        the record with the rest, so such a directory is not searched
        (unless ``names_only``: listing names reads no file); the
        directories inside it are. Nor is a directory searched that holds
        a record under a second name, wherever that directory is: a
        search reads through no link, but a second name is a file like
        any other to it. Reading outside working directories
        that the settings forbid stays forbidden. (A shell command that
        reads a record is not looked into, as no shell command is.)

        Nothing is written into another session's own directory, whatever
        the settings allow: a session's record is its own, also where a
        path that leads to it lies among the files it works on. A working
        directory that lies elsewhere (a project folder of the user's) is
        ruled on as any path outside the working directory is.
        """
        import os
        from pathlib import Path

        from config.prompts.prompts_nekopower_sessions import (
            SESSION_RECORD_READ_REFUSED,
            SESSION_RECORD_SEARCH_REFUSED,
            SESSION_RECORD_WRITE_REFUSED,
            SESSION_WRITE_REFUSED,
        )
        from config.nekopower_settings import SESSION_RECORD_ALIAS_SCAN_ENTRIES
        from nekopower.runtime.permissions import is_inside
        from nekopower.runtime.snapshot import SYSTEM_FILE

        own = self._session_of.get(commission_id)
        places = [item for item in await self._ledger.session_places() if item[0] != own]
        if not writes:
            # What another task agent changed in its git worktree is read as
            # what it left in any working directory. (Writes there are ruled
            # on before this: ``_rule_on_tree_write``.)
            places.extend(await self._tree_places(but=own))
        if not places:
            return None
        kept_files = (TRANSCRIPT_NAME, SYSTEM_FILE, DSH_SESSION_NAME)

        def look() -> tuple[bool, bool, bool, bool, bool]:
            # Whether the path is in another session's own directory,
            # whether it is in another session's working directory,
            # whether it is one of the files a session keeps of itself,
            # whether it is a directory with such files somewhere in it,
            # and whether it is a directory with a second name of such a
            # file somewhere in it.
            target = Path(path)
            in_own = any(session_dir and is_inside(target, Path(session_dir)) for _id, _workdir, session_dir in places)
            in_work = any(workdir and is_inside(target, Path(workdir)) for _id, workdir, _session_dir in places)
            record = False
            # A file with a second name (a hard link) may be a record under
            # another name, wherever it lies: among the files the caller
            # works on, too. One with a single name is what its path says,
            # and is not compared file by file.
            linked = _has_second_name(path)
            if in_own or in_work or linked:
                real = os.path.normcase(os.path.realpath(target))
                try:
                    found = os.stat(target) if linked else None
                except OSError:
                    found = None  # Gone since it was looked at: no record.
                for _id, _workdir, session_dir in places:
                    for name in kept_files if session_dir else ():
                        kept = Path(session_dir) / name
                        if real == os.path.normcase(os.path.realpath(kept)):
                            record = True
                        elif found is not None and not record:
                            try:
                                # The same file under a second name.
                                other = os.stat(kept)
                                record = (other.st_dev, other.st_ino) == (found.st_dev, found.st_ino)
                            except OSError:
                                pass  # The record is not there: not the same file.
            # A directory whose files would be read: not one that is
            # written to, which searches nothing. (Asked once, and only
            # where it is gone by: it costs a call, and every file a task
            # agent writes comes by here. Asked so that a directory which
            # is not to be reached says no, and does not raise.)
            searched = not writes and not names_only and os.path.isdir(target)
            holds = (
                (in_own or in_work)
                and searched
                and any(session_dir and is_inside(Path(session_dir), target) for _id, _workdir, session_dir in places)
            )
            aliased = False
            if searched:
                # Only a record that has a second name can lie anywhere
                # under one, and it has none unless somebody gave it one:
                # as a rule nothing is looked for, and no directory is
                # gone through.
                twice: set[tuple[int, int]] = set()
                for _id, _workdir, session_dir in places:
                    for name in kept_files if session_dir else ():
                        try:
                            kept = os.stat(Path(session_dir) / name)
                        except OSError:
                            continue  # Not there.
                        if kept.st_nlink > 1:
                            twice.add((kept.st_dev, kept.st_ino))
                if twice:
                    # (The sessions' own directories, where the records
                    # lie under their first names, are ruled on above.)
                    apart: dict[str, set[tuple[int, int]]] = {}
                    for _id, _workdir, session_dir in places:
                        if not session_dir:
                            continue
                        try:
                            # (Under its real name: going through a
                            # directory follows no link, and meets it
                            # under that name.)
                            real = os.path.realpath(session_dir)
                            kept = os.stat(real)
                        except OSError:
                            continue  # Not there.
                        name = os.path.normcase(os.path.basename(real))
                        apart.setdefault(name, set()).add((kept.st_dev, kept.st_ino))
                    aliased = _task_agents_facade._lies_in(target, twice, apart, SESSION_RECORD_ALIAS_SCAN_ENTRIES)
            return in_own, in_work, record, holds, aliased

        in_own, in_work, record, holds, aliased = await asyncio.to_thread(look)
        if writes:
            if in_own:
                return {"allow": False, "reason": SESSION_WRITE_REFUSED}
            return {"allow": False, "reason": SESSION_RECORD_WRITE_REFUSED} if record else None
        if inside or (not in_own and not in_work):
            # Among the caller's own files, or in no place of another
            # session: the usual rules, unless it is another session's
            # record (under a second name, or where that session's own
            # directory lies in the caller's working directory), or a
            # search of that directory, which would open the record with
            # the rest. A search of a directory further up is left alone:
            # that is the working directory itself, or a folder in it.
            if record:
                return {"allow": False, "reason": SESSION_RECORD_READ_REFUSED}
            if aliased or (inside and in_own and holds):
                return {"allow": False, "reason": SESSION_RECORD_SEARCH_REFUSED}
            return None
        if await self._permissions.forbidden("outside_read"):
            return PermissionDecision(False, DENIED_BY_SETTINGS).to_json()
        if record:
            return {"allow": False, "reason": SESSION_RECORD_READ_REFUSED}
        if holds or aliased:
            return {"allow": False, "reason": SESSION_RECORD_SEARCH_REFUSED}
        return PermissionDecision(True).to_json()
