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
        current_voice_id="",
        current_voice_meta=None,
        current_voice_is_stock=True,
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

def test_googletrans_not_warmed_in_china_region():
    assert _gate("translate_global", _cfg(china_region=True)) is False


def test_googletrans_warmed_in_other_regions():
    assert _gate("translate_global", _cfg(china_region=False)) is True


def test_googletrans_warmed_while_region_is_undecided():
    assert _gate("translate_global", _cfg(china_region=None)) is True


# --- CosyVoice clone (dashscope) --------------------------------------------

def test_cosyvoice_off_without_a_voice():
    assert _gate("cosyvoice_clone", _cfg(current_voice_id="")) is False


def test_cosyvoice_off_for_other_clone_providers():
    cfg = _cfg(current_voice_id="v-1", current_voice_meta={"provider": "minimax"}, current_voice_is_stock=False)
    assert _gate("cosyvoice_clone", cfg) is False


@pytest.mark.parametrize("provider", ["cosyvoice", "cosyvoice_intl"])
def test_cosyvoice_on_for_cosyvoice_clone(provider):
    cfg = _cfg(current_voice_id="v-1", current_voice_meta={"provider": provider})
    assert _gate("cosyvoice_clone", cfg) is True


def test_cosyvoice_on_for_custom_voice_without_stored_record():
    # get_tts_worker still sends such a voice to the CosyVoice worker.
    cfg = _cfg(current_voice_id="cosyvoice-v2-lost", current_voice_meta=None, current_voice_is_stock=False)
    assert _gate("cosyvoice_clone", cfg) is True


def test_cosyvoice_off_for_preset_or_native_voice_without_record():
    cfg = _cfg(current_voice_id="Cherry", current_voice_meta=None, current_voice_is_stock=True)
    assert _gate("cosyvoice_clone", cfg) is False


def test_cosyvoice_off_for_imported_voice_without_record():
    # An imported voice with no stored record gets the "unavailable" worker.
    cfg = _cfg(current_voice_id="voice_" + "a" * 32, current_voice_meta=None, current_voice_is_stock=False)
    assert _gate("cosyvoice_clone", cfg) is False


# --- proactive sources (bilibili_api) ---------------------------------------

def test_proactive_bilibili_off_when_sources_are_off():
    assert _gate("proactive_bilibili", _cfg()) is False


def test_proactive_bilibili_off_when_proactive_chat_is_off():
    settings = {key: True for key in _PROACTIVE_ALL_OFF}
    settings["proactiveChatEnabled"] = False
    assert _gate("proactive_bilibili", _cfg(conversation_settings=settings)) is False


def test_proactive_bilibili_on_for_unsaved_settings():
    # Keys never saved follow the frontend default, which the backend cannot see.
    assert _gate("proactive_bilibili", _cfg(conversation_settings={})) is True


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
        "translate_global": False,
        "cosyvoice_clone": False,
        "proactive_bilibili": False,
    }


def test_gate_that_cannot_read_config_falls_back_to_warming():
    def _unreadable(name):
        def read(self):
            raise OSError(f"cannot read {name}")
        return property(read)

    # Every config read the gates use fails, the way WarmupConfig's reads do.
    _Broken = type("_Broken", (), {
        name: _unreadable(name)
        for name in (
            "core_config", "realtime_config", "llm_slot_configs",
            "china_region", "current_voice_meta", "conversation_settings",
        )
    })

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
    assert results["proactive_bilibili"] is True
    assert results["anthropic"] is False
    assert results["gemini"] is False


def test_every_gated_entry_is_in_the_table_and_has_a_gate():
    assert set(_ENTRY_GATES) <= set(MAIN_SERVER_WARMUP)
    assert set(_ENTRY_GATES.values()) == set(_GATES)


def test_selection_keeps_ungated_entries_and_drops_closed_gates():
    closed = {name: False for name in _GATES}
    selected = select_warmup_entries(MAIN_SERVER_WARMUP, closed)
    assert selected == (
        "openai",
        "pyncm_async",
        "bs4",
        "config.prompts.prompts_directives:DIRECTIVE_PATTERNS",
    )

    opened = {name: True for name in _GATES}
    assert select_warmup_entries(MAIN_SERVER_WARMUP, opened) == MAIN_SERVER_WARMUP


@pytest.mark.parametrize("entry", ["pyncm_async", "bs4"])
def test_chat_song_request_libraries_are_always_warmed(entry):
    # /api/music/search and /api/music/play/netease import these on the event
    # loop for a song asked for in chat, whatever the proactive settings are.
    assert entry in MAIN_SERVER_WARMUP
    assert entry not in _ENTRY_GATES
    assert entry in select_warmup_entries(MAIN_SERVER_WARMUP, {name: False for name in _GATES})


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
    cfg = _warmup_config(cm)
    cfg.core_config = {}  # no custom-API voice to fall back to
    assert cfg.current_voice_meta is None


def test_current_voice_meta_resolves_imported_voice_refs():
    ref = "voice_" + "0" * 32
    cm = _FakeConfigManager(
        characters={"当前猫娘": "A", "猫娘": {"A": {"voice_id": ref}}},
        imported={ref: {"provider": "cosyvoice_intl"}},
    )
    assert _warmup_config(cm).current_voice_meta == {"provider": "cosyvoice_intl"}


def test_current_voice_is_stock_for_presets_and_native_voices(monkeypatch):
    from utils.tts import native_voice_registry as registry

    cm = _FakeConfigManager(characters={"当前猫娘": "A", "猫娘": {"A": {"voice_id": "v"}}})
    cm.voice_id_exists_in_any_storage = lambda voice_id: False
    cfg = _warmup_config(cm)
    cfg.core_config = {"CORE_API_TYPE": "qwen"}
    cfg.realtime_config = {"api_type": "qwen", "base_url": ""}

    monkeypatch.setattr(registry, "is_free_preset_voice_id", lambda voice_id: True)
    assert cfg.current_voice_is_stock is True

    cfg2 = _warmup_config(cm)
    cfg2.core_config, cfg2.realtime_config = cfg.core_config, cfg.realtime_config
    monkeypatch.setattr(registry, "is_free_preset_voice_id", lambda voice_id: False)
    monkeypatch.setattr(registry, "resolve_native_voice_for_routing", lambda *a, **k: (a[1], True))
    assert cfg2.current_voice_is_stock is True

    cfg3 = _warmup_config(cm)
    cfg3.core_config, cfg3.realtime_config = cfg.core_config, cfg.realtime_config
    monkeypatch.setattr(registry, "resolve_native_voice_for_routing", lambda *a, **k: (a[1], False))
    assert cfg3.current_voice_is_stock is False


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
    monkeypatch.setattr(
        module_warmup, "_spawn_gated_worker", lambda label, scope=None: spawned.append(label),
    )
    monkeypatch.setattr(module_warmup, "_gated_pending_scope", None)
    monkeypatch.setattr(module_warmup, "_startup_pass_missing", False)
    monkeypatch.setattr(module_warmup, "_failed_entries", set())
    return spawned


def test_rewarm_is_a_no_op_before_startup_warmup(warmup_state):
    # memory_server / agent_server in a split deployment write config too.
    assert module_warmup.request_config_rewarm("core_api") is False
    assert warmup_state == []


def _something_left_to_warm(monkeypatch):
    """Make the re-check see a module to import, whatever this process has loaded."""
    monkeypatch.setattr(module_warmup, "_unloaded_gates", lambda scope=None: {"anthropic"})


def test_rewarm_after_startup_warmup_finished_starts_a_worker(warmup_state, monkeypatch):
    _something_left_to_warm(monkeypatch)
    assert module_warmup.start_main_server_warmup() is True
    assert module_warmup.start_main_server_warmup() is False
    module_warmup._gated_running = False  # the startup pass finished
    assert module_warmup.request_config_rewarm("core_api") is True
    assert warmup_state == ["main", "config"]


def test_rewarm_during_a_running_pass_is_coalesced(warmup_state, monkeypatch):
    _something_left_to_warm(monkeypatch)
    assert module_warmup.start_main_server_warmup() is True
    assert module_warmup.request_config_rewarm("core_api") is False
    assert module_warmup._gated_pending is True
    assert warmup_state == ["main"]

    passes: list[bool] = []
    monkeypatch.setattr(
        module_warmup, "_pending_gated_entries",
        lambda skip_loaded, label, scope=None: passes.append(skip_loaded) or (),
    )
    module_warmup._run_gated_warmup("main")
    # Startup pass, then one more for the request that came in meanwhile.
    assert passes == [False, True]
    assert module_warmup._gated_running is False
    assert module_warmup._gated_pending is False


def test_rewarm_never_raises(warmup_state, monkeypatch):
    _something_left_to_warm(monkeypatch)
    module_warmup._gated_started = True
    tried: list[str] = []

    def _boom(label, scope=None):
        tried.append(label)
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module_warmup, "_spawn_gated_worker", _boom)
    assert module_warmup.request_config_rewarm("conversation_settings") is False
    assert tried == ["config"], "the spawn must actually have been attempted"
    assert module_warmup._gated_running is False


def test_failed_recheck_spawn_keeps_its_request_for_the_next_save(warmup_state, monkeypatch):
    _something_left_to_warm(monkeypatch)
    module_warmup._gated_started = True

    def _boom(label, scope=None):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module_warmup, "_spawn_gated_worker", _boom)
    module_warmup.request_config_rewarm("core_api")  # all gates
    assert module_warmup._gated_pending is True
    assert module_warmup._gated_pending_scope is None

    started: list = []
    monkeypatch.setattr(
        module_warmup, "_spawn_gated_worker", lambda label, scope=None: started.append(scope),
    )
    # A later, narrower save starts the worker; it carries the lost full request.
    assert module_warmup.request_config_rewarm("characters", only={"cosyvoice_clone"}) is True
    assert started == [None]
    assert module_warmup._gated_pending is False


def test_failed_recheck_spawn_merges_with_a_queued_request(warmup_state, monkeypatch):
    _something_left_to_warm(monkeypatch)
    module_warmup._gated_started = True

    def _boom(label, scope=None):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module_warmup, "_spawn_gated_worker", _boom)
    module_warmup.request_config_rewarm("characters", only={"cosyvoice_clone"})
    module_warmup.request_config_rewarm("conversation_settings", only={"proactive_bilibili"})
    assert module_warmup._gated_pending_scope == frozenset({"cosyvoice_clone", "proactive_bilibili"})


def test_config_rewarm_pass_skips_loaded_modules(monkeypatch):
    asked: list[set] = []

    def _all_open(cfg=None, only=None):
        asked.append(None if only is None else set(only))
        return {name: True for name in _GATES}

    monkeypatch.setattr(module_warmup, "evaluate_gates", _all_open)
    monkeypatch.setitem(sys.modules, "anthropic", object())
    entries = module_warmup._pending_gated_entries(True, "config")
    assert "anthropic" not in entries
    # Gated modules not yet loaded, plus ungated ones the startup pass missed.
    assert set(entries) <= set(_ENTRY_GATES) | set(module_warmup._unloaded_plain_entries())
    assert not any(":" in entry for entry in entries)
    assert "dashscope" in entries or "dashscope" in sys.modules
    # The anthropic gate is not even evaluated once its module is loaded.
    assert "anthropic" not in asked[-1]
    full = module_warmup._pending_gated_entries(False, "main")
    assert full == MAIN_SERVER_WARMUP


def test_evaluate_gates_only_reads_what_the_named_gates_need():
    class _OnlyVoice(SimpleNamespace):
        @property
        def llm_slot_configs(self):
            raise AssertionError("anthropic/gemini gates must not run")

    base = vars(_cfg(current_voice_id="v-1", current_voice_meta={"provider": "cosyvoice"})).copy()
    base.pop("llm_slot_configs")
    assert evaluate_gates(_OnlyVoice(**base), only={"cosyvoice_clone"}) == {"cosyvoice_clone": True}


def test_rewarm_is_skipped_once_every_gated_sdk_is_loaded(warmup_state, monkeypatch):
    module_warmup._gated_started = True
    for entry in list(_ENTRY_GATES) + module_warmup._unloaded_plain_entries():
        monkeypatch.setitem(sys.modules, entry, object())
    assert module_warmup.request_config_rewarm("characters") is False
    assert warmup_state == []


def test_scoped_rewarm_is_skipped_once_its_gates_are_loaded(warmup_state, monkeypatch):
    module_warmup._gated_started = True
    for entry in ["dashscope", "dashscope.audio.tts_v2"] + module_warmup._unloaded_plain_entries():
        monkeypatch.setitem(sys.modules, entry, object())
    # anthropic etc. may still be missing; a character save cannot change them.
    assert module_warmup.request_config_rewarm("characters", only={"cosyvoice_clone"}) is False
    assert warmup_state == []


def test_character_save_requests_a_recheck(tmp_path, monkeypatch):
    import threading

    from utils.config_manager.characters import CharactersMixin

    calls: list[tuple] = []
    monkeypatch.setattr(
        module_warmup, "request_config_rewarm",
        lambda reason, only=None: calls.append((reason, only)),
    )
    fake = SimpleNamespace(
        _characters_reload_lock=threading.RLock(),
        _characters_cache_lock=threading.RLock(),
        ensure_config_directory=lambda: None,
        _clear_characters_persist_backoff_locked=lambda: None,
    )
    path = tmp_path / "characters.json"
    CharactersMixin.save_characters(fake, {"猫娘": {}}, str(path), bypass_write_fence=True)
    assert path.exists()
    # A character save can only change the voice: only that gate is re-checked.
    assert calls == [("characters", {"cosyvoice_clone"})]


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


def test_failed_startup_spawn_leaves_rechecks_usable(warmup_state, monkeypatch):
    def _boom(label, scope=None):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module_warmup, "_spawn_gated_worker", _boom)
    assert module_warmup.start_main_server_warmup() is False
    assert module_warmup._gated_running is False
    spawned: list[str] = []
    monkeypatch.setattr(
        module_warmup, "_spawn_gated_worker", lambda label, scope=None: spawned.append(label),
    )
    assert module_warmup.request_config_rewarm("core_api") is True
    assert spawned == ["config"]


# --- review round 6 --------------------------------------------------------

class _VoiceConfigManager(_FakeConfigManager):
    def voice_id_exists_in_any_storage(self, voice_id):
        return False


def _voice_cfg(core, characters=None):
    cm = _VoiceConfigManager(characters=characters or {"当前猫娘": "A", "猫娘": {"A": {}}})
    cfg = _warmup_config(cm)
    cfg.core_config = core
    cfg.realtime_config = {"api_type": core.get("CORE_API_TYPE", ""), "base_url": ""}
    return cfg


def test_voice_falls_back_to_custom_api_voice_like_start_session():
    cfg = _voice_cfg({"TTS_VOICE_ID": "cosyvoice-v2-x", "ENABLE_CUSTOM_API": True})
    assert cfg.current_voice_id == "cosyvoice-v2-x"
    cfg = _voice_cfg({"TTS_VOICE_ID": "cosyvoice-v2-x", "GPTSOVITS_ENABLED": True})
    assert cfg.current_voice_id == "cosyvoice-v2-x"


@pytest.mark.parametrize("core", [
    {"TTS_VOICE_ID": "cosyvoice-v2-x", "ENABLE_CUSTOM_API": False},
    {"TTS_VOICE_ID": "", "ENABLE_CUSTOM_API": True},
    {"TTS_VOICE_ID": "__gptsovits_disabled__|x", "ENABLE_CUSTOM_API": True},
])
def test_no_custom_api_voice_fallback_when_start_session_would_not_use_it(core):
    assert _voice_cfg(core).current_voice_id == ""


def test_card_voice_wins_over_custom_api_voice():
    cfg = _voice_cfg(
        {"TTS_VOICE_ID": "cosyvoice-v2-x", "ENABLE_CUSTOM_API": True},
        characters={"当前猫娘": "A", "猫娘": {"A": {"voice_id": "card-voice"}}},
    )
    assert cfg.current_voice_id == "card-voice"


def test_custom_api_cosyvoice_voice_warms_dashscope():
    cfg = _voice_cfg({"TTS_VOICE_ID": "cosyvoice-v2-x", "ENABLE_CUSTOM_API": True, "CORE_API_TYPE": "qwen"})
    cfg._cm._voices = {"cosyvoice-v2-x": {"provider": "cosyvoice"}}
    assert evaluate_gates(cfg, only={"cosyvoice_clone"}) == {"cosyvoice_clone": True}


def test_real_stock_voice_check_never_imports_gated_sdks():
    gated = {entry.partition(":")[0] for entry in _ENTRY_GATES}
    before = set(sys.modules)
    cfg = _voice_cfg(
        {"CORE_API_TYPE": "qwen"},
        characters={"当前猫娘": "A", "猫娘": {"A": {"voice_id": "custom-voice-without-record"}}},
    )
    evaluate_gates(cfg, only={"cosyvoice_clone"})
    assert not ((set(sys.modules) - before) & gated)


def test_scoped_recheck_evaluates_only_its_gates(monkeypatch):
    asked = []

    def _record(cfg=None, only=None):
        asked.append(set(only))
        return {name: True for name in only}

    monkeypatch.setattr(module_warmup, "evaluate_gates", _record)
    entries = module_warmup._pending_gated_entries(True, "config", frozenset({"cosyvoice_clone"}))
    assert asked[-1] <= {"cosyvoice_clone"}
    assert all(_ENTRY_GATES.get(e) in (None, "cosyvoice_clone") for e in entries)


def test_recheck_retries_ungated_modules_the_startup_pass_missed(monkeypatch):
    monkeypatch.setattr(module_warmup, "evaluate_gates", lambda cfg=None, only=None: {})
    monkeypatch.delitem(sys.modules, "openai", raising=False)
    assert "openai" in module_warmup._pending_gated_entries(True, "config")


def test_pending_scopes_merge_and_widen_to_all(warmup_state, monkeypatch):
    # Independent of what other tests in this process happened to import.
    monkeypatch.setattr(module_warmup, "_unloaded_gates", lambda scope=None: {"x"})
    module_warmup._gated_started = True
    module_warmup._gated_running = True
    module_warmup.request_config_rewarm("characters", only={"cosyvoice_clone"})
    assert module_warmup._gated_pending_scope == frozenset({"cosyvoice_clone"})
    module_warmup.request_config_rewarm("conversation_settings", only={"proactive_bilibili"})
    assert module_warmup._gated_pending_scope == frozenset({"cosyvoice_clone", "proactive_bilibili"})
    module_warmup.request_config_rewarm("core_api")
    assert module_warmup._gated_pending_scope is None


# --- review round 7 --------------------------------------------------------

@pytest.mark.parametrize("core_api", ["qwen", "openai"])
def test_free_preset_on_card_is_dropped_outside_free_mode(monkeypatch, core_api):
    from utils.tts import native_voice_registry as registry

    monkeypatch.setattr(registry, "is_free_preset_voice_id", lambda voice_id: voice_id == "yui-preset")
    cfg = _voice_cfg(
        {"CORE_API_TYPE": core_api, "TTS_VOICE_ID": "cosyvoice-v2-x", "ENABLE_CUSTOM_API": True},
        characters={"当前猫娘": "A", "猫娘": {"A": {"voice_id": "yui-preset"}}},
    )
    # Like _apply_voice_id_for_route: the preset is cleared, then TTS_VOICE_ID applies.
    assert cfg.current_voice_id == "cosyvoice-v2-x"
    cfg._cm._voices = {"cosyvoice-v2-x": {"provider": "cosyvoice"}}
    assert evaluate_gates(cfg, only={"cosyvoice_clone"}) == {"cosyvoice_clone": True}


def test_free_preset_on_card_stays_in_free_mode(monkeypatch):
    from utils.tts import native_voice_registry as registry

    monkeypatch.setattr(registry, "is_free_preset_voice_id", lambda voice_id: voice_id == "yui-preset")
    cfg = _voice_cfg(
        {"CORE_API_TYPE": "free", "TTS_VOICE_ID": "cosyvoice-v2-x", "ENABLE_CUSTOM_API": True},
        characters={"当前猫娘": "A", "猫娘": {"A": {"voice_id": "yui-preset"}}},
    )
    assert cfg.current_voice_id == "yui-preset"
    assert evaluate_gates(cfg, only={"cosyvoice_clone"}) == {"cosyvoice_clone": False}


def test_core_api_type_prefers_the_realtime_api_type():
    cfg = _voice_cfg({"CORE_API_TYPE": "qwen"})
    cfg.realtime_config = {"api_type": "free"}
    assert cfg.core_api_type == "free"


def test_failed_imports_are_not_retried_on_every_save(warmup_state, monkeypatch):
    module_warmup._gated_started = True
    loaded_or_failed = set(_ENTRY_GATES) | set(module_warmup._unloaded_plain_entries())
    broken = "pyncm_async"
    for entry in loaded_or_failed - {broken}:
        monkeypatch.setitem(sys.modules, entry, object())
    monkeypatch.delitem(sys.modules, broken, raising=False)

    def _fail(name):
        raise ImportError(name)

    monkeypatch.setattr(module_warmup, "_warm_one", _fail)
    module_warmup._warm_entries((broken,), "config")
    assert broken in module_warmup._failed_entries
    assert module_warmup.request_config_rewarm("characters") is False
    assert warmup_state == []


def test_first_recheck_after_failed_startup_is_full_scope(warmup_state, monkeypatch):
    scopes: list = []

    def _boom(label, scope=None):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module_warmup, "_spawn_gated_worker", _boom)
    assert module_warmup.start_main_server_warmup() is False
    assert module_warmup._startup_pass_missing is True
    monkeypatch.setattr(
        module_warmup, "_spawn_gated_worker", lambda label, scope=None: scopes.append(scope),
    )
    # Even with every gated SDK loaded, the missing startup pass still has to run.
    for entry in list(_ENTRY_GATES) + module_warmup._unloaded_plain_entries():
        monkeypatch.setitem(sys.modules, entry, object())
    assert module_warmup.request_config_rewarm("characters", only={"cosyvoice_clone"}) is True
    assert scopes == [None]


def test_missing_startup_pass_retries_attribute_entries(monkeypatch):
    monkeypatch.setattr(module_warmup, "evaluate_gates", lambda cfg=None, only=None: {})
    monkeypatch.setattr(module_warmup, "_startup_pass_missing", True)
    entries = module_warmup._pending_gated_entries(True, "config")
    assert "config.prompts.prompts_directives:DIRECTIVE_PATTERNS" in entries


def test_full_recheck_clears_the_missing_startup_flag(monkeypatch):
    monkeypatch.setattr(module_warmup, "_startup_pass_missing", True)
    monkeypatch.setattr(module_warmup, "_gated_running", True)
    monkeypatch.setattr(module_warmup, "_gated_pending", False)
    monkeypatch.setattr(module_warmup, "_pending_gated_entries", lambda *a, **k: ())
    module_warmup._run_gated_warmup("config", None)
    assert module_warmup._startup_pass_missing is False


def test_proactive_settings_save_rechecks_only_bilibili(monkeypatch, tmp_path):
    import json as _json

    from utils import preferences

    class _Cm:
        def __init__(self, path):
            self.path = path

        def ensure_config_directory(self):
            pass

        def get_runtime_config_path(self, _name):
            return self.path

        def get_config_path(self, _name):
            return self.path

    path = tmp_path / "user_preferences.json"
    path.write_text(_json.dumps([]), encoding="utf-8")
    monkeypatch.setattr(preferences, "_config_manager", _Cm(path))
    monkeypatch.setattr(preferences, "PREFERENCES_FILE", str(path))
    monkeypatch.setattr(preferences, "assert_cloudsave_writable", lambda *a, **k: None)
    calls: list = []
    monkeypatch.setattr(
        module_warmup, "request_config_rewarm",
        lambda reason, only=None: calls.append((reason, only)),
    )
    result = preferences.save_global_conversation_settings_versioned({"proactiveVideoChatEnabled": True})
    assert result.success is True
    assert calls == [("conversation_settings", {"proactive_bilibili"})]


def test_failed_recheck_spawn_keeps_a_request_queued_while_it_was_starting(warmup_state, monkeypatch):
    _something_left_to_warm(monkeypatch)
    module_warmup._gated_started = True

    def _boom_after_a_concurrent_save(label, scope=None):
        # Another save arrives while this thread is being started: it is queued.
        module_warmup.request_config_rewarm("conversation_settings", only={"proactive_bilibili"})
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module_warmup, "_spawn_gated_worker", _boom_after_a_concurrent_save)
    module_warmup.request_config_rewarm("characters", only={"cosyvoice_clone"})
    assert module_warmup._gated_pending is True
    assert module_warmup._gated_pending_scope == frozenset({"cosyvoice_clone", "proactive_bilibili"})


def test_a_kept_request_is_not_skipped_by_a_narrow_save_with_nothing_of_its_own(warmup_state, monkeypatch):
    # Only anthropic is missing; a character save (voice only) has nothing of its own to add.
    monkeypatch.setattr(
        module_warmup, "_unloaded_gates",
        lambda scope=None: {"anthropic"} if scope is None or "anthropic" in scope else set(),
    )
    monkeypatch.setattr(module_warmup, "_unloaded_plain_entries", lambda: [])
    module_warmup._gated_started = True

    def _boom(label, scope=None):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module_warmup, "_spawn_gated_worker", _boom)
    module_warmup.request_config_rewarm("core_api")  # kept: a full re-check
    started: list = []
    monkeypatch.setattr(
        module_warmup, "_spawn_gated_worker", lambda label, scope=None: started.append(scope),
    )
    assert module_warmup.request_config_rewarm("characters", only={"cosyvoice_clone"}) is True
    assert started == [None], "the kept full re-check must run, anthropic is still missing"
