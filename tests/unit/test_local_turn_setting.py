# -- coding: utf-8 --
"""Round-trip test for the `localTurnDetectionEnabled` conversation setting.

Verifies the new key survives the whitelist filter AND the bool-type validation
in save/load_global_conversation_settings — the exact gate the frontend toggle's
POST /api/config/conversation-settings body must pass to reach core.py.
"""
from unittest.mock import MagicMock

import utils.preferences as preferences


def _patch_store(monkeypatch, tmp_path):
    pref_file = str(tmp_path / "user_preferences.json")
    monkeypatch.setattr(preferences, "assert_cloudsave_writable", lambda *a, **k: None)
    monkeypatch.setattr(preferences, "_get_preferences_write_path", lambda: pref_file)
    monkeypatch.setattr(preferences, "_get_preferences_read_path", lambda: pref_file)
    monkeypatch.setattr(preferences, "_get_active_preferences_path", lambda: pref_file)
    monkeypatch.setattr(preferences, "_config_manager", MagicMock())
    return pref_file


def test_local_turn_key_whitelisted():
    # load only returns whitelisted keys; membership guarantees persistence round-trips
    assert "localTurnDetectionEnabled" in preferences._ALLOWED_CONVERSATION_SETTINGS


def test_local_turn_setting_roundtrip_true_false(tmp_path, monkeypatch):
    _patch_store(monkeypatch, tmp_path)
    assert preferences.save_global_conversation_settings({"localTurnDetectionEnabled": True}) is True
    assert preferences.load_global_conversation_settings().get("localTurnDetectionEnabled") is True
    assert preferences.save_global_conversation_settings({"localTurnDetectionEnabled": False}) is True
    assert preferences.load_global_conversation_settings().get("localTurnDetectionEnabled") is False


def test_local_turn_setting_non_bool_rejected(tmp_path, monkeypatch):
    _patch_store(monkeypatch, tmp_path)
    preferences.save_global_conversation_settings({"localTurnDetectionEnabled": True})
    # a non-bool value must be dropped by validation, leaving the prior value intact
    preferences.save_global_conversation_settings({"localTurnDetectionEnabled": "yes"})
    assert preferences.load_global_conversation_settings().get("localTurnDetectionEnabled") is True


def test_smart_turn_setting_roundtrip(tmp_path, monkeypatch):
    assert "smartTurnEnabled" in preferences._ALLOWED_CONVERSATION_SETTINGS
    _patch_store(monkeypatch, tmp_path)
    assert preferences.save_global_conversation_settings({"smartTurnEnabled": False}) is True
    assert preferences.load_global_conversation_settings().get("smartTurnEnabled") is False
    assert preferences.save_global_conversation_settings({"smartTurnEnabled": True}) is True
    assert preferences.load_global_conversation_settings().get("smartTurnEnabled") is True


def test_local_turn_setting_coexists_with_noise_reduction(tmp_path, monkeypatch):
    _patch_store(monkeypatch, tmp_path)
    preferences.save_global_conversation_settings({"noiseReductionEnabled": False})
    preferences.save_global_conversation_settings({"localTurnDetectionEnabled": True})
    s = preferences.load_global_conversation_settings()
    assert s.get("noiseReductionEnabled") is False   # not clobbered by the second save
    assert s.get("localTurnDetectionEnabled") is True
