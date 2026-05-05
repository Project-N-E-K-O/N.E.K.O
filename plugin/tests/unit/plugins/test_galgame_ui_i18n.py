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

EXPECTED_LOCALES = ["zh-CN", "en", "ja", "ru", "ko"]


def test_galgame_ui_i18n_locale_bundles_have_same_keys() -> None:
    bundles = {
        locale: json.loads((UI_I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in EXPECTED_LOCALES
    }
    expected_keys = set(bundles["zh-CN"])

    assert len(expected_keys) >= 100
    for locale, bundle in bundles.items():
        assert set(bundle) == expected_keys, locale
        assert all(isinstance(value, str) and value for value in bundle.values())


def test_galgame_ui_i18n_has_install_and_static_shell_keys() -> None:
    bundle = json.loads((UI_I18N_DIR / "en.json").read_text(encoding="utf-8"))

    assert bundle["ui.app.title"] == "Galgame Play Assistant"
    assert bundle["ui.button.collapse"] == "Collapse"
    assert bundle["ui.install.rapidocr.action"] == "Install RapidOCR"
    assert bundle["ui.flash.plugin_not_started"].startswith("Plugin not started")


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
