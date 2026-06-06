"""废弃免费 YUI 预设音色的自动平移（PR: voice-tone-R6NtLH3Hk0 → 现役 yui_cn）。

回归点：YUI 默认音色 ID 更替后，存量用户 characters.json 里残留的旧 tone ID
已不在 free_voices 白名单，cleanup_invalid_voice_ids 会判 invalid 清空 → 空
voice 落到 free/step 的 default_voice（qingchunshaonv），导致默认 YUI 用户无声
掉档到通用女声。cleanup 在判 invalid 前先把旧值平移到现役 yui_cn 兜住。
"""
from __future__ import annotations

from utils.config_manager import ConfigManager, get_reserved


OLD_YUI_VOICE_ID = "voice-tone-R6NtLH3Hk0"
NEW_YUI_VOICE_ID = "voice-tone-RcH2svtsrw"


def _make_manager(character_data: dict) -> ConfigManager:
    mgr = object.__new__(ConfigManager)
    mgr._saved = {}
    mgr.load_characters = lambda: character_data

    def _save(data):
        mgr._saved["data"] = data

    mgr.save_characters = _save
    return mgr


def _yui(voice_id: str) -> dict:
    return {"猫娘": {"YUI": {"昵称": "YUI", "_reserved": {"voice_id": voice_id}}}}


def _patch_free_voices(monkeypatch, free_voices: dict):
    monkeypatch.setattr("utils.api_config_loader.get_free_voices", lambda: free_voices)
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")


def test_cleanup_migrates_deprecated_yui_voice(monkeypatch):
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = _yui(OLD_YUI_VOICE_ID)
    mgr = _make_manager(character_data)

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 0
    assert legacy == []
    assert get_reserved(character_data["猫娘"]["YUI"], "voice_id", default="") == NEW_YUI_VOICE_ID
    # 迁移命中应触发存盘
    assert mgr._saved.get("data") is character_data


def test_cleanup_keeps_current_yui_voice_untouched(monkeypatch):
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = _yui(NEW_YUI_VOICE_ID)
    mgr = _make_manager(character_data)
    mgr.validate_voice_id = lambda voice_id: True  # 现役 preset 合法

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 0
    assert get_reserved(character_data["猫娘"]["YUI"], "voice_id", default="") == NEW_YUI_VOICE_ID
    # 无改动不应存盘
    assert "data" not in mgr._saved


def test_cleanup_still_clears_unrelated_invalid_voice(monkeypatch):
    """回归：平移逻辑不能放过真正无效的、与 YUI 无关的存量 voice_id。"""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = {"猫娘": {"A": {"_reserved": {"voice_id": "some-stale-clone"}}}}
    mgr = _make_manager(character_data)
    mgr.validate_voice_id = lambda voice_id: False

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 1
    assert get_reserved(character_data["猫娘"]["A"], "voice_id", default="") == ""


def test_remap_keeps_deprecated_when_current_unresolvable(monkeypatch):
    """防御：现役 yui_cn 解析不出（或仍落在废弃集合）时不乱换，交给既有清空兜底。"""
    _patch_free_voices(monkeypatch, {})
    mgr = object.__new__(ConfigManager)

    assert mgr.remap_deprecated_free_yui_voice_id(OLD_YUI_VOICE_ID) == OLD_YUI_VOICE_ID
    assert mgr.remap_deprecated_free_yui_voice_id(NEW_YUI_VOICE_ID) == NEW_YUI_VOICE_ID
    assert mgr.remap_deprecated_free_yui_voice_id("") == ""


def test_is_deprecated_free_yui_voice_id_predicate():
    assert ConfigManager.is_deprecated_free_yui_voice_id(OLD_YUI_VOICE_ID) is True
    assert ConfigManager.is_deprecated_free_yui_voice_id(f"  {OLD_YUI_VOICE_ID}  ") is True
    assert ConfigManager.is_deprecated_free_yui_voice_id(NEW_YUI_VOICE_ID) is False
    assert ConfigManager.is_deprecated_free_yui_voice_id("") is False
