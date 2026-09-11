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

"""Text-idle soft flush: the held tail is spoken before the provider's done.

On a realtime voice route with a custom TTS voice, core only sends the round's
FINISH at the provider's ``response.done``. CosyVoice keeps the trailing
sentence unsynthesized until FINISH, and the lanlan.app proxy delivers
``response.done`` only after streaming Gemini's own (discarded) audio — 1.2s
after the last transcript delta when things go well, 9.5s when they do not. The
tail then surfaces after the user has already started the next turn.

The fix is a flush that is not a round end: core asks the worker to finish what
it has when the turn's text goes quiet, the worker drains that stream, and
anything that follows (more text, the real round end) is reconciled against the
drained stream instead of the closed socket.
"""

import asyncio
import time
from queue import Queue
from unittest.mock import MagicMock

import pytest

from main_logic.core import LLMSessionManager, tts_runtime as tts_runtime_mod
from main_logic.tts_client._infra import TTS_SOFT_FLUSH_SENTINEL
from tests.unit.test_cosyvoice_audio_done_generation import (  # noqa: F401 - fixtures
    _LONG_ENOUGH,
    _FakeSynthesizer,
    _audio_done_ids,
    _drain,
    _wait_for,
    fake_dashscope,
    worker,
)


def _settle(seconds=0.15):
    """Give the worker thread a few polling ticks to act (or to not act)."""
    time.sleep(seconds)


# ───────────────────────── worker: cosyvoice ─────────────────────────


def test_soft_flush_finishes_the_held_tail_without_closing_the_round(worker):
    request_queue, response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "尾句还没说完"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "synthesizer")
    synth = _FakeSynthesizer.instances[0]

    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    _wait_for(lambda: synth.finish_payloads, "FINISH from the soft flush")

    # 服务端放完尾包：这只是一次软 flush，本轮没结束，不能宣告音频流关闭
    synth.callback.on_complete()
    _settle()
    assert _audio_done_ids(_drain(response_queue)) == []

    # 真正的收尾到了：旧流已经放干净，直接补收尾，同一条连接不再发第二个 FINISH
    request_queue.put((None, None))
    seen = []
    _wait_for(lambda: (seen.extend(_drain(response_queue)) or _audio_done_ids(seen) == ["speech-a"]),
              "audio_done after the round end")
    assert len(synth.finish_payloads) == 1, "a finished synthesizer must not be finished twice"


def test_round_end_while_the_soft_finished_stream_drains_waits_for_its_completion(worker):
    request_queue, response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "尾句"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "synthesizer")
    synth = _FakeSynthesizer.instances[0]
    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    _wait_for(lambda: synth.finish_payloads, "FINISH from the soft flush")

    # 收尾先于完成通知到达：尾包还在路上，此刻宣告关闭就是早发
    request_queue.put((None, None))
    _settle()
    assert _audio_done_ids(_drain(response_queue)) == []
    assert len(synth.finish_payloads) == 1

    synth.callback.on_complete()
    seen = []
    _wait_for(lambda: (seen.extend(_drain(response_queue)) or _audio_done_ids(seen) == ["speech-a"]),
              "audio_done once the drained stream completed")


def test_text_after_soft_flush_waits_for_the_drain_then_continues_on_a_new_stream(worker):
    request_queue, response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一段。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    first = _FakeSynthesizer.instances[0]
    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    _wait_for(lambda: first.finish_payloads, "FINISH from the soft flush")

    # 同一轮又来文本：旧流还没放干净，不能往已 FINISH 的连接里塞，也不能开新流
    request_queue.put(("speech-a", "同一轮后面还有话。"))
    _settle()
    assert len(_FakeSynthesizer.instances) == 1
    assert first.spoken == [_LONG_ENOUGH + "第一段。"]

    # 旧流放干净 → 新 synthesizer 接着说攒下的文本
    first.callback.on_complete()
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "continuation synthesizer")
    second = _FakeSynthesizer.instances[1]
    assert second.spoken == ["同一轮后面还有话。"]
    assert first.closed, "the drained stream is released once the continuation starts"

    request_queue.put((None, None))
    _wait_for(lambda: second.finish_payloads, "round-end FINISH on the continuation stream")
    second.callback.on_complete()
    seen = []
    _wait_for(lambda: (seen.extend(_drain(response_queue)) or _audio_done_ids(seen) == ["speech-a"]),
              "exactly one audio_done for the whole speech")


def test_stale_close_from_the_drained_stream_does_not_mark_the_new_one_lost(worker):
    request_queue, response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一段。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    first = _FakeSynthesizer.instances[0]
    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    _wait_for(lambda: first.finish_payloads, "FINISH from the soft flush")
    first.callback.on_complete()

    request_queue.put(("speech-a", "同一轮后面还有话。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "continuation synthesizer")
    second = _FakeSynthesizer.instances[1]

    # 服务端这时才关掉旧连接：这是上一代的事，当代连接没有断
    first.callback.on_close()
    first.callback.on_error("request timeout after 23 seconds")
    _settle()
    assert all(item[0] not in ("__reconnecting__", "__error__") for item in _drain(response_queue)), (
        "a superseded stream's errors must not be reported as the current one's"
    )

    request_queue.put((None, None))
    _wait_for(lambda: second.finish_payloads,
              "round-end FINISH still sent: the new stream was never lost")


def test_late_pages_from_the_retired_stream_do_not_reach_the_continuation(worker):
    """close() does not stop the SDK thread; whatever it still delivers is stale."""
    request_queue, response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一段。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    first = _FakeSynthesizer.instances[0]
    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    _wait_for(lambda: first.finish_payloads, "FINISH from the soft flush")
    first.callback.on_complete()
    request_queue.put(("speech-a", "同一轮后面还有话。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "continuation synthesizer")
    _drain(response_queue)

    first.callback.on_data(b"X" * 4096)  # 旧连接迟到的页
    _settle()
    leaked = [item for item in _drain(response_queue)
              if isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__" and b"X" in item[2]]
    assert leaked == [], "a retired stream's pages must not be spliced into the new stream"


def test_soft_flush_arriving_during_the_drain_is_applied_to_the_continuation(worker):
    """core re-arms its timer for text buffered during the drain; that flush must not be lost."""
    request_queue, _response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一段。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    first = _FakeSynthesizer.instances[0]
    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    _wait_for(lambda: first.finish_payloads, "FINISH from the soft flush")

    # 排空期间：续接文本到达，随后 core 为它武装的定时器也到了
    request_queue.put(("speech-a", "同一轮后面还有话。"))
    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    _settle()
    assert len(_FakeSynthesizer.instances) == 1

    first.callback.on_complete()
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "continuation synthesizer")
    second = _FakeSynthesizer.instances[1]
    assert second.spoken == ["同一轮后面还有话。"]
    # 没有 (None, None)：续接流的尾句靠排空期间记下的软 flush 释放
    _wait_for(lambda: second.finish_payloads, "FINISH carried over to the continuation stream")


def test_text_after_a_pending_soft_flush_supersedes_it(worker):
    """Newer text during the drain voids the earlier flush; core will re-arm for it."""
    request_queue, _response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一段。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    first = _FakeSynthesizer.instances[0]
    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    _wait_for(lambda: first.finish_payloads, "FINISH from the soft flush")

    request_queue.put(("speech-a", "第二段"))
    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    request_queue.put(("speech-a", "，还没说完"))
    _settle()  # 三条都要在旧流放干净之前被 worker 消化掉，顺序才是这条用例要钉的
    first.callback.on_complete()
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "continuation synthesizer")
    second = _FakeSynthesizer.instances[1]
    _settle()
    assert second.spoken == ["第二段，还没说完"]
    assert second.finish_payloads == [], "the flush predates the newest text; wait for core's next one"


def test_soft_flush_whose_finish_fails_to_send_drops_the_dead_stream(worker):
    """No FINISH on the wire means no completion will ever come; do not wait for it."""
    request_queue, _response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一段。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    first = _FakeSynthesizer.instances[0]

    def _broken_send(_payload):
        raise ConnectionError("socket gone")

    first.ws.send = _broken_send
    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    _wait_for(lambda: first.closed, "the dead stream is released")

    # 后续文本不该被扣到 2.5s 排空期限，立刻走新 synthesizer
    request_queue.put(("speech-a", _LONG_ENOUGH + "接着说。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "new synthesizer right away", timeout=1.0)
    assert _FakeSynthesizer.instances[1].spoken == [_LONG_ENOUGH + "接着说。"]


def _block_next_reset(shared_callback):
    """Make the next reset_bootstrap_state() (from the SDK thread) wait on an event.

    Simulates the SDK callback being descheduled between flushing its buffers
    and finishing its cleanup — the window both races below live in.
    """
    import threading

    entered, release = threading.Event(), threading.Event()
    real_reset = shared_callback.reset_bootstrap_state
    armed = {"on": True}

    def _reset():
        if armed["on"]:
            armed["on"] = False
            entered.set()
            release.wait(5)
        real_reset()

    shared_callback.reset_bootstrap_state = _reset
    return entered, release


def test_drain_completion_is_published_only_after_the_old_stream_reset(worker):
    """The worker must not start the continuation while the old callback still owns the buffers."""
    import threading

    request_queue, _response_queue, _thread = worker
    request_queue.put(("speech-a", _LONG_ENOUGH + "第一段。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    first = _FakeSynthesizer.instances[0]
    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    _wait_for(lambda: first.finish_payloads, "FINISH from the soft flush")
    request_queue.put(("speech-a", "同一轮后面还有话。"))
    _settle()

    entered, release = _block_next_reset(first.callback._inner)
    threading.Thread(target=first.callback.on_complete, daemon=True).start()
    assert entered.wait(2), "completion callback did not reach its cleanup"
    _settle()
    assert len(_FakeSynthesizer.instances) == 1, (
        "continuation started while the old completion was still resetting shared state"
    )
    release.set()
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "continuation after the reset finished")


def test_timeout_retirement_waits_for_an_in_flight_completion(fake_dashscope, monkeypatch):
    """A callback past its generation check must finish before the generation is retired."""
    import threading
    import types

    from main_logic.tts_client.workers import cosyvoice as mod

    monkeypatch.setattr(mod, "configure_dashscope_sdk_urls", lambda *a, **k: None)
    monkeypatch.setattr(mod, "get_config_manager", lambda: types.SimpleNamespace(
        get_model_api_config=lambda _name: {"base_url": ""}
    ))
    monkeypatch.setattr("main_logic.tts_client._get_voice_meta", lambda _vid: {})
    real_time = time.time
    offset = {"seconds": 0.0}
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(
        time=lambda: real_time() + offset["seconds"], sleep=time.sleep,
    ))

    request_queue, response_queue = Queue(), Queue()
    thread = threading.Thread(target=mod.cosyvoice_vc_tts_worker,
                              args=(request_queue, response_queue, "test-key", "voice-x"), daemon=True)
    thread.start()
    try:
        _wait_for(lambda: response_queue.get(timeout=0.2) == ("__ready__", True), "ready signal")
        request_queue.put(("speech-a", _LONG_ENOUGH + "第一段。"))
        _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
        first = _FakeSynthesizer.instances[0]
        request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
        _wait_for(lambda: first.finish_payloads, "FINISH from the soft flush")
        request_queue.put(("speech-a", "同一轮后面还有话。"))
        _settle()

        # 完成通知已过 generation 检查、卡在清理里；此时排空期限到期
        entered, release = _block_next_reset(first.callback._inner)
        threading.Thread(target=first.callback.on_complete, daemon=True).start()
        assert entered.wait(2)
        offset["seconds"] = 10.0
        _settle(0.3)
        assert len(_FakeSynthesizer.instances) == 1, (
            "retired the generation under a callback that was still mutating shared state"
        )
        release.set()
        _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "continuation once the callback finished")
        assert _FakeSynthesizer.instances[1].spoken == ["同一轮后面还有话。"]
    finally:
        request_queue.put(("__shutdown__", None))
        thread.join(timeout=5)


def test_soft_flush_for_another_speech_is_ignored(worker):
    request_queue, _response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "尾句"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "synthesizer")
    synth = _FakeSynthesizer.instances[0]

    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-stale"))
    _settle()
    assert synth.finish_payloads == [], "a late soft flush for a dead speech must not cut the live one"


def test_soft_flush_releases_a_short_tail_still_below_the_language_buffer(worker):
    """A tail shorter than TTS_LANG_DETECT_MIN_CHARS has no synthesizer yet."""
    request_queue, _response_queue, _thread = worker

    request_queue.put(("speech-a", "嗯。"))
    _settle()
    assert _FakeSynthesizer.instances == [], "still buffering for language detection"

    request_queue.put((TTS_SOFT_FLUSH_SENTINEL, "speech-a"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "synthesizer built for the short tail")
    synth = _FakeSynthesizer.instances[0]
    assert synth.spoken == ["嗯。"]
    _wait_for(lambda: synth.finish_payloads, "FINISH for the short tail")


# ───────────────────────── core: idle timer ─────────────────────────


def _make_mgr() -> LLMSessionManager:
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.tts_cache_lock = asyncio.Lock()
    mgr.tts_request_queue = Queue()
    mgr.tts_pending_chunks = []
    mgr.tts_thread = MagicMock()
    mgr.tts_thread.is_alive.return_value = True
    mgr.tts_ready = True
    mgr.current_speech_id = "s1"
    mgr.input_mode = "audio"
    mgr._tts_done_queued_for_turn = False
    mgr._tts_done_pending_until_ready = False
    mgr._tts_soft_flush_supported = True
    mgr._tts_soft_flush_task = None
    mgr._bg_tasks = set()
    # _request_tts_done_locked 的 stripper 依赖
    mgr._tts_markdown_stripper = MagicMock()
    mgr._tts_markdown_stripper.flush.return_value = ""
    mgr._tts_bracket_stripper = MagicMock()
    mgr._tts_bracket_stripper.flush.return_value = ""
    mgr._tts_norm_speech_id = "s1"
    mgr._tts_replay_done = False
    return mgr


@pytest.fixture
def fast_idle(monkeypatch):
    monkeypatch.setattr(
        tts_runtime_mod.TtsRuntimeMixin,
        "_tts_soft_flush_idle_seconds",
        staticmethod(lambda: 0.05),
    )


def _queued(mgr):
    return _drain(mgr.tts_request_queue)


@pytest.mark.asyncio
async def test_idle_text_fires_a_soft_flush_for_the_live_speech(fast_idle):
    mgr = _make_mgr()
    LLMSessionManager._arm_tts_soft_flush(mgr, "s1")
    await asyncio.sleep(0.2)
    assert _queued(mgr) == [(TTS_SOFT_FLUSH_SENTINEL, "s1")]


@pytest.mark.asyncio
async def test_each_chunk_rearms_the_timer_so_only_one_flush_fires(fast_idle):
    mgr = _make_mgr()
    LLMSessionManager._arm_tts_soft_flush(mgr, "s1")
    await asyncio.sleep(0.02)
    LLMSessionManager._arm_tts_soft_flush(mgr, "s1")
    await asyncio.sleep(0.02)
    LLMSessionManager._arm_tts_soft_flush(mgr, "s1")
    await asyncio.sleep(0.2)
    assert _queued(mgr) == [(TTS_SOFT_FLUSH_SENTINEL, "s1")]


@pytest.mark.asyncio
async def test_round_end_cancels_the_pending_soft_flush(fast_idle):
    mgr = _make_mgr()
    LLMSessionManager._arm_tts_soft_flush(mgr, "s1")
    assert LLMSessionManager._request_tts_done_locked(mgr) == "queued"
    # 定时器在 done 入队的同一步被撤掉，而不是留到点火时再靠 flag 自查
    assert mgr._tts_soft_flush_task is None
    await asyncio.sleep(0.2)
    assert _queued(mgr) == [(None, None)], "the real FINISH makes the soft one pointless"


@pytest.mark.asyncio
async def test_soft_flush_stands_down_when_the_speech_moved_on(fast_idle):
    mgr = _make_mgr()
    LLMSessionManager._arm_tts_soft_flush(mgr, "s1")
    mgr.current_speech_id = "s2"  # barge-in / rotation before the timer fired
    await asyncio.sleep(0.2)
    assert _queued(mgr) == []


@pytest.mark.asyncio
async def test_soft_flush_stands_down_once_done_is_queued_or_deferred(fast_idle):
    mgr = _make_mgr()
    LLMSessionManager._arm_tts_soft_flush(mgr, "s1")
    mgr._tts_done_pending_until_ready = True
    await asyncio.sleep(0.2)
    assert _queued(mgr) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field, value", [
    ("_tts_soft_flush_supported", False),  # worker 不认这个哨兵（会当成新 sid）
    ("input_mode", "text"),                # 文本模式的 completion 紧跟最后一个 chunk
])
async def test_soft_flush_is_not_armed_where_it_cannot_help(fast_idle, field, value):
    mgr = _make_mgr()
    setattr(mgr, field, value)
    LLMSessionManager._arm_tts_soft_flush(mgr, "s1")
    assert mgr._tts_soft_flush_task is None
    await asyncio.sleep(0.2)
    assert _queued(mgr) == []


@pytest.mark.asyncio
async def test_cancel_stops_an_armed_timer(fast_idle):
    mgr = _make_mgr()
    LLMSessionManager._arm_tts_soft_flush(mgr, "s1")
    LLMSessionManager._cancel_tts_soft_flush(mgr)
    await asyncio.sleep(0.2)
    assert _queued(mgr) == []
    assert mgr._tts_soft_flush_task is None


def test_registry_declares_soft_flush_only_for_workers_that_handle_it():
    from main_logic.tts_client import TTS_PROVIDER_REGISTRY

    supporting = {name for name, meta in TTS_PROVIDER_REGISTRY.items() if meta.soft_flush}
    # 加 provider 时同步扩这个集合，并给它的 worker 补 TTS_SOFT_FLUSH_SENTINEL 分支。
    assert supporting == {"cosyvoice"}
