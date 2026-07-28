import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_server.card_forge_server import forge_story_generator


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _FakeLLM:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def ainvoke(self, _messages):
        return SimpleNamespace(content="她在窗边收好信纸。\u201c我会记住这一天。\u201d")


@pytest.mark.unit
async def test_generate_story_preserves_configured_provider_type(monkeypatch):
    class _ConfigManager:
        def get_model_api_config(self, tier):
            if tier == "summary":
                return {
                    "model": "custom-claude",
                    "base_url": "https://custom-anthropic.example/v1",
                    "api_key": "test-key",
                    "provider_type": "anthropic",
                }
            return {}

    async def resolve_active_neko_context(**_kwargs):
        return SimpleNamespace(
            master_name="主人",
            lanlan_name="兰兰",
            lanlan_prompt="温柔而可靠",
            source="test",
            facts_path=None,
        )

    create_calls = []

    def create_chat_llm(model, base_url, api_key, **kwargs):
        create_calls.append((model, base_url, api_key, kwargs))
        return _FakeLLM()

    monkeypatch.setattr(
        "main_logic.card_forge_facts.resolve_active_neko_context",
        resolve_active_neko_context,
    )
    monkeypatch.setattr("utils.config_manager.get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr("utils.llm_client.create_chat_llm", create_chat_llm)
    monkeypatch.setattr("utils.llm_client.set_active_character", lambda *_args: object())
    monkeypatch.setattr("utils.llm_client.reset_active_character", lambda _token: None)
    monkeypatch.setattr("utils.token_tracker.set_call_type", lambda _call_type: None)

    result = await forge_story_generator.generate_forge_card_story(
        {
            "storyLead": "她第一次收到主人写来的信。",
            "runtimeCharacterHint": "兰兰",
            "card": {"attrName": "温柔"},
        }
    )

    assert result.model == "custom-claude"
    assert create_calls[0][3]["provider_type"] == "anthropic"


@pytest.mark.unit
def test_frontend_timeout_covers_both_backend_story_targets():
    source = (
        PROJECT_ROOT / "frontend" / "card-forge" / "src" / "App.jsx"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"const FORGE_STORY_FETCH_TIMEOUT_MS = ([\d_]+)",
        source,
    )

    assert match is not None
    frontend_timeout_ms = int(match.group(1).replace("_", ""))
    backend_retry_window_ms = forge_story_generator.FORGE_STORY_TIMEOUT_SECONDS * 2 * 1000
    assert frontend_timeout_ms >= backend_retry_window_ms + 5_000


@pytest.mark.unit
def test_sensitive_forge_logs_keep_only_length_metadata():
    secret = "主人生日是七月二十六日"

    masked = forge_story_generator._mask_sensitive_text(secret)

    assert masked == f"(len={len(secret)})"
    assert secret not in masked


@pytest.mark.unit
async def test_generate_story_rejects_stale_runtime_character_hint(monkeypatch):
    class _ConfigManager:
        pass

    async def resolve_active_neko_context():
        return SimpleNamespace(
            master_name="主人",
            lanlan_name="新角色",
            lanlan_prompt="",
            source="test",
            facts_path=None,
        )

    monkeypatch.setattr(
        "main_logic.card_forge_facts.resolve_active_neko_context",
        resolve_active_neko_context,
    )
    monkeypatch.setattr("utils.config_manager.get_config_manager", lambda: _ConfigManager())

    with pytest.raises(
        forge_story_generator.ForgeStoryGenerationError,
        match="runtime_character_hint_mismatch",
    ):
        await forge_story_generator.generate_forge_card_story(
            {
                "storyLead": "旧角色的故事引子。",
                "runtimeCharacterHint": "旧角色",
                "card": {"attrName": "温柔"},
            }
        )
