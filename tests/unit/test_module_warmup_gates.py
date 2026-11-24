# -*- coding: utf-8 -*-
"""Config gates of the main_server background warmup (utils/module_warmup.py).

Every heavy SDK stays resident once imported, so the warmup only imports the
ones the saved configuration can reach. These tests feed the gates a fake
config snapshot: nothing here reads the real data directory, and nothing here
may import the gated SDKs themselves (checked by
``test_gates_never_import_the_sdks_they_gate``).

Mutation check: forcing any gate to always return True or always False must
turn at least one test in this file red.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from utils import module_warmup
from utils.module_warmup import (
    MAIN_SERVER_WARMUP,
    _ENTRY_GATES,
    _GATES,
    evaluate_gates,
    select_warmup_entries,
)

pytestmark = pytest.mark.unit

_OPENAI_SLOT = {"base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "provider_type": "openai_compatible"}
_PROACTIVE_ALL_OFF = {
    "proactiveChatEnabled": True,
    "proactiveNewsChatEnabled": False,
    "proactiveVideoChatEnabled": False,
    "proactivePersonalChatEnabled": False,
    "proactiveMusicEnabled": False,
    "proactiveMemeEnabled": False,
    "proactiveVisionChatEnabled": False,
}


def _cfg(**overrides):
    """A config snapshot where every gate is off (China region decided)."""
    base = dict(
        core_config={"CORE_API_TYPE": "qwen"},
        realtime_config={"api_type": "qwen"},
        llm_slot_configs=(_OPENAI_SLOT,),
        china_region=True,
        current_voice_meta=None,
        conversation_settings=dict(_PROACTIVE_ALL_OFF),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _gate(name, cfg):
    return _GATES[name](cfg)


# --- anthropic -------------------------------------------------------------

def test_anthropic_off_for_openai_compatible_slots():
    assert _gate("anthropic", _cfg()) is False


@pytest.mark.parametrize("slot", [
    {"base_url": "https://proxy.example/v1", "provider_type": "anthropic"},
    {"base_url": "https://api.anthropic.com/v1", "provider_type": "openai_compatible"},
    {"base_url": "https://api.kimi.com/coding", "provider_type": None},
])
def test_anthropic_on_when_any_slot_speaks_messages_api(slot):
    assert _gate("anthropic", _cfg(llm_slot_configs=(_OPENAI_SLOT, slot))) is True


# --- gemini ----------------------------------------------------------------

def test_gemini_off_without_gemini_anywhere():
    assert _gate("gemini", _cfg()) is False


def test_gemini_off_for_gemini_model_behind_a_proxy():
    # A gemini model name behind an OpenAI-compatible URL stays on the openai SDK.
    slot = {"base_url": "https://openrouter.ai/api/v1", "model": "google/gemini-2.5-flash"}
    assert _gate("gemini", _cfg(llm_slot_configs=(slot,))) is False


@pytest.mark.parametrize("overrides", [
    {"core_config": {"CORE_API_TYPE": "gemini"}},
    {"realtime_config": {"api_type": "gemini"}},
    {"llm_slot_configs": ({"base_url": "https://generativelanguage.googleapis.com/v1beta", "model": "x"},)},
    {"llm_slot_configs": ({"base_url": "", "model": "gemini-2.5-pro"},)},
])
def test_gemini_on_for_core_realtime_or_native_slot(overrides):
    assert _gate("gemini", _cfg(**overrides)) is True


# --- subtitle translation --------------------------------------------------

def test_translation_china_region_warms_translatepy_only():
    cfg = _cfg(china_region=True)
    assert _gate("translate_china", cfg) is True
    assert _gate("translate_global", cfg) is False


def test_translation_other_regions_warm_googletrans_only():
    cfg = _cfg(china_region=False)
    assert _gate("translate_china", cfg) is False
    assert _gate("translate_global", cfg) is True


def test_translation_undecided_region_warms_both():
    cfg = _cfg(china_region=None)
    assert _gate("translate_china", cfg) is True
    assert _gate("translate_global", cfg) is True


# --- CosyVoice clone (dashscope) --------------------------------------------

def test_cosyvoice_off_without_a_stored_voice():
    assert _gate("cosyvoice_clone", _cfg(current_voice_meta=None)) is False


def test_cosyvoice_off_for_other_clone_providers():
    assert _gate("cosyvoice_clone", _cfg(current_voice_meta={"provider": "minimax"})) is False


@pytest.mark.parametrize("provider", ["cosyvoice", "cosyvoice_intl"])
def test_cosyvoice_on_for_cosyvoice_clone(provider):
    assert _gate("cosyvoice_clone", _cfg(current_voice_meta={"provider": provider})) is True


# --- proactive sources -----------------------------------------------------

@pytest.mark.parametrize("gate", ["proactive_music", "proactive_web", "proactive_bilibili"])
def test_proactive_gates_off_when_sources_are_off(gate):
    assert _gate(gate, _cfg()) is False


@pytest.mark.parametrize("gate", ["proactive_music", "proactive_web", "proactive_bilibili"])
def test_proactive_gates_off_when_proactive_chat_is_off(gate):
    settings = {key: True for key in _PROACTIVE_ALL_OFF}
    settings["proactiveChatEnabled"] = False
    assert _gate(gate, _cfg(conversation_settings=settings)) is False


@pytest.mark.parametrize("gate", ["proactive_music", "proactive_web", "proactive_bilibili"])
def test_proactive_gates_on_for_unsaved_settings(gate):
    # Keys never saved follow the frontend default, which the backend cannot see.
    assert _gate(gate, _cfg(conversation_settings={})) is True


def test_proactive_music_on_with_music_source():
    settings = {**_PROACTIVE_ALL_OFF, "proactiveMusicEnabled": True}
    assert _gate("proactive_music", _cfg(conversation_settings=settings)) is True


@pytest.mark.parametrize("source", [
    "proactiveNewsChatEnabled",
    "proactiveVideoChatEnabled",
    "proactivePersonalChatEnabled",
    "proactiveMusicEnabled",
    "proactiveMemeEnabled",
    "proactiveVisionChatEnabled",
])
def test_proactive_web_on_with_any_scraping_source(source):
    settings = {**_PROACTIVE_ALL_OFF, source: True}
    assert _gate("proactive_web", _cfg(conversation_settings=settings)) is True


@pytest.mark.parametrize("source", ["proactiveVideoChatEnabled", "proactivePersonalChatEnabled"])
def test_proactive_bilibili_on_with_video_or_personal_in_china(source):
    settings = {**_PROACTIVE_ALL_OFF, source: True}
    assert _gate("proactive_bilibili", _cfg(conversation_settings=settings)) is True
    assert _gate(
        "proactive_bilibili", _cfg(conversation_settings=settings, china_region=None)
    ) is True


def test_proactive_bilibili_off_outside_china():
    settings = {**_PROACTIVE_ALL_OFF, "proactiveVideoChatEnabled": True}
    assert _gate(
        "proactive_bilibili", _cfg(conversation_settings=settings, china_region=False)
    ) is False


def test_proactive_bilibili_off_for_news_only():
    settings = {**_PROACTIVE_ALL_OFF, "proactiveNewsChatEnabled": True}
    assert _gate("proactive_bilibili", _cfg(conversation_settings=settings)) is False


# --- evaluation and selection ----------------------------------------------

def test_all_gates_off_for_minimal_config():
    assert evaluate_gates(_cfg()) == {
        "anthropic": False,
        "gemini": False,
        "translate_china": True,
        "translate_global": False,
        "cosyvoice_clone": False,
        "proactive_music": False,
        "proactive_web": False,
        "proactive_bilibili": False,
    }


def test_gate_that_cannot_read_config_falls_back_to_warming():
    class _Broken:
        def __getattr__(self, name):
            raise OSError(f"cannot read {name}")

    results = evaluate_gates(_Broken())
    assert set(results) == set(_GATES)
    assert all(results.values()), results


def test_one_failing_read_does_not_turn_other_gates_on():
    class _NoSettings(SimpleNamespace):
        @property
        def conversation_settings(self):
            raise ValueError("user_preferences.json is corrupt")

    base = vars(_cfg()).copy()
    base.pop("conversation_settings")
    results = evaluate_gates(_NoSettings(**base))
    assert results["proactive_music"] is True
    assert results["proactive_web"] is True
    assert results["proactive_bilibili"] is True
    assert results["anthropic"] is False
    assert results["gemini"] is False


def test_every_gated_entry_is_in_the_table_and_has_a_gate():
    assert set(_ENTRY_GATES) <= set(MAIN_SERVER_WARMUP)
    assert set(_ENTRY_GATES.values()) == set(_GATES)


def test_selection_keeps_ungated_entries_and_drops_closed_gates():
    closed = {name: False for name in _GATES}
    selected = select_warmup_entries(MAIN_SERVER_WARMUP, closed)
    assert selected == ("openai", "config.prompts.prompts_directives:DIRECTIVE_PATTERNS")

    opened = {name: True for name in _GATES}
    assert select_warmup_entries(MAIN_SERVER_WARMUP, opened) == MAIN_SERVER_WARMUP


def test_selection_without_a_result_keeps_the_entry():
    assert select_warmup_entries(("anthropic",), {}) == ("anthropic",)


def test_gates_never_import_the_sdks_they_gate():
    gated = {entry.partition(":")[0] for entry in _ENTRY_GATES}
    before = set(sys.modules)
    evaluate_gates(_cfg(
        llm_slot_configs=({"base_url": "https://api.anthropic.com", "provider_type": "anthropic"},),
        current_voice_meta={"provider": "cosyvoice"},
        conversation_settings={},
    ))
    newly_loaded = (set(sys.modules) - before) & gated
    assert not newly_loaded, f"判定过程不该导入被它判定的重型库：{sorted(newly_loaded)}"


# --- WarmupConfig reads ------------------------------------------------------

class _FakeConfigManager:
    def __init__(self, characters, voices=None, imported=None):
        self._characters = characters
        self._voices = voices or {}
        self._imported = imported or {}

    def load_characters(self):
        return self._characters

    def get_voices_for_current_api(self):
        return self._voices

    def get_imported_voice(self, local_ref):
        return self._imported.get(local_ref)


def _warmup_config(cm):
    cfg = module_warmup.WarmupConfig()
    cfg._cm = cm  # cached_property: an instance attribute takes its place
    return cfg


def test_current_voice_meta_reads_the_current_characters_clone():
    cm = _FakeConfigManager(
        characters={"当前猫娘": "A", "猫娘": {"A": {"voice_id": "cosyvoice-v2-abc"}, "B": {}}},
        voices={"cosyvoice-v2-abc": {"provider": "cosyvoice"}},
    )
    assert _warmup_config(cm).current_voice_meta == {"provider": "cosyvoice"}


def test_current_voice_meta_ignores_other_characters():
    cm = _FakeConfigManager(
        characters={"当前猫娘": "B", "猫娘": {"A": {"voice_id": "cosyvoice-v2-abc"}, "B": {}}},
        voices={"cosyvoice-v2-abc": {"provider": "cosyvoice"}},
    )
    assert _warmup_config(cm).current_voice_meta is None


def test_current_voice_meta_resolves_imported_voice_refs():
    ref = "voice_" + "0" * 32
    cm = _FakeConfigManager(
        characters={"当前猫娘": "A", "猫娘": {"A": {"voice_id": ref}}},
        imported={ref: {"provider": "cosyvoice_intl"}},
    )
    assert _warmup_config(cm).current_voice_meta == {"provider": "cosyvoice_intl"}


def test_china_region_is_none_until_the_probe_is_conclusive(monkeypatch):
    from utils import language_utils

    monkeypatch.setattr(language_utils, "_detect_china_region", lambda: None)
    monkeypatch.setattr(language_utils, "is_china_region", lambda: False)
    assert module_warmup.WarmupConfig().china_region is None

    monkeypatch.setattr(language_utils, "_detect_china_region", lambda: True)
    monkeypatch.setattr(language_utils, "is_china_region", lambda: True)
    assert module_warmup.WarmupConfig().china_region is True


# --- re-check after config saves ---------------------------------------------

@pytest.fixture
def warmup_state(monkeypatch):
    """Isolate the module's worker flags and record spawns instead of starting threads."""
    monkeypatch.setattr(module_warmup, "_under_pytest", lambda: False)
    monkeypatch.setattr(module_warmup, "_gated_started", False)
    monkeypatch.setattr(module_warmup, "_gated_running", False)
    monkeypatch.setattr(module_warmup, "_gated_pending", False)
    spawned: list[str] = []
    monkeypatch.setattr(module_warmup, "_spawn_gated_worker", spawned.append)
    return spawned


def test_rewarm_is_a_no_op_before_startup_warmup(warmup_state):
    # memory_server / agent_server in a split deployment write config too.
    assert module_warmup.request_config_rewarm("core_api") is False
    assert warmup_state == []


def test_rewarm_after_startup_warmup_finished_starts_a_worker(warmup_state):
    assert module_warmup.start_main_server_warmup() is True
    assert module_warmup.start_main_server_warmup() is False
    module_warmup._gated_running = False  # the startup pass finished
    assert module_warmup.request_config_rewarm("core_api") is True
    assert warmup_state == ["main", "config"]


def test_rewarm_during_a_running_pass_is_coalesced(warmup_state, monkeypatch):
    assert module_warmup.start_main_server_warmup() is True
    assert module_warmup.request_config_rewarm("core_api") is False
    assert module_warmup._gated_pending is True
    assert warmup_state == ["main"]

    passes: list[bool] = []
    monkeypatch.setattr(
        module_warmup, "_pending_gated_entries",
        lambda skip_loaded, label: passes.append(skip_loaded) or (),
    )
    module_warmup._run_gated_warmup("main")
    # Startup pass, then one more for the request that came in meanwhile.
    assert passes == [False, True]
    assert module_warmup._gated_running is False
    assert module_warmup._gated_pending is False


def test_rewarm_never_raises(warmup_state, monkeypatch):
    module_warmup._gated_started = True

    def _boom(label):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module_warmup, "_spawn_gated_worker", _boom)
    assert module_warmup.request_config_rewarm("conversation_settings") is False
    assert module_warmup._gated_running is False


def test_config_rewarm_pass_skips_loaded_modules(monkeypatch):
    monkeypatch.setattr(module_warmup, "evaluate_gates", lambda cfg=None: {name: True for name in _GATES})
    monkeypatch.setitem(sys.modules, "anthropic", object())
    entries = module_warmup._pending_gated_entries(True, "config")
    assert "anthropic" not in entries
    assert "config.prompts.prompts_directives:DIRECTIVE_PATTERNS" in entries
    full = module_warmup._pending_gated_entries(False, "main")
    assert full == MAIN_SERVER_WARMUP


class _CoreConfigManager:
    """Answers like ConfigManager.get_core_config: defaults when the file is unusable."""

    def __init__(self, path):
        self._path = path

    def get_config_path(self, name):
        assert name == "core_config.json"
        return str(self._path)

    def get_core_config(self):
        return {"CORE_API_TYPE": "qwen"}

    def get_model_api_config(self, slot, _core_config=None):
        return dict(_OPENAI_SLOT)


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_corrupt_core_config_counts_as_a_failed_read(tmp_path, content):
    path = tmp_path / "core_config.json"
    path.write_text(content, encoding="utf-8")
    cfg = _warmup_config(_CoreConfigManager(path))
    results = evaluate_gates(cfg)
    assert results["anthropic"] is True
    assert results["gemini"] is True


@pytest.mark.parametrize("content", [None, '{"CORE_API_TYPE": "qwen"}'])
def test_missing_or_valid_core_config_is_read_normally(tmp_path, content):
    path = tmp_path / "core_config.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    cfg = _warmup_config(_CoreConfigManager(path))
    results = evaluate_gates(cfg)
    assert results["anthropic"] is False
    assert results["gemini"] is False
