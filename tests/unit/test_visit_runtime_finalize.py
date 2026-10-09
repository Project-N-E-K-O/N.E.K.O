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

"""Visit runtime: ending a visit (§3.2.6 item 22), shutdown, background writes and the reason tables."""

from __future__ import annotations

import asyncio
import json

import pytest

from main_routers.visit_router import inbox_handoff
from main_routers.visit_router import runtime as rtm
from main_routers.visit_router import transport_ws
from main_routers.visit_router.runtime_common import (
    ABORT_REASONS,
    FINALIZE_REASONS,
    LEAVE_REASON_FOR,
    NO_LEAVE_REASONS,
    finalize_reason_for_peer_leave,
    leave_reason_for,
)
from tests.unit.visit_runtime_harness import (
    GUEST_VID,
    Replies,
    bring_up,
    finish,
    settle,
    step,
    teardown,
    wait_for,
)
from utils import external_route_registry as registry
from utils import visit_route_state
from utils.visit_wire import LEAVE_REASONS


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


async def _quiet(tmp_path, monkeypatch, **kw):
    """Active visit whose next LLM turns block (the opening lines finish first)."""
    hgate, ggate = asyncio.Event(), asyncio.Event()
    host_replies = Replies(queue=[["主人家开场。"]], gate=None)
    guest_replies = Replies(queue=[["客人开场。"]], gate=None)
    host_replies.default = [hgate, "之后的话。"]
    guest_replies.default = [ggate, "之后的话。"]
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, host_replies=host_replies,
                                                    guest_replies=guest_replies, **kw)
    await wait_for(lambda: len(host.rt.journal.lines()) >= 2 and len(guest.rt.journal.lines()) >= 2)
    # 下一轮回话被挡住；收尾时的仪式句与简述两轮直接给答复（打断回话那一轮之后才轮到它们）
    host_replies.queue = [["我回来啦。"], ["这次聊得很开心。"]]
    guest_replies.queue = [["我回来啦。"], ["这次聊得很开心。"]]
    return host, guest, wire, clock, (hgate, ggate)


# ── 原因映射表 ───────────────────────────────────────────────────────


def test_every_finalize_reason_either_maps_to_a_leave_reason_or_sends_none():
    for reason in FINALIZE_REASONS:
        mapped = LEAVE_REASON_FOR.get(reason)
        assert (mapped is None) != (reason not in NO_LEAVE_REASONS), reason
        if mapped is not None:
            assert mapped in LEAVE_REASONS
    assert leave_reason_for("peer_blocked", side="host") == "peer_identity_rejected"
    assert leave_reason_for("idle_timeout", side="host") == "ended"
    assert leave_reason_for("max_lines", side="guest") == "ended"
    assert leave_reason_for("local_page_lost", side="host") is None
    assert leave_reason_for("wrap_up", side="guest", done_received=True) == "home"
    assert leave_reason_for("wrap_up", side="host") == "wrapup"
    for reason in ABORT_REASONS:
        assert leave_reason_for(reason, side="host") is None


def test_every_received_leave_reason_maps_into_the_finalize_set():
    for reason in LEAVE_REASONS:
        assert finalize_reason_for_peer_leave(reason) in FINALIZE_REASONS
    assert finalize_reason_for_peer_leave("home") == "peer_left"
    assert finalize_reason_for_peer_leave("declined") == "declined"
    assert finalize_reason_for_peer_leave("something_new") == "peer_left"
    assert finalize_reason_for_peer_leave(None) == "peer_left"


# ── 状态翻转、锁与输入 ─────────────────────────────────────────────


async def test_ending_releases_the_input_with_the_takeover_and_keeps_the_lock_until_done(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    try:
        assert rtm.is_visit_route_active("Host") and registry.get_active_external_route("Host").kind == "neko_visit"
        assert host.rt.request_finalize("route_end") is True
        assert host.rt.request_finalize("peer_lost") is False          # 幂等
        # 接管还没释放：普通聊天此刻输出被压着，这句由串门拒掉（不漏进普通聊天）
        assert host.rt.phase == "ending" and host.rt.takeover_token is not None
        assert rtm.is_visit_route_active("Host")
        claim = await registry.route_external_stream_message("Host", {"input_type": "text", "data": "喂"})
        assert claim is registry.RouteClaim.CLAIMED
        assert host.host.status_codes()[-1] == "VISIT_INPUT_REFUSED_WRAPUP"
        # 接管一释放：输入交还普通聊天，改名 / 删除锁保持到收尾完成
        await wait_for(lambda: host.host.released)
        assert not rtm.is_visit_route_active("Host")
        assert registry.get_active_external_route("Host") is None
        assert registry.is_external_route_locked("Host")
        claim = await registry.route_external_stream_message("Host", {"input_type": "text", "data": "在吗"})
        assert claim is registry.RouteClaim.UNCLAIMED                  # 亲人打字走普通聊天
        await finish(host.rt, clock)
        assert not registry.is_external_route_locked("Host")
        assert visit_route_state.get_visit_route_state("Host") is None
        assert not rtm.is_visit_live(host.rt.visit_id)
        assert host.uploads == [host.rt.visit_id]
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_takeover_is_released_right_after_the_hold_and_before_the_home_line(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    try:
        host.rt.request_finalize("recall")
        await finish(host.rt, clock)
        events = host.host.events
        assert events.index("hold_callbacks") < events.index("release_takeover")
        ritual = [e for e in events if e.startswith("output:visit-ritual")]
        assert ritual and events.index("release_takeover") < events.index(ritual[0])
        assert host.host.released == host.host.takeovers
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_parked_callbacks_are_handed_back_once_both_segments_ended(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    hh = host.host
    try:
        parked = {"source_kind": "plugin", "priority": 1, "text": "插件回调"}
        assert hh.sink(parked) is True                 # 串门期间扣进 VisitInbox
        hh.auto_play = False
        host.rt.request_finalize("recall")
        await finish(host.rt, clock)                  # 退出流程完成：路由已 pop、锁已放
        assert visit_route_state.get_visit_route_state("Host") is None
        late = {"source_kind": "plugin", "priority": 0, "text": "释放之后才到"}
        assert hh.hold_sink(late) is True              # release_takeover 之后经 hold 接住
        segments = [s for s in hh.streams if s.request_id.startswith(("visit-ritual", "visit-debrief"))]
        assert len(segments) == 2 and hh.resubmitted == []
        await rtm.on_page_signal("Host", {"speech_id": segments[0].speech_id, "played_ms": 3000,
                                          "ended": True, "final": True})
        await asyncio.sleep(0.6)
        assert hh.resubmitted == []                    # 只结束了一段：不交还
        await rtm.on_page_signal("Host", {"speech_id": segments[1].speech_id, "played_ms": 3000,
                                          "ended": True, "final": True})
        await wait_for(lambda: hh.resubmitted)
        assert [c["text"] for c in hh.resubmitted] == ["插件回调", "释放之后才到"]
        assert hh.events.index("release_takeover") < hh.events.index("resubmit")
        assert hh.events.index("release_callback_hold") < hh.events.index("resubmit")
        assert inbox_handoff.pending_speech_ids() == 0
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_voice_off_hands_back_after_the_estimates_without_tts(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch,
                                                   settings={"visitVoiceEnabled": False})
    hh = host.host
    try:
        hh.sink({"source_kind": "plugin", "text": "回调"})
        requests = host.rt.journal.usage()["tts_requests"]
        host.rt.request_finalize("route_end")
        await finish(host.rt, clock)
        assert not [s for s in hh.streams if s.request_id.startswith(("visit-ritual", "visit-debrief"))]
        assert host.rt.journal.usage()["tts_requests"] == requests == 0
        assert hh.resubmitted == []
        clock.advance(30)
        await wait_for(lambda: hh.resubmitted)
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_handoff_never_waits_past_the_absolute_deadline(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    hh = host.host
    try:
        hh.sink({"source_kind": "plugin", "text": "回调"})
        hh.auto_play = False
        host.rt.request_finalize("route_end")
        await finish(host.rt, clock)
        segments = [s for s in hh.streams if s.request_id.startswith(("visit-ritual", "visit-debrief"))]
        handoff = host.rt.handoff
        deadline = handoff.absolute_deadline()
        assert deadline - (host.rt.finalize_at or 0) <= 120
        # 一直报进度、从不 ended：20 s 硬顶到了也不交还，直到绝对期限
        while clock() < deadline - 2.5:
            clock.advance(2)
            for seg in segments:
                await rtm.on_page_signal("Host", {"speech_id": seg.speech_id, "played_ms": 100, "ended": False})
            await asyncio.sleep(0.3)
            assert hh.resubmitted == []
        clock.advance(5)
        for _ in range(100):                               # 仍在报进度：只有绝对期限能让它交还
            for seg in segments:
                await rtm.on_page_signal("Host", {"speech_id": seg.speech_id, "played_ms": 100, "ended": False})
            if hh.resubmitted:
                break
            await asyncio.sleep(0.05)
        assert hh.resubmitted                              # 到点一律重投，从不丢弃
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_family_speaking_first_skips_the_home_line_and_only_shows_the_summary(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    hh = host.host
    ritual_gate = asyncio.Event()
    host.replies.queue = [[ritual_gate, "我送走客人啦。"], ["这次和客人聊得很开心。"]]
    try:
        host.rt.request_finalize("recall")
        await wait_for(lambda: "release_takeover" in hh.events)
        hh.last_input = 10 ** 12                       # 亲人在仪式句生成中先开口
        ritual_gate.set()
        await finish(host.rt, clock)
        assert not [e for e in hh.events if e.startswith("output:visit-ritual")]
        summary = [e for e in hh.events if e.startswith("output:visit-debrief")]
        assert summary and hh.events.index("wait_turn_idle") < hh.events.index(summary[0])
        assert not [s for s in hh.streams if s.request_id.startswith(("visit-ritual", "visit-debrief"))]
        chips = [e for e in hh.events if e.startswith("blocks:visit-debrief")]
        assert chips and hh.events.index(summary[0]) < hh.events.index(chips[0])
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


@pytest.mark.parametrize("reason,llm", [("recall", True), ("route_end", False), ("peer_lost", False)])
async def test_home_line_is_generated_only_for_natural_endings(tmp_path, monkeypatch, reason, llm):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    client = host.clients[0]
    host.replies.queue = [["我回来啦。"], ["总结。"]]
    try:
        before = list(client.prompts)
        host.rt.request_finalize(reason)
        await finish(host.rt, clock)
        new = client.prompts[len(before):]
        from config.prompts.prompts_visit import get_visit_back_home_notice

        assert (get_visit_back_home_notice("host", host.rt.lang) in new) is llm
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


# ── 收口顺序与转录 ───────────────────────────────────────────────────


async def test_upload_is_sealed_before_the_spool_is_finalized(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    rt = host.rt
    order = []
    real_seal, real_update = rt.journal.seal, rt.spool.update_state

    async def seal(reason, **kw):
        doc = await real_seal(reason, **kw)
        order.append("upload.json")
        return doc

    async def update(**changes):
        if "finalized" in changes:
            order.append("finalized")
        return await real_update(**changes)

    rt.journal.seal = seal
    rt.spool.update_state = update
    try:
        rt.request_finalize("route_end")
        await finish(rt, clock)
        assert order == ["upload.json", "finalized"]
        spool_dir = host.config_dir / "visit_spool"
        doc = json.loads((spool_dir / f"{rt.visit_id}.upload.json").read_text(encoding="utf-8"))
        assert set(doc) == {"v", "own_visit_uid", "own_char_uid", "transport", "request"}
        assert [ln["text"] for ln in doc["request"]["lines"]] == [r["text"] for r in rt.journal.lines()]
        state = json.loads((spool_dir / f"{rt.visit_id}.state.json").read_text(encoding="utf-8"))
        assert state["finalized"] == "route_end" and state["memory_enabled"] is True
        assert state["debrief_choice"] == "ask_later" and state["debrief_chip_pending"] is True
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_memory_off_still_uploads_and_offers_no_chips(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch, settings={"visitMemoryEnabled": False})
    rt = host.rt
    try:
        rt.request_finalize("route_end")
        await finish(rt, clock)
        spool_dir = host.config_dir / "visit_spool"
        assert (spool_dir / f"{rt.visit_id}.upload.json").exists()
        assert not (spool_dir / f"{rt.visit_id}.jsonl").exists()
        state = json.loads((spool_dir / f"{rt.visit_id}.state.json").read_text(encoding="utf-8"))
        assert state["memory_enabled"] is False and state["debrief_chip_pending"] is False
        assert not [e for e in host.host.events if e.startswith("blocks:")]
        assert [e for e in host.host.events if e.startswith("output:visit-debrief")]
        assert ("region", rt.visit_id) not in host.commits
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_memory_commits_run_as_background_tasks_of_the_character(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    rt = host.rt
    gate = asyncio.Event()
    real = rt.deps.commit_summary

    async def slow(spool, **kw):
        await gate.wait()
        return await real(spool, **kw)

    rt.deps.commit_summary = slow
    try:
        rt.request_finalize("route_end")
        await finish(rt, clock)
        assert not registry.is_external_route_locked("Host")
        assert rtm.has_visit_background_tasks("Host")
        assert registry.is_character_lifecycle_locked("Host")
        gate.set()
        await wait_for(lambda: not rtm.has_visit_background_tasks("Host"))
        assert ("region", rt.visit_id) in host.commits and ("summary", rt.visit_id) in host.commits
        assert host.commits.index(("region", rt.visit_id)) < host.commits.index(("summary", rt.visit_id))
    finally:
        gate.set()
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


# ── 对端离开 ─────────────────────────────────────────────────────────


async def test_peer_leave_waits_for_the_gap_then_keeps_the_late_line(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    rt = host.rt
    # 掐掉真实客人那侧发来的帧：seq + 1 只能由用例自己补上（否则它的可靠消息可能先把缺口补齐）
    wire.drop = lambda role, payload: role == "guest"
    await asyncio.wait_for(wire.queues["guest"].join(), 5)   # 已在路上的帧都处理完
    seq = rt.sequencer.contiguous_seq
    try:
        await rt.on_recv(from_vid=GUEST_VID, cmd=1, payload={
            "t": "leave", "v": 1, "seq": seq + 2, "last_seq": seq + 1, "reason": "home"}, nbytes=80)
        assert rt.exit_task is None, rt.finalize_reason  # 前面还缺一条：先等补齐
        await rt.on_recv(from_vid=GUEST_VID, cmd=2, payload={
            "t": "text", "v": 1, "ln": "g:50", "lp": 50, "seq": seq + 1, "sp": "c", "ad": "hc", "rt": "",
            "wu": False, "final": True, "txt": "最后一句。", "truncated": False, "i_done": 0}, nbytes=200)
        assert rt.finalize_reason == "peer_left" and rt.peer_reason == "home"
        assert any(r["text"] == "最后一句。" for r in rt.journal.lines())
    finally:
        wire.drop = None
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_unacked_reliable_messages_end_with_delivery_failed(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    try:
        wire.drop = lambda role, payload: role == "host"
        await rtm.route_stream_message("Host", {"input_type": "text", "data": "你听得到吗",
                                                "source": "neko_visit:guest_cat"})
        await step(clock, 34, host.rt, guest.rt, every=2.0)
        assert host.rt.finalize_reason == "delivery_failed"
        await finish(host.rt, clock)
        # host 发的全被丢掉、没有 ack：同一条 leave 会按 seq 每 1 s 重传，但只有这一条
        leaves = [p for p in wire.sent["host"] if p.get("t") == "leave"]
        assert leaves and {p["reason"] for p in leaves} == {"delivery_failed"}
        assert len({p["seq"] for p in leaves}) == 1
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_character_switch_and_replaced_manager(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    try:
        assert await registry.finalize_external_routes_for_character("Host") == 1
        assert host.rt.finalize_reason == "character_switch"
        await wait_for(lambda: not rtm.is_visit_route_active("Host"))
        guest.host.current = False
        await guest.rt.tick()
        assert guest.rt.finalize_reason == "manager_replaced"
        await finish(guest.rt, clock)
        # host 已在收尾、可能来不及回 ack：同一条 leave 会按 seq 重传，但只有这一条
        leaves = [p for p in wire.sent["guest"] if p.get("t") == "leave"]
        assert {p["reason"] for p in leaves} == {"error"} and len({p["seq"] for p in leaves}) == 1
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


# ── 关机 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("memory", [True, False])
async def test_shutdown_writes_files_first_and_never_sends_leave(tmp_path, monkeypatch, memory):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch, settings={"visitMemoryEnabled": memory})
    rt = host.rt
    try:
        before = list(wire.sent["host"])
        await asyncio.wait_for(rtm.stop_all("shutdown"), 3)
        assert "leave" not in [p.get("t") for p in wire.sent["host"][len(before):]]
        spool_dir = host.config_dir / "visit_spool"
        doc = json.loads((spool_dir / f"{rt.visit_id}.upload.json").read_text(encoding="utf-8"))
        assert [ln["text"] for ln in doc["request"]["lines"]] == [r["text"] for r in rt.journal.lines()]
        assert doc["request"]["usage"]["tts_requests"] >= 1
        state = json.loads((spool_dir / f"{rt.visit_id}.state.json").read_text(encoding="utf-8"))
        assert state["finalized"] == "shutdown"
        assert (state["debrief_choice"] == "ask_later") is memory
        assert state["debrief_chip_pending"] is memory
        assert host.host.released == host.host.takeovers
        assert visit_route_state.get_visit_route_state("Host") is None
        assert rtm.get_runtime("Host") is None
    finally:
        for g in gates:
            g.set()
        await teardown(guest, wire=wire, clock=clock)


async def test_stop_all_without_visits_does_nothing():
    await rtm.stop_all("shutdown")
    await settle()


# ── 评审补充：失败路径与边界 ─────────────────────────────────────────


async def test_a_failing_exit_step_still_hands_the_callbacks_back(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    hh = host.host

    async def broken(*args, **kwargs):
        raise RuntimeError("ritual exploded")

    host.rt.say_ritual = broken
    try:
        hh.sink({"source_kind": "plugin", "text": "插件回调"})
        host.rt.request_finalize("recall")
        await finish(host.rt, clock)
        await wait_for(lambda: hh.resubmitted)                # 不等兜底期限：没排上的段落直接跳过
        assert [c["text"] for c in hh.resubmitted] == ["插件回调"]
        assert hh.hold_released == hh.holds and hh.holds
        # 仪式句是可选步骤：它失败了，封存与简述照常
        assert host.rt.journal.sealed
        assert [b for b in hh.blocks if b[1] == f"visit-debrief:{host.rt.visit_id}"]
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_leave_that_cannot_be_queued_does_not_wedge_the_channel_close(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    rt = host.rt
    real_send = rt.outbox.send

    def refuse_leave(msg, **kw):
        if msg.get("t") == "leave":
            raise ValueError("outbox full")
        return real_send(msg, **kw)

    rt.outbox.send = refuse_leave
    try:
        rt.request_finalize("route_end")
        await finish(rt, clock)
        await wait_for(lambda: [m for m in wire.downlinks["host"] if m.get("type") == "stop"])
        await wait_for(lambda: rt.outbox._executor is None)       # 写线程停了、.outbox.jsonl 已删
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_ended_runtimes_expire_from_the_recent_table(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from config.visit_settings import VISIT_TRANSCRIPT_MEMORY_TTL_S

    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    rt = host.rt
    try:
        stale = SimpleNamespace(ended_at_mono=clock() - VISIT_TRANSCRIPT_MEMORY_TTL_S - 1)
        fresh = SimpleNamespace(ended_at_mono=clock() - 1)
        rtm._recent["stale-visit"] = stale
        rtm._recent["fresh-visit"] = fresh
        rt.request_finalize("route_end")
        await finish(rt, clock)
        # 没人查过的过期场次也在下一场登记时放掉
        assert "stale-visit" not in rtm._recent
        assert rtm._recent["fresh-visit"] is fresh and rtm._recent[rt.visit_id] is rt
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_home_segments_stay_text_only_after_the_visit_fell_back_from_tts(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    hh = host.host
    try:
        hh.sink({"source_kind": "plugin", "text": "回调"})
        host.rt.voice.fallen_back = True                   # 这一场 TTS 已起不来、改按估时
        host.rt.request_finalize("recall")
        await finish(host.rt, clock)
        assert not [s for s in hh.streams if s.request_id.startswith(("visit-ritual", "visit-debrief"))]
        assert [e for e in hh.events if e.startswith("output:visit-ritual")]
        clock.advance(30)
        await wait_for(lambda: hh.resubmitted)              # 按两段估时交还
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_drained_audio_queue_does_not_hand_the_callbacks_back(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    hh = host.host
    try:
        hh.sink({"source_kind": "plugin", "text": "回调"})
        hh.auto_play = False
        host.rt.request_finalize("recall")
        await finish(host.rt, clock)
        segments = [s for s in hh.streams if s.request_id.startswith(("visit-ritual", "visit-debrief"))]
        assert len(segments) == 2
        for seg in segments:                                # 队列暂时排空，之后还会接着播
            await rtm.on_page_signal("Host", {"speech_id": seg.speech_id, "played_ms": 900,
                                              "ended": True, "final": False})
        await asyncio.sleep(0.6)
        assert hh.resubmitted == []
        for seg in segments:
            await rtm.on_page_signal("Host", {"speech_id": seg.speech_id, "played_ms": 3000,
                                              "ended": True, "final": True})
        await wait_for(lambda: hh.resubmitted)
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_background_tasks_follow_a_rename_of_an_idle_character(monkeypatch):
    from main_logic.visit import local_chars

    async def resolve(uid):
        return "新名字"

    monkeypatch.setattr(local_chars, "resolve_char_name", resolve)
    rtm._uid_by_name["旧名字"] = "uid-1"                   # 上次串门时的名字
    gate = asyncio.Event()
    task = rtm.spawn_visit_background("uid-1", gate.wait)
    try:
        await wait_for(lambda: "新名字" in rtm._uid_by_name)
        assert rtm.has_visit_background_tasks("新名字")
        assert "旧名字" not in rtm._uid_by_name
    finally:
        gate.set()
        await asyncio.gather(task)


async def test_stop_all_also_stops_registered_background_writes(monkeypatch):
    from main_logic.visit import local_chars

    async def resolve(uid):
        return "某角色"

    monkeypatch.setattr(local_chars, "resolve_char_name", resolve)
    forever = asyncio.Event()
    task = rtm.spawn_visit_background("uid-bg", forever.wait)
    try:
        await asyncio.wait_for(rtm.stop_all("shutdown"), 5)
        await asyncio.sleep(0)
        assert task.cancelled()                           # 关机时登记的后台写入也停掉，不留给事件循环销毁
        assert not rtm.has_visit_background_tasks("某角色")
    finally:
        forever.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_a_failing_mandatory_step_still_seals_the_transcript_and_the_spool(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    rt = host.rt
    real_push = rt.push

    async def push(phase, **kw):
        if phase == "ended":
            raise RuntimeError("display socket exploded")
        return await real_push(phase, **kw)

    rt.push = push
    try:
        rt.request_finalize("route_end")
        await finish(rt, clock)
        assert rt.journal.sealed
        state = json.loads((host.config_dir / "visit_spool" / f"{rt.visit_id}.state.json").read_text(encoding="utf-8"))
        assert state["finalized"] == "route_end"
        await wait_for(lambda: ("region", rt.visit_id) in host.commits)
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_stops_the_channel_close_of_a_visit_already_ending(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    rt = host.rt
    wire.drop = lambda role, payload: role == "host"           # 对端收不到：leave 一直等不到确认
    try:
        rt.request_finalize("route_end")
        await wait_for(lambda: rt._closing_task is not None)
        await settle()
        assert not rt._closing_task.done()
        await asyncio.wait_for(rtm.stop_all("shutdown"), 3)
        assert rt._closing_task.done()                         # 关机返回时不再有排空 / leave 在跑
    finally:
        wire.drop = None
        for g in gates:
            g.set()
        await teardown(guest, wire=wire, clock=clock)


async def test_a_home_stream_the_tts_worker_refuses_falls_back_to_text(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    hh = host.host
    hh.dead_worker_prefix = ("visit-ritual",)                # 仪式句开流成功但 worker 已关
    try:
        hh.sink({"source_kind": "plugin", "text": "回调"})
        host.rt.request_finalize("recall")
        await finish(host.rt, clock)
        assert host.rt.voice.fallen_back is True                 # 之后不再开语音流
        assert not [s for s in hh.streams if s.request_id.startswith("visit-debrief")]
        clock.advance(30)
        await wait_for(lambda: hh.resubmitted)                   # 不必等兜底期限
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_shutdown_during_the_exit_flow_keeps_files_first(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    rt = host.rt
    stuck = asyncio.Event()

    async def ritual(*args, **kwargs):
        await stuck.wait()                                  # 收尾流程停在仪式句

    rt.say_ritual = ritual
    try:
        rt.request_finalize("recall")
        await wait_for(lambda: rt.takeover_token is None)   # 已走到仪式句之前
        await asyncio.wait_for(rtm.stop_all("shutdown"), 3)
        await settle()
        assert rt.exit_task.done()
        # 被取消的收尾流程没有自己再 teardown：文件由 shutdown 收口，状态写的是 shutdown
        assert rt.ended_at_mono is None
        assert rt.journal.sealed
        state = json.loads((host.config_dir / "visit_spool" / f"{rt.visit_id}.state.json").read_text(encoding="utf-8"))
        assert state["finalized"] == "shutdown"
    finally:
        stuck.set()
        for g in gates:
            g.set()
        await teardown(guest, wire=wire, clock=clock)


async def test_a_global_goodbye_ends_the_visit_without_a_sound(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    hh = host.host
    try:
        host.rt.request_finalize("goodbye")
        await finish(host.rt, clock)
        assert not [s for s in hh.streams if s.request_id.startswith(("visit-ritual", "visit-debrief"))]
        assert [e for e in hh.events if e.startswith("output:visit-ritual")]   # 固定句照样上屏
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_stop_all_also_stops_detached_background_work():
    stuck = asyncio.Event()
    task = rtm._detach(stuck.wait())                     # 例如还在退避等待的账号映射补写
    await asyncio.wait_for(rtm.stop_all("shutdown"), 3)
    assert task.cancelled()


async def test_stop_all_during_a_pending_handback_still_releases_the_callbacks(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _quiet(tmp_path, monkeypatch)
    hh = host.host
    try:
        hh.sink({"source_kind": "plugin", "text": "回调"})
        hh.auto_play = False                                  # 仪式句 / 简述一直没播完：交还还在等
        host.rt.request_finalize("recall")
        await finish(host.rt, clock)
        assert rtm.get_runtime("Host") is None and hh.resubmitted == []
        await asyncio.wait_for(rtm.stop_all("shutdown"), 3)   # 关机取消了等着交还的任务
        assert [c["text"] for c in hh.resubmitted] == ["回调"]
        assert hh.hold_released == hh.holds and hh.holds
        assert inbox_handoff.pending_speech_ids() == 0
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_a_background_task_whose_owner_is_unknown_yet_blocks_lifecycle_changes(monkeypatch):
    from main_logic.visit import local_chars

    resolving = asyncio.Event()
    release = asyncio.Event()

    async def slow_resolve(uid):
        resolving.set()
        await release.wait()
        return "Mimi"

    monkeypatch.setattr(local_chars, "resolve_char_name", slow_resolve)
    work = asyncio.Event()
    task = rtm.spawn_visit_background("uid-recovery", work.wait)
    try:
        await asyncio.wait_for(resolving.wait(), 3)
        assert rtm.has_visit_background_tasks("Mimi")        # 名字还没认出来：保守地挡住
        release.set()
        await wait_for(lambda: "Mimi" in rtm._uid_by_name)
        assert rtm.has_visit_background_tasks("Mimi") and not rtm.has_visit_background_tasks("Other")
    finally:
        release.set()
        work.set()
        await asyncio.gather(task)
