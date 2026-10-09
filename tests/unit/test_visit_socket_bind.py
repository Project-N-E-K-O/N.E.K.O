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

"""``visit_bind`` on the display socket and the visit downlink filter (design §4.5, PR-09b)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import config
import config.visit_settings as visit_settings
from main_routers import websocket_router
from main_routers.visit_router import display_socket, host_port, runtime
from main_routers.visit_router.debrief import chips_request_id
from tests.unit.test_websocket_binary_audio import _ProtocolManager
from tests.unit.visit_memory_test_helpers import OWN_A, OWN_B, ln, make_visit, vid
from utils import external_route_registry as registry
from utils.visit_route_state import VISIT_SOCKET_BOUND_ATTR

TOKEN = "t" * 32
ORIGIN = "http://127.0.0.1:48911"
NAME = "A"


class VisitSocket:
    """A display-socket double with a peer address, handshake headers and a URL."""

    def __init__(self, messages, *, host="127.0.0.1", headers=None, url_host="127.0.0.1"):
        self.events = [{"type": "websocket.receive", "text": json.dumps(m)} for m in messages]
        self.events.append({"type": "websocket.disconnect", "code": 1000})
        self.client = SimpleNamespace(host=host)
        self.headers = {"origin": ORIGIN, **(headers or {})}
        self.url = SimpleNamespace(hostname=url_host)
        self.sent: list[dict] = []
        self.closed = False

    async def accept(self):
        return None

    async def receive(self):
        await asyncio.sleep(0)
        return self.events.pop(0)

    async def send_text(self, payload):
        self.sent.append(json.loads(payload))

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, *_a, **_k):
        self.closed = True

    def statuses(self):
        return [json.loads(f["message"])["code"] for f in self.sent if f.get("type") == "status"]

    def visit_frames(self):
        return [f for f in self.sent if str(f.get("type", "")).startswith("visit_") or f.get("type") == "chat_blocks"]


def bind(token=TOKEN):
    return {"action": "visit_bind", "csrf_token": token}


class FakeRuntime:
    def __init__(self, frames):
        self.frames = frames
        self.finalized: list[str] = []
        self.peer = None
        self.replayed: list[list[dict]] = []

    def bind_replay_frames(self):
        return [dict(f) for f in self.frames]

    def replay_for_bind(self):
        # 真运行时排进有序显示队列（见 test_bind_replay_rides_the_ordered_display_queue）
        self.replayed.append(self.bind_replay_frames())

    def request_finalize(self, reason):
        self.finalized.append(reason)
        return True


INVITE = {"type": "visit_state_change", "action": "invite_ready", "side": "host", "visit_id": vid(1),
          "invite_code": "ABCDEFGHJK", "ts": 1.0}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    runtime._reset_for_tests()
    display_socket._reset_for_tests()
    monkeypatch.setattr(config, "AUTOSTART_CSRF_TOKEN", TOKEN)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    monkeypatch.setattr(visit_settings, "NEKO_VISIT_ALLOW_NONLOCAL", False)
    monkeypatch.setattr(runtime, "runtime_deps", lambda: SimpleNamespace(config_dir=lambda: tmp_path))
    from main_logic.visit import local_chars

    names = {"uid-a": "A", "uid-b": "B"}

    async def resolve(uid):
        return names.get(uid)

    monkeypatch.setattr(local_chars, "resolve_char_name", resolve)
    monkeypatch.setattr("main_routers.visit_router.local_context.prompt_lang", lambda: "zh")
    from main_routers.visit_router import accounts

    async def own_uid():
        return OWN_A

    monkeypatch.setattr(accounts, "own_visit_uid", own_uid)
    yield names
    runtime._reset_for_tests()
    display_socket._reset_for_tests()


def install(monkeypatch, manager, *, visit_active=False, rt=None):
    calls = {"stream": [], "signal": []}

    async def route_stream(_name, message):
        calls["stream"].append(message)
        return True

    async def on_signal(_name, message):
        calls["signal"].append(message)
        return True

    async def finalize_none(_name):
        return 0

    async def start_session(_name, _message):
        return False

    monkeypatch.setattr(websocket_router, "get_config_manager", object)
    monkeypatch.setattr(websocket_router, "get_session_manager", lambda: {NAME: manager})
    monkeypatch.setattr(websocket_router, "get_session_id", lambda: {})
    registry.register_external_route_kind(registry.ExternalRouteKind(
        kind="neko_visit", is_active=lambda _n: visit_active, route_stream_message=route_stream,
        on_start_session=start_session, finalize_for_character=finalize_none,
        on_page_signal=on_signal,
        current_instance=lambda _n: "visit:x",
    ))
    monkeypatch.setattr(runtime, "get_runtime", lambda name: rt if name == NAME else None)
    monkeypatch.setattr(runtime, "is_visit_route_active", lambda name: visit_active and name == NAME)
    return calls


async def run(socket, manager):
    manager.websocket = socket
    await websocket_router.websocket_endpoint(socket, NAME)
    for _ in range(50):
        if not display_socket._replays:
            break
        await asyncio.gather(*list(display_socket._replays), return_exceptions=True)


# ── 鉴权 ──────────────────────────────────────────────────────────────


async def test_valid_bind_marks_the_connection_and_replays_the_invite(monkeypatch):
    manager = _ProtocolManager()
    rt = FakeRuntime([INVITE])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    socket = VisitSocket([bind()])
    await run(socket, manager)
    assert getattr(socket, VISIT_SOCKET_BOUND_ATTR) is True
    assert rt.replayed == [[INVITE]]
    assert socket.statuses() == [] and manager.statuses == []


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
async def test_proxy_header_refuses_bind_even_from_loopback(monkeypatch, header):
    manager = _ProtocolManager()
    rt = FakeRuntime([INVITE])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    socket = VisitSocket([bind()], headers={header: "127.0.0.1"})
    await run(socket, manager)
    assert not display_socket.is_bound(socket)
    assert socket.statuses() == ["VISIT_E_UNAUTHORIZED"] and socket.visit_frames() == []
    assert rt.replayed == []


async def test_behind_proxy_refuses_a_rewritten_loopback_peer(monkeypatch):
    # uvicorn proxy_headers 已把 client.host 改写成 127.0.0.1：只看 client.host 会放行
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    manager = _ProtocolManager()
    rt = FakeRuntime([INVITE])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    socket = VisitSocket([bind()])
    await run(socket, manager)
    assert not display_socket.is_bound(socket) and socket.visit_frames() == []
    assert rt.replayed == []
    assert socket.statuses() == ["VISIT_E_UNAUTHORIZED"]


async def test_allow_nonlocal_lets_both_through(monkeypatch):
    monkeypatch.setattr(visit_settings, "NEKO_VISIT_ALLOW_NONLOCAL", True)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    manager = _ProtocolManager()
    rt = FakeRuntime([INVITE])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    a = VisitSocket([bind()], headers={"X-Forwarded-For": "10.0.0.2"})
    await run(a, manager)
    b = VisitSocket([bind()], host="192.168.1.20")
    await run(b, manager)
    assert display_socket.is_bound(a) and display_socket.is_bound(b)
    assert len(rt.replayed) == 2


async def test_non_loopback_peer_is_refused_with_valid_token_and_origin(monkeypatch):
    manager = _ProtocolManager()
    rt = FakeRuntime([INVITE])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    socket = VisitSocket([bind()], host="192.168.1.20")
    await run(socket, manager)
    assert not display_socket.is_bound(socket)
    assert socket.statuses() == ["VISIT_E_UNAUTHORIZED"] and socket.visit_frames() == []
    assert rt.replayed == []


@pytest.mark.parametrize("token,origin", [("wrong" * 6, ORIGIN), (TOKEN, "http://evil.example"), ("", ORIGIN)])
async def test_bad_token_or_origin_is_refused(monkeypatch, token, origin):
    manager = _ProtocolManager()
    rt = FakeRuntime([INVITE])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    socket = VisitSocket([bind(token)], headers={"origin": origin})
    await run(socket, manager)
    assert not display_socket.is_bound(socket)
    assert socket.statuses() == ["VISIT_E_UNAUTHORIZED"] and socket.visit_frames() == []
    assert rt.replayed == []


async def test_rebinding_after_reconnect_replays_the_invite_again(monkeypatch):
    manager = _ProtocolManager()
    rt = FakeRuntime([INVITE])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    first = VisitSocket([bind()])
    await run(first, manager)
    second = VisitSocket([bind()])
    await run(second, manager)
    assert rt.replayed == [[INVITE], [INVITE]]


# ── 未绑定连接的输入 ───────────────────────────────────────────────────


async def test_unbound_text_while_visiting_is_refused_and_reaches_nobody(monkeypatch):
    manager = _ProtocolManager()
    calls = install(monkeypatch, manager, visit_active=True)
    socket = VisitSocket([{"action": "stream_data", "input_type": "text", "data": "hi", "request_id": "r1"}])
    await run(socket, manager)
    assert calls["stream"] == []
    assert not any(kind == "stream_data" for kind, _ in manager.calls)
    status = [json.loads(f["message"]) for f in socket.sent if f.get("type") == "status"]
    assert status == [{"code": "VISIT_E_UNAUTHORIZED", "details": {"request_id": "r1"}}]


async def test_bound_text_while_visiting_goes_to_the_visit_only(monkeypatch):
    manager = _ProtocolManager()
    calls = install(monkeypatch, manager, visit_active=True)
    socket = VisitSocket([bind(), {"action": "stream_data", "input_type": "text", "data": "hi"}])
    await run(socket, manager)
    assert [m["data"] for m in calls["stream"]] == ["hi"]
    assert not any(kind == "stream_data" for kind, _ in manager.calls)
    assert socket.statuses() == []


async def test_unbound_text_without_a_visit_is_ordinary_chat(monkeypatch):
    # 串门不在飞：未 bind 的连接照常走普通聊天，不回任何串门状态
    manager = _ProtocolManager()
    calls = install(monkeypatch, manager, visit_active=False)
    socket = VisitSocket([{"action": "stream_data", "input_type": "text", "data": "hi"}])
    await run(socket, manager)
    await asyncio.sleep(0)
    assert calls["stream"] == []
    assert [m["data"] for kind, m in manager.calls if kind == "stream_data"] == ["hi"]
    assert socket.statuses() == [] and manager.statuses == []


# ── 下行只发给已绑定的连接 ─────────────────────────────────────────────


class DownlinkManager:
    def __init__(self, websocket):
        self.websocket = websocket
        self.blocks: list = []
        self.outputs: list = []

    async def render_chat_blocks(self, blocks, *, request_id, source, source_name):
        self.blocks.append(request_id)
        return True

    async def mirror_assistant_output(self, text, *, metadata, request_id):
        self.outputs.append(text)


async def test_unbound_socket_gets_no_visit_downlink_until_it_binds(monkeypatch):
    a = VisitSocket([])
    b = VisitSocket([])
    setattr(a, VISIT_SOCKET_BOUND_ATTR, True)
    mgr = DownlinkManager(a)
    host = host_port.ManagerHost(NAME, mgr)
    assert await host.send_frame(INVITE) is True
    assert await host.render_chat_blocks([{"type": "text", "text": "x"}], request_id="q", source_name=NAME)
    await host.mirror_assistant_output("简述", metadata={}, request_id="s")
    # 新窗口接走 display socket、还没 bind：邀请码、芯片、简述一律不发
    mgr.websocket = b
    assert await host.send_frame(INVITE) is False
    assert await host.render_chat_blocks([{"type": "text", "text": "x"}], request_id="q2", source_name=NAME) is False
    await host.mirror_assistant_output("简述2", metadata={}, request_id="s2")
    chips = {"type": "chat_blocks", "blocks": [{"type": "text", "text": "x"}], "request_id": "q",
             "metadata": {"source": "system", "source_name": NAME, "passthrough": True}}
    # 芯片直接写给校验过的那个连接（同 render_chat_blocks 的帧形状），不经管理器重读 websocket
    assert a.sent == [INVITE, chips] and b.sent == []
    # 回家简述是猫娘的普通发言（同一句也念出声、进 sync 流），§4.5 不要求按 bind 过滤
    assert mgr.blocks == [] and mgr.outputs == ["简述", "简述2"]


class SwappingManager(DownlinkManager):
    """``websocket`` returns the next socket on every read (a replacement landing between two reads)."""

    def __init__(self, *sockets):
        super().__init__(sockets[0])
        self._sockets = list(sockets)

    @property
    def websocket(self):
        return self._sockets.pop(0) if len(self._sockets) > 1 else self._sockets[0]

    @websocket.setter
    def websocket(self, _value):
        pass


async def test_frames_are_checked_and_written_on_the_same_connection():
    unbound = VisitSocket([])
    bound = VisitSocket([])
    setattr(bound, VISIT_SOCKET_BOUND_ATTR, True)
    # 第一次读到未 bind 的新连接、第二次读到已 bind 的旧连接：不能拿旧连接的校验结果写给新连接
    host = host_port.ManagerHost(NAME, SwappingManager(unbound, bound))
    assert await host.send_frame(INVITE) is False
    host = host_port.ManagerHost(NAME, SwappingManager(unbound, bound))
    assert await host.render_chat_blocks([{"type": "text", "text": "x"}], request_id="q", source_name=NAME) is False
    assert unbound.sent == [] and bound.sent == []


# ── debrief 芯片重放 ──────────────────────────────────────────────────


async def _pending(tmp_path, n, *, own_char_uid="uid-a", **changes):
    spool = await make_visit(tmp_path, vid(n), [ln(0, "你好")], own_char_uid=own_char_uid,
                             debrief_chip_pending=True, **changes)
    return spool


def _chip_ids(socket):
    return [f["request_id"] for f in socket.sent if f.get("type") == "chat_blocks"]


async def test_bind_replays_owed_chips_of_this_character_only(monkeypatch, tmp_path):
    await _pending(tmp_path, 1, debrief_choice="ask_later")
    await _pending(tmp_path, 2, finalized="crash")
    await _pending(tmp_path, 3, own_char_uid="uid-b", debrief_choice="ask_later")
    await make_visit(tmp_path, vid(4), [ln(0, "x")], own_char_uid="uid-a", debrief_choice="ask_later")
    manager = _ProtocolManager()
    install(monkeypatch, manager)
    socket = VisitSocket([bind()])
    await run(socket, manager)
    assert _chip_ids(socket) == [chips_request_id(vid(1)), chips_request_id(vid(2))]
    # 崩溃场次先带「意外中断」
    assert socket.statuses() == ["VISIT_INTERRUPTED_LAST_TIME"]
    chips = [f for f in socket.sent if f.get("type") == "chat_blocks"][0]
    assert chips["metadata"] == {"source": "system", "source_name": NAME, "passthrough": True}
    assert [b["type"] for b in chips["blocks"]] == ["text", "buttons"]


async def test_renamed_character_gets_its_chips_under_the_new_name(monkeypatch, tmp_path, _env):
    await _pending(tmp_path, 5, debrief_choice="ask_later", own_char="Old")
    manager = _ProtocolManager()
    install(monkeypatch, manager)
    socket = VisitSocket([bind()])
    await run(socket, manager)
    assert _chip_ids(socket) == [chips_request_id(vid(5))]


async def test_preview_and_failed_blocks_are_left_to_pr14(monkeypatch, tmp_path):
    await _pending(tmp_path, 6, debrief_choice="preview:diary",
                   debrief_pending={"diary": "d", "facts": []})
    manager = _ProtocolManager()
    install(monkeypatch, manager)
    socket = VisitSocket([bind()])
    await run(socket, manager)
    assert _chip_ids(socket) == []


async def test_ack_is_per_connection_and_never_clears_the_flag(monkeypatch, tmp_path):
    spool = await _pending(tmp_path, 7, debrief_choice="ask_later")
    rid = chips_request_id(vid(7))
    manager = _ProtocolManager()
    install(monkeypatch, manager)
    first = VisitSocket([bind()])
    await run(first, manager)
    assert _chip_ids(first) == [rid]
    # 页面渲染后回 ack；不对应当前该投递那一块的 ack（例如预览块的）不记
    await display_socket.handle_chip_ack(first, NAME, {"action": "visit_debrief_chip_ack", "visit_id": vid(7),
                                                       "request_id": "visit-debrief-preview:" + vid(7)})
    assert getattr(first, "neko_visit_delivered_debrief") == set()
    await display_socket.handle_chip_ack(first, NAME, {"action": "visit_debrief_chip_ack", "visit_id": vid(7),
                                                       "request_id": rid})
    assert getattr(first, "neko_visit_delivered_debrief") == {rid}
    assert (await spool.read_state())["debrief_chip_pending"] is True
    # 同一条连接再 bind：已送达的不重推
    await display_socket.replay_chips(first, NAME)
    assert _chip_ids(first) == [rid]
    # 新连接（刷新 / 新窗口）首次 bind 照常重放
    second = VisitSocket([bind()])
    await run(second, manager)
    assert _chip_ids(second) == [rid]


async def test_ack_from_an_unbound_connection_is_ignored(monkeypatch, tmp_path):
    await _pending(tmp_path, 8, debrief_choice="ask_later")
    manager = _ProtocolManager()
    install(monkeypatch, manager)
    socket = VisitSocket([{"action": "visit_debrief_chip_ack", "visit_id": vid(8),
                           "request_id": chips_request_id(vid(8))}])
    await run(socket, manager)
    assert getattr(socket, "neko_visit_delivered_debrief", set()) == set()
    assert manager.statuses == []      # 不再当未知 action


async def test_chips_of_a_visit_still_in_its_exit_flow_are_replayed(monkeypatch, tmp_path):
    # 收尾时页面恰好重连没 bind：芯片标记已落盘、运行时还登记着，bind 时照样重放
    await _pending(tmp_path, 9, debrief_choice="ask_later")
    monkeypatch.setattr(runtime, "is_visit_live", lambda visit_id: True)
    manager = _ProtocolManager()
    install(monkeypatch, manager)
    socket = VisitSocket([bind()])
    await run(socket, manager)
    assert _chip_ids(socket) == [chips_request_id(vid(9))]


async def test_bind_replay_rides_the_ordered_display_queue(tmp_path, monkeypatch):
    from main_routers.visit_router import transport_ws
    from tests.unit.visit_runtime_harness import bring_up, settle, teardown
    from utils import visit_route_state

    runtime.register_visit_route_kind()
    host, guest, wire, clock, _wall = await bring_up(tmp_path, monkeypatch, accept=False)
    try:
        hrt = host.rt
        assert hrt.phase == "awaiting_accept"
        await settle()
        before = len(host.host.frames)
        # 绑定那一刻同步排队：之后的阶段帧排在快照后面，旧快照不会落在新阶段之后
        hrt.replay_for_bind()
        hrt._post_display({"type": "visit_later", "visit_id": hrt.visit_id})
        await settle()
        later = [f["type"] for f in host.host.frames[before:]]
        assert later == ["visit_invite", "visit_later"]
    finally:
        await teardown(host, guest, wire=wire, clock=clock)
        runtime._reset_for_tests()
        transport_ws._reset_for_tests()
        visit_route_state._reset_for_tests()


async def test_ack_before_the_chip_reached_this_connection_is_ignored(monkeypatch, tmp_path):
    # ack 先于重放到达：不能让这条连接跳过还没发给它的芯片
    await _pending(tmp_path, 10, debrief_choice="ask_later")
    rid = chips_request_id(vid(10))
    socket = VisitSocket([])
    setattr(socket, VISIT_SOCKET_BOUND_ATTR, True)
    await display_socket.handle_chip_ack(socket, NAME, {"action": "visit_debrief_chip_ack", "visit_id": vid(10),
                                                        "request_id": rid})
    assert getattr(socket, "neko_visit_delivered_debrief", set()) == set()
    await display_socket.replay_chips(socket, NAME)
    assert _chip_ids(socket) == [rid]
    await display_socket.handle_chip_ack(socket, NAME, {"action": "visit_debrief_chip_ack", "visit_id": vid(10),
                                                        "request_id": rid})
    assert getattr(socket, "neko_visit_delivered_debrief") == {rid}


async def test_chips_written_by_the_runtime_count_as_sent(tmp_path):
    socket = VisitSocket([])
    setattr(socket, VISIT_SOCKET_BOUND_ATTR, True)
    host = host_port.ManagerHost(NAME, DownlinkManager(socket))
    assert await host.render_chat_blocks([{"type": "text", "text": "x"}], request_id="visit-debrief:q",
                                         source_name=NAME)
    assert getattr(socket, "neko_visit_sent_debrief") == {"visit-debrief:q"}


# ── 评审第 3 轮：分派途中路由换成串门 ───────────────────────────────────


async def test_dispatch_carries_whether_the_connection_is_bound(monkeypatch):
    from utils.visit_route_state import DISPLAY_SOCKET_CONNECTION

    manager = _ProtocolManager()
    seen: list = []

    async def route(_name, message):
        seen.append(DISPLAY_SOCKET_CONNECTION.get())
        return True

    async def zero(_name):
        return 0

    install(monkeypatch, manager, visit_active=False)
    registry.register_external_route_kind(registry.ExternalRouteKind(
        kind="game", is_active=lambda _n: True, route_stream_message=route, on_start_session=None,
        finalize_for_character=zero, current_instance=lambda _n: "g", audio_passthrough=True,
    ))
    a = VisitSocket([{"action": "stream_data", "input_type": "text", "data": "a"}])
    await run(a, manager)
    b = VisitSocket([bind(), {"action": "stream_data", "input_type": "text", "data": "b"}])
    await run(b, manager)
    assert seen[0] is a and seen[1] is b
    assert not display_socket.is_bound(seen[0]) and display_socket.is_bound(seen[1])
    assert DISPLAY_SOCKET_CONNECTION.get() is None        # 分派之后复位


async def test_visit_refuses_input_dispatched_from_an_unbound_connection():
    from utils.visit_route_state import DISPLAY_SOCKET_CONNECTION

    class Rt:
        phase = "started"
        takeover_token = object()

        def __init__(self):
            self.statuses = []
            self.accepted = []

        async def status(self, code, **details):
            self.statuses.append((code, details))

        async def on_stream_message(self, message):
            self.accepted.append(message)
            return True

    rt = Rt()
    runtime._runtimes[NAME] = rt
    try:
        unbound = VisitSocket([])
        token = DISPLAY_SOCKET_CONNECTION.set(unbound)
        try:
            assert await runtime.route_stream_message(NAME, {"data": "x", "request_id": "r9"}) is True
        finally:
            DISPLAY_SOCKET_CONNECTION.reset(token)
        # 未授权提示发回发起输入的那条连接，不经运行时（mgr.websocket）
        assert rt.accepted == [] and rt.statuses == []
        status = [json.loads(f["message"]) for f in unbound.sent if f.get("type") == "status"]
        assert status == [{"code": "VISIT_E_UNAUTHORIZED", "details": {"request_id": "r9"}}]
        # 没经 display socket 分派（变量未设置）或已绑定：照常交给串门
        assert await runtime.route_stream_message(NAME, {"data": "y"}) is True
        bound = VisitSocket([])
        setattr(bound, VISIT_SOCKET_BOUND_ATTR, True)
        token = DISPLAY_SOCKET_CONNECTION.set(bound)
        try:
            assert await runtime.route_stream_message(NAME, {"data": "z"}) is True
        finally:
            DISPLAY_SOCKET_CONNECTION.reset(token)
        assert [m["data"] for m in rt.accepted] == ["y", "z"]
    finally:
        runtime._runtimes.pop(NAME, None)


# ── 评审第 4 轮：按社区账号分区 ─────────────────────────────────────────


async def test_chips_of_another_account_or_without_login_are_not_replayed(monkeypatch, tmp_path):
    from main_routers.visit_router import accounts

    await _pending(tmp_path, 11, debrief_choice="ask_later")                     # OWN_A 的场次
    await _pending(tmp_path, 12, debrief_choice="ask_later", own_uid=OWN_B)      # 另一账号的场次
    manager = _ProtocolManager()
    install(monkeypatch, manager)
    socket = VisitSocket([bind()])
    await run(socket, manager)
    assert _chip_ids(socket) == [chips_request_id(vid(11))]

    async def nobody():
        return None

    monkeypatch.setattr(accounts, "own_visit_uid", nobody)                       # 已登出
    logged_out = VisitSocket([bind()])
    await run(logged_out, manager)
    assert _chip_ids(logged_out) == []


async def test_replay_stops_when_the_account_changes_midway(monkeypatch, tmp_path):
    # 扫描 / 发送途中登出或换账号：不再把上一个账号的芯片发给这一页
    from main_routers.visit_router import accounts

    await _pending(tmp_path, 13, debrief_choice="ask_later")
    await _pending(tmp_path, 14, debrief_choice="ask_later")
    calls = {"n": 0}

    async def switching():
        calls["n"] += 1
        return OWN_A if calls["n"] <= 2 else OWN_B      # 扫描时 + 第一块发前仍是 A，之后换成 B

    monkeypatch.setattr(accounts, "own_visit_uid", switching)
    socket = VisitSocket([])
    setattr(socket, VISIT_SOCKET_BOUND_ATTR, True)
    await display_socket.replay_chips(socket, NAME)
    assert _chip_ids(socket) == [chips_request_id(vid(13))]


async def test_recovery_interrupted_status_is_pinned_to_the_bound_connection(monkeypatch, tmp_path):
    from main_routers.visit_router import debrief

    await _pending(tmp_path, 19, finalized="crash")
    unbound = VisitSocket([])
    bound = VisitSocket([])
    setattr(bound, VISIT_SOCKET_BOUND_ATTR, True)

    class Swapping(DownlinkManager):
        reads = 0

        @property
        def websocket(self):
            Swapping.reads += 1
            return bound if Swapping.reads == 1 else unbound      # 校验时是已 bind 的，之后被换掉

        @websocket.setter
        def websocket(self, _v):
            pass

        async def send_status(self, message):
            unbound.sent.append({"type": "status", "message": message})   # 走管理器会写到新连接上
            return True

    mgr = Swapping(bound)
    monkeypatch.setattr(host_port.ManagerHost, "for_character",
                        classmethod(lambda cls, name: host_port.ManagerHost(name, mgr)))
    assert await debrief.render_chips(vid(19), own_char=NAME, status="interrupted") is False
    assert Swapping.reads >= 2                     # 过了 bind 与账号核对、确实走到了写入
    assert all(f.get("type") != "status" for f in unbound.sent)


async def test_recovery_sends_no_chips_when_the_interrupted_notice_failed(monkeypatch, tmp_path):
    from main_routers.visit_router import debrief
    from tests.unit.visit_runtime_harness import FakeHost

    await _pending(tmp_path, 20, finalized="crash")
    fake = FakeHost("Host")

    async def refuse(payload):
        return False if payload.get("type") == "status" else True

    fake.send_frame = refuse
    monkeypatch.setattr(host_port.ManagerHost, "for_character", classmethod(lambda cls, name: fake))
    assert await debrief.render_chips(vid(20), own_char="Host", status="interrupted") is False
    assert fake.blocks == []


# ── 评审第 9 轮 ───────────────────────────────────────────────────────


async def test_repeated_bind_on_the_same_connection_replays_once(monkeypatch):
    manager = _ProtocolManager()
    rt = FakeRuntime([INVITE])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    socket = VisitSocket([bind(), bind(), bind()])
    await run(socket, manager)
    assert rt.replayed == [[INVITE]] and socket.statuses() == []


async def test_crash_notice_then_account_change_sends_no_chip(monkeypatch, tmp_path):
    from main_routers.visit_router import accounts

    await _pending(tmp_path, 15, finalized="crash")
    calls = {"n": 0}

    async def switching():
        calls["n"] += 1
        return OWN_A if calls["n"] <= 2 else OWN_B      # 扫描与提示前是 A，写完提示后换成 B

    monkeypatch.setattr(accounts, "own_visit_uid", switching)
    socket = VisitSocket([])
    setattr(socket, VISIT_SOCKET_BOUND_ATTR, True)
    await display_socket.replay_chips(socket, NAME)
    assert socket.statuses() == ["VISIT_INTERRUPTED_LAST_TIME"] and _chip_ids(socket) == []


async def test_runtime_statuses_only_reach_a_bound_connection():
    unbound = VisitSocket([])
    bound = VisitSocket([])
    setattr(bound, VISIT_SOCKET_BOUND_ATTR, True)
    mgr = DownlinkManager(unbound)
    host = host_port.ManagerHost(NAME, mgr)
    assert await host.send_status("VISIT_VOICE_UNAVAILABLE", {"visit_id": "v"}) is False
    assert unbound.sent == []
    mgr.websocket = bound
    assert await host.send_status("VISIT_VOICE_UNAVAILABLE", {"visit_id": "v"}) is True
    assert bound.statuses() == ["VISIT_VOICE_UNAVAILABLE"]


# ── 评审第 10 轮 ──────────────────────────────────────────────────────


class _StatusStuckSocket(VisitSocket):
    """Status frames fail (stuck / closed socket); everything else is written."""

    async def send_text(self, payload):
        if json.loads(payload).get("type") == "status":
            raise ConnectionError("closed")
        await super().send_text(payload)


async def test_crash_notice_not_written_sends_no_chip(monkeypatch, tmp_path):
    from main_routers.visit_router import accounts

    await _pending(tmp_path, 16, finalized="crash")
    await _pending(tmp_path, 17, debrief_choice="ask_later")

    async def own():
        return OWN_A

    monkeypatch.setattr(accounts, "own_visit_uid", own)
    socket = _StatusStuckSocket([])
    setattr(socket, VISIT_SOCKET_BOUND_ATTR, True)
    await display_socket.replay_chips(socket, NAME)
    assert _chip_ids(socket) == []          # 提示与芯片都留给下次 visit_bind


async def test_crash_notice_written_still_sends_its_chip(monkeypatch, tmp_path):
    from main_routers.visit_router import accounts

    await _pending(tmp_path, 18, finalized="crash")

    async def own():
        return OWN_A

    monkeypatch.setattr(accounts, "own_visit_uid", own)
    socket = VisitSocket([])
    setattr(socket, VISIT_SOCKET_BOUND_ATTR, True)
    await display_socket.replay_chips(socket, NAME)
    assert socket.statuses() == ["VISIT_INTERRUPTED_LAST_TIME"]
    assert _chip_ids(socket) == ["visit-debrief:" + vid(18)]


async def _recovery_host(monkeypatch):
    from tests.unit.visit_runtime_harness import FakeHost

    fake = FakeHost("Host")
    monkeypatch.setattr(host_port.ManagerHost, "for_character", classmethod(lambda cls, name: fake))
    return fake


async def test_recovery_skips_a_visit_of_another_account(monkeypatch, tmp_path):
    # 启动补录逐场回调、不分账号：B 账号的页面已 bind 时，A 的中断提示与芯片都不发给它
    from main_routers.visit_router import debrief

    await _pending(tmp_path, 21, finalized="crash", own_uid=OWN_B)
    fake = await _recovery_host(monkeypatch)
    assert await debrief.render_chips(vid(21), own_char="Host", status="interrupted") is False
    assert fake.frames == [] and fake.blocks == []


async def test_recovery_of_the_current_account_still_renders(monkeypatch, tmp_path):
    from main_routers.visit_router import debrief

    await _pending(tmp_path, 22, finalized="crash")
    fake = await _recovery_host(monkeypatch)
    assert await debrief.render_chips(vid(22), own_char="Host", status="interrupted") is True
    assert [b[1] for b in fake.blocks] == [chips_request_id(vid(22))]


async def test_recovery_with_unreadable_state_sends_nothing(monkeypatch, tmp_path):
    from main_routers.visit_router import debrief

    fake = await _recovery_host(monkeypatch)
    assert await debrief.render_chips(vid(23), own_char="Host", status=None) is False
    assert fake.frames == [] and fake.blocks == []


async def test_recovery_account_change_during_the_notice_sends_no_chip(monkeypatch, tmp_path):
    from main_routers.visit_router import accounts, debrief

    await _pending(tmp_path, 24, finalized="crash")
    calls = {"n": 0}

    async def switching():
        calls["n"] += 1
        return OWN_A if calls["n"] == 1 else OWN_B      # 核对时是 A，写完提示后换成 B

    monkeypatch.setattr(accounts, "own_visit_uid", switching)
    fake = await _recovery_host(monkeypatch)
    assert await debrief.render_chips(vid(24), own_char="Host", status="interrupted") is False
    assert fake.blocks == []

