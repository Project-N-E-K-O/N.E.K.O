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

"""``open_mirror_speech_stream`` (visit design OD-15 v3, §5 PR-09b)."""

from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

import main_logic.core as core_module
from main_logic.core.turn import MirrorSpeechStream
from tests.unit.test_core_game_route_memory_contract import _FakeAliveThread, _make_manager

LLM = core_module.LLMSessionManager


class _DeadThread:
    def is_alive(self):
        return False


def _mgr(*, ready=True, alive=True):
    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread() if alive else _DeadThread()
    mgr.tts_ready = ready
    mgr._mirror_stream_callbacks = {}
    mgr._mirror_stream_tail = None
    mgr._mirror_last_claimed_sid = None
    mgr._mirror_stream_ends = {}
    mgr.interrupts = []

    async def interrupt():
        mgr.interrupts.append(mgr.current_speech_id)

    mgr.interrupt_mirror_speech = interrupt
    mgr.remember_speech_playback_gain = lambda sid, gain: gain
    return mgr


def _queued(mgr):
    items = []
    while not mgr.tts_request_queue.empty():
        items.append(mgr.tts_request_queue.get_nowait())
    return items


async def _settle(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


def _open(mgr, counted=None, failed=None):
    on_enqueued = counted.append if counted is not None else None
    on_failed = (lambda: failed.append(True)) if failed is not None else None
    return LLM.open_mirror_speech_stream(mgr, metadata={"source": "neko_visit"}, request_id="r",
                                         on_enqueued=on_enqueued, on_failed=on_failed)


async def test_push_then_finish_queues_text_and_one_end_marker():
    mgr = _mgr()
    counted: list[int] = []
    stream = _open(mgr, counted)
    assert isinstance(stream, MirrorSpeechStream)
    assert stream.push("你好，") and stream.push("今天天气") and stream.push("不错。")
    assert stream.finish() is True
    await _settle()
    items = _queued(mgr)
    assert items[-1] == (None, None) and items.count((None, None)) == 1
    texts = [t for sid, t in items[:-1]]
    assert {sid for sid, _ in items[:-1]} == {stream.speech_id}
    assert "".join(texts) == "你好，今天天气不错。"
    assert sum(counted) == len("".join(texts))
    assert mgr.current_speech_id == stream.speech_id
    # 结束标记入队即注销回调
    assert stream.speech_id not in mgr._mirror_stream_callbacks
    # 收尾后再推 / 再收尾一律 no-op
    assert stream.push("多余") is False and stream.finish() is False
    await _settle()
    assert _queued(mgr) == []


async def test_text_waits_for_the_worker_and_the_callback_outlives_finish():
    mgr = _mgr(ready=False)
    counted: list[int] = []
    stream = _open(mgr, counted)
    stream.push("等一下。")
    assert stream.finish() is True
    await _settle()
    # 未就绪：正文进待发缓存、收尾被推迟，回调还登记着
    assert mgr.tts_pending_chunks == [(stream.speech_id, "等一下。")]
    assert mgr._tts_done_pending_until_ready is True and _queued(mgr) == []
    assert stream.speech_id in mgr._mirror_stream_callbacks
    mgr.tts_ready = True
    mgr._snapshot_tts_runtime = lambda: None
    mgr._tts_runtime_is_current = lambda _r: True
    mgr._tts_output_is_current = lambda: True
    await LLM._flush_tts_pending_chunks(mgr)
    assert _queued(mgr) == [(stream.speech_id, "等一下。"), (None, None)]
    assert counted == [len("等一下。")]
    assert stream.speech_id not in mgr._mirror_stream_callbacks


async def test_abort_is_terminal_and_interrupts_its_own_speech():
    mgr = _mgr()
    counted: list[int] = []
    stream = _open(mgr, counted)
    stream.push("第一句。")
    await _settle()
    assert _queued(mgr) == [(stream.speech_id, "第一句。")]
    assert stream.abort() is True
    await _settle()
    assert mgr.interrupts == [stream.speech_id]
    assert stream.speech_id not in mgr._mirror_stream_callbacks
    assert stream.push("后面的") is False and stream.finish() is False
    assert stream.abort() is False          # 幂等
    await _settle()
    assert _queued(mgr) == [] and mgr.interrupts == [stream.speech_id]


async def test_abort_before_anything_ran_queues_nothing():
    mgr = _mgr()
    stream = _open(mgr)
    stream.push("还没来得及")
    stream.abort()
    await _settle()
    assert _queued(mgr) == [] and mgr.tts_pending_chunks == []
    assert mgr.current_speech_id == "old-speech" and mgr.interrupts == []


async def test_abort_of_a_superseded_stream_leaves_the_current_speech_alone():
    mgr = _mgr()
    old = _open(mgr)
    old.push("旧。")
    old.finish()
    await _settle()
    new = _open(mgr)
    new.push("新。")
    await _settle()
    old.abort()
    await _settle()
    assert mgr.interrupts == [] and mgr.current_speech_id == new.speech_id


async def test_finish_after_the_worker_died_reports_no_worker():
    mgr = _mgr()
    stream = _open(mgr)
    stream.push("你好。")
    await _settle()
    mgr.tts_thread = _DeadThread()
    assert stream.finish() == MirrorSpeechStream.NO_WORKER
    assert stream.push("再说") is False


async def test_missing_worker_closes_the_stream_so_push_fails_fast():
    mgr = _mgr(alive=False)
    stream = _open(mgr)
    assert stream.push("你好。") is True       # 还没开播：先收下
    await _settle()
    assert stream.closed and stream.push("再说") is False
    assert _queued(mgr) == [] and mgr.tts_pending_chunks == []


async def test_failing_tts_start_closes_the_stream():
    mgr = _mgr()

    async def broken():
        raise RuntimeError("no tts")

    mgr.ensure_tts_pipeline_alive = broken
    stream = _open(mgr)
    stream.push("你好。")
    await _settle()
    assert stream.closed and stream.push("再说") is False


async def test_stream_and_main_chat_keep_their_own_speech_ids():
    mgr = _mgr()
    counted: list[int] = []
    first = _open(mgr, counted)
    first.push("串门一句。")
    first.finish()
    await _settle()
    # 主聊天一轮：没有登记回调，入队照旧
    async with mgr.lock:
        mgr.current_speech_id = "main-turn"
        mgr._tts_done_queued_for_turn = False
    async with mgr.tts_cache_lock:
        LLM._enqueue_tts_text_chunk(mgr, "main-turn", "主聊天。")
        assert LLM._request_tts_done_locked(mgr) == "queued"
    second = _open(mgr, counted)
    second.push("又一句。")
    second.finish()
    await _settle()
    items = _queued(mgr)
    assert items == [
        (first.speech_id, "串门一句。"), (None, None),
        ("main-turn", "主聊天。"), (None, None),
        (second.speech_id, "又一句。"), (None, None),
    ]
    assert first.speech_id != second.speech_id
    assert counted == [len("串门一句。"), len("又一句。")]
    assert mgr._mirror_stream_callbacks == {}


async def test_ordinary_enqueue_without_any_stream_is_unchanged():
    # 普通聊天：管理器上没有任何流式登记（含 __new__ 构造、没有这个属性的旧夹具）
    mgr = _make_manager()
    del_attr = "_mirror_stream_callbacks"
    assert not hasattr(mgr, del_attr)
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    async with mgr.tts_cache_lock:
        LLM._enqueue_tts_text_chunk(mgr, "old-speech", "你好。")
        assert LLM._request_tts_done_locked(mgr) == "queued"
    assert _queued(mgr) == [("old-speech", "你好。"), (None, None)]


async def test_callback_registry_is_bounded():
    mgr = _mgr()
    streams = [_open(mgr, []) for _ in range(70)]
    assert len(mgr._mirror_stream_callbacks) == 64
    assert streams[-1].speech_id in mgr._mirror_stream_callbacks
    assert streams[0].speech_id not in mgr._mirror_stream_callbacks
    for s in streams:
        s.abort()
    await _settle()


@pytest.mark.parametrize("text", ["", None])
async def test_empty_push_is_accepted_without_queueing(text):
    mgr = _mgr()
    stream = _open(mgr)
    assert stream.push(text) is True
    stream.finish()
    await _settle()
    assert _queued(mgr) == [(None, None)]


async def test_visit_host_adapter_opens_a_real_stream():
    from main_routers.visit_router.host_port import ManagerHost

    mgr = _mgr()
    mgr.open_mirror_speech_stream = lambda **kw: LLM.open_mirror_speech_stream(mgr, **kw)
    counted: list[int] = []
    stream = ManagerHost("Lan", mgr).open_speech_stream(metadata={}, request_id="r", on_enqueued=counted.append)
    assert isinstance(stream, MirrorSpeechStream)
    stream.push("到家了。")
    stream.finish()
    await _settle()
    assert counted == [len("到家了。")]


# ── 评审第 1 轮：轮流认领 TTS 轮次、失去归属、失败信号 ──────────────────


async def test_back_to_back_streams_play_in_open_order():
    # 回家仪式句与简述：两条流各自一次 push + finish，同一拍里接连打开
    mgr = _mgr()
    counted: list[int] = []
    failed: list[bool] = []
    ritual = _open(mgr, counted, failed)
    ritual.push("我回来啦。")
    assert ritual.finish() is True
    summary = _open(mgr, counted, failed)
    summary.push("今天去串门了。")
    assert summary.finish() is True
    await _settle(60)
    assert _queued(mgr) == [(ritual.speech_id, "我回来啦。"), (None, None),
                            (summary.speech_id, "今天去串门了。"), (None, None)]
    assert counted == [len("我回来啦。"), len("今天去串门了。")]
    assert failed == [] and mgr._mirror_stream_callbacks == {}


async def test_a_newer_stream_waits_until_the_older_one_finishes():
    mgr = _mgr()
    older = _open(mgr)
    older.push("第一句，")
    await _settle()
    newer = _open(mgr)
    newer.push("第二句。")
    newer.finish()
    await _settle()
    # 旧流还没收尾：新流不认领，旧流的 speech id 与 done 标记不被覆盖
    assert mgr.current_speech_id == older.speech_id
    assert _queued(mgr) == [(older.speech_id, "第一句，")]
    older.push("说完了。")
    older.finish()
    await _settle(60)
    assert _queued(mgr) == [(older.speech_id, "说完了。"), (None, None),
                            (newer.speech_id, "第二句。"), (None, None)]


async def test_stream_that_lost_the_turn_stops_touching_tts_state():
    mgr = _mgr()
    failed: list[bool] = []
    stream = _open(mgr, failed=failed)
    stream.push("串门的话，")
    await _settle()
    async with mgr.lock:
        mgr.current_speech_id = "main-turn"     # 普通聊天接走了轮次
    stream.push("被打断的后半句。")
    assert stream.finish() is True
    await _settle()
    assert _queued(mgr) == [(stream.speech_id, "串门的话，")]
    assert mgr._tts_done_queued_for_turn is False   # 没替主聊天这一轮请求结束标记
    assert failed == [True] and stream.closed and stream.push("x") is False
    assert stream.speech_id not in mgr._mirror_stream_callbacks


async def test_abort_cleanup_finishes_before_the_next_stream_claims(monkeypatch):
    mgr = _mgr(ready=False)
    order: list[str] = []

    async def slow_interrupt():
        order.append("interrupt-start")
        await asyncio.sleep(0.05)
        mgr.tts_pending_chunks.clear()          # 与 _finish_tts_clear 一样清掉待发缓存
        order.append("interrupt-end")

    mgr.interrupt_mirror_speech = slow_interrupt
    first = _open(mgr)
    first.push("前一句")
    await _settle()
    first.abort()
    second = _open(mgr)
    second.push("后一句。")
    await asyncio.sleep(0.1)
    await _settle()
    # 后一条流在前一条的打断清理做完之后才认领：它的待发文字没被一并清掉
    assert order == ["interrupt-start", "interrupt-end"]
    assert mgr.tts_pending_chunks == [(second.speech_id, "后一句。")]
    assert mgr.current_speech_id == second.speech_id


async def test_enqueue_error_after_partial_text_interrupts_and_reports_failure():
    mgr = _mgr()
    failed: list[bool] = []
    stream = _open(mgr, failed=failed)
    stream.push("第一段，")
    await _settle()
    real = LLM._enqueue_tts_text_chunk

    def broken(m, sid, text):
        raise RuntimeError("tts queue gone")

    mgr._enqueue_tts_text_chunk = lambda sid, text: broken(mgr, sid, text)
    stream.push("第二段。")
    await _settle()
    assert failed == [True] and stream.closed
    assert mgr.interrupts == [stream.speech_id]   # 已进 TTS 的半句被打断
    assert real is not None


async def test_start_failure_reports_failure_once():
    mgr = _mgr()
    failed: list[bool] = []

    async def broken():
        raise RuntimeError("no tts")

    mgr.ensure_tts_pipeline_alive = broken
    stream = _open(mgr, failed=failed)
    stream.push("你好。")
    stream.finish()
    await _settle()
    assert failed == [True]


async def test_no_failure_signal_for_finish_or_abort():
    mgr = _mgr()
    failed: list[bool] = []
    done = _open(mgr, failed=failed)
    done.push("好。")
    done.finish()
    await _settle()
    gone = _open(mgr, failed=failed)
    gone.push("算了")
    await _settle()
    gone.abort()
    await _settle()
    assert failed == []


async def test_a_stream_never_finished_only_holds_the_turn_for_a_bounded_time(monkeypatch):
    monkeypatch.setattr(MirrorSpeechStream, "_PREDECESSOR_WAIT_S", 0.05)
    mgr = _mgr()
    failed: list[bool] = []
    stuck = _open(mgr, failed=failed)
    stuck.push("说到一半")
    await _settle()
    nxt = _open(mgr)
    nxt.push("下一句。")
    nxt.finish()
    await asyncio.sleep(0.1)
    await _settle()
    assert mgr.current_speech_id == nxt.speech_id
    # 晚到的旧流发现轮次已被接走：不再入队、报失败
    stuck.push("后半句")
    await _settle()
    assert failed == [True]
    assert (stuck.speech_id, "后半句") not in _queued(mgr)


async def test_visit_host_adapter_passes_the_failure_signal():
    from main_routers.visit_router.host_port import ManagerHost

    mgr = _mgr()

    async def broken():
        raise RuntimeError("no tts")

    mgr.ensure_tts_pipeline_alive = broken
    mgr.open_mirror_speech_stream = lambda **kw: LLM.open_mirror_speech_stream(mgr, **kw)
    failed: list[bool] = []
    stream = ManagerHost("Lan", mgr).open_speech_stream(metadata={}, request_id="r", on_enqueued=lambda n: None,
                                                        on_failed=lambda: failed.append(True))
    stream.push("你好。")
    await _settle()
    assert failed == [True]


# ── 评审第 2 轮 ───────────────────────────────────────────────────────


async def test_family_turn_started_after_open_is_not_taken_over():
    # 回家段落打开之后、认领之前，亲人先开口（普通对话开了新一轮）：本流放弃，不抢那一轮
    mgr = _mgr()
    failed: list[bool] = []
    stream = _open(mgr, failed=failed)
    stream.push("我回来啦。")
    stream.finish()
    mgr.current_speech_id = "family-turn"
    await _settle()
    assert mgr.current_speech_id == "family-turn"
    assert _queued(mgr) == [] and mgr.tts_pending_chunks == []
    assert failed == [True] and stream.closed


async def test_deferred_end_marker_keeps_lines_apart():
    # worker 未就绪：前一行的结束标记只是推迟，后一行要等它真正入队再认领，两行不会合成一句
    mgr = _mgr(ready=False)
    first = _open(mgr)
    first.push("第一句。")
    first.finish()
    second = _open(mgr)
    second.push("第二句。")
    second.finish()
    await _settle(60)
    assert mgr.current_speech_id == first.speech_id
    assert mgr.tts_pending_chunks == [(first.speech_id, "第一句。")]
    assert mgr._tts_done_pending_until_ready is True
    mgr.tts_ready = True
    mgr._snapshot_tts_runtime = lambda: None
    mgr._tts_runtime_is_current = lambda _r: True
    mgr._tts_output_is_current = lambda: True
    await LLM._flush_tts_pending_chunks(mgr)
    await _settle(60)
    assert _queued(mgr) == [(first.speech_id, "第一句。"), (None, None),
                            (second.speech_id, "第二句。"), (None, None)]
    assert mgr._mirror_stream_ends == {}


async def test_timed_out_predecessor_is_failed_and_interrupted_before_the_claim(monkeypatch):
    monkeypatch.setattr(MirrorSpeechStream, "_PREDECESSOR_WAIT_S", 0.05)
    mgr = _mgr()
    order: list[str] = []

    async def interrupt():
        order.append(f"interrupt:{mgr.current_speech_id}")

    mgr.interrupt_mirror_speech = interrupt
    failed: list[bool] = []
    stuck = _open(mgr, failed=failed)
    stuck.push("说到一半")
    await _settle()
    nxt = _open(mgr)
    nxt.push("下一句。")
    nxt.finish()
    await asyncio.sleep(0.1)
    await _settle()
    # 先打断旧行（当时它还是当前语音），再由新行认领
    assert order == [f"interrupt:{stuck.speech_id}"]
    assert failed == [True] and stuck.closed
    assert mgr.current_speech_id == nxt.speech_id
    assert _queued(mgr)[-2:] == [(nxt.speech_id, "下一句。"), (None, None)]


async def test_deferred_end_discarded_elsewhere_fails_and_hands_over(monkeypatch):
    # 推迟到 worker 就绪的结束标记被别处清掉（普通对话打断 / 会话重建）：本流报失败并交出轮次，
    # 下一条不必等满上限
    monkeypatch.setattr(MirrorSpeechStream, "_DEFERRED_POLL_S", 0.01)
    mgr = _mgr(ready=False)
    failed: list[bool] = []
    first = _open(mgr, failed=failed)
    first.push("第一句。")
    first.finish()
    second = _open(mgr)
    second.push("第二句。")
    await _settle()
    assert mgr._tts_done_pending_until_ready is True and failed == []
    mgr.tts_pending_chunks.clear()                 # _finish_tts_clear 同款清理
    mgr._tts_done_pending_until_ready = False
    await asyncio.sleep(0.05)
    await _settle()
    assert failed == [True]
    assert mgr.current_speech_id == second.speech_id
    assert first.speech_id not in mgr._mirror_stream_ends


# ── 评审第 1 轮（云端）────────────────────────────────────────────────


async def test_finished_streams_are_not_kept_alive_by_the_manager():
    mgr = _mgr()
    failed: list[bool] = []
    first = _open(mgr, failed=failed)
    first.push("第一句。")
    first.finish()
    ref = weakref.ref(first)
    del first
    for i in range(5):
        stream = _open(mgr, failed=failed)
        stream.push(f"第{i}句。")
        stream.finish()
        await _settle(40)
    await _settle(40)
    gc.collect()
    assert ref() is None                       # 不经 tail → _predecessor 链留住整串历史
    assert mgr._mirror_stream_tail._on_failed is None   # 最后一条也不再拖着失败回调（及其闭包）
    assert failed == []


async def test_middle_stream_aborted_before_its_claim_does_not_fail_the_next():
    # A、B、C 同一拍打开；B 在等 A 时被中止：C 等 A 说完再认领，不误判「轮次被接走」
    mgr = _mgr()
    failed: list[str] = []
    a = _open(mgr)
    b = _open(mgr)
    c = LLM.open_mirror_speech_stream(mgr, metadata={}, request_id="c", on_failed=lambda: failed.append("c"))
    a.push("第一段，")
    await _settle()
    b.abort()
    c.push("第三句。")
    c.finish()
    await _settle(40)
    assert mgr.current_speech_id == a.speech_id      # A 还没说完：C 不认领
    a.push("说完了。")
    a.finish()
    await _settle(60)
    assert failed == [] and mgr.interrupts == []
    assert _queued(mgr) == [(a.speech_id, "第一段，"), (a.speech_id, "说完了。"), (None, None),
                            (c.speech_id, "第三句。"), (None, None)]


async def test_stream_opened_after_a_claim_waits_through_an_aborted_middle_one():
    # C 打开时 A 已认领（C 的 base 就是 A）：B 中止后 C 也不能直接盖过还在说的 A
    mgr = _mgr()
    a = _open(mgr)
    a.push("第一段，")
    await _settle()
    b = _open(mgr)
    c = _open(mgr)
    b.abort()
    c.push("第三句。")
    c.finish()
    await _settle(40)
    assert mgr.current_speech_id == a.speech_id
    assert _queued(mgr) == [(a.speech_id, "第一段，")]
    a.finish()
    await _settle(60)
    assert _queued(mgr) == [(None, None), (c.speech_id, "第三句。"), (None, None)]


async def test_timed_out_owner_behind_an_aborted_stream_is_interrupted_before_the_claim(monkeypatch):
    monkeypatch.setattr(MirrorSpeechStream, "_PREDECESSOR_WAIT_S", 0.05)
    mgr = _mgr()
    failed: list[str] = []
    a = LLM.open_mirror_speech_stream(mgr, metadata={}, request_id="a", on_failed=lambda: failed.append("a"))
    a.push("说到一半")
    await _settle()
    b = LLM.open_mirror_speech_stream(mgr, metadata={}, request_id="b", on_failed=lambda: failed.append("b"))
    c = _open(mgr)
    b.abort()
    c.push("下一句。")
    c.finish()
    await asyncio.sleep(0.2)
    await _settle(60)
    # 还占着轮次的是 A：超过上限先让 A 失败、打断它的半句，再由 C 认领
    assert failed == ["a"] and mgr.interrupts == [a.speech_id]
    assert mgr.current_speech_id == c.speech_id
    assert _queued(mgr)[-2:] == [(c.speech_id, "下一句。"), (None, None)]


async def test_finish_without_a_worker_also_ends_the_stream_task():
    mgr = _mgr()
    stream = _open(mgr)
    stream.push("你好。")
    await _settle()
    mgr.tts_thread = _DeadThread()
    assert stream.finish() == MirrorSpeechStream.NO_WORKER
    await _settle()
    assert stream._task.done()


async def test_a_live_stream_drops_its_predecessor_once_it_claimed():
    mgr = _mgr()
    first = _open(mgr)
    first.push("第一句。")
    first.finish()
    ref = weakref.ref(first)
    del first
    live = _open(mgr)
    live.push("还在说，")
    await _settle(60)
    gc.collect()
    assert mgr.current_speech_id == live.speech_id and ref() is None


async def test_an_aborted_tail_drops_its_predecessor_once_handed_over():
    mgr = _mgr()
    first = _open(mgr)
    first.push("第一句，")
    await _settle()
    tail = _open(mgr)
    tail.abort()
    first.finish()
    ref = weakref.ref(first)
    del first
    await _settle(60)
    gc.collect()
    assert mgr._mirror_stream_tail is tail and ref() is None


async def test_concurrent_give_ups_interrupt_once_and_survive_a_cancelled_caller():
    mgr = _mgr()
    gate = asyncio.Event()
    interrupts: list[str] = []

    async def slow_interrupt():
        await gate.wait()
        interrupts.append(mgr.current_speech_id)

    mgr.interrupt_mirror_speech = slow_interrupt
    owner = _open(mgr)
    owner.push("说到一半")
    await _settle()
    first = asyncio.ensure_future(owner._give_up())
    second = asyncio.ensure_future(owner._give_up())
    await _settle()
    first.cancel()                          # 等收尾的一方被取消：收尾本身照常做完
    await _settle()
    gate.set()
    await second
    await _settle()
    assert interrupts == [owner.speech_id] and owner._released.done()


async def test_an_aborted_stream_never_reports_failure_even_if_given_up_mid_cleanup():
    mgr = _mgr()
    gate = asyncio.Event()

    async def slow_interrupt():
        await gate.wait()

    mgr.interrupt_mirror_speech = slow_interrupt
    failed: list[bool] = []
    owner = _open(mgr, failed=failed)
    owner.push("说到一半")
    await _settle()
    owner.abort()
    await _settle()
    giving_up = asyncio.ensure_future(owner._give_up())   # 后继恰在打断清理途中等满了上限
    await _settle()
    gate.set()
    await giving_up
    assert failed == []
