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

"""CosyVoice completion ownership, driven through the real worker loop.

The DashScope SDK reports completion with no reference to the synthesizer that
finished, and the worker shares one callback object across turns and reconnects.
A completion from a superseded synthesizer therefore reads, from inside the
callback, exactly like the current turn finishing -- and reporting the audio
stream closed while the current turn is still speaking is the defect the signal
exists to remove. These tests fake the SDK so the out-of-order completion can be
fired deliberately.
"""

import queue
import sys
import time
import threading
import types

import pytest

from main_logic.tts_client._infra import TTS_AUDIO_DONE_SENTINEL, TTS_SHUTDOWN_SENTINEL


# synthesizer 只在缓冲攒够 TTS_LANG_DETECT_MIN_CHARS 才建，测试文本要够长。
_LONG_ENOUGH = "这是一段足够长的测试文本用来触发合成器创建" * 2


class _FakeAudioFormat:
    OGG_OPUS_48KHZ_MONO_64KBPS = "ogg-opus-48k"


class _FakeResultCallback:
    """Stand-in for dashscope's ResultCallback base (it only defines hooks)."""


class _FakeRequest:
    def getFinishRequest(self):
        return {"action": "finish"}


class _FakeWebSocket:
    def __init__(self, synth):
        self._synth = synth

    def send(self, payload):
        self._synth.finish_payloads.append(payload)
        if _FakeSynthesizer.complete_on_finish:
            self._synth.callback.on_complete()


class _FakeSynthesizer:
    """Records its callback so the test can fire completions out of order."""

    instances = []
    # 每次 streaming_call 依次消费一个脚本项（拿到 synth 实例）；空了就走默认。
    script = []
    # True 时 ws.send(FINISH) 会同步打回 on_complete —— 模拟 SDK 接收线程
    # 抢在 send 返回前就完成的那一瞬。
    complete_on_finish = False

    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.closed = False
        self.finish_payloads = []
        self.spoken = []
        self.ws = _FakeWebSocket(self)
        self.request = _FakeRequest()
        _FakeSynthesizer.instances.append(self)
        self.callback.on_open()

    def streaming_call(self, text):
        self.spoken.append(text)
        if _FakeSynthesizer.script:
            _FakeSynthesizer.script.pop(0)(self)
            return
        # 一次给够 bootstrap 阈值（1024B），逼 worker 立刻把音频投出去，
        # 这样 _active_sid 会被置上 —— 与真实链路一致。
        self.callback.on_data(b"\x00" * 2048)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_dashscope(monkeypatch):
    """Install a fake dashscope package for the duration of one test."""
    root = types.ModuleType("dashscope")
    root.api_key = None
    audio = types.ModuleType("dashscope.audio")
    tts_v2 = types.ModuleType("dashscope.audio.tts_v2")
    tts_v2.ResultCallback = _FakeResultCallback
    tts_v2.SpeechSynthesizer = _FakeSynthesizer
    tts_v2.AudioFormat = _FakeAudioFormat
    audio.tts_v2 = tts_v2
    root.audio = audio
    monkeypatch.setitem(sys.modules, "dashscope", root)
    monkeypatch.setitem(sys.modules, "dashscope.audio", audio)
    monkeypatch.setitem(sys.modules, "dashscope.audio.tts_v2", tts_v2)
    _FakeSynthesizer.instances = []
    _FakeSynthesizer.script = []
    _FakeSynthesizer.complete_on_finish = False
    return root


@pytest.fixture
def worker(fake_dashscope, monkeypatch):
    """Run cosyvoice_vc_tts_worker on a thread with its environment stubbed."""
    from main_logic.tts_client.workers import cosyvoice as mod

    monkeypatch.setattr(mod, "configure_dashscope_sdk_urls", lambda *a, **k: None)
    monkeypatch.setattr(mod, "get_config_manager", lambda: types.SimpleNamespace(
        get_model_api_config=lambda _name: {"base_url": ""}
    ))
    import main_logic.tts_client as pkg
    monkeypatch.setattr(pkg, "_get_voice_meta", lambda _vid: {}, raising=False)

    request_queue: queue.Queue = queue.Queue()
    response_queue: queue.Queue = queue.Queue()
    thread = threading.Thread(
        target=mod.cosyvoice_vc_tts_worker,
        args=(request_queue, response_queue, "test-key", "voice-x"),
        daemon=True,
    )
    thread.start()
    assert _wait_for(lambda: _peek_ready(response_queue), "ready signal")
    yield request_queue, response_queue, thread
    request_queue.put((TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)


def _peek_ready(response_queue):
    try:
        item = response_queue.get(timeout=0.2)
    except queue.Empty:
        return False
    return item == ("__ready__", True)


def _wait_for(predicate, what, timeout=5.0):
    deadline = threading.Event()
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return True
        deadline.wait(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def _drain(response_queue):
    out = []
    while True:
        try:
            out.append(response_queue.get_nowait())
        except queue.Empty:
            return out


def _audio_done_ids(items):
    return [sid for kind, sid in
            ((i[0], i[1]) for i in items if isinstance(i, tuple) and len(i) == 2)
            if kind == TTS_AUDIO_DONE_SENTINEL]


def test_superseded_synthesizer_completion_does_not_close_the_current_turn(worker):
    request_queue, response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一轮。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    request_queue.put((None, None))
    first = _FakeSynthesizer.instances[0]
    _wait_for(lambda: first.finish_payloads, "FINISH for the first turn")

    # 第二轮：sid 切换会关掉旧 synthesizer 并建新的
    request_queue.put(("speech-b", _LONG_ENOUGH + "第二轮。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "second synthesizer")
    second = _FakeSynthesizer.instances[1]
    request_queue.put((None, None))
    _wait_for(lambda: second.finish_payloads, "FINISH for the second turn")

    # 第一代 synthesizer 的完成通知迟到了：此刻回调里的共享状态描述的是第二轮。
    first.callback.on_complete()
    assert _audio_done_ids(_drain(response_queue)) == [], (
        "a superseded synthesizer's completion must not close the current turn"
    )

    # 本轮自己的完成通知才算数
    second.callback.on_complete()
    assert _audio_done_ids(_drain(response_queue)) == ["speech-b"]


def test_reconnect_within_a_turn_does_not_close_the_stream(worker):
    """A mid-turn rebuild also completes the old synthesizer -- not a round end."""
    request_queue, response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一轮。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    first = _FakeSynthesizer.instances[0]

    # 本轮还没发过 FINISH：任何完成通知都不该被当成收尾
    first.callback.on_complete()
    assert _audio_done_ids(_drain(response_queue)) == []


def test_idle_keepalive_finish_does_not_close_the_stream(fake_dashscope, monkeypatch):
    """The 15s keep-alive FINISH beats a socket timeout; the turn is not over.

    More text of the same speech can still follow, so the completion it triggers
    must not be reported as the audio stream closing.
    """
    from main_logic.tts_client.workers import cosyvoice as mod

    monkeypatch.setattr(mod, "configure_dashscope_sdk_urls", lambda *a, **k: None)
    monkeypatch.setattr(mod, "get_config_manager", lambda: types.SimpleNamespace(
        get_model_api_config=lambda _name: {"base_url": ""}
    ))
    import main_logic.tts_client as pkg
    monkeypatch.setattr(pkg, "_get_voice_meta", lambda _vid: {}, raising=False)

    # 假时钟：只把 time() 往前推，sleep 仍走真的，别把 worker 的轮询变成忙等。
    real_time = time.time
    offset = {"seconds": 0.0}
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(
        time=lambda: real_time() + offset["seconds"],
        sleep=time.sleep,
    ))

    request_queue: queue.Queue = queue.Queue()
    response_queue: queue.Queue = queue.Queue()
    thread = threading.Thread(
        target=mod.cosyvoice_vc_tts_worker,
        args=(request_queue, response_queue, "test-key", "voice-x"),
        daemon=True,
    )
    thread.start()
    try:
        _wait_for(lambda: _peek_ready(response_queue), "ready signal")
        request_queue.put(("speech-a", _LONG_ENOUGH + "第一轮。"))
        _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "synthesizer")
        synth = _FakeSynthesizer.instances[0]

        # 越过空闲阈值：worker 会主动发一次 FINISH 保活
        offset["seconds"] = 60.0
        _wait_for(lambda: synth.finish_payloads, "keep-alive FINISH")

        synth.callback.on_complete()
        assert _audio_done_ids(_drain(response_queue)) == [], (
            "a keep-alive FINISH does not end the round"
        )
    finally:
        request_queue.put((TTS_SHUTDOWN_SENTINEL, None))
        thread.join(timeout=5)


def test_completion_racing_the_finish_send_still_closes_the_stream(worker):
    """The SDK thread can complete before ws.send returns; arming must precede it."""
    request_queue, response_queue, _thread = worker
    _FakeSynthesizer.complete_on_finish = True

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一轮。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "synthesizer")
    synth = _FakeSynthesizer.instances[0]
    request_queue.put((None, None))
    _wait_for(lambda: synth.finish_payloads, "FINISH")

    seen = []

    def _closed():
        seen.extend(_drain(response_queue))
        return _audio_done_ids(seen) == ["speech-a"]

    _wait_for(_closed, "audio_done from the completion that raced the FINISH send")


def test_reconnect_does_not_splice_stale_buffer_into_the_new_stream(worker):
    """A rebuilt connection restarts the OGG stream; leftovers would corrupt it."""
    request_queue, response_queue, _thread = worker

    def _half_packet(synth):
        synth.callback.on_data(b"A" * 500)   # 低于 1024 的 bootstrap 阈值，留在缓冲里

    def _boom(_synth):
        raise RuntimeError("stream broke")

    def _fresh_stream(synth):
        synth.callback.on_data(b"B" * 2048)

    _FakeSynthesizer.script = [_half_packet, _boom, _fresh_stream]

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一轮。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    request_queue.put(("speech-a", "同一轮的后续文本"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "rebuilt synthesizer")

    seen = []

    def _got_audio():
        seen.extend(item for item in _drain(response_queue)
                    if isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__")
        return bool(seen)

    _wait_for(_got_audio, "audio from the rebuilt stream")
    payload = seen[-1][2]
    assert payload == b"B" * 2048, (
        "the rebuilt stream must not carry the dead connection's half packet "
        f"(got {len(payload)} bytes)"
    )
