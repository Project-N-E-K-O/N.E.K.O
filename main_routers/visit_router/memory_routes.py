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

"""Visit memory management endpoints: peer list, local forget, blocklist.

Design: ``docs/design/visit-infrastructure.md`` section 4.6
``GET /api/visit/memory/peers`` and ``POST /api/visit/memory/forget |
forget_all | contacts/block``. Decorators use paths relative to the visit
router (``prefix='/api/visit'`` is added where the router is included).

Every endpoint, the read one included, passes the local-origin gate: the
peer address must be loopback (no proxy mode, no forwarding headers) unless
``NEKO_VISIT_ALLOW_NONLOCAL`` is on, and then the usual Origin / Host +
CSRF check. Not affected by the ``NEKO_VISIT_ENABLED`` release switch (data
management keeps working).

Runtime hooks (:func:`configure_memory_routes`, wired by PR-09b): the
signed-in account's ``visit_uid``, whether a character is visiting right now,
the per-character admission lock and the in-visit block reaction. Until they
are wired the account is unknown: the list is empty and changes answer
``VISIT_LOGIN_REQUIRED``.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.visit_settings import NEKO_VISIT_ALLOW_NONLOCAL, VISIT_MEMORY_PLATFORM
from main_logic.visit import local_chars, memory_bridge
from main_logic.visit.forget_runner import AdmissionLock, VisitActive, forget_all, forget_person
from main_logic.visit.limits import Blocklist, BlocklistUnavailable
from main_logic.visit.subjects import (
    PeerRoster,
    RosterCorruptError,
    derive_person_id,
    derive_short_code,
    group_chat_subject,
    group_participant_subject,
    participant_subject,
)
from main_routers.system_router._shared import _read_json_object, _validate_local_mutation_request
from memory.scoped_client import ScopedMemoryClient, ScopedMemoryError
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

router = APIRouter()

_FORWARDING_HEADERS = ("forwarded", "x-forwarded-for", "x-real-ip")
_UID_MAX_LEN = 64


@dataclass
class MemoryRouteHooks:
    """Runtime hooks of the memory endpoints (see :func:`configure_memory_routes`)."""

    own_visit_uid: Callable[[], Awaitable[str | None]]
    is_visit_active: Callable[[str], bool]
    admission_lock: AdmissionLock | None
    on_blocked: Callable[[str], Awaitable[Any]] | None
    config_dir: Callable[[], Path]
    client: Callable[[], ScopedMemoryClient]


async def _no_account() -> str | None:
    return None


def _default_config_dir() -> Path:
    from utils.config_manager import get_config_manager

    return Path(get_config_manager().config_dir)


_hooks = MemoryRouteHooks(
    own_visit_uid=_no_account,
    is_visit_active=lambda _name: False,
    admission_lock=None,
    on_blocked=None,
    config_dir=_default_config_dir,
    client=memory_bridge.default_client,
)


def configure_memory_routes(**hooks: Any) -> None:
    """Replace runtime hooks: ``own_visit_uid``, ``is_visit_active``, ``admission_lock``,
    ``on_blocked``, ``config_dir``, ``client`` (unknown names raise ``TypeError``)."""
    for name, value in hooks.items():
        if not hasattr(_hooks, name):
            raise TypeError(f"unknown memory route hook {name!r}")
        setattr(_hooks, name, value)


def _error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, **extra}, status_code=status)


def _behind_proxy() -> bool:
    return os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def local_visit_gate(request: Request, payload: dict | None = None) -> JSONResponse | None:
    """Return a 403 response unless the request comes from this machine's own UI.

    Primary gate: the real peer address is loopback, proxy mode is off and no
    forwarding header is present (all skipped when ``NEKO_VISIT_ALLOW_NONLOCAL``
    is on). Second layer: the shared Origin / Host + CSRF check.
    """
    if not NEKO_VISIT_ALLOW_NONLOCAL:
        client_host = request.client.host if request.client else None
        if (
            _behind_proxy()
            or any(header in request.headers for header in _FORWARDING_HEADERS)
            or not _is_loopback(client_host)
        ):
            return _error(403, "VISIT_E_UNAUTHORIZED")
    return _validate_local_mutation_request(request, payload=payload)


def _clean_uid(value: Any) -> str | None:
    if isinstance(value, str) and value and len(value) <= _UID_MAX_LEN and value.isprintable():
        return value
    return None


async def _subject_counts(name: str) -> dict[str, dict]:
    try:
        rows = await memory_bridge.list_visit_subjects(name, client=_hooks.client())
    except ScopedMemoryError as exc:
        logger.warning("visit memory peers: scoped_subjects unavailable: %s", exc)
        return {}
    return {
        f"{row.get('subject_kind')}:{row.get('subject_id')}": row
        for row in rows
        if str(row.get("subject_id", "")).split(":", 1)[0] == VISIT_MEMORY_PLATFORM
    }


def _count(rows: dict[str, dict], subject: dict, field: str) -> int:
    row = rows.get(f"{subject['subject_kind']}:{subject['subject_id']}") or {}
    value = row.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


@router.get("/memory/peers")
async def list_memory_peers(request: Request, catgirl: str = ""):
    """Every person ``catgirl`` has visited with, under the signed-in account."""
    denied = local_visit_gate(request)
    if denied is not None:
        return denied
    if not catgirl:
        return _error(400, "catgirl_required")
    own_uid = await _hooks.own_visit_uid()
    if not own_uid:
        return JSONResponse({"peers": []})
    config_dir = _hooks.config_dir()
    roster = PeerRoster(config_dir, own_uid=own_uid)
    peers = await roster.list_peers()
    try:
        blocklist = await Blocklist.aload(config_dir)
        blocked = {entry.visit_uid for entry in blocklist.entries()} if blocklist.available else set()
    except BlocklistUnavailable:
        blocked = set()
    counts = await _subject_counts(catgirl)
    out = []
    for peer_uid, peer in sorted(peers.items()):
        by_char = peer.get("by_char")
        entry = by_char.get(catgirl) if isinstance(by_char, dict) else None
        if not isinstance(entry, dict):
            continue
        pairs = [p for p in entry.get("pairs") or [] if isinstance(p, str)]
        chars = entry.get("chars") if isinstance(entry.get("chars"), dict) else {}
        person = participant_subject(derive_person_id(own_uid, peer_uid))
        subjects = [person] + [group_chat_subject(p) for p in pairs]
        char_rows = []
        for char_id, info in sorted(chars.items()):
            for pair_id in pairs:
                subject = group_participant_subject(pair_id, char_id)
                subjects.append(subject)
                char_rows.append({
                    "peer_char_id": char_id,
                    "display_name": str(info.get("display_name") or ""),
                    "pair_id": pair_id,
                    "last_visit_at": info.get("last_seen"),
                    "fact_count": _count(counts, subject, "facts"),
                })
        visits = entry.get("visits")
        out.append({
            "peer_uid": peer_uid,
            "short_id": derive_short_code(peer_uid),
            "display_name": str(peer.get("display_name") or ""),
            "first_seen": peer.get("first_seen"),
            "last_seen": peer.get("last_seen"),
            "visits": visits if isinstance(visits, int) and not isinstance(visits, bool) else 0,
            "blocked": peer_uid in blocked,
            "fact_count": sum(_count(counts, s, "facts") for s in subjects),
            "reflection_count": sum(_count(counts, s, "reflections") for s in subjects),
            "chars": char_rows,
        })
    return JSONResponse({"peers": out})


@router.post("/memory/forget")
async def forget_memory_peer(request: Request):
    """"Forget this person" under one local character (local visit memory only)."""
    payload = await _read_json_object(request)
    denied = local_visit_gate(request, payload)
    if denied is not None:
        return denied
    catgirl = payload.get("catgirl")
    peer_uid = _clean_uid(payload.get("peer_uid"))
    if not isinstance(catgirl, str) or not catgirl or peer_uid is None:
        return _error(400, "invalid_request")
    own_uid = await _hooks.own_visit_uid()
    if not own_uid:
        return _error(409, "VISIT_LOGIN_REQUIRED")
    if _hooks.is_visit_active(catgirl):
        return _error(409, "visit_active")
    char_uid = await local_chars.resolve_char_uid(catgirl)
    if char_uid is None:
        return _error(404, "unknown_catgirl")
    try:
        outcome = await forget_person(
            _hooks.config_dir(), own_uid=own_uid, own_char=catgirl, own_char_uid=char_uid,
            peer_uid=peer_uid, client=_hooks.client(), admission_lock=_hooks.admission_lock,
            is_visit_active=_hooks.is_visit_active,
        )
    except VisitActive:
        return _error(409, "visit_active")
    except (RosterCorruptError, OSError, ValueError) as exc:
        logger.error("visit forget failed before execution: %r", exc)
        return _error(503, "forget_failed", retry=True)
    if not outcome.done:
        # 撤销日志已落盘，启动补录与重试会补完剩余步骤
        return _error(503, "forget_pending", retry=True)
    return JSONResponse({"ok": True, "forgotten": outcome.forgotten})


@router.post("/memory/forget_all")
async def forget_all_memory(request: Request):
    """"Forget everyone": under one local character, or under every local character."""
    payload = await _read_json_object(request)
    denied = local_visit_gate(request, payload)
    if denied is not None:
        return denied
    catgirl = payload.get("catgirl")
    if catgirl is not None and (not isinstance(catgirl, str) or not catgirl):
        return _error(400, "invalid_request")
    own_uid = await _hooks.own_visit_uid()
    if not own_uid:
        return _error(409, "VISIT_LOGIN_REQUIRED")
    chars = await local_chars.load_local_characters()
    if catgirl is not None:
        if catgirl not in chars:
            return _error(404, "unknown_catgirl")
        chars = {catgirl: chars[catgirl]}
    if any(_hooks.is_visit_active(name) for name in chars):
        return _error(409, "visit_active")
    try:
        outcome = await forget_all(
            _hooks.config_dir(), own_uid=own_uid, chars=chars, client=_hooks.client(),
            admission_lock=_hooks.admission_lock, is_visit_active=_hooks.is_visit_active,
        )
    except VisitActive:
        return _error(409, "visit_active")
    except (RosterCorruptError, OSError, ValueError) as exc:
        logger.error("visit forget_all failed before execution: %r", exc)
        return _error(503, "forget_failed", retry=True)
    if not outcome.done:
        return _error(503, "forget_pending", retry=True, forgotten=outcome.forgotten)
    return JSONResponse({"ok": True, "forgotten": outcome.forgotten})


@router.post("/contacts/block")
async def block_contact(request: Request):
    """Block or unblock one ``visit_uid`` on this machine (all accounts); memory is untouched."""
    payload = await _read_json_object(request)
    denied = local_visit_gate(request, payload)
    if denied is not None:
        return denied
    peer_uid = _clean_uid(payload.get("peer_uid"))
    blocked = payload.get("blocked")
    if peer_uid is None or not isinstance(blocked, bool):
        return _error(400, "invalid_request")
    config_dir = _hooks.config_dir()
    try:
        blocklist = await Blocklist.aload(config_dir)
        if blocked:
            display = ""
            own_uid = await _hooks.own_visit_uid()
            if own_uid:
                peer = await PeerRoster(config_dir, own_uid=own_uid).get_peer(peer_uid) or {}
                display = str(peer.get("display_name") or "")
            changed = await blocklist.ablock(peer_uid, display_name_at_block=display)
        else:
            changed = await blocklist.aunblock(peer_uid)
    except BlocklistUnavailable:
        return _error(503, "blocklist_unavailable", retry=True)
    if blocked and _hooks.on_blocked is not None:
        # 在飞串门中拉黑对端 → 立即结束这场
        await _hooks.on_blocked(peer_uid)
    return JSONResponse({"ok": True, "changed": bool(changed)})
