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
        assert [p["reason"] for p in wire.sent["host"] if p.get("t") == "leave"] == ["declined"]
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
