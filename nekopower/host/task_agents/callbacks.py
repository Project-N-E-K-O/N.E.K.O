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

"""Host callback handlers, permission gating, user questions, reports, lessons, and web search.

Method-only mixin: every instance attribute is initialized in
``TaskAgentManager.__init__`` (``nekopower.host.task_agents.manager``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

from nekopower.host.ledger import Commission
from nekopower.host.permissions import (
    CHOICES,
    DENIED_BY_SETTINGS,
    GrantScope,
    SPACE_COMMAND,
    PermissionAsk,
    PermissionDecision,
)
from nekopower.host.plugin_runs import audited_run_entry
from nekopower.host.preferences import feedback_text
from nekopower.host.runtimes import RUNTIME_BUILTIN, RUNTIME_DSH
from .types import (
    logger,
    WEB_SEARCH_PLUGIN_ID,
    WEB_SEARCH_ENTRY_ID,
    _WEB_SEARCH_PARALLEL,
    _ADDRESSED_KINDS,
    _search_queries,
    _SessionAction,
    _Session,
    _may_be_a_record,
    answer_words,
    build_answers,
)


class CallbacksMixin:
    """Permission, question, report, lesson, and web-search callback methods."""

    async def _ask(
        self, commission_id: str, question: dict[str, Any], *, at_answer: Callable[[], None] | None = None,
        direct: _Session | None = None,
    ) -> dict[str, Any]:
        """Put a question to the user and wait for the answer.

        The commission is ``waiting_user`` meanwhile and ``working`` again
        afterwards. ``at_answer`` is done the moment the answer to this
        question is in (``answer``), with nothing awaited before: this
        gets to go on only a while later.
        """
        session = direct or self._sessions.get(commission_id)
        if session is None:
            raise RuntimeError("the commission has no session to ask for")
        async with session.ask_lock:
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            # Which question this is, for whoever reads a sentence as
            # its answer: by the time the answer is handed in, another
            # question may wait in its place (``answer``).
            question = {**question, "id": uuid.uuid4().hex}
            moved = await self._ledger.transition(
                commission_id,
                "waiting_user",
                reason="waiting for the user",
                # Only one at work asks so: a queued one is asked about by ``_ask_beside`` alone
                # (a session on its way out, whose commission was queued again meanwhile, is
                # refused as it was before a queued commission could wait for the user at all).
                only_from=("working",),
                pending_question=json.dumps(question, ensure_ascii=False),
            )
            if not moved.applied or moved.commission is None:
                raise RuntimeError(f"the commission cannot wait for the user ({moved.refusal})")
            session.question, session.at_answer, session.question_id = future, at_answer, question["id"]
            try:
                await self._reporter.question(moved.commission, question)
                answer = await future
                await self._note_tool_answer(commission_id, question, answer)
                return answer
            finally:
                session.question, session.at_answer = None, None
                if session.question_id == question["id"]:
                    # (Not what a report asked meanwhile, should there be one.)
                    session.question_id = ""
                # Back to working only from waiting: a commission stopped, or
                # failed (by shutting down, say) meanwhile, stays as it is.
                await asyncio.shield(
                    self._ledger.transition(
                        commission_id, "working", reason="the user answered", only_from=("waiting_user",)
                    )
                )

    async def _note_tool_answer(self, commission_id: str, question: dict[str, Any], answer: dict[str, Any]) -> None:
        """Put on record that the user answered what a tool asked (``tool_answered``), for the timeline.

        The words of the answer to a question put with a tool; of a
        request for approval, or for going on past the usage limit, the
        choice and nothing else. The answer is the tool's all the same
        when this cannot be written.
        """
        kind = str(question.get("kind") or "")
        choice = answer.get("choice")
        payload: dict[str, Any] = {"question_kind": kind, "choice": choice if isinstance(choice, str) else ""}
        if kind == "questions":
            # With what was picked with a button, where the answer came so.
            payload["text"] = answer_words(answer) if answer.get("selected") else str(answer.get("text") or "")
        try:
            await self._ledger.append_event(commission_id, "tool_answered", payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it is for the timeline only
            logger.warning("noting that the user answered failed", exc_info=True)

    async def ask_user(self, commission_id: str, question: dict[str, Any]) -> dict[str, Any]:
        """Put a question to the user for a commission and wait; returns ``{"text", "choice"}``.

        For the gateway: a plugin that wants the user's confirmation is not
        answered by the task agent that called it.
        """
        return await self._ask(commission_id, question)

    async def ask_permission(self, commission_id: str, ask: PermissionAsk) -> str:
        """Ask the user to approve something; the ``Asker`` of the permission gate."""
        direct = None
        if commission_id in self._asking_beside:
            return await self._ask_beside(commission_id, ask)
        if commission_id not in self._sessions:
            commission = await self._ledger.get(commission_id)
            if commission is None or commission.route != "plugin" or commission.status != "working":
                raise RuntimeError("the commission has no session to ask for")
            direct = _Session(commission_id)
            if commission_id in self._direct_questions:
                return "deny"
            self._direct_questions[commission_id] = direct
        try:
            answer = await self._ask(commission_id, ask.to_json(), at_answer=ask.at_answer, direct=direct)
        finally:
            if direct is not None:
                self._direct_questions.pop(commission_id, None)
        choice = answer.get("choice")
        # Anything that is not one of the four answers is a refusal. An
        # approval wider than the question offered is narrowed by the gate.
        return choice if choice in CHOICES else "deny"

    # -- host callbacks ---------------------------------------------------

    def _at_work_in(self, session_id: str) -> str:
        """Return the commission a session is at work on.

        A runtime names the session a callback comes from; permissions,
        questions, reports and everything else the host keeps are a
        commission's. A session no commission holds here is taken by its
        own id, which is that of the commission it was made for, as every
        session's was: what such a callback asks is decided as it always
        was (by the settings alone, there being no session to ask through).
        """
        return self._held_by.get(session_id, session_id)

    async def _cb_native_execution_action(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._cb_execution_action(session_id, payload, native=True)

    async def _cb_execution_action(
        self, session_id: str, payload: dict[str, Any], *, native: bool = False
    ) -> dict[str, Any]:
        from nekopower.runtime.execution import EXECUTION_TOOLS, JOB_TOOLS

        commission = self._held_by.get(session_id)
        session = self._sessions.get(commission) if commission else None
        if session is None or session.session_id != session_id or session.closing:
            return {"allow": False}
        kind = "dsh_native" if native else "runtime"
        if native and session.runtime_name != RUNTIME_DSH:
            return {"allow": False}
        if payload.get("phase") == "release" and native:
            session.job_sources.pop(payload.get("receipt"), None)
            return {"allow": False}
        if payload.get("phase") == "job_notice_live" and not native:
            source = session.job_sources.get(payload.get("receipt"))
            return {"allow": bool(source and source[0]())}
        if payload.get("phase") == "job_notice" and not native:
            batch = "receipts" in payload
            receipts = payload["receipts"] if batch else [payload.get("receipt")]
            if not isinstance(receipts, list) or not all(isinstance(r, str) for r in receipts):
                return {"allow": False}
            checked = {}
            for receipt in dict.fromkeys(receipts):
                source = session.job_sources.get(receipt)
                if source and source[0]() and await source[1]():
                    checked[receipt] = source
            # A later transfer may have waited on the ledger while an earlier
            # source was revoked. Check the whole batch without yielding.
            allowed = [r for r, source in checked.items() if session.job_sources.get(r) is source and source[0]()]
            return {"origins": allowed} if batch else {"allow": bool(allowed)}
        if payload.get("phase") == "check":
            return {"allow": self._execution_audit.active(commission, session_id, payload.get("receipt"), kind=kind)}
        if payload.get("phase") == "finish":
            receipt = payload.get("receipt", "")
            try:
                outcome = await self._execution_audit.finish(
                    commission, session_id, receipt, payload.get("status", ""), kind=kind
                )
            except (asyncio.CancelledError, Exception):
                session.job_sources.pop(receipt, None)
                raise
            if outcome is None:
                return {"allow": False}
            if payload.get("started_job") is True:
                keep = outcome.command_completed
            else:
                keep = outcome.delivered and payload.get("retain_source") is True
            if not keep:
                session.job_sources.pop(payload.get("receipt"), None)
            return {"allow": outcome.delivered}
        tool = payload.get("tool")
        if payload.get("phase") == "native_failure":
            from nekopower.dsh.profile import GATED_TOOLS
            from nekopower.host import call_audit

            if not native or tool not in (*GATED_TOOLS, *JOB_TOOLS):
                return {"allow": False}
            async with call_audit.call(self._ledger, commission, "dsh_native", tool) as record:
                record.status = "failed"
                record.reason = "native_failure"
            return {"allow": False}
        if payload.get("phase") != "begin" or tool not in EXECUTION_TOOLS:
            return {"allow": False}
        category = payload.get("category")
        if category not in {"workdir_files", "outside_read", "outside_write", "run_command", "other"}:
            return {"allow": False}
        if not native and session.runtime_name != RUNTIME_BUILTIN and tool != "web_fetch":
            return {"allow": False}
        scope_ref = payload.get("scope_ref")
        if not isinstance(scope_ref, str) or len(scope_ref) != 64 or any(c not in "0123456789abcdef" for c in scope_ref):
            return {"allow": False}

        import hashlib
        from urllib.parse import urlsplit
        from nekopower.host import call_audit

        # Only the host knows which model this live runtime was actually opened with.
        model = session.model
        if not model or not model[1]:
            return {"allow": False}
        domain = urlsplit(str(model[0] or "")).hostname
        label = f"{model[1]} ({domain})" if domain else str(model[1])
        recipient = "model:" + hashlib.sha256(json.dumps(model).encode()).hexdigest()
        current = self._permissions.current_run(commission)
        effective_category = category
        if (category == "outside_read" and self._space is not None and isinstance(payload.get("path"), str)
                and await self._space.contains(payload["path"])):
            effective_category = "workdir_files"
        delivery_rule = self._permissions.current_rules(commission, "managed_delivery")
        action_rule = self._permissions.current_rules(commission, effective_category)

        def bind_action_rule(category):
            # Path and session ownership checks may yield before the gate
            # decides which rule actually governs this action.
            nonlocal action_rule
            action_rule = self._permissions.current_rules(commission, category)

        grants = []
        origins = payload.get("origins", [])
        if not isinstance(origins, list) or not all(isinstance(ref, str) for ref in origins):
            return {"allow": False}
        if tool == "job_list":
            origins = [ref for ref in origins if ref in session.job_sources and session.job_sources[ref][0]()]
        if any(ref not in session.job_sources for ref in origins):
            return {"allow": False}
        sources = [session.job_sources[ref][0] for ref in origins]

        def source_live() -> bool:
            return (delivery_rule() and action_rule() and self._held_by.get(session_id) == commission
                    and self._sessions.get(commission) is session and not session.closing and session.model == model
                    and all(self._permissions.grant_active(grant) for grant in grants) and all(check() for check in sources))

        def live() -> bool:
            return current() and source_live()

        async def permit(record):
            record.scope_ref = scope_ref
            if native and payload.get("private_route") is not True and category != "workdir_files":
                # A customized native recipe can add observers outside the stock return path.
                return {"allow": False, "reason": DENIED_BY_SETTINGS}
            if await self._permissions.forbidden("managed_delivery"):
                return {"allow": False, "reason": DENIED_BY_SETTINGS}
            if sources and tool in {"read", "grep"} and category == "workdir_files":
                answer = {"allow": source_live() and not await self._permissions.forbidden(category)}
            elif tool in JOB_TOOLS or (kind == "runtime" and tool == "web_fetch"):
                if category != "other":
                    return {"allow": False}
                decision = await self._permissions.check(
                    commission, category, detail={"tool": tool, "content_to": label},
                    scope=GrantScope("dsh-stock" if native else "builtin-runtime", f"{tool}:{scope_ref}", recipient),
                )
                if decision.grant_id:
                    grants.append(decision.grant_id)
                record.grant_id = self._permissions.authorization_ref(decision.grant_id)
                answer = decision.to_json()
            else:
                answer = await self._cb_permission(
                    session_id, payload, audit=record, recipient=recipient, label=label,
                    grants=grants, before_check=bind_action_rule,
                )
            if answer.get("allow"):
                from contextvars import copy_context

                parent = copy_context()

                def available():
                    return record.closed and record.status in {"completed", "failed"} and source_live()

                async def notice():
                    return await asyncio.create_task(deliver(record, available), context=parent.copy())

                session.job_sources[record.call_id] = (available, notice)
            return answer

        async def deliver(record, eligible=live):
            async with call_audit.call(self._ledger, commission, "transfer_content", recipient) as transfer:
                transfer.scope_ref = scope_ref
                transfer.grant_id = record.grant_id
                if (not eligible() or await self._permissions.forbidden(effective_category)
                        or await self._permissions.forbidden("managed_delivery") or not eligible()):
                    transfer.status = "denied"
                    transfer.reason = "delivery_expired"
                    return False
            return transfer.closed and eligible()

        answer = await self._execution_audit.begin(commission, session_id, kind, tool, permit, deliver, live)
        if tool == "job_list" and answer.get("allow"):
            answer["origins"] = origins
        return answer

    async def _cb_permission(
        self, session_id: str, payload: dict[str, Any], *, audit=None, recipient="", label="", grants=None, before_check=None,
    ) -> dict[str, Any]:
        """Rule on what a session asks to do (``_rule_on_permission``), and hold the answer while a landing is under way where it writes.

        What it is allowed may change a file of a directory that another
        task agent's changes are being merged into this moment: the user
        may have answered while that merge runs. It is told once the
        merge is through (``_pause_for_landing``), as it is held before
        its next model request.
        """
        ruled = await self._rule_on_permission(
            session_id, payload, audit=audit, recipient=recipient, label=label, grants=grants, before_check=before_check
        )
        await self._pause_for_landing(session_id)
        return ruled

    async def _rule_on_permission(
        self, session_id: str, payload: dict[str, Any], *, audit=None, recipient="", label="", grants=None, before_check=None,
    ) -> dict[str, Any]:
        session_id = self._at_work_in(session_id)
        detail = {key: payload.get(key) for key in ("tool", "path", "command", "cwd") if payload.get(key)}
        if label:
            detail["content_to"] = label
        from nekopower.runtime.permissions import GATEWAY_CALL_CATEGORY, HOST_TOOL_CALL_CATEGORY, WRITE_TOOL_NAMES

        category = str(payload.get("category") or "other")
        if category == GATEWAY_CALL_CATEGORY:
            return await self._rule_on_gateway_call(session_id, payload)
        if category == HOST_TOOL_CALL_CATEGORY:
            return await self._rule_on_host_tool(session_id, payload)
        path = payload.get("path")
        writes = payload.get("tool") in WRITE_TOOL_NAMES
        # A path outside the working directory, or one a tool is about to
        # write: the space may lie inside a working directory that is one of
        # its parents, and writing it is then a write "inside the working
        # directory" as far as the category goes.
        if (
            self._space is not None
            and (category in ("outside_read", "outside_write") or (writes and category == "workdir_files"))
            and isinstance(path, str)
            and path
            and await self._space.contains(path)
        ):
            if writes or category == "outside_write":
                # Only the host writes the space, whatever the settings allow
                # and wherever the working directory is.
                from config.prompts.prompts_nekopower_space import SPACE_WRITE_REFUSED

                return {"allow": False, "reason": SPACE_WRITE_REFUSED}
            # What was kept for task agents is read like the files a task
            # agent works on: nobody is asked.
            category = "workdir_files"
        if isinstance(path, str) and path and (writes or category == "outside_write"):
            # A task agent in a git worktree writes there and not in its
            # project's directory, and nobody writes in the worktree of another.
            ruled = await self._rule_on_tree_write(session_id, path)
            if ruled is not None:
                return ruled
        if (
            isinstance(path, str)
            and path
            and (
                category in ("outside_read", "outside_write")
                or (writes and category == "workdir_files")
                # A file among those the commission works on that has a
                # second name may be another session's record under that
                # name. (A link that leads elsewhere counts as outside the
                # working directory already.) So may a search (it reads
                # what is in a directory), and a file named like one a
                # session keeps of itself, by its own name or by what it
                # leads to: another session's own directory may lie in the
                # working directory, and its record is not read by way of
                # that either. Only these are looked at: the others are
                # read as ever, with nothing asked.
                or (
                    category == "workdir_files"
                    and (payload.get("tool") == "grep" or await asyncio.to_thread(_may_be_a_record, path))
                )
            )
        ):
            # What another session left is read without asking, and its own
            # directory is never written (``_rule_on_other_session``): not
            # from a working directory that contains it either, where the
            # write counts as one "inside the working directory".
            ruled = await self._rule_on_other_session(
                session_id,
                path,
                writes=writes or category == "outside_write",
                # ``glob`` tells names; every other tool that takes a
                # directory reads the files in it.
                names_only=payload.get("tool") == "glob",
                # Among the files the commission works on. Other sessions may
                # work in the same folder: that makes nothing in it another
                # session's, and no setting about reading elsewhere applies.
                inside=category == "workdir_files",
            )
            if ruled is not None:
                return ruled
        if self._space is not None and category == "run_command":
            command, cwd = payload.get("command"), payload.get("cwd")
            if (isinstance(command, str) and await self._space.named_in(command)) or (
                isinstance(cwd, str) and cwd and await self._space.contains(cwd)
            ):
                # What a shell command does is not looked into. One that
                # points at the directory of the space, or runs inside it,
                # may change what task agents keep for good, past the host.
                # The user is asked about every such command, and told that
                # it is one: an approval of commands given before, for good
                # or for the session, does not cover it, and nothing is kept
                # of the answer. Commands the settings forbid stay forbidden.
                # A command that reaches the space another way (a relative
                # path, a variable) is not seen here; there is no sandbox.
                if await self._permissions.forbidden("run_command"):
                    return PermissionDecision(False, DENIED_BY_SETTINGS).to_json()
                decision = await self._permissions.ask_each_time(session_id, SPACE_COMMAND, detail=detail)
                if decision.allow and await self._permissions.forbidden("run_command"):
                    # Forbidden while the question was open.
                    return PermissionDecision(False, DENIED_BY_SETTINGS).to_json()
                return decision.to_json()
        if before_check is not None:
            before_check(category)
        session = self._sessions.get(session_id)
        if session is not None and session.runtime_name == RUNTIME_BUILTIN:
            decision = await self._permissions.check_builtin(session_id, category, detail=detail, writes=writes, recipient=recipient)
        else:
            stock_tool = session is not None and session.runtime_name == RUNTIME_DSH and payload.get("private_route") is True
            decision = await self._permissions.check_native(session_id, category, detail=detail, stock_tool=stock_tool, recipient=recipient)
        if grants is not None and decision.grant_id:
            grants.append(decision.grant_id)
        if audit is not None:
            audit.grant_id = self._permissions.authorization_ref(decision.grant_id)
        return decision.to_json()

    async def _rule_on_gateway_call(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Rule on a call of a gateway tool that a task agent in dsh is about to make.

        Only the dsh runtime asks this (``nekopower.runtime.host``). dsh gives
        a call of a gateway tool up after a minute, so whatever the call
        needs the user for is asked now, before it is made
        (``Gateway.clear_call``). The call is let through whatever came of
        it: the gateway carries it out or answers it with the refusal, in the
        words every call refused at its entry is answered with, and neither
        decides nor asks again.
        """
        arguments = payload.get("arguments")
        if self._gateway is not None:
            await self._gateway.clear_call(
                session_id, str(payload.get("tool") or ""), arguments if isinstance(arguments, dict) else {}
            )
        return PermissionDecision(True).to_json()

    async def _rule_on_host_tool(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Rule on a call of one of the host's own tools that a task agent in dsh is about to make.

        Only the dsh runtime asks this, and only about the web search: with
        plugin entries set to be asked about, the search asks the user, and
        dsh gives a call less than a minute. So the user is asked now. The
        call is let through whatever came of it: the search answers it with
        what was decided here, in the words it refuses a call with in the
        built-in runtime, and does not ask again.
        """
        from config.nekopower_settings import GATEWAY_CLEARED_CALL_SECONDS

        if payload.get("tool") == "web_search":
            commission = await self._ledger.get(session_id)
            if commission is not None and session_id in self._sessions:
                arguments = payload.get("arguments")
                queries = _search_queries(arguments if isinstance(arguments, dict) else {})
                decision = await self._search_permission(commission)
                if session_id in self._sessions:
                    self._drop_search_ruling(session_id)
                    until = asyncio.get_running_loop().time() + GATEWAY_CLEARED_CALL_SECONDS
                    self._search_rulings[session_id] = (queries, decision, until)
                else:
                    self._permissions.release_grant(decision.grant_id)
        return PermissionDecision(True).to_json()

    def _drop_search_ruling(self, commission_id: str) -> None:
        ruled = self._search_rulings.pop(commission_id, None)
        if ruled is not None:
            self._permissions.release_grant(ruled[1].grant_id)

    async def _search_permission(self, commission: Commission) -> PermissionDecision:
        """Decide whether the task agent may send this batch of web queries.

        The search is a plugin entry: the plugin switch and the
        ``plugin_entry`` permission apply as they do through the gateway.
        """
        if not self._registry.plugins_enabled():
            return PermissionDecision(False, "web search is not available: plugins are switched off")
        return await self._permissions.check(
            commission.id,
            "plugin_entry",
            hold_once=True,
            scope=GrantScope("plugin_entry", f"plugin:{WEB_SEARCH_PLUGIN_ID}:{WEB_SEARCH_ENTRY_ID}"),
            detail={"tool": "web_search", "capability": f"plugin:{WEB_SEARCH_PLUGIN_ID}:{WEB_SEARCH_ENTRY_ID}"},
        )

    async def _cb_ask_user(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = self._at_work_in(session_id)
        questions = list(payload.get("questions") or [])
        answer = await self._ask(session_id, {"kind": "questions", "questions": questions})
        text = str(answer.get("text") or "")
        answers = build_answers(questions, text, answer.get("selected"))
        live = self._sessions.get(session_id)
        said = answer_words(answer) if answer.get("selected") else text
        if live is not None and live.carries_on_for is not None and said.strip():
            # What the user answered a copy goes back with the commission,
            # should it be handed back (``_handed_back``), as the answer to
            # a report does. (Not a permission or how much may be used:
            # those are no words for the commission's work.)
            try:
                await self._ledger.append_event(session_id, "answered", {"text": said, "asked_by_tool": True})
            except Exception:  # noqa: BLE001 - the answer is the tool's all the same
                logger.warning("noting what the user answered a copy failed", exc_info=True)
        return answers

    async def _cb_report(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        # The report arrives as an event as well; that is where it is acted on.
        # What it says the session takes from now on (``duty``) is no part of
        # that event: it is for the session's card, and written here.
        # (A leader that says its commission is done while its team is at
        # work is refused, and no event comes: ``_team_still_at_work``.)
        await self._team_still_at_work(session_id, payload)
        duty = payload.get("duty")
        if not isinstance(duty, str) or not duty.strip():
            return None
        try:
            return await self._own_duty(session_id, duty)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the report goes through whatever becomes of the card
            logger.warning("writing what a session says it takes failed", exc_info=True)
            return None

    async def _own_duty(self, session_id: str, duty: str) -> dict[str, Any] | None:
        """Write what a session says it takes on its card; returns what its task agent is told, if anything.

        Only a session with a card of its own to say it on: that of a
        project, or of a single commission. Not a task agent another one
        started, and not a copy,
        which is offered the parameter because it is opened with the
        tools of the session it was copied from. A line the user wrote
        stays, and the task agent is told so.
        """
        from config.prompts.prompts_nekopower_presets import CARD_DUTY_IS_THE_USERS, CARD_NOT_ITS_OWN

        commission = await self._ledger.get(self._at_work_in(session_id))
        if commission is None:
            return None
        session = await self._ledger.get_session(commission.session_id)
        if session is None:
            return None
        # (Of the kinds another session can address: neither a task agent
        # another one started nor a copy is one.)
        if session.kind not in _ADDRESSED_KINDS:
            return {"message": CARD_NOT_ITS_OWN}
        _kept, written = await self._ledger.write_card(session.id, by="self", duty=duty)
        return None if written else {"message": CARD_DUTY_IS_THE_USERS}

    async def _cb_note_lesson(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any] | None:
        commission = await self._ledger.get(self._at_work_in(session_id))
        if commission is None:
            return None
        if action is not None:
            action.require()
            action.record.scope_ref = commission.session_id
        if payload.get("category") == "feedback":
            # A correction or approval from the user is a task preference. It is
            # an inference, so it takes effect once the same thing was seen twice.
            await self._preferences.observe(
                feedback_text(payload), lanlan_name=commission.lanlan_name, source_commission=commission.id
            )
            await self._ledger.append_event(commission.id, "feedback", dict(payload))
            return {"message": "filed as a task preference"}
        message = await self._lessons.submit(commission, payload)
        return {"message": message} if message else None

    async def _cb_web_search(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        commission = await self._ledger.get(self._at_work_in(session_id))
        if commission is None:
            raise RuntimeError("unknown commission")
        queries = list(_search_queries(payload))
        capability = f"plugin:{WEB_SEARCH_PLUGIN_ID}:{WEB_SEARCH_ENTRY_ID}"
        ruled = self._search_rulings.pop(commission.id, None)
        if ruled is not None and ruled[0] == tuple(queries) and asyncio.get_running_loop().time() <= ruled[2]:
            # Ruled on before the call was made (a task agent in dsh): the
            # user was asked then, if they had to be. Only for what they
            # were asked about: an allowance for once is not another search's.
            decision = ruled[1]
        else:
            if ruled is not None:
                self._permissions.release_grant(ruled[1].grant_id)
            decision = await self._search_permission(commission)
        refusal = decision.reason
        if not self._registry.plugins_enabled():
            refusal = "web search is not available: plugins are switched off"
        elif decision.allow and not self._permissions.grant_active(decision.grant_id):
            refusal = DENIED_BY_SETTINGS
        if refusal is not None:
            self._permissions.release_grant(decision.grant_id)
            await self._ledger.append_event(
                commission.id, "capability_denied", {"capability": capability, "reason": refusal}
            )
            raise RuntimeError(refusal)
        gate = asyncio.Semaphore(_WEB_SEARCH_PARALLEL)

        async def one(query: str) -> dict[str, Any]:
            async with gate:
                outcome = await audited_run_entry(
                    self._ledger, self._runner,
                    commission_id=commission.id,
                    plugin_id=WEB_SEARCH_PLUGIN_ID,
                    entry_id=WEB_SEARCH_ENTRY_ID,
                    args={"query": query},
                    lanlan_name=commission.lanlan_name,
                    conversation_id=commission.conversation_id,
                    latest_user_request=commission.user_text,
                    still_wanted=lambda: self._registry.plugins_enabled() and self._permissions.grant_active(decision.grant_id),
                )
            if not outcome.success:
                raise RuntimeError(f"web search failed: {outcome.error or 'the web_search plugin is not available'}")
            data = outcome.data if isinstance(outcome.data, dict) else {}
            sources = []
            for item in data.get("results") or []:
                if isinstance(item, dict) and item.get("url"):
                    source = {"url": str(item["url"])}
                    if item.get("title"):
                        source["title"] = str(item["title"])
                    if item.get("snippet"):
                        source["snippet"] = str(item["snippet"])
                    sources.append(source)
            result: dict[str, Any] = {"query": query, "sources": sources}
            if isinstance(data.get("summary"), str) and data["summary"].strip():
                result["answer"] = data["summary"]
            return result

        try:
            return {"results": list(await asyncio.gather(*(one(query) for query in queries)))}
        finally:
            self._permissions.release_grant(decision.grant_id)
