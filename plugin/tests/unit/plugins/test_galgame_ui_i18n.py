from __future__ import annotations

import json
from pathlib import Path


UI_I18N_DIR = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "galgame_plugin"
    / "i18n"
    / "ui"
)
STATIC_I18N_JS = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "galgame_plugin"
    / "static"
    / "i18n.js"
)

EXPECTED_LOCALES = ["zh-CN", "en", "ja", "ru", "ko"]


def test_galgame_ui_i18n_locale_bundles_have_same_keys() -> None:
    bundles = {
        locale: json.loads((UI_I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in EXPECTED_LOCALES
    }
    expected_keys = set(bundles["zh-CN"])

    assert len(expected_keys) >= 100
    for locale, bundle in bundles.items():
        bundle_keys = set(bundle)
        missing = sorted(expected_keys - bundle_keys)
        extra = sorted(bundle_keys - expected_keys)
        assert bundle_keys == expected_keys, (
            f"{locale}: missing={missing[:20]} extra={extra[:20]}"
        )
        assert all(isinstance(value, str) and value for value in bundle.values())


def test_galgame_ui_i18n_has_install_and_static_shell_keys() -> None:
    bundle = json.loads((UI_I18N_DIR / "en.json").read_text(encoding="utf-8"))

    assert bundle["ui.app.title"] == "Galgame Play Assistant"
    assert bundle["ui.button.collapse"] == "Collapse"
    assert bundle["ui.install.rapidocr.download_models.action"] == "Download Models Now"
    assert bundle["ui.first_run.action.show_rapidocr_models_guide"] == "View Manual Download Guide"
    assert bundle["ui.flash.plugin_not_started"].startswith("Plugin not started")


def test_galgame_ui_i18n_rapidocr_copy_is_not_left_half_deleted() -> None:
    forbidden_fragments = [
        "stable capture,.",
        "fell back to. Reason",
        "回退到了。原因",
        "优先 兜底",
        "では にフォールバック",
        "し をフォールバック",
        "에서는로",
        "우선하고를",
        "откат на. Причина",
        "приоритетом RapidOCR и резервом",
    ]
    for locale in EXPECTED_LOCALES:
        bundle = json.loads((UI_I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        for key in [
            "ui.install.ocr_desc",
            "ui.install.ocr_auto.title",
            "ui.install.rapidocr.fallback_body",
            "ui.install.rapidocr.ready_body",
        ]:
            value = bundle[key]
            assert not any(fragment in value for fragment in forbidden_fragments), (locale, key, value)


def test_galgame_ui_i18n_has_dynamic_dashboard_keys() -> None:
    bundle = json.loads((UI_I18N_DIR / "en.json").read_text(encoding="utf-8"))

    for key in [
        "ui.field.connection_state",
        "ui.field.ocr_reader_status",
        "ui.field.memory_reader_process",
        "ui.agent_status.paused_window_not_foreground",
        "ui.connection_state.active",
        "ui.mode_label.choice_advisor",
        "ui.reader_mode.auto",
        "ui.capture_profile.match_source.bucket_exact",
        "ui.action.select_ocr_window",
    ]:
        assert key in bundle


def test_galgame_ui_i18n_script_prefers_query_locale_with_api_fallback() -> None:
    script = STATIC_I18N_JS.read_text(encoding="utf-8")

    assert "new URLSearchParams(location.search).get('locale')" in script
    assert "const queryLocale = this._queryLocale();" in script
    assert "const storageLocale = this._storageLocale();" in script
    assert "if (queryLocale) {" in script
    assert "this.setLang(queryLocale);" in script
    assert "else if (storageLocale) {" in script
    assert "this.setLang(storageLocale);" in script
    assert "localStorage.getItem('locale')" in script
    assert "value === 'auto' ? this._browserLocale() : value" in script
    assert "else {" in script
    assert "/ui-api/locale" in script
    assert "/ui-api/i18n/ui/" in script
    assert "i18n-ready" in script


def test_galgame_ui_i18n_script_maps_manager_locales_to_ui_bundles() -> None:
    script = STATIC_I18N_JS.read_text(encoding="utf-8")

    for expected in [
        "add('zh-CN');",
        "add('en');",
        "add('ja');",
        "add('ko');",
        "add('ru');",
    ]:
        assert expected in script


def test_galgame_ui_first_run_has_manual_rapidocr_model_cta() -> None:
    script = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "galgame_plugin"
        / "static"
        / "main.js"
    ).read_text(encoding="utf-8")

    assert "show_rapidocr_models_guide" in script
    assert "ui.first_run.action.show_rapidocr_models_guide" in script
    assert "ui.flash.rapidocr_manual_guide_revealed" in script
