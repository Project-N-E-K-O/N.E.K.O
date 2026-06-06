"""废弃免费 YUI 预设音色的自动平移（PR: voice-tone-R6NtLH3Hk0 → 现役 YUI 音色）。

回归点：YUI 默认音色 ID 更替后，存量用户 characters.json 里残留的旧 tone ID
已不在 free_voices 白名单，cleanup_invalid_voice_ids 会判 invalid 清空 → 空
voice 落到 free/step 的 default_voice（qingchunshaonv），导致默认 YUI 用户无声
掉档到通用女声。cleanup 在判 invalid 前先把旧值按线路平移：国内 free → 现役
yui_cn；海外 free → 品牌 sentinel "yui"（free_intl native），与
ensure_default_yui_voice_for_free_api 对偶。
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
    # 迁移命中应触发存盘
    assert mgr._saved.get("data") is character_data


def test_cleanup_migrates_whitespace_padded_deprecated_voice(monkeypatch):
    """带前后空白的废弃值也应被识别并平移到干净的现役 yui_cn。"""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = _yui(f"  {OLD_YUI_VOICE_ID}  ")
    mgr = _make_manager(character_data)

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 0
    assert get_reserved(character_data["猫娘"]["YUI"], "voice_id", default="") == NEW_YUI_VOICE_ID
    # 迁移命中应触发存盘（与 test_cleanup_migrates_deprecated_yui_voice 对偶）
    assert mgr._saved.get("data") is character_data


def test_cleanup_overseas_free_remaps_to_yui_sentinel(monkeypatch):
    """海外免费（lanlan.app）下废弃 StepFun tone 应绑品牌 sentinel "yui"，
    而非国内 voice-tone preset——否则非空 voice_id 会落进 external TTS。"""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = _yui(OLD_YUI_VOICE_ID)
    mgr = _make_manager(character_data, core_config=_OVERSEAS_FREE)

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 0
    assert get_reserved(character_data["猫娘"]["YUI"], "voice_id", default="") == "yui"


def test_cleanup_overseas_by_geo_remaps_to_yui_sentinel(monkeypatch):
    """URL 仍是 lanlan.tech 但地理判海外时，靠 _check_non_mainland 兜底绑 "yui"。"""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = _yui(OLD_YUI_VOICE_ID)
    mgr = _make_manager(character_data, core_config=_DOMESTIC_FREE, non_mainland=True)

    mgr.cleanup_invalid_voice_ids()

    assert get_reserved(character_data["猫娘"]["YUI"], "voice_id", default="") == "yui"


def test_remap_non_free_route_keeps_value(monkeypatch):
    """非 free 路由（如 qwen）下废弃 StepFun preset 用不上，不迁移、交清空兜底。"""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    mgr = _make_manager({}, core_config=_NON_FREE)

    assert mgr.remap_deprecated_free_yui_voice_id(OLD_YUI_VOICE_ID) == OLD_YUI_VOICE_ID


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


def test_cleanup_clears_whitespace_padded_invalid_voice(monkeypatch):
    """回归（CodeRabbit / Codex）：带前后空白的非废弃无效 voice_id 不能被 remap
    的归一化误当成「已迁移」而漏清，必须照常走 invalid 清空。"""
    _patch_free_voices(monkeypatch, {"yui_cn": NEW_YUI_VOICE_ID})
    character_data = {"猫娘": {"A": {"_reserved": {"voice_id": "  some-stale-clone  "}}}}
    mgr = _make_manager(character_data)
    mgr.validate_voice_id = lambda voice_id: False

    cleaned, legacy = mgr.cleanup_invalid_voice_ids()

    assert cleaned == 1
    assert get_reserved(character_data["猫娘"]["A"], "voice_id", default="") == ""


def test_remap_requires_yui_cn_not_other_preset(monkeypatch):
    """回归（Codex）：国内 free 但 free_voices 缺 yui_cn、只有别的 preset 时不得
    借 cuteGirl 等当替身把废弃 YUI 串成别的音色——原样返回，交清空兜底。"""
    _patch_free_voices(monkeypatch, {"cuteGirl": "voice-tone-PGLiyZt65w"})
    mgr = _make_manager({})

    assert mgr.remap_deprecated_free_yui_voice_id(OLD_YUI_VOICE_ID) == OLD_YUI_VOICE_ID


def test_remap_keeps_deprecated_when_current_unresolvable(monkeypatch):
    """国内 free 但现役 yui_cn 解析不出（free_voices 为空）时不乱换。"""
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
