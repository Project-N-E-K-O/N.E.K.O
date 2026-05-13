from __future__ import annotations

from copy import deepcopy

import pytest

from utils.config_manager import ensure_default_yui_voice_for_free_api, get_reserved


YUI_FREE_VOICE_ID = "voice-tone-QqMv39SYSW"


class _FakeConfigManager:
    def __init__(self, characters: dict, core_config: dict | None = None):
        self.characters = deepcopy(characters)
        self.core_config = deepcopy(core_config or {})
        self.saved_characters = None

    async def aload_characters(self):
        return deepcopy(self.characters)

    async def asave_characters(self, characters):
        self.saved_characters = deepcopy(characters)
        self.characters = deepcopy(characters)

    async def aget_core_config(self):
        return deepcopy(self.core_config)


def _characters_with_current_yui(*, voice_id: str = "", model_path: str = "yui-origin/yui-origin.model3.json") -> dict:
    return {
        "当前猫娘": "YUI",
        "猫娘": {
            "YUI": {
                "昵称": "YUI",
                "_reserved": {
                    "voice_id": voice_id,
                    "avatar": {
                        "model_type": "live2d",
                        "live2d": {
                            "model_path": model_path,
                        },
                    },
                },
            }
        },
    }


@pytest.mark.asyncio
async def test_free_api_binds_empty_current_default_yui_voice(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(_characters_with_current_yui(voice_id=""))

    changed = await ensure_default_yui_voice_for_free_api(
        config_manager,
        {"coreApi": "free", "assistApi": "free"},
    )

    assert changed is True
    assert config_manager.saved_characters is not None
    yui = config_manager.saved_characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == YUI_FREE_VOICE_ID


@pytest.mark.asyncio
async def test_free_api_bind_can_use_current_core_config_when_reader_entry_calls_without_payload(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(
        _characters_with_current_yui(voice_id=""),
        core_config={"coreApi": "free", "assistApi": "free"},
    )

    changed = await ensure_default_yui_voice_for_free_api(config_manager)

    assert changed is True
    yui = config_manager.saved_characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == YUI_FREE_VOICE_ID


@pytest.mark.asyncio
async def test_non_free_api_does_not_bind_empty_yui_voice(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(_characters_with_current_yui(voice_id=""))

    changed = await ensure_default_yui_voice_for_free_api(
        config_manager,
        {"coreApi": "qwen", "assistApi": "qwen"},
    )

    assert changed is False
    assert config_manager.saved_characters is None
    yui = config_manager.characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == ""


@pytest.mark.asyncio
async def test_free_api_does_not_overwrite_existing_yui_voice(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(_characters_with_current_yui(voice_id="custom-voice"))

    changed = await ensure_default_yui_voice_for_free_api(
        config_manager,
        {"coreApi": "free", "assistApi": "free"},
    )

    assert changed is False
    assert config_manager.saved_characters is None
    yui = config_manager.characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == "custom-voice"


@pytest.mark.asyncio
async def test_free_api_does_not_bind_non_default_yui_model(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(
        _characters_with_current_yui(voice_id="", model_path="custom-yui/custom-yui.model3.json")
    )

    changed = await ensure_default_yui_voice_for_free_api(
        config_manager,
        {"coreApi": "free", "assistApi": "free"},
    )

    assert changed is False
    assert config_manager.saved_characters is None
    yui = config_manager.characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == ""
