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

Commissions started by another familiar report to that familiar, not to her,
so nothing is written for them here.
"""

from __future__ import annotations

from typing import Any

from nekoclaw.host.ledger import Commission, Ledger
from nekoclaw.host.outbox import OutboxSender

KIND_TASK_RESULT = "task_result"
KIND_QUESTION = "question"
KIND_CORRECTION = "correction"
KIND_RECEIPT = "receipt"

CHANNEL_PLUGIN = "user_plugin"
CHANNEL_FAMILIAR = "familiar"


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
        """Report how a commission ended. Nothing is stored for a silent one."""
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
        return await self._add(commission, KIND_TASK_RESULT, payload)

    async def question(self, commission: Commission, question: dict[str, Any]) -> int | None:
        """Report that a commission waits for the user to answer or approve."""
        root = commission
        payload = {
            "lanlan_name": root.lanlan_name,
            "task_id": commission.id,
            "title": commission.title,
            "question": question,
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
        """Report the conclusion of a first round that outlasted its budget."""
        payload = {
            "lanlan_name": lanlan_name,
            "task_id": commission.id if commission else None,
            "receipt": kind,
            "text": text,
            "delivery_mode": "proactive",
        }
        return await self._add(commission, KIND_RECEIPT, payload)
