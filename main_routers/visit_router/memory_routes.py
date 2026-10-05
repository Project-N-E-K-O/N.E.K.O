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
from main_logic.visit.forget_runner import (
    AdmissionLock,
    CharacterUnresolved,
    LifecycleGuard,
    VisitActive,
    forget_all,
    forget_person,
)
from main_logic.visit.forget import RevocationLogUnreadable
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


def _behind_proxy() -> bool:
    return os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    # ::ffff:127.0.0.1 之类 IPv4 映射地址：3.11.11 之前的 is_loopback 认不出，先拆开
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


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
    # 首尾带空白（含全空白）的不收：黑名单会先 strip 再用，同一个人在清除与拉黑两边成了两个 id，
    # 全空白的还会在 strip 后变成空串、让拉黑抛 500
    if (
        isinstance(value, str) and value and value == value.strip()
        and len(value) <= _UID_MAX_LEN and value.isprintable()
    ):
        return value
    return None


def _finite_ts(value: Any) -> float | int | None:
    # 名册是非严格读：NaN / Infinity / 超出浮点范围的超大整数之类的坏时间戳序列化不了，
    # 会让整张列表 500，按坏值回 None（与名册严格读同一个判定）
    return value if is_finite_number(value) else None


def _display(value: Any) -> str:
    # 名册是非严格读：display_name 里的孤立代理字符（如 U+D800）json.load 读得进来，
    # 到 JSONResponse 编码 UTF-8 时才抛错，整张列表 500。去掉控制 / 代理字符再回给前端
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
        # 交给 Blocklist 自己归一化（strip + 小写）：名册里的 peer_uid 原样保存
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
        # 名册是非严格读：一条坏了的 pairs（不是列表）只当作空，不能让整张列表 500
        pairs = [p for p in raw_pairs if isinstance(p, str)] if isinstance(raw_pairs, list) else []
        chars = entry.get("chars") if isinstance(entry.get("chars"), dict) else {}
        try:
            person = participant_subject(derive_person_id(own_uid, peer_uid))
        except ValueError:
            # 名册里坏了的对端 id（空串等）：跳过这一条，不让整张列表 500
            continue
        subjects = [person]
        valid_pairs = []
        for pair in pairs:
            try:
                subjects.append(group_chat_subject(pair))
            except ValueError:
                # 空串 / 控制字符之类构不成 subject 的 pair id：跳过这一个，不让整张列表 500
                continue
            valid_pairs.append(pair)
        char_rows = []
        for char_id, info in sorted(chars.items()):
            # 名册里单只猫的记录坏了（不是对象）只影响装饰信息，不能让整个列表 500
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
    # 严格探一遍这个账号的名册结构：读不出 / 某个人的条目坏了都不能当作「没有这个人」
    await roster.peers_of_char(own_char)
    if peer_uid in await roster.list_peers(strict=True):
        return False
    if await memory_bridge.forget_in_progress(config_dir, own_char_uid, peer_uid, own_uid=own_uid):
        # 上一次清除已删掉名册条目、后续步骤还没做完：重试要接着把它跑完，不能回「没什么可清」
        return False
    try:
        visits = await VisitSpool.find_visits_for_pairs(
            config_dir, own_char_uid, [derive_pair_id(own_uid, peer_uid)], own_uid=own_uid,
        )
    except SpoolStateUnreadable:
        return False
    return not visits


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
    # 与拉黑同一口径按小写认人：名册与对子 / 个人 id 的推导都区分大小写，传大写变体时会找不到
    # 这个人、转而去擦一个新推出来的无关 subject，还报「已清除」
    peer_uid = peer_uid.lower()
    own_uid = await _hooks.own_visit_uid()
    if not own_uid:
        return _error(409, "VISIT_LOGIN_REQUIRED")
    if _hooks.is_visit_active(catgirl):
        return _error(409, "visit_active")
    try:
        # 与清除全部同一道严格检查：读不出 / 条目坏了时常规加载会静默换成默认或滤掉它，
        # 下面就会回一个看似永久的 404，这次清除既没落盘也不提示重试
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
            # 从没和这个角色串过门（或 uid 拼错）：不写哨兵、不开日志、不发 scoped_forget。
            # 否则 memory_server 不可用时，一份对应「不存在的人」的哨兵会挡住这个角色开场
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
        # 拿到守卫时按 uid 解析不出名字：刚被删除与角色配置一时读不出分不清，
        # 回可重试的 503（真删了的话，重试时入口就会回 404）
        return _error(503, "forget_failed", retry=True)
    except (RosterCorruptError, RevocationLogUnreadable, OSError, ValueError) as exc:
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
    try:
        # 读不出时常规加载会静默换成默认角色：清除全部不能把默认角色当成完整范围报成功
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
    except (RosterCorruptError, RevocationLogUnreadable, OSError, ValueError) as exc:
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
    # 与黑名单同一口径（大小写不敏感，按小写记）：名册查显示名、结束在飞串门都用规范形，
    # 否则传大写变体时黑名单记上了、在飞的那场却按原值找不到、结束不了
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
        # 在飞串门中拉黑对端 → 立即结束这场。屏蔽已经落盘：结束失败只记日志并如实告知，
        # 不能回 500 让前端以为没屏蔽（重试只会得到 changed=false）
        try:
            await _hooks.on_blocked(peer_uid)
        except Exception as exc:  # noqa: BLE001
            logger.error("visit block: ending the live visit failed: %r", exc)
            return JSONResponse({"ok": True, "changed": bool(changed), "ended": False})
    return JSONResponse({"ok": True, "changed": bool(changed)})
