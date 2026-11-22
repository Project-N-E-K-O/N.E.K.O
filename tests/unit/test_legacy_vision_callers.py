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
    yield
    lv._reset_for_tests()


def _configs(vision=VISION):
    async def aget(slot, **_kwargs):
        return {"conversation": CHAT, "vision": vision}[slot]

    def get(slot, **_kwargs):
        return {"conversation": CHAT, "vision": vision}[slot]

    return SimpleNamespace(aget_model_api_config=aget, get_model_api_config=get)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("vision, expected", [(None, "chat-x"), (VISION, "llava")])
async def test_watch_together_completion_model(monkeypatch, tmp_path, vision, expected):
    from main_logic.watch_together import engine

    used = []

    async def completion(cfg, *_args, **_kwargs):
        used.append(cfg["model"])
        return {"events": []}

    monkeypatch.setattr(engine, "structured_json_completion", completion)
    instance = engine.Engine(tmp_path, None, "cat")
    instance._cm = _configs(vision=vision)

    assert await instance.llm([{"type": "text", "text": "x"}], {}) == {"events": []}
    assert used == [expected]
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
