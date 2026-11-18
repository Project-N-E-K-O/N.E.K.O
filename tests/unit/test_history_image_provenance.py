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


async def _turn(client, text, **kwargs):
    client.script = [[_text("ok"), _text("", "stop")]]
    await client.stream_text(text, **kwargs)
    return client.requests[-1]


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
    assert len(_urls(first)) == 2
    message = next(m for m in client._conversation_history if image_count(m.content))
    assert message.metadata["image_kinds"] == ["live", "user"]

    second = await _turn(client, "and now?")
    urls = _urls(second)
    # The live frame left; the photo the user attached is still there.
    assert len(urls) == 1
    assert _b64(urls[0]) != _b64(_urls(first)[0])
    assert _b64(urls[0]) == _b64(_urls(first)[1])
    first_turn = next(
        m for m in client._conversation_history
        if isinstance(m.content, list)
        and any(p.get("text") == "what am I looking at?" for p in m.content if isinstance(p, dict))
    )
    assert any("1 image(s)" in p.get("text", "") for p in first_turn.content if isinstance(p, dict))
    assert first_turn.metadata["image_kinds"] == ["user"]

    third = await _turn(client, "still?")
    assert len(_urls(third)) == 1


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
    assert len(_urls(first)) == 2
    message = next(m for m in client._conversation_history if image_count(m.content))
    assert message.metadata["image_kinds"] == ["auto_screenshot", "user"]

    # The next-turn live rule leaves the proactive screenshot alone.
    second = await _turn(client, "hmm")
    assert len(_urls(second)) == 2

    client.discard_auto_captured_history_images()
    third = await _turn(client, "privacy mode is on now")
    urls = _urls(third)
    assert len(urls) == 1
    assert _b64(urls[0]) == _b64(_urls(first)[1])


def test_revocation_fails_closed_when_the_image_mapping_was_lost():
    client = _client()
    from utils.llm_client import HumanMessage

    message = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
        {"type": "text", "text": "x"},
    ])
    message.metadata["image_kinds"] = ["unknown"]
    message.metadata["image_prefit_kinds"] = ["live", "user"]
    client._conversation_history.append(message)
    client._strip_answered_live_frames()  # unknown is never stripped as live
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
