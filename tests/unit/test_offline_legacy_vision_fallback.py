"""Offline chat: images go to the chat model; a retired vision config keeps working.

A session sends a picture to its chat model. A user who configured a separate
vision model before the vision slot was retired keeps that route until the
release: images switch to it as before, and the deprecation is announced.
A chat model that cannot read images yields a clear status instead of a raw
provider error.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest

from tests.unit.test_offline_provider_frame_publish import _png_b64
from tests.unit.test_offline_turn_cancellation_e2e import _client, _emitted, _text
from utils import legacy_vision as lv

pytestmark = pytest.mark.unit

CHAT = {"model": "chat-x", "base_url": "http://chat.invalid/v1", "api_key": "chat-key", "is_custom": True}
VISION = {"model": "llava", "base_url": "http://127.0.0.1:11434/v1", "api_key": "", "is_custom": True}


@pytest.fixture(autouse=True)
def _reset_notices(monkeypatch):
    lv._reset_for_tests()
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())
    yield
    lv._reset_for_tests()


def _refusal() -> openai.BadRequestError:
    request = httpx.Request("POST", "http://chat.invalid/v1/chat/completions")
    body = {"code": 20041, "message": "The model is not a VLM (Vision Language Model). Please use text-only prompts."}
    return openai.BadRequestError(
        f"Error code: 400 - {body}", response=httpx.Response(400, request=request), body=body,
    )


def _session(vision=None):
    client = _client()
    client.model = CHAT["model"]
    client.base_url = CHAT["base_url"]
    client.api_key = CHAT["api_key"]
    client.provider_type = None
    choice = lv.choose_image_model(CHAT, vision)
    legacy = choice.legacy or {}
    client.vision_model = legacy.get("model", "")
    client.vision_base_url = legacy.get("base_url", "")
    client.vision_api_key = legacy.get("api_key") or None
    client.vision_provider_type = None
    client._image_choice = choice
    client.switch_model = AsyncMock(return_value=True)
    client.on_status_message = AsyncMock()
    return client


def _statuses(client):
    return [json.loads(call.args[0]) for call in client.on_status_message.await_args_list]


async def test_image_goes_to_the_chat_model():
    notices = []
    lv.register_notice_sink(notices.append)
    client = _session()
    client._pending_images = [_png_b64(4, 4)]
    client.script = [[_text("看到了。"), _text("", "stop")]]

    await client.stream_text("看看这个")

    client.switch_model.assert_not_awaited()
    assert len(client.requests) == 1
    assert _emitted(client) == ["看到了。"]
    assert notices == []


async def test_separate_vision_config_takes_images_and_is_announced():
    notices = []
    lv.register_notice_sink(notices.append)
    client = _session(vision=VISION)
    client._pending_images = [_png_b64(4, 4)]
    client.script = [[_text("看到了。"), _text("", "stop")]]

    await client.stream_text("看看这个")

    client.switch_model.assert_awaited_once_with("llava", use_vision_config=True)
    assert len(client.requests) == 1
    assert [json.loads(n)["code"] for n in notices] == [lv.LEGACY_VISION_DEPRECATED_CODE]


async def test_text_turns_never_announce():
    notices = []
    lv.register_notice_sink(notices.append)
    client = _session(vision=VISION)
    client.script = [[_text("好。"), _text("", "stop")]]

    await client.stream_text("你好")

    client.switch_model.assert_not_awaited()
    assert notices == []


async def test_chat_model_refusing_images_gets_a_clear_status():
    client = _session()
    client._pending_images = [_png_b64(4, 4)]
    client.script = [_refusal()]

    await client.stream_text("看看这个")

    client.switch_model.assert_not_awaited()
    assert len(client.requests) == 1
    assert {"code": "CHAT_MODEL_IMAGE_UNSUPPORTED"} in _statuses(client)
    # Shared with screenshots and proactive chat.
    assert lv.chat_refuses_images(CHAT)


async def test_other_errors_keep_their_status():
    client = _session()
    client.script = [openai.BadRequestError(
        "max_tokens is too large",
        response=httpx.Response(400, request=httpx.Request("POST", "http://chat.invalid/v1")),
        body=None,
    )]

    await client.stream_text("你好")

    codes = [s.get("code") for s in _statuses(client)]
    assert "CHAT_MODEL_IMAGE_UNSUPPORTED" not in codes
    assert "TEXT_GEN_ERROR" in codes


async def test_tool_images_stay_on_the_chat_model():
    client = _session()
    assert await client.prepare_for_tool_images() is True
    client.switch_model.assert_not_awaited()
    # Once the chat model is known to reject images, the tool result keeps
    # its text and the picture is not sent.
    lv.mark_chat_image_refused(CHAT, "t")
    assert await client.prepare_for_tool_images() is False


async def test_tool_images_use_a_separate_vision_config():
    notices = []
    lv.register_notice_sink(notices.append)
    client = _session(vision=VISION)
    assert await client.prepare_for_tool_images() is True
    client.switch_model.assert_awaited_once_with("llava", use_vision_config=True)
    assert len(notices) == 1


async def test_proactive_media_uses_a_separate_vision_config():
    notices = []
    lv.register_notice_sink(notices.append)
    client = _session(vision=VISION)
    client.script = [[_text("看到了。"), _text("", "stop")]]

    await client.prompt_ephemeral("callback with media", images=[_png_b64(4, 4)])

    client.switch_model.assert_awaited_once()
    assert client.switch_model.await_args.args == ("llava",)
    assert len(notices) == 1


async def test_refusal_on_the_separate_vision_config_is_not_blamed_on_the_chat_model():
    client = _session(vision=VISION)
    # The session already talks to the separate vision config.
    client.model = VISION["model"]
    client.base_url = VISION["base_url"]
    client.api_key = None
    client._pending_images = [_png_b64(4, 4)]
    client.script = [_refusal()]

    await client.stream_text("看看这个")

    codes = [s.get("code") for s in _statuses(client)]
    assert "CHAT_MODEL_IMAGE_UNSUPPORTED" not in codes
    assert "TEXT_GEN_ERROR" in codes
    # A refusal from the separate vision route says nothing about the chat model.
    assert not lv.chat_refuses_images(CHAT)


async def test_failed_switch_does_not_spend_the_notice():
    notices = []
    lv.register_notice_sink(notices.append)
    client = _session(vision=VISION)
    client.switch_model = AsyncMock(side_effect=RuntimeError("vision endpoint down"))
    client._pending_images = [_png_b64(4, 4)]
    client.script = [[_text("不该出现"), _text("", "stop")]]

    with pytest.raises(RuntimeError):
        await client.stream_text("看看这个")
    assert notices == []

    # Tool images: a failed switch degrades to "could not see it", no notice.
    assert await client.prepare_for_tool_images() is False
    assert notices == []


async def test_dropped_proactive_switch_does_not_spend_the_notice():
    notices = []
    lv.register_notice_sink(notices.append)
    client = _session(vision=VISION)
    client.switch_model = AsyncMock(return_value=False)  # a reply began meanwhile
    client.script = [[_text("不该出现"), _text("", "stop")]]

    await client.prompt_ephemeral("callback with media", images=[_png_b64(4, 4)])
    assert notices == []


async def test_rejected_images_leave_history_so_text_chat_recovers():
    from utils.image_history import image_count

    client = _session()
    client._pending_images = [_png_b64(4, 4)]
    client.script = [_refusal(), [_text("好。"), _text("", "stop")]]

    await client.stream_text("看看这个")
    await client.stream_text("那算了，聊点别的")

    # The second, text-only request no longer carries the rejected picture.
    assert len(client.requests) == 2
    assert all(image_count(getattr(m, "content", "")) == 0 for m in client.requests[1])
    assert _emitted(client) == ["好。"]


async def test_tool_images_already_on_the_vision_route_still_note_the_use():
    notices = []
    lv.register_notice_sink(notices.append)
    client = _session(vision=VISION)
    client.model = VISION["model"]
    client.base_url = VISION["base_url"]
    client.api_key = None
    client.vision_api_key = None
    assert await client.prepare_for_tool_images() is True
    client.switch_model.assert_not_awaited()
    assert len(notices) == 1


async def test_proactive_media_dropped_by_the_budget_does_not_note_the_use(monkeypatch):
    import main_logic.omni_offline_client._lifecycle as lifecycle_mod

    async def drop_all(images, _budget):
        return [], {"user_visible": True, "original_count": len(images), "final_count": 0,
                    "normalized": 0, "sampled": 0, "compressed": 0, "dropped": len(images)}

    monkeypatch.setattr(lifecycle_mod, "fit_images_to_turn_budget", drop_all)
    notices = []
    lv.register_notice_sink(notices.append)
    client = _session(vision=VISION)
    client.script = [[_text("嗯。"), _text("", "stop")]]

    await client.prompt_ephemeral("callback with media", images=[_png_b64(4, 4)])
    assert notices == []


async def test_older_image_turns_without_recorded_kinds_are_cleaned_too():
    from main_logic.omni_offline_client._shared import HumanMessage
    from utils.image_history import image_count

    client = _session()
    old = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _png_b64(4, 4)}},
        {"type": "text", "text": "以前发过的图"},
    ])
    client._conversation_history.append(old)
    client.script = [_refusal()]

    await client.stream_text("这张呢")

    assert image_count(old.content) == 0
    assert "以前发过的图" in str(old.content)


async def test_refusal_without_an_image_choice_marks_the_client_route():
    client = _session()
    client._image_choice = None
    client._pending_images = [_png_b64(4, 4)]
    client.script = [_refusal()]

    await client.stream_text("看看这个")
    assert lv.chat_refuses_images(CHAT)


async def test_images_after_a_refusal_are_not_sent_again():
    from utils.image_history import image_count

    client = _session()
    client._pending_images = [_png_b64(4, 4)]
    client.script = [_refusal(), [_text("好的。"), _text("", "stop")]]
    await client.stream_text("看看这个")

    # A new picture on a later turn: the text goes out, the picture does not.
    client._pending_images = [_png_b64(4, 4)]
    await client.stream_text("那这张呢")

    assert len(client.requests) == 2
    assert all(image_count(getattr(m, "content", "")) == 0 for m in client.requests[1])
    assert "那这张呢" in str(client.requests[1][-1].content)
    assert _emitted(client) == ["好的。"]
    # The user is told their own picture was skipped.
    import asyncio
    await asyncio.sleep(0)
    assert {"code": "CHAT_MODEL_IMAGE_SKIPPED"} in _statuses(client)


async def test_skipped_live_frames_stay_quiet():
    lv.mark_chat_image_refused(CHAT, "t")
    client = _session()
    client.script = [[_text("嗯。"), _text("", "stop")]]
    await client.stream_text("我在看这个", turn_images=[_png_b64(4, 4)])
    import asyncio
    await asyncio.sleep(0)
    assert {"code": "CHAT_MODEL_IMAGE_SKIPPED"} not in _statuses(client)
    assert _emitted(client) == ["嗯。"]


async def test_separate_vision_config_is_not_affected_by_a_chat_refusal():
    from utils.image_history import image_count

    lv.mark_chat_image_refused(CHAT, "t")
    client = _session(vision=VISION)
    client._pending_images = [_png_b64(4, 4)]
    client.script = [[_text("看到了。"), _text("", "stop")]]
    await client.stream_text("看看这个")
    assert image_count(client.requests[0][-1].content) == 1


async def test_proactive_callback_refusal_is_recorded():
    client = _session()
    client.script = [_refusal()]
    await client.prompt_ephemeral("callback with media", images=[_png_b64(4, 4)])
    assert lv.chat_refuses_images(CHAT)


async def test_proactive_callback_sends_text_only_to_a_refusing_chat_model():
    from utils.image_history import image_count

    lv.mark_chat_image_refused(CHAT, "t")
    client = _session()
    client.script = [[_text("收到。"), _text("", "stop")]]
    await client.prompt_ephemeral("callback with media", images=[_png_b64(4, 4)])

    assert len(client.requests) == 1
    assert all(image_count(getattr(m, "content", "")) == 0 for m in client.requests[0])
    assert _emitted(client) == ["收到。"]


async def test_proactive_callback_refusal_on_a_separate_vision_route_is_not_recorded():
    client = _session(vision=VISION)
    client.script = [_refusal()]
    await client.prompt_ephemeral("callback with media", images=[_png_b64(4, 4)])
    assert not lv.chat_refuses_images(CHAT)


@pytest.mark.parametrize("extra", [
    {"turn_image_user_flags": [False, True]},       # a screenshot sampled with the frames
    {"turn_source": "screen", "turn_source_count": 1},  # an attachment after the frames
])
async def test_user_pictures_among_live_frames_are_reported(extra):
    import asyncio

    lv.mark_chat_image_refused(CHAT, "t")
    client = _session()
    client.script = [[_text("嗯。"), _text("", "stop")]]
    await client.stream_text("看这个", turn_images=[_png_b64(4, 4), _png_b64(5, 5)], **extra)
    await asyncio.sleep(0)
    assert {"code": "CHAT_MODEL_IMAGE_SKIPPED"} in _statuses(client)


async def test_refusal_record_applies_without_an_image_choice():
    from utils.image_history import image_count

    lv.mark_chat_image_refused(CHAT, "t")
    client = _session()
    client._image_choice = None
    client._pending_images = [_png_b64(4, 4)]
    client.script = [[_text("好。"), _text("", "stop")]]
    await client.stream_text("看看")
    assert image_count(client.requests[0][-1].content) == 0


async def test_proactive_callback_refusal_cleans_history_images():
    from main_logic.omni_offline_client._shared import HumanMessage
    from utils.image_history import image_count

    client = _session()
    old = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _png_b64(4, 4)}},
        {"type": "text", "text": "以前的图"},
    ])
    client._conversation_history.append(old)
    client.script = [_refusal()]
    await client.prompt_ephemeral("callback with media", images=[_png_b64(4, 4)])
    assert image_count(old.content) == 0
