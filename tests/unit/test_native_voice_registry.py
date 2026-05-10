"""Provider-agnostic invariants of utils.native_voice_registry.

Tests use a synthetic provider so they keep passing even if the Gemini
catalog changes; Gemini-specific behavior is covered in
test_gemini_tts_voices.py.
"""
import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.native_voice_registry import (
    NativeVoiceProvider,
    get_active_realtime_native_provider,
    get_native_tts_worker,
    get_native_voice_catalog_for_ui,
    get_provider,
    is_native_voice,
    normalize_native_voice,
    register_provider,
    register_tts_worker_resolver,
    resolve_native_voice_for_routing,
)


_SYNTHETIC_PROVIDER = NativeVoiceProvider(
    key="__test_synth__",
    catalog={"Alpha": "Female", "Beta": "Male"},
    aliases={"a": "Alpha", "b": "Beta", "女": "Alpha"},
    default_voice="Alpha",
    default_male_voice="Beta",
    catalog_prefix="Synth",
)


@pytest.fixture(autouse=True)
def _register_synthetic():
    register_provider(_SYNTHETIC_PROVIDER)
    yield
    # 不显式 deregister —— 注册表设计为幂等覆盖，重跑 fixture 即可重置；
    # 同时其他测试不会依赖 __test_synth__ 不存在。


def test_is_native_voice_per_provider():
    assert is_native_voice("Alpha", provider_key="__test_synth__") is True
    assert is_native_voice("alpha", provider_key="__test_synth__") is True
    assert is_native_voice("女", provider_key="__test_synth__") is True
    assert is_native_voice("Puck", provider_key="__test_synth__") is False


def test_is_native_voice_across_any_provider():
    """无 provider_key 时跨注册表查询，至少能命中合成 provider 与 Gemini。"""
    assert is_native_voice("Alpha") is True
    assert is_native_voice("Puck") is True  # Gemini 在 import 时注册
    assert is_native_voice("definitely-not-a-voice-id") is False


def test_normalize_unknown_provider_raises():
    with pytest.raises(KeyError):
        normalize_native_voice("__nope__", "Alpha")


def test_get_native_voice_catalog_for_ui_returns_none_for_unknown():
    assert get_native_voice_catalog_for_ui(None) is None
    assert get_native_voice_catalog_for_ui("__nope__") is None


def test_get_native_voice_catalog_for_ui_shape():
    catalog = get_native_voice_catalog_for_ui("__test_synth__")
    assert catalog is not None
    assert set(catalog.keys()) == {"Alpha", "Beta"}
    for name, meta in catalog.items():
        assert meta["provider"] == "__test_synth__"
        assert meta["builtin"] is True
        assert "Synth" in meta["prefix"]
        assert name in meta["prefix"]


def test_resolve_for_routing_unknown_core_returns_no_native():
    """core_api_type 不在注册表里时 use_native=False，调用方走 custom TTS。"""
    voice, use_native = resolve_native_voice_for_routing("nonexistent", "Alpha", None)
    assert use_native is False
    assert voice == "Alpha"


def test_resolve_for_routing_unrecognized_voice_preserves_original_input():
    """provider 命中但 voice 不在 catalog 时返回原始输入（strip 后），
    与 unknown-core 分支对偶；不能把用户输入悄悄替换成 default_voice，否则
    将来 caller 想在 use_native=False 分支用返回值时会被误导喵。"""
    voice, use_native = resolve_native_voice_for_routing(
        "__test_synth__",
        "  my_custom_clone  ",
        None,
    )
    assert (voice, use_native) == ("my_custom_clone", False)


def test_resolve_for_routing_empty_input_preserves_emptiness():
    voice, use_native = resolve_native_voice_for_routing("__test_synth__", "", None)
    assert (voice, use_native) == ("", False)
    voice, use_native = resolve_native_voice_for_routing("__test_synth__", None, None)
    assert (voice, use_native) == ("", False)


def test_resolve_for_routing_collision_disables_native():
    """同名克隆 voice 应该把 native routing 让给 custom TTS。"""
    stored = {"alpha"}
    voice, use_native = resolve_native_voice_for_routing(
        "__test_synth__",
        "a",  # alias → Alpha
        lambda vid: vid.casefold() in stored,
    )
    assert voice == "Alpha"
    assert use_native is False


def test_resolve_for_routing_no_collision_uses_native():
    voice, use_native = resolve_native_voice_for_routing(
        "__test_synth__",
        "a",
        lambda vid: False,
    )
    assert (voice, use_native) == ("Alpha", True)


def test_active_realtime_uses_realtime_config_first():
    class _CM:
        def get_model_api_config(self, model_type):
            assert model_type == "realtime"
            return {"api_type": "__test_synth__"}

        def get_core_config(self):
            return {"CORE_API_TYPE": "gemini"}

    assert get_active_realtime_native_provider(_CM()) == "__test_synth__"


def test_active_realtime_falls_back_to_core_when_realtime_unavailable():
    class _CM:
        def get_model_api_config(self, model_type):
            raise RuntimeError("no realtime config")

        def get_core_config(self):
            return {"CORE_API_TYPE": "__test_synth__"}

    assert get_active_realtime_native_provider(_CM()) == "__test_synth__"


def test_active_realtime_returns_none_for_unregistered_provider():
    class _CM:
        def get_model_api_config(self, model_type):
            return {"api_type": "some_other_provider"}

        def get_core_config(self):
            return {"CORE_API_TYPE": "some_other_provider"}

    assert get_active_realtime_native_provider(_CM()) is None


def test_get_native_tts_worker_requires_voice_match_and_resolver():
    """worker resolver 注册前 → None；voice 不在 catalog → None；都满足才返回 tuple。"""

    class _CM:
        def get_core_config(self):
            return {"CORE_API_KEY": "synthetic-key"}

    cm = _CM()

    # voice 不在 catalog
    assert get_native_tts_worker("__test_synth__", cm, "not-a-voice") is None
    # 还没注册 resolver
    assert get_native_tts_worker("__test_synth__", cm, "Alpha") is None

    sentinel_worker = object()

    def _resolver(cm):
        return sentinel_worker, cm.get_core_config().get("CORE_API_KEY", "")

    register_tts_worker_resolver("__test_synth__", _resolver)
    result = get_native_tts_worker("__test_synth__", cm, "Alpha")
    assert result == (sentinel_worker, "synthetic-key", "__test_synth__")

    # core_api_type 不匹配 provider → None（即使 voice 同名）
    assert get_native_tts_worker("nonexistent", cm, "Alpha") is None


def test_get_provider_returns_none_for_falsy_key():
    assert get_provider(None) is None
    assert get_provider("") is None
    assert get_provider("__test_synth__") is _SYNTHETIC_PROVIDER


def test_builtin_providers_auto_loaded_on_registry_import():
    """Importing the registry alone (no explicit provider import) must give a
    populated registry — otherwise cross-cutting code that runs before any
    TTS/realtime client has loaded a provider would query an empty list and
    silently fall through to external TTS routing.

    Spawn a fresh subprocess so we observe a clean Python startup, not the
    state already polluted by previous imports in this test session.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from utils.native_voice_registry import "
                "list_providers, is_native_voice; "
                "providers = list_providers(); "
                "assert 'gemini' in providers, providers; "
                "assert is_native_voice('Puck') is True; "
                "print('OK')"
            ),
        ],
        capture_output=True,
        text=True,
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")),
    )
    assert result.returncode == 0, (
        f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert result.stdout.strip() == "OK"
