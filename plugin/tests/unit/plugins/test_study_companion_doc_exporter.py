from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from plugin.plugins.study_companion.doc_exporter import DocExporter, _pdf_safe_text, escape_markdown, safe_utf8_truncate
from plugin.plugins.study_companion.models import DocExportConfig, STUDY_EXPORT_FORMATS, STUDY_EXPORT_STYLES
from plugin.plugins.study_companion.store import StudyStore


class _Logger:
    def warning(self, *args, **kwargs):
        return None


def _store(tmp_path: Path) -> StudyStore:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    store.ensure_topic(topic_id="photosynthesis", name="Photosynthesis", subject="biology", chapter="plants")
    store.append_mastery_snapshot(
        {
            "topic_id": "photosynthesis",
            "mastery": 0.75,
            "accuracy": 0.8,
            "recency": 0.7,
            "consistency": 0.6,
            "confidence": 0.9,
            "level": "learning",
            "attempts": 3,
            "flags": [],
        }
    )
    store.append_interaction(
        kind="concept_explain",
        input_text="**raw** markdown [link](https://example.test) " + ("x" * 3000),
        output_text="Photosynthesis converts light. 😀",
        history_limit=10,
    )
    return store


def test_markdown_build_escapes_and_truncates_user_text(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        exporter = DocExporter(store)
        markdown = exporter.build_markdown(title="My *Notes*", style="unknown", recent_limit=5)

        assert "# My \\*Notes\\*" in markdown
        assert "\\*\\*raw\\*\\*" in markdown
        assert "\\[link\\]" in markdown
        assert "truncated" in markdown
        assert "- Tone: `friendly`" in markdown
        assert "Photosynthesis" in markdown
        assert exporter.normalize_style("unknown") == "neko"
    finally:
        store.close()


def test_topic_id_export_resolves_topics_outside_style_page_limit(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "many-topics.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        for index in range(250):
            store.ensure_topic(
                topic_id=f"topic-{index:03d}",
                name=f"Topic {index:03d}",
                subject="subject",
                chapter=f"chapter-{index:03d}",
            )

        markdown = DocExporter(store).build_markdown(
            style="compact",
            topic_ids=["topic-249"],
        )

        assert "Topic 249" in markdown
        assert "`topic\\-249`" in markdown
        assert "Topics included: 1" in markdown
    finally:
        store.close()


def test_export_markdown_handles_empty_store_and_declared_constants(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "empty.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        exported = DocExporter(store).export(fmt="markdown", style="compact", title="Empty")

        assert exported.content.startswith(b"# Empty")
        assert exported.filename == "empty.md"
        assert exported.content_type.startswith("text/markdown")
        assert "markdown" in STUDY_EXPORT_FORMATS
        assert "compact" in STUDY_EXPORT_STYLES
    finally:
        store.close()


def test_export_pdf_docx_and_xmind_bytes(tmp_path: Path) -> None:
    pytest.importorskip("reportlab")
    pytest.importorskip("docx")
    store = _store(tmp_path)
    try:
        exporter = DocExporter(store, config=DocExportConfig(xmind_enabled=True))

        pdf = exporter.export(fmt="pdf", title="Study PDF")
        docx = exporter.export(fmt="docx", title="Study DOCX")
        xmind = exporter.export(fmt="xmind", title="Study XMind")

        assert pdf.content.startswith(b"%PDF")
        assert docx.content.startswith(b"PK")
        assert xmind.content.startswith(b"PK")
        archive_path = tmp_path / "notes.xmind"
        archive_path.write_bytes(xmind.content)
        with ZipFile(archive_path) as archive:
            assert {"content.json", "metadata.json", "manifest.json"}.issubset(set(archive.namelist()))
    finally:
        store.close()


def test_export_pdf_preserves_unicode_text(tmp_path: Path) -> None:
    pytest.importorskip("reportlab")
    store = StudyStore(tmp_path / "unicode.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        store.append_interaction(kind="concept_explain", input_text="光合作用", output_text="植物吸收光能", history_limit=10)
        pdf = DocExporter(store).export(fmt="pdf", title="中文笔记")

        assert _pdf_safe_text("中文笔记") == "中文笔记"
        assert pdf.content.startswith(b"%PDF")
    finally:
        store.close()


def test_doc_exporter_rejects_store_without_required_methods() -> None:
    with pytest.raises(TypeError, match="missing required methods"):
        DocExporter(object())  # type: ignore[arg-type]


def test_xmind_export_requires_explicit_enable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="XMind export is disabled"):
            DocExporter(store, config=DocExportConfig(xmind_enabled=False)).export(fmt="xmind")
    finally:
        store.close()


def test_preview_export_uses_markdown_metadata_for_non_markdown_format(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        exported = DocExporter(store, config=DocExportConfig(xmind_enabled=True)).export(
            fmt="xmind",
            title="Preview Notes",
            preview_only=True,
        )

        assert exported.content.startswith(b"# Preview Notes")
        assert exported.filename == "preview-notes.md"
        assert exported.content_type.startswith("text/markdown")
        assert exported.format == "markdown"
    finally:
        store.close()


def test_escape_markdown_handles_emoji_and_none() -> None:
    assert escape_markdown(None) == ""
    assert "😀" in escape_markdown("emoji 😀")


def test_safe_utf8_truncate_does_not_split_multibyte_characters() -> None:
    assert safe_utf8_truncate("\u4e2d\u6587abc", 5) == "\u4e2d"
    assert safe_utf8_truncate("abc", 120) == "abc"
