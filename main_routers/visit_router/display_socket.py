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

"""The visit side of the display socket ``/ws/{lanlan_name}`` (design §4.5, PR-09b).

``websocket_router`` hands the visit actions to this module and imports it
lazily (only when a visit action arrives or a visit owns the character), so
ordinary chat never loads the visit package:

* ``visit_bind`` (:func:`handle_bind`): the main gate is the connection's
  real peer address (loopback, no proxy mode, no forwarding header), then
  the same CSRF token and Origin / Host allow-list as the local HTTP
  endpoints. A bound connection is marked on the connection object itself
  (:data:`utils.visit_route_state.VISIT_SOCKET_BOUND_ATTR`, never a global)
  and gets the frames it missed: ``invite_ready`` / ``visit_invite`` of a
  live visit and the debrief chips of every visit of this character whose
  ``state.json.debrief_chip_pending`` is set.
* ``visit_debrief_chip_ack`` (:func:`handle_chip_ack`): remembered per
  connection only; it never clears ``debrief_chip_pending``.
* every visit downlink (``visit_*`` frames, chips, the after-visit summary)
  goes to bound connections only (:func:`is_bound`, used by ``host_port``).
* :func:`refuse_unbound` answers ``status{VISIT_E_UNAUTHORIZED}`` to input
  from an unbound connection while a visit owns the character.
* :func:`finalize_on_goodbye`: a global goodbye (``goodbye_state{active}``)
  finalizes the live visit with ``goodbye`` (OD-25).

A display socket going away never finalizes a visit: the transport WS is the
only source of a grace period (PR-07).
"""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any, Optional

from utils.logger_config import get_module_logger
from utils.visit_route_state import VISIT_SOCKET_BOUND_ATTR, VISIT_SOCKET_DELIVERED_ATTR

logger = get_module_logger(__name__, "Main")

_SEND_TIMEOUT_S = 2.0
_UNAUTHORIZED = "VISIT_E_UNAUTHORIZED"
# 这几种状态投递的是「记成日记 / 不记」两个芯片；预览块、写入中、写入失败块随 PR-14 的两步写入
_CHIP_CHOICES = (None, "ask_later", "generating:diary")

_replays: set[asyncio.Task] = set()
"""Bind replays in flight (kept referenced until done)."""


def is_bound(websocket: Any) -> bool:
    """Whether ``websocket`` passed ``visit_bind`` (False for None or a fake without the mark)."""
    return websocket is not None and getattr(websocket, VISIT_SOCKET_BOUND_ATTR, False) is True


def _delivered(websocket: Any) -> set:
    delivered = getattr(websocket, VISIT_SOCKET_DELIVERED_ATTR, None)
    if not isinstance(delivered, set):
        delivered = set()
        setattr(websocket, VISIT_SOCKET_DELIVERED_ATTR, delivered)
    return delivered


def bind_allowed(websocket: Any, message: Any) -> bool:
    """Both gates of ``visit_bind``: loopback peer first, then token + Origin / Host."""
    from config import AUTOSTART_CSRF_TOKEN
    from main_routers.visit_router.local_guard import local_peer_allowed, websocket_origin_allowed

    client = getattr(websocket, "client", None)
    client_host = getattr(client, "host", None)
    headers = getattr(websocket, "headers", None) or {}
    if not local_peer_allowed(client_host, headers):
        return False
    url = getattr(websocket, "url", None)
    if not websocket_origin_allowed(headers.get("origin", ""), getattr(url, "hostname", None)):
        return False
    token = message.get("csrf_token") if isinstance(message, dict) else None
    return bool(
        isinstance(token, str) and token and AUTOSTART_CSRF_TOKEN
        and secrets.compare_digest(token, AUTOSTART_CSRF_TOKEN)
    )


async def _send(websocket: Any, payload: dict) -> bool:
    try:
        await asyncio.wait_for(websocket.send_text(json.dumps(payload, ensure_ascii=False)), _SEND_TIMEOUT_S)
        return True
    except Exception as exc:  # noqa: BLE001 - 页面不在 / 卡住：下次 bind 再重放
        logger.debug("visit display: %s not written: %s", payload.get("type"), type(exc).__name__)
        return False


async def _send_status(websocket: Any, code: str, details: Optional[dict] = None) -> bool:
    message = json.dumps({"code": code, "details": dict(details or {})}, ensure_ascii=False)
    return await _send(websocket, {"type": "status", "message": message})


async def refuse(websocket: Any, request_id: Any = None) -> None:
    """``status{VISIT_E_UNAUTHORIZED}`` on this connection (with the request id when given)."""
    details = {"request_id": request_id} if isinstance(request_id, str) and request_id else {}
    await _send_status(websocket, _UNAUTHORIZED, details)


async def refuse_unbound(websocket: Any, message: Any) -> bool:
    """Refuse input of an unbound connection while a visit owns the character; True if refused."""
    if is_bound(websocket):
        return False
    request_id = message.get("request_id") if isinstance(message, dict) else None
    await refuse(websocket, request_id)
    return True


async def handle_bind(websocket: Any, lanlan_name: str, message: Any) -> bool:
    """``visit_bind``: authorize this connection and replay what it missed (in the background)."""
    if not bind_allowed(websocket, message):
        logger.warning("visit display: %s (bind refused)", _UNAUTHORIZED)
        await refuse(websocket)
        return False
    setattr(websocket, VISIT_SOCKET_BOUND_ATTR, True)
    _delivered(websocket)
    from main_routers.visit_router import runtime

    rt = runtime.get_runtime(str(lanlan_name or ""))
    if rt is not None:
        # 与打标记同步、排进串门自己的有序显示队列：之前排着的帧先发、之后的阶段帧后发，
        # 旧快照不会落在新阶段之后
        rt.replay_for_bind()
    # 芯片重放要读磁盘（state.json）：放后台，不占住这条连接的收消息循环
    task = asyncio.ensure_future(replay_chips(websocket, str(lanlan_name or "")))
    _replays.add(task)
    task.add_done_callback(_replays.discard)
    return True


async def replay_chips(websocket: Any, lanlan_name: str) -> None:
    """Debrief chips a freshly bound connection gets again (live invite frames go through the runtime)."""
    try:
        await _replay_chips(websocket, lanlan_name)
    except Exception as exc:  # noqa: BLE001 - 读不出就等下次 bind / 启动补录
        logger.warning("visit display: chip replay failed: %r", exc)


async def _pending_debriefs(lanlan_name: str) -> list[dict]:
    """``state.json`` of every visit of ``lanlan_name`` whose chips are still owed (oldest id first)."""
    from main_logic.visit import local_chars
    from main_logic.visit.spool import STATE_SUFFIX, VisitSpool
    from main_routers.visit_router import runtime

    config_dir = runtime.runtime_deps().config_dir()
    pending: list[dict] = []
    names: dict[str, Optional[str]] = {}
    for visit_id in await VisitSpool.list_visit_ids(config_dir, (STATE_SUFFIX,)):
        # 还登记着的场次（退出流程中）也重放：芯片标记已落盘、而收尾时页面恰好重连没 bind，
        # 之后再没有别的重放时机；与收尾那次重复时前端按 request_id 去重
        try:
            state = await VisitSpool(config_dir, visit_id).read_state()
        except Exception as exc:  # noqa: BLE001 - 一场坏了不挡其余场次
            logger.debug("visit display: state of %s unreadable: %s", visit_id[:6], type(exc).__name__)
            continue
        if state is None or not state.get("debrief_chip_pending"):
            continue
        uid = state["own_char_uid"]
        if uid not in names:
            # 按 uid 认当前名字：改名后芯片跟着新名字走；角色已删就不投递
            names[uid] = await local_chars.resolve_char_name(uid)
        if names[uid] == lanlan_name:
            pending.append(state)
    return pending


async def _replay_chips(websocket: Any, lanlan_name: str) -> None:
    from main_routers.visit_router.debrief import chip_blocks, chips_request_id
    from main_routers.visit_router.local_context import prompt_lang

    delivered = _delivered(websocket)
    lang = prompt_lang()
    for state in await _pending_debriefs(lanlan_name):
        visit_id = state["visit_id"]
        if state["debrief_choice"] not in _CHIP_CHOICES:
            # 预览块 / 写入中 / 写入失败块的重放属于 PR-14（/debrief/choice 与两步写入）
            continue
        request_id = chips_request_id(visit_id)
        if request_id in delivered:
            continue
        if state["finalized"] == "crash":
            await _send_status(websocket, "VISIT_INTERRUPTED_LAST_TIME", {"visit_id": visit_id})
        await _send(websocket, chat_blocks_frame(chip_blocks(visit_id, lang), request_id=request_id,
                                                 source_name=lanlan_name))


def chat_blocks_frame(blocks: list, *, request_id: str, source_name: str) -> dict:
    """The ``chat_blocks`` frame of a system message (same shape as ``SessionManager.render_chat_blocks``)."""
    return {
        "type": "chat_blocks",
        "blocks": [dict(block) for block in blocks if isinstance(block, dict)],
        "request_id": request_id,
        "metadata": {"source": "system", "source_name": source_name or "", "passthrough": True},
    }


async def handle_chip_ack(websocket: Any, lanlan_name: str, message: Any) -> None:
    """``visit_debrief_chip_ack``: remember it on this connection when it names the owed block."""
    from main_logic.visit.spool import VisitSpool
    from main_routers.visit_router import runtime
    from main_routers.visit_router.debrief import chips_request_id
    from utils.visit_wire import VISIT_ID_RE

    if not is_bound(websocket) or not isinstance(message, dict):
        return
    visit_id = message.get("visit_id")
    request_id = message.get("request_id")
    if not isinstance(visit_id, str) or VISIT_ID_RE.fullmatch(visit_id) is None or not isinstance(request_id, str):
        return
    try:
        state = await VisitSpool(runtime.runtime_deps().config_dir(), visit_id).read_state()
    except Exception:  # noqa: BLE001 - 读不出：不记，下次 bind 照常重放
        return
    if state is None or not state.get("debrief_chip_pending"):
        return
    # 只认当前状态应投递的那一块：旧芯片迟到的 ack 不能把还没送达的下一块标成已送达
    if state["debrief_choice"] in _CHIP_CHOICES and request_id == chips_request_id(visit_id):
        _delivered(websocket).add(request_id)


def visit_owns_input(lanlan_name: str) -> bool:
    """Whether the visit route is the character's active external route (cheap, no visit import)."""
    from utils.external_route_registry import get_active_external_route
    from utils.visit_route_state import VISIT_ROUTE_KIND

    spec = get_active_external_route(lanlan_name)
    return spec is not None and spec.kind == VISIT_ROUTE_KIND


def finalize_on_goodbye(lanlan_name: str) -> bool:
    """``goodbye_state{active:true}`` while visiting: finalize with ``goodbye`` (OD-25); True if started."""
    from main_routers.visit_router import runtime

    if not runtime.is_visit_route_active(lanlan_name):
        return False
    rt = runtime.get_runtime(lanlan_name)
    return bool(rt is not None and rt.request_finalize("goodbye"))


async def end_visits_with_peer(peer_uid: str) -> int:
    """``on_blocked`` hook of ``/contacts/block``: end every live visit with ``peer_uid`` (``peer_blocked``)."""
    from main_routers.visit_router import runtime

    ended = 0
    peer_uid = str(peer_uid or "").lower()
    for rt in runtime.live_runtimes():
        peer = rt.peer
        if peer is not None and str(peer.uid).lower() == peer_uid and rt.request_finalize("peer_blocked"):
            ended += 1
    return ended


def _reset_for_tests() -> None:
    for task in list(_replays):
        task.cancel()
    _replays.clear()
