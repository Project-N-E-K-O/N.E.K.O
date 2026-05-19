from __future__ import annotations

import pytest
from plugin.sdk.shared.i18n import load_plugin_i18n_from_dir, resolve_i18n_refs, tr

_EXPECTED_ENTRY_IDS = [
    "galgame_get_status",
    "galgame_install_textractor",
    "galgame_download_rapidocr_models",
    "galgame_install_dxcam",
    "galgame_get_snapshot",
    "galgame_get_history",
    "galgame_set_mode",
    "galgame_set_ocr_backend",
    "galgame_set_ocr_timing",
    "galgame_set_llm_vision",
    "galgame_set_ocr_screen_templates",
    "galgame_build_ocr_screen_template_draft",
    "galgame_validate_ocr_screen_templates",
    "galgame_get_ocr_screen_awareness_snapshot",
    "galgame_train_ocr_screen_awareness_model",
    "galgame_evaluate_ocr_screen_awareness_model",
    "galgame_bind_game",
    "galgame_set_ocr_capture_profile",
    "galgame_auto_recalibrate_ocr_dialogue_profile",
    "galgame_apply_recommended_ocr_capture_profile",
    "galgame_rollback_ocr_capture_profile",
    "galgame_list_memory_reader_processes",
    "galgame_set_memory_reader_target",
    "galgame_list_ocr_windows",
    "galgame_set_ocr_window_target",
    "galgame_open_ui",
    "galgame_explain_line",
    "galgame_summarize_scene",
    "galgame_suggest_choice",
    "galgame_agent_command",
    "galgame_continue_auto_advance",
    "galgame_get_character_profile",
    "galgame_set_character_mode",
    "galgame_get_character_list",
    "galgame_import_character_data",
    "galgame_get_scene_context",
    "galgame_get_story_so_far",
    "galgame_get_recent_lines",
    "galgame_get_push_history",
]

_EXPECTED_RUNTIME_KEYS = [
    "install.textractor.ok",
    "install.textractor.fail",
    "install.dxcam.ok",
    "install.dxcam.fail",
    "errors.not_configured",
    "errors.install_in_progress",
]

_EXPECTED_LOCALES = ["zh-CN", "zh-TW", "en", "ja", "ru", "ko", "es", "pt"]


def _assert_bundle_has_key(i18n, locale: str, key: str) -> None:
    bundle = i18n.messages.get(locale) or {}
    assert key in bundle
    assert isinstance(bundle[key], str) and bundle[key]


@pytest.mark.parametrize("locale", _EXPECTED_LOCALES)
def test_i18n_all_locales_have_all_keys(galgame_i18n_dir, locale) -> None:
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)
    for entry_id in _EXPECTED_ENTRY_IDS:
        _assert_bundle_has_key(i18n, locale, f"entries.{entry_id}.name")
        _assert_bundle_has_key(i18n, locale, f"entries.{entry_id}.description")
    for key in _EXPECTED_RUNTIME_KEYS:
        _assert_bundle_has_key(i18n, locale, key)
    base_locale = "en"
    assert len(i18n.messages[locale]) == len(i18n.messages[base_locale])


def test_tr_ref_resolves_to_correct_locale(galgame_i18n_dir) -> None:
    ref = tr("entries.galgame_get_status.name", default="fallback")
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)

    zh = resolve_i18n_refs(ref, i18n, locale="zh-CN")
    en = resolve_i18n_refs(ref, i18n, locale="en")

    assert zh == "获取 galgame 插件状态"
    assert en == "Get galgame plugin status"


def test_zh_tw_locale_is_traditional_chinese_not_zh_cn_copy(galgame_i18n_dir) -> None:
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)
    zh_cn = i18n.messages["zh-CN"]
    zh_tw = i18n.messages["zh-TW"]

    assert zh_tw != zh_cn
    assert zh_tw["plugin.name"] == "Galgame 遊玩助手"
    assert zh_tw["plugin.description"] == "讓貓娘陪伴你一起玩 Galgame"

    simplified_fragments = [
        "游玩",
        "让猫娘",
        "获取",
        "设置",
        "窗口",
        "进程",
        "识别",
        "截图",
        "当前",
        "状态",
        "后台",
        "点击",
        "发送",
    ]
    for key, value in zh_tw.items():
        assert not any(fragment in value for fragment in simplified_fragments), (key, value)


def test_tr_default_fallback(galgame_i18n_dir) -> None:
    ref = tr("entries.nonexistent.key", default="默认值")
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)

    result = resolve_i18n_refs(ref, i18n, locale="en")

    assert result == "默认值"
