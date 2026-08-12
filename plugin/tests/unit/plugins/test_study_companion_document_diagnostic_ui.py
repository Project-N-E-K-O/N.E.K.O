from __future__ import annotations

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

    assert "invalid_endpoint invalid_request" in formatter
    assert "`analysis_${code}`" in formatter
