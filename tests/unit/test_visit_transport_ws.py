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
        # New socket reconnects: replace the deadline with the absolute page-reload deadline.
        self.events.append("page_armed")

    expired = False
    reload_state = ("departed", "deadline", False)

    def page_expired(self, now: float) -> bool:
        return self.expired

    def page_reload_state(self):
        return self.reload_state

    def restore_page_reload_state(self, state) -> None:
        self.events.append("page_restored")
        self.reload_state = state


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

    def write_failed(self, frame, now=None) -> None:
        self.log.append(("write_failed", frame.seq))


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

    def on_frame_sent(self, frame):
        self.log.append(("frame_sent", frame.payload.get("t")))


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


# ── Routing table ─────────────────────────────────────────────────────────────


def test_route_is_relative_and_mounts_once_under_the_prefix(app):
    paths = [effective_path(r) for r in iter_routes(app.routes)]
    assert "/api/visit/transport/ws" in paths
    assert not any(p.startswith("/api/visit/api/visit") for p in paths)
    assert [getattr(r, "path", "") for r in tw.router.routes] == ["/transport/ws"]


# ── Handshake: loopback / proxy headers / Origin / auth ────────────────────────────────


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
    # With proxy_headers=True, uvicorn already rewrote client.host to XFF's 127.0.0.1.
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


# ── Capability gate and credentials ───────────────────────────────────────────────────────


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
    vrs.activate_visit_route(LANLAN, visit_id="ZZZZZZZZZZZZZZZZZZZZZZ")  # The same character has already started another visit.
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
        # Runtime adds another initial issuance: reject; renewal: allow.
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


# ── Uplink forwarding ───────────────────────────────────────────────────────────


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


# ── Reload / supersession ────────────────────────────────────────────────────────


def _rejoin(ws) -> list[dict]:
    _auth(ws)
    _preflight(ws)
    out = [json.loads(ws.receive_text())]
    _sdk_ok(ws)
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
        _sdk_ok(ws)
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
    # Reconnection only changes to the absolute deadline (page_armed); page_back requires successful reentry.
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
        _sdk_ok(ws)
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
            # Superseded old-connection disconnection does not count as page loss.
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
    # TestClient stops scheduling an old handler after server closure; drive _handle_frame directly to test this timing.
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
        # Page reloads while awaiting Servers: the new connection passed preflight and awaits its own credentials.
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

    # Deliberately separate wall and monotonic clocks greatly: callbacks must receive monotonic time.
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
        new = tw._attach(link, _RecordingWS())  # Complete synchronously without waiting for the old connection's send lock.
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
        conn.retired = True  # Supersession occurs while it queues for the lock.
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
        tw.register_transport_session(fresh)  # Replace runtime for the same (visit_id, side).
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
        _sdk_ok(ws)
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        _barrier(ws, s)  # Avoid _sync: stats also retries; verify specifically the next connected report here.
        assert not s.fail_once and ("resend_hello",) not in s.log
        ws.send_text(json.dumps({"type": "state", "state": "connected", "peer_present": True,
                                 "remote_video": False}))
        hello = json.loads(ws.receive_text())
        media = json.loads(ws.receive_text())
    assert hello["type"] == "send" and media["type"] == "media"


def test_cancelled_handler_still_starts_the_page_grace(monkeypatch):
    # Server shutdown / ASGI handler cancellation makes the first await in finally raise CancelledError:
    # page-loss handling must precede every await.
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
            await asyncio.Event().wait()  # No further frames: wait until cancellation.
            raise AssertionError("unreachable")

        async def close(self, code=1000, reason=""):
            # anyio cancellation remains active: every await in handler cleanup raises CancelledError again.
            raise asyncio.CancelledError()

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        task = asyncio.ensure_future(tw.visit_transport_ws(_HandlerWS()))
        while not tw.is_transport_attached(VISIT_ID, "guest"):
            await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):  # Cancellation is exactly this test's intended outcome.
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
        tw._attach(link, _RecordingWS())  # Page reload: new connection takes over.
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
        # Servers returns as the visit ends: handler resumes before the close task runs.
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
        _sdk_ok(ws)
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        _barrier(ws, s)
        # Failed attempt must not start reentry: leave outbox unchanged on on_state failure, and unresumed on snapshot failure.
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
    # New iframe has not joined: outbox stays stopped; send hello / replays to its own socket only after reentry.
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


# ── Local-source classification ───────────────────────────────────────────────────────


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


# ── Cases added during review (wehos, 593d997) ─────────────────────────────────────


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
    # Reproduce: user turns camera off during hello; runtime sends media{publish:false}; old snapshot must not arrive last and restart publishing.
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1  # This connection follows a reload.
        tasks = []

        def _camera_off():
            s.snapshot = {"publish": False, "crop": "upper", "ladder": 0}
            tasks.append(asyncio.ensure_future(s.send({"type": "media", "publish": False, "crop": "upper", "ladder": 0})))

        ws = _SlowWS(on_first_send=_camera_off)
        conn = tw._attach(link, ws)
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = conn.sdk_seen = conn.sdk_ok = True
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


def test_a_rejoin_whose_replay_write_fails_keeps_the_original_reload_deadline():
    import asyncio

    class _BrokenWS(_RecordingWS):
        async def send_text(self, text):
            raise OSError("socket gone")                  # The first replay frame cannot be written.

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1  # This connection follows a reload.
        original = object()
        s.liveness.reload_state = original                # This reload's original absolute deadline.
        conn = tw._attach(link, _BrokenWS())
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = conn.sdk_seen = conn.sdk_ok = True
        await tw._handle_frame(link, conn, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest")
        return s, original

    try:
        s, original = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    events = s.liveness.events
    assert events[-2:] == ["page_restored", "page_lost"]   # After abandonment, restore the original deadline and recalculate socket phase from this disconnection.
    assert s.liveness.reload_state is original        # Repeated reconnection cannot extend the absolute deadline.
    assert [e for e in s.outbox.log if e[0] == "write_failed"]  # Roll back bookkeeping for unwritten frames (like the pump); do not count them as sent.


def test_a_superseded_rejoin_whose_replay_write_fails_does_not_touch_the_deadline():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        gate = asyncio.Event()

        class _StuckThenBroken(_RecordingWS):
            async def send_text(self, text):
                await gate.wait()                         # Replay blocked by backpressure.
                raise OSError("write timed out")

        old = tw._attach(link, _StuckThenBroken())
        old.preflight_seen = old.preflight_ok = old.credentials_sent = old.sdk_seen = old.sdk_ok = True
        rejoining = asyncio.ensure_future(
            tw._handle_frame(link, old, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest"))
        await asyncio.sleep(0.05)
        tw._attach(link, _RecordingWS())                  # Iframe reconnects with a new connection, superseding the old.
        before = list(s.liveness.events)
        gate.set()                                        # Only now does the old connection's write fail.
        await rejoining
        return s, before

    try:
        s, before = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert "page_restored" not in s.liveness.events[len(before):]   # Superseded connections do not restore deadlines or overwrite the new connection's reentry.


def test_a_rejoin_restore_skips_when_a_new_socket_attached_meanwhile():
    import asyncio

    class _BrokenWS(_RecordingWS):
        async def send_text(self, text):
            raise OSError("socket gone")

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        real_lost = s.on_page_lost

        def lost_then_new_socket(now):
            real_lost(now)
            link.conn = tw._Connection(websocket=_RecordingWS(), reattach=True)   # A new connection arrives immediately after abandonment.

        s.on_page_lost = lost_then_new_socket
        conn = tw._attach(link, _BrokenWS())
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = conn.sdk_seen = conn.sdk_ok = True
        await tw._handle_frame(link, conn, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest")
        return s

    try:
        s = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert "page_restored" not in s.liveness.events          # The new connection owns the deadline; do not restore the old one.


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
        _sdk_ok(ws)
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        _barrier(ws, session)
        # Unserializable snapshot: skip all reentry (no hello, outbox still paused, grace uncleared); do not treat socket as disconnected.
        assert tw.is_transport_attached(VISIT_ID, "guest")
        assert PAUSE_PAGE_RELOAD in session.outbox.paused
        assert "page_back" not in session.liveness.events
        assert session.liveness.events.count("page_lost") == 1
        # After snapshot recovery, the next report completes reentry.
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
        _preflight(ws, ok=False, reason="no_webrtc")  # New iframe fails preflight: it never rejoins.
        _barrier(ws, s)
        # Auth neither clears the deadline nor restarts 20 s; switch to absolute (departure + 35 − 5), retaining the origin.
        assert s.liveness.page_departed_at == departed
        assert s.liveness.page_deadline == pytest.approx(
            departed + vs.VISIT_PEER_REJOIN_GRACE_S - vs.VISIT_PAGE_REJOIN_SAFETY_S)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        ws.receive_text()
        _sdk_ok(ws)
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        ws.receive_text()
        ws.receive_text()
        _barrier(ws, s)
        assert s.liveness.page_deadline is None  # Clear only on successful reentry.


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
            # Old iframe superseded, new one not yet in room: require a deadline.
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
    # Sole downlink entry is session.send (bound to the registered session).
    assert not hasattr(tw, "send_downlink")


def test_credentials_message_tier_comes_from_the_credentials():
    import dataclasses

    creds = dataclasses.replace(_creds(), tier="hd1200")
    msg = tw.build_credentials_message(creds, side="guest", crop="upper", codec="vp9")
    # Profile, bitrate, and frame rate come from credentials' profile, no longer hardcoded sd600.
    assert msg["tier"] == "hd1200"
    assert msg["publish"]["bitrate_kbps"] == vs.VISIT_TIERS["hd1200"]["video_kbps"]
    assert msg["publish"]["fps"] == vs.VISIT_TIERS["hd1200"]["fps"]
    # This profile has no full-body crop: reject.
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


# ── Absolute page-reload deadline (design §4.8 VISIT_PEER_REJOIN_GRACE_S row) ──────


def _reload_session():
    from main_logic.visit.liveness import VisitLiveness

    s = FakeSession()
    s.liveness = VisitLiveness("guest", -1000.0)
    s.liveness.on_peer_verified(-1000.0)  # Already in session: both hellos verified and acknowledged.
    s.liveness.on_hello_acked(-1000.0)
    s.liveness.on_ready(-1000.0)
    return s


def _tick(s, t: float):
    # Peer remains online: inspect only local-page deadline, preventing peer-heartbeat death from winning first.
    s.liveness.on_peer_message(t)
    return s.liveness.tick(t)


def test_reload_ws_back_at_19s_sdk_at_31s_ends_at_30s():
    # Design acceptance: WS returns at 19s, SDK delays until 31s → local_page_lost at local 30s (peer_left at peer 35s, consistent outcomes).
    s = _reload_session()
    s.on_page_lost(0.0)
    s.on_page_attached(19.0)
    assert _tick(s, 29.9) is None
    assert _tick(s, 30.0) == "local_page_lost"


def test_reload_ws_back_at_19s_sdk_at_21s_survives():
    # WS returns at 19s, SDK joins at 21s: cannot reuse 20s from disconnection.
    s = _reload_session()
    s.on_page_lost(0.0)
    s.on_page_attached(19.0)
    assert _tick(s, 20.5) is None
    s.on_page_rejoined(21.0)
    # Do not clear deadline before reentry preparation completes (rollback must remain possible); clear after commit.
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
    # Last successful send at -10s: peer declares death around +20s; local must return before -10 + 27 = 17s.
    s = _reload_session()
    s.liveness.on_message_sent(-10.0)
    s.on_page_lost(0.0)
    assert _tick(s, 16.9) is None
    assert _tick(s, 17.0) == "local_page_lost"


def test_flapping_page_never_extends_past_the_absolute_deadline():
    s = _reload_session()
    s.on_page_lost(0.0)
    s.on_page_attached(5.0)
    s.on_page_lost(6.0)  # Disconnect again: origin remains 0.
    s.on_page_attached(15.0)
    assert _tick(s, 29.9) is None
    assert _tick(s, 30.0) == "local_page_lost"


def test_superseding_a_live_socket_starts_the_reload_now():
    s = _reload_session()
    s.on_page_attached(100.0)  # Direct supersession without disconnection: start from supersession time.
    assert _tick(s, 129.9) is None
    assert _tick(s, 130.0) == "local_page_lost"


def test_sdk_stage_uses_the_absolute_deadline_not_the_caps_timer():
    # WS returns at 2s: reentry deadline is absolute 30s; VISIT_CAPS_SDK_TIMEOUT_S independently times the runtime capability gate.
    s = _reload_session()
    s.on_page_lost(0.0)
    s.on_page_attached(2.0)
    assert _tick(s, 29.9) is None
    assert _tick(s, 30.0) == "local_page_lost"


def test_second_drop_after_reconnect_gets_its_own_socket_budget():
    # Reproduce (wehos): disconnect t=0, return t=5, disconnect t=25; absolute min(30, 0 + 27) = 27, not immediate death at t=25.
    s = _reload_session()
    s.liveness.on_message_sent(0.0)
    s.on_page_lost(0.0)
    s.on_page_attached(5.0)
    s.on_page_lost(25.0)
    assert _tick(s, 25.0) is None
    assert _tick(s, 26.9) is None
    assert _tick(s, 27.0) == "local_page_lost"


def test_a_late_socket_cannot_revive_an_expired_page():
    # Disconnect t=0, WS deadline t=20; reconnect t=20.2 before tick runs: deadline must not shift.
    s = _reload_session()
    s.on_page_lost(0.0)
    s.on_page_attached(20.2)
    assert s.liveness.page_deadline == 20.0
    assert _tick(s, 20.5) == "local_page_lost"


def test_rejoin_after_the_deadline_is_refused():
    # SDK reports joined after absolute expiry but before the next tick: neither clear deadline nor resume outbox.
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
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = conn.sdk_seen = conn.sdk_ok = True
        await tw._handle_frame(link, conn, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest")
        assert conn.retired  # Retire synchronously: reject subsequent frames.
        await tw._handle_frame(link, conn, {"type": "recv", "from_vid": PEER_VID, "cmd": 2,
                                            "payload": {"t": "text"}}, 10, VISIT_ID, "guest")
        assert s.recvs == []
        await asyncio.sleep(0.05)  # Close in background.
        return s, ws, conn

    try:
        s, ws, conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    # No reentry: close the late iframe already back in room with 4404 (WS closure makes it leave), sending no downlink.
    assert ws.closed_with == tw.CLOSE_UNKNOWN_VISIT
    assert ws.sent == [] and not conn.rejoined
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
        _sdk_ok(ws)
        ws.send_text(json.dumps({"type": "state", "state": "joined", "peer_present": True, "remote_video": False}))
        _barrier(ws, s)
        # On failure: pause outbox again and restore page deadline (cleared before due(), which runs last); leave no partial restoration.
        assert PAUSE_PAGE_RELOAD in s.outbox.paused
        assert s.liveness.events[-2:] == ["page_back", "page_restored"]
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
        _preflight(ws)  # Preflight itself passes, but runtime handling fails.
        _barrier(ws, s)
        msg = tw.build_credentials_message(_creds(), side="guest", crop="upper", codec="vp9")
        assert not _run(ws, s.send, msg)


# ── Reentry: validate actual snapshot delivery, roll back failed commits, retry on stats (wehos fourth round) ──


def _reload_and_join(client, s, ws_fn):
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    _wait_page_lost(s, 1)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _preflight(ws)
        ws.receive_text()  # credentials
        _sdk_ok(ws)        # Each new connection must pass capability gate ③ before reentry.
        return ws_fn(ws)


def _sdk_ok(ws) -> None:
    ws.send_text(json.dumps({"type": "caps", "stage": "sdk", "transport_ok": True, "video_ok": True}))


def _joined(ws, state: str = "joined") -> None:
    ws.send_text(json.dumps({"type": "state", "state": state, "peer_present": True, "remote_video": False}))


def test_rejoin_accepts_a_read_only_mapping_snapshot(app):
    import types

    class _Proxy(FakeSession):
        def media_snapshot(self):
            # json.dumps(MappingProxyType) raises TypeError, but {**proxy, "type": "media"} can be delivered normally.
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
    # Encoded snapshots over 16 KB cannot be sent: do not first set rejoined / clear deadline, then let send_json discard them.
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
        # Deadline clearing fails: do not set rejoined; pause outbox again, or rejoined blocks later reports and the deadline never clears.
        assert not s.fail_once
        assert not tw._links[(VISIT_ID, "guest")].conn.rejoined
        assert PAUSE_PAGE_RELOAD in s.outbox.paused
        _sync(ws)  # Next stats retries the entire sequence.
        _barrier(ws, s)  # Assert before receiving: regressions fail directly rather than hanging on receive_text.
        # First resend_hello was recorded before commit failed; expect a second attempt with deadline cleared.
        assert s.log.count(("resend_hello",)) == 2
        assert s.liveness.events[-1] == "page_back"
        hello, media = json.loads(ws.receive_text()), json.loads(ws.receive_text())
        _barrier(ws, s)
        assert s.liveness.events[-1] == "page_back"
        return hello, media

    hello, media = _reload_and_join(_client(app), s, scenario)
    assert hello["type"] == "send" and media["type"] == "media"


def test_stats_retries_a_rolled_back_rejoin_while_in_the_room(app, session):
    # Iframe reports joined once per room entry: after preparation rollback, retry via the next 5s stats.
    session.snapshot = {"publish": True, "bad": {1}}

    def scenario(ws):
        _joined(ws)
        _barrier(ws, session)
        assert ("resend_hello",) not in session.log
        session.snapshot = {"publish": True, "crop": "upper", "ladder": 0}
        _sync(ws)
        _barrier(ws, session)  # Assert before receiving: regressions fail directly rather than hanging on receive_text.
        assert ("resend_hello",) in session.log
        return json.loads(ws.receive_text()), json.loads(ws.receive_text())

    hello, media = _reload_and_join(_client(app), session, scenario)
    assert hello["type"] == "send"
    assert media == {"type": "media", "publish": True, "crop": "upper", "ladder": 0}


def test_stats_does_not_rejoin_a_connection_that_is_not_in_the_room(app, session):
    def scenario(ws):
        # No join reported yet: stats does not trigger reentry.
        _sync(ws)
        _barrier(ws, session)
        assert ("resend_hello",) not in session.log
        # Joined but preparation failed, then SDK disconnected again (reconnecting): stats must not retry while out of room.
        session.snapshot = {"publish": True, "bad": {1}}
        _joined(ws)
        _joined(ws, "reconnecting")
        session.snapshot = {"publish": True, "crop": "upper", "ladder": 0}
        _sync(ws)
        _barrier(ws, session)
        assert ("resend_hello",) not in session.log
        assert PAUSE_PAGE_RELOAD in session.outbox.paused
        # A new join report completes reentry normally.
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


def test_late_iframe_is_closed_when_it_reports_joined(app, session):
    def scenario(ws):
        # Deadline falls between credential acquisition and room entry: close defensively on the join report.
        session.liveness.expired = True
        _joined(ws)
        # Poll instead of blocking for close frames: regressions fail directly without hanging.
        _wait_until(lambda: not tw.is_transport_attached(VISIT_ID, "guest"))

    _reload_and_join(_client(app), session, scenario)
    assert ("resend_hello",) not in session.log
    assert PAUSE_PAGE_RELOAD in session.outbox.paused



def test_failed_commit_restores_the_page_deadline_and_keeps_the_outbox_frames(app):
    # Runtime overrides commit: clear deadline via super, then raise; restore deadline, and due() has not consumed frames yet.
    class _ClearsThenFails(FakeSession):
        def on_page_rejoin_committed(self, now):
            super().on_page_rejoin_committed(now)
            raise RuntimeError("commit failed after clearing")

    s = _ClearsThenFails()
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)

    def scenario(ws):
        _joined(ws)
        _barrier(ws, s)
        assert s.liveness.events[-2:] == ["page_back", "page_restored"]
        assert s.outbox.hello_pending  # due() not called: one-shot hello replay remains available.
        assert PAUSE_PAGE_RELOAD in s.outbox.paused

    _reload_and_join(_client(app), s, scenario)


def test_failed_hook_or_ambiguous_state_does_not_leave_a_stale_in_room():
    import asyncio

    class _BrokenOnReconnecting(FakeSession):
        async def on_state(self, msg):
            if msg.get("state") == "reconnecting":
                raise RuntimeError("state hook failed")
            await super().on_state(msg)

    async def scenario():
        tw._reset_for_tests()
        s = _BrokenOnReconnecting()
        s.snapshot = {"publish": True, "bad": {1}}  # First reentry preparation fails and rolls back.
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        conn = tw._attach(link, _RecordingWS())
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = conn.sdk_seen = conn.sdk_ok = True
        await tw._handle_frame(link, conn, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest")
        assert conn.in_room and not conn.rejoined
        # error is not a room entry/exit state: remain in room and allow stats to retry.
        await tw._handle_frame(link, conn, {"type": "state", "state": "error", "error_code": "x"}, 10,
                               VISIT_ID, "guest")
        in_room_after_error = conn.in_room
        # reconnecting hook fails: do not retain stale in_room=True, or stats would reenter an iframe outside the room.
        await tw._handle_frame(link, conn, {"type": "state", "state": "reconnecting"}, 10, VISIT_ID, "guest")
        s.snapshot = {"publish": True, "crop": "upper", "ladder": 0}
        await tw._handle_frame(link, conn, {"type": "stats"}, 10, VISIT_ID, "guest")
        return s, conn, in_room_after_error

    try:
        s, conn, in_room_after_error = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert in_room_after_error
    assert not conn.in_room and not conn.rejoined
    assert ("resend_hello",) not in s.log


def test_rejoin_fallback_snapshot_never_overrides_a_runtime_media():
    import asyncio

    class _SnapshotGone(FakeSession):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def media_snapshot(self):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("snapshot unavailable")
            return {"publish": True, "crop": "upper", "ladder": 0}

    class _SendsMediaDuringFrames(_RecordingWS):
        def __init__(self, session):
            super().__init__()
            self.session = session

        async def send_text(self, text):
            import asyncio as _aio

            await super().send_text(text)
            if json.loads(text).get("type") == "send":
                # Runtime turns camera off during hello (send lock held; runtime media queues behind it).
                _aio.ensure_future(self.session.send({"type": "media", "publish": False}))
                await _aio.sleep(0)

    async def scenario():
        tw._reset_for_tests()
        s = _SnapshotGone()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        ws = _SendsMediaDuringFrames(s)
        conn = tw._attach(link, ws)
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = conn.sdk_seen = conn.sdk_ok = True
        await tw._handle_frame(link, conn, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest")
        await asyncio.sleep(0.05)  # Let runtime media queued behind the lock finish sending.
        return ws

    try:
        ws = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    media = [m for m in ws.sent if m.get("type") == "media"]
    assert media == [{"type": "media", "publish": False}]


def test_rejoin_fallback_snapshot_is_sent_when_nothing_newer_went_out():
    import asyncio

    class _SnapshotGone(FakeSession):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def media_snapshot(self):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("snapshot unavailable")
            return {"publish": True, "crop": "upper", "ladder": 0}

    async def scenario():
        tw._reset_for_tests()
        s = _SnapshotGone()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        ws = _RecordingWS()
        conn = tw._attach(link, ws)
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = conn.sdk_seen = conn.sdk_ok = True
        await tw._handle_frame(link, conn, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest")
        return ws

    try:
        ws = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert [m for m in ws.sent if m.get("type") == "media"] == [
        {"publish": True, "crop": "upper", "ladder": 0, "type": "media"}]


def test_pre_encoded_text_is_still_size_checked():
    import asyncio

    async def scenario():
        conn = tw._Connection(websocket=_RecordingWS(), reattach=False)
        # Valid JSON: without size checking _RecordingWS accepts it, causing the test to fail.
        big = json.dumps({"type": "media", "pad": "x" * tw.FRAME_MAX_BYTES})
        return await conn.send_json({"type": "media"}, text=big), conn

    ok, conn = asyncio.run(scenario())
    assert not ok and conn.websocket.sent == []


def test_stats_does_not_end_the_socket_when_the_rejoin_check_raises():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()

        def _boom(now):
            raise RuntimeError("liveness broken")

        s.liveness.page_expired = _boom
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        conn = tw._attach(link, _RecordingWS())
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = conn.sdk_seen = conn.sdk_ok = True
        conn.in_room = True
        await tw._handle_frame(link, conn, {"type": "stats"}, 10, VISIT_ID, "guest")
        return conn

    try:
        conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert not conn.rejoined and not conn.closed



def test_backpressured_socket_does_not_block_closing_the_late_iframe(monkeypatch):
    import asyncio

    monkeypatch.setattr(tw, "CLOSE_LOCK_WAIT_S", 0.05)

    class _StuckWS(_RecordingWS):
        async def send_text(self, text):
            await asyncio.Event().wait()  # Backpressure: write never completes.

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        s.liveness.expired = True
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        ws = _StuckWS()
        conn = tw._attach(link, ws)
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = conn.sdk_seen = conn.sdk_ok = True
        await asyncio.wait_for(
            tw._handle_frame(link, conn, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest"), 1)
        await asyncio.sleep(0.3)
        return ws, conn

    try:
        ws, conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    # Close unwritable sockets too, without any preceding downlink.
    assert conn.closed and ws.closed_with == tw.CLOSE_UNKNOWN_VISIT



def test_page_back_after_the_deadline_is_closed_on_attach(app, session):
    client = _client(app)
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        _sync(ws)
    _wait_page_lost(session, 1)
    session.liveness.expired = True
    with client.websocket_connect(URL, headers={"origin": ORIGIN}) as ws:
        _auth(ws)
        # Wait for attach, then poll closure (regressions fail directly instead of hanging on receive).
        _wait_until(lambda: "page_armed" in session.liveness.events)
        _wait_until(lambda: not tw.is_transport_attached(VISIT_ID, "guest"))
        _expect_close(ws, tw.CLOSE_UNKNOWN_VISIT)
    # Neither preflight nor credential acquisition was reached.
    assert session.preflights == [] and session.issued == 0


def test_deadline_passing_before_preflight_issues_no_credentials():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        ws = _RecordingWS()
        conn = tw._attach(link, ws)
        s.liveness.expired = True  # Expires after attach but before preflight.
        await tw._handle_frame(link, conn, {"type": "caps", "stage": "preflight", "visit_id": VISIT_ID,
                                            "side": "guest", "preflight_ok": True,
                                            "is_secure_context": True, "ua": "x"}, 10, VISIT_ID, "guest")
        await asyncio.sleep(0.05)
        return s, ws, conn

    try:
        s, ws, conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    # Do not obtain Servers credentials for expired pages (issuance records and quotas have side effects).
    assert s.issued == 0 and ws.sent == []
    assert conn.retired and ws.closed_with == tw.CLOSE_UNKNOWN_VISIT



def test_rejected_runtime_media_does_not_suppress_the_fallback_snapshot():
    import asyncio

    class _SnapshotGone(FakeSession):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def media_snapshot(self):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("snapshot unavailable")
            return {"publish": True, "crop": "upper", "ladder": 0}

    class _RejectedMediaDuringFrames(_RecordingWS):
        def __init__(self, session):
            super().__init__()
            self.session = session

        async def send_text(self, text):
            await super().send_text(text)
            if json.loads(text).get("type") == "send":
                # Runtime sent unsendable media (unserializable): undelivered media must not suppress the fallback snapshot.
                asyncio.ensure_future(self.session.send({"type": "media", "bad": {1}}))
                await asyncio.sleep(0)

    async def scenario():
        tw._reset_for_tests()
        s = _SnapshotGone()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        ws = _RejectedMediaDuringFrames(s)
        conn = tw._attach(link, ws)
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = conn.sdk_seen = conn.sdk_ok = True
        await tw._handle_frame(link, conn, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest")
        await asyncio.sleep(0.05)
        return ws

    try:
        ws = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert [m for m in ws.sent if m.get("type") == "media"] == [
        {"publish": True, "crop": "upper", "ladder": 0, "type": "media"}]


def test_non_string_state_does_not_close_the_socket():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        conn = tw._attach(link, _RecordingWS())
        conn.credentials_sent = True
        await tw._handle_frame(link, conn, {"type": "state", "state": ["joined"]}, 10, VISIT_ID, "guest")
        return conn

    try:
        conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert not conn.in_room and not conn.closed


def test_rollback_restores_the_deadline_even_when_pausing_fails(app):
    class _PauseFails(_Outbox):
        def pause(self, now=None, reason="transport"):
            if reason == PAUSE_PAGE_RELOAD and ("resend_hello",) in self.log:
                raise RuntimeError("pause failed")
            super().pause(now, reason)

    class _ClearsThenFails(FakeSession):
        def on_page_rejoin_committed(self, now):
            super().on_page_rejoin_committed(now)
            raise RuntimeError("commit failed after clearing")

    s = _ClearsThenFails()
    s.outbox = _PauseFails(s.log)
    tw.register_transport_session(s)
    vrs.activate_visit_route(LANLAN, visit_id=VISIT_ID)

    def scenario(ws):
        _joined(ws)
        _barrier(ws, s)
        assert s.liveness.events[-2:] == ["page_back", "page_restored"]

    _reload_and_join(_client(app), s, scenario)



class _BufferedWS:
    """A server-side websocket whose ``receive`` never yields while frames are buffered
    (like uvicorn's websockets implementation), for driving ``visit_transport_ws`` directly."""

    def __init__(self, frames):
        from types import SimpleNamespace

        from starlette.websockets import WebSocketState

        self.client = SimpleNamespace(host="127.0.0.1", port=50123)
        self.headers = {"origin": ORIGIN}
        self.url = SimpleNamespace(hostname="testserver")
        self.query_params = {"visit_id": VISIT_ID, "side": "guest"}
        self.frames = [json.dumps(f) for f in frames]
        self.sent: list[dict] = []
        self.closed_with = None
        self.application_state = WebSocketState.CONNECTED

    async def accept(self):
        return None

    async def receive(self):
        if self.frames:
            return {"type": "websocket.receive", "text": self.frames.pop(0)}
        return {"type": "websocket.disconnect"}

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000, reason=""):
        from starlette.websockets import WebSocketState

        if self.closed_with is None:
            self.closed_with = code
        self.application_state = WebSocketState.DISCONNECTED


def test_buffered_frames_cannot_turn_the_late_page_close_into_1000(monkeypatch):
    import asyncio

    monkeypatch.setattr(local_guard, "AUTOSTART_CSRF_TOKEN", TOKEN)
    monkeypatch.setattr(vs, "NEKO_VISIT_ALLOW_NONLOCAL", False)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)

    class _ExpiresAfterIssue(FakeSession):
        async def issue_credentials(self):
            msg = await super().issue_credentials()
            self.liveness.expired = True  # Expire immediately after credentials are obtained.
            return msg

    async def scenario():
        tw._reset_for_tests()
        s = _ExpiresAfterIssue()
        tw.register_transport_session(s)
        tw._links[(VISIT_ID, "guest")].connections_seen = 1  # Replacement connection after page reload.
        ws = _BufferedWS([
            {"type": "auth", "csrf_token": TOKEN},
            {"type": "caps", "stage": "preflight", "visit_id": VISIT_ID, "side": "guest",
             "preflight_ok": True, "is_secure_context": True, "ua": "x"},
            {"type": "state", "state": "joined"},
            {"type": "state", "state": "connected"},
            {"type": "stats"},
        ])
        await tw.visit_transport_ws(ws)
        await asyncio.sleep(0.05)
        return ws

    try:
        ws = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    # 1000 is not terminal for iframe (it reconnects again); require 4404.
    assert ws.closed_with == tw.CLOSE_UNKNOWN_VISIT


def test_late_page_frames_reach_no_runtime_hook():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        conn = tw._attach(link, _RecordingWS())
        s.liveness.expired = True  # Expires after attach, before preflight.
        await tw._handle_frame(link, conn, {"type": "caps", "stage": "preflight", "visit_id": VISIT_ID,
                                            "side": "guest", "preflight_ok": False,
                                            "is_secure_context": True, "ua": "x"}, 10, VISIT_ID, "guest")
        return s, conn

    try:
        s, conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    # Expired-page preflight neither writes route state nor reaches runtime (otherwise preflight_ok:false ends as unsupported).
    assert s.preflights == [] and conn.retired



def test_unhashable_reason_or_state_does_not_close_the_socket():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        conn = tw._attach(link, _RecordingWS())
        await tw._handle_frame(link, conn, {"type": "caps", "stage": "preflight", "visit_id": VISIT_ID,
                                            "side": "guest", "preflight_ok": False, "reason": ["x"],
                                            "is_secure_context": True, "ua": "x"}, 10, VISIT_ID, "guest")
        await tw._handle_frame(link, conn, {"type": "state", "state": {"a": 1}}, 10, VISIT_ID, "guest")
        return s, conn

    try:
        s, conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert s.preflights[0]["reason"] is None
    # Runtime receives sanitized state.
    assert s.states[-1]["state"] is None and not conn.closed
    assert tw._sdk_caps({"reason": ["x"]})["reason"] is None



def test_credentials_are_not_sent_when_the_deadline_passes_during_the_issue():
    import asyncio

    class _ExpiresDuringIssue(FakeSession):
        async def issue_credentials(self):
            msg = await super().issue_credentials()
            self.liveness.expired = True  # Expires during the Servers round trip.
            return msg

    async def scenario():
        tw._reset_for_tests()
        s = _ExpiresDuringIssue()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        ws = _RecordingWS()
        conn = tw._attach(link, ws)
        await tw._handle_frame(link, conn, {"type": "caps", "stage": "preflight", "visit_id": VISIT_ID,
                                            "side": "guest", "preflight_ok": True,
                                            "is_secure_context": True, "ua": "x"}, 10, VISIT_ID, "guest")
        await asyncio.sleep(0.05)
        return s, ws, conn

    try:
        s, ws, conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    # Obtained but not sent: otherwise iframe joins with the same vid before being closed.
    assert s.issued == 1 and ws.sent == []
    assert ws.closed_with == tw.CLOSE_UNKNOWN_VISIT


def test_unknown_deadline_after_preflight_issues_no_credentials():
    import asyncio

    class _BrokenAfterPreflight(FakeSession):
        async def on_preflight(self, caps):
            await super().on_preflight(caps)

            def _boom(now):
                raise RuntimeError("liveness broken")

            self.liveness.page_expired = _boom

    async def scenario():
        tw._reset_for_tests()
        s = _BrokenAfterPreflight()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        ws = _RecordingWS()
        conn = tw._attach(link, ws)
        await tw._handle_frame(link, conn, {"type": "caps", "stage": "preflight", "visit_id": VISIT_ID,
                                            "side": "guest", "preflight_ok": True,
                                            "is_secure_context": True, "ua": "x"}, 10, VISIT_ID, "guest")
        return s, conn

    try:
        s, conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    # Unreadable deadline: skip credential acquisition with side effects (consistent with reentry).
    assert s.issued == 0 and not conn.closed



def test_a_send_failure_retires_the_socket():
    import asyncio

    class _BrokenWS(_RecordingWS):
        async def send_text(self, text):
            raise RuntimeError("socket gone")

    async def scenario():
        conn = tw._Connection(websocket=_BrokenWS(), reattach=False)
        ok = await conn.send_json({"type": "media", "publish": True})
        await asyncio.sleep(0.05)
        return ok, conn

    ok, conn = asyncio.run(scenario())
    # Unwritable means cleanup: receive loop exits and starts a page-loss deadline, leaving no committed-but-unsent reentry.
    assert not ok and conn.retired and conn.closed
    assert conn.websocket.closed_with == tw.CLOSE_SEND_FAILED



def test_rejoin_checks_and_commits_with_one_clock_reading():
    import asyncio

    from main_logic.visit.liveness import VisitLiveness

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        s.liveness = VisitLiveness("guest", -100.0)
        s.liveness.on_peer_verified(-100.0)
        s.liveness.on_hello_acked(-100.0)
        s.liveness.on_ready(-100.0)
        s.liveness.on_page_lost(0.0)
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        link.connections_seen = 1
        # Attach at 1.0 (reconnect: absolute deadline 30); frame entry 29.0, reentry 29.9; another clock read in reentry would get 30.1 (just expired).
        readings = iter([1.0, 29.0, 29.9, 30.1, 30.1, 30.1])
        s.now = lambda: next(readings)
        conn = tw._attach(link, _RecordingWS())
        assert s.liveness.page_deadline == 30.0
        conn.preflight_seen = conn.preflight_ok = conn.credentials_sent = conn.sdk_seen = conn.sdk_ok = True
        await tw._handle_frame(link, conn, {"type": "state", "state": "joined"}, 10, VISIT_ID, "guest")
        return s, conn

    try:
        s, conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    # Commit with the same now as the check: deadline is actually cleared, avoiding rejoined state with a remaining deadline.
    assert conn.rejoined and s.liveness.page_deadline is None


def test_replacing_a_session_closes_its_socket_as_superseded():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        stale, fresh = FakeSession(), FakeSession()
        tw.register_transport_session(stale)
        ws = _RecordingWS()
        tw._attach(tw._links[(VISIT_ID, "guest")], ws)
        tw.register_transport_session(fresh)
        await asyncio.sleep(0.05)
        return ws

    try:
        ws = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    # 4404: terminal and leave room (4409 iframe stays; session replacement has no new same-vid iframe to evict it).
    assert ws.closed_with == tw.CLOSE_UNKNOWN_VISIT



class _BrokenSendWS(_RecordingWS):
    async def send_text(self, text):
        raise RuntimeError("socket gone")


def test_a_send_failure_unbinds_and_starts_the_reload_at_once():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        conn = tw._attach(link, _BrokenSendWS())
        ok = await s.send({"type": "media", "publish": True})
        # Unbind synchronously and start deadline without waiting for receive loop (half-disconnected sockets may await ping timeout).
        attached = tw.is_transport_attached(VISIT_ID, "guest")
        await asyncio.sleep(0.05)
        return s, conn, ok, attached

    try:
        s, conn, ok, attached = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert not ok and not attached
    assert s.liveness.events.count("page_lost") == 1
    assert conn.websocket.closed_with == tw.CLOSE_SEND_FAILED


def test_a_failed_stop_closes_with_a_terminal_code():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        conn = tw._attach(tw._links[(VISIT_ID, "guest")], _BrokenSendWS())
        await s.send({"type": "stop", "reason": "home"})
        await asyncio.sleep(0.05)
        return conn

    try:
        conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    # Final stop was not delivered: iframe must not reconnect, acquire credentials, or join with the same vid.
    assert conn.websocket.closed_with == tw.CLOSE_UNKNOWN_VISIT


def test_retired_socket_is_not_reported_as_attached():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        conn = tw._attach(tw._links[(VISIT_ID, "guest")], _RecordingWS())
        tw._spawn_close(conn, tw.CLOSE_SUPERSEDED, "x")
        return tw.is_transport_attached(VISIT_ID, "guest")

    try:
        attached = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert attached is False



def test_registering_the_same_session_again_keeps_its_socket():
    import asyncio

    async def scenario():
        tw._reset_for_tests()
        s = FakeSession()
        tw.register_transport_session(s)
        link = tw._links[(VISIT_ID, "guest")]
        conn = tw._attach(link, _RecordingWS())
        tw.register_transport_session(s)  # Idempotent retry.
        await asyncio.sleep(0.05)
        return link, conn

    try:
        link, conn = asyncio.run(scenario())
    finally:
        tw._reset_for_tests()
    assert link.conn is conn and not conn.retired and not conn.closed


def test_a_downlink_write_that_never_drains_retires_the_socket(monkeypatch):
    import asyncio

    monkeypatch.setattr(tw, "SEND_TIMEOUT_S", 0.05)
    monkeypatch.setattr(tw, "CLOSE_LOCK_WAIT_S", 0.05)

    class _StuckWS(_RecordingWS):
        async def send_text(self, text):
            await asyncio.Event().wait()            # Iframe stops reading: writes never return.

    async def scenario():
        ws = _StuckWS()
        conn = tw._Connection(websocket=ws, reattach=False)
        ok = await asyncio.wait_for(conn.send_json({"type": "media", "publish": True}), 1)
        await asyncio.sleep(0.2)
        return ok, ws.closed_with

    ok, closed_with = asyncio.run(scenario())
    assert ok is False                              # Hosting / activation / pump no longer blocks on this write.
    assert closed_with == tw.CLOSE_SEND_FAILED      # Retire this socket; iframe reconnects normally.


def test_rejoin_waits_for_this_connections_sdk_gate_and_books_its_frames(app):
    s = FakeSession()
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
        ws.receive_text()  # credentials
        _joined(ws)        # Join report arrives first; this connection has not reported capability gate ③ yet.
        _barrier(ws, s)
        assert not any(e[0] in ("resend_hello", "resume") for e in s.log)
        assert PAUSE_PAGE_RELOAD in s.outbox.paused
        _sdk_ok(ws)        # Rejoin as soon as the capability gate passes.
        hello = json.loads(ws.receive_text())
        json.loads(ws.receive_text())  # media
    assert hello["type"] == "send"
    # Frames sent directly during reentry also enter runtime bookkeeping.
    assert ("frame_sent", "hello") in s.log
