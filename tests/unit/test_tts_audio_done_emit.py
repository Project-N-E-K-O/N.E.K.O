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

"""Behavioural guards for the per-speech audio_done sentinel.

The frontend finalizes lip-sync the moment the signal arrives, so the sentinel
must trail every audio chunk of its own round, must never carry another round's
speech_id, and must stay silent for an interrupted or under-drained round.
These tests drive the real ``_run_sentence_tts_worker`` skeleton (the family
behind cogtts / doubao / gemini / mimo / minimax / openai) with a fake queue
pair, so they observe the queue as ``tts_response_handler`` would.
"""

import asyncio
import queue
import threading
import time

from main_logic import tts_client
from main_logic.tts_client._infra import AudioDoneEmitter, TTS_AUDIO_DONE_SENTINEL


def _echo_setup():
    """Build a setup() whose synthesize emits one deterministic chunk per sentence."""

    async def setup(queue_proxy):
        async def synthesize(text, _speech_id):
            queue_proxy.put(b"audio-" + text.encode())

        return synthesize, None

    return setup


def _gated_setup(gate, started, gated_text):
    """Like ``_echo_setup`` but ``gated_text`` stalls until the test releases it.

    The stall keeps one sentence in flight while the worker is already handling
    the round's terminal marker, which is the window an early signal would slip
    through.
    """

    async def setup(queue_proxy):
        async def synthesize(text, _speech_id):
            if text == gated_text:
                started.set()
                while not gate.is_set():
                    await asyncio.sleep(0.01)
            queue_proxy.put(b"audio-" + text.encode())

        return synthesize, None

    return setup


def _start_worker(setup, label="Mock TTS", response_queue=None):
    request_queue = queue.Queue()
    response_queue = response_queue if response_queue is not None else queue.Queue()
    thread = threading.Thread(
        target=tts_client._run_sentence_tts_worker,
        args=(request_queue, response_queue, setup),
        kwargs={"label": label},
        daemon=True,
    )
    thread.start()
    assert _wait_for_item(response_queue, lambda item: item == ("__ready__", True))
    return request_queue, response_queue, thread


def _wait_for_item(q, predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            item = q.get(timeout=max(0.01, deadline - time.time()))
        except queue.Empty:
            continue
        if predicate(item):
            return item
    raise AssertionError("等待的队列消息未出现")


def _shutdown(request_queue, thread, timeout=10):
    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=timeout)
    assert not thread.is_alive()


def _drain(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items


def _audio_done_items(items):
    return [
        item
        for item in items
        if isinstance(item, tuple) and item and item[0] == TTS_AUDIO_DONE_SENTINEL
    ]


class _StuckSlotQueue(queue.Queue):
    """Queue that kills the drain loop right before it frees a sentence slot.

    Audio still lands, but the slot never clears, so the round's drain hits its
    hard ceiling with delivery unfinished.
    """

    def put(self, item, *args, **kwargs):
        if isinstance(item, tuple) and item and item[0] == "__tts_sentence_done__":
            raise RuntimeError("sentence boundary put exploded")
        return super().put(item, *args, **kwargs)


def test_audio_done_trails_every_audio_chunk_of_the_round():
    gate = threading.Event()
    started = threading.Event()
    request_queue, response_queue, thread = _start_worker(
        _gated_setup(gate, started, "Second.")
    )

    request_queue.put(("speech-1", "First.Second."))
    request_queue.put((None, None))
    # 卡住第二句的合成，逼出"本轮文本已结束但音频还没投完"的窗口：
    # 早发的实现会在这个窗口里把哨兵插到尾音前面。
    assert started.wait(5)
    time.sleep(0.3)
    gate.set()
    _shutdown(request_queue, thread)

    assert _drain(response_queue) == [
        b"audio-First.",
        ("__tts_sentence_done__", "speech-1", "First."),
        b"audio-Second.",
        ("__tts_sentence_done__", "speech-1", "Second."),
        (TTS_AUDIO_DONE_SENTINEL, "speech-1"),
    ]


def test_repeated_terminal_marker_does_not_repeat_the_signal():
    request_queue, response_queue, thread = _start_worker(_echo_setup())

    request_queue.put(("speech-1", "Only one."))
    request_queue.put((None, None))
    request_queue.put((None, None))
    _shutdown(request_queue, thread)

    assert _audio_done_items(_drain(response_queue)) == [
        (TTS_AUDIO_DONE_SENTINEL, "speech-1")
    ]


def test_each_round_reports_its_own_speech_id_after_its_own_audio():
    request_queue, response_queue, thread = _start_worker(_echo_setup())

    request_queue.put(("speech-1", "One."))
    request_queue.put((None, None))
    request_queue.put(("speech-2", "Two."))
    request_queue.put((None, None))
    _shutdown(request_queue, thread)

    assert _drain(response_queue) == [
        b"audio-One.",
        ("__tts_sentence_done__", "speech-1", "One."),
        (TTS_AUDIO_DONE_SENTINEL, "speech-1"),
        b"audio-Two.",
        ("__tts_sentence_done__", "speech-2", "Two."),
        (TTS_AUDIO_DONE_SENTINEL, "speech-2"),
    ]


def test_interrupted_round_never_reports_its_audio_stream_closed():
    request_queue, response_queue, thread = _start_worker(_echo_setup())

    request_queue.put(("speech-1", "First."))
    _wait_for_item(response_queue, lambda item: item == b"audio-First.")
    request_queue.put(("__interrupt__", None))
    request_queue.put((None, None))
    _shutdown(request_queue, thread)

    assert _audio_done_items(_drain(response_queue)) == []


def test_round_after_an_interrupt_still_reports_closure_with_the_new_speech_id():
    request_queue, response_queue, thread = _start_worker(_echo_setup())

    request_queue.put(("speech-1", "First."))
    _wait_for_item(response_queue, lambda item: item == b"audio-First.")
    request_queue.put(("__interrupt__", None))
    request_queue.put(("speech-2", "Second."))
    request_queue.put((None, None))
    _shutdown(request_queue, thread)

    assert _audio_done_items(_drain(response_queue)) == [
        (TTS_AUDIO_DONE_SENTINEL, "speech-2")
    ]


def test_undrained_round_stays_silent_instead_of_closing_early():
    # 抽干撞上硬上限说明音频还没投完，此时发信号就是"早发"，前端会截掉尾音。
    request_queue, response_queue, thread = _start_worker(
        _echo_setup(), response_queue=_StuckSlotQueue()
    )

    request_queue.put(("speech-1", "Stuck."))
    request_queue.put((None, None))
    _shutdown(request_queue, thread, timeout=20)

    delivered = _drain(response_queue)
    assert b"audio-Stuck." in delivered
    assert _audio_done_items(delivered) == []


def test_emitter_enqueues_one_signal_per_speech_id():
    q = queue.Queue()
    emitter = AudioDoneEmitter(q)

    emitter.emit("speech-1")
    emitter.emit("speech-1")

    assert _drain(q) == [(TTS_AUDIO_DONE_SENTINEL, "speech-1")]


def test_emitter_rearms_after_reset():
    q = queue.Queue()
    emitter = AudioDoneEmitter(q)

    emitter.emit("speech-1")
    emitter.reset()
    emitter.emit("speech-1")

    assert _drain(q) == [
        (TTS_AUDIO_DONE_SENTINEL, "speech-1"),
        (TTS_AUDIO_DONE_SENTINEL, "speech-1"),
    ]


def test_emitter_stays_silent_during_interrupt_teardown():
    q = queue.Queue()
    emitter = AudioDoneEmitter(q)

    emitter.begin_interrupt()
    emitter.emit("speech-1")
    assert _drain(q) == []

    emitter.end_interrupt()
    emitter.emit("speech-1")
    assert _drain(q) == [(TTS_AUDIO_DONE_SENTINEL, "speech-1")]


def test_emitter_rejects_ownerless_and_interrupt_speech_ids():
    q = queue.Queue()
    emitter = AudioDoneEmitter(q)

    for sid in (None, "", "__interrupt__"):
        emitter.emit(sid)

    assert _drain(q) == []
    # 守卫过的 sid 不应占用去重槽位，真实 sid 仍要发得出去。
    emitter.emit("speech-1")
    assert _drain(q) == [(TTS_AUDIO_DONE_SENTINEL, "speech-1")]


def test_emitter_swallows_queue_failures():
    class _DeadQueue:
        def put(self, item):
            raise RuntimeError("queue is gone")

    # 漏发是可接受降级；投递失败不能把 worker 的收尾路径炸掉。
    AudioDoneEmitter(_DeadQueue()).emit("speech-1")
