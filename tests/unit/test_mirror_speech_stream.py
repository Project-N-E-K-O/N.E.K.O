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


def _open(mgr, counted=None):
    on_enqueued = (lambda n: counted.append(n)) if counted is not None else None
    return LLM.open_mirror_speech_stream(mgr, metadata={"source": "neko_visit"}, request_id="r",
                                         on_enqueued=on_enqueued)


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
