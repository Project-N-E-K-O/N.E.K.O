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

"""Session forking, shadow-copy carry-on and hand-back, and beside-member spawning.

Method-only mixin: every instance attribute is initialized in
``TaskAgentManager.__init__`` (``nekopower.host.task_agents.manager``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

from nekopower.host.ledger import ENDED_STATUSES, PROJECT_KINDS, Commission, Session, new_session_id
from nekopower.host.permissions import CHOICES, DENIED_BY_USER, OPEN_BESIDE, PermissionAsk
from nekopower.host.runtimes import RUNTIME_BUILTIN, RUNTIME_DSH
from .types import (
    logger,
    _FORK_REMOVE_RETRIES,
    ForkRefused,
    _Beside,
    _Session,
    _carried_on,
    _forked_work_text,
    _Handed,
    _Unsaid,
    _Found,
    _in_builtin,
)

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.task_agents`` via ``monkeypatch.setattr``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import task_agents as _task_agents_facade


class ForksBesideMixin:
    """Session fork, copy carry-on, and beside-member methods."""

    async def _copies_said(self, commission: Commission) -> str:
        """Return what the message that opens a commission says of copies of its session and of its project; empty for nothing.

        Of copies: ``_copies_of_session``. Of its project: to a leader,
        what came of the commissions it handed over and who the members
        are (``Families.leader_notes``); to a member, where other members
        worked (``Families.member_notes``). Put together by the host; no
        model is asked.
        """
        self._handed_said.pop(commission.id, None)
        said = await self._copies_of_session(commission)
        try:
            # Of the git worktree it is worked on in, whoever started it.
            tree = await self._tree_said(commission)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it opens without it
            logger.warning("what a commission is told of its worktree could not be put together", exc_info=True)
            tree = ""
        said = "\n\n".join(part for part in (said, tree) if part)
        if not commission.session_id or commission.parent_id is not None or commission.source == "ask":
            return said
        try:
            place = await self._ledger.get_session(commission.session_id)
            more = ""
            if place is not None and place.kind == "project" and place.project:
                more, told = await self.families.leader_notes(place, commission.id)
                if told:
                    # On record as told once the session took the message (``_note_opened``).
                    self._handed_said[commission.id] = list(told)
            elif place is not None and place.kind == "line":
                more = await self.families.member_notes(place)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it opens without them
            logger.warning("what a commission is told of its project could not be put together", exc_info=True)
            more = ""
        return "\n\n".join(part for part in (said, more) if part)

    async def _copies_of_session(self, commission: Commission) -> str:
        """Return what the message that opens a commission says of copies of its session; empty for nothing.

        Worked on in a copy of its project's session (``_carry_on_in_copy``):
        that it is one, what it can do there, and how it goes back to the
        session itself. Worked on in a project's session that copies of it
        finished commissions in since it took an opening message last:
        which, and what came of them (``TASK_AGENT_FORKED_WORK_MAX_ITEMS``,
        within ``TASK_AGENT_FORKED_WORK_MAX_TOKENS``). What was said in
        them did not come back to it.
        """
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from utils.tokenize import count_tokens, truncate_to_tokens

        self._forks_said.pop(commission.id, None)
        if not commission.session_id or commission.parent_id is not None:
            return ""
        place = await self._ledger.get_session(commission.session_id)
        if place is None:
            return ""
        if place.kind == "fork":
            carried = _carried_on(commission) and place.forked_from and await self._ledger.carried_on_in(place.id)
            if not carried:
                return ""
            # What the session itself waits for the user to answer, if it
            # does: not this copy's to answer or to settle.
            waited_on = await self._waited_on(str(place.forked_from))
            if not waited_on:
                return texts.TASK_AGENT_COMMISSION_IN_COPY
            return f"{texts.TASK_AGENT_COMMISSION_IN_COPY}\n\n{texts.TASK_AGENT_COPY_SESSION_WAITS.format(question=waited_on)}"
        # Whichever commission the session itself is opened with next, one
        # filed for what another session wrote included.
        if place.kind not in PROJECT_KINDS:
            return ""
        finished = await self._ledger.forked_work(place.id, limit=settings.TASK_AGENT_FORKED_WORK_MAX_ITEMS)
        # The one it follows up is told of by the line that says so
        # (``TASK_AGENT_COMMISSION_CONTINUES_IN_COPY``), and not a second time
        # here; told all the same.
        told = [item.id for item in finished if item.id == commission.continues]
        done = [item for item in finished if item.id not in (commission.id, commission.continues)]
        if not done:
            if told:
                self._forks_said[commission.id] = told
            return ""
        # Told once the session took the message that says it (``_note_opened``).
        self._forks_said[commission.id] = [*told, *(item.id for item in done)]
        return _forked_work_text(done, [])

    def _news_of_copies(self, main: str) -> None:
        """Have the session itself told what copies of it finished or handed back, once it is at work (``_tell_of_copies``)."""
        self._copy_news.add(main)
        if main in self._telling_of_copies or self._closed:
            return
        self._telling_of_copies.add(main)
        self._spawn(self._tell_of_copies(main)).add_done_callback(lambda _done: self._telling_of_copies.discard(main))

    async def _ended_in_copy(self, session_id: str) -> None:
        """A commission ended in a session: news for the session it is a copy of, if it is one made to carry it on in."""
        try:
            place = await self._ledger.get_session(session_id)
            if place is None or place.kind != "fork" or not place.forked_from:
                return
            if not await self._ledger.carried_on_in(place.id):
                return
        except Exception:  # noqa: BLE001 - told with the next commission opened in it, as ever
            logger.warning("reading the session a commission ended in failed", exc_info=True)
            return
        self._news_of_copies(str(place.forked_from))

    async def _tell_of_copies(self, main: str) -> None:
        """Tell a project's session, while it is at work, what copies of it finished or handed back. For its information only.

        Said before its next step (``steer``), as the user's words are,
        but as nobody's: it approves nothing (``_Found``). Only while the
        commission that holds it is at work, with a run under way: not
        while it waits for the user (it is told once it goes on), nor
        while it rests or nothing holds it, which this would wake; what
        it finished is told then with the next commission opened in it
        (``_copies_said``), and what it handed back comes with that
        commission itself. Each finished one is told once (``told_to_session``).
        """
        from config import nekopower_settings as settings

        def at_work() -> tuple[str, _Session, Any] | None:
            # The commission that holds it, its place and its runtime, while
            # it may be told: at work, with a run under way.
            holder = self._held_by.get(main)
            live = self._sessions.get(holder) if holder is not None else None
            runtime = self._runtime_of(live) if live is not None else None
            if (
                holder is None
                or live is None
                or runtime is None
                or live.carries_on_for is not None
                or live.closing
                or not live.ready
                or live.idle
                or live.settling
                or live.ending is not None
                or live.awaiting_prompt
                or live.stands is None
                or live.stands.status != "working"
                or live.stands.resting
            ):
                return None
            return holder, live, runtime

        while main in self._copy_news and not self._closed:
            if at_work() is None:
                # Kept for when it is at work again (``_handle``).
                return
            self._copy_news.discard(main)
            most = settings.TASK_AGENT_FORKED_WORK_MAX_ITEMS
            news = self._handed_back_news.pop(main, [])
            handed: list[tuple[Commission, str]] = []
            try:
                # Past those already on their way to it: what is left over
                # once a text has told of the most it may goes with the next.
                # (One more than is told: whether any is left over.)
                passed = len(self._copies_on_the_way) + sum(len(items) for items in self._forks_said.values())
                found = await self._ledger.forked_work(main, limit=most + passed + 1)
                # Told by an opening message that is on its way (``_copies_said``)
                # until it is on record as told (``_note_opened``), as it stands
                # once that was read: not a second time here.
                told_in_openings = {item for items in self._forks_said.values() for item in items}
                waiting_to_be_told = [
                    item for item in found if item.id not in self._copies_on_the_way and item.id not in told_in_openings
                ]
                done = waiting_to_be_told[:most]
                while news and len(done) + len(handed) < most:
                    commission_id, copy_id = news[0]
                    # Only one that still waits for it: one that is started is
                    # opened with what the copy found, which says it all.
                    if commission_id in self._queue and self._session_of.get(commission_id) == main:
                        waiting = await self._ledger.get(commission_id)
                        if waiting is not None:
                            handed.append((waiting, copy_id))
                    news.pop(0)
            except Exception:  # noqa: BLE001 - kept, and tried again when it is next seen at work
                logger.warning("reading what copies of a session did failed; it is told later", exc_info=True)
                self._copy_news.add(main)
                self._handed_back_news[main] = [
                    *((waiting.id, copy_id) for waiting, copy_id in handed),
                    *news,
                    *self._handed_back_news.get(main, []),
                ]
                return
            if news or len(waiting_to_be_told) > len(done):
                # More than one text tells of: the rest with the next.
                self._handed_back_news[main] = [*news, *self._handed_back_news.get(main, [])]
                self._copy_news.add(main)
            if not done and not handed:
                continue
            now = at_work()
            if now is None:
                # Not at work any more once the ledger was read: kept for later.
                self._copy_news.add(main)
                self._handed_back_news[main] = [
                    *((waiting.id, copy_id) for waiting, copy_id in handed),
                    *self._handed_back_news.get(main, []),
                ]
                return
            holder, live, runtime = now
            text = _Found(_forked_work_text(done, handed))
            told = [item.id for item in done]
            # By the number the text is sent under (``_send_text`` counts it
            # before it awaits anything): put on record as told once the
            # session took it (``_on_taken``). A run that is stopped first
            # drops it, and so does a refusal (``_withdraw``): it is told again.
            number = live.sent + 1
            if told:
                live.copies_told[number] = told
                self._copies_on_the_way.update(told)
            if handed:
                live.handed_told[number] = [(waiting.id, copy_id) for waiting, copy_id in handed]
            # What a copy wrote, from what it read, is about to stand in the
            # conversation of the commission at work: what the user allowed
            # it for the session or the run is asked again from here on, as
            # for a note from another session. Nothing is awaited between
            # the look above and the text being sent.
            self._permissions.received_external_input(holder)
            try:
                await self._send_text(runtime, live, "steer", text)
            except BaseException:
                live.copies_told.pop(number, None)
                live.handed_told.pop(number, None)
                live.copies_dropped = [item for item in live.copies_dropped if item not in told]
                live.handed_dropped = [pair for pair in live.handed_dropped if pair[0] not in {w.id for w, _ in handed}]
                self._copies_on_the_way.difference_update(told)
                # Told again when it is next seen at work (or with the next
                # commission opened in it); what was handed back as well.
                self._copy_news.add(main)
                self._handed_back_news[main] = [
                    *((waiting.id, copy_id) for waiting, copy_id in handed),
                    *self._handed_back_news.get(main, []),
                ]
                raise
            try:
                # On record as what it was told, under a type of its own: no
                # word of the user's, which a review reads ``told`` for.
                await self._ledger.append_event(holder, "told_of_copies", {"text": str(text)})
            except Exception:  # noqa: BLE001 - the note is for the record; it was told all the same
                logger.warning("noting what a session was told of its copies failed", exc_info=True)

    def _copies_dropped(self, session: _Session) -> None:
        """Make what copies finished or handed back, told of in a text the session did not take, to be told again."""
        if not session.copies_dropped and not session.handed_dropped:
            return
        self._copies_on_the_way.difference_update(session.copies_dropped)
        if session.handed_dropped:
            self._handed_back_news[session.session_id] = [
                *session.handed_dropped,
                *self._handed_back_news.get(session.session_id, []),
            ]
        session.copies_dropped, session.handed_dropped = [], []
        self._news_of_copies(session.session_id)

    async def _copies_taken(self, session: _Session, told: list[str]) -> None:
        """Put on record that a session took the text that told it what copies of it finished (``_tell_of_copies``)."""
        for commission_id in told:
            try:
                await self._ledger.append_event(
                    commission_id, "told_to_session", {"commission_id": session.commission_id, "steered": True}
                )
            except Exception:  # noqa: BLE001 - told again with the next commission, which does little harm
                logger.warning("noting that a session was told of what a copy of it did failed", exc_info=True)
            finally:
                self._copies_on_the_way.discard(commission_id)

    # -- carrying on in a copy of a project's session ---------------------

    def _carrying_on(self, session_id: str) -> int:
        """How many commissions are carried on in copies of a session, or are about to be.

        Each once: one that was started in its copy a moment ago is still
        among those being copied for until that is through.
        """
        at_work = {commission_id for commission_id, item in self._sessions.items() if item.carries_on_for == session_id}
        return len(at_work | self._copying_for.get(session_id, set()))

    def _waits_for(self, commission_id: str, session_id: str) -> bool:
        """Whether a commission is queued for a session that another commission holds, and nothing else.

        Held by another commission, that is: not only kept from starting
        while a commission is being put back among those that wait for it
        (``_handing_back_to``), which is over in a moment, and after which
        it starts there in its turn.
        """
        holder = self._held_by.get(session_id)
        return (
            commission_id in self._queue
            and commission_id not in self._sessions
            and commission_id not in self._resume
            and self._session_of.get(commission_id) == session_id
            and holder is not None
            and holder != commission_id
        )

    def _try_carry_on(self, commission: Commission) -> bool:
        """Have a commission that was queued behind another one of its session carried on in a copy, if it may be.

        Looked at once, as it is queued, with nothing awaited: whether it
        is of the kind that is (``_carried_on``), whether its session is
        held by another commission, and whether that many are carried on
        in copies of it already (``TASK_AGENT_FORK_CONTINUE_MAX_PER_SESSION``).
        The rest is looked at by ``_carry_on_in_copy``, which runs by itself:
        the commission stays queued meanwhile, and is started in its
        session as ever should that be free first. Returns whether that
        look was begun: once it is through, and the commission still
        waits, another member may do it instead (``_try_beside``).
        """
        from config import nekopower_settings as settings

        if not settings.TASK_AGENT_FORK_CONTINUE_ENABLED or self._closed or not _carried_on(commission):
            return False
        main = self._session_of.get(commission.id)
        if main is None or not self._waits_for(commission.id, main):
            return False
        if self._carrying_on(main) >= settings.TASK_AGENT_FORK_CONTINUE_MAX_PER_SESSION:
            return False
        waiting = self._copying_for.setdefault(main, set())
        waiting.add(commission.id)

        def over(_done: Any) -> None:
            waiting.discard(commission.id)
            if not waiting and self._copying_for.get(main) is waiting:
                del self._copying_for[main]
            # Not carried on in a copy: looked at for another member, with what was told of it meanwhile.
            self._try_beside(commission.id)

        self._spawn(self._carry_on_in_copy(commission.id, main)).add_done_callback(over)
        return True

    async def _carry_on_in_copy(self, commission_id: str, main: str) -> None:
        """Carry a queued commission on in a copy of the session it waits for, if that is worth it.

        Only a project's session of the built-in runtime is copied, and
        only while the commission that holds it is at work or waits for the
        user (one that rests lets the session go). Its conversation is sent
        with every request of the copy: from a provider's cache, most
        likely, while the session made a request lately
        (``TASK_AGENT_FORK_CONTINUE_HOT_SECONDS``); past that, it is not
        copied when it is long (``TASK_AGENT_FORK_CONTINUE_COLD_MAX_TOKENS``),
        and the commission waits as it would have. Nor while no place is
        free, NekoPower is switched off, or everything is being stopped.

        Copied (``fork_session``), and looked at again with nothing awaited
        from there to where it takes its place: still queued for the
        session, which is still held, with a place free. Then it is taken
        out of the queue and started in the copy, which the ledger has as
        its session from then on. Otherwise the copy is removed at once and
        the commission waits on.
        """
        from config import nekopower_settings as settings

        if not self._waits_for(commission_id, main):
            return
        kept = await self._ledger.get_session(main)
        if kept is None or kept.kind not in PROJECT_KINDS or kept.runtime == RUNTIME_DSH or kept.status == "cleared":
            return
        holder = self._held_by.get(main)
        stands = await self._ledger.get(holder) if holder is not None else None
        if stands is None or stands.status not in ("working", "waiting_user") or stands.resting:
            return

        def worth_it() -> bool:
            # Looked at again once it is copied, which takes a moment: a
            # cache that went cold meanwhile makes a long copy dear as well.
            last = self._last_request_at.get(main)
            hot = last is not None and asyncio.get_running_loop().time() - last <= settings.TASK_AGENT_FORK_CONTINUE_HOT_SECONDS
            return hot or kept.context_tokens <= settings.TASK_AGENT_FORK_CONTINUE_COLD_MAX_TOKENS

        def may() -> bool:
            return (
                worth_it()
                # Its record cleared meanwhile: the copy holds what is gone.
                and kept.status != "cleared"
                and self._waits_for(commission_id, main)
                and not self._closed
                and len(self._sessions) < self.limit
                and self._is_switched_on()
                and not getattr(self._waker, "stopping", False)
            )

        if not may():
            return
        # Only a side question: one answered by looking, with nothing
        # changed. Work waits for the session, and for whatever the session
        # waits for the user to answer first. Asked last, as it costs a
        # request, of a commission that would be carried on otherwise; and
        # all of it looked at again once it is told.
        if not await self._is_side_question(commission_id, may) or not may() or not self._still_side_question(commission_id):
            return
        try:
            copy = await self.fork_session(main)
        except ForkRefused as exc:
            logger.info("commission %s waits for its session: it could not be copied (%s)", commission_id, exc)
            return
        except Exception:  # noqa: BLE001 - it waits for its session as it would have
            logger.warning("copying a session for commission %s failed; it waits for the session", commission_id, exc_info=True)
            return
        try:
            # How long the session's conversation is by now: it may have
            # grown while it was copied.
            kept = await self._ledger.get_session(main) or kept
        except Exception:  # noqa: BLE001 - looked at as it was read before
            logger.warning("reading a session again after it was copied failed", exc_info=True)
        if not may() or not self._still_side_question(commission_id):
            await self._discard_copy(copy)
            return
        self._queue.remove(commission_id)
        self._session_of[commission_id] = copy.id
        if not self._take_place(commission_id):
            # (Not to be: a place is free, and the copy is held by nobody.)
            self._session_of[commission_id] = main
            self._queue.append(commission_id)
            await self._discard_copy(copy)
            return
        # (Counted among those carried on in copies of the session from here
        # on: by ``_copying_for`` until it is started, by its place after.)
        # Until it is on record with the copy, or back in the queue, nothing
        # else starts in the session itself: one queued behind it would
        # otherwise go first, should it come back.
        self._handing_back_to[main] = self._handing_back_to.get(main, 0) + 1
        on_record = False
        try:
            on_record = await self._put_on_record_with_copy(commission_id, copy, main, holder)
        finally:
            left = self._handing_back_to[main] - 1
            if left:
                self._handing_back_to[main] = left
            else:
                del self._handing_back_to[main]
            if self._queue and len(self._sessions) < self.limit and not self._closed:
                self._spawn(self._start_next())
        if on_record:
            await self._launch(commission_id)

    async def _is_side_question(self, commission_id: str, still: Callable[[], bool] | None = None) -> bool:
        """Whether a commission is a side question, as the judge says (``set_side_question_judge``).

        Asked once for the same words of the commission: again only when
        they changed (a follow-up went on with it). Not told (no judge, a
        request that failed, an answer that cannot be read): it is not one,
        and that is not kept, so it is asked again the next time.
        """
        judge = self._side_question_judge
        if judge is None:
            return False
        commission = await self._ledger.get(commission_id)
        if commission is None or (still is not None and not still()):
            # (Nor asked of one that would not be carried on by now.)
            return False
        # What the session waits for the user to answer, if it does: a
        # request that goes on with that is work, which the judge cannot
        # tell without it.
        main = self._session_of.get(commission_id)
        waiting_for = await self._waited_on(main) if main else ""
        if still is not None and not still():
            return False
        words = f"{commission.dispatch_text}\x00{commission.user_text}\x00{waiting_for}"
        added = self._said_meanwhile(commission_id)
        said = self._side_said.get(commission_id)
        if said is not None and said[0] == words and said[1] == added:
            return said[2]
        try:
            verdict = await judge(
                commission.dispatch_text, "\n".join([commission.user_text, *added]).strip(), waiting_for
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not told: it waits for its session, as work does
            logger.warning("telling whether commission %s is a side question failed", commission_id, exc_info=True)
            verdict = None
        if verdict is None:
            logger.info("commission %s waits for its session: whether it is a side question was not told", commission_id)
            return False
        # Kept with what was said to it meanwhile, as it was asked: what is
        # said to it after that is looked at by ``_still_side_question``.
        if commission.status not in ENDED_STATUSES:
            self._side_said[commission_id] = (words, added, verdict)
        if not verdict:
            logger.info("commission %s waits for its session: it is work, not a side question", commission_id)
        return verdict

    def _said_meanwhile(self, commission_id: str) -> tuple[str, ...]:
        """What the user said to a queued commission since it was handed over, which goes along with it when it starts."""
        return tuple(
            str(note) for note in self._notes.get(commission_id, []) if not isinstance(note, (_Handed, _Unsaid, _Found))
        )

    def _still_side_question(self, commission_id: str) -> bool:
        """Whether what goes along with a commission is still what was told to be a side question (``_is_side_question``)."""
        said = self._side_said.get(commission_id)
        return said is not None and said[2] and said[1] == self._said_meanwhile(commission_id)

    async def _waited_on(self, session_id: str) -> str:
        """Return the question that the commission holding a session waits for the user to answer; empty for none."""
        from config.nekopower_settings import TASK_AGENT_FORK_PENDING_QUESTION_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        holder = self._held_by.get(session_id)
        stands = await self._ledger.get(holder) if holder is not None else None
        if stands is None or stands.status != "waiting_user":
            return ""
        question = stands.question() or {}
        asked = question.get("question")
        if not isinstance(asked, str) or not asked.strip():
            parts: list[str] = []

            def walk(value: Any) -> None:
                if isinstance(value, str):
                    parts.append(value)
                elif isinstance(value, dict):
                    for key, item in value.items():
                        if key not in ("id", "kind"):
                            walk(item)
                elif isinstance(value, list):
                    for item in value:
                        walk(item)

            walk(question)
            asked = " ".join(parts)
        asked = " ".join(asked.split())
        return truncate_to_tokens(asked, TASK_AGENT_FORK_PENDING_QUESTION_MAX_TOKENS) if asked else ""

    async def _put_on_record_with_copy(self, commission_id: str, copy: Session, main: str, holder: str | None) -> bool:
        """Put a commission on record with the copy it is carried on in; back in the queue where that fails. Returns whether it is on record (``_carry_on_in_copy``)."""
        try:
            # On record with the copy from here on: what it is at, what it
            # used, and where it is taken up again. The copy is marked as
            # one made to carry a commission on in first: written the other
            # way round, a commission could be the copy's on record with
            # nothing to say the copy hands it back when it needs a change.
            await self._ledger.append_event(
                commission_id, "carried_on_in_copy", {"session_id": copy.id, "of": main, "behind": holder}
            )
            await self._ledger.update(commission_id, session_id=copy.id)
        except Exception as exc:  # noqa: BLE001 - it waits for the session itself as before
            logger.warning("carrying commission %s on in a copy of its session failed: %s", commission_id, exc)
            # Back where it was: on record it is still the session itself's.
            await self._back_in_line(commission_id, copy, main)
            return False
        if not self._still_side_question(commission_id):
            # More was said to it while it was put on record, which goes
            # with it and was not what was told about: back to the session
            # itself after all. (Said later than this, before the copy takes
            # it, it is in the copy, which hands it back for a change.)
            try:
                await self._ledger.update(commission_id, session_id=main)
            except Exception:  # noqa: BLE001 - it goes on in the copy (on record as its), which hands it back for a change
                logger.warning("taking commission %s back from its copy failed", commission_id, exc_info=True)
                return True
            # On record as the session itself's from here on: back there,
            # whether or not the note of it can be written.
            try:
                await self._ledger.append_event(commission_id, "not_carried_on", {"copy": copy.id, "of": main})
            except Exception:  # noqa: BLE001 - the note is for the record
                logger.warning("noting that commission %s was taken back from its copy failed", commission_id, exc_info=True)
            await self._back_in_line(commission_id, copy, main)
            return False
        return True

    async def _back_in_line(self, commission_id: str, copy: Session, main: str) -> None:
        """Let go of a commission's place in a copy it is not carried on in after all, and queue it for the session itself again."""
        # Its place and the copy are let go of.
        self._sessions.pop(commission_id, None)
        if self._held_by.get(copy.id) == commission_id:
            del self._held_by[copy.id]
        self._session_of[commission_id] = main
        if commission_id not in self._queue:
            # Back among those that wait for the session itself, in the
            # order the ledger took them (a commission handed back, or
            # queued again after a restart, included), as one handed
            # back is (``_put_back_in_order``); nothing starts in the
            # session meanwhile (``_handing_back_to``). Where that order
            # cannot be read, at the end.
            self._queue.append(commission_id)
            try:
                filed = await self._ledger.filed_order(
                    [item for item in list(self._queue) if self._session_of.get(item) == main]
                )
            except Exception:  # noqa: BLE001 - it waits at the end of the queue, which is all that is lost
                logger.warning("reading in which order commissions were filed failed", exc_info=True)
                filed = {}
            if commission_id in filed:
                self._put_back_in_order(commission_id, main, filed)
        await self._discard_copy(copy)

    def _watch_copy(self, session: _Session, event: dict[str, Any]) -> None:
        """Note, of a copy a commission is carried on in, a call that hands the commission back, and what it writes down after.

        The call itself is answered by what stands in for the tool (it is
        not carried out); before its next request the copy is told to
        write down what it found (``_hand_back_step``).
        """
        from config.nekopower_settings import TASK_AGENT_FORK_HAND_BACK_TOOLS

        kind = event.get("type")
        if kind == "tool_start" and session.hand_back is None:
            name = str(event.get("tool_name") or "")
            if name in TASK_AGENT_FORK_HAND_BACK_TOOLS:
                session.hand_back = name
        elif kind == "text" and session.hand_back_left is not None:
            session.findings.append(str(event.get("delta") or ""))

    async def _hand_back_step(self, session: _Session) -> dict[str, Any] | None:
        """Answer a copy that called a tool which changes something, before its next request.

        First it is told to write down what it found, and has
        ``TASK_AGENT_FORK_HAND_BACK_REQUESTS`` requests for that. When they
        are made and its run goes on, it is handed back with what it wrote
        (``_hand_back``) and the run ends. A copy whose commission ended
        meanwhile (it reported) goes on as any session does.
        """
        from config.nekopower_settings import TASK_AGENT_FORK_HAND_BACK_REQUESTS
        from config.prompts.prompts_nekopower_brain import TASK_AGENT_FORK_HAND_BACK

        if session.handing_back is not None:
            # Handed back already, its close asked again by the host: no
            # more requests of it.
            return {"stop": True}
        if session.hand_back_left is None:
            left = max(1, int(TASK_AGENT_FORK_HAND_BACK_REQUESTS))
            session.hand_back_left = left - 1
            session.findings = []
            try:
                await self._ledger.append_event(session.commission_id, "hand_back_told", {"tool": session.hand_back})
            except Exception:  # noqa: BLE001 - the note is for the record; the copy is told all the same
                logger.warning("noting that a copy is to hand its commission back failed", exc_info=True)
            return {"notice": TASK_AGENT_FORK_HAND_BACK.format(tool=session.hand_back, requests=left)}
        if session.hand_back_left > 0:
            session.hand_back_left -= 1
            return None
        if await self._hand_back_or_later(session):
            return {"stop": True}
        return None

    async def _hand_back(self, session: _Session, *, said: str = "") -> bool:
        """Hand a commission carried on in a copy back to the session the copy was made of. Returns whether it was.

        ``said`` is what the copy's run ended with; else what it wrote
        since it was told to write down what it found. Only a commission
        that is still at work is handed back. The copy is closed; once it
        is, the commission goes on in the session itself (``_handed_back``).
        A close that cannot be asked for raises, the commission marked as
        handed back all the same (``_hand_back_or_later`` tries again).
        """
        if session.handing_back is not None or session.closing or session.carries_on_for is None:
            return False
        commission = await self._ledger.get(session.commission_id)
        if commission is None or commission.status != "working" or session.closing:
            return False
        session.handing_back = (said or "".join(session.findings)).strip()
        try:
            await self._ledger.append_event(
                session.commission_id,
                "handed_back",
                {"tool": session.hand_back, "to": session.carries_on_for, "copy": session.session_id},
            )
        except Exception:  # noqa: BLE001 - the note is for the record; it is handed back all the same
            logger.warning("noting that a copy handed its commission back failed", exc_info=True)
        if self._runtime_of(session) is None:
            # No runtime holds it (it went away): as good as closed, and
            # nothing will say so.
            await self._handed_back(session)
            return True
        await self._close_session(session)
        return True

    async def _hand_back_or_later(self, session: _Session, *, said: str = "") -> bool:
        """Hand a copy's commission back (``_hand_back``); a close that cannot be asked for now is asked again by the host.

        Not by another model request (before which the copy would be
        looked at again): the copy has had what it gets for writing down
        what it found, and once its run is over nothing comes by at all.
        So the close is asked again a few times, a while apart, by a task
        of its own; given up on, the commission fails as one whose run
        failed, and lets its place go. Meanwhile what the copy's runs say
        as they end decides nothing (it is marked as handed back).
        """
        try:
            return await self._hand_back(session, said=said)
        except Exception:  # noqa: BLE001 - asked again by a task of its own
            logger.warning("handing a copy's commission back failed; its close is asked again", exc_info=True)
            if session.handing_back is None:
                # It failed before it was marked (the ledger was not to be
                # read): marked now, and whether it is still at work is
                # looked at once the copy is closed (``_back_in_session``).
                session.handing_back = (said or "".join(session.findings)).strip()
            self._spawn(self._close_copy_later(session))
            return True

    async def _close_copy_later(self, session: _Session) -> None:
        """Ask again to close a copy that hands its commission back (``_hand_back_or_later``)."""
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
                logger.warning("closing a copy to hand its commission back failed again", exc_info=True)
        if self._sessions.get(commission_id) is session and not session.closing and not self._closed:
            session.handing_back = None
            await self._fail(commission_id, "its copy could not be closed to hand it back to its session", said="run")
            self._discard(commission_id)

    async def _handed_back(self, session: _Session) -> None:
        """Go on, in the session itself, with a commission a copy handed back, the copy being closed.

        The copy lets go of its place and its session; the ledger has the
        session itself as the commission's from here on; the commission is
        queued for it (``_wake_working``), with what the copy wrote down,
        framed as no word of the user's, and then what the user said to it
        while it was in the copy (which the session itself never saw).
        """
        commission_id, copy_id, main = session.commission_id, session.session_id, str(session.carries_on_for)
        candidates = self._candidates.get(commission_id)
        self._discard(commission_id)
        if self._held_by.get(copy_id) == commission_id:
            # Let go of here: the commission is not over, and the look at
            # the hold ``_discard`` has made would leave it held.
            del self._held_by[copy_id]
        if candidates is not None:
            self._candidates[commission_id] = candidates
        if self._closed:
            return
        # Until it is back in the queue, in its place, nothing else is
        # started in the session itself (``_session_free_for``): the session
        # may come free meanwhile, and one handed over after this one would
        # be worked on first. Other sessions go on as ever.
        self._handing_back_to[main] = self._handing_back_to.get(main, 0) + 1
        try:
            await self._back_in_session(session, commission_id, copy_id, main)
        finally:
            left = self._handing_back_to[main] - 1
            if left:
                self._handing_back_to[main] = left
            else:
                del self._handing_back_to[main]
            if self._queue and len(self._sessions) < self.limit and not self._closed:
                self._spawn(self._start_next())

    async def _back_in_session(self, session: _Session, commission_id: str, copy_id: str, main: str) -> None:
        """Put a commission a copy handed back on record as the session itself's, and queue it for it (``_handed_back``)."""
        import secrets

        from config.nekopower_settings import REST_LOOK_RETRY_DELAYS, TASK_AGENT_FORK_FINDINGS_MAX_TOKENS
        from config.prompts import prompts_nekopower_brain as texts
        from config.prompts.prompts_nekopower_sessions import frame
        from utils.tokenize import truncate_to_tokens

        try:
            await self._ledger.update_session(copy_id, status="closed")
            stands = await self._looked(lambda: self._ledger.get(commission_id), what="for a commission a copy hands back")
            if stands is None or stands.status != "working":
                # Over meanwhile (stopped, say), or never read before it was
                # handed back: it stays the copy's, and nothing is queued.
                return
            told = await self._told_in_copy(commission_id)
            await self._ledger.update(commission_id, session_id=main)
            # The session itself's here too, now that the ledger has it so.
            self._session_of[commission_id] = main
        except Exception as exc:  # noqa: BLE001 - the commission fails with the reason
            logger.warning("handing commission %s back to its session failed: %s", commission_id, exc)
            await self._fail(commission_id, f"it could not be handed back to its session: {exc}", said="run", detail=str(exc))
            return
        nonce = secrets.token_hex(8)
        message = _Found(
            frame(
                texts.TASK_AGENT_FORK_HANDED_BACK.format(tool=session.hand_back, nonce=nonce),
                truncate_to_tokens(session.handing_back or "", TASK_AGENT_FORK_FINDINGS_MAX_TOKENS)
                or texts.TASK_AGENT_FORK_FOUND_NOTHING,
                nonce,
            )
        )
        # Queued with what it carries, asked again a few times where the
        # ledger cannot be written this moment: left as it is, it would stay
        # at work with nobody at it and in no queue. (One that is on its way
        # back already is not queued a second time: ``_wake_working`` sees
        # to that.)
        for delay in (*REST_LOOK_RETRY_DELAYS, None):
            try:
                woken = await self._wake_working(commission_id, message, reason="handed back by a copy of its session")
                break
            except Exception as exc:  # noqa: BLE001 - asked again; the last failure ends the commission
                if delay is None:
                    logger.warning("queueing commission %s for its session again failed: %s", commission_id, exc)
                    await self._fail(commission_id, f"it could not be handed back to its session: {exc}", said="run", detail=str(exc))
                    return
                await asyncio.sleep(delay)
        if not woken:
            return
        self._notes.setdefault(commission_id, []).extend(told)
        # The session itself is told, should it be at work, that this waits
        # for it (``_tell_of_copies``).
        self._handed_back_news.setdefault(main, []).append((commission_id, copy_id))
        self._news_of_copies(main)
        # In its place among those that wait for the session itself, by
        # when each was filed: read now that it is queued, with nothing
        # started in the session meanwhile (``_handing_back_to``), so that
        # every one queued so far is among them. One that is queued while
        # this is read counts as later.
        try:
            filed = await self._ledger.filed_order(
                [item for item in list(self._queue) if self._session_of.get(item) == main]
            )
        except Exception:  # noqa: BLE001 - it waits at the end of the queue, which is all that is lost
            logger.warning("reading in which order commissions were filed failed", exc_info=True)
            return
        if commission_id in filed:
            self._put_back_in_order(commission_id, main, filed)

    async def _told_in_copy(self, commission_id: str, *, before: int | None = None) -> list[str]:
        """Return what the user said to a commission a copy hands back, and answered it there, in the order it was said.

        Everything the user said to it so far went to the copy: what came
        while it was queued went with the work it was given there. (It is
        handed back once: from then on it is the session's own.) Read a
        page at a time, each asked again a few times where the ledger
        cannot be read this moment, before the commission is moved.
        """
        told: list[str] = []
        after = 0
        while True:
            page = await self._looked(
                lambda: self._ledger.events(commission_id, after=after), what="for what was said to a copy"
            )
            told.extend(
                str(event.payload.get("text") or "")
                for event in page
                if event.type in ("told", "answered")
                and str(event.payload.get("text") or "").strip()
                and (before is None or event.seq < before)
            )
            if len(page) < 500 or (before is not None and page[-1].seq >= before):
                return told
            after = page[-1].seq

    def _put_back_in_order(self, commission_id: str, main: str, filed: dict[str, int]) -> None:
        """Put a commission handed back by a copy among those that wait for the session itself, in the order they were handed over.

        Queued again, it went to the end. Handed over before others that
        wait for the session, it is to be worked on before them: a later
        one may build on what this one changes. It goes in front of the
        first that was filed after it (in the order the ledger took them,
        ``filed``, which no two share), or that came to wait since that was
        read (which is later). Commissions of other
        sessions keep their places. Nothing is awaited here: whoever
        starts the next one finds it in its place.
        """
        if commission_id not in self._queue:
            return
        self._queue.remove(commission_id)
        at = len(self._queue)
        for index, item in enumerate(self._queue):
            if self._session_of.get(item) != main:
                continue
            known = filed.get(item)
            if known is None or known > filed[commission_id]:
                at = index
                break
        self._queue.insert(at, commission_id)

    # -- another member beside a busy one ---------------------------------

    def _try_beside(self, commission_id: str) -> None:
        """Have a commission that waits for a busy member looked at for another member to do it beside that one.

        Looked at once, as it is queued (after the look for a copy to
        carry it on in, where there is one: ``_try_carry_on``), with
        nothing awaited: only one of hers that went to the member that
        works in its project for nothing its words said, and says
        something plainly apart from that member's work
        (``Verdict.apart``, kept by ``_give_session``). The rest is
        looked at by ``_beside``, which runs by itself: whether it waits
        for that member at all, to begin with.
        """
        main = self._apart.pop(commission_id, None)
        if main is None or self._closed:
            return
        self._spawn(self._beside(commission_id, main))

    async def _beside(self, commission_id: str, main: str) -> None:
        """Have another member do a commission that waits for a busy one, in a git worktree of its own, where the user wants that.

        In a project without a leader everything goes to the member that
        works, and waits while that one is at something. Work that is
        plainly about something else (``_try_beside``) need not: what the
        user set says what becomes of it (``landing_settings.py``,
        ``beside``). ``queue``: it waits, as it did before. ``ask``: the
        user is asked, as for any task agent that can change files opened
        beside the ones a project has (``OPEN_BESIDE``, by whose setting
        none is opened where that is forbidden, and none is asked about
        where it is allowed). ``open``: another is opened with nobody
        asked, unless that setting forbids it.

        Only while all of this holds: the commission is still queued
        behind that member, whose project has no leader; the member
        changes files, and so would the new one; it is held by a
        commission at work or waiting for the user; a worktree can be
        made for the project (``tree_possible``); the project has fewer
        members that take work than it may (``PROJECT_MEMBERS_MAX``); the
        commission was not told to be a side question (by what was told
        already: nobody is asked for this); NekoPower is switched on and
        nothing is being stopped. No model is asked.

        Asked, the commission keeps its place in the queue, is on record
        as waiting for the user (with no session: it is at none) and
        starts nowhere (``_session_free_for``). No member is made until
        the user allows it, and then with no other made in between
        (``Families.placing``): what
        is asked says which it would be (``detail``: ``member``,
        ``preset``, ``project``, ``beside``). Allowed, a member is made
        (``seat`` ``tree``) and the commission is its: started at once
        where a place is free, else next in the queue. Refused, that is
        put on record (``beside_declined``) and it waits for the busy
        member where it stood; it is not asked about again, being looked
        at once (``_try_beside``). Should that member come free first,
        what was asked is taken back and the commission starts there in
        its turn (``_beside_came_free``); stopped, it is taken back as
        well (``abort``).
        """
        from config import nekopower_settings as settings
        from nekopower.host.landing_settings import BESIDE_ASK, BESIDE_OPEN
        from nekopower.host.orchestrator import WORKING_PRESET
        from nekopower.host.templates import model_name

        def waits() -> bool:
            return (
                self._waits_for(commission_id, main)
                and not self._closed
                and self._is_switched_on()
                and not getattr(self._waker, "stopping", False)
            )

        async def room(commission: Commission, kept: Session) -> bool:
            # Whether another member can be had now: fewer than the project may have, and a worktree for it.
            taking = [
                member
                for member in await self._ledger.members(commission.lanlan_name, str(kept.project))
                if await self._ledger.session_alive(member)
            ]
            if len(taking) >= settings.PROJECT_MEMBERS_MAX:
                return False
            return await self.tree_possible(kept.project, kept.workdir or commission.workdir)

        try:
            choice = (await self.landing.get()).get("beside")
            if choice not in (BESIDE_ASK, BESIDE_OPEN):
                return
            commission = await self._ledger.get(commission_id)
            kept = await self._ledger.get_session(main)
            if commission is None or kept is None:
                return
            if await self._ledger.lead_session(commission.lanlan_name, kept.project) is not None:
                # Its leader says who does what.
                return
            holder = self._held_by.get(main)
            stands = await self._ledger.get(holder) if holder is not None else None
            if stands is None or stands.status not in ("working", "waiting_user") or stands.resting:
                # (One that rests lets the session go: asked now, it would be taken back in a moment.)
                return
            told = self._side_said.get(commission_id)
            if told is not None and told[2]:
                # Answered by looking, with nothing changed: no member is opened for that.
                return
            # What the busy one can change goes by the tools its run was opened with, as where it is
            # noted to change files (``_note_writer``): its preset may say otherwise by now.
            live = self._sessions.get(holder)
            opened = live.changes_files if live is not None and live.session_id == main else None
            if not (opened if opened is not None else (await self._template_of(kept)).changes_files):
                return
            if not (await asyncio.to_thread(self.templates.resolve, WORKING_PRESET, kept.project)).changes_files:
                return
            if await self._permissions.forbidden(OPEN_BESIDE) or not await room(commission, kept):
                return
            name = await self._ledger.member_name(
                commission.lanlan_name, str(kept.project), f"{kept.project}/{model_name(WORKING_PRESET)}"
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it waits for the member as it would have
            logger.warning("looking at commission %s for another member to do it failed", commission_id, exc_info=True)
            return
        if not waits():
            # (The member came free while that was read, say: nobody is asked about what need not wait.)
            return
        goes_on = await self._goes_on_with(commission, kept)
        if goes_on:
            # It goes on with what the busy one is at, as the small model says (the words alone
            # did not tell): it waits for that one, as it would have.
            return
        if not waits():
            return
        # What the user says is waited for here (``_ask_beside``), and taken back by
        # resolving it (``_withdraw_beside``).
        asking = self._asking_beside[commission_id] = _Beside(main, asyncio.get_running_loop().create_future())
        moved: Session | None = None
        started = False
        handed: list[_Handed] = []
        try:
            asked = False
            if choice == BESIDE_ASK:
                decision = await self._permissions.check(
                    commission_id,
                    OPEN_BESIDE,
                    detail={
                        "tool": "beside",
                        "title": commission.title,
                        "preset": WORKING_PRESET,
                        # It changes files in a copy of its own, merged back when it is done.
                        "tree": True,
                        # The member that would be made, for a page to show before there is one.
                        "member": name,
                        "project": kept.project,
                        # The busy one it would work beside, and what that one is at.
                        "beside": kept.name,
                        "beside_session": kept.id,
                        "at_hand": stands.title,
                    },
                )
                if asking.withdrawn:
                    return
                if not decision.allow:
                    if decision.reason == DENIED_BY_USER:
                        # Not asked about again: it waits for the member where it stood.
                        await self._ledger.append_event(commission_id, "beside_declined", {"beside": main})
                    return
                asked = decision.asked
            if goes_on is None and not asked:
                # Not told whether it goes on with what the busy one is at, and nobody answered
                # (``open``, or allowed for good): it waits for that one, as it would have.
                return
            # Looked at once more and made with no other member made, and no leader, in
            # between (as where a commission is placed, ``Families.place``): the user may
            # have taken a while, and two of these may get here together with room for one.
            async with self.families.placing:
                if await self._ledger.lead_session(commission.lanlan_name, kept.project) is not None:
                    return
                if (await self.landing.get()).get("beside") != choice:
                    # Set otherwise since it was read (to queue, say): it waits for the member.
                    return
                holding = self._held_by.get(main)
                still = await self._ledger.get(holding) if holding is not None else None
                if still is None or still.status not in ("working", "waiting_user") or still.resting:
                    # What the busy one was at is over, or rests, by now (the user took a while): it
                    # lets the session go in a moment, and the commission starts there in its turn.
                    return
                if not await room(commission, kept):
                    return
                name = await self._ledger.member_name(
                    commission.lanlan_name, str(kept.project), f"{kept.project}/{model_name(WORKING_PRESET)}"
                )
                # Looked at last, with nothing but the making itself awaited after: forbidden since it
                # was looked at (where nobody is asked: a ``deny`` set while the user is asked is seen
                # to by the gate), or no longer waiting (a session that came free meanwhile takes it,
                # as it would have).
                if await self._permissions.forbidden(OPEN_BESIDE) or not waits():
                    return
                if not asked and self._said_meanwhile(commission_id):
                    # More was said to it since it was told apart, which goes with it and may say
                    # otherwise ("go on with what is there"): with nobody asked, it waits for the
                    # member as it would have. (Asked, the user answered knowing what they said.)
                    return
                member = await self._ledger.create_session(
                    name=name,
                    lanlan_name=commission.lanlan_name,
                    kind="line",
                    project=kept.project,
                    # Where the commission would have been worked on had it waited (``_start``): the
                    # busy member's directory, which is its project's but for a member that was a
                    # session of its own before it became the project's, and kept the one it had.
                    # Another member changes who does it and when, not where what it changes lands.
                    workdir=kept.workdir or commission.workdir,
                    preset=WORKING_PRESET,
                    duty=commission.title,
                    # A git worktree is made for each of its commissions: it does not wait for
                    # the one that works in the project's directory.
                    seat="tree",
                )
            # The model it is sent to, by its preset.
            await self.settle_model(member)
            if commission_id not in self._queue:
                # Stopped while its member was made: it is over, and is not put on record with
                # the member (which stays, with nothing to do).
                return
            await self._ledger.update(commission_id, session_id=member.id)
            # The member's on record from here on, whatever of the rest can be written.
            moved = member
            try:
                await self._ledger.append_event(
                    commission_id, "beside_opened", {"session": member.id, "beside": main, "asked": asked}
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the note is for the record; the commission is the member's
                logger.warning("noting that commission %s got a member of its own failed", commission_id, exc_info=True)
            try:
                await self._note_writer(member.id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - it is the member's all the same, and is looked at again as it starts
                logger.warning("noting where the member made for commission %s works failed", commission_id, exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it waits for the member as it would have
            logger.warning("having another member do commission %s failed; it waits", commission_id, exc_info=True)
        finally:
            self._asking_beside.pop(commission_id, None)
            if moved is not None and commission_id in self._queue:
                # What was written to the busy member and handed to this commission to take along,
                # as what that member was at (``hand_over``: what it was at is over, its session not
                # yet let go of), is the busy member's: given back below, for whatever that one is
                # at next. What the user said to the commission goes with it.
                # Loops, not comprehensions: Nuitka 4.2.2 cannot compile one in a finally whose
                # try leaves more than one way (tests/unit/test_no_comprehension_in_multi_exit_finally.py).
                rest: list[str] = []
                for note in self._notes.get(commission_id, []):
                    if isinstance(note, _Handed):
                        handed.append(note)
                    else:
                        rest.append(note)
                if handed:
                    if rest:
                        self._notes[commission_id] = rest
                    else:
                        self._notes.pop(commission_id, None)
                # The member's here as well, with nothing awaited between this and its place
                # being taken: started at once where a place is free, else next in the
                # queue, for the member. (Stopped meanwhile, it is in no queue any more.)
                self._session_of[commission_id] = moved.id
                if self._take_place(commission_id):
                    self._queue.remove(commission_id)
                    started = True
            if asking.held:
                left = self._handing_back_to.get(main, 1) - 1
                if left > 0:
                    self._handing_back_to[main] = left
                else:
                    self._handing_back_to.pop(main, None)
            if self._queue and len(self._sessions) < self.limit and not self._closed:
                self._spawn(self._start_next())
            if handed:
                # Pending again (whatever this is cancelled by: the ledger has them as handed over).
                wakes: list[int] = []
                for note in handed:
                    wakes.extend(note.wakes)
                await asyncio.shield(self._put_back(wakes))
        if started:
            await self._launch(commission_id)

    async def _goes_on_with(self, commission: Commission, kept: Session) -> bool | None:
        """Whether a commission goes on with what the busy member ``kept`` is at, as the judge says (``set_beside_judge``).

        None when that is not told: no judge, what the member is at could not
        be read, or the request failed.
        """
        from nekopower.host.ledger import UNDER_WAY_STATUSES

        judge = self._beside_judge
        if judge is None:
            return None
        try:
            at_hand = [
                " - ".join(dict.fromkeys(words for words in (item.title, item.dispatch_text) if words))
                for item in await self._ledger.select(session_id=kept.id, statuses=UNDER_WAY_STATUSES)
                if item.id != commission.id and item.parent_id is None
            ]
            said = "\n".join([commission.user_text, *self._said_meanwhile(commission.id)]).strip()
            return await judge(commission.dispatch_text, said, kept.name, at_hand)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not told
            logger.warning("telling whether commission %s goes on with what its member is at failed", commission.id, exc_info=True)
            return None

    async def _ask_beside(self, commission_id: str, ask: PermissionAsk) -> str:
        """Put to the user whether another member is to do a queued commission, and wait; the ``Asker`` for ``_beside``.

        As ``_ask`` does for a commission at work, for one that has not
        started: it is ``waiting_user`` meanwhile and ``queued`` again
        afterwards, never ``working`` (which would put it on record as
        started). While it waits it is on record with no session: it is
        at none, and the busy member it waits for is not the one that
        asks. Taken back (``_withdraw_beside``), before it is put or
        after, the answer is a refusal, which whoever asked tells from
        the user's by ``_Beside.withdrawn``. What the user chose is put
        on record for the timeline as an answer to any request for
        approval is (``tool_answered``); nothing is of what was taken back.
        """
        asking = self._asking_beside[commission_id]
        direct = _Session(commission_id)
        question = {**ask.to_json(), "id": uuid.uuid4().hex}
        direct.question, direct.at_answer, direct.question_id = asking.answer, ask.at_answer, question["id"]
        self._direct_questions[commission_id] = direct
        try:
            moved = await self._ledger.transition(
                commission_id,
                "waiting_user",
                reason="waiting for the user",
                pending_question=json.dumps(question, ensure_ascii=False),
                session_id=None,
            )
            if not moved.applied or moved.commission is None:
                raise RuntimeError(f"the commission cannot wait for the user ({moved.refusal})")
            try:
                if not asking.answer.done():
                    # (Not put to the user where it was taken back before it got here.)
                    await self._reporter.question(moved.commission, question)
                answer = await asking.answer
            finally:
                # Back among the queued only from waiting: one stopped, or failed (by
                # shutting down, say) meanwhile, stays as it is. With the session it waits
                # for: whoever asked writes the member's, should one be made. Asked again a
                # few times where the ledger cannot be written this moment; given up on, what
                # the user answered stands all the same (it is in), the commission is still
                # queued here, and is put on record as it starts (``_start``).
                try:
                    await asyncio.shield(
                        self._looked(
                            lambda: self._ledger.transition(
                                commission_id,
                                "queued",
                                reason="what was asked was taken back" if asking.withdrawn else "the user answered",
                                only_from=("waiting_user",),
                                session_id=asking.main,
                            ),
                            what="to put a commission the user was asked about back among the queued",
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - the answer stands; its record is put right as it starts
                    logger.warning(
                        "putting commission %s back among the queued failed; it is on record as waiting", commission_id,
                        exc_info=True,
                    )
            if not asking.withdrawn:
                # What the user chose, for the timeline, as of any request for approval
                # (``_ask``). Noted once the commission is back among the queued: the answer
                # puts nobody to work, starting does. Of what was taken back, nothing: the
                # user answered nothing.
                await self._note_tool_answer(commission_id, question, answer)
        finally:
            self._direct_questions.pop(commission_id, None)
        choice = answer.get("choice")
        return choice if choice in CHOICES else "deny"

    def _withdraw_beside(self, commission_id: str, why: str) -> None:
        """Take back what the user is asked about a queued commission (``_beside``), if anything still is.

        Nothing is awaited: whoever asked finds it taken back (``why``)
        once it gets to go on, and sees to the rest. Not once the user's
        answer is in: that stands.
        """
        asking = self._asking_beside.get(commission_id)
        if asking is not None and not asking.answer.done():
            asking.withdrawn = why
            asking.answer.set_result({"text": "", "choice": None})

    def _beside_came_free(self) -> None:
        """Take back what the user is asked about commissions whose session came free meanwhile (``_beside``).

        Such a commission starts in the session it waited for, in its
        turn, as it would have: nothing is on record of the question but
        that it was put and taken back. Until that is seen to, nothing
        starts in the session (``_handing_back_to``): not the commission,
        of which something is still asked, nor what waits behind it,
        which would go first. Nothing is awaited here.
        """
        for commission_id, asking in self._asking_beside.items():
            if asking.held or self._held_by.get(asking.main) is not None:
                continue
            asking.held = True
            self._handing_back_to[asking.main] = self._handing_back_to.get(asking.main, 0) + 1
            self._withdraw_beside(commission_id, "free")

    # -- copying a session ------------------------------------------------

    async def fork_session(self, source_session_id: str, *, name: str | None = None, upto: str | None = None) -> Session:
        """Make a session from another by copying what was said in it, and return it. Raises ``ForkRefused``.

        The new session (kind ``fork``) holds the other's conversation up
        to one entry of its record (``upto``; all of it when none is
        named), cut back to the last place where it is whole: the other
        may be at work, and is not disturbed. It is opened with the system
        prompt and the tool definitions the other was opened with, so a
        provider that kept the head of the other's requests has the same
        head for this one's. See ``nekopower/runtime/fork.py``.

        It is the other's in everything but what was said: the same
        character, project and working directory; a directory of its own
        for its record. Nothing is at work in it: it is ``closed``, and a
        commission whose ``session_id`` names it is worked on in it like
        in any session (``submit``), without the tools that write files
        and run commands (``_spec``). What its requests use is counted
        with it and with that commission. What was said in it is removed
        like any session's when it is old, with the session it was copied
        from when that one's is, and at once by ``SpaceKeeper.clear_session``.

        Only a session the built-in runtime holds can be copied: dsh keeps
        what was said itself and has no command for it.
        """
        if self._closed:
            raise ForkRefused("the host is shutting down")
        # From before the session is looked at until its copy is on record
        # (or given up), with no await before this: what was said in it
        # is not removed meanwhile (``hold_files``; one whose record is
        # being removed this moment is refused below). Removed in between,
        # the copy would not be found among the session's copies, which
        # go when it does, and would keep what the session no longer has.
        self._copying_from[source_session_id] = self._copying_from.get(source_session_id, 0) + 1
        registering: asyncio.Task[Session] | None = None
        try:
            registering = await self._fork(source_session_id, name=name, upto=upto)
        finally:
            if registering is None:
                # Not written, or no longer waited for.
                self._copied_from(source_session_id)
        # The copy is written. Putting it on record is a task of its own,
        # which lets go of the session it was made from when it is through.
        # Whoever asked may be cancelled meanwhile, and hears of that at
        # once; the record of that session is still not removed under a
        # copy that is on no record yet.
        return await asyncio.shield(registering)

    def _copied_from(self, source_session_id: str) -> None:
        """Let go of a session a copy was being made from (of this copy's share in it, if there are several)."""
        left = self._copying_from[source_session_id] - 1
        if left > 0:
            self._copying_from[source_session_id] = left
        else:
            del self._copying_from[source_session_id]

    async def _fork(self, source_session_id: str, *, name: str | None, upto: str | None) -> "asyncio.Task[Session]":
        """Have the runtime write the copy ``fork_session`` returns; returns the task that puts it on record.

        The session it is made from is held by the caller until this
        returns or raises, and by that task from then on.
        """
        from config.nekopower_settings import TASK_AGENT_FORK_SECONDS

        source = await self._ledger.get_session(source_session_id)
        if source is None:
            raise ForkRefused(f"there is no session {source_session_id!r}")
        if source.runtime == RUNTIME_DSH:
            raise ForkRefused("a session that runs in dsh cannot be copied: dsh has no command for it")
        if source.status == "cleared" or source_session_id in self._clearing:
            raise ForkRefused("what was said in the session is no longer kept")
        source_dir = await asyncio.to_thread(self._session_dir_of, source)
        if not await asyncio.to_thread(_in_builtin, source_dir):
            raise ForkRefused("nothing was said in the session yet")
        if self._closed:
            # Shut down while the session's files were looked at. Shutting
            # down did not see this (it was neither started nor waited
            # for yet), and is over or all but: no runtime is made for
            # it now, and nothing is copied.
            raise ForkRefused("the host is shutting down")
        session_id = new_session_id()
        target_dir = self._paths.session_dir(session_id, source.project)
        runtime = self._runtimes.builtin()
        if self._pump is None:
            self._pump = asyncio.ensure_future(self._pump_events())
        answer: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._forks[session_id] = answer
        self._fork_dirs[session_id] = str(target_dir)
        try:
            await runtime.fork(
                session_id,
                session_dir=str(target_dir),
                source_session_id=source.id,
                source_dir=str(source_dir),
                upto=upto,
            )
            event = await asyncio.wait_for(answer, TASK_AGENT_FORK_SECONDS)
        except asyncio.TimeoutError:
            # It may still write the copy: removed when it says it has.
            self._fork_given_up(session_id, target_dir, answer)
            raise ForkRefused("the runtime did not answer in time") from None
        except asyncio.CancelledError:
            # Whoever asked for the copy is gone, and the runtime may write
            # it all the same: likewise removed when it says it has.
            self._fork_given_up(session_id, target_dir, answer)
            raise
        finally:
            self._forks.pop(session_id, None)
            self._fork_dirs.pop(session_id, None)
        if event.get("type") != "forked":
            # It may have written a part of it and not got to remove that
            # (a snapshot something had open): the directory was set aside
            # for this copy, and is on no record. Removed by a task of
            # its own: whoever asked may be cancelled while it is, and
            # what cannot be removed yet would be nobody's to remove.
            await asyncio.shield(self._remove_fork_dir_soon(session_id, target_dir))
            raise ForkRefused(str(event.get("message") or "the runtime could not copy the session"))
        # With no await since the runtime's word was taken: nothing comes
        # between the copy being written and its being put on record.
        # A task that shutting down waits for and does not cancel: cut
        # off before the ledger got to it, the copy would be on the disk
        # and on no record, with nothing left that knows of it.
        registering = asyncio.ensure_future(
            self._put_fork_on_record(
                source, session_id, target_dir, name=name, forked_at=str(event.get("upto") or "") or None
            )
        )
        self._fork_records.add(registering)
        registering.add_done_callback(self._fork_records.discard)
        # What it fails with is read here, should whoever asked have left.
        registering.add_done_callback(lambda done: done.cancelled() or done.exception())
        return registering

    def _fork_given_up(self, session_id: str, target_dir: Any, answer: "asyncio.Future[dict[str, Any]]") -> None:
        """Note that a copy is no longer waited for: what the runtime writes of it is removed when it says it is through.

        It may have said so already, in the moment whoever waited left
        (the runtime in this process says it before its command
        returns). Nothing more is said of the copy then, so what there
        is of it is removed now, by a task of its own.
        """
        if answer.done() and not answer.cancelled() and answer.exception() is None:
            self._remove_fork_dir_soon(session_id, target_dir)
            return
        self._forks_given_up[session_id] = str(target_dir)

    def _remove_fork_dir_soon(self, session_id: str, target_dir: Any) -> "asyncio.Future[None]":
        """Begin to remove what was written of a copy that is on no record, in a task of its own.

        Kept with the copies that are being put on record: shutting
        down waits for these and does not cut them off (``shutdown``,
        ``forks_settled``).
        """
        removing = asyncio.ensure_future(self._remove_fork_dir(session_id, target_dir))
        self._fork_records.add(removing)
        removing.add_done_callback(self._fork_records.discard)

        def seen(removing: "asyncio.Future[None]") -> None:
            # Mostly nobody waits for it: what it ended with would go by unseen.
            if not removing.cancelled() and removing.exception() is not None:
                logger.error(
                    "removing what was copied to %s ended with an error; it may be left there, on no record",
                    target_dir,
                    exc_info=removing.exception(),
                )

        removing.add_done_callback(seen)
        return removing

    async def _put_fork_on_record(
        self, source: Session, session_id: str, target_dir: Any, *, name: str | None, forked_at: str | None
    ) -> Session:
        """Put a copy the runtime has written on record, and let go of the session it was made from.

        All of it, whatever becomes of whoever asked for the copy: the
        copy is written, and without its row nothing would find it;
        when the row cannot be written, what was written is removed
        (the runtime is through with it: it said so); and only then is
        the session it was made from let go of.
        """
        try:
            return await self._ledger.create_session(
                id=session_id,
                name=name or f"{source.name}/fork",
                lanlan_name=source.lanlan_name,
                kind="fork",
                project=source.project,
                workdir=source.workdir,
                session_dir=str(target_dir),
                runtime=RUNTIME_BUILTIN,
                forked_from=source.id,
                forked_at=forked_at,
                status="closed",
                # Sent where the session it was copied from is, or what a
                # provider kept of the conversation is not read.
                model_slot=source.model_slot,
                model_name=source.model_name,
                model_ident=source.model_ident,
                model_free=source.model_free,
                model_fallback=source.model_fallback,
                model_tier=source.model_tier,
            )
        except Exception as exc:  # noqa: BLE001 - told as a copy that was not made
            # Written, and not on record: nothing would ever come by it to remove it.
            logger.warning("a copied session could not be put on record; what was copied is removed", exc_info=True)
            await self._remove_fork_dir(session_id, target_dir)
            raise ForkRefused(f"the copy could not be put on record: {exc}") from exc
        finally:
            self._copied_from(source.id)

    async def _remove_fork_dir(self, session_id: str, target_dir: Any) -> None:
        """Remove the directory that was set aside for a copy that came to nothing; it is on no record.

        One that cannot be removed (something has it open, say) is
        still this host's to remove: with the copies nobody waited for
        any more, when it shuts down. Should the host be shutting down
        already, that may be over, and this tries again itself, a few
        times.
        """
        import os
        import shutil

        def removed() -> bool:
            shutil.rmtree(target_dir, True)
            return not os.path.lexists(str(target_dir))

        if await asyncio.to_thread(removed):
            return
        if not self._closed:
            logger.error("what was copied to %s could not be removed; tried again when the host shuts down", target_dir)
            self._forks_given_up[session_id] = str(target_dir)
            return
        for _attempt in range(_FORK_REMOVE_RETRIES):
            await asyncio.sleep(_task_agents_facade._FORK_REMOVE_RETRY_SECONDS)
            if await asyncio.to_thread(removed):
                return
        logger.error("what was copied to %s could not be removed; it is left there, on no record", target_dir)
