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
import logging

import pytest

from main_routers.visit_router import credentials as cr
from main_routers.visit_router import runtime as rtm
from main_routers.visit_router import transport_ws
from tests.unit.visit_runtime_harness import (
    credentials,
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


# ── Admission ─────────────────────────────────────────────────────────────


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
        assert calls == [None]   # Exactly once, before reservation.
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


# ── Capability gate ───────────────────────────────────────────────────────────


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
    assert len(side.creds_calls) == 1            # This issuance already counted.
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
    await settle()                                            # Phase frames use the ordered display queue.
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
    # End before peer verification: cancel room in background.
    await wait_for(lambda: side.cancelled)
    assert side.cancelled[0][0] == rt.visit_id


# ── Await guest /peer ─────────────────────────────────────────────────────


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


# ── Hello verification ───────────────────────────────────────────────────────


async def test_a_hello_before_joined_keeps_the_awaiting_phase(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    wire = Wire()
    wire.attach(rt, None, HOST_VID)
    await through_gate(rt)
    await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload=_guest_hello(), nbytes=900)  # Peer hello arrives first.
    assert rt.phase == "awaiting_accept"
    monkeypatch.setattr(transport_ws, "is_transport_attached", lambda visit_id, side: True)
    assert not rt.joined and rtm.VisitRuntime.transport_alive(rt)  # Verified peer: channel is live, so cleanup still sends leave.
    await rt.on_transport_state({"state": "joined", "peer_present": True})            # Room-entry report arrives later.
    assert rt.phase == "awaiting_accept"                  # Do not revert to invite_ready: invitation remains acceptable.
    assert not side.host.frames_of("visit_state_change", "invite_ready")
    status, _ = await rt.accept(False)
    assert status == 200
    await _finished(rt)


async def test_verified_hello_invites_the_family_and_fills_peer_vid(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    try:
        await rt.on_transport_state({"state": "connected", "peer_present": True})
        assert wire.sent_types("host") == [] or "hello" in wire.sent_types("host")
        await rt.flush()
        assert "hello" in wire.sent_types("host")
        await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload=_guest_hello(), nbytes=900)
        assert rt.phase == "awaiting_accept" and rt.peer.uid == GUEST_UID
        await settle()                                        # Invite frames use the ordered display queue.
        invite = side.host.frames_of("visit_invite")
        assert len(invite) == 1 and invite[0]["peer_short_id"] == GUEST_UID[:6].upper()
        media = [m for m in wire.downlinks["host"] if m.get("type") == "media"]
        assert media[-1]["subscribe"] is False and media[-1]["peer_vid"] == GUEST_VID
        assert side.clients == []                  # No isolated session before reception.
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
        assert hrt.sequencer.contiguous_seq == 3            # text advances seq normally and gets acked.
        await hrt.flush()
        # Reliable delivery finishes in shielded tasks; pumps send ack later, so await wire delivery.
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "ack" and p.get("seq") == 3])
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


# ── Reception and activation ───────────────────────────────────────────────────────


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
        clock.advance(9)                       # Host sends ready only at second 67.
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
        # First credentials after reload include verified peer_vid.
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


# ── In-flight credentials /activation racing cleanup and shutdown ───────────────────────


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
    await _finished(rt)                                   # Cleanup ends before credentials exist; no room yet to cancel.
    assert side.cancelled == []
    creds_task = rt._creds_task
    assert creds_task is not None and not creds_task.done() and creds_task in rtm._detached  # Shutdown can collect after unregistration.
    side.creds_gate.set()                                 # Servers only now issues the room.
    assert await issuing is None
    await wait_for(lambda: side.cancelled)
    rt._cancel_room_once()                                # Cleanup and late credentials each call it; send once.
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
    # Do not await Servers: shutdown cancels pending credentials without exposing CancelledError to transport handlers.
    assert await asyncio.wait_for(issuing, 1) is None
    side.creds_gate.set()
    await settle()
    assert side.host.takeovers == []
    assert rtm.get_runtime("Host") is None and rt.finalizing


async def test_a_cancelled_accept_request_still_finishes_the_acceptance(tmp_path, monkeypatch):
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
        accepting.cancel()                                # Accept request cancelled (page disconnected, etc.).
        await asyncio.gather(accepting, return_exceptions=True)
        gate.set()
        await wait_for(lambda: "ready" in [p.get("t") for p in wire.sent["host"]], timeout=5)  # Still send ready.
        assert hrt.ready_exchanged and not hrt.finalizing
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_an_activation_cancelled_while_opening_the_spool_leaves_a_memory_off_state(tmp_path, monkeypatch):
    from main_logic.visit import spool as spool_mod

    monkeypatch.setattr(rtm, "VISIT_ACTIVATION_ALLOWANCE_S", 0.3)
    real_open = spool_mod.VisitSpool.open

    async def slow_open(self, *args, **kwargs):
        await asyncio.sleep(5)                            # Spool opening exceeds activation deadline: activation cancelled.
        return await real_open(self, *args, **kwargs)

    monkeypatch.setattr(spool_mod.VisitSpool, "open", slow_open)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    try:
        status, _ = await hrt.accept(True)
        assert status == 200
        await asyncio.wait_for(_finished(hrt), 15)
        state = await spool_mod.VisitSpool(hrt.config_dir, hrt.visit_id).read_state()
        assert state["memory_enabled"] is False            # Cleanup changes already-written memory-enabled state back to disabled.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


@pytest.mark.parametrize("memory", [True, False])
async def test_an_activation_cancelled_while_writing_the_state_still_leaves_memory_off(tmp_path, monkeypatch, memory):
    from main_logic.visit import spool as spool_mod

    monkeypatch.setattr(rtm, "VISIT_ACTIVATION_ALLOWANCE_S", 0.3)
    real_write = spool_mod.VisitSpool.write_state

    async def late_write(self, state):
        await asyncio.sleep(1.0)                         # Cancellation during state write; it persists after cleanup's update.
        await real_write(self, state)

    async def slow_write(self, state):
        # Like disk-writing threads: complete the write despite cancelled waiters.
        await asyncio.shield(asyncio.ensure_future(late_write(self, state)))

    monkeypatch.setattr(spool_mod.VisitSpool, "write_state", slow_write)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False,
                                                    settings={"visitMemoryEnabled": memory})
    hrt = host.rt
    try:
        status, _ = await hrt.accept(True)
        assert status == 200
        await asyncio.wait_for(_finished(hrt), 15)
        state = None
        for _ in range(50):
            state = await spool_mod.VisitSpool(hrt.config_dir, hrt.visit_id).read_state()
            if state and state.get("finalized"):
                break
            await asyncio.sleep(0.1)
        assert state and state["finalized"]               # Cleanup awaits that write before finalized, even for memory-disabled visits.
        assert state["memory_enabled"] is False           # Cleanup still restores memory disabled.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


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
        hrt.request_finalize("route_end")                 # Visit ended while awaiting spool persistence.
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
        gate.set()                                        # Session constructed only after cleanup starts.
        assert (await accepting)[0] == 200
        await finish(hrt, clock)
        assert len(host.clients) == 1 and host.clients[0].closed
        assert hrt.room is None and not hrt.activated
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_session_created_after_teardown_is_closed_in_the_background(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "VISIT_ACTIVATION_ALLOWANCE_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    gate, reached = asyncio.Event(), asyncio.Event()
    real_create = host.deps.create_session

    async def stubborn_create(*args, **kwargs):
        reached.set()
        while not gate.is_set():                              # Swallow cancellation, exceeding cleanup wait.
            try:
                await gate.wait()
            except asyncio.CancelledError:
                continue
        return await real_create(*args, **kwargs)

    host.deps.create_session = stubborn_create
    try:
        accepting = asyncio.ensure_future(hrt.accept(True))
        await asyncio.wait_for(reached.wait(), 5)
        hrt.request_finalize("route_end")
        await asyncio.wait_for(_finished(hrt), 10)            # teardown unregisters without waiting for it.
        assert not host.clients
        gate.set()                                            # Session only now constructed.
        await wait_for(lambda: host.clients and host.clients[0].closed, timeout=5)   # Close itself in background.
        accepting.cancel()
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_stalled_seal_does_not_keep_the_visit_registered(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(rtm, "_SEAL_MAX_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = threading.Event()
    real_seal = rt.journal._seal_sync
    order: list[str] = []

    def slow_seal(doc):
        release.wait(10)                                      # Disk stalls while writing .upload.json
        real_seal(doc)
        order.append("sealed")

    real_finalize = rt._finalize_spool

    async def finalize_spool(reason):
        order.append("finalized")
        await real_finalize(reason)

    rt.journal._seal_sync = slow_seal
    rt._finalize_spool = finalize_spool
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        assert rtm.get_runtime("Host") is None                # Still unregister and release reservation.
        await asyncio.sleep(0.3)                              # Start the background cleanup chain.
        assert "finalized" not in order                       # Seal incomplete: do not mark spool finalized yet.
        assert rtm.has_visit_background_tasks("Host")         # Character cannot rename/delete before background cleanup finishes.
        assert rt.visit_id not in host.uploads                # .upload.json unpersisted: do not schedule upload yet.
        release.set()
        await wait_for(lambda: "finalized" in order, timeout=5)
        assert order == ["sealed", "finalized"]               # Background order remains seal before finalized.
        assert rt.journal.sealed
        await wait_for(lambda: rt.visit_id in host.uploads, timeout=5)   # Schedule upload after persistence.
        await wait_for(lambda: not rtm.has_visit_background_tasks("Host"), timeout=5)
    finally:
        release.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_during_a_stalled_seal_waits_for_the_same_seal(tmp_path, monkeypatch):
    import threading

    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = threading.Event()
    real_seal = rt.journal._seal_sync
    seals = []

    def slow_seal(doc):
        seals.append(doc)
        release.wait(10)
        real_seal(doc)

    rt.journal._seal_sync = slow_seal
    try:
        rt.request_finalize("route_end")
        await wait_for(lambda: seals, timeout=5)              # Exit flow blocked on sealing.
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)   # Shut down now.
        state = await rt.spool.read_state() if rt.spool is not None else None
        assert not (state or {}).get("finalized")             # Seal unpersisted: do not mark finalized first.
        assert not rt._spool_finalized
        release.set()
        await wait_for(lambda: rt.journal.sealed and rt._sealing.done(), timeout=5)
        assert len(seals) == 1                                # Seal exactly once, without a second empty seal.
    finally:
        release.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_spool_opened_after_teardown_is_finalized(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "VISIT_ACTIVATION_ALLOWANCE_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = hrt._open_spool

    async def stubborn_open(subjects):
        reached.set()
        while not gate.is_set():                              # Swallow cancellation, exceeding cleanup wait.
            try:
                await gate.wait()
            except asyncio.CancelledError:
                continue
        await real_open(subjects)

    hrt._open_spool = stubborn_open
    try:
        accepting = asyncio.ensure_future(hrt.accept(True))
        await asyncio.wait_for(reached.wait(), 5)
        hrt.request_finalize("route_end")
        await asyncio.wait_for(_finished(hrt), 10)            # No spool attached at sealing.
        assert hrt.spool is None
        gate.set()                                            # Spool opens only now.
        await wait_for(lambda: hrt.spool is not None and hrt._spool_finalized, timeout=5)
        state = None
        for _ in range(50):
            state = await hrt.spool.read_state()
            if state and state.get("finalized"):
                break
            await asyncio.sleep(0.1)
        assert state and state.get("finalized")               # Late spool closes/finalizes itself.
        assert not hrt.spool.is_open
        accepting.cancel()
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def _bring_up_with_a_pending_header(tmp_path, monkeypatch):
    """Both sides activated while their upload headers are still being written (the disk is slow)."""
    from main_routers.visit_router import transcript_upload

    monkeypatch.setattr(rtm, "_JOURNAL_OPEN_MAX_S", 0.1)
    gate = asyncio.Event()
    real_open = transcript_upload.UploadJournal.open

    async def slow_open(self, **kw):
        await gate.wait()
        await real_open(self, **kw)

    monkeypatch.setattr(transcript_upload.UploadJournal, "open", slow_open)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    assert not host.rt.journal.is_open
    return host, guest, wire, clock, gate


async def test_a_header_still_writing_at_the_end_keeps_seal_before_finalized(tmp_path, monkeypatch):
    host, guest, wire, clock, gate = await _bring_up_with_a_pending_header(tmp_path, monkeypatch)
    rt = host.rt
    order: list[str] = []
    real_seal = rt.journal.seal

    async def seal(reason, **kw):
        doc = await real_seal(reason, **kw)
        if doc is not None:
            order.append("sealed")
        return doc

    real_finalize = rt._finalize_spool

    async def finalize_spool(reason):
        order.append("finalized")
        await real_finalize(reason)

    rt.journal.seal = seal
    rt._finalize_spool = finalize_spool
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        await asyncio.sleep(0.2)
        assert order == []                                    # Upload header still writing: neither seal nor mark finalized first.
        assert rtm.has_visit_background_tasks("Host")
        gate.set()                                            # Upload header persists.
        await wait_for(lambda: order == ["sealed", "finalized"], timeout=5)   # Seal before finalized.
        await wait_for(lambda: not rtm.has_visit_background_tasks("Host"), timeout=5)
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_with_a_header_still_writing_does_not_finalize_the_spool(tmp_path, monkeypatch):
    host, guest, wire, clock, gate = await _bring_up_with_a_pending_header(tmp_path, monkeypatch)
    rt = host.rt
    try:
        assert rt.spool is not None
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)
        assert not rt._spool_finalized                        # seal() immediately returning None is not completed sealing.
        state = await rt.spool.read_state()
        assert not (state or {}).get("finalized")             # Leave for startup recovery.
        assert rt._sealing is None                            # Shutdown never calls seal(), avoiding a None seal cache.
        assert rt.journal._open_abandoned                     # Cancelled upload-header writes stop awaiting stuck disk.
        assert rt._journal_opening.done()                     # Shutdown cancels it explicitly, without relying on event-loop teardown.
        assert not rtm.has_visit_background_tasks("Host")     # Do not start unbounded background sealing chains, allowing process exit.
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_ended_is_not_sent_while_the_display_queue_is_still_draining(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "_DISPLAY_FLUSH_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    real_send = host.host.send_frame

    queued_at_end: list = []

    async def send_frame(payload):
        if payload.get("line_id") in ("slow1", "slow2"):
            await asyncio.sleep(0.5)                          # Page backpressure: slow writes per frame.
        if payload.get("type") == "visit_state_change" and payload.get("action") == rtm.PHASE_ENDED:
            task = rt._display_task
            queued_at_end.append((len(rt._display), task is None or task.done()))   # Remaining queue frames and sender stopped status.
        return await real_send(payload)

    host.host.send_frame = send_frame
    try:
        rt._post_display({"type": "visit_line", "visit_id": rt.visit_id, "line_id": "slow1"})
        rt._post_display({"type": "visit_line", "visit_id": rt.visit_id, "line_id": "slow2"})
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        await asyncio.sleep(1.2)
        frames = host.host.frames
        ended = [i for i, f in enumerate(frames) if f.get("type") == "visit_state_change"
                 and f.get("action") == rtm.PHASE_ENDED]
        assert ended
        late = [f for f in frames[ended[0]:] if f.get("line_id") in ("slow1", "slow2")]
        assert not late                                       # Stop undeliverable frames first: no whole sentences after ended.
        assert queued_at_end == [(0, True)]                   # Stop/clear display queues before ended; no concurrent writes.
    finally:
        host.host.send_frame = real_send
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_seal_landing_after_stop_all_leaves_the_spool_to_recovery(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(rtm, "_SEAL_MAX_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = threading.Event()
    real_seal = rt.journal._seal_sync

    def slow_seal(doc):
        release.wait(10)
        real_seal(doc)

    rt.journal._seal_sync = slow_seal
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)             # Sealing moved to background.
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)   # Process starts shutdown now.
        release.set()
        await wait_for(lambda: rt.journal.sealed and rt._sealing.done(), timeout=5)
        await wait_for(lambda: not rtm.has_visit_background_tasks("Host"), timeout=5)
        assert not rt._spool_finalized                        # Persistence after shutdown: startup recovers spool/memory commits.
    finally:
        release.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_late_seal_that_fails_still_schedules_the_upload(tmp_path, monkeypatch):
    host, guest, wire, clock, gate = await _bring_up_with_a_pending_header(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        assert rt.visit_id not in host.uploads

        def broken_seal(doc):
            raise OSError("disk full")

        rt.journal._seal_sync = broken_seal
        gate.set()                                            # Upload header persisted but sealing fails.
        await wait_for(lambda: rt.visit_id in host.uploads, timeout=5)   # Still schedule upload; retries reseal from the stream.
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_stop_all_abandons_a_header_left_to_a_deferred_seal(tmp_path, monkeypatch):
    host, guest, wire, clock, gate = await _bring_up_with_a_pending_header(tmp_path, monkeypatch)
    rt = host.rt
    try:
        opening = rt._journal_opening
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)             # Normal cleanup times out awaiting header; runtime already unregistered.
        assert rtm.has_visit_background_tasks("Host") and not opening.done()  # Only background sealing chain awaits it.
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)
        await wait_for(lambda: opening.done(), timeout=2)
        assert opening.cancelled() and rt.journal._open_abandoned  # Cancelling background chains abandons/cancels header writes too.
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_stop_all_abandons_the_header_even_before_the_deferred_seal_starts(tmp_path, monkeypatch):
    from main_logic.visit import local_chars

    host, guest, wire, clock, gate = await _bring_up_with_a_pending_header(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()

    async def stuck_resolve(uid):
        await stuck.wait()                                    # Stuck disk also blocks character-name lookup; background chain has not entered _seal_after_header.
        return "Host"

    try:
        opening = rt._journal_opening
        monkeypatch.setattr(local_chars, "resolve_char_name", stuck_resolve)
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        assert not opening.done()
        await wait_for(lambda: bool(rtm._resolving_names))    # Prove chain still reads character names, not _seal_after_header.
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)  # Cancellation during character-name read.
        await wait_for(lambda: opening.done(), timeout=2)
        assert opening.cancelled() and rt.journal._open_abandoned
    finally:
        stuck.set()
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_the_header_wait_shares_the_seal_deadline(tmp_path, monkeypatch):
    host, guest, wire, clock, gate = await _bring_up_with_a_pending_header(tmp_path, monkeypatch)
    rt = host.rt
    monkeypatch.setattr(rtm, "_JOURNAL_OPEN_MAX_S", 5.0)      # Header write's own limit far exceeds sealing deadline.
    monkeypatch.setattr(rtm, "_SEAL_MAX_S", 1.0)
    stuck = asyncio.get_running_loop().create_future()
    rt.family_records.add(stuck)                              # One relative utterance never persists, exhausting the sealing deadline.
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        assert await asyncio.wait_for(rt.seal_and_finalize("route_end"), 10) is False
        assert loop.time() - started < 1.6                    # No separate header-wait budget; share the same deadline.
    finally:
        stuck.cancel()
        rt.family_records.discard(stuck)
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_seal_outliving_the_exit_flow_is_handed_to_stop_all(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(rtm, "_SEAL_MAX_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = threading.Event()
    real_seal = rt.journal._seal_sync

    def slow_seal(doc):
        release.wait(10)                                      # Stuck seal write.
        real_seal(doc)

    rt.journal._seal_sync = slow_seal
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 15)
        write = rt.journal.seal_write                         # Actual disk-writing task, awaited only through shield outside.
        assert write is not None and not write.done()
        assert write in rtm._awaited_writes and write not in rtm._detached  # Registered as wait-only, never cancel.
        await asyncio.wait_for(rtm.stop_all("shutdown"), 10)
        assert not write.cancelled()                          # Shutdown does not cancel it; queued sealing persists when released.
        release.set()
        await wait_for(lambda: write.done() and rt.journal.sealed, timeout=5)
    finally:
        release.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_stop_all_waits_for_a_seal_write_started_during_shutdown(tmp_path, monkeypatch):
    import threading

    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = threading.Event()
    real_seal = rt.journal._seal_sync

    def slow_seal(doc):
        threading.Timer(0.7, release.set).start()         # Disk write exceeds this visit's shutdown wait.
        release.wait(10)
        real_seal(doc)

    rt.journal._seal_sync = slow_seal
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 10)   # This seal starts only during shutdown.
        write = rt.journal.seal_write
        assert write is not None and write.done() and not write.cancelled()   # stop_all's final wait catches its completion.
        assert rt.journal.sealed_path.exists()            # .upload.json really persisted.
    finally:
        release.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_seal_wrapper_left_by_shutdown_is_retired_by_stop_all(tmp_path, monkeypatch):
    import threading

    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = threading.Event()
    real_seal = rt.journal._seal_sync

    def stuck_seal(doc):
        release.wait(10)                                  # Disk write exceeds the entire shutdown budget.
        real_seal(doc)

    rt.journal._seal_sync = stuck_seal
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 10)
        write = rt.journal.seal_write
        assert write is not None and not write.done()     # Wait without cancelling disk writes still in threads.
        assert rt._sealing is not None and rt._sealing.done()   # stop_all collects outer shield-waiters, not leaving them until loop destruction.
        release.set()
        await wait_for(lambda: write.done() and rt.journal.sealed_path.exists(), timeout=5)
    finally:
        release.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_stop_all_lets_an_outbox_cleanup_started_during_shutdown_finish(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    real_flush = rt.outbox.flush

    async def slow_flush():
        await asyncio.sleep(0.7)                          # Exceeds this visit's own wait, still within total budget.
        await real_flush()

    rt.outbox.flush = slow_flush
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 10)
        cleanup = rt.outbox.close_task
        assert cleanup is not None and cleanup.done()
        assert not cleanup.cancelled()                    # First await file deletion with a deadline, without immediate cancellation.
        assert not rt.outbox.path.exists()
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_late_seal_is_the_one_seal_of_the_visit(tmp_path, monkeypatch):
    import threading

    host, guest, wire, clock, gate = await _bring_up_with_a_pending_header(tmp_path, monkeypatch)
    rt = host.rt
    release = threading.Event()
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        real_seal = rt.journal._seal_sync

        def slow_seal(doc):
            release.wait(10)
            real_seal(doc)

        rt.journal._seal_sync = slow_seal
        gate.set()                                            # Header persisted: background sealing begins, disk write stuck.
        await wait_for(lambda: rt._sealing is not None, timeout=5)
        assert not rt._seal_settled()                         # Shutdown/teardown see the same pending seal.
        release.set()
        await wait_for(lambda: rt._sealing.done() and rt.journal.sealed, timeout=5)
    finally:
        release.set()
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_late_spool_waits_for_a_deferred_seal(tmp_path, monkeypatch):
    from main_routers.visit_router import transcript_upload

    monkeypatch.setattr(rtm, "_JOURNAL_OPEN_MAX_S", 0.1)
    monkeypatch.setattr(rtm, "VISIT_ACTIVATION_ALLOWANCE_S", 0.2)
    header_gate = asyncio.Event()
    real_open = transcript_upload.UploadJournal.open

    async def slow_open(self, **kw):
        await header_gate.wait()
        await real_open(self, **kw)

    monkeypatch.setattr(transcript_upload.UploadJournal, "open", slow_open)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    spool_gate, reached = asyncio.Event(), asyncio.Event()
    real_open_spool = hrt._open_spool

    async def stubborn_open(subjects):
        reached.set()
        while not spool_gate.is_set():
            try:
                await spool_gate.wait()
            except asyncio.CancelledError:
                continue
        await real_open_spool(subjects)

    hrt._open_spool = stubborn_open
    try:
        accepting = asyncio.ensure_future(hrt.accept(True))
        await asyncio.wait_for(reached.wait(), 5)
        hrt.request_finalize("route_end")
        await asyncio.wait_for(_finished(hrt), 10)            # Header writing: defer sealing to background.
        spool_gate.set()                                      # Spool attaches only now.
        await wait_for(lambda: hrt.spool is not None, timeout=5)
        await asyncio.sleep(0.2)
        assert not hrt._spool_finalized                       # Seal unpersisted: do not mark finalized first.
        header_gate.set()                                     # Header persists ->seal ->finalize.
        await wait_for(lambda: hrt._spool_finalized, timeout=5)
        assert hrt.journal.sealed
        accepting.cancel()
    finally:
        header_gate.set()
        spool_gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_stalled_spool_finalize_does_not_keep_the_visit_registered(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "_SEAL_MAX_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    gate = asyncio.Event()
    real_update = rt.spool.update_state

    async def slow_update(**changes):
        if "finalized" in changes:
            await gate.wait()                                 # Disk stuck during finalized write.
        return await real_update(**changes)

    rt.spool.update_state = slow_update
    finalizers: list = []
    real_finalize = rt._finalize_spool

    async def finalize_spool(reason):
        finalizers.append(asyncio.current_task())
        return await real_finalize(reason)

    rt._finalize_spool = finalize_spool
    try:
        before = len(host.commits)
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        assert rtm.get_runtime("Host") is None                # Still unregister and release reservation.
        assert rtm.has_visit_background_tasks("Host")         # Memory commit still awaits it in background.
        assert finalizers and finalizers[0] in rtm._detached  # Underlying stuck disk task also registered, visible to shutdown.
        assert len(host.commits) == before
        gate.set()
        await wait_for(lambda: len(host.commits) > before, timeout=5)   # Start memory commit after spool writes.
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_late_line_recorded_after_ended_was_sent_is_not_shown(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await wait_for(lambda: len(rt.journal.lines()) >= 1)
        lp = rt.room.max_lp_seen + 1
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:120", "i": 0, "lp": lp, "txt": "半截", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)                # Partial bubble opens on page.
        stuck = asyncio.Event()

        async def slow_close(*args, **kwargs):
            await stuck.wait()

        rt.close_current_line = slow_close
        rt.request_finalize("route_end")
        gate = asyncio.Event()
        real_record = rt.record_line

        async def slow_record(*args, **kwargs):
            await gate.wait()                                 # Slow persistence while ended sends.
            return await real_record(*args, **kwargs)

        rt.record_line = slow_record
        before = len(host.host.frames)
        receiving = asyncio.ensure_future(rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:120", "lp": lp, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
            "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "补到的整句", "truncated": False, "i_done": 1,
        }, nbytes=200))
        await asyncio.sleep(0.05)
        rt._ended_published = True
        gate.set()
        await asyncio.wait_for(receiving, 5)
        await settle()
        assert not [f for f in host.host.frames[before:] if f.get("type") == "visit_line"
                    and f.get("line_id") == "g:120"]          # No later page completion after ended.
        stuck.set()
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_spool_attached_after_the_deferred_files_finished_is_finalized(tmp_path, monkeypatch):
    from main_routers.visit_router import transcript_upload

    monkeypatch.setattr(rtm, "_JOURNAL_OPEN_MAX_S", 0.1)
    monkeypatch.setattr(rtm, "VISIT_ACTIVATION_ALLOWANCE_S", 0.2)
    header_gate = asyncio.Event()
    real_open = transcript_upload.UploadJournal.open

    async def slow_open(self, **kw):
        await header_gate.wait()
        await real_open(self, **kw)

    monkeypatch.setattr(transcript_upload.UploadJournal, "open", slow_open)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    spool_gate, reached = asyncio.Event(), asyncio.Event()
    real_open_spool = hrt._open_spool

    async def stubborn_open(subjects):
        reached.set()
        while not spool_gate.is_set():
            try:
                await spool_gate.wait()
            except asyncio.CancelledError:
                continue
        await real_open_spool(subjects)

    hrt._open_spool = stubborn_open
    try:
        accepting = asyncio.ensure_future(hrt.accept(True))
        await asyncio.wait_for(reached.wait(), 5)
        hrt.request_finalize("route_end")
        await asyncio.wait_for(_finished(hrt), 10)
        header_gate.set()                                     # Deferred seal chain finishes first, without a spool yet.
        await wait_for(lambda: hrt._deferred_files_done, timeout=5)
        assert not hrt._spool_finalized
        spool_gate.set()                                      # Spool attaches later with no collector; finalize itself.
        await wait_for(lambda: hrt._spool_finalized, timeout=5)
        accepting.cancel()
    finally:
        header_gate.set()
        spool_gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_nothing_reaches_the_page_once_ended_was_decided(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        before = len(host.host.frames)
        rt._ended_published = True
        rt._post_display({"type": "visit_line", "visit_id": rt.visit_id, "line_id": "after-ended"})
        await settle()
        assert not rt._display                                # Gate every entry point consistently.
        assert not [f for f in host.host.frames[before:] if f.get("line_id") == "after-ended"]
    finally:
        rt._ended_published = False
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_stays_within_its_total_budget(tmp_path, monkeypatch):
    import threading

    from main_routers.visit_router import session_pool

    monkeypatch.setattr(rtm, "VISIT_SHUTDOWN_BUDGET_S", 0.6)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = threading.Event()
    stuck = asyncio.Event()
    real_seal = rt.journal._seal_sync

    def slow_seal(doc):
        release.wait(10)
        real_seal(doc)

    async def slow_close_line(*args, **kwargs):
        await stuck.wait()

    async def slow_outbox_close():
        await stuck.wait()

    async def slow_session_close(session):
        await stuck.wait()

    async def stubborn():
        while not stuck.is_set():                             # Background task ignores cancellation.
            try:
                await stuck.wait()
            except asyncio.CancelledError:
                continue

    rt.journal._seal_sync = slow_seal
    rt.close_current_line = slow_close_line
    rt.outbox.close = slow_outbox_close
    monkeypatch.setattr(session_pool, "close_visit_session", slow_session_close)
    lingering = asyncio.ensure_future(stubborn())
    rtm._keep_detached(lingering)
    try:
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)   # Every step blocks.
        # Total duration uses one budget, not summed step caps; final background cleanup also uses remaining budget.
        assert asyncio.get_running_loop().time() - started < 0.95
    finally:
        release.set()
        stuck.set()
        await asyncio.gather(lingering, return_exceptions=True)
        await teardown(host, guest, wire=wire, clock=clock)


async def test_non_finite_transport_stats_are_not_kept(tmp_path, monkeypatch):
    import json

    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt.on_transport_stats({"rtt_ms": float("inf"), "rx_fps": float("nan"), "tx_fps": 30, "rx_kbps": 500,
                               "loss_pct": 0.0, "rx_w": 640, "rx_h": 480})
        assert "rtt_ms" not in rt.stats and "rx_fps" not in rt.stats and rt.stats["tx_fps"] == 30
        json.dumps(rt.snapshot(), allow_nan=False)            # /state still serializes.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_gives_the_room_cancel_its_floor_even_past_the_budget(tmp_path, monkeypatch, clocks):
    monkeypatch.setattr(rtm, "VISIT_SHUTDOWN_BUDGET_S", 0.1)
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    done = []

    async def slow_cancel(visit_id, **kwargs):
        await asyncio.sleep(0.5)                              # Room cancellation takes longer than total budget.
        done.append(visit_id)
        return True

    rt.deps.cancel_room = slow_cancel
    await asyncio.wait_for(rtm.stop_all("shutdown"), 5)
    assert done == [rt.visit_id]                              # Wait at least the minimum; do not retain invite/quota until expiry.


async def test_stop_all_waits_for_a_room_cancel_left_by_a_finished_visit(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    done = []

    async def slow_cancel(visit_id, **kwargs):
        await asyncio.sleep(0.3)                              # Room cancellation still in flight.
        done.append(visit_id)
        return True

    rt.deps.cancel_room = slow_cancel
    rt.request_finalize("route_end")                          # No matching host; normal ending.
    await _finished(rt)
    assert rt.visit_id not in [r.visit_id for r in rtm._runtimes.values()] and done == []
    await asyncio.wait_for(rtm.stop_all("shutdown"), 5)
    assert done == [rt.visit_id]                              # Runtime dismantled; shutdown still awaits cancellation delivery.


async def test_stop_all_waits_for_orphan_room_cancels_alongside_the_shutdowns(tmp_path, monkeypatch, clocks):
    monkeypatch.setattr(rtm, "_SHUTDOWN_ROOM_CANCEL_S", 0.3)
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    real_shutdown = rt.shutdown
    order: list[str] = []

    async def slow_shutdown():
        await asyncio.sleep(1.0)                              # This visit shuts down slowly, much longer than orphan wait caps.
        await real_shutdown()
        order.append("shutdown_done")

    async def never_answers():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            order.append("orphan_cancelled")
            raise

    rt.shutdown = slow_shutdown
    orphan = asyncio.ensure_future(never_answers())          # Dismantled visit left an unresponsive room-cancellation request.
    rtm._room_cancels.add(orphan)
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 10)
        # Await alongside shutdown; cancel orphans at deadline rather than adding a wait after visit shutdown (order only, not absolute timings).
        assert order == ["orphan_cancelled", "shutdown_done"]
    finally:
        orphan.cancel()
        rtm._room_cancels.discard(orphan)


async def test_stop_all_leaves_a_registered_visits_own_room_cancel_to_its_shutdown(tmp_path, monkeypatch, clocks):
    # Orphans stop at 0.5s, distinguishing behavior; shutdown waits at least 0.4+0.5=0.9s, with 0.3s margin.
    monkeypatch.setattr(rtm, "_SHUTDOWN_ROOM_CANCEL_S", 0.5)
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    done = []

    async def slow_cancel(visit_id, **kwargs):
        await asyncio.sleep(0.6)                              # Slow but normal room cancellation.
        done.append(visit_id)
        return True

    rt.deps.cancel_room = slow_cancel
    rt._cancel_room_once()                                    # Still-registered visit already sent cancellation.
    real_shutdown = rt.shutdown

    async def slow_shutdown():
        await asyncio.sleep(0.4)                              # Earlier seal/spool steps consume time.
        await real_shutdown()                                 # At cancellation step, await its own guaranteed minimum.

    rt.shutdown = slow_shutdown
    await asyncio.wait_for(rtm.stop_all("shutdown"), 10)
    assert done == [rt.visit_id]                              # Not prematurely cut off as an orphan.


async def test_stop_all_cancels_a_room_cancel_whose_shutdown_failed_early(tmp_path, monkeypatch, clocks, caplog):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)

    async def never_answers(visit_id, **kwargs):
        await asyncio.sleep(30)

    rt.deps.cancel_room = never_answers
    rt._cancel_room_once()                                    # This visit's own cancellation.
    pending = rt._room_cancel_task

    async def broken_shutdown():
        raise RuntimeError("shutdown broke early")            # Never reached its own cancellation wait.

    rt.shutdown = broken_shutdown
    try:
        with caplog.at_level(logging.WARNING):
            await asyncio.wait_for(rtm.stop_all("shutdown"), 5)
        assert pending.cancelled()                            # stop_all collects unawaited/uncancelled requests, also waiting after cancellation.
        assert any("shutdown failed" in r.getMessage() and rt.visit_id[:6] in r.getMessage()
                   for r in caplog.records)                   # Log mid-flow errors by visit.
    finally:
        pending.cancel()


async def test_a_late_spool_after_stop_all_is_left_to_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "VISIT_ACTIVATION_ALLOWANCE_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = hrt._open_spool

    async def stubborn_open(subjects):
        reached.set()
        while not gate.is_set():
            try:
                await gate.wait()
            except asyncio.CancelledError:
                continue
        await real_open(subjects)

    hrt._open_spool = stubborn_open
    try:
        accepting = asyncio.ensure_future(hrt.accept(True))
        await asyncio.wait_for(reached.wait(), 5)
        hrt.request_finalize("route_end")
        await asyncio.wait_for(_finished(hrt), 10)
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)   # Unregistered visit skips shutdown(), but process is shutting down.
        gate.set()
        await wait_for(lambda: hrt.spool is not None, timeout=5)
        await asyncio.sleep(0.3)
        assert not hrt._spool_finalized                       # Leave for startup recovery, not partial writes during process exit.
        accepting.cancel()
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_failing_deferred_chain_still_hands_late_spools_over(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt._files_deferred = True

        def broken_start_seal(*args, **kwargs):
            raise RuntimeError("boom")

        rt._start_seal = broken_start_seal
        await rt._seal_late_journal("route_end", rt.wall(), header_ok=True)   # Sealing errors leak no background tasks.
        assert rt._deferred_files_done                        # Set flags even on mid-chain errors so late-attached spools have collectors.
        assert rt._spool_finalized                            # Already attached spool collected normally.
    finally:
        rt.__dict__.pop("_start_seal", None)                  # Restore real sealing in teardown cleanup.
        await teardown(host, guest, wire=wire, clock=clock)


async def test_the_held_turn_wrap_up_is_released_when_the_visit_ends(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        events = host.host.events
        assert "release_turn_wrap_up" in events
        assert events.index("release_turn_wrap_up") > events.index("release_takeover")   # Settle only after returning ordinary conversation.
        outputs = [i for i, e in enumerate(events) if e.startswith("output:visit-")]
        assert outputs and events.index("release_turn_wrap_up") > max(outputs)   # After ritual line and overview finish using managers.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_busy_admission_releases_the_held_wrap_up(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    side.host.interrupt_ok = False                        # Cannot interrupt main chat: reject as busy.
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await rt.issue_credentials() is None
        await asyncio.wait_for(_finished(rt), 10)
        assert rt.finalize_reason == "busy"
        assert "release_turn_wrap_up" in side.host.events     # Admission failures also release and settle.
    finally:
        await teardown(side, clock=clock)


async def test_shutdown_keeps_the_turn_wrap_up_held(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)
        assert "release_turn_wrap_up" not in host.host.events   # Do not release at shutdown; idle sessions must not renew/replace during shutdown.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_lines_recorded_before_the_first_join_reach_the_journal(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    assert rt._journal_opening is None                        # Before room entry, upload-header writing has not begun.
    # Peer hello arrived/accepted/activated first; opening line recorded before room report.
    await rt.record_line("own_cat", side="host", lp=1, ln="h:1", text="开场", truncated=False)
    assert [r["text"] for r in rt._journal_backlog if r.get("kind") == "line"] == ["开场"]  # Buffer without dropping.
    await rt.on_transport_state({"state": "joined", "peer_present": False})
    await wait_for(lambda: any(r.get("text") == "开场" for r in rt.journal._records))  # Append to stream once header writes.
    rt.request_finalize("route_end")
    await _finished(rt)


async def test_an_overrun_outbox_cleanup_is_handed_to_stop_all(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "_CLOSE_WAIT_S", 0.2)
    monkeypatch.setattr(rtm, "_SHUTDOWN_ROOM_CANCEL_S", 5.0)  # Allow realtime margin for thread-pool flush/file deletion while another visit closes.
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    real_impl = rt.outbox._close_impl

    async def slow_impl(delete):
        await stuck.wait()                                    # Outbox cleanup stuck on disk write.
        await real_impl(delete)

    rt.outbox._close_impl = slow_impl
    rt.outbox.leave_done = lambda now: False                  # Channel close misses deadline and is collected.
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 15)
        cleanup = rt.outbox.close_task
        assert cleanup is not None and not cleanup.done()
        assert cleanup in rtm._outbox_cleanups                # Unregistered runtime: module-level registration makes cleanup visible to shutdown.
        assert rt.outbox.path.exists()
        asyncio.get_running_loop().call_later(0.2, stuck.set)  # Disk recovers later.
        await asyncio.wait_for(rtm.stop_all("shutdown"), 10)
        assert cleanup.done() and not cleanup.cancelled()     # Shutdown first awaits completion, without interrupting flush.
        assert not rt.outbox.path.exists()                    # Remove body-containing files immediately.
    finally:
        stuck.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_failed_grant_renewal_backs_off(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    clock, wall = clocks
    calls: list[int] = []

    async def failing():
        calls.append(1)
        raise cr.VisitServersError("upstream_down")           # Temporary Servers error.

    rt.grant.refresh_due = lambda **kw: True
    rt.grant.ensure_fresh = failing
    await rt.tick()
    await settle()
    assert len(calls) == 1
    clock.advance(2)
    await rt.tick()
    await settle()
    assert len(calls) == 1                                    # No resend on next tick within backoff.
    clock.advance(4)
    await rt.tick()
    await settle()
    assert len(calls) == 2                                    # Retry after backoff.
    rt.request_finalize("route_end")
    await _finished(rt)


async def test_a_grant_renewal_finishing_after_the_end_sends_nothing(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    release = asyncio.Event()
    sent: list[int] = []

    async def slow_fresh():
        await release.wait()                                  # Visit enters cleanup while awaiting Servers.
        return True

    rt.grant.refresh_due = lambda **kw: True
    rt.grant.ensure_fresh = slow_fresh
    real_message = rt._credentials_message

    def message(**kw):
        sent.append(1)
        return real_message(**kw)

    rt._credentials_message = message
    await rt.tick()
    await settle()
    rt.request_finalize("route_end")
    release.set()
    await _finished(rt)
    assert sent == []                                         # Stop writing refreshed credentials to pages.


async def test_a_grant_renewal_failing_for_any_reason_backs_off(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    clock, wall = clocks
    calls: list[int] = []

    async def broken():
        calls.append(1)
        raise RuntimeError("socket reset")                    # Failure other than VisitServersError.

    rt.grant.refresh_due = lambda **kw: True
    rt.grant.ensure_fresh = broken
    await rt.tick()
    await settle()
    clock.advance(2)
    await rt.tick()
    await settle()
    assert len(calls) == 1                                    # Apply backoff too.
    rt.request_finalize("route_end")
    await _finished(rt)


async def test_the_journal_never_starts_after_its_earliest_buffered_line(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    rt._on_usage({"llm_input_tokens": 5})                     # Earlier pre-entry usage does not pull start time backward.
    wall.advance(10)
    await rt.record_line("own_cat", side="host", lp=1, ln="h:1", text="开场", truncated=False)
    early = [r for r in rt._journal_backlog if r.get("kind") == "line"][0]["ts"]
    wall.advance(30)                                          # Room-entry report only after 30s.
    await rt.on_transport_state({"state": "joined", "peer_present": False})
    await wait_for(lambda: rt.journal.is_open)
    assert rt.journal._started_at == early                    # Use earliest dialogue, never later than it or pulled earlier by usage.
    rt.request_finalize("route_end")
    await _finished(rt)


async def test_lines_buffered_before_a_join_that_never_comes_are_still_sealed(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    await rt.record_line("own_cat", side="host", lp=1, ln="h:1", text="开场", truncated=False)
    assert rt._journal_opening is None and rt._journal_backlog  # Only in backlog.
    rt.request_finalize("relay_lost")                         # Cleanup without ever receiving room-entry report.
    await _finished(rt)
    assert rt.journal.sealed
    assert [r["text"] for r in rt.journal.lines()] == ["开场"]  # Backfill header during cleanup and seal buffered dialogue normally.


async def test_a_cancelled_ready_handler_still_finishes_the_guest_activation(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    grt = guest.rt
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = grt._open_spool

    async def slow_open(subjects):
        await real_open(subjects)
        reached.set()
        await gate.wait()

    grt._open_spool = slow_open
    try:
        receiving = asyncio.ensure_future(grt.on_recv(        # Receive owner's ready.
            from_vid=HOST_VID, cmd=1, payload={"t": "ready", "v": 1, "seq": grt.sequencer.contiguous_seq + 1},
            nbytes=50))
        await asyncio.wait_for(reached.wait(), 5)
        receiving.cancel()                                    # Receive handler cancelled (connection lost, etc.).
        await asyncio.gather(receiving, return_exceptions=True)
        gate.set()
        await wait_for(lambda: grt.ready_exchanged, timeout=5)  # Activation still finishes and starts the visit.
        assert not grt.finalizing
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_line_arriving_during_a_background_activation_waits_for_it(tmp_path, monkeypatch):
    from main_routers.visit_router import runtime_rx

    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    grt = guest.rt
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = grt._open_spool
    handled: list[dict] = []

    async def slow_open(subjects):
        await real_open(subjects)
        reached.set()
        await gate.wait()

    async def text_handler(rt, m, from_vid, now):
        handled.append(m)

    grt._open_spool = slow_open
    monkeypatch.setitem(runtime_rx._HANDLERS, "text", text_handler)
    try:
        seq = grt.sequencer.contiguous_seq + 1
        receiving = asyncio.ensure_future(grt.on_recv(
            from_vid=HOST_VID, cmd=1, payload={"t": "ready", "v": 1, "seq": seq}, nbytes=50))
        await asyncio.wait_for(reached.wait(), 5)
        receiving.cancel()                                    # Handler accepting ready cancelled; ready flow continues in background.
        await asyncio.gather(receiving, return_exceptions=True)
        dropped = grt.gate_dropped
        media_gate = asyncio.Event()
        real_media = grt.send_media

        async def slow_media():
            await media_gate.wait()                           # Post-activation ready flow: media push, started, opening.
            return await real_media()

        grt.send_media = slow_media
        dispatching = asyncio.ensure_future(grt.on_recv(      # Reconnected socket receives owner's opening dialogue.
            from_vid=HOST_VID, cmd=2, payload={
                "t": "text", "v": 1, "ln": "h:1", "lp": 1, "seq": seq + 1, "sp": "c", "ad": "gc", "rt": "",
                "wu": False, "final": True, "txt": "你好", "truncated": False, "i_done": 0}, nbytes=200))
        await settle()
        assert not dispatching.done()                         # Await ready completion rather than discarding as early.
        gate.set()
        await wait_for(lambda: grt.activated)
        await settle()
        assert not dispatching.done()                         # Keep waiting after activation; ready's later half remains incomplete.
        media_gate.set()
        await asyncio.wait_for(dispatching, 5)
        assert handled and grt.gate_dropped == dropped
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_the_host_does_not_hold_its_receive_loop_for_an_activation(tmp_path, monkeypatch):
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
        await asyncio.wait_for(reached.wait(), 5)             # Owner accepts while activation runs.
        dropped = hrt.gate_dropped
        await asyncio.wait_for(hrt._dispatch({"t": "text"}, GUEST_VID, clock()), 1)  # Do not block receive loop.
        assert hrt.gate_dropped == dropped + 1                # Still discard early owner-side dialogue.
    finally:
        gate.set()
        await asyncio.gather(accepting, return_exceptions=True)
        await teardown(host, guest, wire=wire, clock=clock)


async def test_only_buffered_lines_start_a_journal_at_the_end(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    rt._on_usage({"llm_input_tokens": 5})                     # Only pre-entry usage recorded, no dialogue.
    assert rt._journal_backlog
    rt.request_finalize("relay_lost")
    await _finished(rt)
    assert rt._journal_opening is None and not rt.journal.sealed  # Do not generate a zero-dialogue transcript.


async def test_state_replay_includes_lines_still_buffered_before_the_header(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    try:
        await rt.record_line("own_cat", side="host", lp=1, ln="h:1", text="开场", truncated=False)
        assert not rt.journal.lines() and rt._journal_backlog  # Header unpersisted: this line exists only in backlog.
        replay = rt.snapshot()["transcript"]
        assert [r.get("text") for r in replay] == ["开场"]       # Page reload now still sees it through /state.
    finally:
        rt.request_finalize("route_end")
        await _finished(rt)


async def test_buffered_usage_is_not_stamped_before_the_journal_start(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    rt._on_usage({"llm_input_tokens": 5})                     # Usage earlier than dialogue.
    wall.advance(10)
    await rt.record_line("own_cat", side="host", lp=1, ln="h:1", text="开场", truncated=False)
    await rt.on_transport_state({"state": "joined", "peer_present": False})
    await wait_for(lambda: rt.journal.is_open and not rt._journal_backlog)
    start = rt.journal._started_at
    stamped = [r["ts"] for r in rt.journal._records if r.get("kind") in ("usage", "anomaly")]
    assert stamped and all(ts >= start for ts in stamped)     # Backfilled usage is no earlier than start.
    rt.request_finalize("route_end")
    await _finished(rt)


async def test_an_activation_that_outlives_teardown_is_handed_to_stop_all(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "VISIT_ACTIVATION_ALLOWANCE_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    gate, reached = asyncio.Event(), asyncio.Event()
    real_open = hrt._open_spool

    async def stubborn_open(subjects):
        reached.set()
        while not gate.is_set():
            try:
                await gate.wait()                             # Disk writes ignore cancellation.
            except asyncio.CancelledError:
                continue
        await real_open(subjects)

    hrt._open_spool = stubborn_open
    try:
        accepting = asyncio.ensure_future(hrt.accept(True))
        await asyncio.wait_for(reached.wait(), 5)
        hrt.request_finalize("route_end")
        await asyncio.wait_for(_finished(hrt), 15)            # Cleanup waits its cap, then unregisters normally.
        activation = hrt._activation
        assert activation is not None and not activation.done()
        assert activation in rtm._detached                    # After unregistering, module-level registration lets stop_all collect it.
    finally:
        gate.set()
        await asyncio.gather(accepting, return_exceptions=True)
        await teardown(host, guest, wire=wire, clock=clock)


async def test_an_owed_ack_after_the_local_leave_is_done_is_sent_directly(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt.sequencer.poll_ack = lambda now, force=False: 7     # Just accepted peer leave, owing ack.
        rt.outbox.leave_done = lambda now=None: True          # Local leave completed.
        rt.outbox.due = lambda now: []                        # Queue stops emitting frames.
        await rt.flush()
        assert [p for p in wire.sent["host"] if p.get("t") == "ack" and p.get("seq") == 7]  # Still reaches peer.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_spool_that_fails_to_open_leaves_a_memory_off_state(tmp_path, monkeypatch):
    from main_logic.visit import spool as spool_mod

    async def broken_open(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(spool_mod.VisitSpool, "open", broken_open)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    try:
        assert host.rt.memory_enabled is False
        state = await host.rt.spool.read_state()
        assert state["memory_enabled"] is False               # Restore persisted memory-disabled state so startup recovery does not repeatedly commit.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_cancelled_first_join_still_finishes_its_bookkeeping(tmp_path, monkeypatch, clocks):
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
        await gate.wait()                                     # Header write stuck.
        return await real_open(**kw)

    rt.journal.open = slow_open
    try:
        joining = asyncio.ensure_future(rt.on_transport_state({"state": "joined", "peer_present": False}))
        await asyncio.wait_for(reached.wait(), 5)
        joining.cancel()                                      # Receive handler cancelled by page disconnect.
        await asyncio.gather(joining, return_exceptions=True)
        gate.set()
        await wait_for(lambda: rt._first_join_gen is None)    # Generation still closes, preventing reconnected room-entry reports waiting forever.
        assert rt.phase == "invite_ready"
    finally:
        gate.set()
        rt.request_finalize("route_end")
        await _finished(rt)


async def test_a_reconnect_during_the_first_join_still_has_a_join_deadline(tmp_path, monkeypatch, clocks):
    from config.visit_settings import VISIT_SELF_RECONNECT_S

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
        await gate.wait()                                     # Header write stuck.
        return await real_open(**kw)

    rt.journal.open = slow_open
    try:
        joining = asyncio.ensure_future(rt.on_transport_state({"state": "joined", "peer_present": False}))
        await asyncio.wait_for(reached.wait(), 5)
        rt.on_page_gone()                                     # Page disconnects and reconnects.
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        assert rt._join_deadline is None                      # joined was still True then; capability gate never started entry deadlines.
        gate.set()
        await asyncio.wait_for(joining, 5)                    # Old attempt invalidated by generation; new connection not yet joined.
        assert not rt.joined and rt._join_deadline is not None  # Start the missing entry deadline.
        clock.advance(VISIT_SELF_RECONNECT_S + 1)
        await rt.tick()
        assert rt.finalize_reason == "relay_lost"             # End if reports never arrive; do not wait forever for entry.
    finally:
        gate.set()
        rt.request_finalize("route_end")
        await _finished(rt)


async def test_a_left_report_while_waiting_for_the_guest_ends_after_the_reconnect_window(tmp_path, monkeypatch,
                                                                                         clocks):
    from config.visit_settings import VISIT_SELF_RECONNECT_S

    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    try:
        await rt.on_transport_state({"state": "joined", "peer_present": False})
        assert rt.phase == rtm.PHASE_INVITE_READY
        await rt.on_transport_state({"state": "left", "peer_present": False})   # iframe leaves room without closing connection.
        clock.advance(VISIT_SELF_RECONNECT_S + 1)
        await rt.tick()
        assert rt.finalize_reason == "relay_lost"             # Do not stay invite_ready for the whole invitation lifetime.
    finally:
        rt.request_finalize("route_end")
        await _finished(rt)


async def test_a_left_report_during_the_visit_ends_after_the_reconnect_window(tmp_path, monkeypatch):
    from config.visit_settings import VISIT_SELF_RECONNECT_S

    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await rt.on_transport_state({"state": "left", "peer_present": True})
        assert rt.outbox.paused                               # Pause outbound traffic as on reconnect.
        clock.advance(VISIT_SELF_RECONNECT_S + 1)
        await rt.tick()
        assert rt.finalize_reason == "relay_lost"             # Do not wait for peer heartbeat death detection.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_failed_memory_off_correction_is_written_with_finalized(tmp_path, monkeypatch):
    from main_logic.visit import spool as spool_mod

    async def broken_open(self, *args, **kwargs):
        raise OSError("disk full")

    real_update = spool_mod.VisitSpool.update_state
    failed: list[int] = []

    async def flaky_update(self, **changes):
        if changes == {"memory_enabled": False} and not failed:
            failed.append(1)
            raise OSError("disk full")                        # Memory-disabled restoration also failed to persist.
        return await real_update(self, **changes)

    monkeypatch.setattr(spool_mod.VisitSpool, "open", broken_open)
    monkeypatch.setattr(spool_mod.VisitSpool, "update_state", flaky_update)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    try:
        assert failed and (await host.rt.spool.read_state())["memory_enabled"] is True
        host.rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(host.rt), 15)
        state = await host.rt.spool.read_state()
        assert state["finalized"] and state["memory_enabled"] is False  # Restore memory disabled when writing finalized too.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_runtime_tasks_surviving_shutdown_cancellation_are_handed_to_stop_all(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = asyncio.Event()

    async def stubborn():
        while not release.is_set():                           # Stuck in a cancellation-ignoring dependency.
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    task = rt.spawn(stubborn())
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 10)
        assert not task.done() and task in rtm._detached      # Module-level registration collects it after unregistration; nothing abandoned.
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await teardown(host, guest, wire=wire, clock=clock)


@pytest.mark.parametrize("attr", ["_exit_task", "_creds_task", "_activation", "_closing_task"])
async def test_lifecycle_tasks_surviving_shutdown_cancellation_are_handed_to_stop_all(tmp_path, monkeypatch, attr):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = asyncio.Event()

    async def stubborn():
        while not release.is_set():                           # Stuck in a cancellation-ignoring dependency.
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    task = asyncio.ensure_future(stubborn())
    setattr(rt, attr, task)
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 15)
        assert not task.done() and task in rtm._detached      # Module-level registration collects it after unregistration; nothing abandoned.
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await teardown(host, guest, wire=wire, clock=clock)


async def test_ended_waits_behind_a_display_write_that_never_retired(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "_DISPLAY_FLUSH_S", 0.2)
    monkeypatch.setattr(rtm, "_ENDED_AFTER_DISPLAY_S", 10.0)  # Background queuing waits sufficiently long; write exits before deadline.
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = asyncio.Event()
    order: list[str] = []
    real_send = host.host.send_frame

    async def send_frame(frame):
        if frame.get("type") == "visit_state_change" and frame.get("action") == "ended":
            order.append("ended")
            return await real_send(frame)
        if frame.get("type") == "visit_line" and frame.get("text") == "卡住的一行":
            while not release.is_set():                       # Page write remains blocked and ignores cancellation.
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            order.append("stuck_line")
        return await real_send(frame)

    host.host.send_frame = send_frame
    try:
        rt._post_display({"type": "visit_line", "text": "卡住的一行"})
        await settle()
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 15)
        assert "ended" not in order                           # The write persists: ended neither races nor overtakes it.
        release.set()
        await wait_for(lambda: "ended" in order)
        assert order == ["stuck_line", "ended"]               # Send afterward.
    finally:
        release.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_ended_is_still_sent_when_the_display_write_never_retires(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "_DISPLAY_FLUSH_S", 0.2)
    monkeypatch.setattr(rtm, "_ENDED_AFTER_DISPLAY_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = asyncio.Event()
    order: list[str] = []
    real_send, real_status = host.host.send_frame, host.host.send_status

    async def send_frame(frame):
        if frame.get("type") == "visit_state_change" and frame.get("action") == "ended":
            order.append("ended")
        elif frame.get("type") == "visit_line" and frame.get("text") == "卡住的一行":
            while not release.is_set():                       # Remains stuck, ignoring cancellation.
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
        return await real_send(frame)

    async def send_status(code, details):
        order.append(f"status:{code}")
        return await real_status(code, details)

    host.host.send_frame = send_frame
    host.host.send_status = send_status
    try:
        rt._post_display({"type": "visit_line", "text": "卡住的一行"})
        await settle()
        rt.request_finalize("kicked")                         # Ending reason includes status VISIT_KICKED.
        await asyncio.wait_for(_finished(rt), 15)
        await wait_for(lambda: "ended" in order and "status:VISIT_KICKED" in order, timeout=5)  # Still attempt delivery at deadline.
        assert order.index("ended") < order.index("status:VISIT_KICKED")  # status follows ended.
    finally:
        release.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_an_unpaired_room_is_cancelled_even_if_the_channel_close_is_cut_off(tmp_path, monkeypatch, clocks):
    monkeypatch.setattr(rtm, "_CLOSE_WAIT_S", 0.2)
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    rt.peer_present = True                                    # Guest in room, hello never verified.
    rt.outbox.leave_done = lambda now=None: False            # leave recovery window blocked; cancel close task at deadline.
    rt.request_finalize("route_end")
    await asyncio.wait_for(_finished(rt), 15)
    await settle()
    assert [c[0] for c in side.cancelled] == [rt.visit_id]    # Still revoke invite-code/quota occupancy.


async def test_stop_all_retires_transport_close_tasks(tmp_path, monkeypatch):
    from main_routers.visit_router import transport_ws as tw

    release = asyncio.Event()

    async def stuck_close():
        while not release.is_set():                       # Backpressure waits for send lock and close frame.
            try:
                await release.wait()
            except asyncio.CancelledError:
                release.set()
                raise

    task = asyncio.ensure_future(stuck_close())
    tw._close_tasks.add(task)
    task.add_done_callback(tw._close_tasks.discard)
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 10)
        assert task.cancelled()                           # Shutdown cancels and bounded-waits all, leaving nothing for loop destruction.
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_the_pump_keeps_sending_after_shutdown_marks_the_visit_terminated(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt._terminated = True                                 # Set on shutdown's first step; later steps still need the pump to send.
        rt.kick()
        await settle()
        await settle()                                        # Pump ran at least once more.
        rt.outbox.send({"t": "typing", "lp": 1, "sp": "c"}, now=clock())
        rt.kick()
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "typing"])  # Send normally.
        assert not rt._pump_task.done()
    finally:
        rt._terminated = False
        await teardown(host, guest, wire=wire, clock=clock)


async def test_an_overrun_channel_close_is_handed_to_stop_all(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "_CLOSE_WAIT_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release, pump_stuck = asyncio.Event(), asyncio.Event()
    rt.outbox.leave_done = lambda now=None: False            # Channel close still unfinished at deadline.

    async def stubborn_send(*args, **kwargs):
        if asyncio.current_task() is rt._pump_task:
            pump_stuck.set()
        while not release.is_set():                           # Cancellation-ignoring write.
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return True

    try:
        rt.transport.send = stubborn_send
        rt.kick()
        await asyncio.wait_for(pump_stuck.wait(), 5)          # Prove the send pump actually blocked in this write.
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 15)
        closing = rt._closing_task
        assert closing is not None and not closing.done()
        assert closing in rtm._detached                       # After unregistering, module-level registration lets stop_all collect it.
        pump = rt._pump_task                                  # Same for pumps blocked in the same write.
        assert pump is not None and not pump.done() and pump in rtm._detached
        release.set()
        await wait_for(lambda: pump.done())                   # After release the pump stops itself, without continuing on unregistered runtimes.
    finally:
        release.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_channel_close_that_overruns_is_retired_before_sealing(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "_CLOSE_WAIT_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    order: list[str] = []
    real_unregister = rtm.unregister_transport_session

    def unregister(transport):
        order.append("unregister")
        return real_unregister(transport)

    monkeypatch.setattr(rtm, "unregister_transport_session", unregister)
    real_seal = rt.journal.seal

    async def seal(reason, **kw):
        order.append("seal")
        return await real_seal(reason, **kw)

    rt.journal.seal = seal
    rt.outbox.leave_done = lambda now: False                  # Peer never acks leave; channel close still unfinished at deadline.
    try:
        rt.request_finalize("route_end")
        await asyncio.sleep(0.1)
        real_send = rt.transport.send

        async def stalled_send(*args, **kwargs):
            await asyncio.sleep(10)                           # Close task's finally send also blocks.
            return await real_send(*args, **kwargs)

        rt.transport.send = stalled_send
        await asyncio.wait_for(_finished(rt), 10)
        assert "unregister" in order and "seal" in order
        assert order.index("unregister") < order.index("seal")  # Close receive channels before sealing.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_lines_are_kept_in_memory_when_the_upload_header_fails(tmp_path, monkeypatch):
    from main_routers.visit_router import transcript_upload

    async def broken_open(self, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(transcript_upload.UploadJournal, "open", broken_open)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        assert not rt.journal.is_open
        await wait_for(lambda: len(rt.journal.lines()) >= 1, timeout=5)   # In-memory transcript remains normal for /state replay and overviews.
        assert rt.snapshot()["transcript"]
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_phase_frames_keep_their_order_with_queued_lines(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    real_send = host.host.send_frame

    async def send_frame(payload):
        if payload.get("line_id") == "slow":
            await asyncio.sleep(0.3)                          # Page backpressure.
        return await real_send(payload)

    host.host.send_frame = send_frame
    try:
        before = len(host.host.frames)
        rt._post_display({"type": "visit_line", "visit_id": rt.visit_id, "line_id": "slow"})
        await rt.push("ending_soon", ends_at=1.0)
        await wait_for(lambda: [f for f in host.host.frames[before:] if f.get("action") == "ending_soon"], timeout=5)
        frames = host.host.frames[before:]
        line = [i for i, f in enumerate(frames) if f.get("line_id") == "slow"]
        phase = [i for i, f in enumerate(frames) if f.get("action") == "ending_soon"]
        assert line and phase and line[0] < phase[0]          # Phase frames do not overtake preceding whole sentences.
    finally:
        host.host.send_frame = real_send
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_retired_channel_close_still_sends_its_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "_CLOSE_WAIT_S", 0.2)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    rt.outbox.leave_done = lambda now: False                  # Peer never acks leave.
    order: list[str] = []
    real_unregister = rtm.unregister_transport_session

    def unregister(transport):
        if transport is rt.transport:                         # Record only local side; guest unregisters itself too.
            order.append("unregister")
        return real_unregister(transport)

    monkeypatch.setattr(rtm, "unregister_transport_session", unregister)
    real_send = rt.transport.send

    async def send(msg, *args, **kwargs):
        if isinstance(msg, dict) and msg.get("type") == "stop":
            order.append("stop:" + str(msg.get("reason")))
        return await real_send(msg, *args, **kwargs)

    rt.transport.send = send
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        assert "stop:route_end" in order and "unregister" in order
        assert order.index("stop:route_end") < order.index("unregister")   # Finish sending stop{reason} before unregistering.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_backlog_lines_survive_a_header_that_fails_late(tmp_path, monkeypatch, clocks):
    monkeypatch.setattr(rtm, "_JOURNAL_OPEN_MAX_S", 0.1)
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    gate = asyncio.Event()

    async def slow_then_broken(**kw):
        await gate.wait()
        raise OSError("disk full")                            # Slow write eventually fails.

    rt.journal.open = slow_then_broken
    try:
        await rt.on_transport_state({"state": "joined", "peer_present": False})
        await rt.record_line("own_human", side="host", lp=3, ln="h:3", text="开头那一句", truncated=False)
        assert rt._journal_backlog                            # Entered backlog first.
        gate.set()
        await wait_for(lambda: [r for r in rt.journal.lines() if r["text"] == "开头那一句"], timeout=5)
        assert not rt._journal_backlog                        # Backlogged lines enter in-memory transcripts.
    finally:
        gate.set()
        await teardown(side, clock=clock)


async def test_the_invite_frame_keeps_its_order_in_the_display_queue(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    real_send = side.host.send_frame

    async def send_frame(payload):
        if payload.get("line_id") == "slow":
            await asyncio.sleep(0.3)                          # Page backpressure.
        return await real_send(payload)

    side.host.send_frame = send_frame
    try:
        await rt.on_transport_state({"state": "connected", "peer_present": True})
        before = len(side.host.frames)
        rt._post_display({"type": "visit_state_change", "visit_id": rt.visit_id, "line_id": "slow"})
        await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload=_guest_hello(), nbytes=900)
        await wait_for(lambda: side.host.frames_of("visit_invite"), timeout=5)
        frames = side.host.frames[before:]
        slow = [i for i, f in enumerate(frames) if f.get("line_id") == "slow"]
        invite = [i for i, f in enumerate(frames) if f.get("type") == "visit_invite"]
        assert slow and invite and slow[0] < invite[0]        # Invite frames cannot overtake earlier frames.
    finally:
        side.host.send_frame = real_send
        await teardown(side, wire=wire, clock=clocks[0])


async def test_a_channel_close_cancelled_mid_send_still_unregisters(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    unregistered = []
    real_unregister = rtm.unregister_transport_session

    def unregister(transport):
        if transport is rt.transport:
            unregistered.append(True)
        return real_unregister(transport)

    monkeypatch.setattr(rtm, "unregister_transport_session", unregister)
    real_send = rt.transport.send
    in_stop = asyncio.Event()

    async def send(msg, *args, **kwargs):
        if isinstance(msg, dict) and msg.get("type") == "stop":
            in_stop.set()
            await stuck.wait()                                # Page backpressure while finally sends stop.
        return await real_send(msg, *args, **kwargs)

    rt.transport.send = send
    try:
        rt.outbox.leave_done = lambda now: True
        closing = asyncio.ensure_future(rt._close_channel("route_end"))
        await asyncio.wait_for(in_stop.wait(), 5)
        closing.cancel()                                      # Cancellation lands precisely during finally's send.
        await asyncio.wait([closing], timeout=2)
        assert closing.done() and unregistered                # Inner finally still unregisters.
    finally:
        stuck.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_lines_buffered_before_a_shutdown_reach_the_stream_left_for_recovery(tmp_path, monkeypatch):
    import threading

    from main_routers.visit_router import transcript_upload

    monkeypatch.setattr(rtm, "_JOURNAL_OPEN_MAX_S", 0.1)
    release = threading.Event()
    real_open_sync = transcript_upload.UploadJournal._open_sync

    def slow_open_sync(self, data):
        release.wait(10)                                      # Header writing blocks in the writer thread.
        return real_open_sync(self, data)

    monkeypatch.setattr(transcript_upload.UploadJournal, "_open_sync", slow_open_sync)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await wait_for(lambda: any(r.get("kind") == "line" for r in rt._journal_backlog), timeout=5)
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)   # Shutdown abandons header waits.
        release.set()                                         # Later the writer creates the stream and appends buffered lines.
        stream = host.config_dir / "visit_spool" / f"{rt.visit_id}.upload.jsonl"
        for _ in range(50):
            if stream.exists() and '"kind":"line"' in stream.read_text(encoding="utf-8").replace(" ", ""):
                break
            await asyncio.sleep(0.05)
        kinds = [json.loads(line)["kind"] for line in stream.read_text(encoding="utf-8").splitlines()]
        assert kinds[0] == "header" and "line" in kinds        # Next-startup recovery can retrieve these lines.
    finally:
        release.set()
        await asyncio.sleep(0.1)
        transcript_upload._open_streams.discard(rt.visit_id)
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_bad_buffered_record_does_not_break_the_shutdown(tmp_path, monkeypatch):
    host, guest, wire, clock, gate = await _bring_up_with_a_pending_header(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt._journal_backlog.append({"kind": "line", "lp": "not-a-number", "side": "host", "speaker": "own_cat",
                                    "ts": None, "text": "坏记录", "truncated": False})
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)
        assert rt.journal._open_abandoned                     # Conversion failures also abandon waiting.
        assert rt._journal_opening.done()                     # And cancel header writes.
        assert rtm.get_runtime("Host") is None                # All later shutdown steps reached.
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_retires_the_transport_before_sealing(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    order: list[str] = []
    real_unregister = rtm.unregister_transport_session

    def unregister(transport):
        if transport is rt.transport:
            order.append("unregister")
        return real_unregister(transport)

    monkeypatch.setattr(rtm, "unregister_transport_session", unregister)
    real_seal = rt.journal.seal

    async def seal(reason, **kw):
        order.append("seal")
        return await real_seal(reason, **kw)

    rt.journal.seal = seal
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)
        assert "unregister" in order and "seal" in order
        assert order.index("unregister") < order.index("seal")  # Stop accepting peer frames before sealing.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_the_spool_is_finalized_only_once(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        assert rt._spool_finalized
        await rt._finalize_spool("shutdown")                  # Background chain /late branch calls again.
        state = await rt.spool.read_state()
        assert state["finalized"] == "route_end"              # No second rewrite.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_failed_takeover_release_is_retried_by_teardown(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    real_release = host.host.release_takeover
    calls = []

    def release_takeover(token):
        calls.append(token)
        if len(calls) == 1:
            raise RuntimeError("release failed")              # First cleanup release fails.
        return real_release(token)

    host.host.release_takeover = release_takeover
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        assert len(calls) == 2 and host.host.released == [calls[0]]   # teardown retries successfully with the same token.
    finally:
        host.host.release_takeover = real_release
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
    assert rtm.is_visit_route_locked("Host")             # Reservation locks immediately, even before runtime registration.
    with pytest.raises(rtm.VisitRefused) as refused:
        await rtm.start_visit("Host", "host", crop="upper", invite_code=None, visit_id="Y" * 22,
                              host=side.host, deps=side.deps, clock=clock, wall=wall)
    assert refused.value.status == 409 and refused.value.body["reason"] == "already_visiting"
    assert visit_route_state.get_visit_route_state("Host") is slot   # First visit's reservation remains intact.
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
    assert rtm.VisitRuntime._codec(nxt) == "vp8"           # Reuse for this visit's renewal/reconnection.
    later = SimpleNamespace(creds=livekit, _livekit_codec=None)
    assert rtm.VisitRuntime._codec(later) == "vp9"
    trtc = SimpleNamespace(creds=SimpleNamespace(transport="trtc"), _livekit_codec=None)
    monkeypatch.setattr(rtm, "_vp8_next_visit", True)
    assert rtm.VisitRuntime._codec(trtc) == "h264" and rtm._vp8_next_visit is True   # TRTC consumes none.


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
        # Synchronous activation in receive loops must have a bound even if stuck (outer 3s only prevents test hangs).
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
    asyncio.get_running_loop().call_later(0.1, gate.set)      # Finish header before sealing.
    await _finished(rt)
    await asyncio.gather(joining)
    assert rt.journal.sealed                                   # Leave no unsealed .upload.jsonl.
    assert not list((side.config_dir / "visit_spool").glob("*.upload.jsonl"))


async def test_a_joined_report_before_credentials_does_not_count_as_entering(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # Credentials not issued yet.
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
        side.host.precondition = "voice_session_active"       # Voice session starts during account lookup.
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
    asyncio.get_running_loop().call_later(0.9, gate.set)      # Longer than shutdown-budget waits.
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
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # Capability gate ③ unreported.
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
        clock.advance(40)                                    # Public-key verification takes 40s.
        return await real()

    side.deps.fetch_pubkeys = slow_pubkeys
    try:
        await rt.on_transport_state({"state": "connected", "peer_present": True})
        await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload=_guest_hello(), nbytes=900)
        assert rt.phase == "awaiting_accept"
        assert rt._accept_deadline - clock() > VISIT_ACCEPT_TIMEOUT_S - 1   # Relative retains full reception time.
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
        await start_side(two, invite_code=INVITE, clock=clock, wall=wall)   # Same invitation and visit_id.
    assert refused.value.body["reason"] == "visit_in_progress"
    assert visit_route_state.get_visit_route_state("Nana") is None
    gate.set()
    rt = await first
    try:
        assert rtm.get_runtime_by_visit(rt.visit_id) is rt
    finally:
        await teardown(one, clock=clock)


async def test_the_other_side_of_a_running_visit_is_refused_on_the_same_machine(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    host = make_side(tmp_path, "host", clock=clock, wall=wall, name="Mimi")
    guest = make_side(tmp_path, "guest", clock=clock, wall=wall, name="Nana")
    guest.deps.config_dir = host.deps.config_dir              # Same config directory, as in production's single process.
    rt = await start_side(host, clock=clock, wall=wall)
    try:
        with pytest.raises(rtm.VisitRefused) as refused:      # Redeem this machine's invite after switching community accounts.
            await start_side(guest, invite_code=INVITE, clock=clock, wall=wall)
        assert refused.value.body["details"]["reason"] == "self_invite"  # Same notice as Servers rejection, preserving visit files.
        assert visit_route_state.get_visit_route_state("Nana") is None
        assert rtm.get_runtime_by_visit(rt.visit_id) is rt
    finally:
        await teardown(host, clock=clock)


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
        await rtm.stop_all("shutdown")                        # Account lookup still pending at shutdown.
        gate.set()
        with pytest.raises(rtm.VisitRefused):
            await asyncio.wait_for(starting, 5)
        assert rtm.get_runtime("Host") is None                # No runtime registration after shutdown.
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
        monkeypatch.setattr(rtm, "_stopping", True)           # New entry during stop_all.
        with pytest.raises(rtm.VisitRefused) as refused:
            await start_side(side, clock=clock, wall=wall)
        assert refused.value.body["reason"] == "shutdown"
        monkeypatch.setattr(rtm, "_stopping", False)
        monkeypatch.setattr(rtm, "_local_account", slow_account)
        starting = asyncio.ensure_future(start_side(side, clock=clock, wall=wall))
        await asyncio.wait_for(reached.wait(), 5)
        monkeypatch.setattr(rtm, "_stopping", True)           # stop_all starts during account lookup; reject even before generation changes.
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
            await real_close(session)
            return
        while not stuck.is_set():                             # Cancellation drain blocks, swallowing cancellation and waiting.
            try:
                await stuck.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setattr(session_pool, "close_visit_session", close_visit_session)
    try:
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)
        assert rtm.get_runtime("Host") is None                # Still unregister so the character does not remain locked.
        assert [t for t in rtm._detached if not t.done()]     # Continue unfinished closure in background.
    finally:
        stuck.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_an_admission_that_wakes_after_stop_all_finished_does_not_register(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    first = make_side(tmp_path / "a", "host", clock=clock, wall=wall)
    rt = await start_side(first, clock=clock, wall=wall)
    held = asyncio.Event()
    real_shutdown = rt.shutdown

    async def slow_shutdown():
        await held.wait()
        await real_shutdown()

    rt.shutdown = slow_shutdown
    gate, reached = asyncio.Event(), asyncio.Event()

    async def slow_account():
        reached.set()
        await gate.wait()
        return "acct"

    second = make_side(tmp_path / "b", "guest", clock=clock, wall=wall, name="Nana")
    stopping = asyncio.ensure_future(rtm.stop_all("shutdown"))
    try:
        await wait_for(lambda: rtm._stopping)
        monkeypatch.setattr(rtm, "_local_account", slow_account)
        starting = asyncio.ensure_future(start_side(second, invite_code=INVITE, clock=clock, wall=wall))   # Starts only during stop_all.
        await asyncio.wait_for(reached.wait(), 5)
        held.set()
        await asyncio.wait_for(stopping, 5)                   # stop_all ends first.
        assert not rtm._stopping
        gate.set()                                            # Account lookup completes only afterward.
        with pytest.raises(rtm.VisitRefused):
            await asyncio.wait_for(starting, 5)
        assert rtm.get_runtime("Nana") is None
    finally:
        held.set()
        gate.set()
        await teardown(first, clock=clock)
        await teardown(second, clock=clock)


async def test_the_isolated_session_is_closed_once_even_when_teardown_is_cancelled(tmp_path, monkeypatch):
    from main_routers.visit_router import session_pool

    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    release = asyncio.Event()
    closes = []

    async def close_visit_session(session):
        closes.append(session)
        while not release.is_set():                           # Cannot close, ignores cancellation.
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setattr(session_pool, "close_visit_session", close_visit_session)
    try:
        caller = asyncio.ensure_future(rt.close_session(5))
        await asyncio.sleep(0.05)
        caller.cancel()                                       # Shutdown cancels cleanup task.
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert rt._session_closing in rtm._detached           # Move unfinished closure to background.
        await rt.close_session(0.05)                          # Shutdown closes again by awaiting the same task.
        assert len(closes) == 1
    finally:
        release.set()
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
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # Report only once.
        assert rt.joined is False
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        assert rt.joined is True and rt.phase == "invite_ready"                  # Backfill initial entry as soon as capability gates pass.
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
        await rt.on_transport_state({"state": "reconnecting"})                    # Disconnect again before capability gate.
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        assert rt.joined is False and rt.phase != "invite_ready"
        assert rt.liveness.self_disconnected_at is not None                       # Retain disconnect timing.
    finally:
        await teardown(side, clock=clock)



def test_a_second_runtime_of_the_same_visit_does_not_take_over_its_registration():
    from types import SimpleNamespace

    first = SimpleNamespace(lanlan_name="Mimi", visit_id="V" * 22, character_uid="")
    second = SimpleNamespace(lanlan_name="Nana", visit_id="V" * 22, character_uid="")
    rtm._register(first)
    # Another local character redeems this machine's invite before Servers self_invite rejection; do not replace the ongoing visit.
    rtm._register(second)
    assert rtm.get_runtime_by_visit("V" * 22) is first
    rtm._unregister(second)
    assert rtm.get_runtime_by_visit("V" * 22) is first and rtm.is_visit_live("V" * 22)
    # Reverse case: first registered runtime ends, while another remains and continues representing this visit.
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
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # Record and await capability gate.
        rt.transport.on_page_lost(clock())                                         # Connection disappears without reporting disconnect.
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
        clock.advance(20)                                   # Slow disk: upload-header write takes 20s.
        await real_open(**kw)

    rt.journal.open = slow_open
    try:
        await rt.on_transport_state({"state": "joined", "peer_present": True})
        assert rt.phase == "joining"
        assert rt.liveness.wait_deadline - clock() > VISIT_PEER_LOST_S - 1   # Still preserve the full peer-wait duration.
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
        rt.transport.on_page_attached(clock())               # New connection replaces old; old no longer reports page_lost.
        assert rt._sdk_deadline is None and rt._join_deadline is None and rt._preflight_deadline is None
        clock.advance(21)
        await rt.tick()                                       # Old connection's 20s capability deadline cannot end reloading visits.
        assert rt.finalize_reason is None
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        assert rt.joined is False                             # Ignore old connection entry reports.
    finally:
        await teardown(side, clock=clock)


async def test_a_manager_replaced_during_the_account_lookup_is_refused(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)

    async def account_while_replaced():
        side.host.current = False                             # Manager replaced during account lookup.
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
        await stuck.wait()                                    # Page never consumes; send_user_activity waits indefinitely.

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
        await gate.wait()                                   # Main-chat round still ending.
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
    assert side.creds_calls == []                            # One-use invitation remains unredeemed.
    assert side.host.takeovers == []


async def test_credential_setup_that_raises_ends_the_visit(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "guest", clock=clock, wall=wall)

    async def broken_interrupt(timeout):
        raise RuntimeError("adapter")                       # Unexpected adapter-contract failure interrupting main chat.

    side.host.interrupt_main_turn = broken_interrupt
    rt = await start_side(side, invite_code=INVITE, clock=clock, wall=wall)
    Wire().attach(rt, None, GUEST_VID)
    await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
    assert await rt.issue_credentials() is None             # Do not propagate into transport callbacks.
    assert rt.finalize_reason == "busy"                     # Clean up/release routing instead of remaining pending.
    await _finished(rt)
    assert side.creds_calls == []


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
        rt.transport.on_page_attached(clock())               # New connection replaces it during header writing.
        gate.set()
        await asyncio.gather(joining)
        assert rt.joined is False and rt.phase != "invite_ready"
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # New connection reports independently.
        assert rt.joined is True and rt.phase == "invite_ready" and rt.journal.is_open
        assert len(opens) == 1                                # Write header once; new connection reuses it.
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
        await through_gate(rt)                                 # First connection passed capability gate ③ but disconnected before entry.
        rt.transport.on_page_lost(clock())
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # New connection reports entry first.
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
        rt.transport.on_page_attached(clock())                # Old connection superseded.
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # New connection reports only once.
        gate.set()
        await asyncio.gather(joining)
        await wait_for(lambda: rt.joined and rt.phase == "invite_ready")         # After old attempt ends, backfill new connection entry.
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
        rt.transport.on_page_attached(clock())                # Old connection superseded.
        await rt.on_transport_state({"state": "joined", "peer_present": False})   # New connection reports entry first.
        await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": True, "codecs": []})  # Pass capability gate again.
        gate.set()                                            # Old header write completes only now.
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
        rt.transport.on_page_attached(clock())                # Page replaced while awaiting credentials.
        side.creds_gate.set()
        assert await issuing is None                          # Old connection receives none and cannot start new connection's capability deadline.
        assert rt._sdk_deadline is None
        assert await rt.issue_credentials() is not None       # New connection requests its own.
        assert rt._sdk_deadline is not None
    finally:
        side.creds_gate.set()
        await teardown(side, clock=clock)


async def test_no_new_exit_flow_once_shutdown_started(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    stuck = asyncio.Event()
    real_seal = rt.journal.seal

    async def slow_seal(*args, **kwargs):
        await stuck.wait()                                    # During shutdown sealing.
        return await real_seal(*args, **kwargs)

    rt.journal.seal = slow_seal
    try:
        stopping = asyncio.ensure_future(rtm.stop_all("shutdown"))
        await wait_for(lambda: rt._shutdown_started)
        assert rt.request_finalize("peer_left") is False      # Peer leave/route_end starts no additional cleanup.
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
        await stuck.wait()                                    # Local accounting blocked.

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
            raise OSError("visit_accounts.json locked")   # First four writes fail, exceeding the backoff-table length.

    side.deps.record_account = flaky
    rt = await start_side(side, clock=clock, wall=wall)
    Wire().attach(rt, None, HOST_VID)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await rt.issue_credentials() is not None    # Visit proceeds normally; issued credentials are not invalidated for local accounting.
        await wait_for(lambda: len(attempts) == 5)          # After table exhaustion, repeat the final interval until persistence succeeds.
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
        await gate.wait()                                     # Page write blocked.
        return await real_media()

    hrt.send_media = slow_media
    try:
        accepting = asyncio.ensure_future(hrt.accept(True))
        await asyncio.wait_for(reached.wait(), 5)
        hrt.request_finalize("route_end")                     # Visit ends meanwhile.
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
        await gate.wait()                                     # Disk stuck.
        await real_open(**kw)

    rt.journal.open = stalled_open
    try:
        assert rt._join_deadline is not None
        # Receive handlers do not hang; room-entry deadline remains through header writing.
        await asyncio.wait_for(rt.on_transport_state({"state": "joined", "peer_present": False}), 3)
        rt.request_finalize("route_end")
        await asyncio.wait_for(_finished(rt), 10)             # Cleanup also avoids unbounded header waits.
        wall.advance(3600)                                    # Disk stuck for an hour.
        gate.set()                                            # Header finally persists late.
        await wait_for(lambda: rt.journal.sealed)             # Seal immediately, leaving no unsealed stream.
        await wait_for(lambda: not list((side.config_dir / "visit_spool").glob("*.upload.jsonl")))
        doc = json.loads((side.config_dir / "visit_spool" / f"{rt.visit_id}.upload.json").read_text(encoding="utf-8"))
        assert doc["request"]["usage"]["duration_s"] < 600    # Duration ends at cleanup start, excluding disk delays.
    finally:
        gate.set()
        await teardown(side, clock=clock)


async def test_a_page_that_comes_back_too_late_is_page_lost_not_unsupported(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt.transport.on_page_lost(clock())                    # Page reload.
        clock.advance(19)
        rt.transport.on_page_attached(clock())                # Reconnect at second 19.
        assert await rt.issue_credentials() is not None
        page_deadline = rt.liveness.page_reload_deadline()
        assert rt._sdk_deadline is not None and page_deadline is not None
        assert abs(rt._sdk_deadline - page_deadline) < 1e-6   # Capability wait clamps to remaining absolute deadline; both expire together.
        clock.advance(page_deadline - clock() + 0.01)         # SDK never reports (design example loads at second 31).
        await rt.tick()
        assert rt.finalize_reason == "local_page_lost"        # Does not mean local visits unsupported.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_reliable_peer_messages_during_finalization_are_still_acked_and_recorded(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await wait_for(lambda: len(rt.journal.lines()) >= 1)
        stuck = asyncio.Event()

        async def slow_close(*args, **kwargs):
            await stuck.wait()                                # Cleanup pauses before data-channel closure.

        rt.close_current_line = slow_close
        rt.request_finalize("route_end")
        seq = rt.sequencer.contiguous_seq + 1
        before = len(host.host.frames)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:90", "lp": rt.room.max_lp_seen + 1, "seq": seq, "sp": "c", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "临走前的最后一句", "truncated": False, "i_done": 0,
        }, nbytes=200)
        assert rt.sequencer.contiguous_seq == seq             # Advance seq and ack so peer leave can be acknowledged.
        assert [r for r in rt.journal.lines() if r["text"] == "临走前的最后一句"]   # Enter local transcript.
        await settle()
        assert not [f for f in host.host.frames[before:] if f.get("type") == "visit_line"]   # Do not display.
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
            "rt": "", "wu": False}, nbytes=200)                # Already overspeed at fragment stage.
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
        assert rt.sequencer.contiguous_seq == seq             # Still advance seq and ack.
        assert len(text_calls) == 1                           # Reuse fragment-stage quota decision.
        assert not [r for r in rt.journal.lines() if r["text"] == "超速的整句"]
        monkeypatch.setattr(rt.limiter, "admit", real_admit)  # Next line passes quota; only reception gate applies.
        rt.activated = False                                  # Cleanup before reception also rejects backfilled dialogue.
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
        assert "合法的最后一句" in texts                         # Valid lines still enter transcripts.
        replay = [r for r in rt.snapshot()["transcript"] if r.get("line_id") == "g:93"]
        assert replay and replay[0]["addressee"] == {"side": "host", "kind": "cat"}   # Replay full frame shape.
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
            "rt": "", "wu": False}, nbytes=200)                # Starts as catgirl line.
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:97", "i": 0, "lp": lp + 1, "txt": "猫", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)                # Starts without farewell status.
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:98", "i": 0, "lp": lp + 2, "txt": "半截", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)                # Partial bubble opens on page.
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:99", "i": 0, "lp": lp + 3, "txt": "又一行", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:100", "i": 0, "lp": lp + 4, "txt": "晚了", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)
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
        assert not [r for r in rt.journal.lines() if r["text"] == "收口改成人类说的"]   # Uncertain speaker: exclude from transcript.
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:97", "lp": lp + 1, "seq": seq + 1, "sp": "c", "ad": "hc",
            "rt": "", "wu": True, "final": True, "txt": "收口改成告别", "truncated": False, "i_done": 1,
        }, nbytes=200)
        assert rt.sequencer.contiguous_seq == seq + 1
        assert not [r for r in rt.journal.lines() if r["text"] == "收口改成告别"]   # Same criterion as room, including wu.
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:98", "lp": lp + 2, "seq": seq + 2, "sp": "c", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "半截补成整句", "truncated": False, "i_done": 1,
        }, nbytes=200)
        assert [r for r in rt.journal.lines() if r["text"] == "半截补成整句"]
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:99", "lp": lp + 2, "seq": seq + 3, "sp": "c", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "占了别人的 lp", "truncated": False, "i_done": 1,
        }, nbytes=200)                                        # Finalization lp differs from start: invalidate whole line.
        rt._ended_published = True                            # Ended already sent.
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:100", "lp": lp + 4, "seq": seq + 4, "sp": "c", "ad": "hc",
            "rt": "", "wu": False, "final": True, "txt": "已结束之后补到", "truncated": False, "i_done": 1,
        }, nbytes=200)
        assert [r for r in rt.journal.lines() if r["text"] == "已结束之后补到"]   # Still enter transcript.
        await settle()
        frames = host.host.frames[before:]
        aborted = {f.get("line_id") for f in frames if f.get("type") == "visit_line_abort"}
        assert {"g:96", "g:97", "g:99"} <= aborted             # Rejected line: remove partial bubble.
        assert [f for f in frames if f.get("type") == "visit_line" and f.get("line_id") == "g:98"]   # Finalize full sentence.
        assert not [f for f in frames if f.get("line_id") == "g:100"
                    and f.get("type") in ("visit_line", "visit_line_abort")]   # No later page completion after ended.
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
        rt._count_anomaly("test", streak=False)               # Header writing: buffer first.
        assert rt._journal_backlog
        gate.set()                                            # Finishes within 10s.
        await asyncio.wait_for(joining, 5)
        await wait_for(lambda: not rt._journal_backlog)       # Backfill on persistence, without waiting for another sentence or sealing.
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
            await hold.wait()                                 # First frame blocks writing to page.
        return await real_send(payload)

    host.host.send_frame = send_frame
    try:
        rt._post_display({"type": "visit_line", "visit_id": rt.visit_id, "line_id": "first"})
        rt._post_display({"type": "visit_line", "visit_id": rt.visit_id, "line_id": "second"})
        await settle()
        rt._display_task.cancel()                             # Sender cancelled with one sentence remaining in queue.
        await settle()
        await rt._flush_display(2)
        assert [f for f in host.host.frames if f.get("line_id") == "second"]   # flush restarts sender and delivers.
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
            await asyncio.sleep(0.3)                          # Page backpressure.
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
        assert ended and shown and shown[0] < ended[0]        # Display farewell before ended.
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
        rt._on_usage({"llm_output_tokens": 7})                # Buffer intervening usage/anomalies too.
        rt._count_anomaly("test", streak=False)
        gate.set()                                            # Header finally persists late.
        await wait_for(lambda: [r for r in rt.journal.lines() if r["text"] == "上传头还没写完时说的"])
        await wait_for(lambda: not rt._journal_backlog)       # Backfill usage/anomalies after that line.
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
        write_gate.wait(10)                                   # Disk stuck backfilling line one.
        real_append(data)

    rt.journal.open = slow_open
    try:
        await rt.on_transport_state({"state": "joined", "peer_present": False})
        for i in (3, 4):
            await rt.record_line("own_human", side="host", lp=i, ln=f"h:{i}", text=f"积压第{i}行", truncated=False)
        rt.journal._append_sync = slow_append
        gate.set()                                            # Header persists; background backfill starts and stalls on line one.
        await wait_for(writing.is_set)
        late = asyncio.ensure_future(rt.record_line("own_human", side="host", lp=5, ln="h:5", text="积压第5行",
                                                    truncated=False))
        await asyncio.sleep(0)                                # New lines queue behind incomplete backlog.
        rt.request_finalize("route_end")                      # Cleanup sealing starts before backfill completes.
        await asyncio.sleep(0.2)
        write_gate.set()
        await asyncio.wait_for(_finished(rt), 10)
        await asyncio.wait_for(late, 5)
        assert rt.journal.sealed
        # Inspect write order (lines() reorders by lp): new lines follow unfinished backlog.
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
        rt.liveness.on_page_lost(clock())                     # Transport page reloading: no page can send/receive frames.
        snap = rt.snapshot()
        assert not snap["connected"] and snap["reconnecting"]
        rt.liveness.on_page_back(clock())
        assert rt.snapshot()["connected"]
        rt.liveness.on_page_lost(clock())
        rt.phase = rtm.PHASE_ENDED                            # Post-ending _recent replay is not reconnecting.
        assert not rt.snapshot()["reconnecting"]
        rt.phase = rtm.PHASE_ACTIVE
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_closes_the_outbox_and_the_isolated_session(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 3)
        assert host.clients[0].closed                         # Isolated-session client closed.
        assert rt.outbox._executor is None                    # Outbox writer stopped and body-containing .outbox.jsonl deleted.
        assert not list((host.config_dir / "visit_spool").glob("*.outbox.jsonl"))
    finally:
        await teardown(guest, wire=wire, clock=clock)


async def test_a_joined_visit_is_not_ended_by_the_join_deadline(tmp_path, monkeypatch, clocks):
    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    try:
        assert rt.joined
        rt._join_deadline = clocks[0]() - 1                   # Entry already reported; only slow header writing remains when deadline expires.
        await rt.tick()
        assert rt.finalize_reason != "relay_lost"
    finally:
        await teardown(side, wire=wire, clock=clocks[0])


async def test_credentials_of_another_account_end_the_visit_and_cancel_the_room(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)      # Local account is acct at admission.
    clock, wall = clocks
    side = make_side(tmp_path, "host", clock=clock, wall=wall)

    async def other_account(**kwargs):
        side.creds_calls.append(kwargs)
        return credentials("host", account="someone-else")   # Switch community account before requesting credentials.

    side.deps.fetch_credentials = other_account
    rt = await start_side(side, clock=clock, wall=wall)
    try:
        assert rt.admitted_account == "acct"
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await rt.issue_credentials() is None
        assert rt.finalize_reason == "busy" and rt.takeover_token is None
        await settle()
        assert side.cancelled and side.cancelled[0][0] == rt.visit_id
    finally:
        await teardown(side, clock=clock)


async def test_guest_does_not_redeem_the_invite_after_an_account_switch(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)      # Local account is acct at admission.
    clock, wall = clocks
    side = make_side(tmp_path, "guest", clock=clock, wall=wall)
    rt = await start_side(side, invite_code=INVITE, clock=clock, wall=wall)

    # Logout/account change after admission raised account_change generation.
    monkeypatch.setattr(rtm, "_account_gen", rtm._account_gen + 1)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await rt.issue_credentials() is None
        # One-use invitation unredeemed: zero credential requests sent.
        assert side.creds_calls == [] and rt.finalize_reason == "busy"
    finally:
        await teardown(side, clock=clock)


async def test_account_change_waits_for_an_in_flight_credential_request(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "guest", clock=clock, wall=wall)
    side.creds_gate = asyncio.Event()          # Credentials requested; Servers has not answered.
    rt = await start_side(side, invite_code=INVITE, clock=clock, wall=wall)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        issuing = asyncio.ensure_future(rt.issue_credentials())
        await wait_for(lambda: side.creds_calls)
        swapped = []

        async def keep_visits():
            return False      # This change does not end in-flight visits, including unregistered visits with background requests.

        async def logout():
            async with rtm.account_change(timeout=5.0, ends_visits=keep_visits):
                swapped.append("session replaced")   # Caller changes local session here.

        change = asyncio.ensure_future(logout())
        await settle(60)
        # In-flight requests prevent session replacement, or _servers_session may redeem invites under the new account.
        assert swapped == []
        side.creds_gate.set()
        await asyncio.wait_for(change, 5)
        await asyncio.wait_for(issuing, 5)
        assert swapped == ["session replaced"]
    finally:
        await teardown(side, clock=clock)


async def test_credentials_request_is_pinned_to_the_admitted_account(tmp_path, monkeypatch, clocks):
    patch_admission(monkeypatch)
    clock, wall = clocks
    side = make_side(tmp_path, "guest", clock=clock, wall=wall)
    side.creds_error = cr.VisitAccountChanged("account_change")    # Credential client detects changed accounts.
    rt = await start_side(side, invite_code=INVITE, clock=clock, wall=wall)
    try:
        await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
        assert await rt.issue_credentials() is None
        assert side.creds_calls[0]["expect_account"] == "acct"
        assert rt.finalize_reason == "busy" and rt.takeover_token is None
    finally:
        await teardown(side, clock=clock)


async def test_peer_blocked_while_its_name_is_read_is_not_installed(tmp_path, monkeypatch, clocks):
    # Blocked after verification but before peer installation (local-name await): hooks cannot yet find peers, so recheck before installation.
    from main_logic.visit.limits import BlockEntry, Blocklist
    from main_routers.visit_router import display_socket

    side, rt, wire = await _host_joined(tmp_path, monkeypatch, clocks)
    entries: list = []

    async def blocklist(path):
        return Blocklist(path, list(entries))

    side.deps.load_blocklist = blocklist
    real_clean = type(rt)._clean_peer_name

    async def clean_then_block(self, raw, uid):
        name = await real_clean(self, raw, uid)
        entries.append(BlockEntry(visit_uid=GUEST_UID, display_name_at_block="x", blocked_at=NOW))
        assert await display_socket.end_visits_with_peer(GUEST_UID) == 0      # Hook still cannot find it.
        return name

    monkeypatch.setattr(type(rt), "_clean_peer_name", clean_then_block)
    await rt.on_transport_state({"state": "connected", "peer_present": True})
    await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload=_guest_hello(), nbytes=900)
    await _finished(rt)
    assert rt.finalize_reason == "peer_blocked" and rt.peer is None
    assert side.host.frames_of("visit_invite") == []
