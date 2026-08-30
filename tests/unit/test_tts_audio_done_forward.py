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
"""The ``audio_done`` end-of-stream signal must land AFTER the audio.

Issue #1566: the frontend used to infer "this turn's audio finished" from
"all four audio queues look empty right now", which finalizes early in the
gap between chunks (mouth stops and restarts, trailing audio orphaned,
isPlaying stuck until the 30s watchdog). The backend half of the fix is an
authoritative ``{"type": "audio_done", "speech_id": ...}`` frame.

Its whole value depends on ORDERING: it must be awaited out after the last
``audio_chunk`` of the same speech_id. Scheduled fire-and-forget it can jump
ahead of trailing audio and the frontend finalizes early -- which is the
defect itself, now shipped from the other side. A dropped signal is only a
degradation (the frontend keeps a give-up timer plus a watchdog), so these
tests pin ordering hard and liveness softly.
"""

from __future__ import annotations

import ast
import asyncio
import queue
from pathlib import Path

import pytest

from main_logic.core import LLMSessionManager
from main_logic.core import tts_runtime as tts_runtime_module
from main_logic.core.game_speech_audio_cache import GAME_SPEECH_AUDIO_CACHE

MAIN_LOGIC_DIR = Path(__file__).resolve().parents[2] / "main_logic"


class _ClientState:
    """Stand-in for FastAPI's ``WebSocketState`` enum.

    Production reads ``ws.client_state == ws.client_state.CONNECTED``, off the
    INSTANCE, so ``CONNECTED`` has to be reachable from the state value itself.
    """

    def __init__(self, name: str):
        self._name = name

    @property
    def CONNECTED(self):
        return _CONNECTED_STATE

    def __eq__(self, other):
        return isinstance(other, _ClientState) and other._name == self._name

    def __hash__(self):
        return hash(self._name)


_CONNECTED_STATE = _ClientState("CONNECTED")
_DISCONNECTED_STATE = _ClientState("DISCONNECTED")


class _RecordingWebsocket:
    """Records send_json / send_bytes in the order the sends COMPLETED.

    ``delays`` makes one payload type slow to flush, which is what turns an
    in-flight send into an observable window: a caller that schedules its send
    instead of awaiting it lets whatever comes next finish first.
    """

    def __init__(self, connected: bool = True, delays: dict[str, float] | None = None):
        self.client_state = _CONNECTED_STATE if connected else _DISCONNECTED_STATE
        self.calls: list[tuple[str, object]] = []
        self.audio_done_seen = asyncio.Event()
        self.delays = delays or {}

    async def send_json(self, payload):
        delay = self.delays.get(payload.get("type")) if isinstance(payload, dict) else None
        if delay:
            await asyncio.sleep(delay)
        self.calls.append(("json", payload))
        if isinstance(payload, dict) and payload.get("type") == "audio_done":
            self.audio_done_seen.set()

    async def send_bytes(self, payload):
        self.calls.append(("bytes", payload))


def _make_mgr(websocket) -> LLMSessionManager:
    """Minimal manager exposing only what send_speech / the handler read."""
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.websocket = websocket
    mgr.tts_response_queue = queue.Queue()
    mgr.current_speech_id = "sid-current"
    mgr.sync_message_queue = queue.Queue()
    mgr._speech_output_total = 0
    mgr._last_speech_output_time = 0.0
    mgr._last_speech_output_bytes = 0
    mgr._tts_replay_audio_emitted = False
    mgr._tts_replay_sentence_audio_emitted = False
    mgr._confirm_pending_ai_voice_echo = lambda *_args: None
    mgr._discard_pending_ai_voice_echo = lambda *_args: None
    return mgr


async def _start_handler(mgr, *messages):
    """Queue the messages and start the handler task."""
    for message in messages:
        mgr.tts_response_queue.put(message)
    return asyncio.create_task(LLMSessionManager.tts_response_handler(mgr))


async def _run_until_audio_done(mgr, *messages, timeout=2.0):
    task = await _start_handler(mgr, *messages)
    try:
        await asyncio.wait_for(mgr.websocket.audio_done_seen.wait(), timeout=timeout)
    except BaseException:
        await _stop(task)
        raise
    return task


async def _stop(task):
    """Cancel the handler; its CancelledError arm wakes the blocked
    ``q.get`` thread, so nothing leaks into the next test."""
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _wait_for(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


async def test_audio_done_sentinel_reaches_the_frontend():
    ws = _RecordingWebsocket()
    mgr = _make_mgr(ws)

    task = await _run_until_audio_done(mgr, ("__audio_done__", "sid-1"))
    await _stop(task)

    assert ws.calls == [("json", {"type": "audio_done", "speech_id": "sid-1"})]


async def test_per_speech_gain_is_forwarded_and_released_when_stream_closes():
    ws = _RecordingWebsocket()
    mgr = _make_mgr(ws)
    mgr.remember_speech_playback_gain("sid-boost", 2.0)

    assert await mgr.send_speech(b"pcm", "sid-boost") is True
    assert mgr.speech_playback_gain("sid-boost") == 2.0
    assert await mgr.send_audio_done("sid-boost") is True

    assert ws.calls == [
        ("json", {"type": "audio_chunk", "speech_id": "sid-boost", "playback_gain": 2.0}),
        ("bytes", b"pcm"),
        ("json", {"type": "audio_done", "speech_id": "sid-boost"}),
    ]
    assert mgr.speech_playback_gain("sid-boost") == 1.0


async def test_game_speech_correlation_is_forwarded_and_released_with_stream():
    ws = _RecordingWebsocket()
    mgr = _make_mgr(ws)
    mgr._remember_game_speech_correlation("sid-game", "sdk-speech-correlation-1")

    assert await mgr.send_speech(b"pcm", "sid-game") is True
    assert await mgr.send_audio_done("sid-game") is True

    assert ws.calls == [
        ("json", {
            "type": "audio_chunk",
            "speech_id": "sid-game",
            "sdk_speech_correlation_id": "sdk-speech-correlation-1",
        }),
        ("bytes", b"pcm"),
        ("json", {
            "type": "audio_done",
            "speech_id": "sid-game",
            "sdk_speech_correlation_id": "sdk-speech-correlation-1",
        }),
    ]
    assert mgr._game_speech_correlation is None


def test_per_speech_gain_registry_has_a_hard_capacity_and_clear_path():
    mgr = _make_mgr(_RecordingWebsocket())
    max_items = 32

    for index in range(max_items + 5):
        mgr.remember_speech_playback_gain(f"sid-{index}", 2.0)

    assert len(mgr._speech_playback_gains) == max_items
    assert mgr.speech_playback_gain("sid-0") == 1.0
    mgr.clear_speech_playback_gains()
    assert mgr._speech_playback_gains == {}


async def test_audio_done_is_emitted_after_the_last_audio_chunk():
    """The ordering guard. If audio_done is not awaited in the handler's
    sequential consumption, it overtakes the audio and this flips."""
    ws = _RecordingWebsocket()
    mgr = _make_mgr(ws)

    task = await _run_until_audio_done(
        mgr,
        ("__audio__", "sid-1", b"pcm-first"),
        ("__audio__", "sid-1", b"pcm-last"),
        ("__audio_done__", "sid-1"),
    )
    # 收尾信号已送达；此时该 sid 的音频必须已经全部在它前面。
    await _stop(task)

    assert ws.calls == [
        ("json", {"type": "audio_chunk", "speech_id": "sid-1"}),
        ("bytes", b"pcm-first"),
        ("json", {"type": "audio_chunk", "speech_id": "sid-1"}),
        ("bytes", b"pcm-last"),
        ("json", {"type": "audio_done", "speech_id": "sid-1"}),
    ]
    # 位置断言之外再钉一次相对次序，避免有人放宽上面的全等断言后守卫失效。
    assert ws.calls.index(("json", {"type": "audio_done", "speech_id": "sid-1"})) > (
        max(i for i, call in enumerate(ws.calls) if call[0] == "bytes")
    )


async def test_handler_commits_opted_in_audio_only_after_matching_stream_done():
    GAME_SPEECH_AUDIO_CACHE.clear()
    ws = _RecordingWebsocket()
    mgr = _make_mgr(ws)
    mgr.current_game_speech_audio_runtime_signature = lambda: "voice-signature"
    assert GAME_SPEECH_AUDIO_CACHE.begin_capture(
        mgr, "sid-1", "opaque-key", "voice-signature"
    )
    try:
        task = await _run_until_audio_done(
            mgr,
            ("__audio__", "sid-1", b"pcm-first"),
            ("__audio__", "sid-1", b"pcm-last"),
            ("__audio_done__", "sid-1"),
        )
        await _stop(task)

        assert GAME_SPEECH_AUDIO_CACHE.get("opaque-key") == (
            b"pcm-first",
            b"pcm-last",
        )
    finally:
        GAME_SPEECH_AUDIO_CACHE.clear()


async def test_handler_discards_capture_when_effective_voice_changes_mid_synthesis():
    GAME_SPEECH_AUDIO_CACHE.clear()
    ws = _RecordingWebsocket()
    mgr = _make_mgr(ws)
    mgr.current_game_speech_audio_runtime_signature = lambda: "fallback-voice"
    assert GAME_SPEECH_AUDIO_CACHE.begin_capture(
        mgr, "sid-1", "opaque-key", "configured-voice"
    )
    try:
        task = await _run_until_audio_done(
            mgr,
            ("__audio__", "sid-1", b"fallback-pcm"),
            ("__audio_done__", "sid-1"),
        )
        await _stop(task)

        assert GAME_SPEECH_AUDIO_CACHE.get("opaque-key") is None
        assert GAME_SPEECH_AUDIO_CACHE.stats()["captures"] == 0
    finally:
        GAME_SPEECH_AUDIO_CACHE.clear()


async def test_handler_cancellation_releases_in_flight_game_speech_capture():
    GAME_SPEECH_AUDIO_CACHE.clear()
    mgr = _make_mgr(_RecordingWebsocket())
    assert GAME_SPEECH_AUDIO_CACHE.begin_capture(
        mgr, "sid-1", "opaque-key", "voice-signature"
    )
    task = await _start_handler(mgr)
    try:
        await asyncio.sleep(0)
        await _stop(task)
        assert GAME_SPEECH_AUDIO_CACHE.stats()["captures"] == 0
    finally:
        if not task.done():
            await _stop(task)
        GAME_SPEECH_AUDIO_CACHE.clear()


async def test_handler_awaits_the_send_instead_of_scheduling_it():
    """The fire-and-forget guard.

    Consuming the queue in order is not enough on its own: the handler has to
    stay parked until the frame is actually out. Here the audio_done flush is
    slow and another message is already waiting behind it -- if the send were
    scheduled (``_fire_task`` / ``create_task``) the handler would run ahead
    and that message would reach the socket first. That is the same window a
    scheduled send opens against a speech's own trailing audio.
    """
    ws = _RecordingWebsocket(delays={"audio_done": 0.15})
    mgr = _make_mgr(ws)

    task = await _start_handler(
        mgr,
        ("__audio_done__", "sid-1"),
        ("__audio__", "sid-2", b"pcm-next"),
    )
    assert await _wait_for(lambda: len(ws.calls) >= 3), f"only got {ws.calls}"
    await _stop(task)

    assert ws.calls == [
        ("json", {"type": "audio_done", "speech_id": "sid-1"}),
        ("json", {"type": "audio_chunk", "speech_id": "sid-2"}),
        ("bytes", b"pcm-next"),
    ]


async def test_bare_bytes_audio_still_precedes_audio_done():
    """Workers that emit raw bytes (no ``__audio__`` envelope) take the
    handler's fallthrough branch; the sentinel must stay behind those too."""
    ws = _RecordingWebsocket()
    mgr = _make_mgr(ws)

    task = await _run_until_audio_done(mgr, b"bare-pcm", ("__audio_done__", "sid-1"))
    await _stop(task)

    assert [kind for kind, _ in ws.calls] == ["json", "bytes", "json"]
    assert ws.calls[-1] == ("json", {"type": "audio_done", "speech_id": "sid-1"})


async def test_disconnected_websocket_does_not_kill_the_handler(monkeypatch):
    warnings: list[str] = []
    skipped = asyncio.Event()

    def record_warning(message, *args, **kwargs):
        text = str(message)
        warnings.append(text)
        if "send_audio_done skipped" in text:
            skipped.set()

    monkeypatch.setattr(tts_runtime_module.logger, "warning", record_warning)

    ws = _RecordingWebsocket(connected=False)
    mgr = _make_mgr(ws)

    task = await _start_handler(mgr, ("__audio_done__", "sid-1"))
    try:
        await asyncio.wait_for(skipped.wait(), timeout=2.0)
        still_running = not task.done()
    finally:
        await _stop(task)

    assert ws.calls == []
    assert still_running, "一次发送失败不能让 tts_response_handler 退出"


async def test_game_completion_is_false_when_audio_chunk_delivery_failed(monkeypatch):
    monkeypatch.setattr(tts_runtime_module.logger, "warning", lambda *_a, **_k: None)

    class _ChunkFailureWebsocket(_RecordingWebsocket):
        async def send_bytes(self, payload):
            del payload
            raise RuntimeError("audio socket failed")

    ws = _ChunkFailureWebsocket()
    mgr = _make_mgr(ws)
    completion = LLMSessionManager._begin_game_speech_completion_wait(mgr, "sid-1")
    task = await _start_handler(
        mgr,
        ("__audio__", "sid-1", b"pcm"),
        ("__audio_done__", "sid-1"),
    )
    try:
        assert await asyncio.wait_for(completion, timeout=2.0) is False
        assert getattr(mgr, "_game_speech_delivery_state", None) is None
        assert not task.done()
    finally:
        await _stop(task)


async def test_game_completion_is_false_when_audio_done_delivery_failed(monkeypatch):
    monkeypatch.setattr(tts_runtime_module.logger, "warning", lambda *_a, **_k: None)
    mgr = _make_mgr(_RecordingWebsocket(connected=False))
    completion = LLMSessionManager._begin_game_speech_completion_wait(mgr, "sid-1")
    task = await _start_handler(mgr, ("__audio_done__", "sid-1"))
    try:
        assert await asyncio.wait_for(completion, timeout=2.0) is False
        assert getattr(mgr, "_game_speech_delivery_state", None) is None
        assert not task.done()
    finally:
        await _stop(task)


@pytest.mark.parametrize("has_socket", [False, True])
async def test_send_audio_done_never_raises_when_socket_is_unusable(has_socket, monkeypatch):
    monkeypatch.setattr(tts_runtime_module.logger, "warning", lambda *_a, **_k: None)
    mgr = _make_mgr(_RecordingWebsocket(connected=False) if has_socket else None)

    assert await LLMSessionManager.send_audio_done(mgr, "sid-1") is False


async def test_send_audio_done_raising_socket_is_swallowed(monkeypatch):
    monkeypatch.setattr(tts_runtime_module.logger, "warning", lambda *_a, **_k: None)
    ws = _RecordingWebsocket()

    async def boom(_payload):
        raise RuntimeError("socket already closed")

    ws.send_json = boom
    mgr = _make_mgr(ws)

    assert await LLMSessionManager.send_audio_done(mgr, "sid-1") is False


@pytest.mark.parametrize("speech_id", [None, ""])
async def test_ownerless_signal_is_never_sent(speech_id):
    """An audio_done without a speech_id would finalize whatever turn the
    frontend happens to be on. Missing beats wrong."""
    ws = _RecordingWebsocket()
    mgr = _make_mgr(ws)

    assert await LLMSessionManager.send_audio_done(mgr, speech_id) is False
    assert ws.calls == []


async def test_audio_done_is_not_mirrored_to_the_monitor_queue():
    """No monitor/viewer surface consumes audio, so the signal stays on the
    app socket (send_speech mirrors bytes; this must not copy that)."""
    ws = _RecordingWebsocket()
    mgr = _make_mgr(ws)

    assert await LLMSessionManager.send_audio_done(mgr, "sid-1") is True
    assert mgr.sync_message_queue.empty()


def _fire_and_forget_calls(tree: ast.AST) -> list[ast.Call]:
    """Every ``_fire_task(...)`` / ``create_task(...)`` / ``ensure_future(...)``."""
    wrappers = {"_fire_task", "create_task", "ensure_future"}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in wrappers:
            found.append(node)
    return found


def test_no_call_site_schedules_audio_done_fire_and_forget():
    """A scheduled audio_done races the audio it is supposed to follow.

    Discovered by scanning, not listed, so a NEW emitter (omni path included)
    is covered the day it lands.
    """
    # 整条链上每一处「必须排在音频之后」的调用点：core 的下行发送、
    # omni transport 触发的回调、worker 侧的哨兵投递。任一处改成
    # fire-and-forget 都会让收尾信号插到尾音前面。
    ordered_calls = {"send_audio_done", "on_audio_done", "emit_audio_done"}
    offenders = []
    for path in sorted(MAIN_LOGIC_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _fire_and_forget_calls(tree):
            mentions = any(
                (isinstance(sub, ast.Attribute) and sub.attr in ordered_calls)
                or (isinstance(sub, ast.Name) and sub.id in ordered_calls)
                for arg in call.args
                for sub in ast.walk(arg)
            )
            if mentions:
                offenders.append(f"{path.name}:{call.lineno}")

    assert not offenders, (
        "audio_done tells the frontend the stream is closed; scheduling it "
        "instead of awaiting it lets it overtake trailing audio and finalize "
        "the turn early -- the exact defect of issue #1566. Await it at the "
        "point where the last chunk has already been sent. Offenders:\n  "
        + "\n  ".join(offenders)
    )
