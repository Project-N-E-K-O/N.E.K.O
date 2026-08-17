from __future__ import annotations

import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

_LOCALES = ("en", "zh-CN", "zh-TW", "ja", "ko", "es", "pt", "ru")
_REQUIRED_DIAGNOSTIC_KEYS = {
    "ui.error.document_analysis_invalid_endpoint",
    "ui.error.document_analysis_invalid_request",
}
_DOCUMENT_ENTRY_KEYS = {
    "entries.document_analysis_status.name",
    "entries.document_analysis_status.description",
    "entries.active_document_analysis.name",
    "entries.active_document_analysis.description",
    "entries.cancel_document_analysis.name",
    "entries.cancel_document_analysis.description",
}


def test_document_diagnostic_i18n_keys_are_complete_and_consistent() -> None:
    i18n_dir = (
        Path(__file__).resolve().parents[3] / "plugins" / "study_companion" / "i18n"
    )
    bundles = {
        locale: json.loads((i18n_dir / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in _LOCALES
    }
    baseline_keys = set(bundles["en"])

    assert _REQUIRED_DIAGNOSTIC_KEYS | _DOCUMENT_ENTRY_KEYS <= baseline_keys
    for locale, bundle in bundles.items():
        assert set(bundle) == baseline_keys, f"{locale} locale keys differ from en"
        for key in _REQUIRED_DIAGNOSTIC_KEYS | _DOCUMENT_ENTRY_KEYS:
            assert isinstance(bundle[key], str) and bundle[key].strip(), (
                f"{locale} is missing a non-empty translation for {key}"
            )
        entry_names = {
            bundle["entries.analyze_document.name"],
            bundle["entries.document_analysis_status.name"],
            bundle["entries.active_document_analysis.name"],
            bundle["entries.cancel_document_analysis.name"],
        }
        assert len(entry_names) == 4, f"{locale} document entry names collide"


def test_portuguese_document_errors_are_localized() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "i18n"
        / "pt.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["ui.error.document_invalid_kind"] == "Escolha um tipo de documento compatível."
    assert payload["ui.error.document_type_mismatch"] == "O tipo do documento não corresponde ao nome do arquivo."
    assert payload["ui.error.document_unsupported_locale"] == "O idioma selecionado não é compatível."
