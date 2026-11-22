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


def test_chat_refusal_is_remembered_per_route():
    calls = []

    async def refuse(cfg):
        calls.append(cfg["model"])
        raise _StatusError("The model is not a VLM (Vision Language Model). Please use text-only prompts.")

    choice = lv.choose_image_model(CHAT, None)
    with pytest.raises(_StatusError):
        _run(lv.run_image_call(choice, refuse, where="t"))
    assert lv.chat_refuses_images(CHAT)
    # Later image calls to the same chat route are not sent at all.
    with pytest.raises(lv.ChatModelImageUnsupported):
        _run(lv.run_image_call(choice, refuse, where="t"))
    assert calls == ["text-only"]
    # Text-only requests still go through.
    assert _run(lv.run_image_call(choice, _recording_call(calls), where="t", has_image=False)) == "ok"
    # Another model, endpoint path or key is another route.
    assert not lv.chat_refuses_images({**CHAT, "model": "vision-chat"})
    assert not lv.chat_refuses_images({**CHAT, "api_key": "k2"})
    assert not lv.chat_refuses_images({**CHAT, "base_url": "https://chat.example/V1"})
    assert lv.chat_refuses_images({**CHAT, "base_url": "https://CHAT.example/v1/"})


def test_other_errors_do_not_mark_the_chat_model():
    async def fail(cfg):
        raise _StatusError("Invalid API key", status_code=401)

    with pytest.raises(_StatusError):
        _run(lv.run_image_call(lv.choose_image_model(CHAT, None), fail, where="t"))
    assert not lv.chat_refuses_images(CHAT)


def test_refusal_from_a_separate_vision_config_does_not_mark_the_chat_model():
    async def refuse(cfg):
        raise _StatusError("does not support image input")

    with pytest.raises(_StatusError):
        _run(lv.run_image_call(lv.choose_image_model(CHAT, VISION), refuse, where="t"))
    assert not lv.chat_refuses_images(CHAT)


def test_notice_sink_may_return_a_task():
    seen = []

    async def deliver(message):
        seen.append(json.loads(message)["code"])

    async def main():
        loop = asyncio.get_running_loop()
        lv.register_notice_sink(lambda message: loop.create_task(deliver(message)))
        calls = []
        assert await lv.run_image_call(lv.choose_image_model(CHAT, VISION), _recording_call(calls), where="t") == "ok"
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls == ["llava"]

    _run(main())
    assert seen == [lv.LEGACY_VISION_DEPRECATED_CODE]


@pytest.mark.parametrize("provider, separate", [
    ("follow_assist", False), ("follow_core", False), ("follow_conversation", False),
    ("follow_memory", False), ("openai", True), ("custom", True), ("", True), (None, True),
])
def test_only_an_explicitly_configured_vision_slot_is_a_legacy_route(provider, separate):
    # A slot following another provider was never configured separately; ''
    # is a config saved before the dropdown existed (filled in by hand).
    assert (lv.choose_image_model(CHAT, VISION, provider).legacy is not None) is separate


def test_resolvers_read_the_vision_dropdown_from_core_config():
    from types import SimpleNamespace

    def manager(provider):
        core = {"visionModelProvider": provider}

        async def aget(slot, **_kw):
            return CHAT if slot == "conversation" else VISION

        async def acore():
            return core

        return SimpleNamespace(
            aget_model_api_config=aget, aget_core_config=acore,
            get_model_api_config=lambda slot, **_kw: CHAT if slot == "conversation" else VISION,
            get_core_config=lambda: core,
        )

    assert not _run(lv.aresolve_image_model(manager("follow_assist"))).using_legacy
    assert _run(lv.aresolve_image_model(manager("openai"))).using_legacy
    assert not lv.resolve_image_model(manager("follow_assist")).using_legacy
    assert lv.resolve_image_model(manager("openai")).using_legacy
    # An explicit snapshot wins over the manager's own.
    assert not _run(lv.aresolve_image_model(manager("openai"), core_config={"visionModelProvider": "follow_core"})).using_legacy


def test_undelivered_notice_is_sent_again_on_a_later_use():
    sent = []
    delivered = [False]

    def sink(message):
        sent.append(message)
        return delivered[0]

    lv.register_notice_sink(sink)
    choice = lv.choose_image_model(CHAT, VISION)
    lv.note_legacy_vision_use(choice, "a")   # no page connected
    lv.note_legacy_vision_use(choice, "b")   # tried again
    delivered[0] = True
    lv.note_legacy_vision_use(choice, "c")
    lv.note_legacy_vision_use(choice, "d")   # shown: not again
    assert len(sent) == 3


@pytest.mark.parametrize("delivered, expected_sends", [(False, 2), (True, 1)])
def test_async_sink_result_decides_whether_the_notice_was_shown(delivered, expected_sends):
    sent = []

    async def sink(message):
        sent.append(message)
        return delivered

    lv.register_notice_sink(sink)
    choice = lv.choose_image_model(CHAT, VISION)

    async def main():
        for _ in range(2):
            lv.note_legacy_vision_use(choice, "t")
            for _ in range(3):
                await asyncio.sleep(0)

    _run(main())
    assert len(sent) == expected_sends


def test_failing_sink_leaves_the_notice_pending():
    calls = []

    def sink(message):
        calls.append(message)
        raise RuntimeError("socket closed")

    lv.register_notice_sink(sink)
    choice = lv.choose_image_model(CHAT, VISION)
    lv.note_legacy_vision_use(choice, "t")
    lv.note_legacy_vision_use(choice, "t")
    assert len(calls) == 2


def test_vision_config_outside_custom_api_is_never_a_legacy_route():
    # Without the custom API, the vision slot is the provider preset, never
    # something the user configured separately.
    preset_vision = {**VISION, "api_key": "preset-key", "is_custom": False}
    assert lv.choose_image_model(CHAT, preset_vision, "openai").legacy is None
