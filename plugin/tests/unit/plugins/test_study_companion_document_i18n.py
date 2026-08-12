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


def test_document_diagnostic_i18n_keys_are_complete_and_consistent() -> None:
    i18n_dir = (
        Path(__file__).resolve().parents[3] / "plugins" / "study_companion" / "i18n"
    )
    bundles = {
        locale: json.loads((i18n_dir / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in _LOCALES
    }
    baseline_keys = set(bundles["en"])

    assert _REQUIRED_DIAGNOSTIC_KEYS <= baseline_keys
    for locale, bundle in bundles.items():
        assert set(bundle) == baseline_keys, f"{locale} locale keys differ from en"
        for key in _REQUIRED_DIAGNOSTIC_KEYS:
            assert isinstance(bundle[key], str) and bundle[key].strip(), (
                f"{locale} is missing a non-empty translation for {key}"
            )
