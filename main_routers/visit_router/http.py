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

"""Visit HTTP endpoints of the runtime (design §4.6, §3.2.1 / §3.2.2).

Two routers, decorated with paths relative to the package router
(``prefix='/api/visit'``):

* :data:`router` -- start / join a visit, behind the ``NEKO_VISIT_ENABLED``
  release switch: ``POST /rooms``, ``GET /invites/{invite_code}/preview``,
  ``POST /rooms/{visit_id}/join``, ``POST /rooms/{visit_id}/accept``.
* :data:`data_router` -- always available (ending or inspecting a visit,
  exporting its transcript): ``POST /route/end``, ``GET /state``,
  ``GET /transcript``.

Rooms and joins are asynchronous: only the checks that can be answered
synchronously run here, then 202; the rest is pushed as
``visit_state_change``. Before the runtime reserves the slot
(:func:`runtime.start_visit`): the cached preflight verdict of this page
environment, the unreclaimable-file cap, for a join the blocklist against the
inviting host, and -- under the per-character :func:`char_admission_lock`,
which the local forget operations take while they persist their sentinel --
no unfinished forget of the character. Every endpoint passes the local-origin
gate (``local_guard.http_denied``). Invite codes never reach a log line (the
access log goes through ``credentials.InviteCodeLogRedactor``).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.visit_settings import VISIT_INVITE_PREVIEW_REUSE_S, VISIT_VIDEO_TIER_DEFAULT
from main_logic.visit import char_lifecycle, local_chars, memory_bridge
from main_logic.visit.limits import Blocklist
from main_logic.visit.memory_commit import SIDE_RANK
from main_logic.visit.recovery import pending_upload_owner, read_pending_upload_doc_sync
from main_logic.visit.spool import VisitSpool
from main_logic.visit.subjects import derive_short_code
from main_routers.system_router._shared import _read_json_object
from main_routers.visit_router import accounts, cloud_routes, runtime, transport_ws
from main_routers.visit_router import credentials as cr
from main_routers.visit_router import transcript_upload as tu
from main_routers.visit_router.local_context import (
    load_character_context,
    prompt_lang,
    protected_display_names,
    speaker_label,
)
from main_routers.visit_router.local_guard import http_denied, visit_enabled
from utils.logger_config import get_module_logger
from utils.visit_wire import VISIT_ID_RE

logger = get_module_logger(__name__, "Main")

router = APIRouter()
"""Start / join endpoints (the package router puts them behind the release switch)."""

data_router = APIRouter()
"""Ending, state and transcript endpoints (not affected by the release switch)."""

_NAME_MAX_CHARS = 128
_CROPS = ("upper", "full")
_END_REASONS = ("recall", "route_end")
_SPEAKER_KIND = {"own_cat": "cat", "peer_cat": "cat", "own_human": "human", "peer_human": "human"}


def _error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, **extra}, status_code=status)


def _refused(status: int, body: Mapping[str, Any]) -> JSONResponse:
    return JSONResponse({"ok": False, **body}, status_code=status)


def _servers_error(exc: cr.VisitServersError) -> JSONResponse:
    return _refused(*exc.to_local_error())


def _valid_name(name: Any) -> bool:
    return isinstance(name, str) and 0 < len(name) <= _NAME_MAX_CHARS and name.isprintable()


def _config_dir() -> Path:
    return Path(runtime.runtime_deps().config_dir())


# ── Per-character admission locks ───────────────────────────────────────

_admission_locks: dict[str, list] = {}
"""character_uid → ``[asyncio.Lock, holders + waiters]`` (dropped once nobody uses it)."""


@contextlib.asynccontextmanager
async def char_admission_lock(character_uid: str) -> AsyncIterator[None]:
    """The visit admission lock of one local character (design §3.2.1 step 0).

    Held by rooms / join from the forget check until the runtime is
    registered, and by the local forget operations while they persist their
    clearing sentinel (``memory_routes`` ``admission_lock`` hook): a clearing
    either sees the new visit as active or the visit sees its sentinel.
    """
    entry = _admission_locks.get(character_uid)
    if entry is None:
        entry = _admission_locks[character_uid] = [asyncio.Lock(), 0]
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] == 0 and _admission_locks.get(character_uid) is entry:
            del _admission_locks[character_uid]


# ── Invite preview (room entry reuses host identity for blacklist checks) ──

_previews: dict[tuple[str, str], tuple[float, cr.InvitePreview, int]] = {}
"""(community account, invite_code) → (monotonic time, preview, account epoch) of recent successful previews.

``join`` reuses one only for the account that fetched it: another account must ask Servers itself
(it may be banned or signed out).
"""

_PREVIEWS_MAX = 32


def _remember_preview(account: Optional[str], invite_code: str, preview: cr.InvitePreview, epoch: int) -> None:
    if not account:
        return
    now = time.monotonic()
    for key in [k for k, (at, _p, _e) in _previews.items() if now - at > VISIT_INVITE_PREVIEW_REUSE_S]:
        del _previews[key]
    _previews.pop((account, invite_code), None)
    _previews[(account, invite_code)] = (now, preview, epoch)
    while len(_previews) > _PREVIEWS_MAX:
        _previews.pop(next(iter(_previews)))


def _recent_preview(account: Optional[str], invite_code: str) -> Optional[tuple[cr.InvitePreview, int]]:
    """``(preview, account epoch)`` cached for this account, still valid, no account change since (else None)."""
    if not account:
        return None
    key = (account, invite_code)
    hit = _previews.get(key)
    if (hit is None or time.monotonic() - hit[0] > VISIT_INVITE_PREVIEW_REUSE_S
            or time.time() >= hit[1].expires_at or hit[2] != runtime.account_epoch()):
        # Never reuse expired invite previews: proxy anew, rejecting expired invitations before reserving.
        _previews.pop(key, None)
        return None
    return hit[1], hit[2]


async def _fetch_preview(invite_code: str, account: Optional[str]) -> tuple[cr.InvitePreview, Optional[int]]:
    """``(preview, account epoch)``; the epoch is None when an account change happened meanwhile."""
    epoch = runtime.account_epoch()
    lang = prompt_lang()
    ctx = await load_character_context()
    preview = await cr.fetch_invite_preview(
        invite_code,
        generic_label=speaker_label("peer_cat", lang),
        protected_names=protected_display_names(lang, ctx.family_names, ctx.char_names),
    )
    # Logout/account switch during request, including A→B→A: preview may use another session's authorization; do not cache.
    if epoch is None or runtime.account_epoch() != epoch:
        return preview, None
    _remember_preview(account, invite_code, preview, epoch)
    return preview, epoch


async def _locally_blocked(preview: cr.InvitePreview) -> bool:
    try:
        blocklist = await Blocklist.aload(_config_dir())
    except Exception as exc:  # noqa: BLE001 - Unreadable blacklist counts as matched (fail closed).
        logger.warning("visit: blocklist unavailable: %s", type(exc).__name__)
        return True
    return cr.is_locally_blocked(preview, blocklist)


@router.get("/invites/{invite_code}/preview")
async def preview_invite(request: Request, invite_code: str):
    """Data of the guest's "let her visit X?" dialog; read only, the code is not consumed."""
    denied = http_denied(request)
    if denied is not None:
        return denied
    if not isinstance(invite_code, str) or cr.INVITE_CODE_RE.fullmatch(invite_code) is None:
        return _servers_error(cr.VisitInviteFormat())
    try:
        preview, epoch = await _fetch_preview(invite_code, await accounts.local_account())
    except cr.VisitServersError as exc:
        return _servers_error(exc)
    blocked = await _locally_blocked(preview)
    if epoch is None or runtime.account_epoch() != epoch:
        # Logout/account change during request/blacklist read invalidates preview authorization for current user.
        return _error(409, "VISIT_E_BUSY", reason="account_change")
    if time.time() >= preview.expires_at:
        # Valid at Servers validation but expired now: return same 410; frontend shows no confirmation dialog.
        return _servers_error(cr.VisitInviteExpired())
    return JSONResponse(preview.to_public(locally_blocked=blocked))


# ── Create / join room ─────────────────────────────────────────────────


async def _refuse_before_admission(request: Request) -> Optional[JSONResponse]:
    """Synchronous refusals that do not depend on the character (cached preflight, backlog)."""
    cached = transport_ws.cached_preflight_failure(request.headers.get("user-agent"))
    if cached is not None:
        return _error(409, "VISIT_UNSUPPORTED_ON_THIS_MACHINE", reason=cached["reason"])
    try:
        full = await tu.upload_backlog_full(_config_dir(), is_live=runtime.is_visit_live)
    except OSError as exc:
        # Directory temporarily unreadable: unknown free capacity rejects new visits; retry later.
        logger.warning("visit: backlog check failed: %s", type(exc).__name__)
        full = True
    if full:
        return _error(409, "VISIT_UPLOAD_BACKLOG")
    return None


class _CharacterLookupFailed(Exception):
    """The character file could not be read right now (distinct from "no such character")."""


async def _resolve_uid(name: str) -> Optional[str]:
    """The character's ``character_uid``; None when there is no such character.

    Raises :class:`_CharacterLookupFailed` when the lookup itself failed: the
    admission lock and the forget check need the uid, so that is refused rather
    than admitted without them.
    """
    try:
        return await local_chars.resolve_char_uid(name)
    except Exception as exc:  # noqa: BLE001 - Unreadable character file cannot bypass admission locks/clear checks.
        logger.warning("visit: character lookup failed: %s", type(exc).__name__)
        raise _CharacterLookupFailed from exc


async def _strict_local_names() -> tuple[set[str], dict[str, str] | None]:
    """The configured names and uids, read strictly (unreadable or missing: raises).

    Only called while a handover marker exists, which proves the machine has
    characters: a missing file must not let the loader fall back to the seed
    profiles (reconciliation would take the marker's character for deleted).
    """
    config = local_chars.get_config_manager()
    runtime_path = getattr(config, "get_runtime_config_path", None)
    # Check runtime copy: get_config_path falls back to project seed if runtime file absent.
    path = runtime_path("characters.json") if callable(runtime_path) else config.get_config_path("characters.json")
    await local_chars.ensure_characters_file_readable(path, missing_ok=False)
    uid_of = await local_chars.load_local_characters()
    return set(uid_of), uid_of


async def _handover_pending(name: str, uid: str) -> bool:
    """Whether a visit of this character must wait for the rename that handed its name over to settle.

    A cloudsave import gave ``name`` to this character while another
    character's rename still owns the entries under it: roster entries and
    spools a visit writes now would migrate to that character. The
    character-config lock (to reconcile first) is only taken when such a
    rename is pending, and not while the startup recovery runs: it
    reconciles that rename under the renamed character's guard, not under
    this lock, and is about to settle it. A failed check refuses (retry later).
    """
    config_dir = _config_dir()
    try:
        if not await char_lifecycle.handover_pending(config_dir, name, uid):
            return False
        from main_routers.visit_router import background
        from utils import character_memory

        if background.startup_recovery_running():
            # Startup reconciliation migrates rename under character guard without config lock; reconciling again here races marker edits,
            # and failed marker cleanup defers clear replay/per-visit backfill to next startup. Migration finishes shortly; reject for now.
            return True

        async with character_memory.character_config_mutation_lock:
            return await char_lifecycle.handover_blocks_visit(config_dir, name, uid, _strict_local_names)
    except Exception as exc:  # noqa: BLE001 - Cannot determine safety: reject visit for now, retry later.
        logger.warning("visit: handover check failed: %s", type(exc).__name__)
        return True


async def _admit(
    name: str, side: str, *, expect_account: Optional[str] = None, expect_epoch: Optional[int] = None,
    **kwargs: Any,
) -> runtime.VisitRuntime | JSONResponse:
    """Forget check and :func:`runtime.start_visit` under the character's admission lock.

    ``expect_account`` / ``expect_epoch`` (join): the account and account-change
    epoch the invite preview was checked under; admission after any account
    change is refused. The epoch read here must also be the one the runtime
    recorded, so a logout / switch anywhere in between refuses the visit.
    """
    try:
        uid = await _resolve_uid(name)
    except _CharacterLookupFailed:
        return _refused(409, {"reason": "busy"})
    if uid and await _handover_pending(name, uid):
        # Cloud snapshot assigned this name to this character before original rename migration finished: new roster/visit records would migrate
        # to original character. Wait for startup reconciliation, usually seconds.
        return _refused(409, {"reason": "handover_pending"})
    # Missing character (uid=None): acquire no lock; persona gate returns VISIT_PERSONA_UNREVIEWED{state:'missing'}.
    async with (char_admission_lock(uid) if uid else contextlib.nullcontext()):
        epoch = runtime.account_epoch()
        if epoch is None or (expect_epoch is not None and epoch != expect_epoch):
            # Logout/account change ongoing or occurred since preview.
            return _refused(409, {"reason": "busy"})
        account = await accounts.local_account()
        if expect_account is not None and account != expect_account:
            # Preview blacklist/expiry checks belonged to another account; invalid after account change.
            return _refused(409, {"reason": "busy"})
        if uid:
            own_uid = await accounts.lookup_visit_uid(account)
            refusal = await memory_bridge.char_forget_refusal(_config_dir(), uid, own_uid=own_uid)
            if refusal == memory_bridge.FORGET_REFUSAL_UNREADABLE:
                # Same code recognized by older frontend, plus reason: unreadable clear record blocks, not an active clear;
                # frontend guides to discard corrupt clear records (POST /api/visit/memory/forget/discard_unreadable).
                return _error(409, "VISIT_FORGET_IN_PROGRESS", reason=refusal)
            if refusal is not None:
                return _error(409, "VISIT_FORGET_IN_PROGRESS")
        try:
            rt = await runtime.start_visit(name, side, **kwargs)
        except runtime.VisitRefused as exc:
            return _refused(exc.status, exc.body)
        # Clear checks must target this visit's exact character/account; rename/name reuse/community-account switch invalidates them.
        # Compare runtime-recorded account (also checked at credential acquisition), detecting switch-away-and-back too.
        if rt.character_uid != uid or rt.admitted_account != account or rt.admitted_epoch != epoch:
            logger.warning("visit %s: character or account changed during admission, refusing", rt.visit_id[:6])
            rt.request_finalize("busy")
            return _refused(409, {"reason": "busy"})
        return rt


@router.post("/rooms")
async def create_room(request: Request):
    """Host side: reserve the character and start the visit; 202, the rest is pushed."""
    payload = await _read_json_object(request)
    denied = http_denied(request, payload)
    if denied is not None:
        return denied
    name = payload.get("catgirl")
    if not _valid_name(name):
        return _error(400, "catgirl_required")
    crop = payload.get("crop", "upper")
    if crop not in _CROPS:
        return _error(400, "crop_format")
    refused = await _refuse_before_admission(request)
    if refused is not None:
        return refused
    admitted = await _admit(name, "host", crop=crop)
    if isinstance(admitted, JSONResponse):
        return admitted
    return JSONResponse({"visit_id": admitted.visit_id, "phase": "pending"}, status_code=202)


@router.post("/rooms/{visit_id}/join")
async def join_room(request: Request, visit_id: str):
    """Guest side: after the confirmation dialog, reserve the character and join; 202."""
    payload = await _read_json_object(request)
    denied = http_denied(request, payload)
    if denied is not None:
        return denied
    if not isinstance(visit_id, str) or not VISIT_ID_RE.fullmatch(visit_id):
        return _error(400, "visit_id_format")
    name = payload.get("catgirl")
    if not _valid_name(name):
        return _error(400, "catgirl_required")
    invite_code = payload.get("invite_code")
    if not isinstance(invite_code, str) or cr.INVITE_CODE_RE.fullmatch(invite_code) is None:
        return _servers_error(cr.VisitInviteFormat())
    if payload.get("confirm") is not True:
        return _error(400, "confirm_required")
    # Visits disabled: reject before proxy preview, no network; start_visit rechecks too.
    switch_off = await runtime.visit_switch_refusal()
    if switch_off is not None:
        return _refused(switch_off.status, switch_off.body)
    refused = await _refuse_before_admission(request)
    if refused is not None:
        return refused
    # Check blacklist before reserving/acquiring credentials; matches consume neither side's quota nor vendor room.
    account = await accounts.local_account()
    cached = _recent_preview(account, invite_code)
    if cached is not None:
        preview, preview_epoch = cached
    else:
        try:
            preview, preview_epoch = await _fetch_preview(invite_code, account)
        except cr.VisitServersError as exc:
            if isinstance(exc, (cr.VisitInviteNotFound, cr.VisitInviteExpired)):
                return _servers_error(cr.VisitInviteInvalid(exc.code))
            return _servers_error(exc)
    if preview.visit_id != visit_id:
        return _servers_error(cr.VisitInviteInvalid("invite_invalid"))
    if time.time() >= preview.expires_at:
        # Fresh preview may expire in transit: reject before reservation, never 202 followed by credential failure.
        return _servers_error(cr.VisitInviteInvalid("invite_expired"))
    if await _locally_blocked(preview):
        return _servers_error(cr.VisitInviteInvalid("peer_blocked"))
    if time.time() >= preview.expires_at:
        # Blacklist reads may take time: expiry meanwhile also rejects before reservation.
        return _servers_error(cr.VisitInviteInvalid("invite_expired"))
    if not account:
        return _servers_error(cr.VisitLoginRequired())
    if preview_epoch is None:
        # Logout/account switch during preview, including A→B→A, means preview no longer represents current account.
        return _refused(409, {"reason": "busy"})
    admitted = await _admit(name, "guest", expect_account=account, expect_epoch=preview_epoch,
                            invite_code=invite_code, visit_id=visit_id)
    if isinstance(admitted, JSONResponse):
        return admitted
    return JSONResponse({"ok": True, "visit_id": admitted.visit_id, "phase": "pending"}, status_code=202)


@router.post("/rooms/{visit_id}/accept")
async def accept_guest(request: Request, visit_id: str):
    """Host's family answers the visitor (``accept:true`` activates first, sends ``ready`` last)."""
    payload = await _read_json_object(request)
    denied = http_denied(request, payload)
    if denied is not None:
        return denied
    if not isinstance(visit_id, str) or not VISIT_ID_RE.fullmatch(visit_id):
        return _error(400, "visit_id_format")
    name = payload.get("catgirl")
    if not _valid_name(name):
        return _error(400, "catgirl_required")
    accept = payload.get("accept")
    if not isinstance(accept, bool):
        return _error(400, "accept_required")
    rt = runtime.get_runtime(name)
    # Shared computer switched community accounts: invitation belongs elsewhere; cannot host/decline for original account.
    if rt is None or rt.visit_id != visit_id or not await _owned_by_current_account(rt):
        return JSONResponse({"ok": False, "error": "no_pending_invite"}, status_code=404)
    if accept:
        # Allow-visits disabled before shutdown hook ended visit: reject hosting, allow declining normally.
        switch_off = await runtime.visit_switch_refusal()
        if switch_off is not None:
            return _refused(switch_off.status, switch_off.body)
    status, body = await rt.accept(accept)
    return JSONResponse(body, status_code=status)


# ── End / status / transcript ───────────────────────────────────────────


@data_router.post("/route/end")
async def end_route(request: Request):
    """``recall`` = natural wrap-up ("call her back" / see the guest off), ``route_end`` = end now."""
    payload = await _read_json_object(request)
    denied = http_denied(request, payload)
    if denied is not None:
        return denied
    name = payload.get("lanlan_name")
    if not _valid_name(name):
        return _error(400, "lanlan_name_required")
    visit_id = payload.get("visit_id")
    if not isinstance(visit_id, str) or not VISIT_ID_RE.fullmatch(visit_id):
        return _error(400, "visit_id_format")
    reason = payload.get("reason")
    if reason not in _END_REASONS:
        return _error(400, "invalid_reason")
    if reason == "recall":
        # Recall her / send guest home advances dialogue: only visit's account may act. Anyone can hard-end via route_end,
        # including switched accounts needing to free the character.
        rt = runtime.get_runtime(name)
        if rt is not None and rt.visit_id == visit_id and not await _owned_by_current_account(rt):
            return JSONResponse({"error": "unknown_visit"}, status_code=404)
    status, body = await runtime.end_visit(name, visit_id, reason)
    return JSONResponse(body, status_code=status)


async def _owned_by_current_account(rt: runtime.VisitRuntime) -> bool:
    """Whether the signed-in community account is the one this visit runs under (credentials, else admission).

    False as well when a logout / account switch happened (or is in progress)
    while the account was being read: the answer could describe the old one.
    """
    epoch = runtime.account_epoch()
    owner = rt.creds.account if rt.creds is not None else rt.admitted_account
    current = await accounts.local_account()
    if epoch is None or runtime.account_epoch() != epoch:
        return False
    return owner is not None and owner == current


IDLE_STATE: Mapping[str, Any] = {
    "active": False, "role": None, "side": None, "visit_id": None, "phase": None, "transport": None,
    "tier": None, "crop": None, "peer": None, "connected": False, "reconnecting": False, "rtt_ms": None,
    "rx_fps": None, "tx_fps": None, "reconnects": 0, "anomalies": 0, "invite_expires_at": None,
    "credentials_expires_at": None, "cross_region": False, "memory_pending": False,
    "debrief": {"pending": False}, "room": None, "transcript": [],
}
"""``GET /state`` of a character that is not visiting (same keys as ``VisitRuntime.snapshot``)."""


def release_info() -> dict:
    """Keys every ``GET /state`` answer carries on top of the snapshot.

    ``enabled`` is the ``NEKO_VISIT_ENABLED`` release switch (the settings
    page hides its visit group while it is off) and ``default_tier`` the
    published video tier the settings page shows read-only (design §5 PR-13).
    """
    return {"enabled": visit_enabled(), "default_tier": VISIT_VIDEO_TIER_DEFAULT}


@data_router.get("/state")
async def visit_state(request: Request, catgirl: str = ""):
    """The character's visit as the page needs it after a reload (never the invite code)."""
    denied = http_denied(request)
    if denied is not None:
        return denied
    if not _valid_name(catgirl):
        return _error(400, "catgirl_required")
    rt = runtime.get_runtime(catgirl)
    if rt is None:
        return JSONResponse({**IDLE_STATE, **release_info()})
    snapshot = rt.snapshot()
    if not await _owned_by_current_account(rt):
        # Shared computer changed community account: report visiting only, never peer/room/dialogue of that visit.
        return JSONResponse({**IDLE_STATE, "active": snapshot["active"], "phase": snapshot["phase"],
                             **release_info()})
    return JSONResponse({**snapshot, **release_info()})


def _local_line(line_id: Any, record: Mapping[str, Any], frame: Optional[Mapping[str, Any]] = None) -> dict:
    side = record.get("side")
    return {
        "line_id": line_id if isinstance(line_id, str) and line_id else f"{side}:{record.get('lp')}",
        "lp": record.get("lp"), "side": side,
        "speaker_kind": _SPEAKER_KIND.get(str(record.get("from") or "")),
        # Persisted transcripts omit recipients/emitted-chunk counts, available only in still-resident push rows.
        "addressee": (frame or {}).get("addressee"), "i_done": (frame or {}).get("i_done"),
        "ts": record.get("ts"), "text": record.get("text"), "truncated": bool(record.get("truncated")),
    }


def _memory_runtime(visit_id: str) -> Optional[runtime.VisitRuntime]:
    return runtime.get_runtime_by_visit(visit_id) or runtime.recent_runtime(visit_id)


async def _peer_forgotten(config_dir: Path, visit_id: str) -> bool:
    """Whether this visit's peer identity may no longer be shown.

    True when "forget this person" nulled the ``state.json`` peer fields, and
    also when the ``state.json`` is gone (activation always writes one, so a
    missing file was swept or never written: no proof the identity is still
    wanted) or unreadable.
    """
    try:
        state = await VisitSpool(config_dir, visit_id).read_state()
    except (OSError, ValueError) as exc:
        # Cannot determine cleared status: hide peer identity, still provide body.
        logger.warning("visit transcript %s: state unreadable: %s", visit_id[:6], type(exc).__name__)
        return True
    return state is None or not state.get("peer_uid")


async def _memory_transcript(config_dir: Path, rt: runtime.VisitRuntime) -> dict:
    lines = []
    for record in rt.transcript_records():
        frame = rt.visit_line_payload_from_record(record)
        lines.append(_local_line(frame.get("line_id"), record, frame))
    # Just-ended visits still in memory also hide peer identity after forgetting this person.
    peer = None if rt.peer is None or await _peer_forgotten(config_dir, rt.visit_id) else rt.peer
    out = {
        "visit_id": rt.visit_id,
        "peer_short_id": peer.short_id if peer else None,
        "peer_uid": peer.uid if peer else None,
        "started_at": rt.started_at_wall,
        "transport": rt.creds.transport if rt.creds else None,
        "lines": lines, "anomalies": rt.anomaly_count(), "source": "memory",
    }
    if rt.ended_at_mono is not None:
        out["ended_at"] = rt.wall() - max(0.0, rt.clock() - rt.ended_at_mono)
    return out


async def _spool_transcript(
    config_dir: Path, visit_id: str, owner: str, pending: Optional[tuple[dict, int]],
) -> Optional[tuple[dict, int]]:
    """``(local transcript, dropped line count)`` of this account's spool, or None.

    ``pending`` is this account's pending upload already read by the caller
    (its ``transport`` fills in when no runtime remembers it).
    """
    try:
        contents = await VisitSpool(config_dir, visit_id).read_back()
    except OSError as exc:
        logger.warning("visit transcript %s: spool unreadable: %s", visit_id[:6], type(exc).__name__)
        return None
    header = contents.header
    if header is None or header.get("own_uid") != owner:
        # Visit belongs to another community account on shared computer: deny local copy, let cloud authorize participants.
        return None
    peer_uid = header.get("peer_uid")
    peer_uid = peer_uid if isinstance(peer_uid, str) and peer_uid else None  # Erased after forgetting this person.
    rt = _memory_runtime(visit_id)
    transport = rt.creds.transport if rt is not None and rt.creds else None
    if transport is None and pending is not None:
        transport = pending[0].get("transport")
    doc = {
        "visit_id": visit_id,
        "peer_short_id": derive_short_code(peer_uid) if peer_uid else None,
        "peer_uid": peer_uid,
        "started_at": header.get("started_at"),
        "transport": transport,
        "lines": [_local_line(line.get("ln"), line) for line in contents.lines],
        "anomalies": (rt.anomaly_count() if rt is not None
                      else await tu.visit_anomalies(config_dir, visit_id, owner)),
        "source": "spool",
    }
    return doc, contents.dropped_lines


async def _pending_upload(config_dir: Path, visit_id: str) -> Optional[tuple[dict, int]]:
    try:
        return await asyncio.to_thread(read_pending_upload_doc_sync, config_dir, visit_id)
    except OSError as exc:
        logger.warning("visit transcript %s: pending upload unreadable: %s", visit_id[:6], type(exc).__name__)
        return None


def _line_key(line: Mapping[str, Any]) -> tuple:
    return line["lp"], line["side"]


def _merge_lines(base: list, more: list, *, local_shape: bool) -> list:
    """``base`` plus the rows of ``more`` whose ``(lp, side)`` it lacks, in ``(lp, side_rank)`` order.

    ``more`` rows are compact (``{lp, side, from, ts, text, truncated}``); with
    ``local_shape`` they are converted to the local full line shape first.
    """
    have = {_line_key(line) for line in base}
    extra = [(_local_line(None, row) if local_shape else dict(row)) for row in more if _line_key(row) not in have]
    if not extra:
        return list(base)
    return sorted(list(base) + extra, key=lambda line: (line["lp"], SIDE_RANK.get(line["side"], 2)))


async def _local_transcript(
    config_dir: Path, visit_id: str, account: Optional[str], owner: Optional[str],
) -> tuple[Optional[dict], Optional[dict]]:
    """``(complete local copy, partial local copy)`` of this account's transcript (either may be None).

    The live / recent runtime wins (its journal is the most complete). Otherwise
    the spool and the pending upload are merged by ``(lp, side)``: they are
    written independently, so each may lack a line the other kept.
    """
    # Visit still in process: memory ledger is fullest (failed spool-row writes only log, keeping rows in ledger).
    # Identify owner by community account, never block for temporarily unwritten account mapping still retrying in background.
    rt = _memory_runtime(visit_id)
    if rt is not None and account and rt.creds is not None and rt.creds.account == account:
        return await _memory_transcript(config_dir, rt), None
    if not owner:
        return None, None
    # Read pending upload once (crashed visits reconstruct possibly large ledgers); spool transport uses same copy.
    pending = await _pending_upload(config_dir, visit_id)
    # Older upload files omit owner: recover from state.json / ledger header, matching reupload rules.
    if pending is not None and (pending[0].get("own_visit_uid")
                                or await pending_upload_owner(config_dir, visit_id)) != owner:
        pending = None
    spooled = await _spool_transcript(config_dir, visit_id, owner, pending)
    if spooled is not None:
        doc, dropped = spooled
        if pending is not None:
            # Spool/upload ledgers persist independently and may lose different rows (write failures only log); union by (lp, side).
            # Unparseable dropped records cannot be identified/proven recovered from other side; report max dropped count.
            upload_doc, upload_dropped = pending
            doc = {**doc, "lines": _merge_lines(doc["lines"], upload_doc["request"]["lines"], local_shape=True)}
            dropped = max(dropped, upload_dropped)
        if dropped:
            # Dropped partial/corrupt crash rows: prefer complete sources, falling back here only if unavailable.
            return None, {**doc, "dropped_lines": dropped}
        if pending is None:
            # Pending upload gone (settled): spool failed writes leave logs, no corrupt rows, concealing omissions.
            # Cloud copy is this side's upload ledger: compare/merge missing rows; if unavailable use spool alone.
            return None, doc
        return doc, None
    if pending is not None:
        upload_doc, dropped = pending
        request_body = upload_doc["request"]
        body = {"source": "upload", "visit_id": visit_id, "role": request_body["role"],
                "lines": request_body["lines"]}
        if not dropped:
            return body, None
        # Crashed ledger also reports dropped records: seek complete cloud copy first.
        return None, {**body, "dropped_lines": dropped}
    return None, None


@data_router.get("/transcript")
async def visit_transcript(request: Request, visit_id: str = ""):
    """One visit's transcript for export / report attachments.

    In-memory transcript (while the visit lives and ``VISIT_TRANSCRIPT_MEMORY_TTL_S``
    after the end) → local spool → the local pending upload (``.upload.json`` /
    ``.upload.jsonl``) → Servers details (this side, every page). The first
    two answer the full local shape (``source: memory | spool``), the last two
    the compact shape of the uploaded lines (``source: upload | cloud``).
    Local copies are served only to the community account that took part
    (visit data is partitioned by account). A local copy that may be short (a
    settled spool, or one that lost records in a crash: ``dropped_lines``) is
    completed with the rows the cloud copy adds and keeps its own shape.
    """
    denied = http_denied(request)
    if denied is not None:
        return denied
    if not isinstance(visit_id, str) or not VISIT_ID_RE.fullmatch(visit_id):
        return _error(400, "visit_id_format")
    config_dir = _config_dir()
    epoch = runtime.account_epoch()
    account = await accounts.local_account()
    owner = await accounts.lookup_visit_uid(account)
    local: Optional[dict] = None
    partial: Optional[dict] = None
    if epoch is not None:
        local, partial = await _local_transcript(config_dir, visit_id, account, owner)
        if runtime.account_epoch() != epoch:
            # Logout/account switch during read: local copy belongs to old account, never current user; cloud only.
            local = partial = None
    if local is not None:
        return JSONResponse(local)
    cloud_epoch = runtime.account_epoch()
    cloud: Optional[dict] = None
    failure: Optional[JSONResponse] = None
    if cloud_epoch is not None:
        try:
            cloud = await cloud_routes.fetch_cloud_transcript(visit_id)
        except cloud_routes.CloudTranscriptIncomplete:
            failure = _error(502, "cloud_transcript_incomplete")
        except cloud_routes.CloudError:
            # Logged out/offline/Servers unreachable/missing cloud copy: truthfully report local copy cleaned up.
            failure = _error(404, "transcript_gone_local")
    if cloud_epoch is None or runtime.account_epoch() != cloud_epoch:
        # Logout/account switch during cloud request/currently: result may belong to another session; return nothing.
        return _error(409, "VISIT_E_BUSY", reason="account_change")
    if cloud is None:
        return JSONResponse(partial) if partial is not None else failure
    if partial is None:
        return JSONResponse(cloud)
    # Merge cloud rows into local copy but retain dropped count: independent writes lose rows; local drops lack identities,
    # so extra cloud rows cannot prove recovery. If cloud contains all local rows, result count matches cloud, while metadata
    # (peer/start/transport/error count/line_id) remains local, consistent with prior exports of same visit.
    merged = _merge_lines(partial["lines"], cloud["lines"], local_shape=partial.get("source") == "spool")
    return JSONResponse({**partial, "lines": merged})


def _reset_for_tests() -> None:
    """Forget the preview cache and idle admission locks (unit tests only)."""
    _previews.clear()
    _admission_locks.clear()


__all__ = ["router", "data_router", "char_admission_lock", "IDLE_STATE"]
