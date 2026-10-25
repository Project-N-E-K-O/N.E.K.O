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

"""Visit runtime: own lines, the family's lines, interruptions and the receive gates (§3.2.4–§3.2.6, §4.5)."""

from __future__ import annotations

import asyncio

import pytest

from main_routers.visit_router import runtime as rtm
from main_routers.visit_router import runtime_talk as rtm_talk
from main_routers.visit_router import transport_ws
from tests.unit.visit_runtime_harness import (
    GUEST_VID,
    HOST_VID,
    Replies,
    bring_up,
    finish,
    settle,
    teardown,
    wait_for,
)
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


def _texts(wire, role):
    return [p for p in wire.sent[role] if p.get("t") == "text"]


async def _gated(tmp_path, monkeypatch, **kw):
    """Both sides active, both opening lines blocked inside the LLM."""
    hgate, ggate = asyncio.Event(), asyncio.Event()
    host_replies = kw.pop("host_replies", None) or Replies(gate=hgate)
    guest_replies = kw.pop("guest_replies", None) or Replies(gate=ggate)
    host_replies.gate = host_replies.gate or hgate
    guest_replies.gate = guest_replies.gate or ggate
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, host_replies=host_replies,
                                                    guest_replies=guest_replies, **kw)
    await wait_for(lambda: host.rt._line is not None and guest.rt._line is not None)
    return host, guest, wire, clock, hgate, ggate


async def test_one_line_is_one_stream_raw_to_tts_and_redacted_outbound(tmp_path, monkeypatch):
    host_replies = Replies(default=["小明今天", "也很开心。", "我们玩吧！"])
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, host_replies=host_replies,
                                                    guest_replies=Replies(gate=asyncio.Event()))
    try:
        await wait_for(lambda: _texts(wire, "host"))
        stream = host.host.streams[0]
        assert stream.request_id == _texts(wire, "host")[0]["ln"]
        assert "".join(stream.pushed) == "小明今天也很开心。我们玩吧！"     # TTS receives raw text.
        assert stream.finished
        outbound = [p.get("txt", "") for p in wire.sent["host"] if p.get("t") in ("line_delta", "text")]
        assert outbound and all("小明" not in t for t in outbound)
        final = _texts(wire, "host")[0]
        deltas = "".join(p["txt"] for p in wire.sent["host"] if p.get("t") == "line_delta" and p["ln"] == final["ln"])
        assert deltas == final["txt"] and final["truncated"] is False
        assert len([s for s in host.host.streams if s.request_id == final["ln"]]) == 1
        # Local dialogue also enters spool/upload logs with the same prefix.
        lines = host.rt.journal.lines()
        assert any(r["from"] == "own_cat" and r["text"] == final["txt"] for r in lines)
        spool = (host.config_dir / "visit_spool" / f"{host.rt.visit_id}.jsonl").read_text(encoding="utf-8")
        assert final["txt"] in spool
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_tts_chars_and_requests_are_recorded_as_usage(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch,
                                                    guest_replies=Replies(gate=asyncio.Event()))
    try:
        await wait_for(lambda: _texts(wire, "host"))
        usage = host.rt.journal.usage()
        assert usage["tts_requests"] >= 1
        assert usage["tts_chars"] == sum(len(c) for s in host.host.streams for c in s.pushed)
        assert usage["llm_output_tokens"] > 0
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_family_line_order_reserve_room_persist_enqueue_mirror(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    order = []
    real_record, real_send = rt.record_line, rt.outbox.send

    async def record(speaker, **kw):
        order.append(f"record:{speaker}")
        return await real_record(speaker, **kw)

    def send(msg, **kw):
        if msg.get("t") in ("text", "line_abort"):
            order.append(f"send:{msg['t']}:{msg.get('sp', '')}:{msg.get('trunc_reason', '')}")
        return real_send(msg, **kw)

    rt.record_line = record
    rt.outbox.send = send
    host.host.events.clear()
    try:
        ok = await rtm.route_stream_message("Host", {"action": "stream_data", "input_type": "text",
                                                    "data": "你们好呀", "source": "neko_visit:guest_cat",
                                                    "request_id": "r1"})
        assert ok is True
        human = [o for o in order if ":h:" in o or o == "record:own_human"]
        assert human == ["record:own_human", "send:text:h:"]
        # A human interrupts the opening line still being generated; its truncated text precedes the human line.
        assert order.index("send:text:c:human_interrupt") < order.index("send:text:h:")
        assert host.host.events[-1] == "mirror_user_input" and host.host.user_inputs == ["你们好呀"]
        assert rt.outbox.pending_bytes >= 0
        # Display frames carry the submitting request_id for local-bubble matching, as does /state replay; upstream wire messages omit it.
        def own_line():
            return [f for f in host.host.frames if f.get("type") == "visit_line"
                    and f["speaker"]["self"] is True and f["speaker"]["kind"] == "human"]
        await wait_for(own_line)
        assert own_line()[0]["request_id"] == "r1"
        await wait_for(lambda: any(r.get("speaker", {}).get("kind") == "human" for r in rt.snapshot()["transcript"]))
        replayed = [r for r in rt.snapshot()["transcript"] if r["speaker"]["kind"] == "human" and r["speaker"]["self"]]
        assert replayed[0]["request_id"] == "r1"
        assert all("request_id" not in p for p in wire.sent["host"])
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_busy_refusal_has_no_side_effects(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    rt.outbox.reserve = lambda nbytes: None
    turns = rt.room.cat_turns_since_human
    own_sends = len(rt.room._own_text_sends)
    line = rt._line
    try:
        await rtm.route_stream_message("Host", {"input_type": "text", "data": "长长的一段话",
                                                "source": "neko_visit:guest_cat", "request_id": "r9"})
        assert host.host.statuses[-1] == ("VISIT_E_BUSY", {"visit_id": rt.visit_id, "request_id": "r9"})
        assert host.host.user_inputs == []
        assert rt.room.cat_turns_since_human == turns
        assert len(rt.room._own_text_sends) == own_sends and rt.room._last_human_key is None
        assert rt._line is line and not line.speaker.done          # The speaking line was not interrupted.
        assert not [r for r in rt.journal.lines() if r["from"] == "own_human"]
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_failures_between_reserve_and_enqueue_release_the_reservation(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    real = rt.room.on_local_human_line

    def boom(ref, now):
        raise RuntimeError("boom")

    rt.room.on_local_human_line = boom
    base = rt.outbox.pending_bytes
    try:
        for i in range(30):
            await rtm.route_stream_message("Host", {"input_type": "text", "data": f"第{i}句",
                                                    "source": "neko_visit:guest_cat"})
        assert rt.outbox.pending_bytes == base
        assert host.host.user_inputs == []
        assert host.host.status_codes().count("VISIT_E_BUSY") == 30
        rt.room.on_local_human_line = real
        await rtm.route_stream_message("Host", {"input_type": "text", "data": "这句能发出去",
                                                "source": "neko_visit:guest_cat"})
        assert host.host.user_inputs == ["这句能发出去"]
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_family_rate_limit_refuses_without_mirror_or_transcript(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        for i in range(25):
            await rtm.route_stream_message("Host", {"input_type": "text", "data": f"第{i}句",
                                                    "source": "neko_visit:guest_cat"})
        refused = host.host.status_codes().count("VISIT_INPUT_REFUSED_RATE")
        accepted = len(host.host.user_inputs)
        assert accepted + refused == 25 and refused >= 5 and accepted <= 20
        humans = [r for r in rt.journal.lines() if r["from"] == "own_human"]
        assert len(humans) == accepted
        clock.advance(11)
        await rtm.route_stream_message("Host", {"input_type": "text", "data": "过一会儿再说",
                                                "source": "neko_visit:guest_cat"})
        assert host.host.user_inputs[-1] == "过一会儿再说"
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_refusals_by_phase_and_side(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    try:
        assert await rtm.route_stream_message("Guest", {"input_type": "text", "data": "我也说",
                                                        "request_id": "g1"}) is True
        assert guest.host.statuses[-1][0] == "VISIT_INPUT_REFUSED_AWAY"
        assert await rtm.route_stream_message("Host", {"input_type": "audio"}) is True
        assert host.host.statuses[-1][0] == "VISIT_VOICE_UNAVAILABLE"
        assert await rtm.route_stream_message("Host", {"input_type": "screen", "data": "x"}) is True
        assert await rtm.on_start_session("Host", {"input_type": "text", "request_id": "s1"}) is True
        assert host.host.acked == ["s1"]
        assert await rtm.on_start_session("Host", {"input_type": "audio", "request_id": "s2"}) is True
        assert host.host.failed == [("audio", "s2")]
        assert await rtm.route_voice_transcript("Host", "说话") is True
        # During finalization: WRAPUP, not NOT_READY.
        host.rt.apply_effects(host.rt.room.on_local_recall(host.rt.clock()))
        await rtm.route_stream_message("Host", {"input_type": "text", "data": "再见", "request_id": "w1"})
        assert host.host.statuses[-1][0] == "VISIT_INPUT_REFUSED_WRAPUP"
        assert host.host.user_inputs == []
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_visit_line_for_another_visit_is_refused(tmp_path, monkeypatch):
    # Page still thinks another visit is active after missed reconnection transitions; reject mismatched visit_id speech rather than send it to current guests.
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        assert await rtm.route_stream_message("Host", {"input_type": "text", "data": "给上一场的", "source": "neko_visit:guest_cat",
                                                        "visit_id": "z" * 22, "request_id": "o1"}) is True
        assert host.host.statuses[-1] == ("VISIT_INPUT_REFUSED_WRAPUP", {"visit_id": rt.visit_id, "request_id": "o1"})
        assert [p for p in _texts(wire, "host") if p.get("sp") == "h"] == []
        assert host.host.user_inputs == []
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_only_visit_composer_lines_reach_the_peer(tmp_path, monkeypatch):
    # Reject avatar file drops/history drops/ordinary submissions before visit-state restoration, without peer delivery or mirroring.
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        for source in ("avatar-drop", "react-chat-window", None):
            message = {"input_type": "text", "data": "<<<TEXT_FILE_1_START>>>私人文件", "request_id": "d1"}
            if source:
                message["source"] = source
            assert await rtm.route_stream_message("Host", message) is True
            assert host.host.statuses[-1] == ("VISIT_INPUT_REFUSED_SOURCE", {"visit_id": rt.visit_id, "request_id": "d1"})
        # Ordinary image submissions likewise receive request_id-tagged rejection instead of silent loss.
        for kind in ("user_image", "avatar_drop_image"):
            assert await rtm.route_stream_message("Host", {"input_type": kind, "data": "data:image/png;base64,AA",
                                                           "request_id": "i1"}) is True
            assert host.host.statuses[-1] == ("VISIT_INPUT_REFUSED_SOURCE", {"visit_id": rt.visit_id, "request_id": "i1"})
        assert [p for p in _texts(wire, "host") if p.get("sp") == "h"] == []
        assert host.host.user_inputs == []
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


@pytest.mark.parametrize("phase", ["pending", "invite_ready", "joining", "awaiting_accept"])
async def test_family_typing_before_activation_is_not_ready(tmp_path, monkeypatch, phase):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    try:
        host.rt._set_phase(phase)
        assert host.rt.room is None
        assert await rtm.route_stream_message("Host", {"input_type": "text", "data": "在吗",
                                                       "request_id": "p1"}) is True
        assert host.host.statuses[-1] == ("VISIT_INPUT_REFUSED_NOT_READY",
                                          {"visit_id": host.rt.visit_id, "request_id": "p1"})
        assert host.host.user_inputs == [] and "text" not in wire.sent_types("host")
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_human_interrupt_stops_now_and_keeps_only_the_released_prefix(tmp_path, monkeypatch):
    pause = asyncio.Event()
    host_replies = Replies(queue=[["第一句话。", "第二", pause, "句话。"]])
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, host_replies=host_replies,
                                                    guest_replies=Replies(gate=asyncio.Event()))
    rt = host.rt
    host.host.auto_play = False
    try:
        await wait_for(lambda: host.host.streams and host.host.streams[0].pushed)
        stream = host.host.streams[0]
        await rtm.on_page_signal("Host", {"speech_id": stream.speech_id, "played_ms": 60000, "ended": False})
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "line_delta"])
        await rtm.route_stream_message("Host", {"input_type": "text", "data": "等一下", "source": "neko_visit:guest_cat"})
        assert stream.aborted
        await wait_for(lambda: len(_texts(wire, "host")) >= 2)
        types = wire.sent_types("host")
        assert types.index("line_abort") < types.index("text")
        cat, human = _texts(wire, "host")[:2]
        assert cat["sp"] == "c" and cat["truncated"] and cat["trunc_reason"] == "human_interrupt"
        assert cat["txt"] == "第一句话。" and human["sp"] == "h"
        # Frontend later reports ended after pipeline cleanup; unspoken text must not count as played for handoff.
        await rtm.on_page_signal("Host", {"speech_id": stream.speech_id, "played_ms": 99999, "ended": True,
                                          "final": True})
        await settle()
        assert all("第二" not in p.get("txt", "") for p in wire.sent["host"])
        await wait_for(lambda: rt._line is None)
        # History writes run in separate turn_lock background tasks and may lag line closure; await actual persistence before asserting.
        await wait_for(lambda: any(
            c.startswith("第一句话。") and "第二" not in c and c != "第一句话。"
            for c in (getattr(m, "content", "") for m in host.clients[0]._conversation_history)), timeout=10)
    finally:
        pause.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_peer_cannot_use_our_line_prefix(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    seq = rt.sequencer.contiguous_seq + 1
    before_frames = len(host.host.frames)
    anomalies = rt.anomaly_count()
    try:
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "h:1", "lp": 5, "seq": seq, "sp": "c", "ad": "hc", "rt": "", "wu": False,
            "final": True, "txt": "冒充", "truncated": False, "i_done": 0}, nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "h:1", "i": 0, "lp": 5, "txt": "冒", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)
        assert rt.sequencer.contiguous_seq == seq                       # Advance seq normally and acknowledge.
        await settle()  # Display frames send asynchronously through the display queue.
        assert not [f for f in host.host.frames[before_frames:] if str(f.get("type")).startswith("visit_line")]
        assert rt.anomaly_count() >= anomalies + 2
        assert not [r for r in rt.journal.lines() if r["text"] == "冒充"]
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_final_text_rejected_for_its_lp_closes_the_shown_line(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    seq = rt.sequencer.contiguous_seq + 1
    lp = rt.room.max_lp_seen + 5
    try:
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:700", "i": 0, "lp": lp, "txt": "半截", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)
        await settle()
        assert [f for f in host.host.frames if f.get("type") == "visit_line_delta" and f.get("line_id") == "g:700"]
        before = len(host.host.frames)
        # Closing line changed lp, yielding lp_changed rejection while consuming seq/ack normally; it will not recur.
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:700", "lp": lp + 1, "seq": seq, "sp": "c", "ad": "hc", "rt": "",
            "wu": False, "final": True, "txt": "半截话", "truncated": False, "i_done": 1}, nbytes=200)
        await settle()
        assert rt.sequencer.contiguous_seq == seq
        aborts = [f for f in host.host.frames[before:] if f.get("type") == "visit_line_abort"
                  and f.get("line_id") == "g:700"]
        assert aborts                                       # Remove partial displayed bubbles rather than leave permanently incomplete speech.
        assert "g:700" not in rt.room._peer_open            # Room no longer considers it unclosed.
        assert rt._deltas.closed("g:700")                   # Later fragments no longer display.
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_peer_text_flood_is_acked_dropped_and_ends_the_visit(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    rt.limiter._recv_bps = 10 ** 9            # Isolate text token buckets, removing frame-byte constraints.
    for st in rt.limiter._senders.values():
        st.recv_bytes.rate = st.recv_bytes.capacity = st.recv_bytes.tokens = 10 ** 9
        st.recv_msgs.rate = st.recv_msgs.capacity = st.recv_msgs.tokens = 10 ** 9
        st.text.rate = 0                      # Isolate bursts without real-time refill, which slow-machine flooding could replenish.
    rt.limiter._recv_msgs_per_s = 10 ** 9
    assert rt.limiter._senders                # Peer sender exists, so settings above take effect.
    seq = rt.sequencer.contiguous_seq
    try:
        for i in range(200):
            seq += 1
            # Peer human lines do not count toward the 80-catgirl-line violation guard, only text buckets and visit totals.
            await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
                "t": "text", "v": 1, "ln": f"g:{100 + i}", "lp": 10 + i, "seq": seq, "sp": "h", "ad": "gh",
                "rt": "", "wu": False, "final": True, "txt": f"第{i}条", "truncated": False, "i_done": 0},
                nbytes=200)
            if rt.exit_task is not None:
                break
        peer_lines = [r for r in rt.journal.lines() if r["from"] == "peer_human"]
        assert len(peer_lines) <= 90
        assert rt.finalize_reason == "peer_protocol_violation"
        assert rt.rate_dropped >= 20
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_simultaneous_openings_sort_the_same_on_both_sides(tmp_path, monkeypatch):
    # Both sides open at lp=1; guest finishes/reaches host first, but both next-turn LLM histories order host then guest.
    host_open = asyncio.Event()
    after = asyncio.Event()
    host_replies = Replies(queue=[[host_open, "主人家开场。"]])
    guest_replies = Replies(queue=[["客人开场。"]])
    host_replies.default = [after, "后续"]
    guest_replies.default = [after, "后续"]
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, host_replies=host_replies,
                                                    guest_replies=guest_replies)
    try:
        await wait_for(lambda: _texts(wire, "guest"))
        await wait_for(lambda: any(r["from"] == "peer_cat" for r in host.rt.journal.lines()))
        host_open.set()
        await wait_for(lambda: _texts(wire, "host"))
        await wait_for(lambda: any(r["from"] == "peer_cat" for r in guest.rt.journal.lines()))
        assert _texts(wire, "host")[0]["lp"] == 1 and _texts(wire, "guest")[0]["lp"] == 1
        await wait_for(lambda: len(host.clients[0].seen) >= 2 and len(guest.clients[0].seen) >= 2)

        def host_first(seen):
            h = [i for i, c in enumerate(seen) if "主人家开场" in c]
            g = [i for i, c in enumerate(seen) if "客人开场" in c]
            assert h and g
            return h[0] < g[0]

        assert host_first(host.clients[0].seen[-1]) and host_first(guest.clients[0].seen[-1])
        # History contains only real dialogue; the prior arrival-notification question was removed.
        from config.prompts.prompts_visit import get_visit_arrival_notice

        assert get_visit_arrival_notice("host", host.rt.lang) not in host.clients[0].seen[-1]
    finally:
        after.set()
        host_open.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_goodbye_line_is_capped_at_forty_characters(tmp_path, monkeypatch):
    long_goodbye = "我" * 200
    host_replies = Replies(gate=asyncio.Event())
    guest_gate = asyncio.Event()
    guest_replies = Replies(queue=[[guest_gate], ["再见啦" + long_goodbye]])
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, host_replies=host_replies,
                                                    guest_replies=guest_replies)
    try:
        await wait_for(lambda: guest.rt._line is not None)
        status, body = await rtm.end_visit("Host", host.rt.visit_id, "recall")
        assert status == 200 and body["mode"] == "wrap_up"
        guest_gate.set()
        await wait_for(lambda: [p for p in _texts(wire, "guest") if p.get("wu")], timeout=10)
        goodbye = [p for p in _texts(wire, "guest") if p.get("wu")][0]
        assert len(goodbye["txt"]) == 40 and goodbye["trunc_reason"] == "goodbye_cap"
        stream = [s for s in guest.host.streams if s.request_id == goodbye["ln"]][0]
        assert "".join(stream.pushed) == goodbye["txt"]
        assert "wrap_up" in wire.sent_types("guest")              # Send wrap_up{speaking} before farewell speech starts.
        speaking = [p for p in wire.sent["guest"] if p.get("t") == "wrap_up" and p.get("ph") == "speaking"]
        assert speaking and speaking[0]["ln"] == goodbye["ln"]
    finally:
        host_replies.gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_recall_again_while_wrapping_up_is_refused(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    try:
        assert (await rtm.end_visit("Guest", guest.rt.visit_id, "recall"))[0] == 200
        assert (await rtm.end_visit("Guest", guest.rt.visit_id, "recall")) == (409, {"code": "VISIT_RECALL_ALREADY"})
        assert (await rtm.end_visit("Guest", "x" * 22, "recall"))[0] == 404
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_ending_closes_the_line_in_progress_before_leave(tmp_path, monkeypatch):
    pause = asyncio.Event()
    host, guest, wire, clock, wall = await bring_up(
        tmp_path, monkeypatch, host_replies=Replies(queue=[["说到一半。", "后半", pause, "句。"]]),
        guest_replies=Replies(gate=asyncio.Event()))
    host.host.auto_play = False
    try:
        await wait_for(lambda: host.host.streams and host.host.streams[0].pushed)
        await rtm.on_page_signal("Host", {"speech_id": host.host.streams[0].speech_id, "played_ms": 60000,
                                          "ended": False})
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "line_delta"])
        status, _ = await rtm.end_visit("Host", host.rt.visit_id, "route_end")
        assert status == 200
        # Takeover remains held while closing this line, so visits still reject input; return ordinary chat only after release.
        assert rtm.is_visit_route_active("Host") and host.rt.takeover_token is not None
        await wait_for(lambda: host.host.released)
        assert not rtm.is_visit_route_active("Host") and rtm.is_visit_route_locked("Host")
        await finish(host.rt, clock)
        types = wire.sent_types("host")
        cut = [p for p in _texts(wire, "host") if p.get("trunc_reason") == "visit_end"]
        assert cut and cut[0]["txt"] == "说到一半。"
        assert types.index("text") < types.index("leave")
        assert host.host.streams[0].aborted
        doc = host.rt.sealed_doc
        assert any(line["text"] == "说到一半。" for line in doc["request"]["lines"])
        assert not rtm.is_visit_route_locked("Host")
    finally:
        pause.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_fortieth_own_line_starts_the_wrap_up(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    try:
        host.rt.room.own_lines_total = 39
        hgate.set()
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "wrap_up" and p.get("ph") == "begin"])
        begin = [p for p in wire.sent["host"] if p.get("t") == "wrap_up" and p.get("ph") == "begin"][0]
        assert begin["reason"] == "budget"
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_five_llm_failures_in_a_row_end_the_visit(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    try:
        host.rt._llm_failures = 4
        host.replies.raise_error = RuntimeError("provider down")
        hgate.set()
        await wait_for(lambda: host.rt.exit_task is not None, timeout=10)
        assert host.rt.finalize_reason == "llm_error"
        failed = [p for p in _texts(wire, "host") if p.get("trunc_reason") == "llm_error"]
        assert failed and failed[0]["truncated"] is True
    finally:
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_page_signal_routes_only_known_speech_ids(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    try:
        assert await rtm.on_page_signal("Host", {"speech_id": "nope", "played_ms": 1, "ended": False}) is False
        assert await rtm.on_page_signal("Host", {"speech_id": "nope", "played_ms": 1, "ended": True}) is False
        assert await rtm.on_page_signal("Host", "junk") is False
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_vid_binding_drops_a_third_party(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    try:
        before = host.rt.binding_dropped
        await host.rt.on_recv(from_vid="g_" + "z" * 24, cmd=1, payload={"t": "hb", "v": 1, "lp_seen": 1,
                                                                       "crop": "upper", "hidden": False}, nbytes=60)
        assert host.rt.binding_dropped == before + 1
        await guest.rt.on_recv(from_vid="h_" + "z" * 24, cmd=1, payload={"t": "hb", "v": 1, "lp_seen": 1,
                                                                        "crop": "upper", "hidden": False}, nbytes=60)
        assert guest.rt.binding_dropped >= 1
        assert HOST_VID != "h_" + "z" * 24
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_peer_goodbye_is_cleaned_and_capped_before_it_reaches_our_prompt(tmp_path, monkeypatch):
    from config.visit_settings import VISIT_GOODBYE_MAX_CHARS

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        forged = "再见" * 200                                # Peer violates LineSpeaker's 40-character limit.
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:90", "lp": 90, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
            "ad": "hc", "rt": "", "wu": True, "final": True, "txt": forged, "truncated": False, "i_done": 0,
        }, nbytes=900)
        assert rt.last_peer_goodbye
        assert len(rt.last_peer_goodbye) <= VISIT_GOODBYE_MAX_CHARS
        assert forged.startswith(rt.last_peer_goodbye)
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_peer_back_after_a_timeout_class_drop_restarts_the_heartbeat_clock(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        now = clock()
        rt.liveness.peer_last_seen = now - 25               # Heartbeat approaches the 30 s death threshold.
        rt._on_peer_presence(False, 1, now)                 # Timeout disconnection creates no reentry grace period.
        assert rt.liveness.peer_departed_at is None
        rt._on_peer_presence(True, None, now + 1)           # Vendor confirms peer presence again.
        assert rt.liveness.peer_last_seen == now + 1
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_rate_limited_peer_line_shows_none_of_its_deltas(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from main_logic.visit.limits import RateChannel

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    real_admit = rt.limiter.admit
    text_calls = []

    def admit(vid, channel, **kw):
        if channel is RateChannel.TEXT:
            text_calls.append(vid)
            return SimpleNamespace(allowed=False, reason="text_rate")
        return real_admit(vid, channel, **kw)

    monkeypatch.setattr(rt.limiter, "admit", admit)
    before = len(host.host.frames)
    dropped = rt.rate_dropped
    try:
        for i in range(2):
            await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
                "t": "line_delta", "v": 1, "ln": "g:77", "i": i, "lp": 77, "txt": f"片{i}", "sp": "c", "ad": "hc",
                "rt": "", "wu": False}, nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:77", "lp": 77, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
            "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "片0片1", "truncated": False, "i_done": 2,
        }, nbytes=200)
        await settle()  # Display frames send asynchronously through the display queue.
        shown = [f for f in host.host.frames[before:] if str(f.get("type")).startswith("visit_line")]
        assert shown == []                                   # Neither deltas nor whole lines display.
        assert len(text_calls) == 1                          # Reserve quota once per line.
        assert rt.rate_dropped == dropped + 1
        assert not [r for r in rt.journal.lines() if r["text"] == "片0片1"]
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_seals_the_line_in_progress_into_the_transcript(tmp_path, monkeypatch):
    import json

    pause = asyncio.Event()
    host, guest, wire, clock, wall = await bring_up(
        tmp_path, monkeypatch, host_replies=Replies(queue=[["说到一半。", "后半", pause, "句。"]]),
        guest_replies=Replies(gate=asyncio.Event()))
    host.host.auto_play = False
    rt = host.rt
    try:
        await wait_for(lambda: host.host.streams and host.host.streams[0].pushed)
        await rtm.on_page_signal("Host", {"speech_id": host.host.streams[0].speech_id, "played_ms": 60000,
                                          "ended": False})
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "line_delta"])
        await asyncio.wait_for(rtm.stop_all("shutdown"), 3)
        doc = json.loads((host.config_dir / "visit_spool" / f"{rt.visit_id}.upload.json").read_text(encoding="utf-8"))
        own = [ln for ln in doc["request"]["lines"] if ln.get("speaker") == "own_cat" or ln.get("from") == "own_cat"]
        assert own and own[-1]["text"].startswith("说到一半") and own[-1].get("truncated") is True
    finally:
        pause.set()
        await teardown(guest, wire=wire, clock=clock)


async def test_an_overlapping_peer_line_counts_as_one_anomaly(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        for ln, lp in (("g:60", 60), ("g:61", 61)):          # New line begins before the old closes.
            await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
                "t": "line_delta", "v": 1, "ln": ln, "i": 0, "lp": lp, "txt": "嗯", "sp": "c", "ad": "hc",
                "rt": "", "wu": False}, nbytes=200)
        before = rt.journal._anomalies
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:61", "lp": 61, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
            "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "嗯", "truncated": False, "i_done": 1,
        }, nbytes=200)
        assert rt.journal._anomalies == before + 1
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_family_line_reserves_room_for_the_final_of_the_line_it_cuts(tmp_path, monkeypatch):
    pause = asyncio.Event()
    host, guest, wire, clock, wall = await bring_up(
        tmp_path, monkeypatch, host_replies=Replies(queue=[["说到一半。", "后半", pause, "句。"]]),
        guest_replies=Replies(gate=asyncio.Event()))
    host.host.auto_play = False
    rt = host.rt
    asked = []
    sizes = []
    real_reserve, real_size = rt.outbox.reserve, rt.outbox.encoded_size

    def reserve(nbytes):
        asked.append(nbytes)
        return real_reserve(nbytes)

    def encoded_size(payload):
        out = real_size(payload)
        sizes.append((payload.get("sp"), out[1]))
        return out

    rt.outbox.reserve = reserve
    rt.outbox.encoded_size = encoded_size
    try:
        await wait_for(lambda: host.host.streams and host.host.streams[0].pushed)
        await rtm.on_page_signal("Host", {"speech_id": host.host.streams[0].speech_id, "played_ms": 60000,
                                          "ended": False})
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "line_delta"])
        await rtm.route_stream_message("Host", {"input_type": "text", "data": "等一下",
                                                "source": "neko_visit:guest_cat"})
        family = [n for sp, n in sizes if sp == "h"][0]
        cut = [n for sp, n in sizes if sp == "c"][0]
        assert asked == [family + cut] and cut > 0          # Reserve the interrupted line's final too.
    finally:
        pause.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_repeated_lp_violations_end_the_visit(tmp_path, monkeypatch):
    from config.visit_settings import VISIT_ANOMALY_FINALIZE_COUNT, VISIT_LP_MAX_JUMP

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        before = rt.journal._anomalies
        for i in range(VISIT_ANOMALY_FINALIZE_COUNT):
            far = rt.room.max_lp_seen + VISIT_LP_MAX_JUMP + 100 + i     # Excessive jump: lp_out_of_range.
            await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
                "t": "line_delta", "v": 1, "ln": f"g:{300 + i}", "i": 0, "lp": far, "txt": "嗯", "sp": "c",
                "ad": "hc", "rt": "", "wu": False}, nbytes=200)
            if rt.exit_task is not None:
                break
        assert rt.finalize_reason == "peer_protocol_violation"
        assert rt.journal._anomalies - before >= VISIT_ANOMALY_FINALIZE_COUNT - 1
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_repeated_line_meta_mismatches_end_the_visit(tmp_path, monkeypatch):
    from config.visit_settings import VISIT_ANOMALY_FINALIZE_COUNT

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    rt.limiter._recv_bps = 10 ** 9
    for st in rt.limiter._senders.values():
        st.recv_bytes.rate = st.recv_bytes.capacity = st.recv_bytes.tokens = 10 ** 9
        st.recv_msgs.rate = st.recv_msgs.capacity = st.recv_msgs.tokens = 10 ** 9
        st.text.rate = st.text.capacity = st.text.tokens = 10 ** 9
    rt.limiter._recv_msgs_per_s = 10 ** 9
    try:
        n = VISIT_ANOMALY_FINALIZE_COUNT + 2
        for i in range(n):                                    # Open all these lines as human first.
            await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
                "t": "line_delta", "v": 1, "ln": f"g:{400 + i}", "i": 0, "lp": 400 + i, "txt": "嗯", "sp": "h",
                "ad": "hc", "rt": "", "wu": False}, nbytes=200)
        for i in range(n):
            ln, lp = f"g:{400 + i}", 400 + i
            # Closing as catgirl contradicts the same line's type; discard as protocol error and end on repeated violations.
            await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
                "t": "text", "v": 1, "ln": ln, "lp": lp, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
                "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "嗯", "truncated": False, "i_done": 1,
            }, nbytes=200)
            if rt.exit_task is not None:
                break
        assert rt.finalize_reason == "peer_protocol_violation"
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_the_owed_ack_still_goes_out_after_our_leave_completed(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        monkeypatch.setattr(rt.outbox, "leave_done", lambda now=None: True)   # Local leave acknowledged or grace expired.
        owed = {"seq": 7}
        monkeypatch.setattr(rt.sequencer, "poll_ack", lambda now, force=False: owed.pop("seq", None))
        await rt._close_channel("peer_left")
        acks = [p for p in wire.sent["host"] if p.get("t") == "ack"]
        assert acks and acks[-1]["seq"] == 7                    # Peer leave need not waste time waiting for gap repair.
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_early_speaking_wrap_ups_with_bad_lp_count_toward_the_cutoff(tmp_path, monkeypatch):
    from config.visit_settings import VISIT_ANOMALY_FINALIZE_COUNT, VISIT_LP_MAX_JUMP

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    for st in rt.limiter._senders.values():                  # Isolate violation counts by removing the 4/s control rate limit.
        st.ctl.rate = st.ctl.capacity = st.ctl.tokens = 10 ** 9
        st.recv_msgs.rate = st.recv_msgs.capacity = st.recv_msgs.tokens = 10 ** 9
    rt.limiter._recv_msgs_per_s = 10 ** 9
    try:
        before = rt.journal._anomalies
        for i in range(VISIT_ANOMALY_FINALIZE_COUNT + 2):
            far = rt.room.max_lp_seen + VISIT_LP_MAX_JUMP + 100 + i
            # wrap_up{speaking} always delivers early, bypassing _rx_wrap_up.
            await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload={
                "t": "wrap_up", "v": 1, "seq": rt.sequencer.contiguous_seq + 1, "lp": far, "ph": "speaking",
                "ln": f"g:{500 + i}", "reason": "quiet", "initiated_by": "host"}, nbytes=200)
            if rt.exit_task is not None:
                break
        assert rt.journal._anomalies > before
        assert rt.finalize_reason == "peer_protocol_violation"
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_replayed_transcript_lines_keep_their_own_line_ids(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await wait_for(lambda: len(rt.journal.lines()) >= 3)
        def key(f):
            return f["lp"], f["speaker"]["side"]

        live = {key(f): f["line_id"] for f in host.host.frames if f.get("type") == "visit_line"}
        replay = rt.snapshot()["transcript"]
        ids = [r["line_id"] for r in replay]
        assert all(ids) and len(set(ids)) == len(ids)       # Page bubbles key by line_id and must not collapse together.
        matched = [r for r in replay if key(r) in live]
        assert len(matched) >= 2
        assert all(r["line_id"] == live[key(r)] for r in matched)   # Same line, same ID as live display.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_runtime_detected_anomaly_is_recorded_once(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        journal, room = rt.journal._anomalies, rt.room.anomalies_total
        rt._count_anomaly("schema")
        assert rt.journal._anomalies == journal + 1 and rt.room.anomalies_total == room + 1
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_valid_early_wrap_up_resets_the_violation_streak(tmp_path, monkeypatch):
    from main_logic.visit.room import RoomEffects

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        monkeypatch.setattr(rt.room, "on_incoming_wrap_up", lambda *a, **k: RoomEffects())
        rt.room.violation_streak = 19
        rt._on_early_wrap_up({"t": "wrap_up", "ph": "speaking", "lp": rt.room.max_lp_seen + 1, "ln": "g:900",
                              "reason": "quiet"}, clock())
        assert rt.room.violation_streak == 0                 # This valid message also breaks a consecutive violation sequence.
        monkeypatch.setattr(rt.room, "on_incoming_wrap_up", lambda *a, **k: RoomEffects(violation="wrap_up_order"))
        rt.room.violation_streak = 3
        rt._on_early_wrap_up({"t": "wrap_up", "ph": "speaking", "lp": rt.room.max_lp_seen + 1, "ln": "g:901",
                              "reason": "quiet"}, clock())
        assert rt.room.violation_streak == 3                 # A violating message cannot reset violations itself.
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_replayed_or_late_line_deltas_are_not_forwarded(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt

    def delta(i, txt):
        return {"t": "line_delta", "v": 1, "ln": "g:70", "i": i, "lp": 70, "txt": txt, "sp": "c", "ad": "hc",
                "rt": "", "wu": False}

    try:
        before = len(host.host.frames)
        anomalies = rt.journal._anomalies
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload=delta(0, "原本的"), nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload=delta(0, "改写的"), nbytes=200)   # Replay the same i.
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:70", "lp": 70, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
            "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "原本的", "truncated": False, "i_done": 1,
        }, nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload=delta(1, "收口之后"), nbytes=200)  # Already finalized.
        await settle()  # Display frames send asynchronously through the display queue.
        shown = [f["text"] for f in host.host.frames[before:] if f.get("type") == "visit_line_delta"]
        assert shown == ["原本的"]
        assert rt.journal._anomalies == anomalies + 1          # Replay counts as one error; silently drop late fragments after closure.
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_soft_decoder_anomalies_are_counted_without_the_streak(tmp_path, monkeypatch):
    from config.visit_settings import VISIT_ANOMALY_FINALIZE_COUNT

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt.room.violation_streak = VISIT_ANOMALY_FINALIZE_COUNT - 1    # Another consecutive violation finalizes.
        journal, room, streak = rt.journal._anomalies, rt.room.anomalies_total, rt.room.violation_streak
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:80", "lp": 80, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
            "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "你好", "truncated": False, "i_done": 0,
            "tail_ms": 999999,
        }, nbytes=200)
        assert rt.journal._anomalies == journal + 1 and rt.room.anomalies_total == room + 1
        assert rt.room.violation_streak <= streak                # Process the message normally without counting a consecutive violation.
        assert rt.exit_task is None
        assert [r for r in rt.journal.lines() if r["text"] == "你好"]
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_line_rejected_as_an_overlap_is_dropped_whole(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt

    def delta(ln, lp):
        return {"t": "line_delta", "v": 1, "ln": ln, "i": 0, "lp": lp, "txt": "嗯", "sp": "c", "ad": "hc",
                "rt": "", "wu": False}

    try:
        before = len(host.host.frames)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload=delta("g:60", 60), nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload=delta("g:61", 60), nbytes=200)   # Another line opens at the same lp.
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:61", "lp": 60, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
            "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "交叠的那行", "truncated": False, "i_done": 1,
        }, nbytes=200)
        await wait_for(lambda: any(f.get("line_id") == "g:60" for f in host.host.frames[before:]))
        await settle()
        lines = [f["line_id"] for f in host.host.frames[before:] if str(f.get("type")).startswith("visit_line")]
        assert "g:61" not in lines and "g:60" in lines
        assert not [r for r in rt.journal.lines() if r["text"] == "交叠的那行"]
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_abort_and_typing_with_a_bad_lp_are_dropped_as_anomalies(tmp_path, monkeypatch):
    from config.visit_settings import VISIT_LP_MAX_JUMP

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await settle()
        before, anomalies = len(host.host.frames), rt.journal._anomalies
        far = rt.room.max_lp_seen + VISIT_LP_MAX_JUMP + 100
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_abort", "v": 1, "ln": "g:95", "lp": far, "i_done": 0, "reason": "human_interrupt"},
            nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=3, payload={"t": "typing", "v": 1, "lp": far + 1, "sp": "c"},
                         nbytes=200)
        await settle()  # Display frames send asynchronously through the display queue.
        types = [f.get("type") for f in host.host.frames[before:]]
        assert "visit_line_abort" not in types and "visit_typing" not in types
        assert rt.journal._anomalies == anomalies + 2
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_family_input_waits_until_ready_is_queued(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, accept=False)
    hrt = host.rt
    seen = {}
    real_send = hrt.outbox.send

    def spy(msg, **kw):
        if msg.get("t") == "ready" and "status" not in seen:
            # Just before ready enqueue: activation completed and phase is active, so family text must already be rejected.
            seen["phase"] = hrt.phase
        return real_send(msg, **kw)

    real_activate = hrt._activate

    async def activate_then_type():
        await real_activate()
        await rtm.route_stream_message("Host", {"input_type": "text", "data": "抢在 ready 前面",
                                                "source": "neko_visit:guest_cat"})
        seen["status"] = host.host.status_codes()[-1] if host.host.statuses else None

    hrt.outbox.send = spy
    hrt._activate = activate_then_type
    try:
        await hrt.accept(True)
        assert seen["status"] == "VISIT_INPUT_REFUSED_NOT_READY"
        assert not [p for p in wire.sent["host"] if p.get("t") == "text" and p.get("sp") == "h"]
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_only_the_verified_peer_can_end_the_visit_by_overflowing(tmp_path, monkeypatch):
    from types import SimpleNamespace

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    overflow = SimpleNamespace(allowed=False, sustained_overflow=True)
    monkeypatch.setattr(rt.limiter, "admit_frame", lambda vid, nbytes, now=None: overflow)
    try:
        await rt.on_recv(from_vid="g_" + "z" * 24, cmd=3, payload={"t": "typing", "v": 1, "lp": 1, "sp": "c"},
                         nbytes=200)
        assert rt.exit_task is None                         # A third room participant exhausting their own bucket cannot end this visit.
        await rt.on_recv(from_vid=GUEST_VID, cmd=3, payload={"t": "typing", "v": 1, "lp": 1, "sp": "c"},
                         nbytes=200)
        assert rt.finalize_reason == "peer_protocol_violation"
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_frames_sent_elsewhere_get_the_same_bookkeeping(tmp_path, monkeypatch):
    from types import SimpleNamespace

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    calls, sent, written = [], [], []
    monkeypatch.setattr(rt.room, "on_wrap_up_sent", lambda ph, now: calls.append(ph))
    monkeypatch.setattr(rt.liveness, "on_message_sent", lambda now: sent.append(now))
    monkeypatch.setattr(rt.outbox, "written", lambda frame, now=None: written.append(frame.seq))
    try:
        rt.on_frame_sent(SimpleNamespace(seq=9, t="wrap_up", retransmit=False, payload={"ph": "begin"}))
        assert calls == ["begin"] and len(sent) == 1        # Start finalization-step timers and record liveness timing normally.
        assert written == [9]                               # Outbox records actual write time, the starting point for grace/delivery timers.
        rt.on_frame_sent(SimpleNamespace(seq=9, t="wrap_up", retransmit=True, payload={"ph": "begin"}))
        assert calls == ["begin", "begin"]                  # Report retransmissions too because initial writes may fail; room uses only the first successful write.
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_failing_sent_bookkeeping_does_not_stop_the_rest_of_the_batch(tmp_path, monkeypatch):
    from types import SimpleNamespace

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    frames = [SimpleNamespace(seq=n, t="text", retransmit=False, to_ws=lambda n=n: {"n": n}) for n in (1, 2)]
    sent, failed = [], []

    async def send(msg):
        sent.append(msg["n"])
        return True

    def broken(frame):
        raise RuntimeError("bookkeeping")

    monkeypatch.setattr(rt.outbox, "due", lambda now=None: list(frames))
    monkeypatch.setattr(rt.outbox, "write_failed", lambda frame, now=None: failed.append(frame.seq))
    monkeypatch.setattr(rt.transport, "send", send)
    monkeypatch.setattr(rt, "on_frame_sent", broken)
    try:
        await rt.flush()
        assert sent == [1, 2] and failed == []                 # Accounting failure must not block later frames in this batch.
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_line_abort_after_the_final_text_changes_nothing(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:71", "i": 0, "lp": 71, "txt": "说完了", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:71", "lp": 71, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
            "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "说完了", "truncated": False, "i_done": 1,
        }, nbytes=200)
        await settle()
        before = len(host.host.frames)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_abort", "v": 1, "ln": "g:71", "lp": 71, "i_done": 0, "reason": "human_interrupt"},
            nbytes=200)                                      # Late/replayed abort.
        await settle()  # Display frames send asynchronously through the display queue.
        assert not [f for f in host.host.frames[before:] if f.get("type") == "visit_line_abort"]
        # After accepted abort, that line's fragments no longer display.
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:72", "i": 0, "lp": 72, "txt": "第一片", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_abort", "v": 1, "ln": "g:72", "lp": 72, "i_done": 1, "reason": "human_interrupt"},
            nbytes=200)
        await settle()
        mark = len(host.host.frames)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:72", "i": 1, "lp": 72, "txt": "停嘴之后", "sp": "c", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)
        await settle()  # Display frames send asynchronously through the display queue.
        assert not [f for f in host.host.frames[mark:] if f.get("type") == "visit_line_delta"]
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_an_early_speaking_wrap_up_does_not_make_the_gap_filler_look_reordered(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    seen = []
    real = rt._lp_rejected

    def spy(violation):
        seen.append(violation)
        return real(violation)

    rt._lp_rejected = spy
    try:
        seq = rt.sequencer.contiguous_seq
        lp = rt.room.max_lp_seen
        # begin at seq+1 was lost; speaking at seq+2 arrives first and delivers early.
        await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload={
            "t": "wrap_up", "v": 1, "seq": seq + 2, "lp": lp + 11, "ph": "speaking", "ln": "g:600",
            "reason": "quiet", "initiated_by": "host"}, nbytes=200)
        # Gap fills with the same sender's earlier begin and lower lp.
        await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload={
            "t": "wrap_up", "v": 1, "seq": seq + 1, "lp": lp + 10, "ph": "begin", "ln": "g:600",
            "reason": "quiet", "initiated_by": "host"}, nbytes=200)
        assert "lp_not_monotonic" not in seen
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_stuck_display_does_not_hold_up_the_receive_path(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    rt.limiter._recv_bps = 10 ** 9
    for st in rt.limiter._senders.values():
        st.recv_bytes.rate = st.recv_bytes.capacity = st.recv_bytes.tokens = 10 ** 9
        st.recv_msgs.rate = st.recv_msgs.capacity = st.recv_msgs.tokens = 10 ** 9
    rt.limiter._recv_msgs_per_s = 10 ** 9
    stuck = asyncio.Event()
    real_send = host.host.send_frame

    async def slow_send(payload):
        await stuck.wait()                                   # Page socket backpressure blocks every write.
        return await real_send(payload)

    host.host.send_frame = slow_send
    try:
        async def burst():
            for i in range(100):
                await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
                    "t": "line_delta", "v": 1, "ln": "g:73", "i": i, "lp": 73, "txt": f"{i}", "sp": "c", "ad": "hc",
                    "rt": "", "wu": False}, nbytes=200)
            await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
                "t": "text", "v": 1, "ln": "g:73", "lp": 73, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
                "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "整句", "truncated": False, "i_done": 100,
            }, nbytes=200)

        await asyncio.wait_for(burst(), 2)                   # Receive processing does not wait for the page.
        assert rt.display_dropped > 0                        # Discard subtitle fragments under backlog.
        stuck.set()
        await wait_for(lambda: any(f.get("type") == "visit_line" and f.get("line_id") == "g:73"
                                   for f in host.host.frames))   # Never drop whole sentences.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_the_upload_record_is_taken_before_a_slow_spool_write(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()

    async def slow_append(line):
        await stuck.wait()

    monkeypatch.setattr(rt.spool, "append", slow_append)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(rt.record_line("own_cat", side="host", lp=500, ln="h:500", text="最后一句",
                                                  truncated=True), 0.2)
        # Spool remains stuck when shutdown seals; upload records already contain this line.
        assert [r for r in rt.journal.lines() if r["text"] == "最后一句"]
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_the_display_queue_is_bounded_and_dropped_when_the_visit_ends(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    real_send = host.host.send_frame

    async def slow_send(payload):
        if payload.get("type") == "visit_line":
            await stuck.wait()                               # Only the display queue path is blocked.
        return await real_send(payload)

    host.host.send_frame = slow_send
    try:
        for i in range(10):                                   # Whole sentences at queue head must survive even when full.
            rt._post_display({"type": "visit_line", "line_id": f"g:{i}"})
        for i in range(50):
            rt._post_display({"type": "visit_line_delta", "line_id": f"g:{i}", "i": 0}, droppable=True)
        for i in range(10, 250):
            rt._post_display({"type": "visit_line", "line_id": f"g:{i}"})
        queued = list(rt._display)
        finals = [f for f in queued if f["type"] == "visit_line"]
        # When full, discard oldest subtitle fragments first; keep all whole sentences, whose per-visit count bounds backlog.
        assert len(finals) == 250 and len(queued) == 256
        assert len([f for f in queued if f["type"] == "visit_line_delta"]) == 6
        task = rt._display_task
        rt.request_finalize("route_end")
        await finish(rt, clock)
        await settle()
        assert not rt._display and task.cancelled()          # Visit ended: clear the queue and stop frame-sending tasks.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_peer_line_effects_apply_before_the_line_is_persisted(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    applied = []
    real_apply = rt.apply_effects

    async def slow_record(*args, **kwargs):
        await stuck.wait()                                   # Slow disk blocks persistence.

    def spy(eff):
        applied.append(eff)
        return real_apply(eff)

    produced = []
    real_done = rt.room.on_incoming_done

    def done_spy(ev, now):
        eff = real_done(ev, now)
        produced.append(eff)
        return eff

    rt.record_line = slow_record
    rt.apply_effects = spy
    rt.room.on_incoming_done = done_spy
    try:
        receiving = asyncio.ensure_future(rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:74", "lp": 74, "seq": rt.sequencer.contiguous_seq + 1, "sp": "h",
            "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "等一下", "truncated": False, "i_done": 0,
        }, nbytes=200))
        await wait_for(lambda: produced and produced[0] in applied)   # Stop speech/cancel pending replies without waiting for this line's persistence.
        assert not receiving.done()
        assert produced[0].reply is None and not produced[0].say_goodbye  # Reply waits until history insertion.
        stuck.set()
        await asyncio.gather(receiving)
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)



async def test_a_line_whose_generation_failed_midway_is_marked_truncated(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(
        tmp_path, monkeypatch,
        host_replies=Replies(queue=[["说到一半，", "然后", RuntimeError("provider dropped the stream")]]),
        guest_replies=Replies(gate=asyncio.Event()))
    try:
        await wait_for(lambda: [p for p in _texts(wire, "host") if p.get("trunc_reason") == "llm_error"], timeout=10)
        failed = [p for p in _texts(wire, "host") if p.get("trunc_reason") == "llm_error"][0]
        assert failed["truncated"] is True and failed["txt"]     # Mark truncated even with a spoken prefix, never as a completed sentence.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_no_display_frame_after_the_visit_ended(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt.request_finalize("route_end")
        await finish(rt, clock)
        before = len(host.host.frames)
        rt._post_display({"type": "visit_line", "line_id": "g:1"})   # Slow-persisting _rx_text only returns now.
        await settle()
        assert len(host.host.frames) == before and not rt._display
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_final_that_contradicts_its_pieces_takes_the_bubble_down(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "line_delta", "v": 1, "ln": "g:76", "i": 0, "lp": 76, "txt": "嗯", "sp": "h", "ad": "hc",
            "rt": "", "wu": False}, nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:76", "lp": 76, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
            "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "嗯", "truncated": False, "i_done": 1,
        }, nbytes=200)
        await wait_for(lambda: [f for f in host.host.frames
                                if f.get("type") == "visit_line_abort" and f.get("line_id") == "g:76"])
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_second_peer_line_on_the_same_lp_is_rejected(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt

    def final(ln, txt):
        return {"t": "text", "v": 1, "ln": ln, "lp": 77, "seq": rt.sequencer.contiguous_seq + 1, "sp": "h",
                "ad": "hc", "rt": "", "wu": False, "final": True, "txt": txt, "truncated": False, "i_done": 0}

    try:
        anomalies = rt.journal._anomalies
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload=final("g:77", "第一行"), nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload=final("g:78", "冒用同一个 lp"), nbytes=200)
        texts = [r["text"] for r in rt.journal.lines()]
        assert "第一行" in texts and "冒用同一个 lp" not in texts
        assert rt.journal._anomalies == anomalies + 1
        ids = [r["line_id"] for r in rt.snapshot()["transcript"]]
        assert len(ids) == len(set(ids))
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_ceremony_turn_gives_up_when_the_session_lock_stays_busy(tmp_path, monkeypatch):
    from types import SimpleNamespace

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stub = SimpleNamespace(turn_lock=asyncio.Lock(), history=[], set_sink=lambda sink: None,
                           forget_untracked=lambda: None)
    real_session, rt.session = rt.session, stub
    try:
        await stub.turn_lock.acquire()                        # The unstoppable round still holds its lock during finalization.
        out = await asyncio.wait_for(rt.one_shot_turn("回家说一句", timeout=0.2), 3)
        assert out is None                                    # Use fixed fallback speech rather than wait forever.
    finally:
        rt.session = real_session
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)



async def test_a_ceremony_turn_shares_one_deadline_between_lock_and_generation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from main_routers.visit_router import runtime_talk

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    budgets: list[float] = []
    real_bounded = runtime_talk._stream_bounded

    async def spy_bounded(session, prompt, timeout):
        budgets.append(timeout)
        return await real_bounded(session, prompt, timeout)

    monkeypatch.setattr(runtime_talk, "_stream_bounded", spy_bounded)

    async def slow_stream(text, **kwargs):
        await asyncio.sleep(2)

    # Double session isolates one_shot_turn's own timing from the ongoing round's lock.
    stub = SimpleNamespace(turn_lock=asyncio.Lock(), history=[], client=SimpleNamespace(stream_text=slow_stream),
                           set_sink=lambda sink: None, forget_untracked=lambda: None)
    real_session, rt.session = rt.session, stub
    try:
        await stub.turn_lock.acquire()
        asyncio.get_running_loop().call_later(0.3, stub.turn_lock.release)    # Release the lock after most of the deadline, leaving 200 ms scheduling margin.
        out = await asyncio.wait_for(rt.one_shot_turn("回家说一句", timeout=0.5), 3)
        assert out is None
        # Generation gets only remaining time, about 0.2 s, not a fresh 0.5 s; inspect arguments rather than wall time.
        assert len(budgets) == 1 and budgets[0] < 0.35
    finally:
        rt.session = real_session
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)

async def test_state_replay_keeps_the_full_visit_line_shape(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await wait_for(lambda: len(rt.journal.lines()) >= 2)
        await settle()
        live = {f["line_id"]: f for f in host.host.frames if f.get("type") == "visit_line"}
        replay = {r["line_id"]: r for r in rt.snapshot()["transcript"]}
        common = set(live) & set(replay)
        assert common
        for ln in common:
            for key in ("addressee", "reply_to", "goodbye", "i_done", "speaker", "text"):
                assert replay[ln][key] == live[ln][key]       # Reload preserves the live frame shape.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_line_whose_llm_ignores_cancellation_still_reaches_the_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm_talk, "_LLM_SETTLE_S", 0.2)
    pause = asyncio.Event()
    host, guest, wire, clock, wall = await bring_up(
        tmp_path, monkeypatch, host_replies=Replies(queue=[["说到一半。", "后半", pause, "句。"]]),
        guest_replies=Replies(gate=asyncio.Event()))
    host.host.auto_play = False
    rt = host.rt
    try:
        await wait_for(lambda: host.host.streams and host.host.streams[0].pushed)
        await rtm.on_page_signal("Host", {"speech_id": host.host.streams[0].speech_id, "played_ms": 60000,
                                          "ended": False})
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "line_delta"])
        line = rt._line
        llm = line.llm_task
        real_cancel = llm.cancel
        llm.cancel = lambda *a, **k: False                    # LLM coroutine refuses to stop and retains the session lock.
        rt.interrupt_line("human_interrupt")
        await wait_for(lambda: [r for r in rt.journal.lines() if r["from"] == "own_cat" and r["truncated"]],
                       timeout=3)                             # Emitted lines still enter transcripts.
        await wait_for(lambda: line.task.done(), timeout=3)   # The line task also finalizes without hanging on locked history insertion.
        llm.cancel = real_cancel
    finally:
        pause.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_line_whose_llm_ignores_the_deadline_still_ends(tmp_path, monkeypatch):
    from tests.unit import visit_runtime_harness as harness

    monkeypatch.setattr(rtm_talk, "VISIT_LLM_TIMEOUT_S", 0.3)
    monkeypatch.setattr(rtm_talk, "_LLM_SETTLE_S", 0.2)
    release = asyncio.Event()

    async def stubborn(self, text, **_kw):
        while not release.is_set():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue                                      # Swallow cancellation and continue after the deadline.

    monkeypatch.setattr(harness.FakeClient, "stream_text", stubborn)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await wait_for(lambda: rt._line is not None)
        line = rt._line
        await wait_for(lambda: line.task.done(), timeout=3)   # Finalize at deadline without waiting for uncooperative generation.
        assert line.llm_error == "timeout"
    finally:
        release.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_an_abandoned_generation_cannot_leak_into_the_next_line(tmp_path, monkeypatch):
    from tests.unit import visit_runtime_harness as harness
    from utils.llm_client import AIMessage, HumanMessage

    monkeypatch.setattr(rtm_talk, "VISIT_LLM_TIMEOUT_S", 0.3)
    monkeypatch.setattr(rtm_talk, "_LLM_SETTLE_S", 0.1)
    release = asyncio.Event()
    real_stream = harness.FakeClient.stream_text
    host_replies = Replies()
    calls: dict = {}

    async def stream_text(self, text, **kw):
        if self.replies is not host_replies:
            # Stall only the host; guests speak normally, avoiding premature llm_error causing host peer_left.
            await real_stream(self, text, **kw)
            return
        calls[id(self)] = calls.get(id(self), 0) + 1
        if calls[id(self)] > 1:
            await real_stream(self, text, **kw)
            return
        self._conversation_history.append(HumanMessage(content=text))
        while not release.is_set():
            try:
                await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                continue                                      # Swallow cancellation and keep running after deadline.
        await self.on_text_delta("串话", True)                 # Text emitted after abandonment.
        self._conversation_history.append(AIMessage(content="幽灵回复"))

    monkeypatch.setattr(harness.FakeClient, "stream_text", stream_text)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, host_replies=host_replies)
    rt = host.rt
    session = rt.session
    finished: list = []
    real_finish = rt._finish_line

    async def finish_spy(line):
        finished.append(line)                                 # Outcomes at each line closure, without racing transient rt._line state.
        return await real_finish(line)

    rt._finish_line = finish_spy
    try:
        await wait_for(lambda: rt._line is not None, timeout=10)
        opening = rt._line                                    # Host opening line.
        await wait_for(lambda: session.stray is not None, timeout=10)  # Opening deadline expires and abandons generation.
        rt.schedule_reply(None)                               # Next line starts while abandoned generation runs, queuing behind any already queued/speaking line.
        await wait_for(lambda: rt.finalize_reason is not None, timeout=10)
        assert rt.finalize_reason == "llm_error"              # Still unstoppable after one full timeout: speech is unavailable, finalize as llm_error.
        # Set finalize_reason first; _run_line's finally closes the line afterward.
        await wait_for(lambda: any(line is not opening for line in finished), timeout=10)
        later = [line for line in finished if line is not opening]
        assert later and all(line.llm_error == "timeout" for line in later)
        assert calls[id(session.client)] == 1                 # Do not open another stream concurrently on the same client.
        release.set()                                         # Abandoned generation now emits text and appends history.
        await wait_for(lambda: session.stray is None or session.stray.done(), timeout=10)
        await settle()
        contents = [getattr(m, "content", "") for m in session.history]
        assert not [c for c in contents if "幽灵回复" in str(c)]  # Remove its appended reply.
        own = [r["text"] for r in rt.journal.lines() if r["from"] == "own_cat"]
        assert not [t for t in own if "串话" in t]             # Its text entered no line.
    finally:
        release.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_home_coming_turn_is_bounded_even_if_the_llm_ignores_cancellation():
    from types import SimpleNamespace

    from main_routers.visit_router.session_pool import VisitSession
    from utils.llm_client import SystemMessage

    release = asyncio.Event()

    class Stubborn:
        def __init__(self):
            self._conversation_history = [SystemMessage(content="instructions")]

        async def stream_text(self, text, **kw):
            while not release.is_set():
                try:
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    continue

    session = VisitSession(client=Stubborn(), side="host")
    owner = SimpleNamespace(session=session, visit_id="v" * 32)
    try:
        text = await asyncio.wait_for(
            rtm_talk.TalkMixin.one_shot_turn(owner, "回家了", timeout=0.2, without_history=True), 3)
        assert text is None                                   # Fail/finalize at deadline without blocking exit.
        assert session.stray is not None and not session.stray.done()
        assert not session.turn_lock.locked()
    finally:
        release.set()


async def test_a_stream_abandoned_while_its_caller_is_cancelled_twice_is_still_recorded():
    from main_routers.visit_router.session_pool import VisitSession
    from utils.llm_client import SystemMessage

    release = asyncio.Event()

    class Stubborn:
        def __init__(self):
            self._conversation_history = [SystemMessage(content="instructions")]

        async def stream_text(self, text, **kw):
            while not release.is_set():
                try:
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    continue

    session = VisitSession(client=Stubborn(), side="host")
    caller = asyncio.ensure_future(rtm_talk._stream_bounded(session, "x", 10))
    try:
        await asyncio.sleep(0.05)
        caller.cancel()                                       # Finalization cancels this round.
        await asyncio.sleep(0.05)                             # It awaits the uncooperative stream stopping.
        caller.cancel()                                       # Cancel again.
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert session.stray is not None and not session.stray.done()   # Still register on the session so the next round waits for it first.
    finally:
        release.set()
        await asyncio.sleep(0.05)


async def test_own_line_frames_reach_the_page_in_order(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    real_send = host.host.send_frame
    slowed = []

    async def send_frame(payload):
        if (payload.get("type") == "visit_line_delta" and not slowed
                and payload.get("speaker", {}).get("side") == "host"):  # Delay only the local line; the guest line displays too.
            slowed.append(payload)
            await asyncio.sleep(0.2)                          # First-fragment page write encounters backpressure.
        return await real_send(payload)

    host.host.send_frame = send_frame
    before = len(host.host.frames)
    try:
        rt.schedule_reply(None)
        await wait_for(lambda: slowed and [f for f in host.host.frames[before:] if f.get("type") == "visit_line"
                                           and f.get("line_id") == slowed[0]["line_id"]], timeout=5)
        await settle()
        own = [f for f in host.host.frames[before:] if f.get("type") in ("visit_line_delta", "visit_line")
               and f.get("speaker", {}).get("side") == "host"]
        ln = slowed[0]["line_id"]
        kinds = [f["type"] for f in own if f.get("line_id") == ln]
        assert kinds[-1] == "visit_line"                      # Whole sentence arrives last; stale fragments must not queue behind it.
        assert "visit_line_delta" in kinds
    finally:
        host.host.send_frame = real_send
        await teardown(host, guest, wire=wire, clock=clock)


async def test_an_admitted_family_line_still_reaches_the_peer_after_the_close_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(rtm, "_CLOSE_WAIT_S", 0.2)
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    real_record = rt.record_line

    async def slow_record(speaker, **kwargs):
        if speaker == "own_human":
            await stuck.wait()                                # Family speech persists more slowly than channel-close waiting permits.
        return await real_record(speaker, **kwargs)

    rt.record_line = slow_record
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "等一下再走", "source": "neko_visit:guest_cat"}))
        await wait_for(lambda: rt.outbox.reserved_bytes > 0)
        rt.request_finalize("delivery_failed")
        await asyncio.sleep(1.0)                              # _CLOSE_WAIT_S exceeded.
        assert not rt._ended_published                        # Reservation remains: exit still awaits closure rather than proceeding.
        assert rtm.get_runtime("Host") is rt                  # teardown must not remove the send channel first.
        stuck.set()
        await asyncio.gather(sending)
        await finish(rt, clock)
        humans = [p for p in wire.sent["host"] if p.get("t") == "text" and p.get("sp") == "h"]
        assert humans and humans[0].get("txt") == "等一下再走"     # This sentence still reaches the peer.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_closed_line_whose_llm_lingers_is_booked_before_the_shutdown_seal(tmp_path, monkeypatch):
    pause = asyncio.Event()
    host, guest, wire, clock, wall = await bring_up(
        tmp_path, monkeypatch, host_replies=Replies(queue=[["说到一半。", "后半", pause, "句。"]]),
        guest_replies=Replies(gate=asyncio.Event()))
    host.host.auto_play = False
    rt = host.rt
    try:
        await wait_for(lambda: host.host.streams and host.host.streams[0].pushed)
        await rtm.on_page_signal("Host", {"speech_id": host.host.streams[0].speech_id, "played_ms": 60000,
                                          "ended": False})
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "line_delta"])
        line = rt._line
        llm = line.llm_task
        real_cancel = llm.cancel
        llm.cancel = lambda *a, **k: False                    # Unstoppable LLM makes _finish_line wait _LLM_SETTLE_S.
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)   # Shutdown waits only 0.5 s.
        own = [r for r in rt.journal.lines() if r["from"] == "own_cat"]
        assert own and own[-1]["truncated"]                   # Closed line enters transcript before sealing.
        assert rt.journal.sealed
        llm.cancel = real_cancel
    finally:
        pause.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_booking_a_line_survives_its_caller_being_cancelled(tmp_path, monkeypatch):
    from main_logic.visit.room import LineRef
    from main_routers.visit_router.line_speaker import LineHeader

    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    gate = asyncio.Event()
    calls = []
    real_record = rt.record_line

    async def slow_record(*args, **kwargs):
        calls.append(kwargs.get("ln"))
        await gate.wait()                                 # Disk slows transcript writing.
        return await real_record(*args, **kwargs)

    rt.record_line = slow_record
    header = LineHeader(ln="h:99", lp=99, ad="gc", rt="", wu=False, sp="c", lang=None)
    line = rtm_talk._LineRun(ref=LineRef("h:99", 99, "host"), header=header, reply_to=None, goodbye=False, prompt="")
    line.payload = {"txt": "收口的这一行", "truncated": True}
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(rt.book_line(line), 0.1)   # Shutdown deadline cancels the caller.
        gate.set()
        await rt.book_line(line)                          # Another caller awaits the same write.
        assert calls == ["h:99"]                          # Write once only.
        assert [r for r in rt.journal.lines() if r["text"] == "收口的这一行"]   # That cancellation did not interrupt writing.
    finally:
        gate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_an_admitted_family_line_is_sent_even_if_its_handler_is_cancelled(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    real_record = rt.record_line

    async def slow_record(speaker, **kwargs):
        if speaker == "own_human":
            await stuck.wait()                                # Family speech persists slowly.
        return await real_record(speaker, **kwargs)

    rt.record_line = slow_record
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "被取消也要发出去", "source": "neko_visit:guest_cat"}))
        await wait_for(lambda: rt.outbox.reserved_bytes > 0)  # Already accepted and room mutated.
        sending.cancel()                                      # Handler canceled, e.g. during shutdown.
        await asyncio.gather(sending, return_exceptions=True)
        assert [t for t in rtm._detached if not t.done()]     # Register ongoing commits at module level for shutdown collection.
        stuck.set()
        await rt.flush()
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "text" and p.get("sp") == "h"],
                       timeout=5)                             # Still persist, enqueue, and send to the peer.
        await wait_for(lambda: "human" in rt._line_kinds.values())  # Local history contains it too.
        await wait_for(lambda: [f for f in host.host.frames
                                if f.get("type") == "visit_line" and f.get("text") == "被取消也要发出去"])  # Also displayed.
        await wait_for(lambda: "被取消也要发出去" in host.host.user_inputs)  # Also mirrored into the main session.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_cancelled_family_line_to_the_own_cat_still_schedules_its_reply(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    real_record = rt.record_line
    plans: list = []

    async def slow_record(speaker, **kwargs):
        if speaker == "own_human":
            await stuck.wait()
        return await real_record(speaker, **kwargs)

    rt.record_line = slow_record
    rt.schedule_reply = plans.append
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "跟自家猫说", "source": "neko_visit:own_cat"}))
        await wait_for(lambda: rt.outbox.reserved_bytes > 0)
        sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)
        stuck.set()
        await wait_for(lambda: [p for p in plans if p is not None])  # Handler exited, but replies still queue.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_waits_for_an_admitted_family_line_before_sealing(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    real_record = rt.record_line
    order: list[str] = []

    async def slow_record(speaker, **kwargs):
        if speaker == "own_human":
            await stuck.wait()
            inner = kwargs.get("on_journaled")

            def journaled():
                order.append("recorded")                      # Record upload log; sealing needs only this.
                if inner is not None:
                    inner()

            kwargs["on_journaled"] = journaled
        return await real_record(speaker, **kwargs)

    real_seal = rt.journal.seal

    async def seal(reason, **kw):
        order.append("seal")
        return await real_seal(reason, **kw)

    rt.record_line = slow_record
    rt.journal.seal = seal
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "关机前这句", "source": "neko_visit:guest_cat"}))
        await wait_for(lambda: rt.outbox.reserved_bytes > 0)
        asyncio.get_running_loop().call_later(0.3, stuck.set)  # Persistence takes some time.
        await asyncio.wait_for(rtm.stop_all("shutdown"), 10)
        await asyncio.gather(sending, return_exceptions=True)
        assert "recorded" in order and "seal" in order
        assert order.index("recorded") < order.index("seal")  # Record transcript before sealing.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_settling_family_records_does_not_wait_for_the_mirror(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    mirror_stuck = asyncio.Event()

    async def stuck_mirror(text, *, metadata, request_id):
        await mirror_stuck.wait()                             # Main-session mirroring stuck.

    host.host.mirror_user_input = stuck_mirror
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "镜像卡住", "source": "neko_visit:guest_cat"}))
        await rt.flush()
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "text" and p.get("sp") == "h"])
        await asyncio.wait_for(rt.settle_family_records(5), 1)  # Wait only for transcripts, not mirroring.
        assert not rt.family_records
        assert not sending.done()                             # Mirroring is indeed still blocked.
    finally:
        mirror_stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_the_normal_seal_waits_for_an_admitted_family_line(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    real_record = rt.record_line
    order: list[str] = []

    async def slow_record(speaker, **kwargs):
        if speaker == "own_human":
            await stuck.wait()
            inner = kwargs.get("on_journaled")

            def journaled():
                order.append("recorded")                      # Record upload log; sealing needs only this.
                if inner is not None:
                    inner()

            kwargs["on_journaled"] = journaled
        return await real_record(speaker, **kwargs)

    real_seal = rt.journal.seal

    async def seal(reason, **kw):
        order.append("seal")
        return await real_seal(reason, **kw)

    rt.record_line = slow_record
    rt.journal.seal = seal
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "收尾前这句", "source": "neko_visit:guest_cat"}))
        await wait_for(lambda: rt.outbox.reserved_bytes > 0)
        asyncio.get_running_loop().call_later(0.3, stuck.set)  # Persistence takes some time.
        await asyncio.wait_for(rt.seal_and_finalize("peer_left"), 15)  # Normal finalization after peer departure, without waiting for reservations.
        await asyncio.gather(sending, return_exceptions=True)
        assert "recorded" in order and "seal" in order
        assert order.index("recorded") < order.index("seal")  # Record transcript before sealing.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_failure_after_a_family_line_is_sent_does_not_report_busy(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt

    def broken_history(ln, lp, text):
        raise RuntimeError("history broke")

    rt._add_own_human_history = broken_history
    try:
        await rtm.route_stream_message("Host", {
            "input_type": "text", "data": "已经发出", "source": "neko_visit:guest_cat", "request_id": "r1"})
        await rt.flush()
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "text" and p.get("sp") == "h"])
        await wait_for(lambda: [f for f in host.host.frames
                                if f.get("type") == "visit_line" and f.get("text") == "已经发出"])  # Display unaffected.
        assert "已经发出" in host.host.user_inputs              # Mirroring unaffected too.
        assert "VISIT_E_BUSY" not in host.host.status_codes()  # Once sent, never return busy or users may resend.
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_family_line_recorded_after_the_outbox_closed_is_not_sent(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    real_record = rt.record_line

    async def slow_record(speaker, **kwargs):
        if speaker == "own_human":
            await stuck.wait()                                # Persistence remains blocked longer than finalization.
        return await real_record(speaker, **kwargs)

    rt.record_line = slow_record
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "迟到的一句", "source": "neko_visit:guest_cat"}))
        await wait_for(lambda: rt.outbox.reserved_bytes > 0)
        await rt.outbox.close()                               # Finalization already closed outbox.
        stuck.set()
        await asyncio.gather(sending, return_exceptions=True)
        await rt.flush()
        await settle()
        assert not [p for p in wire.sent["host"] if p.get("t") == "text" and p.get("sp") == "h"]
        assert not await asyncio.to_thread(rt.outbox.path.exists)
        assert rt.outbox.reserved_bytes == 0                  # Still release reservations.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_settling_family_records_does_not_wait_for_the_spool(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    assert rt.memory_enabled and rt.spool is not None and rt.spool.is_open
    spool_stuck = asyncio.Event()
    real_append = rt.spool.append

    async def stuck_append(record):
        if record.get("from") == "own_human":
            await spool_stuck.wait()                          # Spool persistence blocked.
        return await real_append(record)

    rt.spool.append = stuck_append
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "spool 卡住", "source": "neko_visit:guest_cat"}))
        await wait_for(lambda: any(r.get("text") == "spool 卡住" for r in rt.journal._records))  # Upload log already recorded.
        await asyncio.wait_for(rt.settle_family_records(5), 1)  # Wait only for upload logs, not spool.
        assert not rt.family_records and not sending.done()
    finally:
        spool_stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_family_commit_cancelled_before_it_runs_still_settles(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    real_ensure = asyncio.ensure_future

    def ensure_future(coro, **kw):
        task = real_ensure(coro, **kw)
        if getattr(coro, "__qualname__", "").endswith("._send_human_line.<locals>.commit"):
            task.cancel()                                     # Canceled before execution starts, e.g. event-loop shutdown.
        return task

    monkeypatch.setattr(rtm_talk.asyncio, "ensure_future", ensure_future)
    try:
        sending = real_ensure(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "没跑起来", "source": "neko_visit:guest_cat"}))
        await asyncio.gather(sending, return_exceptions=True)
        monkeypatch.undo()
        await settle()
        assert not rt.family_records                          # Still finish so later sealing does not wait pointlessly.
        assert rt.outbox.reserved_bytes == 0                  # Release reservations too.
    finally:
        monkeypatch.undo()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_the_family_wait_and_the_seal_share_one_deadline(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck, seal_stuck = asyncio.Event(), asyncio.Event()
    real_record = rt.record_line
    real_seal = rt.journal.seal

    async def slow_record(speaker, **kwargs):
        if speaker == "own_human":
            await stuck.wait()                                # This sentence never persists.
        return await real_record(speaker, **kwargs)

    async def slow_seal(reason, **kw):
        await seal_stuck.wait()                               # Sealing stuck too.
        return await real_seal(reason, **kw)

    rt.record_line = slow_record
    rt.journal.seal = slow_seal
    monkeypatch.setattr(rtm, "_SEAL_MAX_S", 1.0)
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "卡住的一句", "source": "neko_visit:guest_cat"}))
        await wait_for(lambda: rt.outbox.reserved_bytes > 0)
        loop = asyncio.get_running_loop()
        started = loop.time()
        assert await asyncio.wait_for(rt.seal_and_finalize("peer_left"), 5) is False
        assert loop.time() - started < 1.6                    # Combined waits must not exceed one _SEAL_MAX_S.
        sending.cancel()
    finally:
        stuck.set()
        seal_stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_the_mirrored_family_line_is_the_wire_fitted_text(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    real_fit = rtm_talk.fit_text_to_wire

    def fit(payload, **kw):
        out = real_fit(payload, **kw)
        if out.get("sp") == "h":
            out = dict(out, txt="裁短了", truncated=True)       # Too large for the wire: truncated.
        return out

    monkeypatch.setattr(rtm_talk, "fit_text_to_wire", fit)
    try:
        await rtm.route_stream_message("Host", {
            "input_type": "text", "data": "一句很长很长的话", "source": "neko_visit:guest_cat"})
        await wait_for(lambda: host.host.user_inputs)
        assert host.host.user_inputs[-1] == "裁短了"             # Mirror matches peer/transcript text, not the pretruncation original.
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_reliable_message_is_handled_even_if_its_receive_is_cancelled(tmp_path, monkeypatch):
    from main_routers.visit_router import runtime_rx

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    handled: list[str] = []

    async def slow_wrap_up(rt_, m, from_vid, now):
        await stuck.wait()                                    # Processing awaits for a while.
        handled.append(m["t"])

    monkeypatch.setitem(runtime_rx._HANDLERS, "wrap_up", slow_wrap_up)
    try:
        seq = rt.sequencer.contiguous_seq + 1
        receiving = asyncio.ensure_future(rt.on_recv(from_vid=GUEST_VID, cmd=1, payload={
            "t": "wrap_up", "v": 1, "seq": seq, "lp": rt.room.max_lp_seen, "ph": "propose", "reason": "recall",
            "initiated_by": "guest"}, nbytes=200))
        await wait_for(lambda: rt.sequencer.contiguous_seq == seq)  # Sequencing accepted it, so the peer will not retransmit.
        receiving.cancel()                                    # Reception handler canceled by disconnection.
        await asyncio.gather(receiving, return_exceptions=True)
        stuck.set()
        await wait_for(lambda: handled == ["wrap_up"])        # Still complete processing without permanent loss.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_receives_waiting_on_the_same_batch_are_still_handled_one_by_one(tmp_path, monkeypatch):
    from main_routers.visit_router import runtime_rx

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    gates: dict[int, asyncio.Event] = {}
    running: list[int] = []
    overlaps: list[tuple] = []

    async def slow_wrap_up(rt_, m, from_vid, now):
        if running:
            overlaps.append((tuple(running), m["seq"]))       # Next message begins before previous processing completes.
        running.append(m["seq"])
        await gates.setdefault(m["seq"], asyncio.Event()).wait()
        running.remove(m["seq"])

    monkeypatch.setitem(runtime_rx._HANDLERS, "wrap_up", slow_wrap_up)
    for st in rt.limiter._senders.values():                  # Remove rate limits to isolate delivery order.
        for bucket in (st.recv_bytes, st.recv_msgs, st.ctl, st.lossy, st.text):
            bucket.rate = bucket.capacity = bucket.tokens = 10 ** 9

    def wrap_up(seq):
        return rt.on_recv(from_vid=GUEST_VID, cmd=1, payload={
            "t": "wrap_up", "v": 1, "seq": seq, "lp": rt.room.max_lp_seen, "ph": "propose", "reason": "recall",
            "initiated_by": "guest"}, nbytes=200)

    try:
        base = rt.sequencer.contiguous_seq
        first = asyncio.ensure_future(wrap_up(base + 1))
        await wait_for(lambda: running == [base + 1])
        later = [asyncio.ensure_future(wrap_up(base + 2)), asyncio.ensure_future(wrap_up(base + 3))]
        await settle()                                        # Both receives await the same batch.
        for seq in (base + 1, base + 2, base + 3):
            gates.setdefault(seq, asyncio.Event()).set()
            await settle()
        await asyncio.wait_for(asyncio.gather(first, *later), 5)
        assert overlaps == []                                 # Process batches serially, never concurrently.
        assert rt.sequencer.contiguous_seq == base + 3
    finally:
        for g in gates.values():
            g.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_waits_for_a_received_batch_before_sealing(tmp_path, monkeypatch):
    from main_routers.visit_router import runtime_rx

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    order: list[str] = []
    stuck = asyncio.Event()

    async def slow_wrap_up(rt_, m, from_vid, now):
        await stuck.wait()                                    # Accepted message still processing.
        order.append("handled")

    monkeypatch.setitem(runtime_rx._HANDLERS, "wrap_up", slow_wrap_up)
    real_seal = rt.journal.seal

    async def seal(reason, **kw):
        order.append("seal")
        return await real_seal(reason, **kw)

    rt.journal.seal = seal
    try:
        receiving = asyncio.ensure_future(rt.on_recv(from_vid=GUEST_VID, cmd=1, payload={
            "t": "wrap_up", "v": 1, "seq": rt.sequencer.contiguous_seq + 1, "lp": rt.room.max_lp_seen,
            "ph": "propose", "reason": "recall", "initiated_by": "guest"}, nbytes=200))
        await wait_for(lambda: rt._delivering is not None and not rt._delivering.done())
        asyncio.get_running_loop().call_later(0.2, stuck.set)
        await asyncio.wait_for(rtm.stop_all("shutdown"), 10)
        await asyncio.gather(receiving, return_exceptions=True)
        assert "handled" in order and "seal" in order
        assert order.index("handled") < order.index("seal")   # Finish accepted batches before sealing.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_settling_deliveries_also_waits_for_the_batch_queued_behind(tmp_path, monkeypatch):
    from main_routers.visit_router import runtime_rx

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    gates: dict[int, asyncio.Event] = {}
    handled: list[int] = []

    async def slow_wrap_up(rt_, m, from_vid, now):
        await gates.setdefault(m["seq"], asyncio.Event()).wait()
        handled.append(m["seq"])

    monkeypatch.setitem(runtime_rx._HANDLERS, "wrap_up", slow_wrap_up)
    for st in rt.limiter._senders.values():                  # Remove rate limits to isolate delivery order.
        for bucket in (st.recv_bytes, st.recv_msgs, st.ctl, st.lossy, st.text):
            bucket.rate = bucket.capacity = bucket.tokens = 10 ** 9

    def wrap_up(seq):
        return rt.on_recv(from_vid=GUEST_VID, cmd=1, payload={
            "t": "wrap_up", "v": 1, "seq": seq, "lp": rt.room.max_lp_seen, "ph": "propose", "reason": "recall",
            "initiated_by": "guest"}, nbytes=200)

    try:
        base = rt.sequencer.contiguous_seq
        first = asyncio.ensure_future(wrap_up(base + 1))
        await wait_for(lambda: rt._delivering is not None and not rt._delivering.done())
        queued = asyncio.ensure_future(wrap_up(base + 2))    # Later arrival waits inside on_recv.
        await settle()
        settling = asyncio.ensure_future(rt._settle_delivering(5))  # Presealing wait.
        await settle()
        gates.setdefault(base + 1, asyncio.Event()).set()     # First batch finishes; queued arrival wakes first and yields batch two.
        await settle()
        assert not settling.done()                            # Still awaiting batch two.
        gates.setdefault(base + 2, asyncio.Event()).set()
        await asyncio.wait_for(settling, 5)
        assert handled == [base + 1, base + 2]                # Both batches finish before sealing.
        await asyncio.gather(first, queued)
    finally:
        for g in gates.values():
            g.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_receive_waiting_when_the_receive_path_closes_takes_nothing(tmp_path, monkeypatch):
    from main_routers.visit_router import runtime_rx

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    handled: list[int] = []

    async def slow_wrap_up(rt_, m, from_vid, now):
        await stuck.wait()
        handled.append(m["seq"])

    monkeypatch.setitem(runtime_rx._HANDLERS, "wrap_up", slow_wrap_up)
    for st in rt.limiter._senders.values():
        for bucket in (st.recv_bytes, st.recv_msgs, st.ctl, st.lossy, st.text):
            bucket.rate = bucket.capacity = bucket.tokens = 10 ** 9

    def wrap_up(seq):
        return rt.on_recv(from_vid=GUEST_VID, cmd=1, payload={
            "t": "wrap_up", "v": 1, "seq": seq, "lp": rt.room.max_lp_seen, "ph": "propose", "reason": "recall",
            "initiated_by": "guest"}, nbytes=200)

    try:
        base = rt.sequencer.contiguous_seq
        first = asyncio.ensure_future(wrap_up(base + 1))
        await wait_for(lambda: rt._delivering is not None and not rt._delivering.done())
        queued = asyncio.ensure_future(wrap_up(base + 2))    # Waiting behind earlier work.
        await settle()
        rt._rx_closed = True                                  # Finalization establishes the sealing boundary.
        stuck.set()
        await asyncio.wait_for(asyncio.gather(first, queued), 5)
        assert handled == [base + 1]                          # Process arrivals accepted before it normally.
        assert rt.sequencer.contiguous_seq == base + 1        # Later arrivals never enter sequencing or start delivery.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_closing_the_session_hands_a_stray_generation_to_stop_all(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stray = asyncio.get_running_loop().create_future()        # Abandoned generation still running.
    rt.session.stray = stray
    try:
        rt._close_session_in_background()
        assert stray in rtm._detached                         # It may outlive session closure; transfer to module-level registration.
    finally:
        stray.cancel()
        rt.session.stray = None
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_stalled_own_line_booking_is_registered_for_stop_all(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch,
                                                    guest_replies=Replies(gate=asyncio.Event()))
    rt = host.rt
    stuck = asyncio.Event()
    real_append = rt.spool.append

    async def stalled_append(record):
        if record.get("from") == "own_cat":
            await stuck.wait()                                # Local speech spool write blocked.
        return await real_append(record)

    rt.spool.append = stalled_append

    def bookings():
        return [t for t in rtm._detached if not t.done()
                and getattr(t.get_coro(), "__qualname__", "").endswith("record_line")]

    try:
        await wait_for(lambda: rt._line is None, timeout=10)  # Opening line already finalized.
        rt.schedule_reply(None)                               # Another line now blocks on spool writing.
        await wait_for(lambda: bookings(), timeout=10)        # Register protected accounting tasks at module level for shutdown collection.
    finally:
        stuck.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_family_line_is_admitted_synchronously_before_its_commit_runs(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    seen: list = []
    real_apply = rt.apply_effects

    def apply_effects(effects):
        seen.append(asyncio.current_task())
        return real_apply(effects)

    rt.apply_effects = apply_effects
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "同步接纳", "source": "neko_visit:guest_cat"}))
        assert await sending is True                          # This visit's route accepted the sentence.
        assert seen and seen[0] is sending                    # Acceptance completes synchronously in handlers, not deferred to persistence tasks.
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_an_admitted_family_line_goes_out_before_leave(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()
    real_record = rt.record_line

    async def slow_record(speaker, **kwargs):
        if speaker == "own_human":
            await stuck.wait()                                # Only family speech persists slowly.
        return await real_record(speaker, **kwargs)

    rt.record_line = slow_record
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "我们先走啦", "source": "neko_visit:guest_cat"}))
        await wait_for(lambda: rt.outbox.reserved_bytes > 0)
        rt.request_finalize("delivery_failed")                # Begin finalization that sends leave directly without draining.
        await asyncio.sleep(0.2)
        stuck.set()
        await asyncio.gather(sending)
        await finish(rt, clock)
        sent = [p.get("t") for p in wire.sent["host"]]
        humans = [i for i, p in enumerate(wire.sent["host"]) if p.get("t") == "text" and p.get("sp") == "h"]
        assert humans and "leave" in sent and humans[0] < sent.index("leave")   # This sentence queues before leave.
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


def test_reply_runner_has_no_await_between_admission_and_taking_the_line():
    # No await between admission checks and self._line=line, or another runner can enter during suspension and speak concurrently.
    import inspect
    import re

    runner = inspect.getsource(rtm_talk.TalkMixin._reply_runner)
    # From successful admission break to speak_line entry; loop sleeps return to admission checks and do not count.
    after = runner[runner.index("if self._reply_task is me:"):]
    assert re.findall(r"\bawait\b", after[:after.index("await self.speak_line(")]) == []
    speak = inspect.getsource(rtm_talk.TalkMixin.speak_line)
    assert "await" not in speak[:speak.index("self._line = line")]


async def test_voice_switched_mid_visit_applies_from_the_next_line(tmp_path, monkeypatch):
    # Section 3.7.6: visitVoiceEnabled changes apply next sentence, without overwriting one-way TTS fallback from reread settings.
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    try:
        first_ln = host.rt._line.ref.line_id
        assert rtm.apply_voice_setting(False) == 2            # Opening already started streaming with voice enabled.
        hgate.set()
        ggate.set()
        await wait_for(lambda: len(_texts(wire, "host")) >= 2, timeout=10)
        lns = [p["ln"] for p in _texts(wire, "host")]
        streamed = {s.request_id for s in host.host.streams}
        assert first_ln in streamed
        assert not (set(lns) - {first_ln}) & streamed         # All later sentences display silently.
        assert host.rt.voice.enabled is False
        host.rt.voice.fallen_back = True
        rtm.apply_voice_setting(True)
        assert host.rt.voice.enabled is True and host.rt.voice.tts_on is False   # Do not reverse the fallback.
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)
