import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import main_routers.characters_router as characters_router
from main_logic.core import LLMSessionManager
from utils.config_manager import ConfigManager
from utils.native_voice_registry import (
    resolve_native_voice_for_routing,
    should_block_free_voice_for_route,
)


class _FakeConfigManager:
    def __init__(self, stored_voice_ids=()):
        self._stored_voice_ids = set(stored_voice_ids)

    def voice_id_exists_in_any_storage(self, voice_id):
        return voice_id.casefold() in {
            stored_voice_id.casefold()
            for stored_voice_id in self._stored_voice_ids
        }


class _FakeCharactersRouterConfigManager:
    """Mimics the ConfigManager surface used by characters_router.get_voices
    plus the registry's get_active_realtime_native_provider lookup."""

    def __init__(self, realtime_api_type, base_url="", stored_voice_ids=()):
        self._realtime_api_type = realtime_api_type
        self._base_url = base_url
        self._stored_voice_ids = set(stored_voice_ids)

    def get_voices_for_current_api(self, for_listing: bool = False):
        return {}

    async def aget_core_config(self):
        return {"CORE_API_TYPE": "gemini"}

    def get_model_api_config(self, model_type):
        return {"api_type": self._realtime_api_type, "base_url": self._base_url}

    def get_core_config(self):
        return {"CORE_API_TYPE": "gemini"}

    def voice_id_exists_in_any_storage(self, voice_id):
        return voice_id.casefold() in {
            stored_voice_id.casefold()
            for stored_voice_id in self._stored_voice_ids
        }

    async def aload_characters(self):
        return {"猫娘": {}}


def _make_mgr(voice_id, stored_voice_ids=()):
    mgr = object.__new__(LLMSessionManager)
    mgr.core_api_type = "gemini"
    mgr.voice_id = voice_id
    mgr._is_free_preset_voice = False
    mgr._config_manager = _FakeConfigManager(stored_voice_ids)
    return mgr


def _make_config_manager_with_realtime_api_type(realtime_api_type):
    mgr = object.__new__(ConfigManager)
    mgr.get_voices_for_current_api = lambda for_listing=False: {}
    mgr.get_model_api_config = lambda model_type: {"api_type": realtime_api_type}
    mgr.get_core_config = lambda: {"CORE_API_TYPE": "gemini"}
    return mgr


def test_gemini_alias_checks_canonical_voice_collision():
    config_manager = _FakeConfigManager(stored_voice_ids={"Puck"})

    assert (
        resolve_native_voice_for_routing(
            "gemini",
            "中文男",
            config_manager.voice_id_exists_in_any_storage,
        )
        == ("Puck", False)
    )


def test_gemini_alias_checks_canonical_voice_collision_case_insensitively():
    config_manager = _FakeConfigManager(stored_voice_ids={"puck"})

    assert (
        resolve_native_voice_for_routing(
            "gemini",
            "中文男",
            config_manager.voice_id_exists_in_any_storage,
        )
        == ("Puck", False)
    )


def test_gemini_alias_without_collision_uses_native_realtime_voice():
    mgr = _make_mgr("中文男")
    config_manager = _FakeConfigManager()

    assert (
        resolve_native_voice_for_routing(
            "gemini",
            "中文男",
            config_manager.voice_id_exists_in_any_storage,
        )
        == ("Puck", True)
    )
    assert LLMSessionManager._resolve_realtime_voice(mgr, {}) == "Puck"


def test_validate_gemini_voice_uses_active_realtime_provider():
    local_realtime_mgr = _make_config_manager_with_realtime_api_type("local")
    gemini_realtime_mgr = _make_config_manager_with_realtime_api_type("gemini")

    assert ConfigManager.validate_voice_id(local_realtime_mgr, "中文男") is False
    assert ConfigManager.validate_voice_id(gemini_realtime_mgr, "中文男") is True


def test_validate_keeps_free_native_voice_on_lanlan_app_route():
    """回归：lanlan.app/free 路由下 validate_voice_id 仍认 Step/free 原生音色合法，
    避免 cleanup_invalid_voice_ids 在用户切线路时把 characters.json 里保存的
    qingchunshaonv 等 voice_id 静默清空（PR #1290 Codex P1）。"""
    mgr = object.__new__(ConfigManager)
    mgr.get_voices_for_current_api = lambda for_listing=False: {}
    mgr.get_model_api_config = lambda model_type: {
        "api_type": "free",
        "base_url": "wss://lanlan.app/realtime",
    }
    mgr.get_core_config = lambda: {
        "CORE_API_TYPE": "free",
        "CORE_URL": "wss://lanlan.app/realtime",
    }

    assert ConfigManager.validate_voice_id(mgr, "qingchunshaonv") is True


def test_new_catgirl_default_voice_id_keeps_legacy_fallback(monkeypatch):
    monkeypatch.setattr("utils.api_config_loader.get_free_voices", lambda: {})

    assert characters_router._get_new_catgirl_default_voice_id() == "voice-tone-PGLiyZt65w"


def test_new_catgirl_default_voice_id_prefers_configured_presets(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"cuteGirl": "", "other": "voice-other"},
    )
    assert characters_router._get_new_catgirl_default_voice_id() == "voice-other"

    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"cuteGirl": "voice-custom", "other": "voice-other"},
    )
    assert characters_router._get_new_catgirl_default_voice_id() == "voice-custom"


def test_native_preview_provider_respects_custom_voice_collision():
    native_mgr = _FakeCharactersRouterConfigManager("gemini")
    colliding_mgr = _FakeCharactersRouterConfigManager("gemini", stored_voice_ids={"Puck"})

    assert characters_router._get_active_native_preview_provider(native_mgr, "中文男") == "gemini"
    assert characters_router._get_active_native_preview_provider(colliding_mgr, "Puck") is None
    assert characters_router._get_active_native_preview_provider(colliding_mgr, "中文男") is None


def test_step_native_preview_provider_respects_custom_voice_collision():
    native_mgr = _FakeCharactersRouterConfigManager("free", "wss://lanlan.tech/realtime")
    colliding_mgr = _FakeCharactersRouterConfigManager(
        "free",
        "wss://lanlan.tech/realtime",
        stored_voice_ids={"qingchunshaonv"},
    )

    assert characters_router._get_active_native_preview_provider(native_mgr, "中文女") == "free"
    assert characters_router._get_active_native_preview_provider(colliding_mgr, "qingchunshaonv") is None
    assert characters_router._get_active_native_preview_provider(colliding_mgr, "中文女") is None


@pytest.mark.asyncio
async def test_voice_catalog_uses_active_realtime_provider(monkeypatch):
    monkeypatch.setattr(
        characters_router,
        "get_config_manager",
        lambda: _FakeCharactersRouterConfigManager("local"),
    )

    local_result = await characters_router.get_voices()

    monkeypatch.setattr(
        characters_router,
        "get_config_manager",
        lambda: _FakeCharactersRouterConfigManager("gemini"),
    )

    gemini_result = await characters_router.get_voices()

    assert "native_voices" not in local_result
    assert "native_voices" in gemini_result


@pytest.mark.asyncio
async def test_free_voice_catalog_hidden_on_lanlan_app(monkeypatch):
    monkeypatch.setattr(
        characters_router,
        "get_config_manager",
        lambda: _FakeCharactersRouterConfigManager(
            "free",
            "wss://lanlan.app/realtime",
        ),
    )

    overseas_free_result = await characters_router.get_voices()

    monkeypatch.setattr(
        characters_router,
        "get_config_manager",
        lambda: _FakeCharactersRouterConfigManager(
            "free",
            "wss://lanlan.tech/realtime",
        ),
    )

    domestic_free_result = await characters_router.get_voices()

    assert "native_voices" not in overseas_free_result
    assert "native_voices" in domestic_free_result


def test_free_native_voice_blocked_on_lanlan_app_route():
    voice_id_exists = _FakeConfigManager().voice_id_exists_in_any_storage

    assert (
        should_block_free_voice_for_route(
            "free",
            "  qingchunshaonv  ",
            "wss://lanlan.app/realtime",
            voice_id_exists,
        )
        is True
    )
    assert (
        should_block_free_voice_for_route(
            "free",
            "qingchunshaonv",
            "wss://lanlan.tech/realtime",
            voice_id_exists,
        )
        is False
    )
    assert (
        should_block_free_voice_for_route(
            "free",
            "qingchunshaonv",
            "wss://notlanlan.app/realtime",
            voice_id_exists,
        )
        is False
    )


def test_voice_mode_gemini_native_uses_realtime_audio_not_external_tts():
    mgr = _make_mgr("Puck")

    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr,
            "audio",
            {"base_url": "https://generativelanguage.googleapis.com"},
            {"ENABLE_CUSTOM_API": True, "TTS_MODEL_URL": "http://localhost:9880"},
        )
        is False
    )


def test_custom_tts_config_requires_gptsovits_enabled():
    mgr = _make_mgr("")
    realtime_config = {"base_url": "https://generativelanguage.googleapis.com"}

    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr,
            "audio",
            realtime_config,
            {
                "ENABLE_CUSTOM_API": True,
                "TTS_MODEL_URL": "http://localhost:9880",
                "GPTSOVITS_ENABLED": False,
            },
        )
        is False
    )
    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr,
            "audio",
            realtime_config,
            {
                "ENABLE_CUSTOM_API": True,
                "TTS_MODEL_URL": "http://localhost:9880",
                "GPTSOVITS_ENABLED": True,
            },
        )
        is True
    )


@pytest.mark.asyncio
async def test_hot_swap_to_external_tts_starts_pipeline(monkeypatch):
    mgr = _make_mgr("")
    mgr.use_tts = False
    mgr.pending_use_tts = True
    called = False

    async def fake_ensure_tts_pipeline_alive(self):
        nonlocal called
        called = True

    monkeypatch.setattr(
        LLMSessionManager,
        "ensure_tts_pipeline_alive",
        fake_ensure_tts_pipeline_alive,
    )

    await LLMSessionManager._apply_pending_tts_route_after_swap(mgr)

    assert mgr.use_tts is True
    assert called is True
