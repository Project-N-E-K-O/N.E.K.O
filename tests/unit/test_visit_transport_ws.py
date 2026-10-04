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
        role=side, visit_id=VISIT_ID, char_tag="c" * 32, transport="trtc",
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
    vrs.activate_visit_route(LANLAN)
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
    vrs.activate_visit_route(LANLAN)
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        _sync(ws)
    assert s.issued == 0


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
        assert not _run(ws, tw.send_downlink, VISIT_ID, "guest", first)
        assert _run(ws, tw.send_downlink, VISIT_ID, "guest", {**first, "refresh": True})
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
    session.log.clear()
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        msgs = _rejoin(ws)
        ws.send_text(json.dumps({"type": "stats"}))
    assert [m["type"] for m in msgs] == ["credentials", "send", "media"]
    assert msgs[1]["payload"]["t"] == "hello"
    assert msgs[2] == {"type": "media", "publish": True, "crop": "upper", "ladder": 0}
    assert session.liveness.events == ["page_lost", "page_back", "page_lost"]
    assert ("resume", PAUSE_PAGE_RELOAD) in session.log


def test_reload_snapshot_is_taken_from_the_runtime_not_hardcoded(app):
    host = FakeSession(side="host")
    host.snapshot = {"subscribe": False, "peer_vid": "g_" + "2" * 24}
    tw.register_transport_session(host)
    vrs.activate_visit_route(LANLAN)
    url = f"/api/visit/transport/ws?visit_id={VISIT_ID}&side=host"
    client = _client(app)
    with client.websocket_connect(url, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
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
            assert session.liveness.events == ["page_back"]


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
        await task
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
        sent = await asyncio.wait_for(tw.send_downlink(VISIT_ID, "guest", {"type": "media", "publish": False}), 1)
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
    vrs.activate_visit_route(LANLAN)
    with _client(app).websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        _sync(ws)
        msg = tw.build_credentials_message(_creds(), side="guest", crop="upper", codec="vp9")
        assert _run(ws, tw.send_downlink, VISIT_ID, "guest", msg)
        assert json.loads(ws.receive_text())["type"] == "credentials"
        assert not _run(ws, tw.send_downlink, VISIT_ID, "guest", msg)


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
