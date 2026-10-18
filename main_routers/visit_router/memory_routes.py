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
forget_all | forget/discard_unreadable | contacts/block``. Decorators use paths relative to the visit
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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.visit_settings import VISIT_MEMORY_PLATFORM
from main_logic.visit import local_chars, memory_bridge
from main_logic.visit.forget_runner import (
    AdmissionLock,
    CharacterUnresolved,
    LifecycleGuard,
    VisitActive,
    forget_all,
    forget_person,
)
from main_logic.visit.forget import RevocationLogUnreadable, quarantine_unreadable_records
from main_logic.visit.limits import Blocklist, BlocklistUnavailable
from main_logic.visit.sanitize import strip_control_chars
from main_logic.visit.spool import SpoolStateUnreadable, VisitSpool
from main_logic.visit.subjects import (
    PeerRoster,
    RosterCorruptError,
    derive_pair_id,
    derive_person_id,
    derive_short_code,
    group_chat_subject,
    group_participant_subject,
    is_finite_number,
    participant_subject,
)
from main_routers.system_router._shared import _read_json_object
from main_routers.visit_router.local_guard import http_denied
from memory.scoped_client import ScopedMemoryClient, ScopedMemoryError
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

router = APIRouter()

_UID_MAX_LEN = 64


@dataclass
class MemoryRouteHooks:
    """Runtime hooks of the memory endpoints (see :func:`configure_memory_routes`)."""

    own_visit_uid: Callable[[], Awaitable[str | None]]
    is_visit_active: Callable[[str], bool]
    admission_lock: AdmissionLock | None
    lifecycle_guard: LifecycleGuard | None
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
    lifecycle_guard=None,
    on_blocked=None,
    config_dir=_default_config_dir,
    client=memory_bridge.default_client,
)


def configure_memory_routes(**hooks: Any) -> None:
    """Replace runtime hooks: ``own_visit_uid``, ``is_visit_active``, ``admission_lock``,
    ``lifecycle_guard``, ``on_blocked``, ``config_dir``, ``client`` (unknown names raise ``TypeError``).

    ``lifecycle_guard(own_char_uids)`` is held for a whole clearing; PR-09b wires
    it to the per-character visit background-task registry, which makes rename /
    delete answer 400 while it is held.
    """
    for name, value in hooks.items():
        if not hasattr(_hooks, name):
            raise TypeError(f"unknown memory route hook {name!r}")
        setattr(_hooks, name, value)


def _error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, **extra}, status_code=status)


def local_visit_gate(request: Request, payload: dict | None = None) -> JSONResponse | None:
    """Return a 403 response unless the request comes from this machine's own UI.

    Same gate as every other visit endpoint (``local_guard.http_denied``):
    loopback peer without proxy mode or forwarding headers (unless
    ``NEKO_VISIT_ALLOW_NONLOCAL``), then the shared Origin / Host + CSRF check.
    """
    return http_denied(request, payload)


def _clean_uid(value: Any) -> str | None:
    # Reject leading or trailing whitespace (including all-whitespace values): the blocklist strips first,
    # so the same person would otherwise have different IDs for forgetting and blocking; all-whitespace becomes empty after strip and makes blocking return 500
    if (
        isinstance(value, str) and value and value == value.strip()
        and len(value) <= _UID_MAX_LEN and value.isprintable()
    ):
        return value
    return None


def _finite_ts(value: Any) -> float | int | None:
    # The roster is read permissively: invalid timestamps such as NaN / Infinity / enormous integers outside the float range cannot be serialized,
    # causing the whole list to return 500. Treat invalid values as None (same decision as strict roster reads)
    return value if is_finite_number(value) else None


def _display(value: Any) -> str:
    # The roster is read permissively: json.load accepts a lone surrogate (such as U+D800) in display_name,
    # but UTF-8 encoding in JSONResponse then fails and makes the entire list return 500. Remove control / surrogate characters before returning it to the frontend
    return strip_control_chars(str(value or ""))


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
    except BlocklistUnavailable:
        blocklist = None

    def is_blocked(peer_uid: str) -> bool:
        # Let Blocklist normalize values itself (strip + lowercase); preserve peer_uid in the roster as-is
        if blocklist is None or not blocklist.available:
            return False
        try:
            return blocklist.is_blocked(peer_uid)
        except BlocklistUnavailable:
            return False

    counts = await _subject_counts(catgirl)
    out = []
    for peer_uid, peer in sorted(peers.items()):
        by_char = peer.get("by_char")
        entry = by_char.get(catgirl) if isinstance(by_char, dict) else None
        if not isinstance(entry, dict):
            continue
        raw_pairs = entry.get("pairs")
        # The roster is read permissively: treat a malformed pairs value (not a list) as empty so the whole list does not return 500
        pairs = [p for p in raw_pairs if isinstance(p, str)] if isinstance(raw_pairs, list) else []
        chars = entry.get("chars") if isinstance(entry.get("chars"), dict) else {}
        try:
            person = participant_subject(derive_person_id(own_uid, peer_uid))
        except ValueError:
            # Invalid peer ID in the roster (such as an empty string): skip this entry so the whole list does not return 500
            continue
        subjects = [person]
        valid_pairs = []
        for pair in pairs:
            try:
                subjects.append(group_chat_subject(pair))
            except ValueError:
                # Pair IDs that cannot form a subject, such as empty strings / control characters: skip this one so the whole list does not return 500
                continue
            valid_pairs.append(pair)
        char_rows = []
        for char_id, info in sorted(chars.items()):
            # A malformed record for one cat in the roster (not an object) affects only decoration data, not the entire list
            info = info if isinstance(info, dict) else {}
            for pair_id in valid_pairs:
                try:
                    subject = group_participant_subject(pair_id, char_id)
                except ValueError:
                    continue
                subjects.append(subject)
                char_rows.append({
                    "peer_char_id": char_id,
                    "display_name": _display(info.get("display_name")),
                    "pair_id": pair_id,
                    "last_visit_at": _finite_ts(info.get("last_seen")),
                    "fact_count": _count(counts, subject, "facts"),
                })
        visits = entry.get("visits")
        out.append({
            "peer_uid": peer_uid,
            "short_id": derive_short_code(peer_uid),
            "display_name": _display(peer.get("display_name")),
            "first_seen": _finite_ts(peer.get("first_seen")),
            "last_seen": _finite_ts(peer.get("last_seen")),
            "visits": visits if isinstance(visits, int) and not isinstance(visits, bool) else 0,
            "blocked": is_blocked(peer_uid),
            "fact_count": sum(_count(counts, s, "facts") for s in subjects),
            "reflection_count": sum(_count(counts, s, "reflections") for s in subjects),
            "chars": char_rows,
        })
    return JSONResponse({"peers": out})


async def _nothing_to_forget(
    config_dir: Path, *, own_uid: str, own_char: str, own_char_uid: str, peer_uid: str,
) -> bool:
    """Whether ``peer_uid`` left nothing under ``own_char`` that a "forget this person" could clear.

    True only when this account's roster does not know the person at all
    (under any local character: a rename may move entries between names
    until the forget takes the lifecycle guard), no unfinished clearing of
    the pair is pending, and no visit of this character (matched by its
    stable uid) still names the pair. The roster is read strictly (a damaged
    roster raises :class:`RosterCorruptError`); unreadable visit states count
    as "maybe something", so the regular forget path decides.
    """
    roster = PeerRoster(config_dir, own_uid=own_uid)
    # Strictly validate this account's roster structure: unreadable data or a malformed person's entry must not be treated as "person not found"
    await roster.peers_of_char(own_char)
    if peer_uid in await roster.list_peers(strict=True):
        return False
    if await memory_bridge.forget_in_progress(config_dir, own_char_uid, peer_uid, own_uid=own_uid):
        # A previous forget removed the roster entry but later steps did not finish: retry must complete them, not return "nothing to clear"
        return False
    try:
        visits = await VisitSpool.find_visits_for_pairs(
            config_dir, own_char_uid, [derive_pair_id(own_uid, peer_uid)], own_uid=own_uid,
        )
    except SpoolStateUnreadable:
        return False
    return not visits


def _unreadable_record_refusal(op: str, exc: RevocationLogUnreadable) -> JSONResponse:
    """503 for a forget blocked by an unreadable forget record: retrying cannot help.

    The way out is ``POST /memory/forget/discard_unreadable`` (the page offers
    it on ``reason == 'forget_record_unreadable'``); every other failure stays
    the plain retryable ``forget_failed``.
    """
    logger.error("visit %s blocked by an unreadable forget record: %r", op, exc)
    return _error(503, "forget_failed", reason=memory_bridge.FORGET_REFUSAL_UNREADABLE, retry=False)


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
    # Match people case-insensitively, like the blocklist (lowercase): roster entries and derived pair / person IDs are case-sensitive, so an uppercase variant would fail to find
    # the person and instead clear an unrelated newly derived subject while reporting "cleared"
    peer_uid = peer_uid.lower()
    own_uid = await _hooks.own_visit_uid()
    if not own_uid:
        return _error(409, "VISIT_LOGIN_REQUIRED")
    if _hooks.is_visit_active(catgirl):
        return _error(409, "visit_active")
    try:
        # Apply the same strict check as forget-all: a normal load silently falls back to the default or filters malformed entries,
        # which would return an apparently permanent 404 below; this forget would neither be persisted nor signal a retry
        await local_chars.ensure_characters_readable()
    except local_chars.CharactersUnreadable as exc:
        logger.error("visit forget: character config unreadable: %s", exc)
        return _error(503, "forget_failed", retry=True)
    char_uid = await local_chars.resolve_char_uid(catgirl)
    if char_uid is None:
        return _error(404, "unknown_catgirl")
    config_dir = _hooks.config_dir()
    try:
        if await _nothing_to_forget(config_dir, own_uid=own_uid, own_char=catgirl, own_char_uid=char_uid,
                                    peer_uid=peer_uid):
            # Never chatted with this character (or the uid is wrong): write no sentinel, log nothing, and send no scoped_forget.
            # Otherwise, when memory_server is unavailable, a sentinel for a nonexistent person could block this character at startup
            return JSONResponse({"ok": True, "forgotten": 0})
        outcome = await forget_person(
            config_dir, own_uid=own_uid, own_char=catgirl, own_char_uid=char_uid,
            peer_uid=peer_uid, client=_hooks.client(), admission_lock=_hooks.admission_lock,
            is_visit_active=_hooks.is_visit_active, lifecycle_guard=_hooks.lifecycle_guard,
            resolve_char_name=local_chars.resolve_char_name,
        )
    except VisitActive:
        return _error(409, "visit_active")
    except CharacterUnresolved:
        # If the uid cannot be resolved to a name while holding the guard, the character may have just been deleted or its config may be temporarily unreadable;
        # return retryable 503 (if truly deleted, retrying the request will return 404 at the entry point)
        return _error(503, "forget_failed", retry=True)
    except RevocationLogUnreadable as exc:
        return _unreadable_record_refusal("forget", exc)
    except (RosterCorruptError, OSError, ValueError) as exc:
        logger.error("visit forget failed before execution: %r", exc)
        return _error(503, "forget_failed", retry=True)
    if not outcome.done:
        # The revocation log is persisted; startup reconciliation and retries will complete the remaining steps
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
    try:
        # A normal load silently falls back to the default character when data is unreadable: forget-all must not report success using the default as its full scope
        await local_chars.ensure_characters_readable()
    except local_chars.CharactersUnreadable as exc:
        logger.error("visit forget_all: character config unreadable: %s", exc)
        return _error(503, "forget_failed", retry=True)
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
            lifecycle_guard=_hooks.lifecycle_guard, resolve_char_name=local_chars.resolve_char_name,
        )
    except VisitActive:
        return _error(409, "visit_active")
    except RevocationLogUnreadable as exc:
        return _unreadable_record_refusal("forget_all", exc)
    except (RosterCorruptError, OSError, ValueError) as exc:
        logger.error("visit forget_all failed before execution: %r", exc)
        return _error(503, "forget_failed", retry=True)
    if not outcome.done:
        return _error(503, "forget_pending", retry=True, forgotten=outcome.forgotten)
    return JSONResponse({"ok": True, "forgotten": outcome.forgotten})


@router.post("/memory/forget/discard_unreadable")
async def discard_unreadable_forget_records(request: Request):
    """Move every unreadable forget record (revocation log / clearing sentinel) into quarantine.

    The way out of the fail-closed admission gate: an unreadable revocation
    log cannot be attributed to any character or account, so it makes every
    ``POST /rooms`` / ``join`` on this machine answer 409
    ``VISIT_FORGET_IN_PROGRESS{reason:'forget_record_unreadable'}``. Needs
    ``{confirm: true}``. Readable records are never touched; each file is
    re-read under its own file lock (the one every record write holds) right
    before it is moved, so a forget being written concurrently is safe.
    Files are moved, not deleted. No account is needed: the records are
    machine-wide and cannot be attributed to one.
    """
    payload = await _read_json_object(request)
    denied = local_visit_gate(request, payload)
    if denied is not None:
        return denied
    if payload.get("confirm") is not True:
        return _error(400, "confirm_required")
    try:
        moved, failed = await quarantine_unreadable_records(_hooks.config_dir())
    except OSError as exc:
        logger.error("visit discard unreadable forget records: cannot list: %s", type(exc).__name__)
        return _error(503, "discard_failed", retry=True, discarded=0)
    for record in moved:
        memory_bridge.diag("forget_record_discarded", kind=record.kind, file=record.name, error=record.error)
    if failed:
        return _error(503, "discard_failed", retry=True, discarded=len(moved))
    return JSONResponse({"ok": True, "discarded": len(moved)})


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
    # Match the blocklist behavior (case-insensitive, stored lowercase): use canonical form for roster lookup and ending active peer sessions,
    # otherwise an uppercase variant would be added to the blocklist while the active session remains unfindable and cannot be ended
    peer_uid = peer_uid.lower()
    config_dir = _hooks.config_dir()
    try:
        blocklist = await Blocklist.aload(config_dir)
        if blocked:
            display = ""
            own_uid = await _hooks.own_visit_uid()
            if own_uid:
                peer = await PeerRoster(config_dir, own_uid=own_uid).get_peer(peer_uid) or {}
                display = _display(peer.get("display_name"))
            changed = await blocklist.ablock(peer_uid, display_name_at_block=display)
        else:
            changed = await blocklist.aunblock(peer_uid)
    except BlocklistUnavailable:
        return _error(503, "blocklist_unavailable", retry=True)
    if blocked and _hooks.on_blocked is not None:
        # Blocking a peer during an active peer session ends it immediately. The block is already persisted: log an end failure and report it accurately,
        # rather than returning 500 and making the frontend think the block failed (a retry would only return changed=false)
        try:
            await _hooks.on_blocked(peer_uid)
        except Exception as exc:  # noqa: BLE001
            logger.error("visit block: ending the live visit failed: %r", exc)
            return JSONResponse({"ok": True, "changed": bool(changed), "ended": False})
    return JSONResponse({"ok": True, "changed": bool(changed)})
