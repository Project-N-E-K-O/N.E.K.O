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
"""Offline history records where each image came from, and acts on it.

* Live screen/camera frames of a turn (independent ASR samples them over the
  utterance) keep their pixels for that turn only; from the next request on
  the history holds a receipt.
* When screen consent is revoked (privacy mode turned on), automatically
  captured images (live frames, the proactive screenshot) leave history;
  images the user attached stay.

Real ``OmniOfflineClient.stream_text`` turns; only the provider is faked.
"""

from types import SimpleNamespace

import pytest

from main_logic.core import LLMSessionManager
from tests.unit.test_offline_provider_frame_publish import _png_b64
from tests.unit.test_offline_turn_cancellation_e2e import _client, _text
from utils.image_history import image_count, without_image_pixels
from utils.llm_client.messages import _normalize_messages

pytestmark = pytest.mark.unit


def _urls(request) -> list[str]:
    urls = []
    for message in _normalize_messages(request):
        content = message.get("content")
        if isinstance(content, list):
            urls += [p["image_url"]["url"] for p in content if p.get("type") == "image_url"]
    return urls


def _b64(url: str) -> str:
    return url.split(",", 1)[1]


def _dominant_channel(url: str) -> str:
    """Which of red/blue dominates the decoded image (the test images are flat colours)."""
    import base64
    import io

    from PIL import Image

    r, _g, b = Image.open(io.BytesIO(base64.b64decode(_b64(url)))).convert("RGB").getpixel((5, 5))
    return "red" if r > b else "blue"


async def _turn(client, text, **kwargs):
    """Run one turn; return the image URLs its request carried, read right away."""
    client.script = [[_text("ok"), _text("", "stop")]]
    await client.stream_text(text, **kwargs)
    # The recorded request shares message objects with history, which later
    # turns edit in place: snapshot the URLs now.
    return _urls(client.requests[-1])


def test_without_image_pixels_can_keep_chosen_images():
    content = [
        {"type": "image_url", "image_url": {"url": "data:a"}},
        {"type": "image_url", "image_url": {"url": "data:b"}},
        {"type": "text", "text": "hi"},
    ]
    kept = without_image_pixels(content, keep=[1])
    assert image_count(kept) == 1
    assert kept[0]["image_url"]["url"] == "data:b"
    assert "1 image(s)" in kept[-1]["text"]
    assert without_image_pixels(content, keep=[0, 1]) is content
    assert image_count(without_image_pixels(content)) == 0


@pytest.mark.asyncio
async def test_live_frames_keep_pixels_only_for_their_own_turn():
    client = _client()
    live = _png_b64(40, 40, (200, 10, 10))
    photo = _png_b64(41, 41, (10, 10, 200))
    # As submit_multimodal_turn hands it over: sampled frames first, then the
    # user's attachments, with turn_source_count covering the frames only.
    first = await _turn(
        client, "what am I looking at?",
        turn_images=(live, photo), turn_source="screen", turn_source_count=1,
    )
    assert sorted(_dominant_channel(u) for u in first) == ["blue", "red"]
    message = next(m for m in client._conversation_history if image_count(m.content))
    assert message.metadata["image_kinds"] == ["live", "user"]

    second = await _turn(client, "and now?")
    # The live frame (red) left; the photo the user attached (blue) is still there,
    # identified by content rather than position.
    assert [_dominant_channel(u) for u in second] == ["blue"]
    first_turn = next(
        m for m in client._conversation_history
        if isinstance(m.content, list)
        and any(p.get("text") == "what am I looking at?" for p in m.content if isinstance(p, dict))
    )
    assert any("1 image(s)" in p.get("text", "") for p in first_turn.content if isinstance(p, dict))
    assert first_turn.metadata["image_kinds"] == ["user"]

    third = await _turn(client, "still?")
    assert len(third) == 1


@pytest.mark.asyncio
async def test_proactive_turn_also_drops_answered_live_frames():
    client = _client()
    await _turn(
        client, "look", turn_images=(_png_b64(40, 40),), turn_source="screen", turn_source_count=1,
    )
    client.script = [[_text("proactive line"), _text("", "stop")]]
    assert await client.prompt_ephemeral("callback") is True
    assert _urls(client.requests[-1]) == []


@pytest.mark.asyncio
async def test_revocation_strips_automatic_captures_and_keeps_user_images():
    client = _client()
    client.set_proactive_screenshot(_png_b64(40, 40, (1, 2, 3)))
    photo = _png_b64(41, 41, (4, 5, 6))
    client._pending_images = [photo]
    first = await _turn(client, "reply to proactive talk with my photo")
    assert len(first) == 2
    message = next(m for m in client._conversation_history if image_count(m.content))
    assert message.metadata["image_kinds"] == ["auto_screenshot", "user"]

    # The next-turn live rule leaves the proactive screenshot alone.
    second = await _turn(client, "hmm")
    assert len(second) == 2

    client.discard_auto_captured_history_images()
    third = await _turn(client, "privacy mode is on now")
    urls = third
    assert len(urls) == 1
    assert _b64(urls[0]) == _b64(first[1])


def test_revocation_fails_closed_when_the_image_mapping_was_lost():
    client = _client()
    from utils.llm_client import HumanMessage

    message = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
        {"type": "text", "text": "x"},
    ])
    message.metadata["image_kinds"] = ["unknown"]
    message.metadata["image_prefit_kinds"] = ["auto_screenshot", "user"]
    client._conversation_history.append(message)
    client._strip_answered_live_frames()  # no live frame in it: not the live rule's business
    assert image_count(client._conversation_history[-1].content) == 1
    client.discard_auto_captured_history_images()
    assert image_count(client._conversation_history[-1].content) == 0


def test_consent_revocation_reaches_the_session_only_on_allowed_to_revoked():
    calls = []
    manager = SimpleNamespace(
        _proactive_vision_enabled=True,
        session=SimpleNamespace(discard_auto_captured_history_images=lambda: calls.append(1)),
        lanlan_name="T",
    )
    LLMSessionManager.update_proactive_vision_consent(manager, False)
    assert calls == [1]
    # Re-sent on every settings save / reconnect: no second strip.
    LLMSessionManager.update_proactive_vision_consent(manager, False)
    assert calls == [1]
    LLMSessionManager.update_proactive_vision_consent(manager, True)
    assert calls == [1]


@pytest.mark.asyncio
async def test_stripping_keeps_the_message_identity_for_in_flight_write_back():
    """A turn still replying finds its message by identity (cancel commit, history replacement)."""
    client = _client()
    await _turn(
        client, "look", turn_images=(_png_b64(40, 40),), turn_source="screen", turn_source_count=1,
    )
    message = next(m for m in client._conversation_history if image_count(m.content))
    client._strip_answered_live_frames()
    assert any(m is message for m in client._conversation_history)
    assert image_count(message.content) == 0
    assert message.metadata["image_kinds"] == []


def test_budget_trimmed_turn_with_live_frames_is_stripped_whole_next_turn():
    from utils.llm_client import HumanMessage

    client = _client()
    message = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBB"}},
        {"type": "text", "text": "x"},
    ])
    message.metadata["image_kinds"] = ["unknown", "unknown"]
    message.metadata["image_prefit_kinds"] = ["live", "user"]
    client._conversation_history.append(message)
    client._strip_answered_live_frames()
    assert image_count(message.content) == 0


@pytest.mark.asyncio
async def test_user_screenshot_sampled_with_live_frames_is_kept_as_an_attachment():
    """Independent ASR samples a screenshot the user attached together with live frames."""
    client = _client()
    live = _png_b64(40, 40, (200, 10, 10))
    shot = _png_b64(41, 41, (10, 200, 10))
    client.script = [[_text("ok"), _text("", "stop")]]
    await client.submit_multimodal_turn(
        "what about this screenshot?", (live, shot),
        turn_id="t1", source="screen", image_request_ids=(None, "req-1"),
    )
    message = next(m for m in client._conversation_history if image_count(m.content))
    assert message.metadata["image_kinds"] == ["live", "user"]

    urls = await _turn(client, "and the next question")
    assert len(urls) == 1
    client.discard_auto_captured_history_images()
    urls = await _turn(client, "privacy mode on")
    assert len(urls) == 1


@pytest.mark.asyncio
async def test_declined_proactive_turn_leaves_the_live_turn_untouched(monkeypatch):
    """A callback that declines because another reply is live must not strip that reply's frames."""
    client = _client()
    await _turn(
        client, "look", turn_images=(_png_b64(40, 40),), turn_source="screen", turn_source_count=1,
    )
    # The entry check passes; another reply starts during the callback's
    # awaits, so the final check before it begins declines.
    calls = []

    def declines(_mode):
        calls.append(1)
        return len(calls) > 1

    monkeypatch.setattr(client, "_declines_over_another_reply", declines)
    assert await client.prompt_ephemeral("callback") is False
    assert len(calls) >= 2
    assert any(image_count(m.content) for m in client._conversation_history)


@pytest.mark.asyncio
async def test_revocation_mid_turn_strips_the_unsent_proactive_screenshot_synchronously():
    """Turn A is in history and parked on its transcript callback when consent is revoked.

    The strip happens inside the consent update with no await: a concurrent
    reader of history (turn B) and turn A's own request, sent after the
    callback, both carry only the user's photo, not the proactive screenshot.
    """
    import asyncio

    client = _client()
    client.set_proactive_screenshot(_png_b64(40, 40, (1, 2, 3)))
    photo = _png_b64(41, 41, (4, 5, 6))
    client._pending_images = [photo]
    parked = asyncio.Event()
    resume = asyncio.Event()

    async def transcript_callback(_text):
        parked.set()
        await resume.wait()

    client.script = [[_text("ok"), _text("", "stop")]]
    turn_a = asyncio.create_task(
        client.stream_text("reply with my photo", input_transcript_callback=transcript_callback)
    )
    try:
        await _until_parked(parked, turn_a)
        message = next(m for m in client._conversation_history if image_count(m.content))
        assert message.metadata["image_kinds"] == ["auto_screenshot", "user"]
        assert image_count(message.content) == 2

        manager = SimpleNamespace(_proactive_vision_enabled=True, session=client, lanlan_name="T")
        LLMSessionManager.update_proactive_vision_consent(manager, False)
        # No await since the revocation: what turn B would read right now.
        assert image_count(message.content) == 1
        assert message.metadata["image_kinds"] == ["user"]
        kept_url = next(p["image_url"]["url"] for p in message.content if p.get("type") == "image_url")

        resume.set()
        await asyncio.wait_for(turn_a, timeout=5)
    finally:
        resume.set()
        if not turn_a.done():
            turn_a.cancel()
    urls = _urls(client.requests[-1])
    # Turn A's own request: only the user's photo.
    assert urls == [kept_url]
    # Plugins get what the provider got: the snapshot taken before the
    # revocation must not publish the stripped screenshot.
    published_images, published_sources = client._publish_provider_frames.call_args.args[:2]
    assert len(published_images) == 1 and list(published_sources) == ["user"]


async def _until_parked(parked, task):
    """Wait for the turn to park; surface its own exception if it ends first."""
    import asyncio

    waiter = asyncio.ensure_future(parked.wait())
    done, _ = await asyncio.wait({waiter, task}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        waiter.cancel()
        task.result()
        raise AssertionError("turn finished before parking on its transcript callback")
    if waiter not in done:
        waiter.cancel()
        raise AssertionError("turn never parked on its transcript callback")


@pytest.mark.asyncio
async def test_proactive_reply_keeps_live_frames_of_a_turn_still_heading_to_the_provider():
    """A user turn is in history but parked on its transcript callback; a proactive reply runs."""
    import asyncio

    client = _client()
    live = _png_b64(40, 40, (200, 10, 10))
    parked = asyncio.Event()
    resume = asyncio.Event()

    async def transcript_callback(_text):
        parked.set()
        await resume.wait()

    client.script = [
        [_text("proactive"), _text("", "stop")],
        [_text("user reply"), _text("", "stop")],
    ]
    turn = asyncio.create_task(client.stream_text(
        "what is on screen?", turn_images=(live,), turn_source="screen", turn_source_count=1,
        input_transcript_callback=transcript_callback,
    ))
    try:
        await _until_parked(parked, turn)
        await client.prompt_ephemeral("callback")
        resume.set()
        await asyncio.wait_for(turn, timeout=5)
    finally:
        resume.set()
        if not turn.done():
            turn.cancel()
    # The user turn's own request still carries its live frame.
    assert [_dominant_channel(u) for u in _urls(client.requests[-1])] == ["red"]


@pytest.mark.asyncio
async def test_revocation_drops_a_staged_proactive_screenshot_not_yet_in_history():
    client = _client()
    client.set_proactive_screenshot(_png_b64(40, 40, (1, 2, 3)))
    client.discard_auto_captured_history_images()
    assert client._proactive_image_to_inject is None
    assert await _turn(client, "reply after privacy mode turned on") == []


@pytest.mark.asyncio
async def test_revocation_while_the_turn_fits_its_images_keeps_the_screenshot_out(monkeypatch):
    """The turn already took the screenshot; consent is revoked during its image fit."""
    import asyncio

    from main_logic.omni_offline_client import _streaming as offline_streaming

    real_fit = offline_streaming.fit_images_to_turn_budget
    fitting = asyncio.Event()
    resume = asyncio.Event()

    async def paused_fit(images, budget):
        fitting.set()
        await resume.wait()
        return await real_fit(images, budget)

    monkeypatch.setattr(offline_streaming, "fit_images_to_turn_budget", paused_fit)
    client = _client()
    client.set_proactive_screenshot(_png_b64(40, 40, (1, 2, 3)))
    client._pending_images = [_png_b64(41, 41, (4, 5, 6))]
    client.script = [[_text("ok"), _text("", "stop")]]
    turn = asyncio.create_task(client.stream_text("reply with my photo"))
    await asyncio.wait_for(fitting.wait(), timeout=5)
    client.discard_auto_captured_history_images()
    resume.set()
    await asyncio.wait_for(turn, timeout=5)

    message = next(m for m in client._conversation_history if image_count(m.content))
    assert message.metadata["image_kinds"] == ["user"]
    assert len(_urls(client.requests[-1])) == 1


@pytest.mark.asyncio
async def test_sampled_frame_count_marks_live_frames_without_a_bus_channel():
    client = _client()
    await _turn(
        client, "look", turn_images=(_png_b64(40, 40), _png_b64(41, 41)), turn_source_count=1,
    )
    message = next(m for m in client._conversation_history if image_count(m.content))
    assert message.metadata["image_kinds"] == ["live", "user"]
