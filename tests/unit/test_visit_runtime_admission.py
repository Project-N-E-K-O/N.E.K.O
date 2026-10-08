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

"""Visit runtime: admission, capability gate, credentials, room entry, hello and the host's accept (§3.2.1–§3.2.2)."""

from __future__ import annotations

import asyncio
import json

import pytest

from main_routers.visit_router import credentials as cr
from main_routers.visit_router import runtime as rtm
from main_routers.visit_router import transport_ws
from tests.unit.visit_runtime_harness import (
    GUEST_CHAR_UID,
    GUEST_UID,
    GUEST_VID,
    HOST_VID,
    INVITE,
    NOW,
    FakeClock,
    Wire,
    bring_up,
    finish,
    make_side,
    patch_admission,
    settle,
    start_side,
    step,
    teardown,
    through_gate,
    ticket,
    wait_for,
)
from utils import external_route_registry as registry
from utils import visit_route_state


@pytest.fixture(autouse=True)
def _clean():
    rtm._reset_for_tests()
    transport_ws._reset_for_tests()
    visit_route_state._reset_for_tests()
    rtm.register_visit_route_kind()
    yield
    rtm._reset_for_tests()
    transport_ws._reset_for_tests()
    visit_route_state._reset_for_tests()


@pytest.fixture
def clocks():
    return FakeClock(1000.0), FakeClock(NOW)


def _hello(*, role: str, sub: str, vid: str, char_tag: str, seq: int = 1, proto: int = 1, video: bool = True,
           **ticket_over) -> dict:
    return {"t": "hello", "v": 1, "seq": seq,
            "ticket": ticket(role=role, sub=sub, vid=vid, char_tag=char_tag, **ticket_over),
            "caps": {"video": video, "tier": "sd600", "proto": proto, "app_version": "1.0", "crop": "upper"},
            "lang": "zh"}


def _guest_hello(**over) -> dict:
    return _hello(role="guest", sub=GUEST_UID, vid=GUEST_VID, char_tag=GUEST_CHAR_UID, **over)


async def _host_joined(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    wire = Wire()
    wire.attach(rt, None, HOST_VID)
    await through_gate(rt)
    await rt.on_transport_state({"state": "joined", "peer_present": False})
    return side, rt, wire


async def _finished(rt):
    assert rt.exit_task is not None
    await finish(rt, rt.clock)


# ── 准入 ─────────────────────────────────────────────────────────────


async def test_route_lock_is_checked_once_and_before_the_slot(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    calls = []
    real = registry.is_external_route_locked

    def spy(name, **kw):
        calls.append(visit_route_state.get_visit_route_state(name))
        return real(name, **kw)

    monkeypatch.setattr(registry, "is_external_route_locked", spy)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    try:
        assert calls == [None]   # 恰一次，且当时还没有占位
        assert visit_route_state.get_visit_route_state("Host") is rt.slot
        assert rt.phase == "pending"
        await settle()
        assert side.host.frames_of("visit_state_change", "pending")
    finally:
        await teardown(side, clock=clocks[0])


async def test_unreviewed_persona_is_refused_before_any_reservation(tmp_path, monkeypatch, clocks):
    from main_routers.visit_router import persona
    from main_routers.visit_router.persona import PersonaGate

    patch_admission(monkeypatch)

    async def gate(name):
        return PersonaGate(ok=False, state="unreviewed")

    monkeypatch.setattr(persona, "persona_gate", gate)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    reserved = []
    monkeypatch.setattr(rtm, "activate_visit_route", lambda *a, **k: reserved.append(a))
    with pytest.raises(rtm.VisitRefused) as err:
        await start_side(side, clock=clock, wall=wall)
    assert err.value.status == 409 and err.value.body["code"] == "VISIT_PERSONA_UNREVIEWED"
    assert reserved == [] and side.creds_calls == []


async def test_busy_route_is_refused_and_another_kind_keeps_its_slot(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    registry.register_external_route_kind(registry.ExternalRouteKind(
        kind="other", is_active=lambda n: n == "Host", route_stream_message=None, on_start_session=None,
        finalize_for_character=None, current_instance=lambda n: "x", audio_passthrough=True,
    ))
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    with pytest.raises(rtm.VisitRefused) as err:
        await start_side(side, clock=clock, wall=wall)
    assert err.value.status == 409
    assert visit_route_state.get_visit_route_state("Host") is None


@pytest.mark.parametrize("failure", ["voice_session_active", "goodbye_silent", "busy"])
async def test_preconditions_after_the_slot_release_it(tmp_path, monkeypatch, clocks, failure):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    side.host.precondition = failure
    with pytest.raises(rtm.VisitRefused) as err:
        await start_side(side, clock=clock, wall=wall)
    assert err.value.body == {"reason": failure}
    assert visit_route_state.get_visit_route_state("Host") is None
    assert rtm.get_runtime("Host") is None


async def test_no_local_login_is_a_synchronous_409(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)

    async def nobody():
        return None

    monkeypatch.setattr(rtm, "_local_account", nobody)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    with pytest.raises(rtm.VisitRefused) as err:
        await start_side(side, clock=clock, wall=wall)
    assert (err.value.status, err.value.body) == (409, {"code": "VISIT_LOGIN_REQUIRED"})
    assert visit_route_state.get_visit_route_state("Host") is None and side.creds_calls == []


async def test_recent_servers_ban_is_a_synchronous_403(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    monkeypatch.setattr(cr, "banned_recently", lambda account: account == "acct")
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    with pytest.raises(rtm.VisitRefused) as err:
        await start_side(side, clock=clock, wall=wall)
    assert (err.value.status, err.value.body) == (403, {"code": "VISIT_BANNED"})
    assert visit_route_state.get_visit_route_state("Host") is None


# ── 能力门 ───────────────────────────────────────────────────────────


async def test_failed_preflight_ends_without_servers_or_takeover(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await rt.on_preflight({"stage": "preflight", "preflight_ok": False, "reason": "no_webrtc"})
    assert await rt.issue_credentials() is None
    await _finished(rt)
    assert side.creds_calls == [] and side.host.takeovers == []
    assert rt.finalize_reason == "unsupported"
    assert "VISIT_UNSUPPORTED_ON_THIS_MACHINE" in side.host.status_codes()
    assert side.host.frames_of("visit_state_change", "ended")[-1]["reason"] == "unsupported"
    assert visit_route_state.get_visit_route_state("Host") is None
    assert not rtm.is_visit_route_locked("Host")


async def test_preflight_timeout_unlocks_the_character(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    clock.advance(14)
    await rt.tick()
    assert rt.exit_task is None
    clock.advance(1.5)
    await rt.tick()
    await _finished(rt)
    assert rt.finalize_reason == "unsupported"
    assert side.creds_calls == [] and side.host.takeovers == []
    assert not rtm.is_visit_route_locked("Host")


async def test_sdk_gate_failure_releases_the_takeover_after_one_issue(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
    msg = await rt.issue_credentials()
    assert msg is not None and msg["type"] == "credentials" and msg["peer_vid"] is None
    await rt.on_sdk_caps({"stage": "sdk", "transport_ok": False, "video_ok": False, "codecs": []})
    await _finished(rt)
    assert len(side.creds_calls) == 1            # 这一次签发已经计了
    assert side.host.takeovers and side.host.released == side.host.takeovers
    assert rt.finalize_reason == "unsupported"


async def test_sdk_gate_timeout_finalizes_unsupported(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
    await rt.issue_credentials()
    clock.advance(19)
    await rt.tick()
    assert rt.exit_task is None
    clock.advance(2)
    await rt.tick()
    await _finished(rt)
    assert rt.finalize_reason == "unsupported"
    assert side.host.released == side.host.takeovers != []


async def test_servers_unreachable_releases_the_slot_without_takeover(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    side.creds_error = cr.VisitServersUnreachable("http_503")
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
    assert await rt.issue_credentials() is None
    await _finished(rt)
    assert side.host.takeovers == []
    assert "VISIT_SERVERS_UNREACHABLE" in side.host.status_codes()
    assert visit_route_state.get_visit_route_state("Host") is None


@pytest.mark.parametrize("exc,code", [
    (cr.VisitLoginRequired("unauthenticated"), "VISIT_LOGIN_REQUIRED"),
    (cr.VisitBanned("banned"), "VISIT_BANNED"),
    (cr.VisitQuotaExceeded("quota_exceeded", retry_after_s=60), "VISIT_QUOTA_EXCEEDED"),
    (cr.VisitCrossRegionUnsupported("cross_region_unsupported"), "VISIT_CROSS_REGION_UNSUPPORTED"),
    (cr.VisitInviteInvalid("invite_expiring"), "VISIT_INVITE_INVALID"),
    (cr.VisitRoomEnded("room_ended"), "VISIT_KICKED"),
])
async def test_servers_errors_map_to_their_status(tmp_path, monkeypatch, clocks, exc, code):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "guest", clock=clock, wall=wall)
    side.creds_error = exc
    rt = await start_side(side, invite_code=INVITE, clock=clock, wall=wall)
    Wire().attach(rt, None, GUEST_VID)
    await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
    await rt.issue_credentials()
    await _finished(rt)
    assert code in side.host.status_codes()
    assert side.host.takeovers == []


async def test_invite_code_only_after_joined_and_never_in_the_state(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    assert side.host.frames_of("visit_state_change", "invite_ready") == []
    await rt.on_transport_state({"state": "joined", "peer_present": False})
    ready = side.host.frames_of("visit_state_change", "invite_ready")
    try:
        assert len(ready) == 1 and ready[0]["invite_code"] == INVITE
        assert "invite_code" not in repr(rt.snapshot())
        assert rt.phase == "invite_ready"
    finally:
        await teardown(side, clock=clocks[0])


async def test_gate_passed_but_never_joined_is_relay_lost_without_invite(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    clock.advance(26)
    await rt.tick()
    await _finished(rt)
    assert rt.finalize_reason == "relay_lost"
    assert side.host.frames_of("visit_state_change", "invite_ready") == []
    # 对端核验之前结束：后台取消房间
    await wait_for(lambda: side.cancelled)
    assert side.cancelled[0][0] == rt.visit_id


# ── 等客 / 等对端 ─────────────────────────────────────────────────────


async def test_host_waits_the_invite_and_ends_without_leave(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    clock, _wall = clocks
    clock.advance(31)
    await rt.tick()
    assert rt.exit_task is None
    clock.advance(600)
    await rt.tick()
    await _finished(rt)
    assert rt.finalize_reason == "invite_expired"
    assert "leave" not in wire.sent_types("host")
    await wait_for(lambda: side.cancelled)


async def test_guest_gives_up_after_thirty_seconds_without_hello(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "guest", clock=clock, wall=wall)
    rt = await start_side(side, invite_code=INVITE, clock=clock, wall=wall)
    Wire().attach(rt, None, GUEST_VID)
    await through_gate(rt)
    await rt.on_transport_state({"state": "joined", "peer_present": True})
    clock.advance(31)
    await rt.tick()
    await _finished(rt)
    assert rt.finalize_reason == "peer_lost"


# ── hello 核验 ───────────────────────────────────────────────────────


async def test_verified_hello_invites_the_family_and_fills_peer_vid(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    try:
        await rt.on_transport_state({"state": "connected", "peer_present": True})
        assert wire.sent_types("host") == [] or "hello" in wire.sent_types("host")
        await rt.flush()
        assert "hello" in wire.sent_types("host")
        await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload=_guest_hello(), nbytes=900)
        assert rt.phase == "awaiting_accept" and rt.peer.uid == GUEST_UID
        invite = side.host.frames_of("visit_invite")
        assert len(invite) == 1 and invite[0]["peer_short_id"] == GUEST_UID[:6].upper()
        media = [m for m in wire.downlinks["host"] if m.get("type") == "media"]
        assert media[-1]["subscribe"] is False and media[-1]["peer_vid"] == GUEST_VID
        assert side.clients == []                  # 接待之前不建隔离会话
    finally:
        await teardown(side, wire=wire, clock=clocks[0])


@pytest.mark.parametrize("over,expected", [
    ({"visit_id": "ZZZZZZZZZZZZZZZZZZZZZZ"}, "peer_identity_rejected"),
    ({"role": "host"}, "peer_identity_rejected"),
    ({"transport": "trtc"}, "peer_identity_rejected"),
    ({"vid": "g_" + "c" * 24}, "peer_identity_rejected"),
])
async def test_bad_tickets_leave_peer_identity_rejected(tmp_path, monkeypatch, clocks, over, expected):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    await rt.on_transport_state({"state": "connected", "peer_present": True})
    role = over.pop("role", "guest")
    hello = _hello(role=role, sub=GUEST_UID, vid=over.pop("vid", GUEST_VID), char_tag=GUEST_CHAR_UID, **over)
    await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload=hello, nbytes=900)
    await _finished(rt)
    assert rt.finalize_reason == expected
    leaves = [p for p in wire.sent["host"] if p.get("t") == "leave"]
    assert leaves and leaves[0]["reason"] == "peer_identity_rejected"
    assert side.host.frames_of("visit_invite") == []


async def test_blocked_peer_sees_only_an_identity_rejection(tmp_path, monkeypatch, clocks):
    from main_logic.visit.limits import Blocklist, BlockEntry

    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)

    async def blocked(path):
        return Blocklist(path, [BlockEntry(visit_uid=GUEST_UID, display_name_at_block="x", blocked_at=NOW)])

    side.deps.load_blocklist = blocked
    await rt.on_transport_state({"state": "connected", "peer_present": True})
    await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload=_guest_hello(), nbytes=900)
    await _finished(rt)
    assert rt.finalize_reason == "peer_blocked"
    leaves = [p for p in wire.sent["host"] if p.get("t") == "leave"]
    assert leaves and leaves[0]["reason"] == "peer_identity_rejected"


async def test_protocol_mismatch_leaves_with_its_reason_and_toast(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    await rt.on_transport_state({"state": "connected", "peer_present": True})
    await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload=_guest_hello(proto=2), nbytes=900)
    await _finished(rt)
    assert rt.finalize_reason == "proto_mismatch"
    assert "VISIT_PROTO_MISMATCH" in side.host.status_codes()


async def test_text_before_verification_is_dropped(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    try:
        text = {"t": "text", "v": 1, "ln": "g:1", "lp": 1, "seq": 1, "sp": "c", "ad": "hc", "rt": "", "wu": False,
                "final": True, "txt": "hi", "truncated": False, "i_done": 0}
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload=text, nbytes=200)
        assert rt.gate_dropped == 1 and side.host.frames_of("visit_line") == []
    finally:
        await teardown(side, wire=wire, clock=clocks[0])


async def test_awaiting_accept_gate_acks_text_but_shows_and_stores_nothing(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    try:
        before = len(host.host.frames)
        for payload, cmd in (
            ({"t": "text", "v": 1, "ln": "g:9", "lp": 3, "seq": 2, "sp": "c", "ad": "hc", "rt": "", "wu": False,
              "final": True, "txt": "早到的话", "truncated": False, "i_done": 0}, 2),
            ({"t": "line_delta", "v": 1, "ln": "g:9", "i": 0, "lp": 3, "txt": "早", "sp": "c", "ad": "hc", "rt": "",
              "wu": False}, 2),
            ({"t": "wrap_up", "v": 1, "seq": 3, "lp": 3, "ph": "propose", "reason": "recall",
              "initiated_by": "guest"}, 1),
        ):
            await hrt.on_recv(from_vid=GUEST_VID, cmd=cmd, payload=payload, nbytes=300)
        assert not [f for f in host.host.frames[before:] if str(f.get("type", "")).startswith("visit_line")]
        assert hrt.gate_dropped >= 3 and hrt.room is None and host.clients == []
        assert hrt.sequencer.contiguous_seq == 3            # text 照常按 seq 推进（会回 ack）
        await hrt.flush()
        acks = [p for p in wire.sent["host"] if p.get("t") == "ack"]
        assert acks and acks[-1]["seq"] == 3
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


# ── 接待与激活 ───────────────────────────────────────────────────────


async def test_ready_goes_out_only_after_the_host_is_ready_to_receive(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    seen = {}
    real_send = hrt.outbox.send

    def spy(msg, **kw):
        if msg.get("t") == "ready":
            seen.setdefault("count", 0)
            seen["count"] += 1
            seen.setdefault("state", (hrt.session is not None, hrt.room is not None and hrt.room.phase == "active",
                             hrt.spool is not None and hrt.spool.is_open, hrt.activated, hrt.journal.is_open))
        return real_send(msg, **kw)

    hrt.outbox.send = spy
    try:
        assert host.clients == []
        await hrt.accept(True)
        assert seen["state"] == (True, True, True, True, True) and seen["count"] == 1
        assert len(host.clients) == 1
        await wait_for(lambda: guest.rt.activated)
        await wait_for(lambda: guest.host.frames_of("visit_state_change", "departed"))
        assert len(guest.clients) == 1
        media = [m for m in wire.downlinks["host"] if m.get("type") == "media"]
        assert media[-1]["subscribe"] is True
        gmedia = [m for m in wire.downlinks["guest"] if m.get("type") == "media"]
        assert gmedia and gmedia[-1]["publish"] is True
        assert host.host.frames_of("visit_state_change", "started")
        assert guest.host.frames_of("visit_state_change", "departed")
        assert host.host.parked == 1 and guest.host.parked == 1
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_guest_publishes_nothing_and_says_nothing_before_ready(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    try:
        await settle()
        assert "text" not in wire.sent_types("guest") and "line_delta" not in wire.sent_types("guest")
        gmedia = [m for m in wire.downlinks["guest"] if m.get("type") == "media"]
        assert all(m["publish"] is False for m in gmedia)
        assert guest.rt.media_snapshot()["publish"] is False
        assert host.rt.media_snapshot()["subscribe"] is False
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_family_never_answers_the_invite_declines(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    try:
        await step(clock, 61, host.rt, guest.rt)
        await finish(host.rt, clock)
        assert host.rt.finalize_reason == "declined"
        assert [p["reason"] for p in wire.sent["host"] if p.get("t") == "leave"] == ["declined"]
        await wait_for(lambda: guest.rt.exit_task is not None, timeout=10)
        assert guest.rt.finalize_reason == "declined"
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_late_accept_within_the_activation_allowance_is_not_declined(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    try:
        await step(clock, 59, host.rt, guest.rt)
        status, _ = await host.rt.accept(True)
        assert status == 200
        clock.advance(9)                       # host 第 67 s 才发出 ready
        await wait_for(lambda: guest.rt.activated, timeout=10)
        await guest.rt.tick()
        assert guest.rt.exit_task is None
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_accept_twice_and_accept_without_invite(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    try:
        assert (await rt.accept(True))[0] == 404
    finally:
        await teardown(side, wire=wire, clock=clocks[0])


async def test_declining_sends_leave_declined(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    try:
        status, _ = await host.rt.accept(False)
        assert status == 200
        assert (await host.rt.accept(True))[0] in (404, 409)
        await finish(host.rt, clock)
        leaves = [p for p in wire.sent["host"] if p.get("t") == "leave"]
        assert [p["reason"] for p in leaves] == ["declined"]
        assert host.clients == []
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_page_reload_before_ready_keeps_media_off(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    try:
        hsnap, gsnap = host.rt.media_snapshot(), guest.rt.media_snapshot()
        assert hsnap["subscribe"] is False and hsnap["peer_vid"] == GUEST_VID
        assert gsnap["publish"] is False
        # 重载后的首发凭证带上已核验的 peer_vid
        msg = await host.rt.issue_credentials()
        assert msg["peer_vid"] == GUEST_VID
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_video_unavailable_keeps_media_off_after_ready(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    try:
        guest.rt.video_ok = False
        assert guest.rt.media_snapshot()["publish"] is False
        host.rt.peer.video = False
        assert host.rt.media_snapshot()["subscribe"] is False
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


# ── 在飞的凭证 / 激活与收尾、关机的竞态 ─────────────────────────────


@pytest.mark.parametrize("role", ["host", "guest"])
async def test_both_sides_interrupt_the_main_turn_before_taking_over(tmp_path, monkeypatch, clocks, role):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, role, clock=clock, wall=wall)
    rt = await start_side(side, invite_code=INVITE if role == "guest" else None, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID if role == "host" else GUEST_VID)
    try:
        await through_gate(rt)
        events = side.host.events
        assert events.index("interrupt_main_turn") < events.index("acquire_takeover")
    finally:
        await teardown(side, clock=clock)


async def test_room_minted_after_the_host_already_ended_is_cancelled_once(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    side.creds_gate = asyncio.Event()
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
    issuing = asyncio.ensure_future(rt.issue_credentials())
    await wait_for(lambda: side.creds_calls)
    rt.request_finalize("route_end")
    await _finished(rt)                                   # 收尾走完时还没有凭证：那时没有房间可取消
    assert side.cancelled == []
    side.creds_gate.set()                                 # Servers 这才签出房间
    assert await issuing is None
    await wait_for(lambda: side.cancelled)
    rt._cancel_room_once()                                # 收尾流程与晚到的凭证各调一次：只发一次
    await settle()
    assert len(side.cancelled) == 1
    assert side.host.takeovers == []


async def test_shutdown_while_credentials_are_pending_takes_nothing_over(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    side.creds_gate = asyncio.Event()
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
    issuing = asyncio.ensure_future(rt.issue_credentials())
    await wait_for(lambda: side.creds_calls)
    await asyncio.wait_for(rtm.stop_all("shutdown"), 3)
    # 不等 Servers 回话：挂起的领凭证随关机取消，也不把 CancelledError 抛给 transport 处理器
    assert await asyncio.wait_for(issuing, 1) is None
    side.creds_gate.set()
    await settle()
    assert side.host.takeovers == []
    assert rtm.get_runtime("Host") is None and rt.finalizing


async def test_activation_that_finishes_after_the_end_publishes_nothing(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = hrt._open_spool

    async def slow_open(subjects):
        await real_open(subjects)
        reached.set()
        await gate.wait()

    hrt._open_spool = slow_open
    try:
        accepting = asyncio.ensure_future(hrt.accept(True))
        await asyncio.wait_for(reached.wait(), 5)
        hrt.request_finalize("route_end")                 # 等 spool 落盘期间这场被结束
        await settle()
        gate.set()
        assert (await accepting)[0] == 200
        await finish(hrt, clock)
        assert hrt.room is None and not hrt.activated and hrt.phase == "ended"
        assert not host.host.frames_of("visit_state_change", "started")
        assert "ready" not in [p.get("t") for p in wire.sent["host"]]
        assert hrt.spool is not None and not hrt.spool.is_open
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_session_created_while_ending_is_still_closed(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    gate, reached = asyncio.Event(), asyncio.Event()
    real_create = host.deps.create_session

    async def slow_create(*args, **kwargs):
        reached.set()
        await gate.wait()
        return await real_create(*args, **kwargs)

    host.deps.create_session = slow_create
    try:
        accepting = asyncio.ensure_future(hrt.accept(True))
        await asyncio.wait_for(reached.wait(), 5)
        hrt.request_finalize("route_end")
        await settle()
        await asyncio.sleep(0.3)
        gate.set()                                        # 收尾已开始之后会话才建好
        assert (await accepting)[0] == 200
        await finish(hrt, clock)
        assert len(host.clients) == 1 and host.clients[0].closed
        assert hrt.room is None and not hrt.activated
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_second_start_during_the_reservation_is_refused(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    gate, reached = asyncio.Event(), asyncio.Event()

    async def slow_account():
        reached.set()
        await gate.wait()
        return "acct"

    monkeypatch.setattr(rtm, "_local_account", slow_account)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    first = asyncio.ensure_future(start_side(side, clock=clock, wall=wall))
    await asyncio.wait_for(reached.wait(), 5)
    slot = visit_route_state.get_visit_route_state("Host")
    assert slot is not None and rtm.get_runtime("Host") is None
    assert rtm.is_visit_route_locked("Host")             # 占位即上锁：运行时还没登记也算
    with pytest.raises(rtm.VisitRefused) as refused:
        await rtm.start_visit("Host", "host", crop="upper", invite_code=None, visit_id="Y" * 22,
                              host=side.host, deps=side.deps, clock=clock, wall=wall)
    assert refused.value.status == 409 and refused.value.body["reason"] == "already_visiting"
    assert visit_route_state.get_visit_route_state("Host") is slot   # 第一场的占位没被换掉
    gate.set()
    rt = await first
    try:
        assert rt.slot is slot and rtm.get_runtime("Host") is rt
    finally:
        await teardown(side, clock=clock)


def test_the_vp8_fallback_applies_to_the_next_livekit_visit_only(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(rtm, "_vp8_next_visit", True)
    livekit = SimpleNamespace(transport="livekit")
    nxt = SimpleNamespace(creds=livekit, _livekit_codec=None)
    assert rtm.VisitRuntime._codec(nxt) == "vp8"
    assert rtm._vp8_next_visit is False
    assert rtm.VisitRuntime._codec(nxt) == "vp8"           # 本场续期 / 重连沿用
    later = SimpleNamespace(creds=livekit, _livekit_codec=None)
    assert rtm.VisitRuntime._codec(later) == "vp9"
    trtc = SimpleNamespace(creds=SimpleNamespace(transport="trtc"), _livekit_codec=None)
    monkeypatch.setattr(rtm, "_vp8_next_visit", True)
    assert rtm.VisitRuntime._codec(trtc) == "h264" and rtm._vp8_next_visit is True   # TRTC 不消耗


async def test_ending_while_the_journal_opens_does_not_reopen_the_wait(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = rt.journal.open

    async def slow_open(**kw):
        reached.set()
        await gate.wait()
        await real_open(**kw)

    rt.journal.open = slow_open
    joining = asyncio.ensure_future(rt.on_transport_state({"state": "joined", "peer_present": False}))
    await asyncio.wait_for(reached.wait(), 5)
    rt.request_finalize("route_end")
    await settle()
    gate.set()
    await asyncio.gather(joining)
    await _finished(rt)
    assert rt.phase == "ended"
    assert not side.host.frames_of("visit_state_change", "invite_ready")


async def test_ending_while_the_peer_name_is_cleaned_installs_no_peer(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    gate, reached = asyncio.Event(), asyncio.Event()
    real_clean = rt._clean_peer_name

    async def slow_clean(*args):
        reached.set()
        await gate.wait()
        return await real_clean(*args)

    rt._clean_peer_name = slow_clean
    try:
        await rt.on_transport_state({"state": "connected", "peer_present": True})
        hello = asyncio.ensure_future(rt.on_recv(from_vid=GUEST_VID, cmd=1, payload=_guest_hello(), nbytes=900))
        await asyncio.wait_for(reached.wait(), 5)
        rt.request_finalize("route_end")
        await settle()
        gate.set()
        await asyncio.gather(hello)
        await _finished(rt)
        assert rt.peer is None and rt.phase == "ended"
        assert not side.host.frames_of("visit_invite")
    finally:
        gate.set()
        await teardown(side, wire=wire, clock=clocks[0])


async def test_a_stalled_guest_activation_ends_the_visit_within_the_allowance(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "VISIT_ACTIVATION_ALLOWANCE_S", 0.3)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    stall = asyncio.Event()

    async def hang(*args, **kwargs):
        await stall.wait()

    guest.deps.create_session = hang
    try:
        started = asyncio.get_running_loop().time()
        # 收包循环里同步激活：卡住也不能无限挂着（外层 3 s 只为让测试本身不挂死）
        await asyncio.wait_for(guest.rt.on_ready(), 3)
        assert asyncio.get_running_loop().time() - started < 2.0
        assert guest.rt.finalize_reason == "llm_error" and not guest.rt.activated
    finally:
        stall.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_cancels_the_room_of_an_unpaired_host(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    assert side.cancelled == []
    await asyncio.wait_for(rtm.stop_all("shutdown"), 3)
    assert len(side.cancelled) == 1 and side.cancelled[0][0] == rt.visit_id
    assert "leave" not in wire.sent_types("host")


async def test_ending_while_the_journal_opens_still_seals_it(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = rt.journal.open

    async def slow_open(**kw):
        reached.set()
        await gate.wait()
        await real_open(**kw)

    rt.journal.open = slow_open
    joining = asyncio.ensure_future(rt.on_transport_state({"state": "joined", "peer_present": False}))
    await asyncio.wait_for(reached.wait(), 5)
    rt.request_finalize("route_end")
    asyncio.get_running_loop().call_later(0.1, gate.set)      # 封存之前写完上传头
    await _finished(rt)
    await asyncio.gather(joining)
    assert rt.journal.sealed                                   # 不留一份没封存的 .upload.jsonl
    assert not list((side.config_dir / "visit_spool").glob("*.upload.jsonl"))


async def test_a_joined_report_before_credentials_does_not_count_as_entering(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # 凭证还没下发
        assert rt.joined is False
        await through_gate(rt)
        await rt.on_transport_state({"state": "joined", "peer_present": False})
        assert rt.joined is True and rt.phase == "invite_ready"
        await settle()
        assert side.host.frames_of("visit_state_change", "invite_ready")
    finally:
        await teardown(side, clock=clock)


async def test_preconditions_are_checked_again_after_the_account_lookup(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)

    async def account_while_voice_starts():
        side.host.precondition = "voice_session_active"       # 查账号期间语音会话起来了
        return "acct"

    monkeypatch.setattr(rtm, "_local_account", account_while_voice_starts)
    with pytest.raises(rtm.VisitRefused) as refused:
        await start_side(side, clock=clock, wall=wall)
    assert refused.value.body["reason"] == "voice_session_active"
    assert visit_route_state.get_visit_route_state("Host") is None and rtm.get_runtime("Host") is None


async def test_a_slow_journal_open_is_awaited_before_sealing(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = rt.journal.open

    async def slow_open(**kw):
        reached.set()
        await gate.wait()
        await real_open(**kw)

    rt.journal.open = slow_open
    joining = asyncio.ensure_future(rt.on_transport_state({"state": "joined", "peer_present": False}))
    await asyncio.wait_for(reached.wait(), 5)
    rt.request_finalize("route_end")
    asyncio.get_running_loop().call_later(0.9, gate.set)      # 比关机预算里的等待更久
    await _finished(rt)
    await asyncio.gather(joining)
    assert rt.journal.sealed
    assert not list((side.config_dir / "visit_spool").glob("*.upload.jsonl"))


async def test_joined_before_the_sdk_gate_passed_does_not_count(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await rt.issue_credentials() is not None
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # 能力门 ③ 还没报
        assert rt.joined is False and not side.host.frames_of("visit_state_change", "invite_ready")
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        await rt.on_transport_state({"state": "joined", "peer_present": False})
        assert rt.joined is True and rt.phase == "invite_ready"
    finally:
        await teardown(side, clock=clock)


async def test_the_accept_deadline_starts_when_verification_finishes(tmp_path, monkeypatch, clocks):
    from config.visit_settings import VISIT_ACCEPT_TIMEOUT_S

    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    clock = clocks[0]
    real = side.deps.fetch_pubkeys

    async def slow_pubkeys():
        clock.advance(40)                                    # 核验读公钥花了 40 s
        return await real()

    side.deps.fetch_pubkeys = slow_pubkeys
    try:
        await rt.on_transport_state({"state": "connected", "peer_present": True})
        await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload=_guest_hello(), nbytes=900)
        assert rt.phase == "awaiting_accept"
        assert rt._accept_deadline - clock() > VISIT_ACCEPT_TIMEOUT_S - 1   # 亲人仍有完整的接待时间
    finally:
        await teardown(side, wire=wire, clock=clock)


async def test_two_characters_cannot_take_the_same_visit_at_once(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    gate, reached = asyncio.Event(), asyncio.Event()

    async def slow_account():
        reached.set()
        await gate.wait()
        return "acct"

    monkeypatch.setattr(rtm, "_local_account", slow_account)
    clock, wall = clocks
    one = make_side(tmp_path / "a", "guest", clock=clock, wall=wall, name="Mimi")
    two = make_side(tmp_path / "b", "guest", clock=clock, wall=wall, name="Nana")
    first = asyncio.ensure_future(start_side(one, invite_code=INVITE, clock=clock, wall=wall))
    await asyncio.wait_for(reached.wait(), 5)
    with pytest.raises(rtm.VisitRefused) as refused:
        await start_side(two, invite_code=INVITE, clock=clock, wall=wall)   # 同一张邀请、同一个 visit_id
    assert refused.value.body["reason"] == "visit_in_progress"
    assert visit_route_state.get_visit_route_state("Nana") is None
    gate.set()
    rt = await first
    try:
        assert rtm.get_runtime_by_visit(rt.visit_id) is rt
    finally:
        await teardown(one, clock=clock)


async def test_an_admission_still_awaiting_when_stop_all_runs_does_not_register(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    gate, reached = asyncio.Event(), asyncio.Event()

    async def slow_account():
        reached.set()
        await gate.wait()
        return "acct"

    monkeypatch.setattr(rtm, "_local_account", slow_account)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    starting = asyncio.ensure_future(start_side(side, clock=clock, wall=wall))
    try:
        await asyncio.wait_for(reached.wait(), 5)
        await rtm.stop_all("shutdown")                        # 关机时它还在查账号
        gate.set()
        with pytest.raises(rtm.VisitRefused):
            await asyncio.wait_for(starting, 5)
        assert rtm.get_runtime("Host") is None                # 关机之后不再登记运行时
        assert visit_route_state.get_visit_route_state("Host") is None
    finally:
        gate.set()
        if not starting.done():
            starting.cancel()
        await teardown(side, clock=clock)


async def test_an_admission_during_stop_all_is_refused_as_shutdown(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    gate, reached = asyncio.Event(), asyncio.Event()

    async def slow_account():
        reached.set()
        await gate.wait()
        return "acct"

    try:
        monkeypatch.setattr(rtm, "_stopping", True)           # stop_all 正在跑时新到的入场
        with pytest.raises(rtm.VisitRefused) as refused:
            await start_side(side, clock=clock, wall=wall)
        assert refused.value.body["reason"] == "shutdown"
        monkeypatch.setattr(rtm, "_stopping", False)
        monkeypatch.setattr(rtm, "_local_account", slow_account)
        starting = asyncio.ensure_future(start_side(side, clock=clock, wall=wall))
        await asyncio.wait_for(reached.wait(), 5)
        monkeypatch.setattr(rtm, "_stopping", True)           # 查账号期间 stop_all 开始（代数还没变也拦）
        gate.set()
        with pytest.raises(rtm.VisitRefused) as refused:
            await asyncio.wait_for(starting, 5)
        assert refused.value.body["reason"] == "shutdown"
        assert rtm.get_runtime("Host") is None
    finally:
        gate.set()
        monkeypatch.setattr(rtm, "_stopping", False)
        await teardown(side, clock=clock)


async def test_a_session_that_will_not_close_does_not_keep_the_visit_registered(tmp_path, monkeypatch):
    from main_routers.visit_router import session_pool

    monkeypatch.setattr(rtm, "_SESSION_CLOSE_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    real_close = session_pool.close_visit_session

    async def close_visit_session(session):
        if session is not rt.session:
            return await real_close(session)
        while not stuck.is_set():                             # 取消排空卡住：吞掉取消、继续等
            try:
                await stuck.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setattr(session_pool, "close_visit_session", close_visit_session)
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        assert rtm.get_runtime("Host") is None                # 照样注销，这个角色不会一直锁着
        assert [t for t in rtm._detached if not t.done()]     # 没关完的交给后台继续关
    finally:
        stuck.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_single_joined_report_ahead_of_the_sdk_gate_is_replayed(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await rt.issue_credentials() is not None
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # 只报这一次
        assert rt.joined is False
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        assert rt.joined is True and rt.phase == "invite_ready"                  # 能力门一过就补做首次入房
        await settle()
        assert side.host.frames_of("visit_state_change", "invite_ready")
    finally:
        await teardown(side, clock=clock)


async def test_a_pending_joined_report_is_dropped_once_the_connection_drops(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await rt.issue_credentials() is not None
        await rt.on_transport_state({"state": "joined", "peer_present": False})
        await rt.on_transport_state({"state": "reconnecting"})                    # 能力门之前又断了
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        assert rt.joined is False and rt.phase != "invite_ready"
        assert rt.liveness.self_disconnected_at is not None                       # 仍按断线计时
    finally:
        await teardown(side, clock=clock)



def test_a_second_runtime_of_the_same_visit_does_not_take_over_its_registration():
    from types import SimpleNamespace

    first = SimpleNamespace(lanlan_name="Mimi", visit_id="V" * 22, character_uid="")
    second = SimpleNamespace(lanlan_name="Nana", visit_id="V" * 22, character_uid="")
    rtm._register(first)
    # 本机另一个角色兑换本机发出的邀请（Servers 以 self_invite 拒之前）：不顶掉在进行的那一场
    rtm._register(second)
    assert rtm.get_runtime_by_visit("V" * 22) is first
    rtm._unregister(second)
    assert rtm.get_runtime_by_visit("V" * 22) is first and rtm.is_visit_live("V" * 22)
    # 反过来：先登记的那个结束了、另一个还在，就由它接着代表这一场
    rtm._register(second)
    rtm._unregister(first)
    assert rtm.get_runtime_by_visit("V" * 22) is second
    rtm._unregister(second)
    assert not rtm.is_visit_live("V" * 22)


async def test_a_pending_joined_report_dies_with_its_page(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await rt.issue_credentials() is not None
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # 记下，等能力门
        rt.transport.on_page_lost(clock())                                         # 这条连接没报断线就没了
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        assert rt.joined is False and not side.host.frames_of("visit_state_change", "invite_ready")
    finally:
        await teardown(side, clock=clock)


async def test_the_guest_wait_starts_after_the_journal_header_is_written(tmp_path, monkeypatch, clocks):
    from config.visit_settings import VISIT_PEER_LOST_S

    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "guest", clock=clock, wall=wall)
    rt = await start_side(side, invite_code=INVITE, clock=clock, wall=wall)
    Wire().attach(rt, None, GUEST_VID)
    await through_gate(rt)
    real_open = rt.journal.open

    async def slow_open(**kw):
        clock.advance(20)                                   # 磁盘慢：写上传头花了 20 s
        await real_open(**kw)

    rt.journal.open = slow_open
    try:
        await rt.on_transport_state({"state": "joined", "peer_present": True})
        assert rt.phase == "joining"
        assert rt.liveness.wait_deadline - clock() > VISIT_PEER_LOST_S - 1   # 仍有完整的等对端时间
    finally:
        await teardown(side, clock=clock)


async def test_a_superseded_socket_takes_its_pending_join_and_setup_deadlines_with_it(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await rt.issue_credentials() is not None
        assert rt._sdk_deadline is not None
        await rt.on_transport_state({"state": "joined", "peer_present": False})
        rt.transport.on_page_attached(clock())               # 新连接顶替了旧连接（旧连接不再报 page_lost）
        assert rt._sdk_deadline is None and rt._join_deadline is None and rt._preflight_deadline is None
        clock.advance(21)
        await rt.tick()                                       # 旧连接的能力门期限（20 s）不会把重载中的这场判死
        assert rt.finalize_reason is None
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        assert rt.joined is False                             # 旧连接的入房报告不算
    finally:
        await teardown(side, clock=clock)


async def test_a_manager_replaced_during_the_account_lookup_is_refused(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)

    async def account_while_replaced():
        side.host.current = False                             # 查账号期间 manager 被换掉
        return "acct"

    monkeypatch.setattr(rtm, "_local_account", account_while_replaced)
    with pytest.raises(rtm.VisitRefused) as refused:
        await start_side(side, clock=clock, wall=wall)
    assert refused.value.body["reason"] == "busy"
    assert visit_route_state.get_visit_route_state("Host") is None


async def test_a_stuck_ordinary_speech_interrupt_releases_the_takeover(tmp_path, monkeypatch, clocks):
    monkeypatch.setattr(rtm, "_INTERRUPT_MAIN_TURN_S", 0.2)
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    stuck = asyncio.Event()

    async def hang():
        await stuck.wait()                                    # 页面不收：send_user_activity 一直等

    side.host.interrupt_ordinary_speech = hang
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await asyncio.wait_for(rt.issue_credentials(), 3) is None
        assert rt.finalize_reason == "busy"
        await _finished(rt)
        assert side.host.released == side.host.takeovers and side.host.takeovers
    finally:
        stuck.set()


async def test_ending_while_the_main_turn_is_interrupted_fetches_no_credentials(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "guest", clock=clock, wall=wall)
    gate, reached = asyncio.Event(), asyncio.Event()

    async def slow_interrupt(timeout):
        reached.set()
        await gate.wait()                                   # 主对话轮还在收尾
        return True

    side.host.interrupt_main_turn = slow_interrupt
    rt = await start_side(side, invite_code=INVITE, clock=clock, wall=wall)
    Wire().attach(rt, None, GUEST_VID)
    await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
    issuing = asyncio.ensure_future(rt.issue_credentials())
    await asyncio.wait_for(reached.wait(), 5)
    rt.request_finalize("route_end")
    gate.set()
    assert await issuing is None
    await _finished(rt)
    assert side.creds_calls == []                            # 一次性邀请码没被兑掉
    assert side.host.takeovers == []


async def test_a_first_join_whose_socket_was_replaced_meanwhile_does_not_count(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = rt.journal.open

    opens = []

    async def slow_open(**kw):
        opens.append(kw)
        reached.set()
        await gate.wait()
        await real_open(**kw)

    rt.journal.open = slow_open
    try:
        joining = asyncio.ensure_future(rt.on_transport_state({"state": "joined", "peer_present": False}))
        await asyncio.wait_for(reached.wait(), 5)
        rt.transport.on_page_attached(clock())               # 写上传头期间新连接顶替了它
        gate.set()
        await asyncio.gather(joining)
        assert rt.joined is False and rt.phase != "invite_ready"
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # 新连接自己报
        assert rt.joined is True and rt.phase == "invite_ready" and rt.journal.is_open
        assert len(opens) == 1                                # 上传头只写一次，新连接沿用
    finally:
        gate.set()
        await teardown(side, clock=clock)


async def test_a_replacement_page_must_pass_its_own_sdk_gate_before_joining(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await through_gate(rt)                                 # 第一条连接过了能力门 ③，还没入房就断了
        rt.transport.on_page_lost(clock())
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # 新连接先报入房
        assert rt.joined is False and rt.phase != "invite_ready"
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        assert rt.joined is True and rt.phase == "invite_ready"
    finally:
        await teardown(side, clock=clock)


async def test_a_successor_join_during_a_stale_first_join_is_kept(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = rt.journal.open

    async def slow_open(**kw):
        reached.set()
        await gate.wait()
        await real_open(**kw)

    rt.journal.open = slow_open
    try:
        joining = asyncio.ensure_future(rt.on_transport_state({"state": "joined", "peer_present": False}))
        await asyncio.wait_for(reached.wait(), 5)
        rt.transport.on_page_attached(clock())                # 旧连接被顶替
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # 新连接只报这一次
        gate.set()
        await asyncio.gather(joining)
        await wait_for(lambda: rt.joined and rt.phase == "invite_ready")         # 旧的那次结束后补做新连接的入房
    finally:
        gate.set()
        await teardown(side, clock=clock)


async def test_a_successor_join_reported_before_its_sdk_gate_survives_a_stale_first_join(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = rt.journal.open

    async def slow_open(**kw):
        reached.set()
        await gate.wait()
        await real_open(**kw)

    rt.journal.open = slow_open
    try:
        joining = asyncio.ensure_future(rt.on_transport_state({"state": "joined", "peer_present": False}))
        await asyncio.wait_for(reached.wait(), 5)
        rt.transport.on_page_attached(clock())                # 旧连接被顶替
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # 新连接先报入房
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})  # 再过能力门
        gate.set()                                            # 旧的上传头这时才写完
        await asyncio.gather(joining)
        await wait_for(lambda: rt.joined and rt.phase == "invite_ready")
    finally:
        gate.set()
        await teardown(side, clock=clock)


async def test_credentials_that_arrive_for_a_replaced_page_start_no_sdk_deadline(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    side.creds_gate = asyncio.Event()
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        issuing = asyncio.ensure_future(rt.issue_credentials())
        await wait_for(lambda: side.creds_calls)
        rt.transport.on_page_attached(clock())                # 等凭证期间页面被顶替
        side.creds_gate.set()
        assert await issuing is None                          # 旧连接拿不到，也不替新连接装能力门期限
        assert rt._sdk_deadline is None
        assert await rt.issue_credentials() is not None       # 新连接自己来要
        assert rt._sdk_deadline is not None
    finally:
        side.creds_gate.set()
        await teardown(side, clock=clock)


async def test_no_new_exit_flow_once_shutdown_started(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    stuck = asyncio.Event()
    real_seal = rt.journal.seal

    async def slow_seal(*args, **kwargs):
        await stuck.wait()                                    # 关机封存期间
        return await real_seal(*args, **kwargs)

    rt.journal.seal = slow_seal
    try:
        stopping = asyncio.ensure_future(rtm.stop_all("shutdown"))
        await wait_for(lambda: rt._shutdown_started)
        assert rt.request_finalize("peer_left") is False      # 对端 leave / route_end 不再另起收尾流程
        assert rt.exit_task is None
        stuck.set()
        await asyncio.wait_for(stopping, 3)
    finally:
        stuck.set()


async def test_a_stuck_account_map_write_does_not_hold_the_visit(tmp_path, monkeypatch, clocks):
    monkeypatch.setattr(rtm, "_ACCOUNT_RECORD_S", 0.1)
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    stuck = asyncio.Event()

    async def hang(account, visit_uid):
        await stuck.wait()                                    # 本地记账卡住

    side.deps.record_account = hang
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await asyncio.wait_for(rt.issue_credentials(), 3) is not None
    finally:
        stuck.set()
        await teardown(side, clock=clock)


async def test_a_failed_account_map_write_is_retried_in_the_background(tmp_path, monkeypatch, clocks):
    monkeypatch.setattr(rtm, "_ACCOUNT_RECORD_S", 0.1)
    monkeypatch.setattr(rtm, "_ACCOUNT_RETRY_DELAYS_S", (0.01, 0.02))
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    attempts = []

    async def flaky(account, visit_uid):
        attempts.append(visit_uid)
        if len(attempts) < 5:
            raise OSError("visit_accounts.json locked")   # 前四次写不进（超过退避表长度）

    side.deps.record_account = flaky
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await rt.issue_credentials() is not None    # 串门照常（凭证已签发，不为本地记账作废）
        await wait_for(lambda: len(attempts) == 5)          # 退避表用完后按最后的间隔继续，补写到写成为止
        await asyncio.sleep(0.1)
        assert len(attempts) == 5
    finally:
        await teardown(side, clock=clock)


async def test_no_started_frame_once_the_visit_is_ending(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    gate, reached = asyncio.Event(), asyncio.Event()
    real_media = hrt.send_media

    async def slow_media():
        reached.set()
        await gate.wait()                                     # 页面写入卡住
        return await real_media()

    hrt.send_media = slow_media
    try:
        accepting = asyncio.ensure_future(hrt.accept(True))
        await asyncio.wait_for(reached.wait(), 5)
        hrt.request_finalize("route_end")                     # 这期间结束了
        gate.set()
        await asyncio.gather(accepting)
        await finish(hrt, clock)
        assert not host.host.frames_of("visit_state_change", "started")
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_stalled_journal_open_neither_hangs_entry_nor_finalization(tmp_path, monkeypatch, clocks):
    monkeypatch.setattr(rtm, "_JOURNAL_OPEN_MAX_S", 0.2)
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    gate = asyncio.Event()
    real_open = rt.journal.open

    async def stalled_open(**kw):
        await gate.wait()                                     # 磁盘卡住
        await real_open(**kw)

    rt.journal.open = stalled_open
    try:
        assert rt._join_deadline is not None
        # 收包处理不跟着挂死；入房期限在上传头写完之前一直在
        await asyncio.wait_for(rt.on_transport_state({"state": "joined", "peer_present": False}), 3)
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)             # 收尾也不无限等上传头
        wall.advance(3600)                                    # 磁盘卡了一个小时
        gate.set()                                            # 上传头晚到写完
        await wait_for(lambda: rt.journal.sealed)             # 立刻封存，不留没封存的流水
        await wait_for(lambda: not list((side.config_dir / "visit_spool").glob("*.upload.jsonl")))
        doc = json.loads((side.config_dir / "visit_spool" / f"{rt.visit_id}.upload.json").read_text(encoding="utf-8"))
        assert doc["request"]["usage"]["duration_s"] < 600    # 时长按收尾那一刻算，不把卡盘的时间算进去
    finally:
        gate.set()
        await teardown(side, clock=clock)


async def test_a_page_that_comes_back_too_late_is_page_lost_not_unsupported(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt.transport.on_page_lost(clock())                    # 页面重载
        clock.advance(19)
        rt.transport.on_page_attached(clock())                # 第 19 s 连回
        assert await rt.issue_credentials() is not None
        page_deadline = rt.liveness.page_reload_deadline()
        assert rt._sdk_deadline is not None and page_deadline is not None
        assert abs(rt._sdk_deadline - page_deadline) < 1e-6   # 能力门期限按绝对期限剩余截短：同时到点
        clock.advance(page_deadline - clock() + 0.01)         # SDK 一直没报（设计稿：第 31 s 才加载完）
        await rt.tick()
        assert rt.finalize_reason == "local_page_lost"        # 不是「本机不支持串门」
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_reliable_peer_messages_during_finalization_are_still_acked_and_recorded(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await wait_for(lambda: len(rt.journal.lines()) >= 1)
        stuck = asyncio.Event()

        async def slow_close(*args, **kwargs):
            await stuck.wait()                                # 收尾流程停在关闭数据通道之前

        rt.close_current_line = slow_close
        rt.request_finalize("route_end")
        seq = rt.sequencer.contiguous_seq + 1
        before = len(host.host.frames)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:90", "lp": rt.room.max_lp_seen + 1, "seq": seq, "sp": "c", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "临走前的最后一句", "truncated": False, "i_done": 0,
        }, nbytes=200)
        assert rt.sequencer.contiguous_seq == seq             # 推进序号（会回 ack），对端的 leave 才等得到确认
        assert [r for r in rt.journal.lines() if r["text"] == "临走前的最后一句"]   # 进本侧转录
        await settle()
        assert not [f for f in host.host.frames[before:] if f.get("type") == "visit_line"]   # 不上屏
        stuck.set()
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_late_text_during_finalization_keeps_the_reception_gate_and_the_line_quota(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from main_logic.visit.limits import RateChannel

    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await wait_for(lambda: len(rt.journal.lines()) >= 1)
        real_admit = rt.limiter.admit
        text_calls = []

        def admit(vid, channel, **kw):
            if channel is RateChannel.TEXT:
                text_calls.append(vid)
                return SimpleNamespace(allowed=False, reason="text_rate")
            return real_admit(vid, channel, **kw)

        monkeypatch.setattr(rt.limiter, "admit", admit)
        lp = rt.room.max_lp_seen + 1
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:91", "i": 0, "lp": lp, "txt": "超速", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)                # 分片时已判超速
        assert len(text_calls) == 1
        stuck = asyncio.Event()

        async def slow_close(*args, **kwargs):
            await stuck.wait()

        rt.close_current_line = slow_close
        rt.request_finalize("route_end")
        seq = rt.sequencer.contiguous_seq + 1
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:91", "lp": lp, "seq": seq, "sp": "c", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "超速的整句", "truncated": False, "i_done": 1,
        }, nbytes=200)
        assert rt.sequencer.contiguous_seq == seq             # 照样推进序号（回 ack）
        assert len(text_calls) == 1                           # 复用分片时的配额决定
        assert not [r for r in rt.journal.lines() if r["text"] == "超速的整句"]
        monkeypatch.setattr(rt.limiter, "admit", real_admit)  # 下一行配额放行，只看接待闸门
        rt.activated = False                                  # 接待前开始收尾：补到的台词同样不收
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:92", "lp": lp + 1, "seq": seq + 1, "sp": "c", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "接待前的一句", "truncated": False, "i_done": 0,
        }, nbytes=200)
        assert rt.sequencer.contiguous_seq == seq + 1
        assert not [r for r in rt.journal.lines() if r["text"] == "接待前的一句"]
        rt.activated = True
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:93", "lp": lp + 2, "seq": seq + 2, "sp": "c", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "合法的最后一句", "truncated": False, "i_done": 0,
        }, nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:94", "lp": lp + 2, "seq": seq + 3, "sp": "c", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "占用同一 lp 的一句", "truncated": False, "i_done": 0,
        }, nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:95", "lp": 10 ** 9, "seq": seq + 4, "sp": "c", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "越界 lp 的一句", "truncated": False, "i_done": 0,
        }, nbytes=200)
        texts = [r["text"] for r in rt.journal.lines()]
        assert "合法的最后一句" in texts                         # 合法的照样进转录
        replay = [r for r in rt.snapshot()["transcript"] if r.get("line_id") == "g:93"]
        assert replay and replay[0]["addressee"] == {"side": "host", "kind": "cat"}   # 重放带完整帧形状
        assert "占用同一 lp 的一句" not in texts and "越界 lp 的一句" not in texts
        stuck.set()
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_late_text_whose_speaker_changed_since_its_first_piece_is_not_recorded(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await wait_for(lambda: len(rt.journal.lines()) >= 1)
        lp = rt.room.max_lp_seen + 1
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:96", "i": 0, "lp": lp, "txt": "猫", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)                # 开口按猫娘行
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:97", "i": 0, "lp": lp + 1, "txt": "猫", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)                # 开口不是告别
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:98", "i": 0, "lp": lp + 2, "txt": "半截", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)                # 页面上开出半截气泡
        before = len(host.host.frames)
        stuck = asyncio.Event()

        async def slow_close(*args, **kwargs):
            await stuck.wait()

        rt.close_current_line = slow_close
        rt.request_finalize("route_end")
        seq = rt.sequencer.contiguous_seq + 1
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:96", "lp": lp, "seq": seq, "sp": "h", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "收口改成人类说的", "truncated": False, "i_done": 1,
        }, nbytes=200)
        assert rt.sequencer.contiguous_seq == seq
        assert not [r for r in rt.journal.lines() if r["text"] == "收口改成人类说的"]   # 说话人记不准：不进转录
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:97", "lp": lp + 1, "seq": seq + 1, "sp": "c", "ad": "hc",
            "rt": "", "wu": True, "final": True, "txt": "收口改成告别", "truncated": False, "i_done": 1,
        }, nbytes=200)
        assert rt.sequencer.contiguous_seq == seq + 1
        assert not [r for r in rt.journal.lines() if r["text"] == "收口改成告别"]   # 与 room 同一判据（含 wu）
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:98", "lp": lp + 2, "seq": seq + 2, "sp": "c", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "半截补成整句", "truncated": False, "i_done": 1,
        }, nbytes=200)
        assert [r for r in rt.journal.lines() if r["text"] == "半截补成整句"]
        await settle()
        frames = host.host.frames[before:]
        aborted = {f.get("line_id") for f in frames if f.get("type") == "visit_line_abort"}
        assert {"g:96", "g:97"} <= aborted                    # 不一致的那两行：撤掉半截气泡
        assert [f for f in frames if f.get("type") == "visit_line" and f.get("line_id") == "g:98"]   # 整句收口
        stuck.set()
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_the_backlog_is_recorded_when_the_header_lands_in_time(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    gate = asyncio.Event()
    real_open = rt.journal.open

    async def slow_open(**kw):
        await gate.wait()
        await real_open(**kw)

    rt.journal.open = slow_open
    try:
        joining = asyncio.ensure_future(rt.on_transport_state({"state": "joined", "peer_present": False}))
        await wait_for(lambda: rt._journal_opening is not None)
        rt._count_anomaly("test", streak=False)               # 上传头还在写：先攒着
        assert rt._journal_backlog
        gate.set()                                            # 10 s 之内写完
        await asyncio.wait_for(joining, 5)
        await wait_for(lambda: not rt._journal_backlog)       # 不等下一句或封存，落盘时就补记
        assert rt.journal.anomalies == 1
    finally:
        gate.set()
        await teardown(side, clock=clock)


async def test_a_display_queue_whose_sender_stopped_is_restarted_by_the_flush(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    real_send = host.host.send_frame
    hold = asyncio.Event()

    async def send_frame(payload):
        if payload.get("line_id") == "first":
            await hold.wait()                                 # 第一帧写页面时卡住
        return await real_send(payload)

    host.host.send_frame = send_frame
    try:
        rt._post_display({"type": "visit_line", "visit_id": rt.visit_id, "line_id": "first"})
        rt._post_display({"type": "visit_line", "visit_id": rt.visit_id, "line_id": "second"})
        await settle()
        rt._display_task.cancel()                             # 发送任务被取消：队列里还剩一句
        await settle()
        await rt._flush_display(2)
        assert [f for f in host.host.frames if f.get("line_id") == "second"]   # flush 时重新拉起、送出去
    finally:
        hold.set()
        host.host.send_frame = real_send
        await teardown(host, guest, wire=wire, clock=clock)


async def test_page_frames_queued_before_the_end_reach_the_page_first(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    real_send = host.host.send_frame

    async def send_frame(payload):
        if payload.get("type") == "visit_line" and payload.get("line_id") == "slow":
            await asyncio.sleep(0.3)                          # 页面背压
        return await real_send(payload)

    host.host.send_frame = send_frame
    try:
        rt._post_display({"type": "visit_line", "visit_id": rt.visit_id, "line_id": "slow"})
        rt._post_display({"type": "visit_line", "visit_id": rt.visit_id, "line_id": "goodbye"})
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        frames = host.host.frames
        ended = [i for i, f in enumerate(frames) if f.get("type") == "visit_state_change"
                 and f.get("action") == rtm.PHASE_ENDED]
        shown = [i for i, f in enumerate(frames) if f.get("line_id") == "goodbye"]
        assert ended and shown and shown[0] < ended[0]        # 告别句先上屏，再「已结束」
    finally:
        host.host.send_frame = real_send
        await teardown(host, guest, wire=wire, clock=clock)


async def test_lines_recorded_while_the_journal_header_is_late_are_backfilled(tmp_path, monkeypatch, clocks):
    monkeypatch.setattr(rtm, "_JOURNAL_OPEN_MAX_S", 0.1)
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    gate = asyncio.Event()
    real_open = rt.journal.open

    async def slow_open(**kw):
        await gate.wait()
        await real_open(**kw)

    rt.journal.open = slow_open
    try:
        await rt.on_transport_state({"state": "joined", "peer_present": False})
        assert not rt.journal.is_open
        await rt.record_line("own_human", side="host", lp=3, ln="h:3", text="上传头还没写完时说的", truncated=False)
        rt._on_usage({"llm_output_tokens": 7})                # 这期间的用量与异常同样先攒着
        rt._count_anomaly("test", streak=False)
        gate.set()                                            # 上传头晚到写完
        await wait_for(lambda: [r for r in rt.journal.lines() if r["text"] == "上传头还没写完时说的"])
        await wait_for(lambda: not rt._journal_backlog)       # 用量 / 异常排在那一行后面补记
        assert rt.journal.usage()["llm_output_tokens"] == 7
        assert rt.journal.anomalies == 1
    finally:
        gate.set()
        await teardown(side, clock=clock)



async def test_the_backlog_still_being_flushed_is_in_the_sealed_journal(tmp_path, monkeypatch, clocks):
    import threading

    monkeypatch.setattr(rtm, "_JOURNAL_OPEN_MAX_S", 0.1)
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    gate = asyncio.Event()
    real_open = rt.journal.open
    write_gate = threading.Event()
    writing = threading.Event()
    real_append = rt.journal._append_sync

    async def slow_open(**kw):
        await gate.wait()
        await real_open(**kw)

    def slow_append(data):
        writing.set()
        write_gate.wait(10)                                   # 补写第 1 行时磁盘卡住
        real_append(data)

    rt.journal.open = slow_open
    try:
        await rt.on_transport_state({"state": "joined", "peer_present": False})
        for i in (3, 4):
            await rt.record_line("own_human", side="host", lp=i, ln=f"h:{i}", text=f"积压第{i}行", truncated=False)
        rt.journal._append_sync = slow_append
        gate.set()                                            # 上传头落盘：后台补写开始，卡在第 1 行
        await wait_for(writing.is_set)
        late = asyncio.ensure_future(rt.record_line("own_human", side="host", lp=5, ln="h:5", text="积压第5行",
                                                    truncated=False))
        await asyncio.sleep(0)                                # 积压没补完时新来的行排在积压后面
        rt.request_finalize("route_end")                      # 补写没跑完就开始收尾封存
        await asyncio.sleep(0.2)
        write_gate.set()
        await asyncio.wait_for(_finished(rt), 10)
        await asyncio.wait_for(late, 5)
        assert rt.journal.sealed
        # 按写入顺序看（lines() 会按 lp 重排）：积压没补完时新来的行排在积压后面
        texts = [r["text"] for r in rt.journal._records if str(r.get("text", "")).startswith("积压")]
        assert texts == ["积压第3行", "积压第4行", "积压第5行"]
    finally:
        gate.set()
        write_gate.set()
        await teardown(side, clock=clock)


async def test_state_reports_a_page_reload_as_reconnecting(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        snap = rt.snapshot()
        assert snap["connected"] and not snap["reconnecting"]
        rt.liveness.on_page_lost(clock())                     # 传输页重载中：没有页面能收发帧
        snap = rt.snapshot()
        assert not snap["connected"] and snap["reconnecting"]
        rt.liveness.on_page_back(clock())
        assert rt.snapshot()["connected"]
        rt.liveness.on_page_lost(clock())
        rt.phase = rtm.PHASE_ENDED                            # 结束后留在 _recent 里重放：不算重连中
        assert not rt.snapshot()["reconnecting"]
        rt.phase = rtm.PHASE_ACTIVE
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_closes_the_outbox_and_the_isolated_session(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 3)
        assert host.clients[0].closed                         # 隔离会话的客户端关掉了
        assert rt.outbox._executor is None                    # outbox 写线程停了、带正文的 .outbox.jsonl 删了
        assert not list((host.config_dir / "visit_spool").glob("*.outbox.jsonl"))
    finally:
        await teardown(guest, wire=wire, clock=clock)


async def test_a_joined_visit_is_not_ended_by_the_join_deadline(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    try:
        assert rt.joined
        rt._join_deadline = clocks[0]() - 1                   # 已报入房、只是上传头写得慢时入房期限到点
        await rt.tick()
        assert rt.finalize_reason != "relay_lost"
    finally:
        await teardown(side, wire=wire, clock=clocks[0])
