"""Each image path reads with the chat model and falls back to a retired vision config."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from utils import legacy_vision as lv

pytestmark = pytest.mark.unit

CHAT = {"model": "chat-x", "base_url": "https://chat.example/v1", "api_key": "k", "is_custom": True,
        "provider_type": "openai"}
VISION = {"model": "llava", "base_url": "http://127.0.0.1:11434/v1", "api_key": "", "is_custom": True,
          "provider_type": "openai"}


class _Refusal(Exception):
    status_code = 400

    def __init__(self):
        super().__init__("The model is not a VLM (Vision Language Model). Please use text-only prompts.")


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
    """create_chat_llm_async stand-in: the chat model refuses images, llava reads them."""

    class _LLM:
        def __init__(self, model):
            self.model = model

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def ainvoke(self, *_args, **_kwargs):
            if self.model == "chat-x":
                raise _Refusal()
            return SimpleNamespace(content="a cat on a desk", response_metadata={})

    async def create(*args, **kwargs):
        model = kwargs.get("model") or (args[0] if args else "")
        models.append(model)
        return _LLM(model)

    return create


@pytest.mark.asyncio
async def test_screenshot_description_falls_back(monkeypatch):
    from utils import config_manager, screenshot_utils

    models = []
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _configs())
    monkeypatch.setattr(screenshot_utils, "create_chat_llm_async", _llm_factory(models))

    assert await screenshot_utils.analyze_image_with_vision_model("encoded") == "a cat on a desk"
    assert models == ["chat-x", "llava"]


@pytest.mark.asyncio
async def test_screenshot_description_without_legacy_fails_quietly(monkeypatch):
    from utils import config_manager, screenshot_utils

    models = []
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _configs(vision=CHAT))
    monkeypatch.setattr(screenshot_utils, "create_chat_llm_async", _llm_factory(models))

    assert await screenshot_utils.analyze_image_with_vision_model("encoded") is None
    assert models == ["chat-x"]


@pytest.mark.asyncio
async def test_minigame_vision_falls_back(monkeypatch):
    from utils import game_vision

    models = []
    monkeypatch.setattr(game_vision, "get_config_manager", lambda: _configs())
    monkeypatch.setattr(game_vision, "create_chat_llm_async", _llm_factory(models))

    attachments = [{"type": "image", "label": "",
                    "image_data_url": "data:image/jpeg;base64,AAAA"}]
    result = await game_vision._invoke(
        text="what is this", attachments=attachments, system_prompt="sys",
        max_completion_tokens=100, timeout=10, is_current=lambda: True,
    )
    assert result == "a cat on a desk"
    assert models == ["chat-x", "llava"]


@pytest.mark.asyncio
async def test_watch_together_completion_falls_back(monkeypatch, tmp_path):
    from main_logic.watch_together import engine

    used = []

    async def completion(cfg, *_args, **_kwargs):
        used.append(cfg["model"])
        if cfg["model"] == "chat-x":
            raise _Refusal()
        return {"events": []}

    monkeypatch.setattr(engine, "structured_json_completion", completion)
    instance = engine.Engine(tmp_path, None, "cat")
    instance._cm = _configs()

    assert await instance.llm([{"type": "text", "text": "x"}], {}) == {"events": []}
    assert used == ["chat-x", "llava"]
    # The endpoint recorded for diagnostics still names the chat model it tried first.
    assert instance.vision_endpoint["model"] == "chat-x"


@pytest.mark.asyncio
async def test_watch_together_requires_a_chat_model(tmp_path):
    from main_logic.watch_together import engine

    instance = engine.Engine(tmp_path, None, "cat")
    instance._cm = SimpleNamespace(get_model_api_config=lambda slot: {"model": ""} if slot == "conversation" else VISION)
    with pytest.raises(RuntimeError, match="聊天模型"):
        await instance.vision_config()


def test_proactive_refusal_hook_engages_only_with_a_legacy_config():
    from main_logic.proactive_chat.generation import ProactiveModelConfig, _image_refusal_hook

    def config(choice):
        return ProactiveModelConfig(
            conversation_model="chat-x", conversation_base_url=CHAT["base_url"],
            conversation_api_key="k", conversation_provider_type=None,
            vision_model=choice.config["model"], vision_base_url=choice.config["base_url"],
            vision_api_key=choice.config["api_key"], image_choice=choice,
        )

    assert _image_refusal_hook(config(lv.choose_image_model(CHAT, CHAT))) is None
    hook = _image_refusal_hook(config(lv.choose_image_model(CHAT, VISION)))
    assert hook is not None
    hook()
    assert lv.choose_image_model(CHAT, VISION).using_legacy
    # Once engaged, a new round already reads with the vision config: no hook needed.
    assert _image_refusal_hook(config(lv.choose_image_model(CHAT, VISION))) is None


@pytest.mark.asyncio
async def test_proactive_phase2_reports_image_refusal(monkeypatch):
    from main_logic.proactive_chat import generation
    from tests.unit.test_proactive_phase2_generation import _FakeManager, _patch_runtime_guards

    _patch_runtime_guards(monkeypatch)

    async def make_llm(**_kwargs):
        raise _Refusal()

    refused = []
    for use_vision in (True, False):
        await generation._generate_phase2_stream(
            mgr=_FakeManager(), proactive_sid="proactive-sid", lanlan_name="兰兰",
            messages=[object(), object()], make_llm=make_llm,
            phase2_use_vision=use_vision, phase2_disable_thinking=True,
            conversation_model="chat-x", expects_source_tag=True, proactive_lang="zh",
            master_name="博士", human_text="开始生成", screenshot_b64="abc" if use_vision else None,
            on_image_refused=lambda: refused.append(True),
        )
    # Only a round that sent the screenshot counts the refusal.
    assert refused == [True]


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

    lv.engage_legacy_vision(lv.choose_image_model(CHAT, VISION), "t")
    for _ in range(3):
        await asyncio.sleep(0)

    open_page.session_manager.send_status.assert_awaited_once()
    sent = open_page.session_manager.send_status.await_args.args[0]
    assert lv.LEGACY_VISION_FALLBACK_CODE in sent
