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
"""Live screen-share frames never become the text session's user attachments.

The bug: a voice session with a running manual screen share is rebuilt as a
text session. Frames the share keeps sending meanwhile are buffered and then
replayed into the offline client's ``_pending_images``. The next typed
message carries them as if the user had attached them, history keeps their
pixels for two image turns, and every later request resends them until the
session closes: the character keeps describing a screen that is long gone.

Frames the frontend marks ``live_share: true`` (manual share, voice vision
heartbeat) are dropped by a text session, both on arrival and on replay.
Screenshots the user attached (no marker, ``request_id`` set) keep working.
``screen_share_stopped`` forgets the cached live frames of a voice session
and fences frames that reached the server before it.
"""

import asyncio
import time
from dataclasses import replace

import pytest

from main_logic import core as core_module
from main_logic.core import streaming as streaming_mod
from main_logic.omni_realtime_client import OmniRealtimeClient
from tests.unit.test_core_game_route_memory_contract import (
    _make_callback_media_manager,
    _make_offline_session_for_callback_media,
)
from tests.unit.test_offline_provider_frame_publish import _png_b64
from tests.unit.test_offline_turn_cancellation_e2e import _client, _text
from utils.llm_client.messages import _normalize_messages

pytestmark = pytest.mark.unit


def _frame(*, live: bool, request_id: str | None = None, at: float | None = None) -> dict:
    message = {"input_type": "screen", "data": "data:image/jpeg;base64," + _png_b64(40, 40)}
    if live:
        message["live_share"] = True
    if request_id:
        message["request_id"] = request_id
    if at is not None:
        message["_visual_input_ingress_time"] = at
    return message


async def _decode_only(data):
    return data.split(",", 1)[1]


@pytest.fixture
def manager(monkeypatch):
    session = _make_offline_session_for_callback_media()
    mgr = _make_callback_media_manager(session)
    mgr._bg_tasks = set()
    mgr._asr_route_mode = "blocked"
    mgr._live_share_stopped_at = 0.0
    texts = []

    async def stream_text(text, **kwargs):
        texts.append(text)
        if callable(kwargs.get("on_turn_committed")):
            kwargs["on_turn_committed"]()

    session.stream_text = stream_text
    mgr.texts = texts
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda *a: None)
    monkeypatch.setattr(streaming_mod._core_facade, "process_screen_data", _decode_only, raising=False)
    return mgr


def _image_urls(request) -> list[str]:
    urls = []
    for message in _normalize_messages(request):
        content = message.get("content")
        if isinstance(content, list):
            urls += [part["image_url"]["url"] for part in content if part.get("type") == "image_url"]
    return urls


@pytest.mark.asyncio
async def test_live_frames_buffered_during_voice_to_text_rebuild_are_not_replayed(manager):
    """Repro of the field report: the handoff buffer must not feed the text session."""
    manager.session_ready = False
    manager._starting_session_count = 1
    await manager._stream_data_now(_frame(live=True))
    await manager._stream_data_now(_frame(live=True))
    await manager._stream_data_now({"input_type": "text", "data": "typed during voice", "request_id": "t1"})
    assert [m["input_type"] for m in manager.pending_input_data] == ["screen", "screen", "text"]

    manager._starting_session_count = 0
    manager.session_ready = True
    await manager._flush_pending_input_data()

    assert manager.session.stream_image.await_count == 0
    assert manager.texts == ["typed during voice"]
    assert manager.pending_input_data == []


@pytest.mark.asyncio
async def test_user_screenshot_buffered_during_rebuild_still_reaches_text_session(manager):
    manager.session_ready = False
    manager._starting_session_count = 1
    await manager._stream_data_now(_frame(live=False, request_id="r1"))
    await manager._stream_data_now({"input_type": "text", "data": "look", "request_id": "r1"})
    manager._starting_session_count = 0
    manager.session_ready = True
    await manager._flush_pending_input_data()

    assert manager.session.stream_image.await_count == 1
    assert manager.texts == ["look"]


@pytest.mark.asyncio
async def test_text_session_drops_live_frames_that_arrive_after_the_switch(manager):
    await manager._process_stream_data_internal(_frame(live=True))
    assert manager.session.stream_image.await_count == 0

    await manager._process_stream_data_internal(_frame(live=False, request_id="r2"))
    assert manager.session.stream_image.await_count == 1


@pytest.mark.asyncio
async def test_independent_asr_replay_keeps_live_frames(manager, monkeypatch):
    """Independent ASR is voice mode over an offline client: its live frames stay."""
    manager._asr_route_mode = "independent"
    seen = []

    async def record(message, **_kwargs):
        seen.append(message)

    monkeypatch.setattr(manager, "_process_stream_input", record, raising=False)
    manager.pending_input_data = [_frame(live=True)]
    await manager._flush_pending_input_data()
    assert len(seen) == 1 and seen[0]["live_share"] is True


@pytest.mark.asyncio
async def test_live_frame_never_rides_later_text_turns(manager):
    """End to end with the real offline client: no image in any later request."""
    client = _client()
    manager.session = client
    await manager._process_stream_data_internal(_frame(live=True))
    assert client._pending_images == []

    for i in range(3):
        client.script = [[_text(f"reply {i}"), _text("", "stop")]]
        await client.stream_text(f"text-only message {i}")
        assert _image_urls(client.requests[-1]) == []

    # Control: a user attachment through the same path is still sent.
    await manager._process_stream_data_internal(_frame(live=False, request_id="r3"))
    client.script = [[_text("seen"), _text("", "stop")]]
    await client.stream_text("what is in my screenshot?")
    assert len(_image_urls(client.requests[-1])) == 1


@pytest.mark.asyncio
async def test_stop_notice_fences_a_live_frame_still_decoding(manager, monkeypatch):
    """The stop lands while an earlier frame is being validated: that frame is dropped."""
    staged = []
    manager._asr_route_mode = "independent"
    monkeypatch.setattr(
        manager,
        "_stage_independent_visual_frame",
        lambda image_b64, **kw: staged.append(kw) or True,
        raising=False,
    )
    decoding = asyncio.Event()
    resume = asyncio.Event()

    async def paused_decode(data):
        decoding.set()
        await resume.wait()
        return data.split(",", 1)[1]

    monkeypatch.setattr(streaming_mod._core_facade, "process_screen_data", paused_decode, raising=False)
    old = asyncio.create_task(
        manager._process_stream_data_internal(_frame(live=True, at=time.monotonic()))
    )
    await asyncio.wait_for(decoding.wait(), timeout=5)
    manager.handle_screen_share_stopped()
    resume.set()
    await asyncio.wait_for(old, timeout=5)
    assert staged == []

    # A later frame (a share restarted, or the voice vision heartbeat) is fresh.
    # Explicitly later: on Windows time.monotonic() ticks every ~15.6 ms, so a
    # reading taken right after the stop can equal it, and a frame stamped in
    # the stop's own tick cannot be ordered after it (it is fenced, as it should).
    await manager._process_stream_data_internal(
        _frame(live=True, at=manager._live_share_stopped_at + 1.0)
    )
    assert [kw["request_id"] for kw in staged] == [None]
    # User attachments are never fenced.
    await manager._process_stream_data_internal(
        _frame(live=False, request_id="r4", at=manager._live_share_stopped_at - 1)
    )
    assert [kw["request_id"] for kw in staged] == [None, "r4"]


def test_stop_notice_keeps_user_screenshots_staged_for_independent_asr(manager):
    """Only live frames (no request_id) leave Core's independent-ASR caches."""
    manager._ensure_asr_runtime_state()
    manager._asr_route_mode = "independent"
    now = time.monotonic()
    assert manager._stage_independent_visual_frame("live", source="screen", request_id=None, captured_at=now)
    assert manager._stage_independent_visual_frame("shot", source="screen", request_id="u1", captured_at=now + 0.01)
    assert manager._latest_independent_visual_frame.request_id == "u1"
    manager._prerecord_visual_frames = [
        replace(manager._latest_independent_visual_frame, request_id=None, image_b64="live"),
        manager._latest_independent_visual_frame,
    ]

    manager.handle_screen_share_stopped()

    assert manager._latest_independent_visual_frame.image_b64 == "shot"
    assert [f.request_id for f in manager._prerecord_visual_frames] == ["u1"]

    manager._latest_independent_visual_frame = replace(
        manager._latest_independent_visual_frame, request_id=None
    )
    manager.handle_screen_share_stopped()
    assert manager._latest_independent_visual_frame is None


def _realtime_with_cached_frame(request_id):
    client = OmniRealtimeClient.__new__(OmniRealtimeClient)
    client._latest_image_b64 = "cached"
    client._latest_image_generation = 3
    client._latest_image_captured_at = 1.0
    client._latest_image_source = "screen"
    client._latest_image_request_id = request_id
    client._proactive_image_consumed = False
    client._supports_native_image = True
    return client


def test_stop_notice_clears_the_voice_session_cached_live_frame(manager):
    client = _realtime_with_cached_frame(None)
    manager.session = client
    manager.handle_screen_share_stopped()

    assert client._latest_image_b64 is None
    assert client._proactive_image_consumed is True
    assert client._latest_image_generation == 4
    # No captured-at floor: a user screenshot validated after the stop still stages.
    late_shot = client.stage_multimodal_frame("shot", source="screen", request_id="u", captured_at=9.0)
    assert late_shot.accepted is True


@pytest.mark.asyncio
async def test_stop_notice_drops_a_non_native_frame_description(manager, monkeypatch):
    """StepFun-style providers describe the frame; the description must not outlive the share."""
    client = _realtime_with_cached_frame(None)
    client._supports_native_image = False
    client._image_description = "[实时屏幕截图或相机画面]: a game menu"
    client._image_recognized_this_turn = True
    client._image_being_analyzed = False
    manager.session = client
    manager.handle_screen_share_stopped()
    assert client._image_recognized_this_turn is False
    assert "a game menu" not in client._image_description

    # An analysis still running when the share stopped cannot write back.
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_analysis(**_kwargs):
        started.set()
        await release.wait()
        return "the old game screen"

    import utils.screenshot_utils as screenshot_utils

    monkeypatch.setattr(screenshot_utils, "analyze_image_with_vision_model", slow_analysis)
    client._latest_image_b64 = "frame"
    task = asyncio.create_task(client._analyze_image_with_vision_model("frame"))
    await asyncio.wait_for(started.wait(), timeout=5)
    manager.handle_screen_share_stopped()
    release.set()
    # Same fence as a consent revocation: the result of a discarded frame
    # is dropped.
    assert await task == ""
    assert client._image_recognized_this_turn is False
    assert "the old game screen" not in client._image_description


def test_stop_notice_keeps_a_screenshot_the_user_attached_in_voice(manager):
    client = _realtime_with_cached_frame("user-req")
    manager.session = client
    manager.handle_screen_share_stopped()
    assert client._latest_image_b64 == "cached"
    assert client._proactive_image_consumed is False


@pytest.mark.asyncio
async def test_text_replay_drops_live_frames_before_processing(manager, monkeypatch):
    """The replay layer itself drops them, like it drops plain text for voice."""
    seen = []

    async def record(message, **_kwargs):
        seen.append(message)

    monkeypatch.setattr(manager, "_process_stream_input", record, raising=False)
    manager.pending_input_data = [
        _frame(live=True),
        _frame(live=False, request_id="r5"),
        {"input_type": "text", "data": "hi", "request_id": "r5"},
    ]
    await manager._flush_pending_input_data()
    assert [(m["input_type"], m.get("live_share")) for m in seen] == [("screen", None), ("text", None)]


@pytest.mark.asyncio
async def test_hang_up_then_typing_never_attaches_share_frames(manager, monkeypatch):
    """Manual hang-up (end_session / pause_session), then N typed turns.

    The frontend stops the share before it ends the session, so this path
    has no rebuild window that buffers share frames. Pinned step by step:
    a frame still validating when the voice session ends is dropped; a frame
    arriving with no session does not start one; the text session's start
    window replays only what was typed; later requests carry no image.
    """
    voice = object()
    manager.session = voice

    async def end_during_validation(data):
        # The hang-up lands while this frame is being decoded.
        manager.session = None
        manager.is_active = False
        manager.session_ready = False
        return data.split(",", 1)[1]

    monkeypatch.setattr(streaming_mod._core_facade, "process_screen_data", end_during_validation, raising=False)
    await manager._process_stream_data_internal(_frame(live=True))
    monkeypatch.setattr(streaming_mod._core_facade, "process_screen_data", _decode_only, raising=False)

    async def no_auto_start(*_a, **_k):
        raise AssertionError("a share frame must not start a session")

    monkeypatch.setattr(manager, "start_session", no_auto_start, raising=False)
    await manager._stream_data_now(_frame(live=True))
    assert manager.pending_input_data == []

    # The user types: the text session is starting.
    offline = _client()
    manager._starting_session_count = 1
    await manager._stream_data_now({"input_type": "text", "data": "hello", "request_id": "t9"})
    manager.session = offline
    manager.is_active = True
    manager._starting_session_count = 0
    manager.session_ready = True
    assert [m["input_type"] for m in manager.pending_input_data] == ["text"]
    assert offline._pending_images == []

    # The real replay entry sends what was typed, and nothing else.
    offline.script = [[_text("hi"), _text("", "stop")]]
    replayed = []

    async def recording_stream_text(text, **kwargs):
        replayed.append(text)
        return await type(offline).stream_text(offline, text, **kwargs)

    monkeypatch.setattr(offline, "stream_text", recording_stream_text)
    await manager._flush_pending_input_data()
    assert replayed == ["hello"]
    assert len(offline.requests) == 1
    assert _image_urls(offline.requests[-1]) == []
    first_request_users = [
        m for m in _normalize_messages(offline.requests[0]) if m.get("role") == "user"
    ]
    last_user = first_request_users[-1]["content"]
    last_user_text = last_user if isinstance(last_user, str) else " ".join(
        p.get("text", "") for p in last_user if isinstance(p, dict)
    )
    assert "hello" in last_user_text

    for i in range(4):
        offline.script = [[_text(f"reply {i}"), _text("", "stop")]]
        await offline.stream_text(f"typed {i}")
        assert _image_urls(offline.requests[-1]) == []


def test_superseded_recorder_stop_notice_does_not_close_its_socket():
    """stopRecording() sends screen_share_stopped before pause_session.

    A recorder whose window was superseded but still owns voice must not be
    closed by the notice (its pause would be lost); other old sockets'
    notices are dropped without clearing anything.
    """
    from pathlib import Path

    router_source = (
        Path(__file__).resolve().parents[2] / "main_routers" / "websocket_router.py"
    ).read_text(encoding="utf-8")
    superseded_gate = router_source.split(
        "if (session_id.get(lanlan_name) != this_session_id", 1
    )[1].split('action = message.get("action")', 1)[0]
    branch = superseded_gate.split('if message.get("action") == "screen_share_stopped":', 1)[1]
    branch = branch.split("continue", 1)[0]
    assert "if _owns_voice_connection():" in branch
    assert "handle_screen_share_stopped()" in branch
    assert superseded_gate.index('"screen_share_stopped"') < superseded_gate.index(
        "await websocket.close()"
    )
    assert 'if action == "screen_share_stopped":' in router_source


def test_router_stamps_screen_consent_before_dispatching_the_frame():
    """The consent epoch is read at arrival, before the validation task starts."""
    from pathlib import Path

    router_source = (
        Path(__file__).resolve().parents[2] / "main_routers" / "websocket_router.py"
    ).read_text(encoding="utf-8")
    stamp = router_source.index('"_visual_input_consent_epoch": getattr(')
    dispatch = router_source.index("_fire_task(stream_mgr.stream_data(message))")
    ingress = router_source.index("message = _stamp_user_input_ingress(message)")
    # Stamped with the ingress time, before the frame's validation task.
    assert ingress < stamp < dispatch
    assert stamp - ingress < 1000
