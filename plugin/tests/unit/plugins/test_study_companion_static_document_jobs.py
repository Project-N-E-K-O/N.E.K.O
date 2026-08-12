from __future__ import annotations

import gzip
from pathlib import Path


STATIC = Path(__file__).parents[3] / "plugins" / "study_companion" / "static"


def test_static_document_analysis_uses_cancellable_long_job_contract() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    main = (STATIC / "main.js").read_text(encoding="utf-8")
    helpers = (STATIC / "surface-panels.js").read_text(encoding="utf-8")

    assert 'id="studyDocumentProgress"' in html
    assert 'id="studyDocumentProgressBar"' in html
    assert 'data-i18n="ui.document.cancel_analysis"' in html
    assert "study_start_document_analysis" in helpers
    assert "study_document_analysis_status" in helpers
    assert "study_cancel_document_analysis" in helpers
    assert "let delay = 1000" in helpers
    assert "delay = 2000" in helpers
    assert "cancelRequested: false" in helpers
    assert "if (this.cancelRequested && this.currentId)" in helpers
    assert "data = await call('study_cancel_document_analysis', { job_id: this.currentId }, signal)" in helpers
    assert "this.cancelRequested = false" in helpers
    assert "studyDocumentCancelBtn').disabled = false" in helpers
    assert "if (!jobId) return" in helpers
    assert "controller?.abort(); setDocumentBusy(false)" not in helpers
    assert "window.StudyDocumentJobs.run(callPlugin" in main
    assert "study_analyze_document', {" not in main
    assert "window.StudyDocumentJobs.leave(PLUGIN_ID)" in main
    assert "navigator.sendBeacon('/runs'" in helpers
    assert "if (!documentBusy) studyDocumentCancelBtn.disabled = false" in main


def test_static_document_busy_isolated_and_budget_hints_match_backend_modes() -> None:
    main = (STATIC / "main.js").read_text(encoding="utf-8")
    style = (STATIC / "style.css").read_text(encoding="utf-8")

    busy = main[main.index("function setDocumentBusy"):main.index("function setPasteError")]
    assert "setPanelBusy" not in busy
    assert "studyDocumentCard.dataset.busy" in busy
    assert "tokens > 160000 ? 'document_too_long'" in main
    assert "tokens > 48000" in main
    assert "ui.document.chunked_mode_hint" in main
    assert "ui.document.direct_mode_hint" in main
    assert '.study-document-card[data-busy="true"]' in style
    assert '.main-view[data-busy="true"] .study-document-card' not in style


def test_static_document_changes_stay_inside_bundle_limits() -> None:
    main = (STATIC / "main.js").read_bytes()
    style = (STATIC / "style.css").read_text(encoding="utf-8")

    assert len(main) <= 95_000
    assert len(gzip.compress(main)) <= 22_000
    assert len(style.splitlines()) <= 2_500
