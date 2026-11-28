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

"""Opening prompt assembly, base instructions, space brief, repository document listing, and project filing.

Method-only mixin: every instance attribute is initialized in
``TaskAgentManager.__init__`` (``nekopower.host.task_agents.manager``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from nekopower.host.ledger import Commission, Session
from .types import logger, _REPLY_LANGUAGES, _SessionAction


class OpeningMixin:
    """Opening context assembly and project-filing methods."""

    def _base_instructions(
        self, commission: Commission, *, with_gateway: bool, template: Any = None, roster: tuple[Any, ...] = ()
    ) -> str:
        """Return what the host adds to the system prompt of the session a commission is worked on in.

        ``template`` is the preset the session is opened by and ``roster``
        the presets it can start task agents with (``_roster``): after
        what every session is told come the list of those, for a session
        that may start task agents, and then what its own preset says of
        its role.

        Nothing of the commission itself: a session takes several, and its
        system prompt is kept as it was first opened with (the runtime
        reads these again only after the conversation was summed up).
        These instructions describe how the host's capabilities are reached
        and whom the task agent works for. The current workspace brief is
        appended separately when it can be released.

        In that order, which is from what every session is told alike to
        what only this one is: the system prompts of two sessions are then
        the same up to where they differ, and a provider that kept the
        head of one session's requests has it for the other's as well.
        (The runtime puts what it says itself of one session, its working
        directory, behind all of this: ``runtime/prompt.py``.)
        """
        from config.nekopower_settings import (
            TASK_AGENT_PRESET_ROSTER_MAX_TOKENS,
            TASK_AGENT_SESSION_INSTRUCTIONS_MAX_TOKENS,
        )
        from config.prompts import prompts_nekopower_brain as texts
        from config.prompts import prompts_nekopower_presets as presets
        from nekopower.host.templates import model_name
        from utils.tokenize import count_tokens, truncate_to_tokens

        parts = [texts.TASK_AGENT_BRIEF_GATEWAY_NOTE] if with_gateway else []
        if commission.parent_id is not None:
            parts.append(texts.TASK_AGENT_CHILD_FRAME)
        else:
            parts.append(texts.TASK_AGENT_SESSION_FRAME.format(lanlan_name=commission.lanlan_name))
        if roster:
            # Within a cap of its own, whole lines only: the names and the
            # words around them are not counted in the cap on the templates'
            # descriptions, and what the session is told of its own role
            # comes after this and is not to be cut off by it. A preset
            # that is left out can still be named.
            lines = [presets.PRESET_ROSTER_INTRO]
            room = TASK_AGENT_PRESET_ROSTER_MAX_TOKENS - count_tokens(presets.PRESET_ROSTER_INTRO)
            for item in roster:
                line = presets.PRESET_ROSTER_LINE.format(name=model_name(item.name), description=item.description)
                room -= count_tokens(line) + 1
                if room < 0:
                    break
                lines.append(line)
            if len(lines) > 1:
                # (With no room for even one preset, nothing is said of them.)
                parts.append("\n".join(lines))
        if template is not None and template.body:
            parts.append(template.body)
        return truncate_to_tokens("\n\n".join(parts), TASK_AGENT_SESSION_INSTRUCTIONS_MAX_TOKENS)

    async def _roster(self, commission: Commission, kept: Session, template: Any) -> tuple[Any, ...]:
        """Return the presets a session is told it can start task agents with; none for one that starts none.

        By what the session is, not by the tools this one start leaves it:
        a copy is told what the session it was copied from is told. A list
        that cannot be read leaves the session without one: it starts
        workers, as before there were presets.
        """
        if commission.parent_id is not None or not template.delegates:
            return ()
        try:
            return tuple(await asyncio.to_thread(self.templates.roster, kept.project))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it starts all the same
            logger.warning("listing the presets for a session failed", exc_info=True)
            return ()

    def note_filed(self, commission: Commission) -> None:
        """Keep what she and the user said just before a commission that was filed this moment.

        For the task agent that may take it. Who does it is chosen
        next, which takes a model request, and what is said meanwhile
        pushes what was said before out of what is kept of the main
        dialogue. Dropped again when no task agent takes the
        commission (``drop_excerpt``). Nothing having been said is kept
        as that: a message that is told of late, while it is chosen who
        does the commission, is not read as said before it after all.
        """
        if commission.id not in self._excerpts:
            self._excerpts[commission.id] = self._said_before(commission, upto=commission.created_at)

    def drop_excerpt(self, commission_id: str) -> None:
        """Drop what ``note_filed`` kept for a commission that no task agent takes."""
        self._excerpts.pop(commission_id, None)

    def _keep_excerpt(self, commission: Commission, *, upto: float | None = None) -> None:
        """Keep what she and the user said just before a commission, until its session is opened.

        Her own commissions only: a task agent that another one started is
        told its part by that one. Taken now, when the commission is handed
        over, and not when its session is opened: it may wait for a place,
        and what is said meanwhile is not what it refers to. Kept in memory;
        no model is asked and nothing is read from anywhere else.
        """
        if commission.id in self._excerpts:
            return
        turns = self._said_before(commission, upto=upto)
        if turns:
            self._excerpts[commission.id] = turns

    def _said_before(self, commission: Commission, *, upto: float | None = None) -> list[Any]:
        """Return what she and the user said in the main dialogue just before now, or before ``upto``; empty for none.

        For her own commissions only (see ``_keep_excerpt``): not for one
        filed for what another session wrote, which nobody dispatched and
        which what she and the user said has nothing to do with.
        """
        if self._conversation is None or commission.parent_id is not None or commission.source == "session":
            return []
        try:
            turns = self._conversation.recent(commission.lanlan_name)
        except Exception:  # noqa: BLE001 - it starts without an excerpt
            logger.warning("reading what was said before a commission failed", exc_info=True)
            return []
        if upto is not None:
            # What was said after the sentence was dispatched (while it was
            # being routed, say) is not what it refers to.
            turns = [turn for turn in turns if turn.at <= upto]
        return list(turns)

    def _times_dropped(self, commission: Commission) -> int:
        """Return how many times what is kept of the main dialogue for a commission's character has been dropped."""
        return self._conversation.times_dropped(commission.lanlan_name) if self._conversation is not None else 0

    def _excerpt(self, commission: Commission) -> str:
        """Return the part of a commission's opening message that holds what was said just before it; empty for none.

        Word for word, the newest messages first to be kept, within a cap
        of its own (``TASK_AGENT_EXCERPT_MAX_TOKENS``; the lines that say
        what it is come on top). Wrapped as background that gives no
        instructions: what she said in it she said to the user.
        """
        from config.nekopower_settings import TASK_AGENT_EXCERPT_MAX_TOKENS, TASK_AGENT_EXCERPT_MESSAGE_MAX_TOKENS
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.conversation import excerpt_lines

        # (Nothing is kept for a task agent another one started: ``_keep_excerpt``.)
        turns = self._excerpts.get(commission.id)
        if turns:
            # Taken when the commission was filed or handed over, which
            # may be before what is kept of the main dialogue was last
            # dropped (NekoPower went off): what was said before that
            # is not handed to a task agent after it, wherever it was
            # kept meanwhile. (By how many times it had been dropped when
            # each was kept, and not by when each was said: a clock
            # may be set back, and says the same time twice.)
            now_kept_in = self._times_dropped(commission)
            turns = [turn for turn in turns if turn.kept_in == now_kept_in]
        if not turns:
            return ""
        # With the cap at nothing no line fits, and nothing is handed over.
        lines = excerpt_lines(
            turns,
            lanlan_name=commission.lanlan_name,
            max_tokens=TASK_AGENT_EXCERPT_MAX_TOKENS,
            message_max_tokens=TASK_AGENT_EXCERPT_MESSAGE_MAX_TOKENS,
            # Stated by the commission itself, right above.
            # As the opening message states the user's words.
            already_told=commission.user_text or commission.dispatch_text,
        )
        if not lines:
            return ""
        return "\n".join(
            [
                texts.TASK_AGENT_COMMISSION_EXCERPT_INTRO.format(lanlan_name=commission.lanlan_name),
                texts.TASK_AGENT_COMMISSION_EXCERPT_OPEN,
                *lines,
                texts.TASK_AGENT_COMMISSION_EXCERPT_CLOSE,
            ]
        )

    async def _opening(
        self,
        commission: Commission,
        *,
        cleared: bool,
        back: bool = False,
        excerpt: str = "",
        documents: str = "",
        copies: str = "",
    ) -> str:
        """Return the message that opens a commission in its session.

        ``copies`` is what it says of copies of the session (``_copies_said``),
        put together once for every wording of one start.

        What was dispatched and what the user said; which earlier commission
        it follows up, if one; how the user wants such things done; the
        entries the task brain picked for it, which are called by name
        through the gateway's call tool (they are not among the session's
        tools, which stay the same from one commission to the next); the
        language to answer in. ``cleared`` says that what was said in the
        session before was removed since: a commission that follows another
        up is then told what came of that one. ``back`` says that the
        commission was opened in this session before and that the session
        worked on others since. ``excerpt`` is what was said in the main
        dialogue just before the commission (``_excerpt``), for a session
        that holds nothing to tell what the commission refers to.
        ``documents`` is the list of the files the repository it works in
        keeps for agents (``_repository_documents``), empty for none: said
        with every commission, since what was said in the session before
        is summed up in time and the message that opens the commission
        under way is what is kept.

        What a task agent hands to one it starts is not cut: it is all that
        one is told of its part, and the task agent that wrote it is told
        that the other was started. (What she dispatches is short by the
        time it is filed, ``DISPATCH_TEXT_MAX_TOKENS``.)
        """
        from config.nekopower_settings import TASK_AGENT_BRIEF_MAX_TOKENS, TASK_AGENT_CONTINUED_RESULT_MAX_TOKENS
        from config.prompts import prompts_nekopower_brain as texts
        from config.prompts.prompts_nekopower import REPLY_LANGUAGE_SENTENCE
        from utils.tokenize import truncate_to_tokens

        whole = ""
        if commission.source == "ask":
            # A question another session asked, framed: not cut either.
            whole = await self._opening_of_question(commission)
            parts = []
        elif commission.parent_id is not None:
            whole = texts.TASK_AGENT_CHILD_COMMISSION_TEMPLATE.format(
                description=commission.title, dispatch_text=commission.dispatch_text
            )
            parts = []
        elif commission.source == "session":
            # Filed for what another session wrote: that, framed, is what
            # the commission is. Not cut either: a frame cut short would
            # leave what follows it looking like part of the note.
            whole = await self._opening_of_notes(commission)
            parts = []
        else:
            parts = [
                texts.TASK_AGENT_COMMISSION_TEMPLATE.format(
                    commission_id=commission.id,
                    dispatch_text=commission.dispatch_text,
                    user_text=commission.user_text or commission.dispatch_text,
                )
            ]
        if back:
            parts.append(texts.TASK_AGENT_COMMISSION_BACK)
        earlier = await self._ledger.get(commission.continues) if commission.continues else None
        in_copy = ""
        if earlier is not None:
            if cleared:
                result = truncate_to_tokens(
                    earlier.result_summary or earlier.status_reason or earlier.status,
                    TASK_AGENT_CONTINUED_RESULT_MAX_TOKENS,
                )
                parts.append(texts.TASK_AGENT_COMMISSION_CONTINUES_CLEARED.format(title=earlier.title, result=result))
            elif commission.session_id and earlier.session_id and await self._not_above(earlier, commission.session_id):
                # Worked on in a copy of this session, or of the session this
                # is a copy of (it was followed up there, and this went to
                # the session itself): nothing of it is above. Said past the
                # cap below, whole: it is put on record as told
                # (``_copies_said``), and is within a cap of its own.
                result = truncate_to_tokens(
                    earlier.result_summary or earlier.status_reason or earlier.status,
                    TASK_AGENT_CONTINUED_RESULT_MAX_TOKENS,
                )
                in_copy = texts.TASK_AGENT_COMMISSION_CONTINUES_IN_COPY.format(title=earlier.title, result=result)
            else:
                parts.append(texts.TASK_AGENT_COMMISSION_CONTINUES.format(title=earlier.title))
        preferences = await self._preferences.render(f"{commission.dispatch_text}\n{commission.user_text}")
        if preferences:
            parts.append(f"{texts.TASK_AGENT_BRIEF_PREFERENCES_HEADER}\n{preferences}")
        capped = truncate_to_tokens("\n\n".join(parts), TASK_AGENT_BRIEF_MAX_TOKENS) if parts else ""
        opening = "\n\n".join(part for part in (whole, capped) if part)
        # After the cap above, each within a cap of its own.
        if in_copy:
            opening += "\n\n" + in_copy
        if copies:
            opening += "\n\n" + copies
        if excerpt:
            opening += "\n\n" + excerpt
        picked = await self._picked_entries(commission)
        if picked:
            opening += "\n\n" + picked
        if documents:
            opening += "\n\n" + documents
        language = _REPLY_LANGUAGES.get(commission.lang or "", None)
        if language:
            # Said here, with every commission, and nowhere else: a session's
            # commissions need not share a language, and its system prompt
            # stays as it was opened until it is summed up. The built-in
            # runtime keeps this message through a summary as it is
            # (``opening=True``); dsh sums up on its own. The language
            # is the one of her dialogue when it was dispatched, which is also
            # what the host writes in to her and on the HUD.
            opening += "\n\n" + REPLY_LANGUAGE_SENTENCE.replace("<<LANGUAGE>>", language)
        return opening

    async def _not_above(self, earlier: Commission, session_id: str) -> bool:
        """Whether a commission was worked on in another copy of the session a commission is opened in, and is not in its conversation.

        A copy of a project's session that carried it on, where the session
        is that project's session or another copy of it. Not above where
        neither took its opening message: a copy holds what the session
        itself held when it was made.
        """
        if earlier.session_id == session_id:
            return False
        was_in = await self._ledger.get_session(str(earlier.session_id))
        here = await self._ledger.get_session(session_id)
        if was_in is None or here is None or not was_in.forked_from:
            return False
        home = [session_id, *([str(here.forked_from)] if here.kind == "fork" and here.forked_from else [])]
        if was_in.forked_from not in home:
            return False
        for place in home:
            if await self._ledger.opened_in(earlier.id, place) is not None:
                return False
        return True

    async def _opening_of_question(self, commission: Commission) -> str:
        """Return what opens the commission in which a copy of a session answers a question another session asked it.

        The question is what the commission was filed with; who asked is
        the session of the commission it was filed under.
        """
        import secrets

        from config.nekopower_settings import SESSION_ASK_MAX_REQUESTS
        from config.prompts import prompts_nekopower_sessions as words

        asker = await self._ledger.get(commission.parent_id) if commission.parent_id else None
        place = await self._ledger.get_session(asker.session_id) if asker is not None and asker.session_id else None
        copy = await self._ledger.get_session(commission.session_id) if commission.session_id else None
        lanlan = place.lanlan_name if place is not None else commission.lanlan_name
        whose = (
            words.SESSION_WHOSE if copy is None or copy.lanlan_name == lanlan else words.SESSION_WHOSE_OTHER
        ).format(lanlan_name=lanlan)
        nonce = secrets.token_hex(8)
        intro = words.ASK_OPENING_FRAME.format(
            asker=place.name if place is not None else commission.parent_id,
            whose=whose,
            requests=SESSION_ASK_MAX_REQUESTS,
            nonce=nonce,
        )
        return words.frame(intro, commission.dispatch_text, nonce)

    async def _opening_of_notes(self, commission: Commission) -> str:
        """Return what opens a commission that was filed for notes from other sessions: what it is, and the notes, framed.

        Put together anew whenever the commission is opened (each time
        with frames of their own): the notes are on record with the
        commission they were handed to.
        """
        from config.prompts import prompts_nekopower_sessions as words

        head = words.SESSION_COMMISSION_TEMPLATE.format(commission_id=commission.id, lanlan_name=commission.lanlan_name)
        notes = await self._ledger.messages_opening(commission.id)
        if not notes and await self._ledger.opening_handed_on(commission.id):
            # It never took them (its opening message was refused, say), and
            # they were handed to another commission since, or dropped: they
            # are not said a second time here, from the record or from what
            # was filed.
            return f"{head}\n\n{words.SESSION_COMMISSION_NOTES_GONE}"
        if not notes:
            # Its notes are gone from the record; what was filed says what they were.
            return f"{head}\n\n{commission.dispatch_text}"
        framed = [await self._framed_note(note, commission.lanlan_name, answerable=True) for note in notes]
        return "\n\n".join([head, *framed])

    async def _picked_entries(self, commission: Commission) -> str:
        """Return the part of a commission's opening message that tells of the entries picked for it; empty for none."""
        from config.prompts import prompts_nekopower_brain as texts

        if self._gateway is None:
            return ""
        try:
            definitions = await self._gateway.candidate_definitions(commission.id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it starts all the same; the search tool finds them
            logger.warning("listing the entries picked for a commission failed", exc_info=True)
            return ""
        if not definitions:
            return ""
        lines = [json.dumps(definition, ensure_ascii=False) for definition in definitions]
        return "\n".join([texts.TASK_AGENT_COMMISSION_CANDIDATES_HEADER, *lines])

    async def _space_brief(
        self, commission: Commission, receiving: Callable[[], bool] = lambda: True, *, rules_only: bool = False
    ) -> str | None:
        """Return workspace context, an empty string for an empty space, or None if unavailable.

        A space that cannot be read does not keep a task agent from starting.
        ``rules_only``: the user's rules without the indexes (``TaskAgentSpace.brief``).
        """
        if self._space is None:
            return ""
        async def read(action: _SessionAction) -> str:
            action.record.scope_ref = commission.session_id
            return await self._space.brief(commission.project, rules_only=rules_only)

        for attempt in range(2):
            if not receiving():
                break
            run = self._permissions.current_run(commission.id)
            try:
                return await self._perform_action(commission.id, "space_brief", read, lambda: run() and receiving())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # An unavailable brief does not stop a commission.
                if attempt == 0 and not run():
                    continue  # A peer note rotated the old loan. Read afresh once.
                logger.warning("reading the task agent space failed (%s)", type(exc).__name__)
                break
        return None

    async def _repository_documents(self, workdir: str, session_dir: str | None = None) -> str:
        """Return the list of the files the repository a task agent works in keeps for agents; empty when it has none.

        A list and nothing more: names and sizes, with a sentence that says
        they are reference material and not instructions (``repo_docs``).
        The host reads none of them. Looked for in a worker thread, since
        it reads the file system; a folder that cannot be looked at does
        not keep a task agent from starting.

        In a directory of the host's own (the one a commission is given
        that names none) only that directory is looked at, not what lies
        above it: see ``repo_docs.listing``. Such a directory is below
        the folder the host keeps its data in, or among the sessions
        where this commission's session is kept (``session_dir``): the
        folder for the data may have been changed since the commission
        was first taken up, and its session and working directory stayed
        where they were (``_session_dir_of``). A task agent started by
        another works inside that one's directory, among the same
        sessions.

        Waited for no longer than ``REPO_DOCUMENTS_SCAN_SECONDS``: a folder
        on a network share that does not answer would keep the commission
        from starting, at work by its status and with nothing done. Past
        that the task agent starts without the list. The look itself
        cannot be stopped. It is made in a thread of its own
        (``run_detached``), which the host does not wait for when it
        exits and which is none of the threads its other file work is
        done in; and no more than ``REPO_DOCUMENTS_SCANS_AT_ONCE`` are
        under way: with that many out, the next task agent starts
        without the list at once.
        """
        from pathlib import Path

        from config.nekopower_settings import REPO_DOCUMENTS_SCAN_SECONDS, REPO_DOCUMENTS_SCANS_AT_ONCE
        from nekopower.host import repo_docs
        from nekopower.host.detached import NoThread, run_detached
        from nekopower.runtime.permissions import is_inside

        def look() -> str:
            places = [self._paths.root()]
            if session_dir:
                # ``.../sessions/<commission>``: the sessions of its project.
                places.append(Path(session_dir).parent)
            own = False
            for place in places:
                try:
                    own = own or is_inside(Path(workdir), place)
                except (OSError, ValueError):
                    continue
            return repo_docs.listing(workdir, upward=not own)

        if self._document_scans >= REPO_DOCUMENTS_SCANS_AT_ONCE:
            logger.warning("looks for the files a repository keeps for agents have not come back; none is started")
            return ""

        def back(done: asyncio.Future[str]) -> None:
            self._document_scans -= 1
            if not done.cancelled() and done.exception() is not None:
                # Told here when nobody waits for it any more.
                logger.warning(
                    "listing the files a repository keeps for agents failed (%s)", type(done.exception()).__name__
                )

        self._document_scans += 1
        try:
            scan = run_detached(look, name="nekopower-repo-documents")
        except NoThread:
            # No thread could be started for it: nothing is under way.
            self._document_scans -= 1
            logger.warning("no thread could be started to list the files a repository keeps for agents")
            return ""
        scan.add_done_callback(back)
        try:
            # Shielded: the look goes on in its thread whoever stops waiting
            # for it, and is counted until it comes back.
            return await asyncio.wait_for(asyncio.shield(scan), REPO_DOCUMENTS_SCAN_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("listing the files a repository keeps for agents took too long; it starts without the list")
            return ""
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it starts without the list
            return ""

    async def _use_project(self, commission: Commission) -> None:
        """Tell the space that a commission of a project is being taken up; see ``TaskAgentSpace.use_project``.

        Before its instructions are put together and long before the ledger
        says it is working: a project that just now looked idle is not
        archived under it.
        """
        if self._space is None or not commission.project:
            return
        try:
            await self._space.use_project(commission.project)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it starts all the same
            logger.warning("noting that a project is in use failed", exc_info=True)

    async def _file_under_project(self, commission: Commission) -> Commission:
        """File a commission that starts for the first time under the project its words name.

        Only her own commissions, and only before anything of theirs is on
        the disk: where the session is kept follows from the project. With
        the project comes its working directory, where its files are. No
        model is asked; see ``TaskAgentSpace.assign_project``.
        """
        if (
            self._space is None
            or commission.parent_id is not None
            or commission.project is not None
            or commission.session_dir
            or commission.started_at is not None
        ):
            return commission
        try:
            project = await self._space.assign_project(commission)
            if project is None:
                return commission
            more: dict[str, Any] = {}
            if not commission.workdir and project.workdir:
                from pathlib import Path

                if await asyncio.to_thread(Path(project.workdir).is_dir):
                    more["workdir"] = project.workdir
            # The project and the note of it together (``Ledger.file_under_project``).
            return await self._ledger.file_under_project(commission.id, project.name, **more) or commission
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it is one of the commissions that belong to no project
            logger.warning("filing a commission under a project failed", exc_info=True)
            return commission
