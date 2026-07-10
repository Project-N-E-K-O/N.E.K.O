# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Independent lifecycle state for the new-user icebreaker.

The icebreaker is an onboarding conversation, not a mini-game. Keeping its
session state separate from ``utils.game_route_state`` prevents ordinary PC
reload reconciliation from treating an icebreaker as an open game window.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict


_icebreaker_route_states: Dict[str, dict] = {}
_icebreaker_route_locks: Dict[str, asyncio.Lock] = {}


def _icebreaker_state_key(lanlan_name: str) -> str:
    return str(lanlan_name or "").strip()


def _get_icebreaker_route_lock(lanlan_name: str) -> asyncio.Lock:
    key = _icebreaker_state_key(lanlan_name)
    lock = _icebreaker_route_locks.get(key)
    if lock is None:
        lock = _icebreaker_route_locks.setdefault(key, asyncio.Lock())
    return lock


def _public_icebreaker_route_state(state: dict | None) -> dict:
    if not state:
        return {"icebreaker_active": False}
    return {k: v for k, v in state.items() if not str(k).startswith("_")}


def _get_active_icebreaker_route_state(lanlan_name: str) -> dict | None:
    state = _icebreaker_route_states.get(_icebreaker_state_key(lanlan_name))
    return state if state and state.get("icebreaker_active") else None


def is_icebreaker_route_active(lanlan_name: str) -> bool:
    return _get_active_icebreaker_route_state(lanlan_name) is not None


def get_active_icebreaker_route_session_id(lanlan_name: str) -> str:
    state = _get_active_icebreaker_route_state(lanlan_name)
    return str(state.get("session_id") or "") if state else ""


def activate_icebreaker_route(lanlan_name: str, session_id: str) -> dict:
    now = time.time()
    state = {
        "icebreaker_active": True,
        "lanlan_name": _icebreaker_state_key(lanlan_name),
        "session_id": str(session_id or "default"),
        "started_at": now,
        "last_activity": now,
        "source": "new_user_icebreaker",
    }
    _icebreaker_route_states[_icebreaker_state_key(lanlan_name)] = state
    return state


def touch_icebreaker_route(state: dict | None) -> None:
    if state:
        state["last_activity"] = time.time()


def finalize_icebreaker_route(lanlan_name: str, session_id: str = "", reason: str = "") -> dict | None:
    key = _icebreaker_state_key(lanlan_name)
    state = _icebreaker_route_states.get(key)
    if not state:
        return None
    if session_id and str(state.get("session_id") or "") != str(session_id):
        return state
    state["icebreaker_active"] = False
    state["ended_at"] = time.time()
    state["end_reason"] = str(reason or "icebreaker_end")
    _icebreaker_route_states.pop(key, None)
    return state
