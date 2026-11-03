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

import json
import queue
import sys
import time
import threading
import types

import pytest
from dashscope.audio.tts_v2.speech_synthesizer import Request as _SdkRequest

from main_logic.tts_client._infra import (
    TTS_AUDIO_DONE_SENTINEL,
    TTS_SHUTDOWN_SENTINEL,
    TTS_SOFT_FLUSH_SENTINEL,
)


# Create the synthesizer only after TTS_LANG_DETECT_MIN_CHARS accumulates; test text must be long enough.
_LONG_ENOUGH = "这是一段足够长的测试文本用来触发合成器创建" * 2


class _FakeAudioFormat:
    OGG_OPUS_48KHZ_MONO_64KBPS = "ogg-opus-48k"


class _FakeResultCallback:
    """Stand-in for dashscope's ResultCallback base (it only defines hooks)."""


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
    # Each streaming_call consumes one script entry to obtain a synthesizer; use the default when exhausted.
    script = []
    # When True, ws.send(FINISH) synchronously invokes on_complete, simulating the SDK receiver thread
    # completing before send returns.
    complete_on_finish = False

    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.closed = False
        self.finish_payloads = []
        self.spoken = []
        self.ws = _FakeWebSocket(self)
        # Keep the installed SDK's request builder: a fake method name hid a
        # production incompatibility with the pinned DashScope version.
        self.request = _SdkRequest("test-key", "cosyvoice-v3-plus", "voice-x")
        _FakeSynthesizer.instances.append(self)
        self.callback.on_open()

    def streaming_call(self, text):
        self.spoken.append(text)
        if _FakeSynthesizer.script:
            _FakeSynthesizer.script.pop(0)(self)
            return
        # Supply the entire 1024-byte bootstrap threshold at once, forcing immediate audio delivery
        # and setting _active_sid as on the real path.
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


@pytest.mark.parametrize("round_end", [True, False])
def test_finish_sends_the_installed_sdk_protocol_without_ending_soft_flush(worker, round_end):
    request_queue, response_queue, _thread = worker
    request_queue.put(("speech-a", _LONG_ENOUGH))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "synthesizer")
    synth = _FakeSynthesizer.instances[0]

    marker = (None, None) if round_end else (TTS_SOFT_FLUSH_SENTINEL, "speech-a")
    request_queue.put(marker)
    _wait_for(lambda: synth.finish_payloads, "real SDK FINISH payload")
    assert len(synth.finish_payloads) == 1
    payload = json.loads(synth.finish_payloads[0])
    assert payload["header"] == {
        "action": "finish-task",
        "task_id": synth.request.task_id,
        "streaming": "duplex",
    }
    assert payload["payload"] == {"input": {}}
    assert _audio_done_ids(_drain(response_queue)) == []

    # Tail audio remains accepted after FINISH; only a real round completion
    # can report audio_done. A soft flush must leave the speech open.
    tail_data = b"\x01" * 2048
    synth.callback.on_data(tail_data)
    assert _audio_done_ids(_drain(response_queue)) == []
    synth.callback.on_complete()
    completed = _drain(response_queue)
    assert ("__audio__", "speech-a", tail_data) in completed
    assert _audio_done_ids(completed) == (["speech-a"] if round_end else [])


@pytest.mark.parametrize("close_fails", [False, True])
def test_interrupt_then_new_finish_keeps_old_callbacks_out(worker, close_fails):
    request_queue, response_queue, _thread = worker
    request_queue.put(("speech-a", _LONG_ENOUGH))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    first = _FakeSynthesizer.instances[0]
    if close_fails:
        def broken_close():
            first.closed = True
            raise ConnectionError("already closed")
        first.close = broken_close

    request_queue.put(("__interrupt__", None))
    request_queue.put(("speech-b", _LONG_ENOUGH))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "new turn after interrupt")
    second = _FakeSynthesizer.instances[1]
    assert first.closed
    _drain(response_queue)
    first.callback.on_data(b"old tail" * 512)
    first.callback.on_complete()
    assert _drain(response_queue) == []

    request_queue.put((None, None))
    _wait_for(lambda: second.finish_payloads, "new turn FINISH")
    second.callback.on_complete()
    assert _audio_done_ids(_drain(response_queue)) == ["speech-b"]


@pytest.mark.parametrize("disconnect", ["close", "idle_timeout", "request_timeout"])
def test_finish_releases_disconnected_stream_before_the_next_turn(worker, disconnect):
    request_queue, response_queue, _thread = worker
    request_queue.put(("speech-a", _LONG_ENOUGH))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    first = _FakeSynthesizer.instances[0]
    if disconnect == "close":
        first.callback.on_close()
    elif disconnect == "idle_timeout":
        first.callback.on_error("request timeout after 23 seconds")
    else:
        first.callback.on_error("request timeout")
    request_queue.put((None, None))
    _wait_for(lambda: first.closed, "disconnected stream retirement")
    assert first.finish_payloads == []
    assert _audio_done_ids(_drain(response_queue)) == []

    request_queue.put(("speech-b", _LONG_ENOUGH))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "replacement synthesizer")
    second = _FakeSynthesizer.instances[1]
    request_queue.put((None, None))
    _wait_for(lambda: second.finish_payloads, "replacement FINISH")
    second.callback.on_complete()
    assert _audio_done_ids(_drain(response_queue)) == ["speech-b"]


def test_superseded_synthesizer_completion_does_not_close_the_current_turn(worker):
    request_queue, response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一轮。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    request_queue.put((None, None))
    first = _FakeSynthesizer.instances[0]
    _wait_for(lambda: first.finish_payloads, "FINISH for the first turn")

    # Second round: sid change closes the old synthesizer and creates a new one.
    request_queue.put(("speech-b", _LONG_ENOUGH + "第二轮。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 2, "second synthesizer")
    second = _FakeSynthesizer.instances[1]
    request_queue.put((None, None))
    _wait_for(lambda: second.finish_payloads, "FINISH for the second turn")

    # A late first-generation synthesizer completion arrives while shared callback state describes round two.
    first.callback.on_complete()
    assert _audio_done_ids(_drain(response_queue)) == [], (
        "a superseded synthesizer's completion must not close the current turn"
    )

    # Only this round's own completion notification counts.
    second.callback.on_complete()
    assert _audio_done_ids(_drain(response_queue)) == ["speech-b"]


def test_reconnect_within_a_turn_does_not_close_the_stream(worker):
    """A mid-turn rebuild also completes the old synthesizer -- not a round end."""
    request_queue, response_queue, _thread = worker

    request_queue.put(("speech-a", _LONG_ENOUGH + "第一轮。"))
    _wait_for(lambda: len(_FakeSynthesizer.instances) == 1, "first synthesizer")
    first = _FakeSynthesizer.instances[0]

    # No FINISH sent yet this round: no completion notification may finalize it.
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

    # Fake clock: advance only time(); keep real sleep so worker polling cannot become a busy-wait.
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

        # Cross the idle threshold: worker proactively sends FINISH as keepalive.
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
        synth.callback.on_data(b"A" * 500)   # Below the 1024-byte bootstrap threshold: retain in the buffer.

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
