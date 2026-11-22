"""Images go to the chat model; a retired vision config keeps working until the release."""

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


def _recording_call(calls, result="ok"):
    async def call(cfg):
        calls.append(cfg["model"])
        return result
    return call


def test_chat_model_reads_images_without_a_separate_vision_config():
    notices = []
    lv.register_notice_sink(notices.append)
    calls = []
    for vision in (None, CHAT, {**CHAT, "base_url": "https://chat.example/v1/"}):
        choice = lv.choose_image_model(CHAT, vision)
        assert not choice.using_legacy
        assert _run(lv.run_image_call(choice, _recording_call(calls), where="t")) == "ok"
    assert calls == ["text-only"] * 3
    assert notices == []


def test_separate_vision_config_is_used_first_and_announced_once():
    # A local vision endpoint may have been chosen so screenshots never leave
    # the machine: the chat provider must not start receiving them.
    notices = []
    lv.register_notice_sink(notices.append)
    calls = []
    choice = lv.choose_image_model(CHAT, VISION)
    assert choice.using_legacy and choice.config is VISION
    for where in ("screenshot", "screenshot", "mini-game"):
        assert _run(lv.run_image_call(choice, _recording_call(calls), where=where)) == "ok"
    assert calls == ["llava"] * 3
    assert len(notices) == 1
    payload = json.loads(notices[0])
    assert payload == {"code": lv.LEGACY_VISION_DEPRECATED_CODE, "details": {"vision_model": "llava"}}

    # Once per app start, whichever separate config shows up next.
    other = lv.choose_image_model({**CHAT, "model": "other-chat"}, {**VISION, "model": "qwen-vl"})
    lv.note_legacy_vision_use(other, "watch-together")
    assert len(notices) == 1


def test_deprecation_warning_is_rate_limited_per_call_site(caplog, monkeypatch):
    clock = [1000.0]
    from tests.fake_clock import patch_module_clock
    patch_module_clock(monkeypatch, lv, monotonic=lambda: clock[0])
    choice = lv.choose_image_model(CHAT, VISION)

    def warnings():
        return [r for r in caplog.records if "[DEPRECATED]" in r.getMessage()]

    with caplog.at_level("WARNING", logger="utils.legacy_vision"):
        lv.note_legacy_vision_use(choice, "screenshot")
        lv.note_legacy_vision_use(choice, "screenshot")
        assert len(warnings()) == 1 and "llava" in warnings()[0].getMessage()
        lv.note_legacy_vision_use(choice, "mini-game")
        assert len(warnings()) == 2
        clock[0] += lv._WARN_INTERVAL_SECONDS
        lv.note_legacy_vision_use(choice, "screenshot")
        assert len(warnings()) == 3


def test_no_notice_without_a_separate_config():
    notices = []
    lv.register_notice_sink(notices.append)
    lv.note_legacy_vision_use(lv.choose_image_model(CHAT, None), "t")
    assert notices == []


def test_async_notice_sink_is_scheduled():
    seen = []

    async def sink(message):
        seen.append(json.loads(message)["code"])

    lv.register_notice_sink(sink)

    async def main():
        lv.note_legacy_vision_use(lv.choose_image_model(CHAT, VISION), "t")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    _run(main())
    assert seen == [lv.LEGACY_VISION_DEPRECATED_CODE]


def test_errors_from_the_image_call_propagate():
    async def call(cfg):
        raise _StatusError("does not support image input")

    with pytest.raises(_StatusError):
        _run(lv.run_image_call(lv.choose_image_model(CHAT, None), call, where="t"))


def test_text_only_request_on_the_route_announces_nothing():
    notices = []
    lv.register_notice_sink(notices.append)
    calls = []
    choice = lv.choose_image_model(CHAT, VISION)
    _run(lv.run_image_call(choice, _recording_call(calls), where="t", has_image=False))
    assert calls == ["llava"] and notices == []
    _run(lv.run_image_call(choice, _recording_call(calls), where="t"))
    assert len(notices) == 1
