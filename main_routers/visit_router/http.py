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

from config.visit_settings import VISIT_INVITE_PREVIEW_REUSE_S
from main_logic.visit import local_chars, memory_bridge
from main_logic.visit.limits import Blocklist
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
from main_routers.visit_router.local_guard import http_denied
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


# ── 每角色准入锁 ───────────────────────────────────────────────────────

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


# ── 邀请预览（入房复用其中的 host 身份比对黑名单）─────────────────────

_previews: dict[str, tuple[float, cr.InvitePreview]] = {}
"""invite_code → (monotonic time, preview) of recent successful previews (``join`` reuses them)."""

_PREVIEWS_MAX = 32


def _remember_preview(invite_code: str, preview: cr.InvitePreview) -> None:
    now = time.monotonic()
    for code in [c for c, (at, _p) in _previews.items() if now - at > VISIT_INVITE_PREVIEW_REUSE_S]:
        del _previews[code]
    _previews.pop(invite_code, None)
    _previews[invite_code] = (now, preview)
    while len(_previews) > _PREVIEWS_MAX:
        _previews.pop(next(iter(_previews)))


def _recent_preview(invite_code: str) -> Optional[cr.InvitePreview]:
    hit = _previews.get(invite_code)
    if (hit is None or time.monotonic() - hit[0] > VISIT_INVITE_PREVIEW_REUSE_S
            or time.time() >= hit[1].expires_at):
        # 邀请已到期的预览不复用：重新代转一次，过期的邀请在占位之前就被拒
        _previews.pop(invite_code, None)
        return None
    return hit[1]


async def _fetch_preview(invite_code: str) -> cr.InvitePreview:
    lang = prompt_lang()
    ctx = await load_character_context()
    preview = await cr.fetch_invite_preview(
        invite_code,
        generic_label=speaker_label("peer_cat", lang),
        protected_names=protected_display_names(lang, ctx.family_names, ctx.char_names),
    )
    _remember_preview(invite_code, preview)
    return preview


async def _locally_blocked(preview: cr.InvitePreview) -> bool:
    try:
        blocklist = await Blocklist.aload(_config_dir())
    except Exception as exc:  # noqa: BLE001 - 黑名单读不出来按命中处理（fail closed）
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
        preview = await _fetch_preview(invite_code)
    except cr.VisitServersError as exc:
        return _servers_error(exc)
    return JSONResponse(preview.to_public(locally_blocked=await _locally_blocked(preview)))


# ── 建房 / 入房 ────────────────────────────────────────────────────────


async def _refuse_before_admission(request: Request) -> Optional[JSONResponse]:
    """Synchronous refusals that do not depend on the character (cached preflight, backlog)."""
    cached = transport_ws.cached_preflight_failure(request.headers.get("user-agent"))
    if cached is not None:
        return _error(409, "VISIT_UNSUPPORTED_ON_THIS_MACHINE", reason=cached["reason"])
    try:
        full = await tu.upload_backlog_full(_config_dir(), is_live=runtime.is_visit_live)
    except OSError as exc:
        # 目录一时读不了：判不出余量就不接新串门（下一次再试）
        logger.warning("visit: backlog check failed: %s", type(exc).__name__)
        full = True
    if full:
        return _error(409, "VISIT_UPLOAD_BACKLOG")
    return None


async def _resolve_uid(name: str) -> Optional[str]:
    try:
        return await local_chars.resolve_char_uid(name)
    except Exception as exc:  # noqa: BLE001 - 认不出角色交给人设闸拒绝
        logger.warning("visit: character lookup failed: %s", type(exc).__name__)
        return None


async def _admit(name: str, side: str, **kwargs: Any) -> runtime.VisitRuntime | JSONResponse:
    """Forget check and :func:`runtime.start_visit` under the character's admission lock."""
    uid = await _resolve_uid(name)
    async with (char_admission_lock(uid) if uid else contextlib.nullcontext()):
        account = await accounts.local_account()
        if uid:
            own_uid = await accounts.lookup_visit_uid(account)
            if await memory_bridge.char_forget_in_progress(_config_dir(), uid, own_uid=own_uid):
                return _error(409, "VISIT_FORGET_IN_PROGRESS")
        try:
            rt = await runtime.start_visit(name, side, **kwargs)
        except runtime.VisitRefused as exc:
            return _refused(exc.status, exc.body)
        # 清除检查按的角色 / 账号，必须就是这一场的：期间改了名、名字被占或换了社区账号，这次检查不作数。
        # 账号比的是运行时自己记下的那个（领凭证时还要与它核对），中途换走又换回也认得出
        if rt.character_uid != uid or rt.admitted_account != account:
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
    refused = await _refuse_before_admission(request)
    if refused is not None:
        return refused
    # 黑名单在占位与领凭证之前比对：命中不扣双方配额、不进 vendor 房
    preview = _recent_preview(invite_code)
    if preview is None:
        try:
            preview = await _fetch_preview(invite_code)
        except cr.VisitServersError as exc:
            if isinstance(exc, (cr.VisitInviteNotFound, cr.VisitInviteExpired)):
                return _servers_error(cr.VisitInviteInvalid(exc.code))
            return _servers_error(exc)
    if preview.visit_id != visit_id:
        return _servers_error(cr.VisitInviteInvalid("invite_invalid"))
    if await _locally_blocked(preview):
        return _servers_error(cr.VisitInviteInvalid("peer_blocked"))
    admitted = await _admit(name, "guest", invite_code=invite_code, visit_id=visit_id)
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
    # 共用电脑上已换成别的社区账号：这份邀请不是它的，不能替原账号接待 / 婉拒
    if rt is None or rt.visit_id != visit_id or not await _owned_by_current_account(rt):
        return JSONResponse({"ok": False, "error": "no_pending_invite"}, status_code=404)
    status, body = await rt.accept(accept)
    return JSONResponse(body, status_code=status)


# ── 结束 / 状态 / 转录 ─────────────────────────────────────────────────


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
        # 「叫她回来 / 送客」会推进这一场的对话：只有这一场的社区账号能点。硬结束（route_end）谁都能做，
        # 换了账号的人也要能把角色腾出来
        rt = runtime.get_runtime(name)
        if rt is not None and rt.visit_id == visit_id and not await _owned_by_current_account(rt):
            return JSONResponse({"error": "unknown_visit"}, status_code=404)
    status, body = await runtime.end_visit(name, visit_id, reason)
    return JSONResponse(body, status_code=status)


async def _owned_by_current_account(rt: runtime.VisitRuntime) -> bool:
    """Whether the signed-in community account is the one this visit runs under (credentials, else admission)."""
    owner = rt.creds.account if rt.creds is not None else rt.admitted_account
    return owner is not None and owner == await accounts.local_account()


IDLE_STATE: Mapping[str, Any] = {
    "active": False, "role": None, "side": None, "visit_id": None, "phase": None, "transport": None,
    "tier": None, "crop": None, "peer": None, "connected": False, "reconnecting": False, "rtt_ms": None,
    "rx_fps": None, "tx_fps": None, "reconnects": 0, "anomalies": 0, "invite_expires_at": None,
    "credentials_expires_at": None, "cross_region": False, "memory_pending": False,
    "debrief": {"pending": False}, "room": None, "transcript": [],
}
"""``GET /state`` of a character that is not visiting (same keys as ``VisitRuntime.snapshot``)."""


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
        return JSONResponse(dict(IDLE_STATE))
    snapshot = rt.snapshot()
    if not await _owned_by_current_account(rt):
        # 共用电脑上已换成别的社区账号：只说这个角色正在串门，不给这一场的对端、房间与台词
        return JSONResponse({**IDLE_STATE, "active": snapshot["active"], "phase": snapshot["phase"]})
    return JSONResponse(snapshot)


def _local_line(line_id: Any, record: Mapping[str, Any], frame: Optional[Mapping[str, Any]] = None) -> dict:
    side = record.get("side")
    return {
        "line_id": line_id if isinstance(line_id, str) and line_id else f"{side}:{record.get('lp')}",
        "lp": record.get("lp"), "side": side,
        "speaker_kind": _SPEAKER_KIND.get(str(record.get("from") or "")),
        # 落盘的转录不记收件人与已放出片数：只有内存里还留着那一行的推送时才有
        "addressee": (frame or {}).get("addressee"), "i_done": (frame or {}).get("i_done"),
        "ts": record.get("ts"), "text": record.get("text"), "truncated": bool(record.get("truncated")),
    }


def _memory_runtime(visit_id: str) -> Optional[runtime.VisitRuntime]:
    return runtime.get_runtime_by_visit(visit_id) or runtime.recent_runtime(visit_id)


def _runtime_owner(rt: runtime.VisitRuntime) -> Optional[str]:
    return rt.creds.visit_uid if rt.creds is not None else None


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
        # 判不出有没有被清除：不露对端身份（正文照给）
        logger.warning("visit transcript %s: state unreadable: %s", visit_id[:6], type(exc).__name__)
        return True
    return state is None or not state.get("peer_uid")


async def _memory_transcript(config_dir: Path, rt: runtime.VisitRuntime) -> dict:
    lines = []
    for record in rt.transcript_records():
        frame = rt.visit_line_payload_from_record(record)
        lines.append(_local_line(frame.get("line_id"), record, frame))
    # 刚结束的场次还在内存里：「清除这个人」之后同样不再给出对端身份
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


async def _spool_transcript(config_dir: Path, visit_id: str, owner: str) -> Optional[tuple[dict, int]]:
    """``(local transcript, dropped line count)`` of this account's spool, or None."""
    try:
        contents = await VisitSpool(config_dir, visit_id).read_back()
    except OSError as exc:
        logger.warning("visit transcript %s: spool unreadable: %s", visit_id[:6], type(exc).__name__)
        return None
    header = contents.header
    if header is None or header.get("own_uid") != owner:
        # 共用电脑上另一个社区账号的场次：本机副本不给，由云端按参与者判定
        return None
    peer_uid = header.get("peer_uid")
    peer_uid = peer_uid if isinstance(peer_uid, str) and peer_uid else None  # 「清除这个人」后已抹掉
    rt = _memory_runtime(visit_id)
    transport = rt.creds.transport if rt is not None and rt.creds else None
    if transport is None:
        pending = await _pending_upload(config_dir, visit_id)
        transport = pending[0].get("transport") if pending else None
    doc = {
        "visit_id": visit_id,
        "peer_short_id": derive_short_code(peer_uid) if peer_uid else None,
        "peer_uid": peer_uid,
        "started_at": header.get("started_at"),
        "transport": transport,
        "lines": [_local_line(line.get("ln"), line) for line in contents.lines],
        "anomalies": rt.anomaly_count() if rt is not None else await tu.visit_anomalies(config_dir, visit_id),
        "source": "spool",
    }
    return doc, contents.dropped_lines


async def _pending_upload(config_dir: Path, visit_id: str) -> Optional[tuple[dict, int]]:
    try:
        return await asyncio.to_thread(read_pending_upload_doc_sync, config_dir, visit_id)
    except OSError as exc:
        logger.warning("visit transcript %s: pending upload unreadable: %s", visit_id[:6], type(exc).__name__)
        return None


@data_router.get("/transcript")
async def visit_transcript(request: Request, visit_id: str = ""):
    """One visit's transcript for export / report attachments.

    In-memory transcript (while the visit lives and ``VISIT_TRANSCRIPT_MEMORY_TTL_S``
    after the end) → local spool → the local pending upload (``.upload.json`` /
    ``.upload.jsonl``) → Servers details (this side, every page). The first
    two answer the full local shape (``source: memory | spool``), the last two
    the compact shape of the uploaded lines (``source: upload | cloud``).
    Local copies are served only to the community account that took part
    (visit data is partitioned by account); a spool or upload stream that lost
    records in a crash gives way to a complete source and is the last resort
    (``dropped_lines``).
    """
    denied = http_denied(request)
    if denied is not None:
        return denied
    if not isinstance(visit_id, str) or not VISIT_ID_RE.fullmatch(visit_id):
        return _error(400, "visit_id_format")
    config_dir = _config_dir()
    owner = await accounts.own_visit_uid()
    partial: Optional[dict] = None
    if owner:
        # 进程里还有这一场：内存里的流水最完整（spool 某一行写失败时只记了日志、行仍在流水里）
        rt = _memory_runtime(visit_id)
        if rt is not None and _runtime_owner(rt) == owner:
            return JSONResponse(await _memory_transcript(config_dir, rt))
        spooled = await _spool_transcript(config_dir, visit_id, owner)
        if spooled is not None:
            doc, dropped = spooled
            if not dropped:
                return JSONResponse(doc)
            # 崩溃留下的半行 / 坏行被丢掉了：先找完整的来源，都没有再退回这份
            partial = {**doc, "dropped_lines": dropped}
        pending = await _pending_upload(config_dir, visit_id)
        # 较早的上传文件不记属主：与补传同一规则，从 state.json / 流水头行认回来
        if pending is not None and (pending[0].get("own_visit_uid")
                                    or await pending_upload_owner(config_dir, visit_id)) == owner:
            upload_doc, dropped = pending
            request_body = upload_doc["request"]
            body = {"source": "upload", "visit_id": visit_id, "role": request_body["role"],
                    "lines": request_body["lines"]}
            if not dropped:
                return JSONResponse(body)
            # 崩溃流水里也有丢掉的记录：同样先去云端找完整的那份
            if partial is None:
                partial = {**body, "dropped_lines": dropped}
    try:
        return JSONResponse(await cloud_routes.fetch_cloud_transcript(visit_id))
    except cloud_routes.CloudTranscriptIncomplete:
        if partial is not None:
            return JSONResponse(partial)
        return _error(502, "cloud_transcript_incomplete")
    except cloud_routes.CloudError:
        if partial is not None:
            return JSONResponse(partial)
        # 未登录 / 离线 / Servers 不可达 / 云端没有：如实说本机副本已清理
        return _error(404, "transcript_gone_local")


def _reset_for_tests() -> None:
    """Forget the preview cache and idle admission locks (unit tests only)."""
    _previews.clear()
    _admission_locks.clear()


__all__ = ["router", "data_router", "char_admission_lock", "IDLE_STATE"]
