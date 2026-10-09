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

"""Shared visit-route state primitives (sibling of ``utils/game_route_state.py``).

Lives in ``utils/`` so that both ``main_logic/`` and ``main_routers/`` can
read whether a character is out visiting (or hosting a visitor) without a
reverse-direction import. The visit runtime owns the heavy lifecycle; this
module only holds the per-character state container and its lock.

One character is in at most one visit at a time (OD-03), so the key is the
bare ``lanlan_name``. A slot exists from the moment a visit is reserved
(``phase='pending'``, before credentials are fetched) until it is finalized:
a pending placeholder already counts as active so that a second room or a
mini game cannot grab the same character in between.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Dict, Optional
from weakref import WeakValueDictionary

VISIT_ROUTE_KIND = "neko_visit"
"""External route kind (and takeover owner) of the visit runtime."""

VISIT_SOCKET_BOUND_ATTR = "neko_visit_authorized"
"""Attribute set on a display-socket connection object once it passed ``visit_bind`` (design §4.5)."""

DISPLAY_SOCKET_VISIT_BOUND: ContextVar[Optional[bool]] = ContextVar("neko_display_socket_visit_bound", default=None)
"""Set by ``websocket_router`` around a ``stream_data`` dispatch: whether that connection passed ``visit_bind``."""

VISIT_SOCKET_DELIVERED_ATTR = "neko_visit_delivered_debrief"
"""Attribute holding the debrief block request ids this display-socket connection acknowledged."""

VISIT_SOCKET_SENT_ATTR = "neko_visit_sent_debrief"
"""Attribute holding the debrief block request ids written on this display-socket connection."""

_visit_route_states: Dict[str, dict] = {}

# Per-character lock registry. Entries outlive the state slot on purpose: a
# new reservation racing against the tail of a finalize must serialize on the
# same instance. Weak values release idle keys once nobody references them.
_visit_route_locks: WeakValueDictionary[str, "asyncio.Lock"] = WeakValueDictionary()


def _key(lanlan_name: str) -> str:
    return str(lanlan_name or "")


def activate_visit_route(
    lanlan_name: str, *, phase: str = "pending", visit_id: Optional[str] = None,
) -> dict:
    """Create (or replace) the visit-route slot of a character and return it.

    The returned dict is the live state object; the runtime mutates it in
    place (``phase`` moves through ``pending`` → ... → ``ending``). A replaced
    slot's dict is marked ``visit_route_active=False``, exactly like
    :func:`finalize_visit_route_state`, so tasks still holding it see the flip.
    ``visit_id`` identifies the visit owning the slot (the slot is keyed by
    character only); writers that act on behalf of one visit -- e.g. the
    transport WS caching ``caps_preflight`` -- only touch a slot whose
    ``visit_id`` matches theirs.
    """
    state = {
        "visit_route_active": True,
        "lanlan_name": _key(lanlan_name),
        "phase": phase,
        "visit_id": visit_id,
    }
    previous = _visit_route_states.get(_key(lanlan_name))
    if previous is not None:
        previous["visit_route_active"] = False
    _visit_route_states[_key(lanlan_name)] = state
    return state


def get_visit_route_state(lanlan_name: str) -> Optional[dict]:
    """Return the active slot of a character, or ``None``."""
    state = _visit_route_states.get(_key(lanlan_name))
    return state if state and state.get("visit_route_active") else None


def is_visit_route_active(lanlan_name: str) -> bool:
    """True iff the character holds a visit slot (a pending placeholder counts)."""
    return get_visit_route_state(lanlan_name) is not None


def _get_visit_route_lock(lanlan_name: str) -> "asyncio.Lock":
    """Return (and lazily create) the per-character visit-route lock.

    ``get`` + ``setdefault`` run synchronously without ``await``, so racing
    coroutines on one loop always observe the same instance.
    """
    key = _key(lanlan_name)
    lock = _visit_route_locks.get(key)
    if lock is None:
        lock = _visit_route_locks.setdefault(key, asyncio.Lock())
    return lock


def finalize_visit_route_state(lanlan_name: str) -> Optional[dict]:
    """Drop the slot of a character and return the removed state (or ``None``).

    The removed dict is marked inactive so that any holder of a stale
    reference observes the flip as well.
    """
    state = _visit_route_states.pop(_key(lanlan_name), None)
    if state is not None:
        state["visit_route_active"] = False
    return state


def _reset_for_tests() -> None:
    """Clear every slot (unit tests only; locks are weak and clear themselves)."""
    _visit_route_states.clear()
