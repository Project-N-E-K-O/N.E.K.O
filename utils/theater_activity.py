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

"""In-memory record of characters whose theater session a client is driving.

This is a cheap server-side backstop for the frontend's own theater guards
(``isProactiveChatSuppressed`` / ``blocksOrdinaryVoice``): proactive chat and
ordinary voice (start and PCM frames) consult :func:`is_theater_active` so a
client that missed the frontend suppression still cannot interleave ordinary
chat with a running performance.

The signal is deliberately lossy and fails open:

- it is refreshed by successful theater session requests (launch, restore,
  input, resume) and cleared by end, by an ended snapshot, and by the capsule's
  explicit owner release on exit. Ending clears all owners of that Session's
  lifecycle, including old windows, while other Sessions keep their guard;
- every entry expires ``THEATER_ACTIVITY_TTL_SECONDS`` after the last refresh,
  so a crashed or closed theater window can never block ordinary voice or
  proactive chat for longer than that;
- it lives only in process memory, so a restart forgets it, and a character
  that never opened the theater never appears here (zero behaviour change).

Lives in ``utils/`` so the theater router, the proactive-chat router and the
WebSocket router can share it without importing each other.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

THEATER_ACTIVITY_TTL_SECONDS = 120.0

# lanlan_name -> monotonic timestamp of the last theater request that proved a
# client was driving that character's session.
_last_activity: dict[str, float] = {}
_activity_claims: dict[str, dict[str, float]] = {}
_released_claims: dict[str, float] = {}
# Session identity and lifecycle fence keep an ended Session from leaving
# orphaned owners, without releasing a different Session of the same character.
_claim_sessions: dict[str, tuple[str, str, int]] = {}
_legacy_sessions: dict[str, tuple[str, str, int]] = {}
_ended_sessions: dict[tuple[str, str], tuple[int, float]] = {}


def _key(lanlan_name: Any) -> str:
    return str(lanlan_name or "").strip()


def mark_theater_activity(lanlan_name: Any, *, now: float | None = None, activity_claim_id: str = "") -> bool:
    """Record that a client is currently driving ``lanlan_name``'s theater session."""

    key = _key(lanlan_name)
    current = time.monotonic() if now is None else now
    for claim, stamp in list(_released_claims.items()):
        if current - stamp >= THEATER_ACTIVITY_TTL_SECONDS * 5:
            _released_claims.pop(claim, None)
    if activity_claim_id and activity_claim_id in _released_claims:
        return False
    if key:
        if activity_claim_id:
            _claim_sessions.pop(activity_claim_id, None)
            _activity_claims.setdefault(key, {})[activity_claim_id] = current
        else:
            _legacy_sessions.pop(key, None)
            _last_activity[key] = current
        return True
    return False


def clear_theater_activity(lanlan_name: Any, *, activity_claim_id: str = "") -> None:
    """Release one owner, or the legacy unowned signal; preserve peer owners."""

    if activity_claim_id:
        # Fence a release that overtakes the corresponding GET/start response.
        now = time.monotonic()
        for claim, stamp in list(_released_claims.items()):
            if now - stamp >= THEATER_ACTIVITY_TTL_SECONDS * 5:
                _released_claims.pop(claim, None)
        _released_claims[activity_claim_id] = now
        _claim_sessions.pop(activity_claim_id, None)
        for key, claims in list(_activity_claims.items()):
            claims.pop(activity_claim_id, None)
            if not claims:
                _activity_claims.pop(key, None)
        return
    _last_activity.pop(_key(lanlan_name), None)
    _legacy_sessions.pop(_key(lanlan_name), None)


def clear_all_theater_activity() -> None:
    """Forget every character's theater activity (process reset and test isolation only)."""

    _last_activity.clear()
    _activity_claims.clear()
    _released_claims.clear()
    _claim_sessions.clear()
    _legacy_sessions.clear()
    _ended_sessions.clear()


def is_theater_active(lanlan_name: Any, *, now: float | None = None) -> bool:
    """Return True while ``lanlan_name`` had theater activity within the TTL."""

    key = _key(lanlan_name)
    current = time.monotonic() if now is None else now
    claims = _activity_claims.get(key, {})
    for claim, stamp in list(claims.items()):
        if current - stamp >= THEATER_ACTIVITY_TTL_SECONDS:
            claims.pop(claim, None)
            _claim_sessions.pop(claim, None)
    if claims:
        return True
    _activity_claims.pop(key, None)
    last = _last_activity.get(key) if key else None
    if last is None:
        return False
    current = time.monotonic() if now is None else now
    if current - last < THEATER_ACTIVITY_TTL_SECONDS:
        return True
    # Expired entries are dropped so stale state can never outlive its TTL.
    _last_activity.pop(key, None)
    _legacy_sessions.pop(key, None)
    return False


def can_retire_cancelled_start(story_id: str, session_id: str, lifecycle: int, claim_id: str) -> bool:
    """A late cancelled start must not retire a Session claimed by another host."""
    now = time.monotonic()
    scope = (story_id, session_id, lifecycle)
    for claims in _activity_claims.values():
        for owner, stamp in claims.items():
            if owner != claim_id and now - stamp < THEATER_ACTIVITY_TTL_SECONDS and _claim_sessions.get(owner) == scope:
                return False
    for name, owned in _legacy_sessions.items():
        if owned == scope and now - _last_activity.get(name, 0) < THEATER_ACTIVITY_TTL_SECONDS:
            return False
    return True


def note_theater_session_response(response: Any, *, activity_claim_id: str = "") -> bool:
    """Update the registry from a successful theater session payload.

    Only dict payloads with ``ok: True`` carrying both ``session`` and
    ``participants`` count; error responses (``JSONResponse``) are ignored so a
    failed request neither refreshes nor clears the signal.
    """

    if not isinstance(response, Mapping) or response.get("ok") is not True:
        return False
    session = response.get("session")
    participants = response.get("participants")
    if not isinstance(session, Mapping) or not isinstance(participants, Mapping):
        return False
    name = _key(participants.get("catgirl_name"))
    if not name:
        return False
    story_id = _key(session.get("story_package_id"))
    session_id = _key(session.get("session_id"))
    lifecycle = session.get("lifecycle_revision", 0)
    if not isinstance(lifecycle, int) or isinstance(lifecycle, bool):
        lifecycle = 0
    scope = (story_id, session_id, lifecycle)
    identity = (story_id, session_id)
    now = time.monotonic()
    for ended_identity, (_, stamp) in list(_ended_sessions.items()):
        if now - stamp >= THEATER_ACTIVITY_TTL_SECONDS * 5:
            _ended_sessions.pop(ended_identity, None)
    if session.get("status") == "ended":
        if session_id:
            previous = _ended_sessions.get(identity)
            _ended_sessions[identity] = (max(lifecycle, previous[0] if previous else lifecycle), now)
            for claim, owned in list(_claim_sessions.items()):
                if owned[:2] == identity and owned[2] <= lifecycle:
                    clear_theater_activity(name, activity_claim_id=claim)
            for legacy_name, owned in list(_legacy_sessions.items()):
                if owned[:2] == identity and owned[2] <= lifecycle:
                    clear_theater_activity(legacy_name)
            # Fence the ending request's owner even if its GET is still in
            # flight, but do not clear a newer lifecycle or another Session.
            owned = _claim_sessions.get(activity_claim_id)
            if activity_claim_id and (owned is None or (owned[:2] == identity and owned[2] <= lifecycle)):
                clear_theater_activity(name, activity_claim_id=activity_claim_id)
            return True
        clear_theater_activity(name, activity_claim_id=activity_claim_id)
        return True
    ended = _ended_sessions.get(identity) if session_id else None
    if ended and lifecycle <= ended[0]:
        return False
    claimed = mark_theater_activity(name, activity_claim_id=activity_claim_id)
    if claimed and session_id:
        if activity_claim_id:
            _claim_sessions[activity_claim_id] = scope
        else:
            _legacy_sessions[name] = scope
    return claimed


__all__ = [
    "THEATER_ACTIVITY_TTL_SECONDS",
    "clear_all_theater_activity",
    "clear_theater_activity",
    "is_theater_active",
    "mark_theater_activity",
    "note_theater_session_response",
]
