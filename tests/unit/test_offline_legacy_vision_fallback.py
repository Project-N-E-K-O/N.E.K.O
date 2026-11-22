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
