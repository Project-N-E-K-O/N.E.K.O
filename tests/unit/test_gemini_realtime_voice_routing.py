import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import main_logic.core as core_module
import main_routers.characters_router.voice_preview as characters_router
import main_routers.characters_router.voice_providers as characters_voice_providers
import main_routers.characters_router.crud as characters_crud
from main_logic.core import LLMSessionManager
from utils.config_manager import ConfigManager
from utils.native_voice_registry import (
    resolve_native_voice_for_routing,
)


class _FakeConfigManager:
    def __init__(self, stored_voice_ids=(), core_config=None):
        self._stored_voice_ids = set(stored_voice_ids)
        self._core_config = dict(core_config or {"CORE_API_TYPE": "gemini"})

    def get_core_config(self):
        return dict(self._core_config)

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


def _make_mgr(voice_id, stored_voice_ids=(), core_config=None):
    mgr = object.__new__(LLMSessionManager)
    mgr.core_api_type = "gemini"
    mgr.voice_id = voice_id
    mgr._is_free_preset_voice = False
    mgr._config_manager = _FakeConfigManager(stored_voice_ids, core_config)
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

    assert characters_crud._get_new_catgirl_default_voice_id() == "voice-tone-PGLiyZt65w"


def test_new_catgirl_default_voice_id_prefers_configured_presets(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"cuteGirl": "", "other": "voice-other"},
    )
    assert characters_crud._get_new_catgirl_default_voice_id() == "voice-other"

    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"cuteGirl": "voice-custom", "other": "voice-other"},
    )
    assert characters_crud._get_new_catgirl_default_voice_id() == "voice-custom"


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
async def test_free_voice_catalog_on_lanlan_app_shows_free_intl(monkeypatch):
    """海外免费（lanlan.app）展示 free_intl（Gemini 全量）+ yui/default 置顶 pin；
    国内免费（lanlan.tech）展示 free（阶跃）原生，无 pin。"""
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

    # 海外：Gemini 全量原生 + yui/default 置顶；yui 已从长列表挪进 pin，Leda 保留
    assert "native_voices" in overseas_free_result
    assert "yui" not in overseas_free_result["native_voices"]
    assert "Leda" in overseas_free_result["native_voices"]
    pins = overseas_free_result.get("pinned_voices")
    assert [p["voice_id"] for p in pins] == ["yui", "Leda"]
    assert pins[0]["i18n_key"] == "voice.freeVoice.yui"
    assert pins[1]["i18n_key"] == "voice.freeVoice.default"

    # 国内：阶跃原生，无置顶 pin
    assert "native_voices" in domestic_free_result
    assert "pinned_voices" not in domestic_free_result


@pytest.mark.asyncio
async def test_free_intl_pin_hidden_when_voice_id_collides_with_clone(monkeypatch):
    """用户克隆/自建音色撞名 yui → 该置顶 pin 隐藏（runtime 会按撞名走克隆，
    pin 点了到不了 Gemini）；未撞名的 default(Leda) pin 保留。"""
    monkeypatch.setattr(
        characters_router,
        "get_config_manager",
        lambda: _FakeCharactersRouterConfigManager(
            "free",
            "wss://lanlan.app/realtime",
            stored_voice_ids={"yui"},
        ),
    )

    result = await characters_router.get_voices()
    pins = result.get("pinned_voices")
    assert [p["voice_id"] for p in pins] == ["Leda"]


@pytest.mark.asyncio
async def test_free_intl_native_entry_hidden_when_voice_id_collides(monkeypatch):
    """撞名（跨桶克隆同名）的 Gemini 音色也要从长列表去掉：runtime 按 any-storage
    撞名判定拒绝当 native，展示了点选也到不了 Gemini（与 pin 撞名隐藏对偶）。"""
    monkeypatch.setattr(
        characters_router,
        "get_config_manager",
        lambda: _FakeCharactersRouterConfigManager(
            "free",
            "wss://lanlan.app/realtime",
            stored_voice_ids={"Leda"},
        ),
    )

    result = await characters_router.get_voices()
    # default pin（Leda）撞名隐藏
    assert [p["voice_id"] for p in result.get("pinned_voices")] == ["yui"]
    # 长列表里的 Leda 也撞名隐藏；其它 Gemini 音色不受影响
    assert "Leda" not in result["native_voices"]
    assert "Puck" in result["native_voices"]


def test_free_intl_remaps_gemini_and_yui_native_on_lanlan_app_route():
    """海外免费路由（free + *.lanlan.app）下，Gemini 音色与 yui 经 free_intl 认成
    native；阶跃预设音色不在 free_intl 目录里，按非 native fall through。"""
    voice_id_exists = _FakeConfigManager().voice_id_exists_in_any_storage

    # Gemini 音色 / yui → 海外 free 路由认 native
    assert resolve_native_voice_for_routing(
        "free", "Puck", voice_id_exists, realtime_base_url="wss://lanlan.app/realtime",
    ) == ("Puck", True)
    assert resolve_native_voice_for_routing(
        "free", "  yui  ", voice_id_exists, realtime_base_url="wss://edge.lanlan.app/realtime",
    ) == ("yui", True)
    # default 别名 → Leda
    assert resolve_native_voice_for_routing(
        "free", "default", voice_id_exists, realtime_base_url="wss://lanlan.app/realtime",
    ) == ("Leda", True)

    # 国内 free（lanlan.tech）：阶跃目录不识别 Gemini 音色
    assert resolve_native_voice_for_routing(
        "free", "Puck", voice_id_exists, realtime_base_url="wss://lanlan.tech/realtime",
    ) == ("Puck", False)

    # 海外 free：阶跃预设不在 free_intl 目录 → 非 native
    assert resolve_native_voice_for_routing(
        "free", "qingchunshaonv", voice_id_exists, realtime_base_url="wss://lanlan.app/realtime",
    ) == ("qingchunshaonv", False)


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


def test_livestream_skips_external_tts_regardless_of_voice_preset(monkeypatch):
    """Livestream 上游是 free 路 Gemini 系，无论角色卡 voice_id 是不是 free preset，
    都应跳过外部 TTS 走服务端原生语音。PR #1369 的 free-preset gate 漏掉了
    "livestream + 非 preset 音色"（克隆 / 空 voice_id）这条路径——本测试守门。"""
    monkeypatch.setattr(
        "main_logic.core.is_livestream_active",
        lambda: True,
    )
    realtime_config = {"base_url": "wss://主播自建.example.com/realtime"}
    core_config = {
        "ENABLE_CUSTOM_API": True,
        "TTS_MODEL_URL": "http://localhost:9880",
        "GPTSOVITS_ENABLED": True,
    }

    # 角色卡是克隆音色（非 free preset）
    mgr_clone = _make_mgr("voice-clone-abc")
    mgr_clone.core_api_type = "free"
    mgr_clone._is_free_preset_voice = False
    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr_clone, "audio", realtime_config, core_config,
        )
        is False
    )

    # 角色卡 voice_id 为空
    mgr_empty = _make_mgr("")
    mgr_empty.core_api_type = "free"
    mgr_empty._is_free_preset_voice = False
    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr_empty, "audio", realtime_config, core_config,
        )
        is False
    )

    # 文本模式仍然启用 TTS（没有 realtime audio 通道兜底）
    mgr_text = _make_mgr("voice-clone-abc")
    mgr_text.core_api_type = "free"
    mgr_text._is_free_preset_voice = False
    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr_text, "text", realtime_config, core_config,
        )
        is True
    )


def test_non_livestream_free_preset_still_skips_tts_only_on_lanlan_tech(monkeypatch):
    """回归 PR #1369 原 gate 的窄路径：非 livestream 时，free preset 仅在
    base_url 指向 lanlan.tech 域时跳 TTS，其他域照旧 fallback 外部 TTS。"""
    monkeypatch.setattr(
        "main_logic.core.is_livestream_active",
        lambda: False,
    )
    core_config = {
        "ENABLE_CUSTOM_API": True,
        "TTS_MODEL_URL": "http://localhost:9880",
        "GPTSOVITS_ENABLED": True,
    }
    mgr = _make_mgr("qingchunshaonv")
    mgr.core_api_type = "free"
    mgr._is_free_preset_voice = True

    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr, "audio", {"base_url": "wss://lanlan.tech/realtime"}, core_config,
        )
        is False
    )
    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr, "audio", {"base_url": "wss://lanlan.app/realtime"}, core_config,
        )
        is True
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


def test_explicit_vllm_tts_uses_external_tts_before_native_voice():
    mgr = _make_mgr("Puck")
    realtime_config = {"base_url": "https://generativelanguage.googleapis.com"}

    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr,
            "audio",
            realtime_config,
            {
                "ENABLE_CUSTOM_API": True,
                "ttsModelProvider": "vllm_omni",
                "ttsVoiceId": "Puck",
                "GPTSOVITS_ENABLED": False,
            },
        )
        is True
    )
    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr,
            "audio",
            realtime_config,
            {
                "ENABLE_CUSTOM_API": False,
                "ttsModelProvider": "vllm_omni",
                "ttsVoiceId": "Puck",
                "GPTSOVITS_ENABLED": False,
            },
        )
        is False
    )


def test_vllm_runtime_key_tracks_raw_runtime_config(monkeypatch):
    class _CM:
        def __init__(self, url, model, voice_id, tts_model=""):
            self.url = url
            self.model = model
            self.voice_id = voice_id
            self.tts_model = tts_model

        def get_core_config(self):
            return {
                "ENABLE_CUSTOM_API": True,
                "ttsModelProvider": "vllm_omni",
                "ttsModelUrl": self.url,
                "ttsModelId": self.model,
                "TTS_MODEL": self.tts_model,
                "ttsVoiceId": self.voice_id,
                "DISABLE_TTS": False,
            }

        def get_model_api_config(self, model_type):
            if model_type == "realtime":
                return {"base_url": ""}
            assert model_type == "tts_default"
            return {
                "base_url": "http://localhost:8091",
                "model": "Qwen3-TTS",
                "api_key": "fallback-key",
            }

        def voice_id_exists_in_any_storage(self, voice_id):
            return False

    monkeypatch.setattr(
        core_module,
        "get_tts_worker",
        lambda **kwargs: (object(), "", "vllm_omni"),
    )

    mgr = _make_mgr("")
    mgr._config_manager = _CM("http://localhost:8091", "Qwen3-TTS", "voice-a")
    key_a = LLMSessionManager._build_tts_runtime_key(mgr)
    mgr._config_manager = _CM("http://localhost:8092", "Qwen3-TTS-v2", "voice-b")
    key_b = LLMSessionManager._build_tts_runtime_key(mgr)
    mgr._config_manager = _CM("http://localhost:8093", "", "voice-c", "RouteModel")
    key_c = LLMSessionManager._build_tts_runtime_key(mgr)

    assert key_a != key_b
    assert ("http://localhost:8091", "Qwen3-TTS", "voice-a") in key_a
    assert ("http://localhost:8092", "Qwen3-TTS-v2", "voice-b") in key_b
    assert ("http://localhost:8093", "Qwen3-TTS", "voice-c") in key_c
    assert "RouteModel" not in key_c
    assert "Qwen3-TTS-v2" not in key_a


def test_vllm_runtime_key_uses_provider_defaults_without_raw_runtime_config():
    class _CM:
        def get_core_config(self):
            return {
                "ENABLE_CUSTOM_API": True,
                "ttsModelProvider": "vllm_omni",
                "ttsModelUrl": "",
                "ttsModelId": "",
                "ttsVoiceId": "",
                "TTS_MODEL": "qwen3-tts-flash-realtime-2025-11-27",
                "TTS_VOICE_ID": "assistant-voice",
                "DISABLE_TTS": False,
            }

        def get_model_api_config(self, model_type):
            if model_type == "realtime":
                return {"base_url": ""}
            assert model_type == "tts_default"
            return {
                "base_url": "https://assist.invalid/v1",
                "model": "qwen3-tts-flash-realtime-2025-11-27",
                "api_key": "fallback-key",
            }

        def voice_id_exists_in_any_storage(self, voice_id):
            return False

    mgr = _make_mgr("")
    mgr._config_manager = _CM()
    key = LLMSessionManager._build_tts_runtime_key(mgr)

    assert ("ws://localhost:8091/v1", "Qwen3-TTS", "default") in key
    assert "assistant-voice" not in key


def test_vllm_tts_url_is_not_treated_as_local_voice_clone_server():
    tts_config = {
        "is_custom": True,
        "base_url": "ws://localhost:8091/v1/audio/speech/stream",
    }

    assert (
        characters_voice_providers._is_local_voice_clone_tts_config(
            tts_config,
            {"ttsModelProvider": "vllm_omni"},
        )
        is False
    )
    assert (
        characters_voice_providers._is_local_voice_clone_tts_config(
            tts_config,
            {"ttsModelProvider": "cosyvoice"},
        )
        is True
    )


def test_local_voice_clone_uses_tts_url_fallback_for_registration():
    tts_config = {
        "is_custom": True,
        "url": "ws://localhost:8091/v1/audio/speech/stream",
    }

    assert characters_voice_providers._is_local_voice_clone_tts_config(tts_config, {}) is True
    assert (
        characters_voice_providers._local_voice_clone_tts_base_url(tts_config, {})
        == "ws://localhost:8091/v1/audio/speech/stream"
    )


def test_local_voice_clone_uses_core_tts_url_fallback_for_registration():
    tts_config = {"is_custom": True}
    core_config = {"ttsModelUrl": "ws://localhost:8092/v1"}

    assert characters_voice_providers._is_local_voice_clone_tts_config(tts_config, core_config) is True
    assert characters_voice_providers._local_voice_clone_tts_base_url(tts_config, core_config) == "ws://localhost:8092/v1"


def test_has_custom_tts_ignores_disabled_gptsovits_placeholder():
    mgr = _make_mgr(
        "",
        core_config={
            "CORE_API_TYPE": "gemini",
            "GPTSOVITS_ENABLED": True,
            "TTS_VOICE_ID": "__gptsovits_disabled__|local",
        },
    )

    assert LLMSessionManager._has_custom_tts(mgr) is False


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


# ---------------------------------------------------------------------------
# PR #1764 review #3403710558 边界场景：_is_vllm_omni_tts_enabled 对 snapshot
# 中 ttsModelProvider 字段缺失/空串/大小写/livestream 优先级的容错性
# ---------------------------------------------------------------------------


def test_is_vllm_omni_tts_enabled_returns_false_when_ttsModelProvider_missing():
    """Backward compatibility: legacy core_config.json files lack the ttsModelProvider key."""
    snapshot = {
        "ENABLE_CUSTOM_API": True,
        "GPTSOVITS_ENABLED": False,
        # 故意不写 ttsModelProvider
    }
    assert LLMSessionManager._is_vllm_omni_tts_enabled(snapshot) is False


def test_is_vllm_omni_tts_enabled_returns_false_for_empty_string():
    """When the frontend clears the select, an empty string is written and must not trigger vllm_omni routing."""
    snapshot = {
        "ENABLE_CUSTOM_API": True,
        "ttsModelProvider": "",
        "GPTSOVITS_ENABLED": False,
    }
    assert LLMSessionManager._is_vllm_omni_tts_enabled(snapshot) is False


def test_is_vllm_omni_tts_enabled_parses_string_false():
    snapshot = {
        "ENABLE_CUSTOM_API": "false",
        "ttsModelProvider": "vllm_omni",
        "GPTSOVITS_ENABLED": False,
    }
    assert LLMSessionManager._is_vllm_omni_tts_enabled(snapshot) is False


def test_session_use_tts_ignores_stale_vllm_when_custom_api_string_false():
    mgr = _make_mgr("Puck")
    realtime_config = {"base_url": "https://generativelanguage.googleapis.com"}
    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr,
            "audio",
            realtime_config,
            {
                "ENABLE_CUSTOM_API": "false",
                "ttsModelProvider": "vllm_omni",
                "ttsVoiceId": "Puck",
                "GPTSOVITS_ENABLED": False,
            },
        )
        is False
    )


def test_is_vllm_omni_tts_enabled_is_case_sensitive():
    """The provider key comparison is case-sensitive: VLLM_OMNI / Vllm_Omni must not be recognised as vllm_omni."""
    for variant in ("VLLM_OMNI", "Vllm_Omni", "vLLM_omni"):
        snapshot = {
            "ENABLE_CUSTOM_API": True,
            "ttsModelProvider": variant,
            "GPTSOVITS_ENABLED": False,
        }
        assert LLMSessionManager._is_vllm_omni_tts_enabled(snapshot) is False, \
            f"variant={variant!r} 应返回 False（区分大小写）"


def test_is_vllm_omni_tts_enabled_strips_whitespace():
    """The frontend may persist values with surrounding whitespace; the helper must still recognise them."""
    snapshot = {
        "ENABLE_CUSTOM_API": True,
        "ttsModelProvider": "  vllm_omni  ",
        "GPTSOVITS_ENABLED": False,
    }
    assert LLMSessionManager._is_vllm_omni_tts_enabled(snapshot) is True


def test_livestream_overrides_vllm_omni_tts_routing(monkeypatch):
    """Livestream mode must short-circuit before the vllm_omni external-TTS branch is selected."""
    mgr = _make_mgr("Puck")
    monkeypatch.setattr(
        LLMSessionManager,
        "_is_livestream_active",
        lambda self: True,
    )
    realtime_config = {"base_url": "https://generativelanguage.googleapis.com"}
    snapshot = {
        "ENABLE_CUSTOM_API": True,
        "ttsModelProvider": "vllm_omni",
        "ttsVoiceId": "Puck",
        "GPTSOVITS_ENABLED": False,
    }
    # livestream 早退分支优先（core.py:3786 先于 vllm_omni 检测）
    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr, "audio", realtime_config, snapshot,
        )
        is False
    )
