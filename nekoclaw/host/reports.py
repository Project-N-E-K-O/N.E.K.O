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

"""Writing reports for the main dialogue into the outbox.

Everything the task brain has to tell her goes through ``Reporter``: how a
commission ended, a question that waits for the user, a correction of a
receipt. Each is stored in the ledger's outbox first (``outbox.py`` sends it).

A ``task_result`` row carries the fields of the agent server's existing
``task_result`` event, with the same token limits, so the main server can
treat a commission's result the way it treats a task result today. A plugin
result reported here has the same ``status``, ``delivery_mode``,
``result_kind`` and ``expires_in_s`` the user-plugin channel gives it
(``tests/unit/nekoclaw/test_contract_plugin_results.py``).

Commissions started by another task agent report to that task agent, not to her,
so nothing is written for them here.

How a commission ended is best stored together with the status change that
ends it: build the row with ``task_result_report`` and pass it to
``Reporter.transition``. Both are then written in one transaction, and the row
is not written at all when the change is refused.
"""

from __future__ import annotations

from typing import Any

from nekoclaw.host.ledger import Commission, Ledger, Transition
from nekoclaw.host.outbox import OutboxSender

KIND_TASK_RESULT = "task_result"
KIND_QUESTION = "question"
KIND_CORRECTION = "correction"
KIND_RECEIPT = "receipt"

CHANNEL_PLUGIN = "user_plugin"
CHANNEL_TASK_AGENT = "task_agent"

# Receipts that only confirm the "received" she already answered with.
QUIET_RECEIPTS = frozenset({"accepted", "received", "told", "answered", "stopped", "nothing_active"})


def _question_lines(questions: Any, words: dict[str, str]) -> str:
    """Render the questions of an ``ask_user_question`` call as one line each."""
    lines: list[str] = []
    for item in questions if isinstance(questions, list) else []:
        if isinstance(item, dict):
            text = str(item.get("question") or "").strip()
            labels = [
                str(option.get("label") or "").strip() if isinstance(option, dict) else str(option).strip()
                for option in (item.get("options") or [])
            ]
            labels = [label for label in labels if label]
            if text and labels:
                text += words["options"].format(items=" / ".join(labels))
        else:
            text = str(item).strip()
        if text:
            lines.append(text)
    if len(lines) > 1:
        return " ".join(f"{number}. {line}" for number, line in enumerate(lines, 1))
    return lines[0] if lines else ""


def render_question(title: str, question: dict[str, Any], lang: str) -> str:
    """Return a commission's pending question as the sentence she passes on to the user."""
    from config.nekoclaw_settings import QUESTION_TEXT_MAX_TOKENS
    from config.prompts.prompts_nekoclaw_brain import (
        PERMISSION_CATEGORY_TEXTS,
        PERMISSION_OPTION_NAMES,
        QUESTION_TEXTS,
    )
    from config.prompts._locale import normalize_prompt_locale
    from config.prompts.prompts_sys import _loc
    from utils.tokenize import truncate_to_tokens

    lang = normalize_prompt_locale(lang)
    words = _loc(QUESTION_TEXTS, lang)
    kind = question.get("kind")
    if kind == "permission":
        categories = _loc(PERMISSION_CATEGORY_TEXTS, lang)
        names = _loc(PERMISSION_OPTION_NAMES, lang)
        detail = question.get("detail") if isinstance(question.get("detail"), dict) else {}
        category = str(question.get("category") or "other")
        specific = (
            detail.get("command")
            or detail.get("path")
            or question.get("key")
            or detail.get("title")
            or detail.get("tool")
            or ""
        )
        what = categories.get(category, categories["other"]).format(detail=str(specific)).strip()
        offered = [names[option] for option in question.get("options") or [] if option in names]
        text = words["permission"].format(title=title, what=what, options=" / ".join(offered))
    elif kind == "confirmation":
        text = words["confirm"].format(title=title, question=str(question.get("question") or "").strip())
    else:
        if kind == "questions":
            asked = _question_lines(question.get("questions"), words)
        else:
            parts = [str(question.get("question") or "").strip(), str(question.get("details") or "").strip()]
            asked = " ".join(part for part in parts if part)
        text = words["ask"].format(title=title, question=asked)
    return truncate_to_tokens(text, QUESTION_TEXT_MAX_TOKENS)


class Reporter:
    """Stores reports in the outbox and nudges the sender."""

    def __init__(self, ledger: Ledger, sender: OutboxSender | None = None) -> None:
        self._ledger = ledger
        self._sender = sender

    async def _add(self, commission: Commission | None, kind: str, payload: dict[str, Any]) -> int:
        item_id = await self._ledger.outbox_add(commission.id if commission else None, kind, payload)
        if self._sender is not None:
            self._sender.kick()
        return item_id

    def task_result_report(
        self,
        commission: Commission,
        *,
        channel: str,
        success: bool,
        summary: str,
        status: str,
        detail: str = "",
        error_message: str = "",
        source_kind: str = "",
        source_name: str = "",
        delivery_mode: str = "proactive",
        result_kind: str = "task_result",
        expires_in_s: float | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Build the ``(kind, payload)`` row that reports how a commission ended.

        Stores nothing. Returns None for a silent commission and for one
        started by another task agent, which are not reported to her.
        """
        if commission.parent_id is not None or delivery_mode == "silent":
            return None
        from config import TASK_ERROR_MAX_TOKENS, TASK_LARGE_DETAIL_MAX_TOKENS, TASK_SUMMARY_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        summary = truncate_to_tokens(summary, TASK_SUMMARY_MAX_TOKENS)
        payload = {
            "lanlan_name": commission.lanlan_name,
            "task_id": commission.id,
            "channel": channel,
            "status": status,
            "success": success,
            "text": summary,
            "summary": summary,
            "detail": truncate_to_tokens(detail, TASK_LARGE_DETAIL_MAX_TOKENS) if detail else "",
            "error_message": truncate_to_tokens(error_message, TASK_ERROR_MAX_TOKENS) if error_message else "",
            "direct_reply": False,
            "source_kind": source_kind,
            "source_name": source_name,
            "delivery_mode": delivery_mode,
            "result_kind": result_kind,
            "expires_in_s": expires_in_s,
            "title": commission.title,
        }
        return KIND_TASK_RESULT, payload

    async def task_result(
        self,
        commission: Commission,
        *,
        channel: str,
        success: bool,
        summary: str,
        status: str,
        detail: str = "",
        error_message: str = "",
        source_kind: str = "",
        source_name: str = "",
        delivery_mode: str = "proactive",
        result_kind: str = "task_result",
        expires_in_s: float | None = None,
    ) -> int | None:
        """Report how a commission ended. Nothing is stored for a silent one.

        This stores the row in a transaction of its own; to store it with the
        status change, use ``task_result_report`` and ``transition``.
        """
        report = self.task_result_report(
            commission,
            channel=channel,
            success=success,
            summary=summary,
            status=status,
            detail=detail,
            error_message=error_message,
            source_kind=source_kind,
            source_name=source_name,
            delivery_mode=delivery_mode,
            result_kind=result_kind,
            expires_in_s=expires_in_s,
        )
        if report is None:
            return None
        kind, payload = report
        return await self._add(commission, kind, payload)

    async def transition(
        self,
        commission_id: str,
        status: str,
        *,
        reason: str = "",
        report: tuple[str, dict[str, Any]] | None = None,
        **values: Any,
    ) -> Transition:
        """Change a commission's status and store ``report`` in the same transaction.

        Same as ``Ledger.transition`` otherwise. ``report`` is what
        ``task_result_report`` built, or None to report nothing. When the
        change is refused nothing is stored, and the sender is nudged only
        when a row was stored.
        """
        moved = await self._ledger.transition(
            commission_id, status, reason=reason, outbox=[report] if report is not None else (), **values
        )
        if moved.outbox_ids and self._sender is not None:
            self._sender.kick()
        return moved

    async def question(self, commission: Commission, question: dict[str, Any]) -> int | None:
        """Report that a commission waits for the user to answer or approve.

        ``text`` is the question as one sentence for her to pass on, in the
        commission's language; ``question`` keeps its parts for an interface
        that shows buttons.
        """
        root = commission
        payload = {
            "lanlan_name": root.lanlan_name,
            "task_id": commission.id,
            "title": commission.title,
            "question": question,
            "text": render_question(commission.title, question, commission.lang or "en"),
            "delivery_mode": "proactive",
        }
        return await self._add(commission, KIND_QUESTION, payload)

    async def correction(self, commission: Commission, *, reason: str, detail: str = "", text: str = "") -> int | None:
        """Report that what she was told about a dispatch turned out wrong.

        ``reason`` is ``no_capability`` (accepted, but nothing can do it) or
        ``duplicate`` (accepted, but it repeats something under way).
        """
        if commission.parent_id is not None:
            return None
        payload = {
            "lanlan_name": commission.lanlan_name,
            "task_id": commission.id,
            "title": commission.title,
            "reason": reason,
            "detail": detail,
            "text": text,
            "delivery_mode": "proactive",
        }
        return await self._add(commission, KIND_CORRECTION, payload)

    async def receipt(self, lanlan_name: str, *, kind: str, text: str, commission: Commission | None = None) -> int:
        """Report the conclusion of a first round that outlasted its budget.

        She answered "received" meanwhile. A conclusion that agrees with that
        (accepted, passed on, stopped) comes along with her next turn; one
        she has to act on (nothing can do it, it repeats something, it is
        unclear which task is meant) wakes her.
        """
        payload = {
            "lanlan_name": lanlan_name,
            "task_id": commission.id if commission else None,
            "receipt": kind,
            "text": text,
            "delivery_mode": "passive" if kind in QUIET_RECEIPTS else "proactive",
        }
        return await self._add(commission, KIND_RECEIPT, payload)

    async def turn_notice(
        self,
        lanlan_name: str,
        *,
        reason: str,
        text: str,
        delivery_mode: str = "proactive",
        commission: Commission | None = None,
    ) -> int:
        """Report what the turn check found: something she said does not match what was done.

        ``reason`` is ``can_do`` (she said it cannot be done; it can),
        ``no_record`` (she said it is done; no task says so), ``filed`` (she
        agreed; it was dispatched for her) or ``promised_failed`` (she agreed;
        it could not be dispatched).
        """
        payload = {
            "lanlan_name": lanlan_name,
            "task_id": commission.id if commission else None,
            "title": commission.title if commission else "",
            "reason": reason,
            "detail": "",
            "text": text,
            "delivery_mode": delivery_mode,
        }
        return await self._add(commission, KIND_CORRECTION, payload)
