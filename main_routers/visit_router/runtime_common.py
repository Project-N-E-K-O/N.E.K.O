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

"""Constants and reason tables shared by the visit runtime modules.

Phases (``GET /api/visit/state`` ``phase`` and ``visit_state_change``):
``pending`` (slot reserved, iframe and capability gate), ``invite_ready``
(host in the vendor room, invite code out), ``joining`` (guest in the room),
``awaiting_accept`` (peer verified, before ``ready``), ``active``,
``wrap_up``, ``ending`` (finalize running), ``ended``.

Reason tables (design §3.6.8, §4.2 ``leave``): every finalize reason either
maps to a ``leave.reason`` or sends no ``leave``; every received
``leave.reason`` maps back into the finalize set.
"""

from __future__ import annotations

from typing import Optional

PHASE_PENDING = "pending"
PHASE_INVITE_READY = "invite_ready"
PHASE_JOINING = "joining"
PHASE_AWAITING = "awaiting_accept"
PHASE_ACTIVE = "active"
PHASE_WRAP_UP = "wrap_up"
PHASE_ENDING = "ending"
PHASE_ENDED = "ended"

WAITING_PHASES = frozenset({PHASE_PENDING, PHASE_INVITE_READY, PHASE_JOINING, PHASE_AWAITING})
"""Before ``ready``: no isolated session, no ``VisitRoom``; the family cannot talk to the visit."""

FINALIZE_REASONS = frozenset({
    "route_end", "recall", "wrap_up", "peer_left", "peer_lost", "relay_lost", "local_page_lost",
    "declined", "idle_timeout", "max_duration", "max_lines", "character_switch", "manager_replaced",
    "llm_error", "peer_protocol_violation", "delivery_failed", "peer_identity_rejected",
    "peer_blocked", "proto_mismatch", "kicked", "goodbye", "shutdown", "invite_expired",
    "unsupported", "visit_disabled",
})
"""Every reason a visit can end with once it started (design §3.6.8)."""

ABORT_REASONS = frozenset({
    "busy", "login_required", "banned", "quota_exceeded", "tier_not_entitled",
    "cross_region_unsupported", "invite_invalid", "invite_expired", "servers_unreachable",
})
"""Endings before any peer was verified that §3.6.8 does not list (Servers / admission failures)."""

LEAVE_REASON_FOR: dict[str, str] = {
    "idle_timeout": "ended",
    "max_lines": "ended",
    "max_duration": "ended",
    "route_end": "ended",
    "recall": "ended",
    "wrap_up": "wrapup",
    "llm_error": "error",
    "manager_replaced": "error",
    "character_switch": "character_changed",
    "peer_blocked": "peer_identity_rejected",
    "declined": "declined",
    "goodbye": "goodbye",
    "visit_disabled": "visit_disabled",
    "delivery_failed": "delivery_failed",
    "peer_protocol_violation": "peer_protocol_violation",
    "peer_identity_rejected": "peer_identity_rejected",
    "proto_mismatch": "proto_mismatch",
    "shutdown": "shutdown",
}
"""Finalize reason → ``leave.reason`` (§4.2 ``leave``)."""

NO_LEAVE_REASONS = frozenset({
    "peer_left", "peer_lost", "relay_lost", "kicked", "invite_expired", "unsupported", "local_page_lost",
})
"""Finalize reasons that send no ``leave``: the peer or the data channel is already gone."""

PEER_LEFT_REASONS = frozenset({
    "home", "wrapup", "ended", "character_changed", "error", "shutdown", "visit_disabled",
})
_PEER_LEAVE_SAME = frozenset({
    "declined", "goodbye", "delivery_failed", "proto_mismatch", "peer_identity_rejected",
    "peer_protocol_violation",
})


def finalize_reason_for_peer_leave(leave_reason: object) -> str:
    """Map a received ``leave.reason`` into the finalize set (unknown → ``peer_left``)."""
    if isinstance(leave_reason, str) and leave_reason in _PEER_LEAVE_SAME:
        return leave_reason
    return "peer_left"


def leave_reason_for(reason: str, *, side: str, done_received: bool = False) -> Optional[str]:
    """``leave.reason`` this side sends when it finalizes with ``reason``; None = no ``leave``.

    A guest going home after the host's ``wrap_up{done}`` says ``home``.
    """
    if reason in NO_LEAVE_REASONS or reason in ABORT_REASONS:
        return None
    if reason == "wrap_up" and side == "guest" and done_received:
        return "home"
    return LEAVE_REASON_FOR.get(reason)


STATUS_FOR_REASON: dict[str, str] = {
    "peer_lost": "VISIT_PEER_LOST",
    "relay_lost": "VISIT_RELAY_LOST",
    "local_page_lost": "VISIT_RELAY_LOST",
    "kicked": "VISIT_KICKED",
    "proto_mismatch": "VISIT_PROTO_MISMATCH",
    "peer_identity_rejected": "VISIT_PEER_IDENTITY_REJECTED",
    "peer_blocked": "VISIT_PEER_IDENTITY_REJECTED",
    "unsupported": "VISIT_UNSUPPORTED_ON_THIS_MACHINE",
    "servers_unreachable": "VISIT_SERVERS_UNREACHABLE",
    "login_required": "VISIT_LOGIN_REQUIRED",
    "banned": "VISIT_BANNED",
    "quota_exceeded": "VISIT_QUOTA_EXCEEDED",
    "tier_not_entitled": "VISIT_TIER_NOT_ENTITLED",
    "cross_region_unsupported": "VISIT_CROSS_REGION_UNSUPPORTED",
    "invite_invalid": "VISIT_INVITE_INVALID",
    "invite_expired": "VISIT_INVITE_INVALID",
    "busy": "VISIT_E_BUSY",
}
"""Toast code pushed with ``visit_state_change{ended}`` for the reasons the user should be told about."""

NATURAL_REASONS = frozenset({"recall", "wrap_up", "max_duration"})
"""Endings whose home-coming line is generated (everything else says a fixed line, no LLM)."""

_FIXED_LINE_KIND = {
    "peer_left": "disconnect",
    "peer_lost": "disconnect",
    "relay_lost": "disconnect",
    "local_page_lost": "disconnect",
    "kicked": "disconnect",
    "delivery_failed": "disconnect",
    "character_switch": "switch",
    "manager_replaced": "switch",
    "shutdown": "shutdown",
    "goodbye": "goodbye",
}


def fixed_line_kind(reason: str) -> str:
    """``VISIT_FIXED_LINE`` key for a reason that does not generate its home-coming line."""
    return _FIXED_LINE_KIND.get(reason, "ended")


EXPLICIT_VENDOR_LEAVES = frozenset({"0", "CLIENT_INITIATED", "client_initiated"})
"""``state.vendor_reason`` values of an explicit peer leave (TRTC reason 0, LiveKit client disconnect)."""

KICKED_VENDOR_REASONS = frozenset({"banned", "room_disband"})
"""``state{kicked}`` reasons that end the visit at once (``kick`` = same id signed in again)."""


def addressee_code(side: str, kind: str) -> str:
    """Wire ``ad``: side initial + kind initial (``'gc'`` = the guest's cat)."""
    return ("h" if side == "host" else "g") + ("c" if kind == "cat" else "h")


def decode_addressee(ad: object) -> tuple[Optional[str], Optional[str]]:
    """``(side, kind)`` of a wire ``ad`` (``(None, None)`` when malformed)."""
    if not isinstance(ad, str) or len(ad) != 2 or ad[0] not in "hg" or ad[1] not in "ch":
        return None, None
    return ("host" if ad[0] == "h" else "guest"), ("cat" if ad[1] == "c" else "human")


def other_side(side: str) -> str:
    return "guest" if side == "host" else "host"


def side_prefix(side: str) -> str:
    return "h:" if side == "host" else "g:"


def side_of_ln(ln: object) -> Optional[str]:
    """Side that owns a line id (``h:`` / ``g:`` prefix), or None."""
    if isinstance(ln, str):
        if ln.startswith("h:"):
            return "host"
        if ln.startswith("g:"):
            return "guest"
    return None
