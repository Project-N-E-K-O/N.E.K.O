from __future__ import annotations

from pathlib import Path

import pytest

from plugin.plugins.study_companion.doc_exporter import DocExporter
from plugin.plugins.study_companion.models import DocExportConfig
from plugin.plugins.study_companion.store import StudyStore

pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *args: object, **kwargs: object) -> None:
        return None


def _store(tmp_path: Path) -> StudyStore:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    return store


def test_integration_export_pipeline_builds_markdown_pdf_docx_and_xmind(tmp_path: Path) -> None:
    pytest.importorskip("reportlab")
    pytest.importorskip("docx")
    store = _store(tmp_path)
    try:
        store.append_interaction(
            kind="summarize_session",
            input_text="学习了导数",
            output_text="导数表示瞬时变化率",
        )
        store.ensure_topic(
            topic_id="derivative",
            name="Derivative",
            subject="math",
            chapter="calculus",
            difficulty=0.5,
        )
        exporter = DocExporter(store, config=DocExportConfig(xmind_enabled=True))

        markdown = exporter.export(fmt="markdown", title="Study Notes")
        pdf = exporter.export(fmt="pdf", title="Study Notes")
        docx = exporter.export(fmt="docx", title="Study Notes")
        xmind = exporter.export(fmt="xmind", title="Study Notes")

        assert markdown.content.startswith(b"# Study Notes")
        assert b"Derivative" in markdown.content
        assert pdf.content.startswith(b"%PDF")
        assert docx.content.startswith(b"PK")
        assert xmind.content.startswith(b"PK")
    finally:
        store.close()
