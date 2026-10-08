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
        await settle()  # 页面帧经显示队列异步发出
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
        await settle()  # 页面帧经显示队列异步发出
        shown = [f for f in host.host.frames[before:] if str(f.get("type")).startswith("visit_line")]
        assert shown == []                                   # 增量与整行都不上屏
        assert len(text_calls) == 1                          # 一行只取一次配额
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
        for ln, lp in (("g:60", 60), ("g:61", 61)):          # 旧行还没收口就开了新行
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
        assert asked == [family + cut] and cut > 0          # 被打断那行的 final 一并预留
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
            far = rt.room.max_lp_seen + VISIT_LP_MAX_JUMP + 100 + i     # 跳得太远：lp_out_of_range
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
        for i in range(n):                                    # 先把这些行都按「人类」开口
            await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
                "t": "line_delta", "v": 1, "ln": f"g:{400 + i}", "i": 0, "lp": 400 + i, "txt": "嗯", "sp": "h",
                "ad": "hc", "rt": "", "wu": False}, nbytes=200)
        for i in range(n):
            ln, lp = f"g:{400 + i}", 400 + i
            # 收口改成猫娘：同一行两种解释，按协议异常丢弃；连续这样就按协议违约结束
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
        monkeypatch.setattr(rt.outbox, "leave_done", lambda now=None: True)   # 本侧 leave 已确认 / 宽限到点
        owed = {"seq": 7}
        monkeypatch.setattr(rt.sequencer, "poll_ack", lambda now, force=False: owed.pop("seq", None))
        await rt._close_channel("peer_left")
        acks = [p for p in wire.sent["host"] if p.get("t") == "ack"]
        assert acks and acks[-1]["seq"] == 7                    # 对端的 leave 不用白等补传窗口
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_early_speaking_wrap_ups_with_bad_lp_count_toward_the_cutoff(tmp_path, monkeypatch):
    from config.visit_settings import VISIT_ANOMALY_FINALIZE_COUNT, VISIT_LP_MAX_JUMP

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    for st in rt.limiter._senders.values():                  # 只看违约计数：ctl 4/s 的限速放开
        st.ctl.rate = st.ctl.capacity = st.ctl.tokens = 10 ** 9
        st.recv_msgs.rate = st.recv_msgs.capacity = st.recv_msgs.tokens = 10 ** 9
    rt.limiter._recv_msgs_per_s = 10 ** 9
    try:
        before = rt.journal._anomalies
        for i in range(VISIT_ANOMALY_FINALIZE_COUNT + 2):
            far = rt.room.max_lp_seen + VISIT_LP_MAX_JUMP + 100 + i
            # wrap_up{speaking} 一律走提前交付，不经过 _rx_wrap_up
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
        assert all(ids) and len(set(ids)) == len(ids)       # 页面按 line_id 建气泡：不能塌成一个
        matched = [r for r in replay if key(r) in live]
        assert len(matched) >= 2
        assert all(r["line_id"] == live[key(r)] for r in matched)   # 与直播时同一行同一个 id
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
        assert rt.room.violation_streak == 0                 # 合法的这一条同样算「中间有过正常消息」
        monkeypatch.setattr(rt.room, "on_incoming_wrap_up", lambda *a, **k: RoomEffects(violation="wrap_up_order"))
        rt.room.violation_streak = 3
        rt._on_early_wrap_up({"t": "wrap_up", "ph": "speaking", "lp": rt.room.max_lp_seen + 1, "ln": "g:901",
                              "reason": "quiet"}, clock())
        assert rt.room.violation_streak == 3                 # 本身违约的不清零
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
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload=delta(0, "改写的"), nbytes=200)   # 同一个 i 重放
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:70", "lp": 70, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
            "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "原本的", "truncated": False, "i_done": 1,
        }, nbytes=200)
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload=delta(1, "收口之后"), nbytes=200)  # 已收口
        await settle()  # 页面帧经显示队列异步发出
        shown = [f["text"] for f in host.host.frames[before:] if f.get("type") == "visit_line_delta"]
        assert shown == ["原本的"]
        assert rt.journal._anomalies == anomalies + 1          # 重放计一次异常；收口后的晚到静默丢
    finally:
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_soft_decoder_anomalies_are_counted_without_the_streak(tmp_path, monkeypatch):
    from config.visit_settings import VISIT_ANOMALY_FINALIZE_COUNT

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt.room.violation_streak = VISIT_ANOMALY_FINALIZE_COUNT - 1    # 再来一次连续违约就收尾
        journal, room, streak = rt.journal._anomalies, rt.room.anomalies_total, rt.room.violation_streak
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:80", "lp": 80, "seq": rt.sequencer.contiguous_seq + 1, "sp": "c",
            "ad": "hc", "rt": "", "wu": False, "final": True, "txt": "你好", "truncated": False, "i_done": 0,
            "tail_ms": 999999,
        }, nbytes=200)
        assert rt.journal._anomalies == journal + 1 and rt.room.anomalies_total == room + 1
        assert rt.room.violation_streak <= streak                # 消息本身照常处理，不算进连续违约
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
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload=delta("g:61", 60), nbytes=200)   # 同 lp 又开一行
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
        await settle()  # 页面帧经显示队列异步发出
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
            # ready 入队前一刻：激活已完成、阶段已是 active，亲人这时打的字必须被拒
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
        assert rt.exit_task is None                         # 同房第三人刷爆自己的桶：结束不了这场
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
    calls, sent = [], []
    monkeypatch.setattr(rt.room, "on_wrap_up_sent", lambda ph, now: calls.append(ph))
    monkeypatch.setattr(rt.liveness, "on_message_sent", lambda now: sent.append(now))
    try:
        rt.on_frame_sent(SimpleNamespace(seq=9, t="wrap_up", retransmit=False, payload={"ph": "begin"}))
        assert calls == ["begin"] and len(sent) == 1        # 收尾步骤计时器照常起，存活计时也记上
        rt.on_frame_sent(SimpleNamespace(seq=9, t="wrap_up", retransmit=True, payload={"ph": "begin"}))
        assert calls == ["begin"]
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
            nbytes=200)                                      # 迟到 / 重放的 abort
        await settle()  # 页面帧经显示队列异步发出
        assert not [f for f in host.host.frames[before:] if f.get("type") == "visit_line_abort"]
        # 被接受的 abort 之后，那一行的分片不再上屏
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
        await settle()  # 页面帧经显示队列异步发出
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
        # begin（seq+1）丢了；speaking（seq+2）先到、提前交付
        await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload={
            "t": "wrap_up", "v": 1, "seq": seq + 2, "lp": lp + 11, "ph": "speaking", "ln": "g:600",
            "reason": "quiet", "initiated_by": "host"}, nbytes=200)
        # 缺口补到：同一发送方更早的 begin，lp 更小
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
        await stuck.wait()                                   # 页面 socket 背压：每次写都卡住
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

        await asyncio.wait_for(burst(), 2)                   # 收包路径不等页面
        assert rt.display_dropped > 0                        # 积压时丢的是字幕分片
        stuck.set()
        await wait_for(lambda: any(f.get("type") == "visit_line" and f.get("line_id") == "g:73"
                                   for f in host.host.frames))   # 整句从不丢
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
        # spool 还卡着，关机这时封存：上传记录里已经有这一行
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
            await stuck.wait()                               # 只有显示队列这一路卡住
        return await real_send(payload)

    host.host.send_frame = slow_send
    try:
        for i in range(10):                                   # 队首是整句：满了也不能先丢它们
            rt._post_display({"type": "visit_line", "line_id": f"g:{i}"})
        for i in range(50):
            rt._post_display({"type": "visit_line_delta", "line_id": f"g:{i}", "i": 0}, droppable=True)
        for i in range(10, 250):
            rt._post_display({"type": "visit_line", "line_id": f"g:{i}"})
        queued = list(rt._display)
        finals = [f for f in queued if f["type"] == "visit_line"]
        # 满了先丢最旧的字幕分片；整句一条不丢（整句受每场句数上限约束，堆不到无限）
        assert len(finals) == 250 and len(queued) == 256
        assert len([f for f in queued if f["type"] == "visit_line_delta"]) == 6
        task = rt._display_task
        rt.request_finalize("route_end")
        await finish(rt, clock)
        await settle()
        assert not rt._display and task.cancelled()          # 场次结束：队列清空、发帧任务停掉
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
        await stuck.wait()                                   # 磁盘慢：落盘卡住

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
        await wait_for(lambda: produced and produced[0] in applied)   # 这一行的停嘴 / 取消待发回复不等落盘
        assert not receiving.done()
        assert produced[0].reply is None and not produced[0].say_goodbye  # 回复留到进了历史之后
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
        assert failed["truncated"] is True and failed["txt"]     # 有前缀也标截断，不当成说完的一句
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


async def test_no_display_frame_after_the_visit_ended(tmp_path, monkeypatch):
    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt
    try:
        rt.request_finalize("route_end")
        await finish(rt, clock)
        before = len(host.host.frames)
        rt._post_display({"type": "visit_line", "line_id": "g:1"})   # 慢落盘的 _rx_text 这时才回来
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
        await stub.turn_lock.acquire()                        # 收尾时没停下的那一轮还占着锁
        out = await asyncio.wait_for(rt.one_shot_turn("回家说一句", timeout=0.2), 3)
        assert out is None                                    # 用固定句，不无限等
    finally:
        rt.session = real_session
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)



async def test_a_ceremony_turn_shares_one_deadline_between_lock_and_generation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    host, guest, wire, clock, hgate, ggate = await _gated(tmp_path, monkeypatch)
    rt = host.rt

    async def slow_stream(text, **kwargs):
        await asyncio.sleep(2)

    # 替身会话：只看 one_shot_turn 自己的计时，不受正在进行的那一轮占锁影响
    stub = SimpleNamespace(turn_lock=asyncio.Lock(), history=[], client=SimpleNamespace(stream_text=slow_stream),
                           set_sink=lambda sink: None, forget_untracked=lambda: None)
    real_session, rt.session = rt.session, stub
    try:
        await stub.turn_lock.acquire()
        asyncio.get_running_loop().call_later(0.45, stub.turn_lock.release)   # 锁快到期限才放出来
        started = asyncio.get_running_loop().time()
        out = await asyncio.wait_for(rt.one_shot_turn("回家说一句", timeout=0.5), 3)
        # 整轮不超过一个时限（各给一个完整时限就会到 ~0.95 s）
        assert out is None and asyncio.get_running_loop().time() - started < 0.75
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
                assert replay[ln][key] == live[ln][key]       # 重载后与直播时同一份形状
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
        llm.cancel = lambda *a, **k: False                    # LLM 协程不肯停（还占着会话锁）
        rt.interrupt_line("human_interrupt")
        await wait_for(lambda: [r for r in rt.journal.lines() if r["from"] == "own_cat" and r["truncated"]],
                       timeout=3)                             # 已发出的那行照样进转录
        await wait_for(lambda: line.task.done(), timeout=3)   # 这一行的任务本身也收得了尾，不卡在入史等锁上
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
                continue                                      # 吞掉取消：到点了也不停

    monkeypatch.setattr(harness.FakeClient, "stream_text", stubborn)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    try:
        await wait_for(lambda: rt._line is not None)
        line = rt._line
        await wait_for(lambda: line.task.done(), timeout=3)   # 到点就收口，不等不肯停的生成
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
    calls: dict = {}

    async def stream_text(self, text, **kw):
        calls[id(self)] = calls.get(id(self), 0) + 1
        if calls[id(self)] > 1:
            await real_stream(self, text, **kw)
            return
        self._conversation_history.append(HumanMessage(content=text))
        while not release.is_set():
            try:
                await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                continue                                      # 吞掉取消，到点后还在跑
        await self.on_text_delta("串话", True)                 # 被撇下之后才出的字
        self._conversation_history.append(AIMessage(content="幽灵回复"))

    monkeypatch.setattr(harness.FakeClient, "stream_text", stream_text)
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    session = rt.session
    try:
        await wait_for(lambda: session.stray is not None, timeout=3)   # 开场那行到点，生成被撇下
        await wait_for(lambda: rt._line is None, timeout=3)
        rt.schedule_reply(None)                               # 撇下的还在跑时轮到下一行
        await wait_for(lambda: rt._line is not None, timeout=3)
        nxt = rt._line
        await wait_for(lambda: nxt.task.done(), timeout=3)
        assert nxt.llm_error == "timeout"                     # 不开新流（同一个 client 上不并发两次生成）
        assert calls[id(session.client)] == 1
        assert rt.finalize_reason == "llm_error"              # 等满一个时限还停不下：这场说不了话，按 llm_error 收尾
        release.set()                                         # 撇下的那次这时吐字、往历史里追加
        await wait_for(lambda: session.stray is None or session.stray.done(), timeout=3)
        await settle()
        contents = [getattr(m, "content", "") for m in session.history]
        assert not [c for c in contents if "幽灵回复" in str(c)]  # 它追加的回复被摘掉
        own = [r["text"] for r in rt.journal.lines() if r["from"] == "own_cat"]
        assert not [t for t in own if "串话" in t]             # 它的字没进任何一行
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
        assert text is None                                   # 到点按失败收口，退出流程不被卡住
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
        caller.cancel()                                       # 收尾取消这一轮
        await asyncio.sleep(0.05)                             # 它正在等不肯停的流停下
        caller.cancel()                                       # 再被取消一次
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert session.stray is not None and not session.stray.done()   # 照样记在会话上，下一轮会先等它
    finally:
        release.set()
        await asyncio.sleep(0.05)


async def test_own_line_frames_reach_the_page_in_order(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    rt = host.rt
    real_send = host.host.send_frame
    slowed = []

    async def send_frame(payload):
        if payload.get("type") == "visit_line_delta" and not slowed:
            slowed.append(payload)
            await asyncio.sleep(0.2)                          # 第一片写页面时被背压
        return await real_send(payload)

    host.host.send_frame = send_frame
    before = len(host.host.frames)
    try:
        rt.schedule_reply(None)
        await wait_for(lambda: [f for f in host.host.frames[before:] if f.get("type") == "visit_line"
                                and f.get("speaker", {}).get("side") == "host"], timeout=5)
        await settle()
        own = [f for f in host.host.frames[before:] if f.get("type") in ("visit_line_delta", "visit_line")
               and f.get("speaker", {}).get("side") == "host"]
        ln = slowed[0]["line_id"]
        kinds = [f["type"] for f in own if f.get("line_id") == ln]
        assert kinds[-1] == "visit_line"                      # 整句最后到，过时的分片不会排在它后面
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
            await stuck.wait()                                # 亲人那句落盘慢过关闭通道的等待
        return await real_record(speaker, **kwargs)

    rt.record_line = slow_record
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "等一下再走", "source": "neko_visit:guest_cat"}))
        await wait_for(lambda: rt.outbox.reserved_bytes > 0)
        rt.request_finalize("delivery_failed")
        await asyncio.sleep(1.0)                              # 已超过 _CLOSE_WAIT_S
        assert not rt._ended_published                        # 预留还在：退出流程仍在等关闭任务，不往下走
        assert rtm.get_runtime("Host") is rt                  # teardown 不先拆发送通道
        stuck.set()
        await asyncio.gather(sending)
        await finish(rt, clock)
        humans = [p for p in wire.sent["host"] if p.get("t") == "text" and p.get("sp") == "h"]
        assert humans and humans[0].get("txt") == "等一下再走"     # 这句照样送到对端
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
        llm.cancel = lambda *a, **k: False                    # LLM 不肯停：_finish_line 要等 _LLM_SETTLE_S
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)   # 关机只等 0.5 s
        own = [r for r in rt.journal.lines() if r["from"] == "own_cat"]
        assert own and own[-1]["truncated"]                   # 已收口的那一行在封存之前记进了转录
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
        await gate.wait()                                 # 写转录时磁盘慢
        return await real_record(*args, **kwargs)

    rt.record_line = slow_record
    header = LineHeader(ln="h:99", lp=99, ad="gc", rt="", wu=False, sp="c", lang=None)
    line = rtm_talk._LineRun(ref=LineRef("h:99", 99, "host"), header=header, reply_to=None, goodbye=False, prompt="")
    line.payload = {"txt": "收口的这一行", "truncated": True}
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(rt.book_line(line), 0.1)   # 关机限时到点，取消了调用方
        gate.set()
        await rt.book_line(line)                          # 另一个调用方等的是同一次写入
        assert calls == ["h:99"]                          # 只写一次
        assert [r for r in rt.journal.lines() if r["text"] == "收口的这一行"]   # 写入没被那次取消打断
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
            await stuck.wait()                                # 亲人那句落盘慢
        return await real_record(speaker, **kwargs)

    rt.record_line = slow_record
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "被取消也要发出去", "source": "neko_visit:guest_cat"}))
        await wait_for(lambda: rt.outbox.reserved_bytes > 0)  # 已接纳（改了 room）
        sending.cancel()                                      # 处理函数被取消（关机等）
        await asyncio.gather(sending, return_exceptions=True)
        stuck.set()
        await rt.flush()
        await wait_for(lambda: [p for p in wire.sent["host"] if p.get("t") == "text" and p.get("sp") == "h"],
                       timeout=5)                             # 照样落盘、入队、发到对端
        await wait_for(lambda: "human" in rt._line_kinds.values())  # 本侧历史也有这句
        await wait_for(lambda: [f for f in host.host.frames
                                if f.get("type") == "visit_line" and f.get("text") == "被取消也要发出去"])  # 也上屏
        await wait_for(lambda: "被取消也要发出去" in host.host.user_inputs)  # 也镜像进主会话
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
        await wait_for(lambda: [p for p in plans if p is not None])  # 处理函数走了，回复照样排上
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
            result = await real_record(speaker, **kwargs)
            order.append("recorded")
            return result
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
        asyncio.get_running_loop().call_later(0.3, stuck.set)  # 落盘要一会儿才完
        await asyncio.wait_for(rtm.stop_all("shutdown"), 10)
        await asyncio.gather(sending, return_exceptions=True)
        assert "recorded" in order and "seal" in order
        assert order.index("recorded") < order.index("seal")  # 先记进转录，再封存
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
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
        await sending
        assert seen and seen[0] is sending                    # 接纳在处理函数里同步做完，不推迟到落盘任务
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
            await stuck.wait()                                # 只有亲人那句落盘慢
        return await real_record(speaker, **kwargs)

    rt.record_line = slow_record
    try:
        sending = asyncio.ensure_future(rtm.route_stream_message("Host", {
            "input_type": "text", "data": "我们先走啦", "source": "neko_visit:guest_cat"}))
        await wait_for(lambda: rt.outbox.reserved_bytes > 0)
        rt.request_finalize("delivery_failed")                # 这时开始收尾（不排空、直接发 leave 的那类）
        await asyncio.sleep(0.2)
        stuck.set()
        await asyncio.gather(sending)
        await finish(rt, clock)
        sent = [p.get("t") for p in wire.sent["host"]]
        humans = [i for i, p in enumerate(wire.sent["host"]) if p.get("t") == "text" and p.get("sp") == "h"]
        assert humans and "leave" in sent and humans[0] < sent.index("leave")   # 那句排在 leave 前面
    finally:
        stuck.set()
        hgate.set()
        ggate.set()
        await teardown(host, guest, wire=wire, clock=clock)
