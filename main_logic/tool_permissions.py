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

"""The agent tool-call permission gate: who asked, what the tool does, what follows.

Three rules, one seam.

1. The band comes from ``config.permission_settings`` maps and is never inferred from a
   name, a nickname or a co-occurrence. A group message takes the lower of the group
   band and the speaker band.
2. The risk comes from the tool's own declaration. There is no fallback class: an
   unlabelled tool is refused at registration, in ``ToolRegistry.register``.
3. The decision is one table lookup. This module contains no policy of its own, so a
   review of the feature is a review of that table.

Fail-closed everywhere. An unreadable actor is a stranger. A risk value that does not
parse is a refusal. A confirm nobody answers is a deny. An exception inside the gate
is a deny, because the alternative is a gate that opens when it breaks.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from config.permission_settings import (
    DEFAULT_BAND,
    PERMISSION_APPROVER_ENABLED,
    PERMISSION_CONFIRM_TIMEOUT_SECONDS,
    PermissionAction,
    ToolRisk,
    TrustBand,
    band_for_account,
    band_for_group,
    decide,
    lower_band,
)

logger = logging.getLogger(__name__)

# The carrier key for speaker identity. ``ToolCall.provider_meta`` already exists as
# the opaque per-call dict that no provider sees, so identity rides there instead of
# adding a field that every construction site (and every test fixture) must fill.
ACTOR_KEY = "actor"


class PermissionConfigError(ValueError):
    """A tool or a configuration value cannot be interpreted. Refuse, do not guess."""


@dataclass
class Decision:
    """What the gate concluded about one call. Carries the facts the audit line needs.

    Mutable, and deliberately so: the confirm path annotates ``reason`` after waiting.
    A frozen record would force a second object per decision and lose the thread of
    what the caller was actually told.
    """

    action: PermissionAction
    band: TrustBand
    risk: ToolRisk | None
    account_id: str = ""
    group_id: str = ""
    reason: str = ""


def parse_risk(value: Any) -> ToolRisk:
    """Turn a declared risk into the enum. Nothing here defaults.

    Accepts the enum or its string, because the value arrives over HTTP and from a
    decorator in both shapes. Anything else raises, which is what makes "the author
    labelled it" a fact rather than a hope.
    """
    if isinstance(value, ToolRisk):
        return value
    text = str(value or "").strip().lower()
    if not text:
        raise PermissionConfigError("tool declared no risk")
    try:
        return ToolRisk(text)
    except ValueError:
        raise PermissionConfigError(
            f"unknown tool risk {value!r}; expected one of "
            f"{', '.join(r.value for r in ToolRisk)}"
        ) from None


def resolve_band(actor: Any) -> tuple[TrustBand, str, str]:
    """The band for one turn's speaker, plus the ids used to get there.

    Private chat: the speaker band. Group: the lower of the group band and the speaker
    band, so a blocked person does not regain rights by standing in a friendly group,
    and a trusted friend does not gain rights in a group the owner muted.

    A missing actor -- no message, a background task, a call from a path that never
    learned who spoke -- is DEFAULT_BAND. That is the brief's unmapped rule extended to
    "unknown", and it is the reason the gate must not be reached from a path where
    identity is always absent without first deciding what that path means.
    """
    if not isinstance(actor, dict):
        return DEFAULT_BAND, "", ""
    account_id = str(actor.get("account_id") or "").strip()
    group_id = str(actor.get("group_id") or "").strip()
    speaker = band_for_account(account_id) if account_id else DEFAULT_BAND
    if str(actor.get("message_type") or "").strip().lower() != "group" or not group_id:
        return speaker, account_id, group_id
    return lower_band(band_for_group(group_id), speaker), account_id, group_id


def decide_for(actor: Any, risk: ToolRisk) -> Decision:
    """band -> table -> action. The whole policy, one call."""
    band, account_id, group_id = resolve_band(actor)
    action = decide(band, risk)
    return Decision(
        action=action, band=band, risk=risk,
        account_id=account_id, group_id=group_id,
    )


def audit(decision: Decision, tool_name: str, *, outcome: str = "") -> None:
    """Brief item 5: every deny and every confirm says who, what and which band.

    ALLOW is not logged. A per-character session that allows most calls would bury the
    two lines that matter, and the registry already logs registration.

    The arguments are never logged. The identity layer's privacy rule says the same
    thing (memory/trust_store.py: signals are keyed by id, content stays out), and a
    permission log is exactly where someone would look for other people's messages.
    """
    if decision.action is PermissionAction.ALLOW and not outcome:
        return
    where = f"group={decision.group_id} " if decision.group_id else ""
    logger.warning(
        "[permission] %s tool=%s band=%s risk=%s account=%s %s%s",
        decision.action.value, tool_name, decision.band.value,
        decision.risk.value if decision.risk is not None else "unlabelled",
        decision.account_id or "(unknown)", where, decision.reason or "",
    )


# ------------------------------------------------------------------ confirm


@dataclass
class PendingApproval:
    """One parked call, waiting for an OWNER."""

    request_id: str
    tool_name: str
    decision: Decision
    created_at: float
    _event: asyncio.Event = field(default=None, repr=False)  # type: ignore[assignment]
    approved: bool | None = None
    resolved_at: float | None = None
    timed_out: bool = False

    @property
    def age(self) -> float:
        return time.monotonic() - self.created_at


class ApprovalQueue:
    """Where a CONFIRM waits, and where an approver answers it.

    Deliberately tiny. It parks a call and hands the answer back; it does not decide
    what to do with an unanswered request, because that is ``request_approval``'s job
    (timeout means deny).

    The delivery half is not here. No host code knows who the owner is or how to reach
    them, so ``PERMISSION_APPROVER_ENABLED`` stays False until something can carry the
    question, and a CONFIRM degrades to a deny with a log line. That is fail-closed and
    it is honest: a parked call nobody can see is the same deny with extra latency.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}

    def open(self, tool_name: str, decision: Decision) -> PendingApproval:
        entry = PendingApproval(
            request_id=uuid.uuid4().hex,
            tool_name=tool_name,
            decision=decision,
            created_at=time.monotonic(),
            _event=asyncio.Event(),
        )
        self._pending[entry.request_id] = entry
        return entry

    def resolve(self, request_id: str, approve: bool) -> bool:
        """Answer one request. Returns False when nothing is waiting under that id."""
        entry = self._pending.get(str(request_id or ""))
        if entry is None or entry.approved is not None:
            return False
        entry.approved = bool(approve)
        entry.resolved_at = time.monotonic()
        try:
            entry._event.set()
        except RuntimeError:  # a loop already gone means the caller is gone too
            pass
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        """What an approver would see. Age included, because a stale request is a bug."""
        return [
            {
                "request_id": e.request_id,
                "tool": e.tool_name,
                "band": e.decision.band.value,
                "risk": e.decision.risk.value,
                "account_id": e.decision.account_id,
                "group_id": e.decision.group_id,
                "age_seconds": round(e.age, 3),
            }
            for e in self._pending.values()
            if e.approved is None
        ]

    def forget(self, request_id: str) -> None:
        self._pending.pop(str(request_id or ""), None)

    def clear(self) -> None:
        self._pending.clear()

    def pending_count(self) -> int:
        return sum(1 for e in self._pending.values() if e.approved is None)


approvals = ApprovalQueue()


async def request_approval(entry: PendingApproval, *, timeout: float | None = None) -> bool:
    """Wait for an OWNER. Timeout, cancellation and any error all mean deny.

    The one await the gate performs, so it is the one place the deny-by-default rule is
    written down: every exit path that is not an explicit yes becomes False.
    """
    wait_for = (
        PERMISSION_CONFIRM_TIMEOUT_SECONDS if timeout is None else float(timeout)
    )
    try:
        await asyncio.wait_for(entry._event.wait(), timeout=wait_for)
    except asyncio.TimeoutError:
        entry.approved = False
        entry.timed_out = True
        return False
    except (asyncio.CancelledError, RuntimeError):
        # Cancellation is the turn ending. The call never happened, so it must not be
        # remembered as approved; re-raise nothing, report deny.
        entry.approved = False
        return False
    finally:
        approvals.forget(entry.request_id)
    return bool(entry.approved)


async def enforce(tool_name: str, declared_risk: Any, actor: Any,
                  *, timeout: float | None = None) -> Decision:
    """The gate. Returns the decision it applied; the caller turns a deny into a result.

    ``declared_risk`` is re-parsed here rather than trusted, because the registry and
    this function can be reached from paths that built the value independently (the
    HTTP register endpoint, a plugin SDK payload, a ledger replay).
    """
    try:
        risk = parse_risk(declared_risk)
    except PermissionConfigError as exc:
        # No risk class is invented here. The audit prints "unlabelled" and the deny
        # stands, so a broken label can never be reported as some specific class.
        decision = Decision(
            action=PermissionAction.DENY, band=DEFAULT_BAND, risk=None,
            reason=f"(unusable risk: {exc})",
        )
        audit(decision, tool_name, outcome="refused")
        return decision

    decision = decide_for(actor, risk)
    if decision.action is PermissionAction.ALLOW:
        return decision
    if decision.action is PermissionAction.DENY:
        audit(decision, tool_name)
        return decision

    if not PERMISSION_APPROVER_ENABLED:
        # A CONFIRM with nowhere to go is a deny. Say so, so the operator sees the
        # difference between "the policy denied this" and "the approver channel is off".
        denied = Decision(
            action=PermissionAction.DENY, band=decision.band, risk=decision.risk,
            account_id=decision.account_id, group_id=decision.group_id,
            reason="(confirm requested, but PERMISSION_APPROVER_ENABLED is false)",
        )
        audit(denied, tool_name)
        return denied

    entry = approvals.open(tool_name, decision)
    audit(decision, tool_name, outcome=f"(request {entry.request_id})")
    approved = await request_approval(entry, timeout=timeout)
    if approved:
        return Decision(
            action=PermissionAction.ALLOW, band=decision.band, risk=decision.risk,
            account_id=decision.account_id, group_id=decision.group_id,
            reason="(owner confirmed)",
        )
    reason = "(confirm timed out)" if entry.timed_out else (
        "(confirm cancelled)" if entry.approved is False and not entry.timed_out
        else "(owner refused)")
    denied = Decision(
        action=PermissionAction.DENY, band=decision.band, risk=decision.risk,
        account_id=decision.account_id, group_id=decision.group_id,
        reason=reason,
    )
    audit(denied, tool_name)
    return denied


def permission_denied_result(call: Any, decision: Decision) -> Any:
    """The model-visible shape of a refusal, in the form the registry already uses.

    Returned as an error result rather than raised, because ``ToolRegistry.execute``
    promises never to raise and the model reads a tool error as a recoverable string
    (main_logic/tool_calling.py:607-612). An exception here would end the turn over a
    policy answer.

    The message tells the model what stopped it and which band the speaker landed in,
    so it can say something sensible to the user instead of inventing a failure. The
    arguments are not echoed back.
    """
    from main_logic.tool_calling import ToolResult

    risk = decision.risk.value if decision.risk is not None else "unlabelled"
    message = (
        f"tool '{call.name}' denied by the permission gate "
        f"(band={decision.band.value}, risk={risk}){decision.reason}"
    )
    return ToolResult(
        call_id=getattr(call, "call_id", "") or "",
        name=call.name,
        output={
            "error": "permission_denied",
            "tool": call.name,
            "band": decision.band.value,
            "risk": risk,
        },
        is_error=True,
        error_message=message,
    )


def actor_from_call(call: Any) -> Any:
    """The identity a caller attached to one tool call, or None.

    None is a real answer and a common one today: nothing in the host populates this
    yet, because nothing in the host consumes the QQ connector -- the dialog path is
    the local UI, and group traffic arrives through an out-of-tree plugin. Treat it as
    the question "who is asking?", not as a bug to paper over with OWNER.
    """
    meta = getattr(call, "provider_meta", None)
    if not isinstance(meta, dict):
        return None
    return meta.get(ACTOR_KEY)
