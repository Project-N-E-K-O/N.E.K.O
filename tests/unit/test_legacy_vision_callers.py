"""Each image path reads with the chat model, or a retired vision config while one exists."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from utils import legacy_vision as lv

pytestmark = pytest.mark.unit

CHAT = {"model": "chat-x", "base_url": "https://chat.example/v1", "api_key": "k", "is_custom": True,
        "provider_type": "openai"}
VISION = {"model": "llava", "base_url": "http://127.0.0.1:11434/v1", "api_key": "", "is_custom": True,
          "provider_type": "openai"}


@pytest.fixture(autouse=True)
def _reset():
    lv._reset_for_tests()
    routes.clear()
    yield
    lv._reset_for_tests()


def _configs(vision=VISION):
    async def aget(slot, **_kwargs):
        return {"conversation": CHAT, "vision": vision}[slot]

    def get(slot, **_kwargs):
        return {"conversation": CHAT, "vision": vision}[slot]

    return SimpleNamespace(aget_model_api_config=aget, get_model_api_config=get)


routes = []


def _route(cfg):
    return (cfg["model"], cfg["base_url"], cfg["api_key"])


def _llm_factory(models):
    """create_chat_llm_async stand-in recording which model each request went to."""

    class _LLM:
        def __init__(self, model):
            self.model = model

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def ainvoke(self, *_args, **_kwargs):
            return SimpleNamespace(content="a cat on a desk", response_metadata={})

    async def create(*args, **kwargs):
        model = kwargs.get("model") or (args[0] if args else "")
        models.append(model)
        routes.append((model, kwargs.get("base_url"), kwargs.get("api_key") or ""))
        return _LLM(model)

    return create


@pytest.mark.asyncio
@pytest.mark.parametrize("vision, expected", [(None, "chat-x"), (CHAT, "chat-x"), (VISION, "llava")])
async def test_screenshot_description_model(monkeypatch, vision, expected):
    from utils import config_manager, screenshot_utils

    models = []
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _configs(vision=vision))
    monkeypatch.setattr(screenshot_utils, "create_chat_llm_async", _llm_factory(models))

    assert await screenshot_utils.analyze_image_with_vision_model("encoded") == "a cat on a desk"
    assert models == [expected]
    assert routes == [_route(VISION if expected == "llava" else CHAT)]


@pytest.mark.asyncio
@pytest.mark.parametrize("vision, expected", [(None, "chat-x"), (VISION, "llava")])
async def test_minigame_vision_model(monkeypatch, vision, expected):
    from utils import game_vision

    models = []
    monkeypatch.setattr(game_vision, "get_config_manager", lambda: _configs(vision=vision))
    monkeypatch.setattr(game_vision, "create_chat_llm_async", _llm_factory(models))

    attachments = [{"type": "image", "label": "",
                    "image_data_url": "data:image/jpeg;base64,AAAA"}]
    result = await game_vision._invoke(
        text="what is this", attachments=attachments, system_prompt="sys",
        max_completion_tokens=100, timeout=10, is_current=lambda: True,
    )
    assert result == "a cat on a desk"
    assert models == [expected]
    assert routes == [_route(VISION if expected == "llava" else CHAT)]


@pytest.mark.asyncio
@pytest.mark.parametrize("vision, expected", [(None, "chat-x"), (VISION, "llava")])
async def test_watch_together_completion_model(monkeypatch, tmp_path, vision, expected):
    from main_logic.watch_together import engine

    used = []

    async def completion(cfg, *_args, **_kwargs):
        used.append(_route(cfg))
        return {"events": []}

    monkeypatch.setattr(engine, "structured_json_completion", completion)
    instance = engine.Engine(tmp_path, None, "cat")
    instance._cm = _configs(vision=vision)

    assert await instance.llm([{"type": "text", "text": "x"}], {}) == {"events": []}
    assert used == [_route(VISION if expected == "llava" else CHAT)]
    assert instance.vision_endpoint["model"] == expected


@pytest.mark.asyncio
async def test_watch_together_requires_a_chat_model(tmp_path):
    from main_logic.watch_together import engine

    instance = engine.Engine(tmp_path, None, "cat")
    instance._cm = SimpleNamespace(get_model_api_config=lambda slot: {"model": ""})
    with pytest.raises(RuntimeError, match="聊天模型"):
        await instance.vision_config()


@pytest.mark.parametrize("vision, expected", [(CHAT, ""), (VISION, "llava")])
def test_offline_session_gets_vision_only_as_a_separate_fallback(vision, expected):
    from unittest.mock import MagicMock
    from main_logic.core.lifecycle import LifecycleMixin

    session = LifecycleMixin._create_offline_vlm_client(
        MagicMock(),
        conversation_config=CHAT, vision_config=vision, tool_definitions=[],
        max_response_length=300, external_tts_enabled=False,
    )
    assert session.model == "chat-x"
    assert session.vision_model == expected
    assert session._image_choice.conversation is CHAT
    assert (session._image_choice.legacy is not None) == bool(expected)


@pytest.mark.asyncio
async def test_main_server_broadcasts_the_notice_to_every_page():
    import asyncio
    from app.main_server import _register_legacy_vision_notice

    open_page = SimpleNamespace(session_manager=SimpleNamespace(send_status=AsyncMock()))
    broken_page = SimpleNamespace(session_manager=SimpleNamespace(
        send_status=AsyncMock(side_effect=RuntimeError("closed"))))
    no_manager = SimpleNamespace(session_manager=None)
    _register_legacy_vision_notice({"a": broken_page, "b": open_page, "c": no_manager})

    lv.note_legacy_vision_use(lv.choose_image_model(CHAT, VISION), "t")
    for _ in range(3):
        await asyncio.sleep(0)

    open_page.session_manager.send_status.assert_awaited_once()
    sent = open_page.session_manager.send_status.await_args.args[0]
    assert lv.LEGACY_VISION_DEPRECATED_CODE in sent


@pytest.mark.parametrize("vision, expected", [(None, "chat-x"), (CHAT, "chat-x"), (VISION, "llava")])
def test_proactive_screenshot_model(vision, expected):
    from main_logic.proactive_chat.service import _proactive_model_config

    choice, config = _proactive_model_config(CHAT, vision)
    assert config.conversation_model == "chat-x"
    assert config.vision_model == expected
    assert config.has_vision_model
    assert choice.using_legacy == (expected == "llava")


@pytest.mark.asyncio
async def test_watch_together_accepts_a_separate_vision_config_alone(tmp_path):
    from main_logic.watch_together import engine

    instance = engine.Engine(tmp_path, None, "cat")
    instance._cm = SimpleNamespace(
        get_model_api_config=lambda slot: {"model": "", "api_key": ""} if slot == "conversation" else VISION)
    assert await instance.vision_config() is VISION



@pytest.mark.asyncio
@pytest.mark.parametrize("with_image, told", [(False, 0), (True, 1)])
async def test_watch_together_text_only_request_is_not_a_vision_use(monkeypatch, with_image, told):
    from main_logic.watch_together import engine

    notices = []
    lv.register_notice_sink(notices.append)

    async def completion(cfg, *_args, **_kwargs):
        return {"line": "hi"}

    monkeypatch.setattr(engine, "structured_json_completion", completion)
    content = [{"type": "text", "text": "intermission"}]
    if with_image:
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}})
    await engine.image_json_completion(
        lv.choose_image_model(CHAT, VISION), "sys", content, {}, lambda v: (v, []),
        stage="s", label="l", where="watch-together live")
    assert len(notices) == told


@pytest.mark.parametrize("vision, available", [(VISION, True), (None, False)])
def test_watch_invitation_accepts_a_separate_vision_config(monkeypatch, vision, available):
    from main_logic.watch_together import engine, preparation

    monkeypatch.setattr(engine.media, "check_available", lambda: None)
    manager = SimpleNamespace(
        _config_manager=SimpleNamespace(get_model_api_config=lambda slot: {"model": "", "api_key": ""}
                                        if slot == "conversation" else vision),
        _resolve_tts_worker_spec=lambda: (None, "tts-key", "", "provider", False, {}),
        _tts_worker_supports_completion=lambda *args: True,
    )
    assert preparation.is_available(manager) is available


@pytest.mark.asyncio
async def test_minigame_vision_unavailable_for_a_refusing_chat_model(monkeypatch):
    from utils import game_vision

    lv.mark_chat_image_refused(CHAT, "t")
    models = []
    monkeypatch.setattr(game_vision, "get_config_manager", lambda: _configs(vision=None))
    monkeypatch.setattr(game_vision, "create_chat_llm_async", _llm_factory(models))
    with pytest.raises(ValueError, match="vision_unavailable"):
        await game_vision._invoke(
            text="x", attachments=[{"type": "image", "label": "", "image_data_url": "data:image/jpeg;base64,AAAA"}],
            system_prompt="sys", max_completion_tokens=10, timeout=10, is_current=lambda: True,
        )
    assert models == []


@pytest.mark.parametrize("vision, sees", [(None, False), (VISION, True)])
def test_proactive_drops_the_screenshot_for_a_refusing_chat_model(vision, sees):
    from main_logic.proactive_chat.service import _proactive_model_config

    lv.mark_chat_image_refused(CHAT, "t")
    _choice, config = _proactive_model_config(CHAT, vision)
    # Like before the vision slot was retired: no usable image model, text only.
    assert config.has_vision_model is sees


@pytest.mark.asyncio
async def test_proactive_phase2_refusal_marks_the_chat_model(monkeypatch):
    from main_logic.proactive_chat import generation
    from main_logic.proactive_chat.service import _proactive_model_config
    from tests.unit.test_proactive_phase2_generation import _FakeManager, _patch_runtime_guards

    _patch_runtime_guards(monkeypatch)

    class _Refusal(Exception):
        status_code = 400

    async def make_llm(**_kwargs):
        raise _Refusal("does not support image input")

    async def run(model_config, use_vision):
        await generation._generate_phase2_stream(
            mgr=_FakeManager(), proactive_sid="proactive-sid", lanlan_name="兰兰",
            messages=[object(), object()], make_llm=make_llm,
            phase2_use_vision=use_vision, phase2_disable_thinking=True,
            conversation_model="chat-x", expects_source_tag=True, proactive_lang="zh",
            master_name="博士", human_text="开始生成", screenshot_b64="abc" if use_vision else None,
            on_image_refused=generation._chat_image_refusal_hook(model_config),
        )

    # A separate vision route refusing says nothing about the chat model.
    _c, legacy_config = _proactive_model_config(CHAT, VISION)
    assert generation._chat_image_refusal_hook(legacy_config) is None
    # Same gateway and key, another model: still a separate vision route.
    _c, same_gateway = _proactive_model_config(CHAT, {**CHAT, "model": "chat-x-vl"})
    assert generation._chat_image_refusal_hook(same_gateway) is None
    _c, chat_config = _proactive_model_config(CHAT, None)
    await run(chat_config, use_vision=False)
    assert not lv.chat_refuses_images(CHAT)
    await run(chat_config, use_vision=True)
    assert lv.chat_refuses_images(CHAT)


def test_offline_session_ignores_a_vision_slot_that_follows_the_assist_provider():
    from unittest.mock import MagicMock
    from main_logic.core.lifecycle import LifecycleMixin

    session = LifecycleMixin._create_offline_vlm_client(
        MagicMock(),
        conversation_config=CHAT, vision_config=VISION, vision_provider="follow_assist",
        tool_definitions=[], max_response_length=300, external_tts_enabled=False,
    )
    assert session.vision_model == ""


@pytest.mark.parametrize("provider, expected", [("follow_assist", "chat-x"), ("openai", "llava")])
def test_proactive_honours_the_vision_dropdown(provider, expected):
    from main_logic.proactive_chat.service import _proactive_model_config

    _choice, config = _proactive_model_config(CHAT, VISION, provider)
    assert config.vision_model == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("use_vision, told", [(False, 0), (True, 1)])
async def test_proactive_notes_legacy_use_only_when_the_screenshot_is_sent(monkeypatch, use_vision, told):
    from main_logic.proactive_chat import generation
    from main_logic.proactive_chat.service import _proactive_model_config

    notices = []
    lv.register_notice_sink(notices.append)
    monkeypatch.setattr(generation, "create_chat_llm_async", AsyncMock(return_value=SimpleNamespace()))
    _choice, config = _proactive_model_config(CHAT, VISION)
    await generation._make_proactive_llm(config, use_vision=use_vision)
    assert len(notices) == told



@pytest.mark.asyncio
@pytest.mark.parametrize("taken, delivered", [((False, False), False), ((False, True), True)])
async def test_main_server_broadcast_reports_delivery(taken, delivered):
    from app.main_server import _register_legacy_vision_notice

    pages = {str(i): SimpleNamespace(session_manager=SimpleNamespace(send_status=AsyncMock(return_value=ok)))
             for i, ok in enumerate(taken)}
    captured = {}
    original = lv.register_notice_sink
    try:
        lv.register_notice_sink = lambda sink: captured.setdefault("sink", sink)
        _register_legacy_vision_notice(pages)
    finally:
        lv.register_notice_sink = original
    assert await captured["sink"]("{}") is delivered


def test_refusal_hook_ignores_a_legacy_route_differing_only_in_protocol():
    from main_logic.proactive_chat import generation
    from main_logic.proactive_chat.generation import ProactiveModelConfig
    from main_logic.proactive_chat.service import _proactive_model_config

    _c, config = _proactive_model_config(CHAT, {**CHAT, "provider_type": "anthropic"}, "openai")
    assert config.image_choice.using_legacy
    assert generation._chat_image_refusal_hook(config) is None
    # Without an image choice, the protocol is compared too.
    bare = ProactiveModelConfig(
        conversation_model="chat-x", conversation_base_url=CHAT["base_url"],
        conversation_api_key="k", conversation_provider_type="openai",
        vision_model="chat-x", vision_base_url=CHAT["base_url"],
        vision_api_key="k", vision_provider_type="anthropic",
    )
    assert generation._chat_image_refusal_hook(bare) is None
    same = ProactiveModelConfig(
        conversation_model="chat-x", conversation_base_url=CHAT["base_url"],
        conversation_api_key="k", conversation_provider_type="openai",
        vision_model="chat-x", vision_base_url=CHAT["base_url"],
        vision_api_key="k", vision_provider_type="openai",
    )
    assert generation._chat_image_refusal_hook(same) is not None
