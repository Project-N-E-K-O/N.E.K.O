"""Automatic remapping of deprecated free YUI preset voices (PR: voice-tone-R6NtLH3Hk0 → active domestic yui_cn).

Regression target: after the default YUI voice ID changed, old tone IDs left in existing users' characters.json
no longer appear in the free_voices allowlist; cleanup_invalid_voice_ids marks them invalid and clears them → an empty
voice falls back to the default_voice for free/step (qingchunshaonv), silently downgrading default YUI users
to a generic female voice. Migrate before cleanup checks validity: only domestic free (lanlan.tech) moves to
active yui_cn; overseas free (lanlan.app→free_intl) and non-free routes are not migrated, and are passed unchanged to existing
validation for clearing → server default (free_intl contract: \"unknown ID falls back to server default\", PR #1643:
clients must not inject StepFun magic IDs or aliases into the free_intl catalog).
"""
from __future__ import annotations

from utils.config_manager import ConfigManager, get_reserved


OLD_YUI_VOICE_ID = "voice-tone-R6NtLH3Hk0"
NEW_YUI_VOICE_ID = "voice-tone-RcH2svtsrw"

_DOMESTIC_FREE = {"CORE_API_TYPE": "free", "CORE_URL": "wss://www.lanlan.tech/core"}
_OVERSEAS_FREE = {"CORE_API_TYPE": "free", "CORE_URL": "wss://www.lanlan.app/core"}
_NON_FREE = {"CORE_API_TYPE": "qwen", "CORE_URL": ""}


def _make_manager(character_data: dict, core_config: dict | None = None,
                  non_mainland: bool = False) -> ConfigManager:
    mgr = object.__new__(ConfigManager)
    mgr._saved = {}
    mgr.load_characters = lambda: character_data

    def _save(data):
        mgr._saved["data"] = data

    mgr.save_characters = _save
    mgr.get_core_config = lambda: dict(core_config if core_config is not None else _DOMESTIC_FREE)
    mgr._check_non_mainland = lambda: non_mainland
    # These cases test cleanup/migration logic itself, assuming the region has already been determined — if it has not,
    # cleanup_invalid_voice_ids skips everything (to avoid deleting overseas voices based on a guessed region;
    # see the provisional case in test_geoip_region_gate.py). State that prerequisite here.
    mgr._region_verdict_is_provisional = lambda *_a: False
    return mgr


def _yui(voice_id: str) -> dict:
    return {"猫娘": {"YUI": {"昵称": "YUI", "_reserved": {"voice_id": voice_id}}}}


def _patch_free_voices(monkeypatch, free_voices: dict):
    monkeypatch.setattr("utils.api_config_loader.get_free_voices", lambda: free_voices)


def test_cleanup_migrates_deprecated_yui_voice(monkeypatch):
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = _yui(OLD_YUI_VOICE_ID)
    mgr = _make_manager(character_data)

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 0
    assert legacy == []
    assert get_reserved(character_data["猫娘"]["YUI"], "voice_id", default="") == NEW_YUI_VOICE_ID
    # A migration hit should trigger persistence
    assert mgr._saved.get("data") is character_data


def test_cleanup_migrates_whitespace_padded_deprecated_voice(monkeypatch):
    """Deprecated values with leading/trailing whitespace should also be recognized and remapped to clean active yui_cn."""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = _yui(f"  {OLD_YUI_VOICE_ID}  ")
    mgr = _make_manager(character_data)

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 0
    assert get_reserved(character_data["猫娘"]["YUI"], "voice_id", default="") == NEW_YUI_VOICE_ID
    # A migration hit should trigger persistence (paired with test_cleanup_migrates_deprecated_yui_voice)
    assert mgr._saved.get("data") is character_data


def test_remap_overseas_free_keeps_value_for_server_default(monkeypatch):
    """Overseas free (lanlan.app→free_intl): do not migrate deprecated StepFun tones; return them unchanged for existing
    validation to clear → server default. The client must never inject a \"yui\"/native alias (PR #1643)."""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    mgr = _make_manager({}, core_config=_OVERSEAS_FREE)

    assert mgr.remap_deprecated_free_yui_voice_id(OLD_YUI_VOICE_ID) == OLD_YUI_VOICE_ID


def test_remap_overseas_by_geo_keeps_value(monkeypatch):
    """Even if the URL is still lanlan.tech, do not migrate when geo detection says overseas; _check_non_mainland is the fallback."""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    mgr = _make_manager({}, core_config=_DOMESTIC_FREE, non_mainland=True)

    assert mgr.remap_deprecated_free_yui_voice_id(OLD_YUI_VOICE_ID) == OLD_YUI_VOICE_ID


def test_cleanup_overseas_clears_stale_yui_for_server_default(monkeypatch):
    """End to end: under overseas free, a deprecated tone is not migrated; validation marks it invalid and clears it → server default."""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = _yui(OLD_YUI_VOICE_ID)
    mgr = _make_manager(character_data, core_config=_OVERSEAS_FREE)
    mgr.validate_voice_id = lambda voice_id: False  # A stale StepFun tone is invalid on the overseas route

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 1
    assert get_reserved(character_data["猫娘"]["YUI"], "voice_id", default="") == ""


def test_remap_non_free_route_keeps_value(monkeypatch):
    """On a non-free route such as qwen, a deprecated StepFun preset is not applicable; do not migrate, leave it to the clearing fallback."""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    mgr = _make_manager({}, core_config=_NON_FREE)

    assert mgr.remap_deprecated_free_yui_voice_id(OLD_YUI_VOICE_ID) == OLD_YUI_VOICE_ID


def test_cleanup_keeps_current_yui_voice_untouched(monkeypatch):
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = _yui(NEW_YUI_VOICE_ID)
    mgr = _make_manager(character_data)
    mgr.validate_voice_id = lambda voice_id: True  # An active preset is valid

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 0
    assert get_reserved(character_data["猫娘"]["YUI"], "voice_id", default="") == NEW_YUI_VOICE_ID
    # No changes should not trigger persistence
    assert "data" not in mgr._saved


def test_cleanup_still_clears_unrelated_invalid_voice(monkeypatch):
    """Regression: remapping must not let truly invalid, YUI-unrelated existing voice_id values through."""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = {"猫娘": {"A": {"_reserved": {"voice_id": "some-stale-clone"}}}}
    mgr = _make_manager(character_data)
    mgr.validate_voice_id = lambda voice_id: False

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 1
    assert get_reserved(character_data["猫娘"]["A"], "voice_id", default="") == ""


def test_cleanup_clears_whitespace_padded_invalid_voice(monkeypatch):
    """Regression (CodeRabbit / Codex): normalization during remapping must not mistake a non-deprecated invalid voice_id with surrounding whitespace as \"already migrated\" and skip clearing it;
it must still follow the usual invalid-value cleanup."""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = {"猫娘": {"A": {"_reserved": {"voice_id": "  some-stale-clone  "}}}}
    mgr = _make_manager(character_data)
    mgr.validate_voice_id = lambda voice_id: False

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 1
    assert get_reserved(character_data["猫娘"]["A"], "voice_id", default="") == ""


def test_remap_requires_yui_cn_not_other_preset(monkeypatch):
    """Regression (Codex): on domestic free, if free_voices lacks yui_cn and contains only other presets, do not use a substitute such as cuteGirl to turn the deprecated YUI string into another voice —
return it unchanged for the clearing fallback."""
    _patch_free_voices(monkeypatch, {"cuteGirl": "voice-tone-PGLiyZt65w"})
    mgr = _make_manager({})

    assert mgr.remap_deprecated_free_yui_voice_id(OLD_YUI_VOICE_ID) == OLD_YUI_VOICE_ID


def test_remap_keeps_deprecated_when_current_unresolvable(monkeypatch):
    """Do not substitute a voice when domestic free cannot resolve active yui_cn (free_voices is empty)."""
    _patch_free_voices(monkeypatch, {})
    mgr = _make_manager({})

    assert mgr.remap_deprecated_free_yui_voice_id(OLD_YUI_VOICE_ID) == OLD_YUI_VOICE_ID
    assert mgr.remap_deprecated_free_yui_voice_id(NEW_YUI_VOICE_ID) == NEW_YUI_VOICE_ID
    assert mgr.remap_deprecated_free_yui_voice_id("") == ""


def test_is_deprecated_free_yui_voice_id_predicate():
    assert ConfigManager.is_deprecated_free_yui_voice_id(OLD_YUI_VOICE_ID) is True
    assert ConfigManager.is_deprecated_free_yui_voice_id(f"  {OLD_YUI_VOICE_ID}  ") is True
    assert ConfigManager.is_deprecated_free_yui_voice_id(NEW_YUI_VOICE_ID) is False
    assert ConfigManager.is_deprecated_free_yui_voice_id("") is False
