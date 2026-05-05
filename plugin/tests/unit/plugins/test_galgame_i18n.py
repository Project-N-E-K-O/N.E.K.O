from __future__ import annotations

from plugin.sdk.shared.i18n import load_plugin_i18n_from_dir, resolve_i18n_refs, tr


_EXPECTED_ENTRY_IDS = [
    "galgame_get_status",
    "galgame_install_textractor",
    "galgame_install_tesseract",
    "galgame_install_rapidocr",
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
]

_EXPECTED_RUNTIME_KEYS = [
    "install.textractor.ok",
    "install.textractor.fail",
    "install.tesseract.ok",
    "install.tesseract.fail",
    "install.rapidocr.ok",
    "install.rapidocr.fail",
    "install.dxcam.ok",
    "install.dxcam.fail",
    "errors.not_configured",
    "errors.install_in_progress",
]


def _assert_bundle_has_key(i18n, locale: str, key: str) -> None:
    bundle = i18n.messages.get(locale) or {}
    assert key in bundle
    assert isinstance(bundle[key], str) and bundle[key]


def test_i18n_zh_cn_has_all_keys(galgame_i18n_dir) -> None:
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)
    for entry_id in _EXPECTED_ENTRY_IDS:
        _assert_bundle_has_key(i18n, "zh-CN", f"entries.{entry_id}.name")
        _assert_bundle_has_key(i18n, "zh-CN", f"entries.{entry_id}.description")
    for key in _EXPECTED_RUNTIME_KEYS:
        _assert_bundle_has_key(i18n, "zh-CN", key)
    assert len(i18n.messages["zh-CN"]) == 74


def test_i18n_en_has_all_keys(galgame_i18n_dir) -> None:
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)
    for entry_id in _EXPECTED_ENTRY_IDS:
        _assert_bundle_has_key(i18n, "en", f"entries.{entry_id}.name")
        _assert_bundle_has_key(i18n, "en", f"entries.{entry_id}.description")
    for key in _EXPECTED_RUNTIME_KEYS:
        _assert_bundle_has_key(i18n, "en", key)
    assert len(i18n.messages["en"]) == 74


def test_tr_ref_resolves_to_correct_locale(galgame_i18n_dir) -> None:
    ref = tr("entries.galgame_get_status.name", default="fallback")
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)

    zh = resolve_i18n_refs(ref, i18n, locale="zh-CN")
    en = resolve_i18n_refs(ref, i18n, locale="en")

    assert zh == "获取 galgame 插件状态"
    assert en == "Get galgame plugin status"


def test_tr_default_fallback(galgame_i18n_dir) -> None:
    ref = tr("entries.nonexistent.key", default="默认值")
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)

    result = resolve_i18n_refs(ref, i18n, locale="en")

    assert result == "默认值"
