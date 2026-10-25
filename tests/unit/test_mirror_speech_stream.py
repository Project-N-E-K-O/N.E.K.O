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
    # Register the callback as soon as the end marker is queued
    assert stream.speech_id not in mgr._mirror_stream_callbacks
    # Any further push / finish after finalization is a no-op
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
    # Not ready yet: text goes into the pending-send cache, finalization is deferred, and the callback remains registered
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
    assert stream.abort() is False          # Idempotent
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
    assert stream.push("你好。") is True       # Accept it even before playback starts
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
    # One main-chat turn: no callback is registered; enqueue as usual
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
    # Ordinary chat: no streaming registration exists on the manager (including __new__ instances and old fixtures without this attribute)
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


# ── Review round 1: claim TTS turns in order, lose ownership, failure signals ────────────


async def test_back_to_back_streams_play_in_open_order():
    # The homecoming ritual line and summary: each of two streams pushes and finishes once, starting back-to-back in the same event-loop tick
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
    # Old stream has not finished: new stream does not claim the turn, and the old stream's speech ID and done flag remain untouched
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
        mgr.current_speech_id = "main-turn"     # Ordinary chat takes over the turn
    stream.push("被打断的后半句。")
    assert stream.finish() is True
    await _settle()
    assert _queued(mgr) == [(stream.speech_id, "串门的话，")]
    assert mgr._tts_done_queued_for_turn is False   # Do not request an end marker for this main-chat turn
    assert failed == [True] and stream.closed and stream.push("x") is False
    assert stream.speech_id not in mgr._mirror_stream_callbacks


async def test_abort_cleanup_finishes_before_the_next_stream_claims(monkeypatch):
    mgr = _mgr(ready=False)
    order: list[str] = []

    async def slow_interrupt():
        order.append("interrupt-start")
        await asyncio.sleep(0.05)
        mgr.tts_pending_chunks.clear()          # Clear the pending-send cache, just like _finish_tts_clear
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
    # The next stream claims only after interruption cleanup of the previous one completes: its pending text is not cleared along with it
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
    assert mgr.interrupts == [stream.speech_id]   # A partial sentence already sent to TTS is interrupted
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
    # Late old stream finds that ownership has transferred: do not enqueue; report failure
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


# -- Review round 2 --


async def test_family_turn_started_after_open_is_not_taken_over():
    # After the homecoming segment starts but before it claims the turn, a family member speaks first (ordinary dialogue starts a new turn): this stream yields and does not take over that turn
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
    # Worker is not ready: the previous line's end marker is only deferred; the next line must wait until it is actually queued before claiming, so the two lines are not merged
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
    # Interrupt the old line first (it is still the current speech), then let the new line claim the turn
    assert order == [f"interrupt:{stuck.speech_id}"]
    assert failed == [True] and stuck.closed
    assert mgr.current_speech_id == nxt.speech_id
    assert _queued(mgr)[-2:] == [(nxt.speech_id, "下一句。"), (None, None)]


async def test_deferred_end_discarded_elsewhere_fails_and_hands_over(monkeypatch):
    # The deferred end marker is cleared elsewhere (ordinary-dialogue interruption / session rebuild): this stream reports failure and releases the turn,
    # so the next line does not have to wait for the full timeout
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
    mgr.tts_pending_chunks.clear()                 # Same cleanup as _finish_tts_clear
    mgr._tts_done_pending_until_ready = False
    await asyncio.sleep(0.05)
    await _settle()
    assert failed == [True]
    assert mgr.current_speech_id == second.speech_id
    assert first.speech_id not in mgr._mirror_stream_ends


# ── Review round 1 (cloud) ────────────────────────────────────────────────


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
    assert ref() is None                       # Bypass tail so the _predecessor chain retains the full history
    assert mgr._mirror_stream_tail._on_failed is None   # The last item must not retain the failure callback (and its closure) either
    assert failed == []


async def test_middle_stream_aborted_before_its_claim_does_not_fail_the_next():
    # A, B, and C start in the same tick; B is cancelled while waiting for A: C waits for A to finish before claiming, without falsely assuming "the turn was taken over"
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
    assert mgr.current_speech_id == a.speech_id      # A is still speaking: C does not claim
    a.push("说完了。")
    a.finish()
    await _settle(60)
    assert failed == [] and mgr.interrupts == []
    assert _queued(mgr) == [(a.speech_id, "第一段，"), (a.speech_id, "说完了。"), (None, None),
                            (c.speech_id, "第三句。"), (None, None)]


async def test_stream_opened_after_a_claim_waits_through_an_aborted_middle_one():
    # A claimed before C started (C's base is A): after B is cancelled, C still cannot take over A while it is speaking
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
    # A still owns the turn: when the timeout expires, fail A and interrupt its partial sentence first; then C claims
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
    first.cancel()                          # If the party waiting to finish is cancelled, the finish operation itself still completes normally
    await _settle()
    gate.set()
    await asyncio.wait_for(second, 5)
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
    giving_up = asyncio.ensure_future(owner._give_up())   # A successor reaches its timeout exactly while interruption cleanup is underway
    await _settle()
    gate.set()
    await asyncio.wait_for(giving_up, 5)
    assert failed == []


# ── Review round 2 (cloud) / Codex ─────────────────────────────────────────


async def test_claim_preempts_a_proactive_turn_like_mirror_assistant_speech():
    from main_logic.session_state import SessionStateMachine

    mgr = _mgr()
    mgr.state = SessionStateMachine(lanlan_name="T")
    assert await mgr.state.try_start_proactive()        # Proactive message is being prepared
    stream = _open(mgr)
    stream.push("我回来啦。")
    await _settle()
    # If the proactive message sees that it was preempted while holding the claim lock, it yields without taking the turn away from this stream
    assert mgr.state.is_proactive_preempted() is True
    assert mgr.current_speech_id == stream.speech_id


async def test_claim_without_a_proactive_turn_marks_nothing_sticky():
    from main_logic.session_state import SessionStateMachine

    mgr = _mgr()
    mgr.state = SessionStateMachine(lanlan_name="T")
    stream = _open(mgr)
    stream.push("我回来啦。")
    stream.finish()
    await _settle(40)
    assert mgr.state.is_proactive_preempted() is False
    assert _queued(mgr) == [(stream.speech_id, "我回来啦。"), (None, None)]


async def test_claim_fires_user_input_with_the_stream_id():
    mgr = _mgr()
    stream = _open(mgr)
    stream.push("你好。")
    await _settle()
    assert mgr.state.preempt_marked is True
    assert [kw.get("sid") for ev, kw in mgr.state.events if ev.name == "USER_INPUT"] == [stream.speech_id]


async def test_worker_dying_mid_line_interrupts_the_queued_half(monkeypatch):
    mgr = _mgr()
    failed: list[bool] = []
    stream = _open(mgr, failed=failed)
    stream.push("前半句，")
    await _settle()
    mgr.tts_thread = _DeadThread()
    stream.push("后半句。")
    await _settle()
    assert failed == [True] and mgr.interrupts == [stream.speech_id]
    assert stream._released.done()


async def test_end_marker_refused_for_no_worker_interrupts_first(monkeypatch):
    mgr = _mgr()
    failed: list[bool] = []
    stream = _open(mgr, failed=failed)
    stream.push("一句话。")
    await _settle()
    mgr._request_tts_done_locked = lambda: MirrorSpeechStream.NO_WORKER
    stream.finish()
    await _settle()
    assert failed == [True] and mgr.interrupts == [stream.speech_id]
    assert stream._released.done()


async def test_finish_reporting_no_worker_interrupts_the_queued_text():
    mgr = _mgr()
    stream = _open(mgr)
    stream.push("你好。")
    await _settle()
    mgr.tts_thread = _DeadThread()
    assert stream.finish() == MirrorSpeechStream.NO_WORKER
    await _settle()
    assert mgr.interrupts == [stream.speech_id] and stream._released.done()


async def test_worker_dying_while_the_end_marker_waits_fails_and_hands_over(monkeypatch):
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
    mgr.tts_thread = _DeadThread()                 # The worker waiting to become ready has died: the deferred marker remains, with nobody to resend it
    await asyncio.sleep(0.05)
    await _settle()
    assert failed == [True] and mgr.interrupts[0] == first.speech_id
    assert first._released.done()


# ── Review round 3 (cloud) ─────────────────────────────────────────────────


async def test_finish_without_worker_hands_over_only_after_its_interrupt():
    # As with cancellation: when finish immediately reports no_worker, the cancelled background task must not release ownership first;
    # the successor claims only after interruption cleanup finishes, so its pending text is not cleared by that cleanup
    mgr = _mgr(ready=False)
    order: list[str] = []

    async def slow_interrupt():
        order.append(f"interrupt-start:{mgr.current_speech_id}")
        await asyncio.sleep(0.05)
        mgr.tts_pending_chunks.clear()
        order.append(f"interrupt-end:{mgr.current_speech_id}")

    mgr.interrupt_mirror_speech = slow_interrupt
    first = _open(mgr)
    first.push("前一句")
    await _settle()
    second = _open(mgr)
    second.push("后一句。")
    await _settle()
    mgr.tts_thread = _DeadThread()
    assert first.finish() == MirrorSpeechStream.NO_WORKER

    async def revived():
        mgr.tts_thread = _FakeAliveThread()

    mgr.ensure_tts_pipeline_alive = revived
    await asyncio.sleep(0.1)
    await _settle()
    assert order == [f"interrupt-start:{first.speech_id}", f"interrupt-end:{first.speech_id}"]
    assert mgr.current_speech_id == second.speech_id
    assert mgr.tts_pending_chunks == [(second.speech_id, "后一句。")]
