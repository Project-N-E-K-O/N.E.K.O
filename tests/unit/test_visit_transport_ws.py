# -*- coding: utf-8 -*-
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

"""``WS /api/visit/transport/ws`` (design §5 PR-07, protocol §4.3)."""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import config.visit_settings as vs
from main_logic.visit.outbox import PAUSE_PAGE_RELOAD, OutboundFrame
from main_routers.visit_router import local_guard
from main_routers.visit_router import transport_ws as tw
from main_routers.visit_router.credentials import VisitCredentials
from tests.fastapi_routes import effective_path, iter_routes
from utils import visit_route_state as vrs

VISIT_ID = "AbCdEfGhIjKlMnOpQrStUv"
TOKEN = "csrf-test-token"
ORIGIN = "http://testserver"
LOOPBACK = ("127.0.0.1", 50123)
USER_SIG = "usersig-SECRET-91bd"
PEER_VID = "h_" + "1" * 24
OWN_VID = "g_" + "2" * 24
LANLAN = "Mochi"


class _Liveness:
    def __init__(self) -> None:
        self.events: list[str] = []

    def on_page_lost(self, now: float) -> None:
        self.events.append("page_lost")

    def on_page_back(self, now: float) -> None:
        self.events.append("page_back")

    def on_page_socket_back(self, now: float) -> None:
        # 新 socket 连回：期限改为页面重载的绝对期限
        self.events.append("page_armed")

    expired = False

    def page_expired(self, now: float) -> bool:
        return self.expired


class _Outbox:
    def __init__(self, log: list) -> None:
        self.log = log
        self.paused: set[str] = set()
        self.hello_pending = False
        self.backpressure = None

    def pause(self, now=None, reason="transport") -> None:
        self.paused.add(reason)
        self.log.append(("pause", reason))

    def resume(self, now=None, reason="transport") -> bool:
        self.paused.discard(reason)
        self.log.append(("resume", reason))
        return not self.paused

    def resend_hello(self, now=None) -> bool:
        self.hello_pending = True
        self.log.append(("resend_hello",))
        return True

    def due(self, now=None) -> list[OutboundFrame]:
        if self.hello_pending and not self.paused:
            self.hello_pending = False
            return [OutboundFrame(cmd=1, payload={"t": "hello", "v": 1, "seq": 1, "ticket": "t"}, nbytes=10, pieces=1)]
        return []

    def set_backpressure(self, on: bool) -> None:
        self.backpressure = on


def _creds(side: str = "guest") -> VisitCredentials:
    return VisitCredentials(
        role=side, visit_id=VISIT_ID, char_tag="c" * 32, transport="trtc", tier="sd600",
        expires_at=2_000_000_000.0, vendor_expires_at=1_999_999_000.0,
        vendor={"trtc": {"sdk_app_id": 1400000001, "user_id": OWN_VID, "user_sig": USER_SIG,
                         "private_map_key": "pmk", "str_room_id": VISIT_ID, "expire": 600}},
        identity_ticket="ticket", visit_uid="0" * 24, vid=OWN_VID if side == "guest" else PEER_VID,
        peer_vid=PEER_VID if side == "guest" else None,
        invite_code=None if side == "guest" else "ABCDEFGHJK",
        invite_expires_at=None if side == "guest" else 2_000_000_000.0,
    )


class FakeSession(tw.VisitTransportSession):
    def __init__(self, side: str = "guest") -> None:
        self.log: list = []
        super().__init__(visit_id=VISIT_ID, side=side, lanlan_name=LANLAN, liveness=_Liveness(),
                         outbox=_Outbox(self.log))
        self.preflights: list[dict] = []
        self.sdk: list[dict] = []
        self.states: list[dict] = []
        self.recvs: list[dict] = []
        self.issued = 0
        self.unsupported: list[str] = []
        self.snapshot: dict[str, Any] = {"publish": True, "crop": "upper", "ladder": 0}

    async def on_preflight(self, caps):
        self.preflights.append(caps)
        if not caps["preflight_ok"]:
            self.unsupported.append("preflight")

    async def issue_credentials(self):
        self.issued += 1
        return tw.build_credentials_message(_creds(self.side), side=self.side, crop="upper", codec="vp9")

    async def on_sdk_caps(self, caps):
        self.sdk.append(caps)
        if not caps["transport_ok"]:
            self.unsupported.append("sdk")

    async def on_state(self, msg):
        self.states.append(msg)

    async def on_recv(self, *, from_vid, cmd, payload, nbytes):
        self.recvs.append({"from_vid": from_vid, "cmd": cmd, "payload": payload, "nbytes": nbytes})

    def media_snapshot(self):
        return dict(self.snapshot)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(local_guard, "AUTOSTART_CSRF_TOKEN", TOKEN)
    monkeypatch.setattr(vs, "NEKO_VISIT_ALLOW_NONLOCAL", False)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    tw._reset_for_tests()
    vrs._reset_for_tests()
    parent = APIRouter(prefix="/api/visit")
    parent.include_router(tw.router)
    application = FastAPI()
    application.include_router(parent)
    yield application
    tw._reset_for_tests()
    vrs._reset_for_tests()


@pytest.fixture
def session():
    s = FakeSession()
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)
    return s


URL = f"/api/visit/transport/ws?visit_id={VISIT_ID}&side=guest"


def _client(app, client=LOOPBACK) -> TestClient:
    return TestClient(app, client=client)


def _auth(ws) -> None:
    ws.send_text(json.dumps({"type": "auth", "csrf_token": TOKEN}))


def _preflight(ws, ok: bool = True, reason: str | None = None) -> None:
    msg = {"type": "caps", "stage": "preflight", "visit_id": VISIT_ID, "side": "guest", "preflight_ok": ok,
           "is_secure_context": True, "ua": "Mozilla/5.0"}
    if reason:
        msg["reason"] = reason
    ws.send_text(json.dumps(msg))


def _sync(ws) -> None:
    """Round-trip barrier: stats are processed in order, then the next state is observable."""
    ws.send_text(json.dumps({"type": "stats", "rx_fps": 30}))


def _wait_page_lost(session, count: int) -> None:
    """Wait until the server finished tearing down ``count`` sockets of ``session``.

    Leaving a ``with websocket_connect`` block does not wait for the server
    handler's ``finally``; opening the next socket before it ran would turn
    the old socket into a replaced one (no page grace) instead of a lost one.
    """
    import time as _t

    deadline = _t.monotonic() + 5
    while session.liveness.events.count("page_lost") < count:
        if _t.monotonic() > deadline:
            raise AssertionError(f"page_lost #{count} never happened: {session.liveness.events}")
        _t.sleep(0.01)


def _barrier(ws, session) -> None:
    """Real round-trip barrier: frames are handled in order, so once this ``recv`` reached
    the runtime every frame sent before it has been fully processed."""
    import time as _t

    marker = {"t": "barrier", "n": len(session.recvs)}
    ws.send_text(json.dumps({"type": "recv", "from_vid": PEER_VID, "cmd": 3, "payload": marker}))
    deadline = _t.monotonic() + 5
    while not any(r["payload"] == marker for r in session.recvs):
        if _t.monotonic() > deadline:
            raise AssertionError("barrier frame never reached the runtime")
        _t.sleep(0.01)


def _expect_close(ws, code: int) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_text()
    assert exc.value.code == code


# ── 路由表 ─────────────────────────────────────────────────────────────


def test_route_is_relative_and_mounts_once_under_the_prefix(app):
    paths = [effective_path(r) for r in iter_routes(app.routes)]
    assert "/api/visit/transport/ws" in paths
    assert not any(p.startswith("/api/visit/api/visit") for p in paths)
    assert [getattr(r, "path", "") for r in tw.router.routes] == ["/transport/ws"]


# ── 握手：回环 / 代理头 / Origin / auth ────────────────────────────────


def test_bad_origin_is_rejected_before_accept(app, session):
    with pytest.raises(WebSocketDisconnect) as exc:
        with _client(app).websocket_connect(URL, headers={"origin": "http://evil.example"}):
            pass
    assert exc.value.code == tw.CLOSE_UNAUTHORIZED


def test_non_loopback_peer_is_rejected_even_with_token_and_origin(app, session):
    with pytest.raises(WebSocketDisconnect) as exc:
        with _client(app, client=("192.168.1.20", 5000)).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
            _auth(ws)
            ws.receive_text()
    assert exc.value.code == tw.CLOSE_UNAUTHORIZED
    assert session.preflights == []


def test_behind_proxy_rejects_even_a_rewritten_loopback_peer(app, session, monkeypatch):
    # proxy_headers=True 时 uvicorn 已把 client.host 改写成 XFF 里的 127.0.0.1
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    with pytest.raises(WebSocketDisconnect) as exc:
        with _client(app).websocket_connect(URL, headers={"origin": ORIGIN, "x-forwarded-for": "127.0.0.1"}):
            pass
    assert exc.value.code == tw.CLOSE_UNAUTHORIZED


def test_behind_proxy_disables_visits_even_without_proxy_headers(app, session, monkeypatch):
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "1")
    with pytest.raises(WebSocketDisconnect) as exc:
        with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}):
            pass
    assert exc.value.code == tw.CLOSE_UNAUTHORIZED


@pytest.mark.parametrize("header,value", [
    ("x-forwarded-for", "127.0.0.1"),
    ("forwarded", "for=127.0.0.1"),
    ("x-real-ip", "127.0.0.1"),
])
def test_any_proxy_header_is_rejected_on_a_real_loopback_peer(app, session, header, value):
    with pytest.raises(WebSocketDisconnect) as exc:
        with _client(app).websocket_connect(URL, headers={"origin": ORIGIN, header: value}):
            pass
    assert exc.value.code == tw.CLOSE_UNAUTHORIZED


def test_allow_nonlocal_lets_both_through(app, session, monkeypatch):
    monkeypatch.setattr(vs, "NEKO_VISIT_ALLOW_NONLOCAL", True)
    for client, headers in (
        (("192.168.1.20", 5000), {"origin": ORIGIN}),
        (LOOPBACK, {"origin": ORIGIN, "x-forwarded-for": "10.0.0.1"}),
    ):
        with _client(app, client=client).websocket_connect(URL, headers=headers) as ws:
            _auth(ws)
            _preflight(ws)
            assert json.loads(ws.receive_text())["type"] == "credentials"
        tw._reset_for_tests()
        tw.register_transport_session(session)


def test_first_frame_must_be_auth(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _preflight(ws)
        _expect_close(ws, tw.CLOSE_UNAUTHORIZED)
    assert session.preflights == [] and session.issued == 0


def test_wrong_token_is_rejected(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        ws.send_text(json.dumps({"type": "auth", "csrf_token": "nope"}))
        _expect_close(ws, tw.CLOSE_UNAUTHORIZED)


def test_auth_then_caps_is_processed(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        msg = json.loads(ws.receive_text())
    assert msg["type"] == "credentials"
    assert session.preflights[0]["preflight_ok"] is True


def test_unknown_visit_closes_4404(app):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _expect_close(ws, tw.CLOSE_UNKNOWN_VISIT)


# ── 能力门与凭证 ───────────────────────────────────────────────────────


def test_failed_preflight_is_recorded_and_no_credentials_are_sent(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws, ok=False, reason="no_webrtc")
        _sync(ws)
        ws.send_text(json.dumps({"type": "state", "state": "left", "peer_present": False, "remote_video": False}))
        _sync(ws)
    slot = vrs.get_visit_route_state(LANLAN)
    assert slot["caps_preflight"]["preflight_ok"] is False
    assert slot["caps_preflight"]["reason"] == "no_webrtc"
    assert session.issued == 0
    assert session.unsupported == ["preflight"]


def test_failed_preflight_hook_does_not_fetch_credentials(app):
    class _Broken(FakeSession):
        async def on_preflight(self, caps):
            raise RuntimeError("state update failed")

    s = _Broken()
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        _sync(ws)
    assert s.issued == 0


def test_preflight_is_not_written_into_another_visits_slot(app, session):
    vrs.activate_visit_route(LANLAN, visit_id="ZZZZZZZZZZZZZZZZZZZZZZ")  # 同角色已开了下一场
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws, ok=False, reason="no_webrtc")
        _sync(ws)
    assert "caps_preflight" not in vrs.get_visit_route_state(LANLAN)


def test_credentials_are_sent_once_per_connection(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        first = json.loads(ws.receive_text())
        _preflight(ws)
        _sync(ws)
        ws.send_text(json.dumps({"type": "state", "state": "joining", "peer_present": False, "remote_video": False}))
        _sync(ws)
        assert session.issued == 1
        # 运行时再塞一条首发：拒；续期：放行
        assert not _run(ws, session.send, first)
        assert _run(ws, session.send, {**first, "refresh": True})
        refreshed = json.loads(ws.receive_text())
    assert refreshed["type"] == "credentials" and refreshed["refresh"] is True
    assert first["vendor"] == {"trtc": _creds().vendor["trtc"]}
    assert first["own_vid"] == OWN_VID and first["peer_vid"] == PEER_VID
    assert "publish_video" not in first and "refresh" not in first


def test_host_credentials_have_no_peer_vid_until_hello(app):
    msg = tw.build_credentials_message(_creds("host"), side="host", crop="full", codec="vp8")
    assert msg["peer_vid"] is None and msg["publish"]["codec"] == "vp8"
    assert msg["publish"]["scalability_mode"] == "L1T1" and msg["publish"]["simulcast"] is False
    with pytest.raises(ValueError):
        tw.build_credentials_message(_creds("host"), side="guest", crop="full", codec="vp8")


def test_sdk_caps_transport_failure_reaches_the_runtime(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        ws.receive_text()
        ws.send_text(json.dumps({"type": "caps", "stage": "sdk", "transport_ok": False, "video_ok": False,
                                 "reason": "sdk_unsupported", "codecs": []}))
        _sync(ws)
        ws.send_text(json.dumps({"type": "caps", "stage": "sdk", "transport_ok": True, "video_ok": True}))
        _sync(ws)
    assert [c["transport_ok"] for c in session.sdk] == [False]
    assert session.unsupported == ["sdk"]


def test_sdk_caps_before_credentials_are_ignored(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        ws.send_text(json.dumps({"type": "caps", "stage": "sdk", "transport_ok": True, "video_ok": True}))
        _sync(ws)
    assert session.sdk == []


# ── 上行转发 ───────────────────────────────────────────────────────────


def test_recv_reaches_the_runtime_and_malformed_is_dropped(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        ws.send_text(json.dumps({"type": "recv", "from_vid": PEER_VID, "cmd": 2, "payload": {"t": "text"}}))
        ws.send_text(json.dumps({"type": "recv", "from_vid": "short", "cmd": 2, "payload": {}}))
        ws.send_text(json.dumps({"type": "recv", "from_vid": PEER_VID, "cmd": 9, "payload": {}}))
        ws.send_text(json.dumps({"type": "tx_backpressure", "on": True, "queue": 201, "dropped": True}))
        _sync(ws)
    assert len(session.recvs) == 1
    assert session.recvs[0]["from_vid"] == PEER_VID and session.recvs[0]["payload"] == {"t": "text"}
    assert session.recvs[0]["nbytes"] > 0
    assert session.outbox.backpressure is True


def test_oversize_frame_closes_the_socket(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        ws.send_text(json.dumps({"type": "stats", "pad": "x" * (tw.FRAME_MAX_BYTES + 1)}))
        _expect_close(ws, tw.CLOSE_TOO_LARGE)


def test_disconnect_starts_the_page_grace(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    _wait_page_lost(session, 1)
    assert session.liveness.events == ["page_lost"]
    assert ("pause", PAUSE_PAGE_RELOAD) in session.log
    assert not tw.is_transport_attached(VISIT_ID, "guest")


# ── 重载 / 顶号 ────────────────────────────────────────────────────────


def _rejoin(ws) -> list[dict]:
    _auth(ws)
    _preflight(ws)
    out = [json.loads(ws.receive_text())]
    ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
    out.append(json.loads(ws.receive_text()))
    out.append(json.loads(ws.receive_text()))
    ws.send_text(json.dumps({"type": "state", "state": "connected", "peer_present": True, "remote_video": False}))
    _sync(ws)
    return out


def test_reload_resends_hello_then_exactly_one_media_snapshot_guest(app, session):
    client = _client(app)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        ws.receive_text()
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        _sync(ws)
    _wait_page_lost(session, 1)
    session.log.clear()
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        msgs = _rejoin(ws)
        ws.send_text(json.dumps({"type": "stats"}))
    assert [m["type"] for m in msgs] == ["credentials", "send", "media"]
    assert msgs[1]["payload"]["t"] == "hello"
    assert msgs[2] == {"type": "media", "publish": True, "crop": "upper", "ladder": 0}
    _wait_page_lost(session, 2)
    # 连回只把期限改成绝对期限（page_armed），重入成功才 page_back
    assert session.liveness.events == ["page_lost", "page_armed", "page_back", "page_lost"]
    assert ("resume", PAUSE_PAGE_RELOAD) in session.log


def test_reload_snapshot_is_taken_from_the_runtime_not_hardcoded(app):
    host = FakeSession(side="host")
    host.snapshot = {"subscribe": False, "peer_vid": "g_" + "2" * 24}
    tw.register_transport_session(host)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)
    url = f"/api/visit/transport/ws?visit_id={VISIT_ID}&side=host"
    client = _client(app)
    with client.websocket_connect(url, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    _wait_page_lost(host, 1)
    with client.websocket_connect(url, headers={"origin": ORIGIN}) as ws:
        msgs = _rejoin(ws)
    assert msgs[2] == {"type": "media", "subscribe": False, "peer_vid": "g_" + "2" * 24}


def test_first_connection_sends_no_media_snapshot(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        ws.receive_text()
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        _sync(ws)
    assert not any(e[0] == "resend_hello" for e in session.log)


def test_second_connection_supersedes_the_first_with_4409(app, session):
    client = _client(app)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as old:
        _auth(old)
        _sync(old)
        with client.websocket_connect(URL, headers={"origin": ORIGIN}) as new:
            _auth(new)
            _sync(new)
            _expect_close(old, tw.CLOSE_SUPERSEDED)
            assert tw.is_transport_attached(VISIT_ID, "guest")
            # 被顶掉的旧连接断开不算掉页
            assert session.liveness.events == ["page_armed"]


def test_stop_is_sent_once_and_unregister_closes(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
        assert _run(ws, session.send, {"type": "stop", "reason": "home"})
        assert not _run(ws, session.send, {"type": "stop", "reason": "home"})
        assert json.loads(ws.receive_text()) == {"type": "stop", "reason": "home"}
        _run(ws, _unregister, session)
        _expect_close(ws, tw.CLOSE_NORMAL)
    assert session.liveness.events == []


def test_vendor_grant_never_reaches_the_logs(app, session, caplog):
    with caplog.at_level(logging.DEBUG):
        with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
            _auth(ws)
            _preflight(ws)
            ws.receive_text()
            _run(ws, session.send, {**tw.build_credentials_message(_creds(), side="guest", crop="upper",
                                                                   codec="vp9"), "refresh": True})
            ws.receive_text()
            ws.send_text(json.dumps({"type": "nonsense", "user_sig": USER_SIG}))
            _sync(ws)
    assert USER_SIG not in caplog.text


class GatedSession(FakeSession):
    """``issue_credentials`` calls listed in ``hold_issue`` wait until released.

    Each issued ``credentials`` carries its call number in ``expires_at`` so a
    test can tell which call's message reached which socket.
    """

    def __init__(self, *, hold_issue=()) -> None:
        super().__init__()
        self.hold_issue = set(hold_issue)
        self.gates: dict = {}
        self.oversize_first = False

    async def _wait(self, key):
        import asyncio

        self.gates[key] = asyncio.Event()
        await self.gates[key].wait()

    async def issue_credentials(self):
        self.issued += 1
        n = self.issued
        if n in self.hold_issue:
            await self._wait(("issue", n))
        msg = tw.build_credentials_message(_creds(self.side), side=self.side, crop="upper", codec="vp9")
        msg["expires_at"] = float(n)
        if self.oversize_first and n == 1:
            msg["pad"] = "x" * tw.CREDENTIALS_MAX_BYTES
        return msg


def _release(s, key):
    async def _go():
        s.gates[key].set()
    return _go


def _wait_gate(ws, s, key):
    for _ in range(200):
        if ws.portal.call(_has_gate, s, key):
            return
        import time as _t
        _t.sleep(0.01)
    raise AssertionError(f"gate {key} never reached")


async def _has_gate(s, key):
    return key in s.gates


class _RecordingWS:
    """Stand-in websocket for driving ``_handle_frame`` directly on one event loop."""

    def __init__(self) -> None:
        from starlette.websockets import WebSocketState

        self.sent: list[dict] = []
        self.closed_with = None
        self.application_state = WebSocketState.CONNECTED

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        from starlette.websockets import WebSocketState

        self.closed_with = code
        self.application_state = WebSocketState.DISCONNECTED


def test_late_credentials_of_a_replaced_connection_are_dropped():
    # TestClient 在服务端关掉连接后不再调度旧 handler 的后续代码，这个时序只能直接驱动 _handle_frame
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = GatedSession(hold_issue={1})
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        old_ws, new_ws = _RecordingWS(), _RecordingWS()
        old = tw._Connection(websocket=old_ws, reattach=False)
        link.conn = old
        preflight = {"type": "caps", "stage": "preflight", "preflight_ok": True}
        task = asyncio.ensure_future(tw._handle_frame(link, old, preflight, 10, VISIT_ID, "guest"))
        while ("issue", 1) not in s.gates:
            await asyncio.sleep(0)
        # 等 Servers 期间页面重载：新连接已过预检、正等自己的凭证
        new = tw._Connection(websocket=new_ws, reattach=True)
        new.preflight_seen = True
        link.conn = new
        await old.close(tw.CLOSE_SUPERSEDED, "superseded")
        s.gates[("issue", 1)].set()
        assert await task is None
        return old_ws, new_ws

    try:
        old_ws, new_ws = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert new_ws.sent == []
    assert old_ws.sent == [] and old_ws.closed_with == tw.CLOSE_SUPERSEDED


def test_lifecycle_callbacks_run_on_the_monotonic_clock(app, session, monkeypatch):
    seen: list[float] = []
    session.liveness.on_page_lost = lambda now: seen.append(now)
    from tests.fake_clock import patch_module_clock

    # 墙钟与单调钟故意差很远：回调必须拿到单调钟
    patch_module_clock(monkeypatch, tw, monotonic=lambda: 12345.0, time=lambda: 9e9)
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    assert seen == [12345.0]


def test_takeover_does_not_wait_for_a_stuck_old_socket():
    import asyncio

    class _StuckWS(_RecordingWS):
        async def send_text(self, text):
            await asyncio.Event().wait()

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        old = tw._attach(link, _StuckWS())
        stuck = asyncio.ensure_future(old.send_json({"type": "media", "publish": True}))
        await asyncio.sleep(0)
        new = tw._attach(link, _RecordingWS())  # 同步完成，不等旧连接的发送锁
        refused = await asyncio.wait_for(tw._send_on(old, {"type": "media", "publish": False}), 1)
        sent = await asyncio.wait_for(s.send({"type": "media", "publish": False}), 1)
        stuck.cancel()
        return old, new, refused, sent

    try:
        old, new, refused, sent = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert old.retired and refused is False
    assert sent is True and new.websocket.sent == [{"type": "media", "publish": False}]


def test_send_queued_behind_the_lock_is_dropped_once_retired():
    import asyncio

    class _SlowWS(_RecordingWS):
        def __init__(self):
            super().__init__()
            self.gate = asyncio.Event()

        async def send_text(self, text):
            await self.gate.wait()
            await super().send_text(text)

    async def scenario():
        ws = _SlowWS()
        conn = tw._Connection(websocket=ws, reattach=False)
        first = asyncio.ensure_future(conn.send_json({"type": "media", "publish": True}))
        await asyncio.sleep(0)
        queued = asyncio.ensure_future(conn.send_json({"type": "media", "publish": False}))
        await asyncio.sleep(0)
        conn.retired = True  # 顶号发生在它排队等锁期间
        ws.gate.set()
        return await first, await queued, ws.sent

    first, queued, sent = asyncio.run(scenario())
    assert first is True and queued is False
    assert sent == [{"type": "media", "publish": True}]


def test_close_is_not_blocked_by_a_stuck_send(monkeypatch):
    import asyncio

    monkeypatch.setattr(tw, "CLOSE_LOCK_WAIT_S", 0.05)

    class _StuckWS(_RecordingWS):
        async def send_text(self, text):
            await asyncio.Event().wait()

    async def scenario():
        ws = _StuckWS()
        conn = tw._Connection(websocket=ws, reattach=False)
        stuck = asyncio.ensure_future(conn.send_json({"type": "media", "publish": True}))
        await asyncio.sleep(0)
        await asyncio.wait_for(conn.close(tw.CLOSE_SUPERSEDED, "superseded"), 1)
        stuck.cancel()
        return ws.closed_with

    assert asyncio.run(scenario()) == tw.CLOSE_SUPERSEDED


def test_close_task_ends_even_if_the_close_frame_never_drains(monkeypatch):
    import asyncio

    monkeypatch.setattr(tw, "CLOSE_LOCK_WAIT_S", 0.05)

    class _DeadWS(_RecordingWS):
        async def send_text(self, text):
            await asyncio.Event().wait()

        async def close(self, code=1000, reason=""):
            await asyncio.Event().wait()

    async def scenario():
        conn = tw._Connection(websocket=_DeadWS(), reattach=False)
        stuck = asyncio.ensure_future(conn.send_json({"type": "media", "publish": True}))
        await asyncio.sleep(0)
        await asyncio.wait_for(conn.close(tw.CLOSE_SUPERSEDED, "superseded"), 1)
        stuck.cancel()
        return conn.closed

    assert asyncio.run(scenario()) is True


def test_replaced_session_cannot_send_into_its_successor():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        stale, fresh = FakeSession(), FakeSession()
        tw.register_transport_session(stale)
        tw.register_transport_session(fresh)  # 同 (visit_id, side) 换了一个 runtime
        ws = _RecordingWS()
        conn = tw._attach(tw._links[(VISIT_ID, "guest")], ws)
        conn.preflight_seen = True
        late = await stale.send({"type": "media", "publish": True})
        ok = await fresh.send({"type": "media", "publish": False})
        return late, ok, ws.sent

    try:
        late, ok, sent = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert late is False and ok is True
    assert sent == [{"type": "media", "publish": False}]


def test_failed_rejoin_is_retried_on_the_next_joined_report(app):
    class _Flaky(FakeSession):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def on_page_rejoined(self, now):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("transient")
            super().on_page_rejoined(now)

    s = _Flaky()
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)
    client = _client(app)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    _wait_page_lost(s, 1)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        ws.receive_text()
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        _barrier(ws, s)  # 不用 _sync：stats 本身也会触发重试，这里要验的是下一条 connected
        assert not s.fail_once and ("resend_hello",) not in s.log
        ws.send_text(json.dumps({"type": "state", "state": "connected", "peer_present": True,
                                 "remote_video": False}))
        hello = json.loads(ws.receive_text())
        media = json.loads(ws.receive_text())
    assert hello["type"] == "send" and media["type"] == "media"


def test_cancelled_handler_still_starts_the_page_grace(monkeypatch):
    # 服务端关停 / ASGI 层取消 handler 时，finally 里第一个 await 就会抛 CancelledError：
    # 掉页处理必须排在任何 await 之前
    import asyncio
    from types import SimpleNamespace

    monkeypatch.setattr(local_guard, "AUTOSTART_CSRF_TOKEN", TOKEN)
    monkeypatch.setattr(vs, "NEKO_VISIT_ALLOW_NONLOCAL", False)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)

    class _HandlerWS(_RecordingWS):
        client = SimpleNamespace(host="127.0.0.1")
        headers = {"origin": ORIGIN}
        url = SimpleNamespace(hostname="testserver")
        query_params = {"visit_id": VISIT_ID, "side": "guest"}

        def __init__(self):
            super().__init__()
            self.inbox = [{"type": "websocket.receive", "text": json.dumps({"type": "auth", "csrf_token": TOKEN})}]

        async def accept(self):
            return None

        async def receive(self):
            if self.inbox:
                return self.inbox.pop(0)
            await asyncio.Event().wait()  # 之后不再有帧：一直挂到被取消
            raise AssertionError("unreachable")

        async def close(self, code=1000, reason=""):
            # anyio 的取消是持续生效的：handler 收尾里的每个 await 都会再抛 CancelledError
            raise asyncio.CancelledError()

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        task = asyncio.ensure_future(tw.visit_transport_ws(_HandlerWS()))
        while not tw.is_transport_attached(VISIT_ID, "guest"):
            await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):  # 被取消正是本用例要的结局
            _ = await task
        return s

    try:
        s = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert s.liveness.events == ["page_lost"]
    assert ("pause", PAUSE_PAGE_RELOAD) in s.log


def test_replaced_during_preflight_does_not_fetch_credentials():
    import asyncio

    class _SlowPreflight(FakeSession):
        def __init__(self):
            super().__init__()
            self.gate = None

        async def on_preflight(self, caps):
            self.gate = asyncio.Event()
            await self.gate.wait()
            await super().on_preflight(caps)

    async def scenario():
        tw._reset_for_tests()
        s = _SlowPreflight()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        old = tw._attach(link, _RecordingWS())
        preflight = {"type": "caps", "stage": "preflight", "preflight_ok": True}
        task = asyncio.ensure_future(tw._handle_frame(link, old, preflight, 10, VISIT_ID, "guest"))
        while s.gate is None:
            await asyncio.sleep(0)
        tw._attach(link, _RecordingWS())  # 页面重载，新连接接管
        s.gate.set()
        assert await task is None
        return s

    try:
        s = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert s.issued == 0


def test_unregistered_session_never_delivers_late_credentials():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = GatedSession(hold_issue={1})
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        ws = _RecordingWS()
        conn = tw._attach(link, ws)
        preflight = {"type": "caps", "stage": "preflight", "preflight_ok": True}
        task = asyncio.ensure_future(tw._handle_frame(link, conn, preflight, 10, VISIT_ID, "guest"))
        while ("issue", 1) not in s.gates:
            await asyncio.sleep(0)
        # Servers 回来与场次结束同一拍：handler 先恢复，关闭任务还没来得及跑
        s.gates[("issue", 1)].set()
        tw.unregister_transport_session(s)
        assert await task is None
        await asyncio.sleep(0)
        return ws

    try:
        ws = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert ws.sent == []


@pytest.mark.parametrize("broken", ["on_state", "media_snapshot"])
def test_failed_state_hook_or_snapshot_retries_the_whole_rejoin(app, broken):
    class _Flaky(FakeSession):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        async def on_state(self, msg):
            if broken == "on_state" and self.fail_once:
                self.fail_once = False
                raise RuntimeError("transient")
            await super().on_state(msg)

        def media_snapshot(self):
            if broken == "media_snapshot" and self.fail_once:
                self.fail_once = False
                raise RuntimeError("transient")
            return super().media_snapshot()

    s = _Flaky()
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)
    client = _client(app)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    _wait_page_lost(s, 1)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        ws.receive_text()
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        _barrier(ws, s)
        # 失败的这一拍不能开始重入：on_state 失败时 outbox 不动；快照失败时也不能已经恢复 outbox
        assert not any(e[0] in ("resend_hello", "resume") for e in s.log)
        assert PAUSE_PAGE_RELOAD in s.outbox.paused
        ws.send_text(json.dumps({"type": "state", "state": "connected", "peer_present": True,
                                 "remote_video": False}))
        hello = json.loads(ws.receive_text())
        media = json.loads(ws.receive_text())
    assert hello["type"] == "send" and media == {"type": "media", "publish": True, "crop": "upper", "ladder": 0}


def test_frames_of_a_replaced_connection_never_reach_the_runtime():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        old = tw._Connection(websocket=_RecordingWS(), reattach=False)
        new = tw._Connection(websocket=_RecordingWS(), reattach=True)
        link.conn = new
        await old.close(tw.CLOSE_SUPERSEDED, "superseded")
        await tw._handle_frame(link, old, {"type": "state", "state": "kicked", "peer_present": False,
                                           "remote_video": False, "vendor_reason": "kick"}, 10, VISIT_ID, "guest")
        await tw._handle_frame(link, old, {"type": "recv", "from_vid": PEER_VID, "cmd": 2,
                                           "payload": {"t": "text"}}, 10, VISIT_ID, "guest")
        return s

    try:
        s = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert s.states == [] and s.recvs == []


def test_superseding_a_live_socket_pauses_the_outbox_until_the_new_iframe_rejoins(app, session):
    client = _client(app)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as old:
        _auth(old)
        _sync(old)
        assert session.outbox.paused == set()
        with client.websocket_connect(URL, headers={"origin": ORIGIN}) as new:
            msgs = _rejoin_with_pause_check(new, session)
            _expect_close(old, tw.CLOSE_SUPERSEDED)
    assert [m["type"] for m in msgs] == ["credentials", "send", "media"]


def _rejoin_with_pause_check(ws, session) -> list[dict]:
    _auth(ws)
    _sync(ws)
    # 新 iframe 还没入房：outbox 不跑，任何 hello / 重传都等它重入后再发到它自己的 socket
    assert PAUSE_PAGE_RELOAD in session.outbox.paused
    return _rejoin(ws)


def test_reload_state_before_credentials_does_not_rejoin(app, session):
    client = _client(app)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    _wait_page_lost(session, 1)
    session.log.clear()
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        _sync(ws)
        _run(ws, session.send, {"type": "stop", "reason": "home"})
        assert json.loads(ws.receive_text())["type"] == "stop"
    assert not any(e[0] in ("resend_hello", "resume") for e in session.log)


def test_failed_first_credentials_send_does_not_burn_the_slot(app):
    s = GatedSession()
    s.oversize_first = True
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        _sync(ws)
        msg = tw.build_credentials_message(_creds(), side="guest", crop="upper", codec="vp9")
        assert _run(ws, s.send, msg)
        assert json.loads(ws.receive_text())["type"] == "credentials"
        assert not _run(ws, s.send, msg)


# ── 本机来源判定 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1", True), ("127.8.9.10", True), ("::1", True), ("::ffff:127.0.0.1", True),
    ("192.168.1.20", False), ("10.0.0.1", False), ("localhost", False), ("testclient", False), (None, False),
])
def test_loopback_literal_only(host, ok):
    assert local_guard.is_loopback_host(host) is ok


# ── helpers ────────────────────────────────────────────────────────────


async def _unregister(s):
    tw.unregister_transport_session(s)


def _run(ws, fn, *args):
    """Run an async runtime call on the app's event loop (the TestClient portal)."""
    return ws.portal.call(fn, *args)


# ── 评审（wehos，593d997）补的用例 ─────────────────────────────────────


class _SlowWS(_RecordingWS):
    """send_text yields to the loop before recording (lets runtime sends interleave)."""

    def __init__(self, on_first_send=None):
        super().__init__()
        self.on_first_send = on_first_send

    async def send_text(self, text):
        import asyncio

        if self.on_first_send is not None:
            hook, self.on_first_send = self.on_first_send, None
            hook()
        await asyncio.sleep(0.01)
        await super().send_text(text)


def test_rejoin_snapshot_never_overrides_a_newer_media_state():
    # 复现：发 hello 期间用户关了摄像头，runtime 发 media{publish:false}；旧快照不能最后到、把推流打开
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1  # 这是一条重载后的连接
        tasks = []

        def _camera_off():
            s.snapshot = {"publish": False, "crop": "upper", "ladder": 0}
            tasks.append(asyncio.ensure_future(s.send({"type": "media", "publish": False, "crop": "upper", "ladder": 0})))

        ws = _SlowWS(on_first_send=_camera_off)
        conn = tw._attach(link, ws)
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = True
        await tw._handle_frame(link, conn, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest")
        await asyncio.gather(*tasks)
        return ws.sent

    try:
        sent = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    medias = [m for m in sent if m["type"] == "media"]
    assert sent[0]["type"] == "send"
    assert medias and medias[-1]["publish"] is False


def test_unserializable_downlink_is_dropped_not_a_disconnect():
    import asyncio

    async def scenario():
        conn = tw._Connection(websocket=_RecordingWS(), reattach=False)
        bad_set = await conn.send_json({"type": "media", "publish": True, "x": {1, 2}})
        bad_nan = await conn.send_json({"type": "media", "publish": True, "x": float("nan")})
        bad_text = await conn.send_json({"type": "media", "publish": True, "x": "\ud800"})
        ok = await conn.send_json({"type": "media", "publish": True})
        return bad_set, bad_nan, bad_text, ok, conn

    bad_set, bad_nan, bad_text, ok, conn = asyncio.run(scenario())
    assert (bad_set, bad_nan, bad_text, ok) == (False, False, False, True)
    assert not conn.closed and conn.websocket.sent == [{"type": "media", "publish": True}]


def test_unserializable_snapshot_keeps_the_socket_open(app, session):
    client = _client(app)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    _wait_page_lost(session, 1)
    session.snapshot = {"publish": True, "crop": "upper", "ladder": 0, "bad": {1}}
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        ws.receive_text()
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        _barrier(ws, session)
        # 快照序列化不了：重入整套不做（不发 hello、outbox 仍暂停、宽限没清），socket 也没被当成断线
        assert tw.is_transport_attached(VISIT_ID, "guest")
        assert PAUSE_PAGE_RELOAD in session.outbox.paused
        assert "page_back" not in session.liveness.events
        assert session.liveness.events.count("page_lost") == 1
        # 快照恢复正常后，下一条上报完成重入
        session.snapshot = {"publish": True, "crop": "upper", "ladder": 0}
        ws.send_text(json.dumps({"type": "state", "state": "connected", "peer_present": True, "remote_video": False}))
        assert json.loads(ws.receive_text())["type"] == "send"
        assert json.loads(ws.receive_text()) == {"type": "media", "publish": True, "crop": "upper", "ladder": 0}


def test_page_grace_keeps_running_until_the_new_iframe_rejoins(app):
    from main_logic.visit.liveness import VisitLiveness

    s = FakeSession()
    s.liveness = VisitLiveness("guest", 0.0)
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)
    client = _client(app)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    _wait_until(lambda: s.liveness.page_deadline is not None)
    departed = s.liveness.page_departed_at
    assert s.liveness.page_deadline == pytest.approx(departed + vs.VISIT_LOCAL_PAGE_GRACE_S)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws, ok=False, reason="no_webrtc")  # 新 iframe 预检失败：永远不会重入
        _barrier(ws, s)
        # auth 不清期限，也不重起 20 s：改为绝对期限（离开 + 35 − 5），起点不变
        assert s.liveness.page_departed_at == departed
        assert s.liveness.page_deadline == pytest.approx(
            departed + vs.VISIT_PEER_REJOIN_GRACE_S - vs.VISIT_PAGE_REJOIN_SAFETY_S)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        ws.receive_text()
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        ws.receive_text()
        ws.receive_text()
        _barrier(ws, s)
        assert s.liveness.page_deadline is None  # 重入成功才清


def test_superseding_a_live_socket_starts_the_page_grace(app):
    from main_logic.visit.liveness import VisitLiveness

    s = FakeSession()
    s.liveness = VisitLiveness("guest", 0.0)
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)
    client = _client(app)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as old:
        _auth(old)
        _barrier(old, s)
        assert s.liveness.page_deadline is None
        with client.websocket_connect(URL, headers={"origin": ORIGIN}) as new:
            _auth(new)
            _barrier(new, s)
            # 旧 iframe 已被顶掉、新的还没入房：要有截止时间
            assert s.liveness.page_deadline is not None


def test_failed_preflight_blocks_a_runtime_first_issue(app, session):
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws, ok=False, reason="no_webrtc")
        _barrier(ws, session)
        msg = tw.build_credentials_message(_creds(), side="guest", crop="upper", codec="vp9")
        assert not _run(ws, session.send, msg)
        assert _run(ws, session.send, {"type": "stop", "reason": "unsupported"})
        assert json.loads(ws.receive_text())["type"] == "stop"


def test_module_has_no_key_routed_downlink_entry():
    # 下行唯一入口是 session.send（绑定到登记中的 session）
    assert not hasattr(tw, "send_downlink")


def test_credentials_message_tier_comes_from_the_credentials():
    import dataclasses

    creds = dataclasses.replace(_creds(), tier="hd1200")
    msg = tw.build_credentials_message(creds, side="guest", crop="upper", codec="vp9")
    # 档位、码率、帧率都取自凭证的档位，不再是 sd600 写死
    assert msg["tier"] == "hd1200"
    assert msg["publish"]["bitrate_kbps"] == vs.VISIT_TIERS["hd1200"]["video_kbps"]
    assert msg["publish"]["fps"] == vs.VISIT_TIERS["hd1200"]["fps"]
    # 该档位没有全身裁剪：拒绝
    with pytest.raises(ValueError):
        tw.build_credentials_message(creds, side="guest", crop="full", codec="vp9")
    sd = tw.build_credentials_message(_creds(), side="guest", crop="full", codec="vp9")
    assert sd["publish"]["bitrate_kbps"] == vs.VISIT_TIERS["sd600"]["video_kbps"]


def _wait_until(pred, timeout: float = 5.0) -> None:
    import time as _t

    deadline = _t.monotonic() + timeout
    while not pred():
        if _t.monotonic() > deadline:
            raise AssertionError("condition never became true")
        _t.sleep(0.01)


# ── 页面重载的绝对期限（设计稿 §4.8 VISIT_PEER_REJOIN_GRACE_S 一行）──────


def _reload_session():
    from main_logic.visit.liveness import VisitLiveness

    s = FakeSession()
    s.liveness = VisitLiveness("guest", -1000.0)
    s.liveness.on_peer_verified(-1000.0)  # 已在会话中：不再处于等对端的阶段
    return s


def _tick(s, t: float):
    # 对端始终在线：只看本侧页面期限，不让对端心跳判死抢先
    s.liveness.on_peer_message(t)
    return s.liveness.tick(t)


def test_reload_ws_back_at_19s_sdk_at_31s_ends_at_30s():
    # 设计稿验收：WS 第 19 s 连回、SDK 拖到第 31 s → 本侧第 30 s local_page_lost（对端 35 s 判 peer_left，两侧一致）
    s = _reload_session()
    s.on_page_lost(0.0)
    s.on_page_attached(19.0)
    assert _tick(s, 29.9) is None
    assert _tick(s, 30.0) == "local_page_lost"


def test_reload_ws_back_at_19s_sdk_at_21s_survives():
    # WS 第 19 s 连回、SDK 第 21 s 入房：不能沿用断线起算的 20 s
    s = _reload_session()
    s.on_page_lost(0.0)
    s.on_page_attached(19.0)
    assert _tick(s, 20.5) is None
    s.on_page_rejoined(21.0)
    # 重入准备完之前不清期限（失败时要能回滚），提交之后才清
    assert s.liveness.page_deadline is not None
    s.on_page_rejoin_committed(21.0)
    assert s.liveness.page_deadline is None
    assert _tick(s, 25.0) is None


def test_reload_socket_phase_keeps_its_own_20s():
    s = _reload_session()
    s.on_page_lost(0.0)
    assert _tick(s, 19.9) is None
    assert _tick(s, 20.0) == "local_page_lost"


def test_reload_deadline_respects_the_peers_heartbeat_clock():
    # 最后一次成功发出在 -10 s：对端约在 +20 s 判死，本侧必须在 -10 + 27 = 17 s 前回来
    s = _reload_session()
    s.liveness.on_message_sent(-10.0)
    s.on_page_lost(0.0)
    assert _tick(s, 16.9) is None
    assert _tick(s, 17.0) == "local_page_lost"


def test_flapping_page_never_extends_past_the_absolute_deadline():
    s = _reload_session()
    s.on_page_lost(0.0)
    s.on_page_attached(5.0)
    s.on_page_lost(6.0)  # 又断：起点仍是 0
    s.on_page_attached(15.0)
    assert _tick(s, 29.9) is None
    assert _tick(s, 30.0) == "local_page_lost"


def test_superseding_a_live_socket_starts_the_reload_now():
    s = _reload_session()
    s.on_page_attached(100.0)  # 没有断线、直接被顶号：从顶号时刻起算
    assert _tick(s, 129.9) is None
    assert _tick(s, 130.0) == "local_page_lost"


def test_sdk_stage_uses_the_absolute_deadline_not_the_caps_timer():
    # WS 第 2 s 就连回：重入期限就是绝对期限 30 s；VISIT_CAPS_SDK_TIMEOUT_S 是能力门自己的计时（runtime）
    s = _reload_session()
    s.on_page_lost(0.0)
    s.on_page_attached(2.0)
    assert _tick(s, 29.9) is None
    assert _tick(s, 30.0) == "local_page_lost"


def test_second_drop_after_reconnect_gets_its_own_socket_budget():
    # 复现（wehos）：t=0 断、t=5 连回、t=25 又断；绝对期限 min(30, 0 + 27) = 27，不能在 t=25 当场判死
    s = _reload_session()
    s.liveness.on_message_sent(0.0)
    s.on_page_lost(0.0)
    s.on_page_attached(5.0)
    s.on_page_lost(25.0)
    assert _tick(s, 25.0) is None
    assert _tick(s, 26.9) is None
    assert _tick(s, 27.0) == "local_page_lost"


def test_a_late_socket_cannot_revive_an_expired_page():
    # t=0 断、WS 期限 t=20；tick 还没跑到时 t=20.2 才连回：期限不能被挪走
    s = _reload_session()
    s.on_page_lost(0.0)
    s.on_page_attached(20.2)
    assert s.liveness.page_deadline == 20.0
    assert _tick(s, 20.5) == "local_page_lost"


def test_rejoin_after_the_deadline_is_refused():
    # SDK 在绝对期限之后、下一次 tick 之前才报 joined：不能清期限、不能恢复 outbox
    s = _reload_session()
    s.on_page_lost(0.0)
    s.on_page_attached(19.0)
    assert s.liveness.page_expired(31.0)
    s.on_page_rejoin_committed(31.0)
    assert s.liveness.page_deadline is not None
    assert _tick(s, 31.0) == "local_page_lost"


def test_transport_does_not_re_enter_after_the_deadline():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        s.liveness.expired = True
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        ws = _RecordingWS()
        conn = tw._attach(link, ws)
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = True
        await tw._handle_frame(link, conn, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest")
        return s, ws, conn

    try:
        s, ws, conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    # 不重入，但已回到房里的迟到 iframe 要被叫走：只发 stop，连接退役
    assert ws.sent == [{"type": "stop"}] and not conn.rejoined and conn.retired
    assert not any(e[0] in ("resend_hello", "resume") for e in s.log)


class _FailingDueOutbox(_Outbox):
    def __init__(self, log):
        super().__init__(log)
        self.fail_due = 1

    def due(self, now=None):
        if self.fail_due:
            self.fail_due -= 1
            raise RuntimeError("replay preparation failed")
        return super().due(now)


def test_failed_replay_preparation_rolls_the_rejoin_back(app):
    s = FakeSession()
    s.outbox = _FailingDueOutbox(s.log)
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)
    client = _client(app)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    _wait_page_lost(s, 1)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        ws.receive_text()
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        _barrier(ws, s)
        # 失败时：outbox 重新暂停、页面期限仍在（没有 page_back），不留半恢复状态
        assert PAUSE_PAGE_RELOAD in s.outbox.paused
        assert "page_back" not in s.liveness.events
        ws.send_text(json.dumps({"type": "state", "state": "connected", "peer_present": True, "remote_video": False}))
        assert json.loads(ws.receive_text())["type"] == "send"
        assert json.loads(ws.receive_text())["type"] == "media"
        _barrier(ws, s)
        assert s.liveness.events[-1] == "page_back"


def test_failed_preflight_hook_does_not_open_the_credentials_gate(app):
    class _Broken(FakeSession):
        async def on_preflight(self, caps):
            raise RuntimeError("state update failed")

    s = _Broken()
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)  # 预检本身通过，但 runtime 没处理好
        _barrier(ws, s)
        msg = tw.build_credentials_message(_creds(), side="guest", crop="upper", codec="vp9")
        assert not _run(ws, s.send, msg)


# ── 重入：快照按实际下发检查、提交失败回滚、stats 触发重试（wehos 第四轮） ──


def _reload_and_join(client, s, ws_fn):
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    _wait_page_lost(s, 1)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        ws.receive_text()  # credentials
        return ws_fn(ws)


def _joined(ws, state: str = "joined") -> None:
    ws.send_text(json.dumps({"type": "state", "state": state, "peer_present": True, "remote_video": False}))


def test_rejoin_accepts_a_read_only_mapping_snapshot(app):
    import types

    class _Proxy(FakeSession):
        def media_snapshot(self):
            # json.dumps(MappingProxyType) 会抛 TypeError，但 {**proxy, "type": "media"} 能正常下发
            return types.MappingProxyType(dict(self.snapshot))

    s = _Proxy()
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)

    def scenario(ws):
        _joined(ws)
        hello, media = json.loads(ws.receive_text()), json.loads(ws.receive_text())
        _barrier(ws, s)
        assert s.liveness.events[-1] == "page_back"
        return hello, media

    hello, media = _reload_and_join(_client(app), s, scenario)
    assert hello["type"] == "send"
    assert media == {"type": "media", "publish": True, "crop": "upper", "ladder": 0}


def test_oversize_snapshot_does_not_commit_the_rejoin(app, session):
    # 编码后超过 16 KB 的快照发不出去：不能先置 rejoined、清期限，再被 send_json 丢掉
    session.snapshot = {"publish": True, "crop": "upper", "ladder": 0, "pad": "x" * tw.FRAME_MAX_BYTES}

    def scenario(ws):
        _joined(ws)
        _barrier(ws, session)
        assert ("resend_hello",) not in session.log
        assert PAUSE_PAGE_RELOAD in session.outbox.paused
        assert "page_back" not in session.liveness.events
        assert not tw._links[(VISIT_ID, "guest")].conn.rejoined

    _reload_and_join(_client(app), session, scenario)


def test_failed_commit_rolls_the_rejoin_back_and_is_retried(app):
    class _FlakyCommit(FakeSession):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def on_page_rejoin_committed(self, now):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("commit failed")
            super().on_page_rejoin_committed(now)

    s = _FlakyCommit()
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)

    def scenario(ws):
        _joined(ws)
        _barrier(ws, s)
        # 清期限失败：不置 rejoined、outbox 重新暂停，否则之后的上报全被 rejoined 拦住、期限永远不清
        assert not s.fail_once
        assert not tw._links[(VISIT_ID, "guest")].conn.rejoined
        assert PAUSE_PAGE_RELOAD in s.outbox.paused
        _sync(ws)  # 下一条 stats 整套重做
        _barrier(ws, s)  # 先断言再收：回归时直接失败，不挂在 receive_text 上
        assert ("resend_hello",) in s.log
        hello, media = json.loads(ws.receive_text()), json.loads(ws.receive_text())
        _barrier(ws, s)
        assert s.liveness.events[-1] == "page_back"
        return hello, media

    hello, media = _reload_and_join(_client(app), s, scenario)
    assert hello["type"] == "send" and media["type"] == "media"


def test_stats_retries_a_rolled_back_rejoin_while_in_the_room(app, session):
    # iframe 一次入房只报一次 joined：准备失败回滚后，靠下一条 5 s stats 再试
    session.snapshot = {"publish": True, "bad": {1}}

    def scenario(ws):
        _joined(ws)
        _barrier(ws, session)
        assert ("resend_hello",) not in session.log
        session.snapshot = {"publish": True, "crop": "upper", "ladder": 0}
        _sync(ws)
        _barrier(ws, session)  # 先断言再收：回归时直接失败，不挂在 receive_text 上
        assert ("resend_hello",) in session.log
        return json.loads(ws.receive_text()), json.loads(ws.receive_text())

    hello, media = _reload_and_join(_client(app), session, scenario)
    assert hello["type"] == "send"
    assert media == {"type": "media", "publish": True, "crop": "upper", "ladder": 0}


def test_stats_does_not_rejoin_a_connection_that_is_not_in_the_room(app, session):
    def scenario(ws):
        # 还没报过入房：stats 不触发重入
        _sync(ws)
        _barrier(ws, session)
        assert ("resend_hello",) not in session.log
        # 入房但准备失败，随后 SDK 又掉线（reconnecting）：不在房内就不靠 stats 重试
        session.snapshot = {"publish": True, "bad": {1}}
        _joined(ws)
        _joined(ws, "reconnecting")
        session.snapshot = {"publish": True, "crop": "upper", "ladder": 0}
        _sync(ws)
        _barrier(ws, session)
        assert ("resend_hello",) not in session.log
        assert PAUSE_PAGE_RELOAD in session.outbox.paused
        # 重新入房的上报照常完成重入
        _joined(ws, "connected")
        return json.loads(ws.receive_text()), json.loads(ws.receive_text())

    hello, media = _reload_and_join(_client(app), session, scenario)
    assert hello["type"] == "send" and media["type"] == "media"


def test_send_json_and_the_snapshot_check_share_one_encoder():
    big = {"type": "media", "pad": "x" * tw.FRAME_MAX_BYTES}
    with pytest.raises(tw._DownlinkRejected):
        tw._encode_downlink(big)
    with pytest.raises(tw._DownlinkRejected):
        tw._media_frame({"pad": "x" * tw.FRAME_MAX_BYTES})
    msg, text = tw._media_frame({"publish": False})
    assert msg == {"publish": False, "type": "media"} and json.loads(text) == msg


def test_late_iframe_is_stopped_and_disconnected(app, session):
    def scenario(ws):
        session.liveness.expired = True
        _joined(ws)
        assert json.loads(ws.receive_text()) == {"type": "stop"}
        # 轮询而不是阻塞等关闭帧：回归时直接失败，不会挂住
        _wait_until(lambda: not tw.is_transport_attached(VISIT_ID, "guest"))

    _reload_and_join(_client(app), session, scenario)
    assert ("resend_hello",) not in session.log
    assert PAUSE_PAGE_RELOAD in session.outbox.paused
