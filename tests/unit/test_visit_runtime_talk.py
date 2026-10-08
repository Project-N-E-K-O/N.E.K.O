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
        assert "".join(stream.pushed) == "小明今天也很开心。我们玩吧！"     # TTS 收原文
        assert stream.finished
        outbound = [p.get("txt", "") for p in wire.sent["host"] if p.get("t") in ("line_delta", "text")]
        assert outbound and all("小明" not in t for t in outbound)
        final = _texts(wire, "host")[0]
        deltas = "".join(p["txt"] for p in wire.sent["host"] if p.get("t") == "line_delta" and p["ln"] == final["ln"])
        assert deltas == final["txt"] and final["truncated"] is False
        assert len([s for s in host.host.streams if s.request_id == final["ln"]]) == 1
        # 本侧台词也进 spool 与上传流水（同一前缀）
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
        # 正在想的开场行被人类行打断：它的 text{truncated} 排在人类行之前
        assert order.index("send:text:c:human_interrupt") < order.index("send:text:h:")
        assert host.host.events[-1] == "mirror_user_input" and host.host.user_inputs == ["你们好呀"]
        assert rt.outbox.pending_bytes >= 0
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
        assert rt._line is line and not line.speaker.done          # 正在说的那行没被打断
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
        # 收尾期间：WRAPUP，不是 NOT_READY
        host.rt.apply_effects(host.rt.room.on_local_recall(host.rt.clock()))
        await rtm.route_stream_message("Host", {"input_type": "text", "data": "再见", "request_id": "w1"})
        assert host.host.statuses[-1][0] == "VISIT_INPUT_REFUSED_WRAPUP"
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
        # 之后前端因清管线回报 ended：不会把未念的文字当作播完放出
        await rtm.on_page_signal("Host", {"speech_id": stream.speech_id, "played_ms": 99999, "ended": True,
                                          "final": True})
        await settle()
        assert all("第二" not in p.get("txt", "") for p in wire.sent["host"])
        await wait_for(lambda: rt._line is None)
        history = [getattr(m, "content", "") for m in host.clients[0]._conversation_history]
        assert any(c.startswith("第一句话。") and "第二" not in c and c != "第一句话。" for c in history)
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
        assert rt.sequencer.contiguous_seq == seq                       # 照常按 seq 推进（会回 ack）
        assert not [f for f in host.host.frames[before_frames:] if str(f.get("type")).startswith("visit_line")]
        assert rt.anomaly_count() >= anomalies + 2
        assert not [r for r in rt.journal.lines() if r["text"] == "冒充"]
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_peer_text_flood_is_acked_dropped_and_ends_the_visit(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    rt.limiter._recv_bps = 10 ** 9            # 只看 text 令牌桶，帧级字节桶放开
    for st in rt.limiter._senders.values():
        st.recv_bytes.rate = st.recv_bytes.capacity = st.recv_bytes.tokens = 10 ** 9
        st.recv_msgs.rate = st.recv_msgs.capacity = st.recv_msgs.tokens = 10 ** 9
    rt.limiter._recv_msgs_per_s = 10 ** 9
    seq = rt.sequencer.contiguous_seq
    try:
        for i in range(200):
            seq += 1
            # 对端人类行：不计入 80 行违约守卫，只受 text 令牌桶与整场上限约束
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
    # 两侧都以 lp=1 开场，guest 那句先说完、先到 host：两侧下一轮喂给 LLM 的历史都是「host 句、guest 句」
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
        # 历史里只有真实台词：上一轮的提问（到达通知）已摘掉
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
        assert "wrap_up" in wire.sent_types("guest")              # 告别行开口前先发 wrap_up{speaking}
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
        # 收口这一行期间接管还在：输入仍归串门（拒掉），释放接管之后才交还普通聊天
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
        forged = "再见" * 200                                # 对端不守 LineSpeaker 的 40 字上限
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
        rt.liveness.peer_last_seen = now - 25               # 心跳快到 30 s 判死
        rt._on_peer_presence(False, 1, now)                 # 超时类断开：不起重入宽限
        assert rt.liveness.peer_departed_at is None
        rt._on_peer_presence(True, None, now + 1)           # vendor 又确认对端在场
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
        shown = [f for f in host.host.frames[before:] if str(f.get("type")).startswith("visit_line")]
        assert shown == []                                   # 增量与整行都不上屏
        assert len(text_calls) == 1                          # 一行只取一次配额
        assert rt.rate_dropped == dropped + 1
        assert not [r for r in rt.journal.lines() if r["text"] == "片0片1"]
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)
