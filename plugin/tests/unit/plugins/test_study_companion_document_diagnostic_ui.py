from __future__ import annotations

import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

_PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"


def test_hosted_document_diagnostics_map_invalid_endpoint_and_request() -> None:
    source = (_PLUGIN_DIR / "surfaces" / "study_panel.tsx").read_text(
        encoding="utf-8"
    )

    assert "invalid_endpoint: documentOperation" in source
    assert "'ui.error.document_analysis_invalid_endpoint'" in source
    assert "invalid_request: documentOperation" in source
    assert "'ui.error.document_analysis_invalid_request'" in source


def test_static_document_diagnostics_map_invalid_endpoint_and_request() -> None:
    source = (_PLUGIN_DIR / "static" / "document-controller.js").read_text(
        encoding="utf-8"
    )
    start = source.index("function formatDocumentDiagnostic")
    end = source.index("async function analyzeDocument", start)
    formatter = source[start:end]

    assert "'invalid_endpoint'" in formatter
    assert "'invalid_request'" in formatter
    assert "analysisErrors.has(analysisCode)" in formatter
    assert "`analysis_${analysisCode}`" in formatter


def test_document_phase_deadlines_use_existing_timeout_messages() -> None:
    hosted = (_PLUGIN_DIR / "surfaces" / "study_panel.tsx").read_text(
        encoding="utf-8"
    )
    static = (_PLUGIN_DIR / "static" / "document-controller.js").read_text(
        encoding="utf-8"
    )
    phase_diagnostics = (
        "document_analysis_window_exhausted",
        "document_chunk_window_exhausted",
        "document_merge_window_exhausted",
        "document_finalize_timeout",
    )

    hosted_start = hosted.index("const phaseDeadlineDiagnostics")
    hosted_end = hosted.index("const messages", hosted_start)
    hosted_normalization = hosted[hosted_start:hosted_end]
    static_start = static.index("const analysisAliases")
    static_end = static.index("const analysisCode", static_start)
    static_normalization = static[static_start:static_end]

    for diagnostic in phase_diagnostics:
        assert f"'{diagnostic}'" in hosted_normalization
        assert f"{diagnostic}: 'timeout'" in static_normalization
    assert "phaseDeadlineDiagnostics.has(diagnosticCode)" in hosted_normalization
    assert "? 'timeout'" in hosted_normalization


def test_document_surfaces_warn_when_completed_output_is_truncated() -> None:
    hosted = (_PLUGIN_DIR / "surfaces" / "study_panel.tsx").read_text(
        encoding="utf-8"
    )
    static = (_PLUGIN_DIR / "static" / "document-controller.js").read_text(
        encoding="utf-8"
    )

    assert "function formatDocumentCompletion" in hosted
    assert "payload.diagnostic !== 'output_truncated'" in hosted
    assert "formatDocumentCompletion(data)" in hosted
    assert "formatDocumentCompletion(data || {})" in hosted
    assert static.count("data.diagnostic === 'output_truncated'") == 2
    assert static.count("formatDocumentDiagnostic(data.diagnostic)") >= 4


def test_document_output_truncation_warning_exists_in_all_locales() -> None:
    locale_paths = sorted((_PLUGIN_DIR / "i18n").glob("*.json"))

    assert len(locale_paths) == 8
    for locale_path in locale_paths:
        bundle = json.loads(locale_path.read_text(encoding="utf-8"))
        assert bundle["ui.error.document_output_truncated"].strip(), locale_path.stem
