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

"""The task brain: dispatch, query and reports (design section 6).

The main dialogue forgets: its session is replaced every ten turns or so. The
task brain is what remembers. It lives in the host, keeps every commission in
the ledger and decides who gets a commission done.

``dispatch`` takes one sentence from her. New work, an addition, a change of
mind, a stop request and the answer to a familiar's question all arrive this
way; which commission a sentence is about is worked out against the ledger.
It waits for the first round only:

* first round (waited for, local): is it a stop request, an answer, an
  addition or a repeat; are there candidate plugins; can a familiar be
  started. The receipt says one of: already under way, nothing can do this,
  accepted (or stopped / passed on, for sentences about an existing
  commission). If it takes longer than ``DISPATCH_FIRST_ROUND_SECONDS`` the
  receipt is "received" and the conclusion follows as a report.
* second round (not waited for): choose a plugin entry and call it, or hand
  over to a familiar. Plugins come first. If this round overturns the
  receipt (accepted, but nothing can do it; or it repeats something after
  all), she is told at once through a ``correction`` report.

``query`` never asks a model. It answers "what is going on" from the ledger
alone, and "can this be done" with the same local matching the first round
uses, without filing anything.

Calling a plugin entry directly keeps what the user-plugin channel does today
with ``delivery``, ``result_kind``, ``expires_in_s``, ``llm_result_fields``,
deferred results, ``needs_confirmation`` and ``clarify``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nekoclaw.host.capabilities import CapabilityRegistry, McpDenial, mcp_entry_forbidden
from nekoclaw.host.decision import (
    DecisionModel,
    DecisionRequest,
    Selection,
    Triage,
    commission_text,
    find_duplicate,
    is_capability_question,
)
from nekoclaw.host.ledger import ACTIVE_STATUSES, ENDED_STATUSES, Commission, Ledger
from nekoclaw.host.permissions import PermissionStore
from nekoclaw.host.plugin_runs import (
    PluginRunner,
    RunOutcome,
    describe_result,
    plugin_terminal_status,
    resolve_delivery_mode,
    resolve_result_contract,
)
from nekoclaw.host.preferences import PreferenceStore, describe_stop, preference_kind, stop_tendency_prefix
from nekoclaw.host.reports import CHANNEL_PLUGIN, Reporter
from nekoclaw.host.selection import find_entry, similarity, visible_entries

logger = logging.getLogger(__name__)

_DIRECT_STATUS = {"completed": "done", "blocked": "blocked", "failed": "failed"}
BIND_TASK_ENTRY = "bind_task"


@dataclass(slots=True)
class Receipt:
    """What a dispatch is answered with."""

    # "accepted", "received", "duplicate", "no_capability", "stopped", "told",
    # "answered", "ambiguous", "nothing_active" or "unclear_answer".
    kind: str
    commissions: list[Commission] = field(default_factory=list)
    # For "no_capability": disabled, not_installed, not_enabled, denied or no_match.
    reason: str = ""
    model_calls: int = 0

    @property
    def commission_id(self) -> str | None:
        return self.commissions[0].id if self.commissions else None

    def text(self, lang: str = "en") -> str:
        """Return the receipt as the line the main dialogue model reads."""
        return render_receipt(self, lang)


@dataclass(slots=True)
class QueryAnswer:
    """What a query is answered with."""

    text: str
    # The commissions listed, most relevant first.
    commissions: list[Commission] = field(default_factory=list)
    # For a "can this be done" query: True, False, or None when it was not
    # asked or cannot be told without a model.
    can_do: bool | None = None
    reason: str = ""
    model_calls: int = 0


def prompt_language(lang: Any) -> str:
    """Return the key of the prompt tables for any language code (``zh-CN`` gives ``zh``)."""
    from config.prompts._locale import normalize_prompt_locale

    return normalize_prompt_locale(lang)


def _texts(table: dict[str, Any], lang: str) -> Any:
    from config.prompts.prompts_sys import _loc

    return _loc(table, prompt_language(lang))


def status_word(status: str, lang: str) -> str:
    from config.prompts.prompts_nekoclaw_brain import COMMISSION_STATUS_WORDS

    return _texts(COMMISSION_STATUS_WORDS, lang).get(status, status)


def render_receipt(receipt: Receipt, lang: str) -> str:
    """Return a receipt as text, in the given language."""
    from config.prompts.prompts_nekoclaw_brain import NO_CAPABILITY_REASONS, RECEIPT_TEXTS

    templates = _texts(RECEIPT_TEXTS, lang)
    first = receipt.commissions[0] if receipt.commissions else None
    title = first.title if first else ""
    if receipt.kind == "no_capability":
        reason = _texts(NO_CAPABILITY_REASONS, lang).get(receipt.reason) or receipt.reason
        return templates["no_capability"].format(reason=reason)
    if receipt.kind == "duplicate" and first is not None:
        return templates["duplicate"].format(title=title, status=status_word(first.status, lang))
    if receipt.kind == "ambiguous":
        candidates = "\n".join(
            f"- {commission.title} ({status_word(commission.status, lang)})" for commission in receipt.commissions
        )
        return templates["ambiguous"].format(candidates=candidates)
    template = templates.get(receipt.kind)
    if template is None:
        return receipt.kind
    return template.format(title=title)


class TaskBrain:
    """Dispatch, query and reports. See the module docstring."""

    def __init__(
        self,
        *,
        ledger: Ledger,
        preferences: PreferenceStore,
        registry: CapabilityRegistry,
        runner: PluginRunner,
        decision: DecisionModel,
        reporter: Reporter,
        permission_store: PermissionStore,
        familiars: Any = None,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._ledger = ledger
        self._preferences = preferences
        self._registry = registry
        self._runner = runner
        self._decision = decision
        self._reporter = reporter
        self._permission_store = permission_store
        self._familiars = familiars
        self._enabled = enabled
        self._tasks: set[asyncio.Task[Any]] = set()
        # commission id -> its second round or direct plugin call.
        self._work: dict[str, asyncio.Task[Any]] = {}
        self._deferred_timers: dict[str, asyncio.TimerHandle] = {}
        self._closed = False

    # -- helpers ----------------------------------------------------------

    def _spawn(self, coroutine: Any, *, commission_id: str | None = None) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if commission_id is not None:
            self._work[commission_id] = task

            def forget(done: asyncio.Task[Any], cid: str = commission_id) -> None:
                if self._work.get(cid) is done:
                    del self._work[cid]

            task.add_done_callback(forget)
        return task

    async def settle(self) -> None:
        """Wait for everything running in the background (second rounds, calls)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        if self._familiars is not None:
            await self._familiars.settle()

    def _is_enabled(self) -> bool:
        if self._closed:
            return False
        if self._enabled is None:
            return True
        try:
            return bool(self._enabled())
        except Exception:  # noqa: BLE001 - an unreadable switch counts as off
            return False

    async def _familiar_available(self) -> bool:
        if self._familiars is None:
            return False
        available, _reason = await self._familiars.available()
        return available

    async def _no_capability_reason(self) -> str:
        """Say why nothing can take a task: not installed, not switched on, forbidden."""
        if await self._permission_store.mode("plugin_entry") == "deny":
            return "denied"
        summary = await self._registry.summary()
        if summary.installed == 0:
            return "not_installed"
        if not summary.enabled or summary.routable == 0:
            return "not_enabled"
        return "no_match"

    async def _request(self, text: str, user_text: str, lanlan_name: str, lang: str) -> DecisionRequest:
        under_way = await self._ledger.select(lanlan_name=lanlan_name, statuses=ACTIVE_STATUSES)
        # Familiars started by familiars are their parents' business (as in
        # ``query``): she never dispatched them, so a stop, a repeat or an
        # addition is not about them. Their questions reach the user, though,
        # so an answer can be.
        active = [item for item in under_way if item.parent_id is None]
        answerable = [item for item in under_way if item.parent_id is not None and item.status == "waiting_user"]
        # Plugin entries forbidden in the permission settings are not offered
        # to the decision model at all, so none is called directly either.
        # Only "deny" counts here, on purpose: a direct call is one she
        # dispatched and the model chose, called without asking as the
        # analyzer does today. The asking modes apply where the design puts
        # the questions: a familiar's tool calls and the capability gateway.
        plugins_forbidden = await self._permission_store.mode("plugin_entry") == "deny"
        return DecisionRequest(
            dispatch_text=text,
            user_text=user_text,
            lang=lang,
            plugins=[] if plugins_forbidden else await self._without_denied_mcp(await self._registry.plugins()),
            active=active,
            answerable=answerable,
            familiar_available=await self._familiar_available(),
        )

    async def _denied_mcp(self) -> McpDenial:
        """Return the MCP server settings as of now."""
        snapshot = await self._permission_store.snapshot()
        servers = {str(name): str(mode) for name, mode in (snapshot.get("mcp_servers") or {}).items()}
        category = str((snapshot.get("categories") or {}).get("mcp_server") or "")
        return McpDenial(servers=servers, category=category)

    async def _without_denied_mcp(self, plugins: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Leave out the MCP tools of servers the permission settings forbid.

        "Deny" holds everywhere, the direct route included: a server set to
        "deny" is not reached by her dispatches either, nor through the
        adapter's entries that can reach any server.
        """
        denial = await self._denied_mcp()
        if not denial.any_denied:
            return plugins
        kept: list[dict[str, Any]] = []
        for plugin in plugins:
            plugin_id = str(plugin.get("id") or "")
            entries = plugin.get("entries") or []
            allowed = [
                entry
                for entry in entries
                if not (isinstance(entry, dict) and mcp_entry_forbidden(plugin_id, entry, denial))
            ]
            if len(allowed) == len(entries):
                kept.append(plugin)
                continue
            trimmed = {**plugin, "entries": allowed}
            # Kept only while something routable is left: hidden entries alone
            # would still make the plugin look able to take the task.
            if visible_entries(trimmed):
                kept.append(trimmed)
        return kept

    # -- dispatch ---------------------------------------------------------

    async def dispatch(
        self,
        text: str,
        *,
        lanlan_name: str,
        user_text: str = "",
        conversation_id: str | None = None,
        lang: str = "en",
        source: str = "dispatch",
    ) -> Receipt:
        """Take one sentence from her and return the receipt of the first round.

        ``source`` is ``dispatch`` when she called the tool herself and
        ``promised`` when the sentence is filed for her after she agreed to do
        something out loud.
        """
        from config.nekoclaw_settings import (
            DISPATCH_FIRST_ROUND_SECONDS,
            DISPATCH_TEXT_MAX_TOKENS,
            DISPATCH_USER_TEXT_MAX_TOKENS,
        )
        from utils.tokenize import truncate_to_tokens

        lang = prompt_language(lang)
        if not self._is_enabled():
            return Receipt(kind="no_capability", reason="disabled")
        text = truncate_to_tokens(" ".join(str(text).split()), DISPATCH_TEXT_MAX_TOKENS)
        user_text = truncate_to_tokens(str(user_text or "").strip(), DISPATCH_USER_TEXT_MAX_TOKENS)
        if not text:
            return Receipt(kind="nothing_active")

        first = self._spawn(self._first_round(text, user_text, lanlan_name, conversation_id, lang, source))
        done, _pending = await asyncio.wait({first}, timeout=DISPATCH_FIRST_ROUND_SECONDS)
        if done:
            return first.result()

        # Too slow to wait for. She is told "received" now and the conclusion
        # when there is one, an acceptance included: that is when she learns
        # the commission's id. The second round follows either way.
        def late(task: asyncio.Task[Receipt]) -> None:
            if task.cancelled():
                return
            error = task.exception()
            if error is not None:
                # Nobody awaits this round any more; without this line the
                # failure would leave no trace. There is no receipt text for
                # it, so she is told nothing further.
                logger.error("the first round of a dispatch failed after the early receipt", exc_info=error)
                return
            receipt = task.result()
            self._spawn(
                self._reporter.receipt(
                    lanlan_name,
                    kind=receipt.kind,
                    text=receipt.text(lang),
                    commission=receipt.commissions[0] if receipt.commissions else None,
                )
            )

        first.add_done_callback(late)
        return Receipt(kind="received")

    async def _first_round(
        self,
        text: str,
        user_text: str,
        lanlan_name: str,
        conversation_id: str | None,
        lang: str,
        source: str,
    ) -> Receipt:
        from config.nekoclaw_settings import COMMISSION_TITLE_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        await self.expire_deferred()
        request = await self._request(text, user_text, lanlan_name, lang)
        triage = await self._decision.triage(request)
        by_id = {commission.id: commission for commission in (*request.active, *request.answerable)}
        targets = [by_id[target] for target in triage.targets if target in by_id]
        calls = triage.model_calls

        if triage.kind == "nothing_active":
            return Receipt(kind="nothing_active", model_calls=calls)
        if triage.kind == "ambiguous":
            return Receipt(kind="ambiguous", commissions=targets, model_calls=calls)
        if triage.kind == "duplicate" and targets:
            return Receipt(kind="duplicate", commissions=targets, model_calls=calls)
        if triage.kind == "stop" and targets:
            stopped = await self.abort(targets[0].id, reason="stopped through a dispatch", notify=False)
            return Receipt(kind="stopped", commissions=[stopped or targets[0]], model_calls=calls)
        if triage.kind == "answer" and targets:
            target = targets[0]
            is_permission = (target.question() or {}).get("kind") == "permission"
            if is_permission and triage.choice is None:
                return Receipt(kind="unclear_answer", commissions=[target], model_calls=calls)
            delivered = await self.answer(target.id, text, choice=triage.choice)
            return Receipt(kind="answered" if delivered else "ambiguous", commissions=[target], model_calls=calls)
        if triage.kind == "tell" and targets:
            delivered = await self.tell(targets[0].id, text)
            if delivered:
                return Receipt(kind="told", commissions=targets[:1], model_calls=calls)
            # It could not be passed on; treat the sentence as work of its own.
        if triage.kind in ("stop", "answer", "duplicate"):
            # What it referred to is no longer there.
            return Receipt(kind="nothing_active", model_calls=calls)
        if triage.kind == "none":
            return Receipt(kind="no_capability", reason=await self._no_capability_reason(), model_calls=calls)

        commission, created = await self._ledger.create(
            unless=lambda active: find_duplicate(text, user_text, active),
            lanlan_name=lanlan_name,
            title=truncate_to_tokens(text, COMMISSION_TITLE_MAX_TOKENS),
            dispatch_text=text,
            user_text=user_text,
            source=source,
            conversation_id=conversation_id,
            lang=lang,
        )
        if not created:
            return Receipt(kind="duplicate", commissions=[commission], model_calls=calls)
        request.active = [item for item in request.active if item.id != commission.id]
        self._spawn(self._second_round(commission, request, triage), commission_id=commission.id)
        return Receipt(kind="accepted", commissions=[commission], model_calls=calls)

    async def _second_round(self, commission: Commission, request: DecisionRequest, triage: Triage) -> None:
        try:
            request.preferences = await self._preferences.render(request.match_text)
            selection = await self._decision.select(request, triage)
            await self._ledger.append_event(
                commission.id,
                "decision",
                {
                    "route": selection.route,
                    "plugin_id": selection.plugin_id,
                    "entry_id": selection.entry_id,
                    "reason": selection.reason,
                    "model_calls": triage.model_calls + selection.model_calls,
                },
            )
            if selection.preference:
                # Something she stated for good: it applies from now on. Its
                # kind is read from its words; reporting and ask-first rules
                # then reach every task, not only ones sharing words with it.
                await self._preferences.state(
                    selection.preference,
                    kind=preference_kind(selection.preference),
                    lanlan_name=commission.lanlan_name,
                    source_commission=commission.id,
                )
            if selection.route == "plugin":
                await self._call_plugin(commission, selection, request)
            elif selection.route == "familiar" and self._familiars is not None:
                await self._familiars.submit(commission, candidate_plugin_ids=selection.shown_plugin_ids)
            elif selection.route == "duplicate" and selection.duplicate_of:
                await self._overturn_duplicate(commission, selection.duplicate_of)
            else:
                await self._overturn_no_capability(commission, selection.reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the commission fails with the reason
            logger.exception("second round of commission %s failed", commission.id)
            moved = await self._ledger.transition(
                commission.id, "failed", reason=f"internal error: {type(exc).__name__}"
            )
            if moved.applied:
                await self._reporter.correction(
                    commission, reason="no_capability", detail="error", text=self._no_capability_text(commission, "no_match")
                )

    def _no_capability_text(self, commission: Commission, reason: str) -> str:
        return Receipt(kind="no_capability", commissions=[commission], reason=reason).text(commission.lang or "en")

    async def _overturn_no_capability(self, commission: Commission, why: str) -> None:
        """Accepted, yet nothing can do it: fail the commission and tell her."""
        reason = await self._no_capability_reason()
        moved = await self._ledger.transition(
            commission.id, "failed", reason=f"nothing can do this: {why or reason}"
        )
        if moved.applied:
            await self._reporter.correction(
                commission, reason="no_capability", detail=reason, text=self._no_capability_text(commission, reason)
            )

    async def _overturn_duplicate(self, commission: Commission, duplicate_of: str) -> None:
        """Accepted, yet it repeats something under way: withdraw it and tell her."""
        original = await self._ledger.get(duplicate_of)
        moved = await self._ledger.transition(commission.id, "cancelled", reason=f"repeats commission {duplicate_of}")
        if moved.applied:
            text = ""
            if original is not None:
                text = Receipt(kind="duplicate", commissions=[original]).text(commission.lang or "en")
            await self._reporter.correction(commission, reason="duplicate", detail=duplicate_of, text=text)

    # -- the direct route -------------------------------------------------

    async def _mcp_forbidden(self, selection: Selection, request: DecisionRequest) -> bool:
        """Whether the chosen entry reaches an MCP server forbidden by now."""
        _plugin, entry = find_entry(request.plugins, str(selection.plugin_id), selection.entry_id)
        if entry is None:
            return False
        return mcp_entry_forbidden(str(selection.plugin_id), entry, await self._denied_mcp())

    async def _call_plugin(self, commission: Commission, selection: Selection, request: DecisionRequest) -> None:
        """Call the chosen plugin entry and turn its result into the commission's end."""
        if await self._permission_store.mode("plugin_entry") == "deny" or await self._mcp_forbidden(selection, request):
            # Forbidden while the model was choosing: nothing is called.
            await self._overturn_no_capability(commission, "denied")
            return
        moved = await self._ledger.transition(
            commission.id,
            "working",
            reason="calling a plugin entry",
            route="plugin",
            plugin_id=selection.plugin_id,
            entry_id=selection.entry_id,
        )
        if not moved.applied:
            return
        _plugin, entry = find_entry(request.plugins, str(selection.plugin_id), selection.entry_id)

        async def run_created(run_id: str) -> None:
            await self._ledger.append_event(
                commission.id,
                "plugin_run",
                {"run_id": run_id, "plugin_id": selection.plugin_id, "entry_id": selection.entry_id, "via": "direct"},
            )

        async def progress(**update: Any) -> None:
            await self._ledger.append_event(commission.id, "progress", update)

        outcome = await self._runner.run_entry(
            commission_id=commission.id,
            plugin_id=selection.plugin_id,
            entry_id=selection.entry_id,
            args=selection.plugin_args,
            lanlan_name=commission.lanlan_name,
            conversation_id=commission.conversation_id,
            latest_user_request=commission.user_text,
            on_progress=progress,
            on_run_created=run_created,
        )
        await self._finish_direct(commission, request.plugins, entry, outcome)

    async def _finish_direct(
        self,
        commission: Commission,
        plugins: list[dict[str, Any]],
        entry: dict[str, Any] | None,
        outcome: RunOutcome,
    ) -> None:
        from config.nekoclaw_settings import DEFERRED_COMMISSION_SECONDS, FAMILIAR_RESULT_SUMMARY_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        detail = describe_result(entry, outcome, lang=commission.lang)
        terminal = plugin_terminal_status(outcome.success, outcome.data)
        result_kind, expires_in_s = resolve_result_contract(entry, outcome.envelope)
        delivery = resolve_delivery_mode(outcome.envelope)
        plugin = next((item for item in plugins if item.get("id") == outcome.plugin_id), None)
        source_name = str((plugin or {}).get("name") or outcome.plugin_id)

        if outcome.success and isinstance(outcome.data, dict) and outcome.data.get("deferred") is True:
            # The plugin finishes this later and calls back; until then the
            # commission stays open and nothing is reported.
            deadline = self._ledger._clock() + DEFERRED_COMMISSION_SECONDS
            # Keep how the plugin wants it reported, so a timeout reported
            # later is as quiet as the result would have been.
            await self._ledger.update(
                commission.id, deferred_until=deadline, delivery=commission.delivery or delivery
            )
            reminder_id = outcome.data.get("reminder_id")
            await self._ledger.append_event(commission.id, "deferred", {"reminder_id": reminder_id})
            self._arm_deferred(commission.id, DEFERRED_COMMISSION_SECONDS)
            if reminder_id:
                self._spawn(self._bind_deferred(commission, outcome.plugin_id, str(reminder_id)))
            return

        completed = terminal == "completed"
        failed_text = (detail or str(outcome.error or "")).strip()
        summary = detail if outcome.success else failed_text
        if outcome.success:
            report = self._reporter.task_result_report(
                commission,
                channel=CHANNEL_PLUGIN,
                success=completed,
                summary=detail,
                detail=detail,
                status="completed" if completed else terminal,
                source_kind="plugin",
                source_name=source_name,
                delivery_mode=delivery,
                # Only a completed result keeps its own kind and expiry.
                result_kind=result_kind if completed else "task_result",
                expires_in_s=expires_in_s if completed else None,
            )
        else:
            report = self._reporter.task_result_report(
                commission,
                channel=CHANNEL_PLUGIN,
                success=False,
                summary=failed_text,
                detail=failed_text,
                error_message=failed_text,
                status="failed",
                source_kind="plugin",
                source_name=source_name,
                delivery_mode=delivery,
            )
        # The status and the report are stored together. When the commission
        # was stopped while the plugin was at work, the stop stands and
        # nothing is reported.
        await self._reporter.transition(
            commission.id,
            _DIRECT_STATUS[terminal],
            reason=outcome.reason or terminal,
            report=report,
            result_summary=truncate_to_tokens(summary, FAMILIAR_RESULT_SUMMARY_MAX_TOKENS),
        )

    async def _bind_deferred(self, commission: Commission, plugin_id: str, reminder_id: str) -> None:
        """Tell the plugin which commission its deferred work belongs to."""
        outcome = await self._runner.run_entry(
            commission_id=commission.id,
            plugin_id=plugin_id,
            entry_id=BIND_TASK_ENTRY,
            args={"reminder_id": reminder_id, "agent_task_id": commission.id},
        )
        if not outcome.success:
            logger.warning("binding deferred work of commission %s failed: %s", commission.id, outcome.error)

    def _arm_deferred(self, commission_id: str, delay: float) -> None:
        loop = asyncio.get_running_loop()

        def fire() -> None:
            # A fired timer is dropped first: when the loop's clock runs a
            # little ahead of the ledger's, the sweep finds the deadline not
            # yet passed and sets a new timer for what is left.
            if self._deferred_timers.get(commission_id) is timer:
                del self._deferred_timers[commission_id]
            self._spawn(self.expire_deferred())

        timer = loop.call_later(max(delay, 0.0), fire)
        previous = self._deferred_timers.pop(commission_id, None)
        if previous is not None:
            previous.cancel()
        self._deferred_timers[commission_id] = timer

    async def expire_deferred(self) -> int:
        """Fail deferred commissions whose callback never came. Returns how many.

        Each one is reported once, the way a failed plugin call is.
        """
        reason = "Deferred task timeout (callback not received)"
        now = self._ledger._clock()
        expired = 0
        for commission in await self._ledger.select(statuses={"working"}):
            if commission.deferred_until is None:
                continue
            if commission.deferred_until > now:
                if commission.id not in self._deferred_timers:
                    # Kept over a restart of the host: its timer went with
                    # the old process, so it is set again for what is left.
                    self._arm_deferred(commission.id, commission.deferred_until - now)
                continue
            report = self._reporter.task_result_report(
                commission,
                channel=CHANNEL_PLUGIN,
                success=False,
                summary=reason,
                detail=reason,
                error_message=reason,
                status="failed",
                source_kind="plugin",
                source_name=str(commission.plugin_id or commission.title),
                delivery_mode=commission.delivery or "proactive",
            )
            moved = await self._reporter.transition(
                commission.id, "failed", reason=reason, report=report, deferred_until=None
            )
            if not moved.applied:
                continue
            expired += 1
            timer = self._deferred_timers.pop(commission.id, None)
            if timer is not None:
                timer.cancel()
        return expired

    async def complete_deferred(self, commission_id: str) -> bool:
        """Mark a deferred commission done; called when the plugin's callback arrives."""
        commission = await self._ledger.get(commission_id)
        if commission is None or commission.deferred_until is None or commission.status != "working":
            return False
        moved = await self._ledger.transition(
            commission_id, "done", reason="deferred callback completed", deferred_until=None
        )
        timer = self._deferred_timers.pop(commission_id, None)
        if timer is not None:
            timer.cancel()
        return moved.applied

    # -- acting on an existing commission ---------------------------------

    async def abort(self, commission_id: str, *, reason: str = "stopped", notify: bool = True) -> Commission | None:
        """Stop a commission. Returns it as it is afterwards, or None if unknown.

        The status is set first, so nothing that is still running can change
        it afterwards. Then the running parts are told: a plugin run is
        cancelled on the plugin server, a familiar is closed. ``notify`` is
        False when she asked for the stop herself and the receipt tells her.
        """
        commission = await self._ledger.get(commission_id)
        if commission is None:
            return None
        report = None
        if notify:
            report = self._reporter.task_result_report(
                commission,
                channel=CHANNEL_PLUGIN if commission.route == "plugin" else "familiar",
                success=False,
                summary=reason,
                detail=reason,
                error_message=reason,
                status="cancelled",
                source_kind=commission.route or "",
                source_name=commission.title,
                delivery_mode=commission.delivery or "proactive",
            )
        # The stop and its report are stored together.
        moved = await self._reporter.transition(
            commission_id, "cancelled", reason=reason, report=report, deferred_until=None
        )
        if not moved.applied:
            return commission
        timer = self._deferred_timers.pop(commission_id, None)
        if timer is not None:
            timer.cancel()
        await self._runner.cancel_commission(commission_id, reason)
        work = self._work.get(commission_id)
        if work is not None and work is not asyncio.current_task():
            work.cancel()
        if self._familiars is not None:
            await self._familiars.abort(commission_id)
        target = (
            # Without an entry id the plugin alone names it, not "<plugin>.None".
            (f"{commission.plugin_id}.{commission.entry_id}" if commission.entry_id else str(commission.plugin_id))
            if commission.route == "plugin" and commission.plugin_id
            else (commission.route or "")
        )
        if commission.route == "plugin" and commission.plugin_id and commission.parent_id is None:
            # Being stopped hints at how such things should not be done.
            await self._preferences.observe(
                describe_stop(commission.title, target),
                kind="capability",
                lanlan_name=commission.lanlan_name,
                source_commission=commission.id,
                same_tendency_prefix=stop_tendency_prefix(target),
            )
        return moved.commission

    async def tell(self, commission_id: str, text: str) -> bool:
        """Pass a sentence on to a commission. Only a familiar can take one."""
        if self._familiars is None:
            return False
        return await self._familiars.tell(commission_id, text)

    async def answer(self, commission_id: str, text: str, *, choice: str | None = None) -> bool:
        """Give the user's answer to a commission that waits for one."""
        if self._familiars is None:
            return False
        return await self._familiars.answer(commission_id, text, choice)

    # -- query ------------------------------------------------------------

    async def query(self, text: str, *, lanlan_name: str, lang: str = "en") -> QueryAnswer:
        """Answer what is going on, or whether something can be done.

        No model is asked either way. Progress comes from the ledger alone.
        Whether something can be done is judged by the first round's local
        matching, so the answer agrees with the receipt a dispatch would get;
        nothing is filed and nothing runs. In the one case where a dispatch
        would let the small model look (nothing matches locally and no
        familiar can be started) the answer is left open: ``can_do`` is None.
        """
        from config.nekoclaw_settings import (
            QUERY_LEDGER_ROWS,
            QUERY_RECENT_SECONDS,
            QUERY_RESULT_MAX_TOKENS,
            QUERY_ROW_MAX_TOKENS,
        )
        from config.prompts.prompts_nekoclaw_brain import NO_CAPABILITY_REASONS, QUERY_TEXTS
        from utils.tokenize import truncate_to_tokens

        lang = prompt_language(lang)
        words = _texts(QUERY_TEXTS, lang)
        text = " ".join(str(text or "").split())
        await self.expire_deferred()

        if text and is_capability_question(text):
            if not self._is_enabled():
                reason = "disabled"
                can_do, calls, names = False, 0, []
            else:
                request = await self._request(text, "", lanlan_name, lang)
                # Local matching only: a query never asks a model.
                triage = await self._decision.probe(request, ask_model=False)
                calls = triage.model_calls
                names = triage.candidate_ids
                if triage.kind == "unsure":
                    # A dispatch would let the small model look before saying
                    # no; a query does not, so it draws no conclusion.
                    can_do, reason = None, ""
                else:
                    can_do = triage.kind != "none"
                    reason = "" if can_do else await self._no_capability_reason()
            if can_do is None:
                answer = words["unsure"]
            elif not can_do:
                answer = words["cannot"].format(reason=_texts(NO_CAPABILITY_REASONS, lang).get(reason) or reason)
            elif names:
                answer = words["can_plugin"].format(names=", ".join(names[:5]))
            else:
                answer = words["can_familiar"]
            return QueryAnswer(
                text=truncate_to_tokens(answer, QUERY_RESULT_MAX_TOKENS), can_do=can_do, reason=reason, model_calls=calls
            )

        now = self._ledger._clock()
        active = await self._ledger.select(lanlan_name=lanlan_name, statuses=ACTIVE_STATUSES)
        recent = await self._ledger.select(
            lanlan_name=lanlan_name,
            statuses=ENDED_STATUSES - {"blocked"},
            updated_since=now - QUERY_RECENT_SECONDS,
            limit=QUERY_LEDGER_ROWS * 4,
        )
        # Familiars started by familiars are their parents' business.
        active = [item for item in active if item.parent_id is None]
        recent = [item for item in recent if item.parent_id is None]
        if text:
            # Which one is meant: the commissions sharing most words with the question first.
            def relevance(commission: Commission) -> float:
                return similarity(text, f"{commission_text(commission)} {commission.result_summary or ''}")

            if any(relevance(item) > 0 for item in (*active, *recent)):
                active = sorted(active, key=relevance, reverse=True)
                recent = sorted(recent, key=relevance, reverse=True)
        active = active[:QUERY_LEDGER_ROWS]
        recent = recent[: QUERY_LEDGER_ROWS - len(active)]

        def line(commission: Commission) -> str:
            parts = [f"- {commission.title} ({status_word(commission.status, lang)})"]
            question = commission.question()
            if commission.status == "waiting_user" and question:
                asked = question.get("question") or question.get("category") or ""
                if not asked and isinstance(question.get("questions"), list) and question["questions"]:
                    first = question["questions"][0]
                    asked = first.get("question", "") if isinstance(first, dict) else str(first)
                if asked:
                    parts.append(words["asked"].format(question=asked))
            elif commission.result_summary:
                parts.append(words["result"].format(summary=commission.result_summary))
            elif commission.status_reason and commission.status in ("failed", "blocked"):
                parts.append(words["result"].format(summary=commission.status_reason))
            return truncate_to_tokens(" ".join(parts), QUERY_ROW_MAX_TOKENS)

        lines: list[str] = []
        if active:
            lines.append(words["active"])
            lines.extend(line(item) for item in active)
        if recent:
            lines.append(words["recent"])
            lines.extend(line(item) for item in recent)
        if not lines:
            lines.append(words["empty"])
        return QueryAnswer(
            text=truncate_to_tokens("\n".join(lines), QUERY_RESULT_MAX_TOKENS),
            commissions=[*active, *recent],
        )

    # -- shutdown ---------------------------------------------------------

    async def close(self) -> None:
        """Stop the background work. Commissions keep the status they have."""
        self._closed = True
        for timer in self._deferred_timers.values():
            timer.cancel()
        self._deferred_timers.clear()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
