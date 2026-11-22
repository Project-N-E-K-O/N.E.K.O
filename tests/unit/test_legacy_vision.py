"""Images go to the chat model; the retired vision slot is only a fallback."""

import asyncio
import json

import pytest

from utils import legacy_vision as lv


@pytest.fixture(autouse=True)
def _reset():
    lv._reset_for_tests()
    yield
    lv._reset_for_tests()


class _StatusError(Exception):
    def __init__(self, message, status_code=400, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


CHAT = {"model": "text-only", "base_url": "https://chat.example/v1", "api_key": "k1", "is_custom": True}
VISION = {"model": "llava", "base_url": "http://127.0.0.1:11434/v1", "api_key": "", "is_custom": True}


# Replies seen from providers on 2026-11-22 when a text-only model got an image.
@pytest.mark.parametrize("message", [
    "Error code: 400 - {'code': 20041, 'message': 'The model is not a VLM (Vision Language Model). "
    "Please use text-only prompts.', 'data': None}",
    "Error code: 400 - {'error': {'code': '1210', 'message': \"messages.content.type 参数非法，取值范围 ['text']\"}}",
    "Failed to deserialize the JSON body into the target type: messages[1]: unknown variant `image_url`, "
    "expected `text`",
    "This model does not support image input",
    "当前模型不支持图片输入",
])
def test_image_refusals_are_recognized(message):
    assert lv.is_image_unsupported_error(_StatusError(message))


@pytest.mark.parametrize("exc", [
    _StatusError("The model is not a VLM", status_code=500),
    _StatusError("Image is too large, max 10MB"),
    _StatusError("Invalid API key", status_code=401),
    _StatusError("messages.content.type 参数非法，取值范围 ['text', 'image_url', 'video_url']"),
    _StatusError("max_tokens is too large"),
    RuntimeError("connection reset"),
])
def test_other_errors_are_not_image_refusals(exc):
    assert not lv.is_image_unsupported_error(exc)


def test_refusal_found_in_error_body():
    exc = _StatusError("Error code: 400", body={"error": {"message": "does not support image input"}})
    assert lv.is_image_unsupported_error(exc)


def test_vision_matching_chat_is_not_a_separate_fallback():
    same = dict(CHAT)
    assert lv.choose_image_model(CHAT, same).legacy is None
    assert lv.choose_image_model(CHAT, {**CHAT, "base_url": "https://chat.example/v1/"}).legacy is None
    assert lv.choose_image_model(CHAT, {**CHAT, "model": ""}).legacy is None
    assert lv.choose_image_model(CHAT, {**VISION, "is_custom": False}).legacy is None
    assert lv.choose_image_model(CHAT, VISION).legacy == VISION
    assert lv.choose_image_model(CHAT, {**CHAT, "api_key": "k2"}).legacy is not None
    assert lv.choose_image_model(CHAT, {**CHAT, "provider_type": "anthropic"}).legacy is not None


def _run(coro):
    return asyncio.run(coro)


def test_chat_model_used_when_it_reads_images():
    calls = []

    async def call(cfg):
        calls.append(cfg["model"])
        return "ok"

    choice = lv.choose_image_model(CHAT, VISION)
    assert _run(lv.run_with_image_fallback(choice, call, where="t")) == "ok"
    assert calls == ["text-only"]
    assert not choice.using_legacy


def test_refusal_retries_on_legacy_vision_and_sticks():
    notices = []
    lv.register_notice_sink(notices.append)
    calls = []

    async def call(cfg):
        calls.append(cfg["model"])
        if cfg["model"] == "text-only":
            raise _StatusError("The model is not a VLM (Vision Language Model). Please use text-only prompts.")
        return "seen"

    choice = lv.choose_image_model(CHAT, VISION)
    assert _run(lv.run_with_image_fallback(choice, call, where="t")) == "seen"
    assert calls == ["text-only", "llava"]
    assert len(notices) == 1
    payload = json.loads(notices[0])
    assert payload["code"] == lv.LEGACY_VISION_FALLBACK_CODE
    assert payload["details"] == {"chat_model": "text-only", "vision_model": "llava"}

    # Later calls for the same chat model skip the failing request, and the
    # frontend is not told again.
    again = lv.choose_image_model(CHAT, VISION)
    assert again.using_legacy and again.config["model"] == "llava"
    assert _run(lv.run_with_image_fallback(again, call, where="t")) == "seen"
    assert calls == ["text-only", "llava", "llava"]
    assert len(notices) == 1


def test_changing_the_chat_model_tries_it_again():
    async def refuse(cfg):
        raise _StatusError("does not support image input")

    async def fallback_ok(cfg):
        if cfg["model"] != "llava":
            raise _StatusError("does not support image input")
        return "seen"

    _run(lv.run_with_image_fallback(lv.choose_image_model(CHAT, VISION), fallback_ok, where="t"))
    new_chat = {**CHAT, "model": "vision-chat"}
    choice = lv.choose_image_model(new_chat, VISION)
    assert not choice.using_legacy
    assert choice.config["model"] == "vision-chat"


def test_refusal_without_legacy_vision_propagates():
    async def call(cfg):
        raise _StatusError("does not support image input")

    choice = lv.choose_image_model(CHAT, None)
    with pytest.raises(_StatusError):
        _run(lv.run_with_image_fallback(choice, call, where="t"))
    assert not lv._engaged_routes


def test_other_errors_do_not_fall_back():
    calls = []

    async def call(cfg):
        calls.append(cfg["model"])
        raise _StatusError("Invalid API key", status_code=401)

    with pytest.raises(_StatusError):
        _run(lv.run_with_image_fallback(lv.choose_image_model(CHAT, VISION), call, where="t"))
    assert calls == ["text-only"]
    assert not lv._engaged_routes


def test_async_notice_sink_is_scheduled():
    seen = []

    async def sink(message):
        seen.append(json.loads(message)["code"])

    lv.register_notice_sink(sink)

    async def main():
        assert lv.engage_legacy_vision(lv.choose_image_model(CHAT, VISION), "t")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    _run(main())
    assert seen == [lv.LEGACY_VISION_FALLBACK_CODE]


def test_deprecation_is_logged(caplog):
    with caplog.at_level("WARNING", logger="utils.legacy_vision"):
        lv.engage_legacy_vision(lv.choose_image_model(CHAT, VISION), "screenshot description")
    assert any("[DEPRECATED]" in r.getMessage() and "llava" in r.getMessage() for r in caplog.records)


def test_frontend_is_told_once_per_chat_model():
    notices = []
    lv.register_notice_sink(notices.append)
    choice = lv.choose_image_model(CHAT, VISION)
    assert lv.engage_legacy_vision(choice, "a")
    assert lv.engage_legacy_vision(choice, "b")
    assert len(notices) == 1
    other = lv.choose_image_model({**CHAT, "model": "other-chat"}, VISION)
    assert lv.engage_legacy_vision(other, "a")
    assert len(notices) == 2
