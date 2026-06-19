from __future__ import annotations

from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

pytestmark = pytest.mark.unit

from plugin.plugins.study_companion.doc_exporter import (
    DocExporter,
    _PDF_CJK_SAMPLE,
    _pdf_safe_text,
    escape_markdown,
    safe_utf8_truncate,
)
from plugin.plugins.study_companion.models import (
    DocExportConfig,
    STUDY_EXPORT_FORMATS,
    STUDY_EXPORT_STYLES,
)
from plugin.plugins.study_companion.store import StudyStore


class _Logger:
    def warning(self, *args, **kwargs):
        return None


class _MinimalStore:
    def list_interactions(self, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def list_topics(
        self, limit: int = 100, subject: str | None = None
    ) -> list[dict[str, Any]]:
        return []

    def list_mastery_overview(self, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def list_wrong_questions(
        self,
        *,
        limit: int = 20,
        topic_id: str | None = None,
        statuses: tuple[str, ...] = ("active", "retrying", "resolved"),
    ) -> list[dict[str, Any]]:
        return []


def _sample_cjk_cmap() -> dict[int, int]:
    return {ord(char): index for index, char in enumerate(_PDF_CJK_SAMPLE, start=1)}


def _store(tmp_path: Path) -> StudyStore:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    store.ensure_topic(
        topic_id="photosynthesis",
        name="Photosynthesis",
        subject="biology",
        chapter="plants",
    )
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
        markdown = exporter.build_markdown(
            title="My *Notes*", style="unknown", recent_limit=5
        )

        assert "# My \\*Notes\\*" in markdown
        assert "\\*\\*raw\\*\\*" in markdown
        assert "\\[link\\]" in markdown
        assert "truncated" in markdown
        assert "- Tone: `friendly`" in markdown
        assert "Photosynthesis" in markdown
        assert exporter.normalize_style("unknown") == "neko"
    finally:
        store.close()


def test_topic_id_export_resolves_topics_outside_style_page_limit(
    tmp_path: Path,
) -> None:
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


def test_export_markdown_handles_empty_store_and_declared_constants(
    tmp_path: Path,
) -> None:
    store = StudyStore(tmp_path / "empty.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        exported = DocExporter(store).export(
            fmt="markdown", style="compact", title="Empty"
        )

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
            assert {"content.json", "metadata.json", "manifest.json"}.issubset(
                set(archive.namelist())
            )
    finally:
        store.close()


def test_export_pdf_preserves_unicode_text(tmp_path: Path) -> None:
    pytest.importorskip("reportlab")
    from reportlab.pdfgen.canvas import Canvas

    DocExporter._registered_pdf_fonts.clear()
    drawn_texts: list[str] = []
    original_draw_string = Canvas.drawString

    def _draw_string_spy(self: Canvas, x: float, y: float, text: str) -> None:
        drawn_texts.append(text)
        original_draw_string(self, x, y, text)

    store = StudyStore(tmp_path / "unicode.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        Canvas.drawString = _draw_string_spy
        store.append_interaction(
            kind="concept_explain",
            input_text="光合作用",
            output_text="植物吸收光能",
            history_limit=10,
        )
        pdf = DocExporter(store).export(fmt="pdf", title="中文笔记")

        assert _pdf_safe_text("中文笔记") == "中文笔记"
        assert pdf.content.startswith(b"%PDF")
        assert any("中文笔记" in text for text in drawn_texts)
        assert any("光合作用" in text for text in drawn_texts)
    finally:
        Canvas.drawString = original_draw_string
        store.close()


def test_pdf_font_falls_back_when_cjk_env_var_points_to_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfgen.canvas import Canvas

    DocExporter._registered_pdf_fonts.clear()
    font_names: list[str] = []
    registered_fonts: list[str] = []
    original_get_font = pdfmetrics.getFont
    original_register_font = pdfmetrics.registerFont
    original_set_font = Canvas.setFont

    def _get_font(name: str) -> Any:
        if name == "STSong-Light":
            raise KeyError(name)
        return original_get_font(name)

    def _register_font(font: Any) -> None:
        font_name = getattr(font, "fontName", "")
        if font_name == "STSong-Light":
            registered_fonts.append(font_name)
            return None
        return original_register_font(font)

    def _set_font_spy(
        self: Canvas, psfontname: str, size: float, leading: float | None = None
    ) -> None:
        font_names.append(psfontname)
        fallback_name = "Helvetica" if psfontname == "STSong-Light" else psfontname
        original_set_font(self, fallback_name, size, leading)

    monkeypatch.setattr(pdfmetrics, "getFont", _get_font)
    monkeypatch.setattr(pdfmetrics, "registerFont", _register_font)
    monkeypatch.setattr(Canvas, "setFont", _set_font_spy)
    monkeypatch.setenv("STUDY_PDF_CJK_FONT_PATH", str(tmp_path / "missing-font.ttf"))

    pdf = DocExporter(_MinimalStore()).export(fmt="pdf", title="中文笔记")

    assert pdf.content.startswith(b"%PDF")
    assert font_names[0] == "STSong-Light"
    assert registered_fonts == ["STSong-Light"]
    assert not font_names[0].startswith("CJK-User-")
    assert "STUDY_PDF_CJK_FONT_PATH set but file not found" in caplog.text
    assert _pdf_safe_text("中文笔记") == "中文笔记"


def test_register_pdf_font_returns_string_and_env_var_controls_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics

    DocExporter._registered_pdf_fonts.clear()
    exporter = DocExporter(_MinimalStore())
    registered: list[str] = []
    ttfont_paths: list[str] = []

    class _Face:
        charToGlyph = _sample_cjk_cmap()

    class _TTFont:
        def __init__(self, name: str, path: str) -> None:
            self.fontName = name
            self.face = _Face()
            ttfont_paths.append(path)

    def _register_font(font: Any) -> None:
        registered.append(font.fontName)

    monkeypatch.setattr("reportlab.pdfbase.ttfonts.TTFont", _TTFont)
    monkeypatch.setattr(pdfmetrics, "registerFont", _register_font)
    monkeypatch.setattr(
        pdfmetrics, "getFont", lambda name: (_ for _ in ()).throw(KeyError(name))
    )

    monkeypatch.delenv("STUDY_PDF_CJK_FONT_PATH", raising=False)
    assert exporter._register_pdf_font() == "STSong-Light"

    font_path = tmp_path / "env-font.ttf"
    font_path.write_bytes(b"fake")
    monkeypatch.setenv("STUDY_PDF_CJK_FONT_PATH", str(font_path))

    font_name = exporter._register_pdf_font()

    assert font_name.startswith("CJK-User-")
    assert ttfont_paths == [str(font_path)]
    assert registered[-1] == font_name


def test_register_pdf_font_uses_distinct_cached_user_font_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics

    DocExporter._registered_pdf_fonts.clear()
    registered: list[str] = []

    class _Face:
        charToGlyph = _sample_cjk_cmap()

    class _TTFont:
        def __init__(self, name: str, path: str) -> None:
            self.fontName = name
            self.face = _Face()

    def _register_font(font: Any) -> None:
        registered.append(font.fontName)

    monkeypatch.setattr("reportlab.pdfbase.ttfonts.TTFont", _TTFont)
    monkeypatch.setattr(pdfmetrics, "registerFont", _register_font)
    monkeypatch.setattr(
        pdfmetrics, "getFont", lambda name: (_ for _ in ()).throw(KeyError(name))
    )

    first_path = tmp_path / "font-a.ttf"
    second_path = tmp_path / "font-b.ttf"
    first_path.write_bytes(b"fake")
    second_path.write_bytes(b"fake")
    exporter = DocExporter(_MinimalStore())

    monkeypatch.setenv("STUDY_PDF_CJK_FONT_PATH", str(first_path))
    first_name = exporter._register_pdf_font()
    second_call_name = exporter._register_pdf_font()
    monkeypatch.setenv("STUDY_PDF_CJK_FONT_PATH", str(second_path))
    second_name = exporter._register_pdf_font()

    assert first_name == second_call_name
    assert first_name != second_name
    assert registered == [first_name, second_name]


def test_register_pdf_font_falls_back_when_user_font_lacks_cjk_glyphs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics

    DocExporter._registered_pdf_fonts.clear()
    registered: list[str] = []

    class _Face:
        charToGlyph = {ord("A"): 1}

    class _TTFont:
        def __init__(self, name: str, path: str) -> None:
            self.fontName = name
            self.face = _Face()

    font_path = tmp_path / "latin.ttf"
    font_path.write_bytes(b"fake")
    monkeypatch.setenv("STUDY_PDF_CJK_FONT_PATH", str(font_path))
    monkeypatch.setattr("reportlab.pdfbase.ttfonts.TTFont", _TTFont)
    monkeypatch.setattr(
        pdfmetrics, "getFont", lambda name: (_ for _ in ()).throw(KeyError(name))
    )
    monkeypatch.setattr(
        pdfmetrics, "registerFont", lambda font: registered.append(font.fontName)
    )

    font_name = DocExporter(_MinimalStore())._register_pdf_font()

    assert font_name == "STSong-Light"
    assert registered == ["STSong-Light"]
    assert "does not expose common CJK glyphs" in caplog.text


def test_register_pdf_font_reuses_user_font_with_partial_cjk_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics

    DocExporter._registered_pdf_fonts.clear()
    registered: list[str] = []

    class _Face:
        charToGlyph = {ord(_PDF_CJK_SAMPLE[0]): 1}

    class _TTFont:
        def __init__(self, name: str, path: str) -> None:
            self.fontName = name
            self.face = _Face()

    font_path = tmp_path / "partial-cjk.ttf"
    font_path.write_bytes(b"fake")
    monkeypatch.setenv("STUDY_PDF_CJK_FONT_PATH", str(font_path))
    monkeypatch.setattr("reportlab.pdfbase.ttfonts.TTFont", _TTFont)
    monkeypatch.setattr(
        pdfmetrics, "getFont", lambda name: (_ for _ in ()).throw(KeyError(name))
    )
    monkeypatch.setattr(
        pdfmetrics, "registerFont", lambda font: registered.append(font.fontName)
    )

    with caplog.at_level("INFO"):
        font_name = DocExporter(_MinimalStore())._register_pdf_font()

    assert font_name.startswith("CJK-User-")
    assert registered == [font_name]
    assert "STUDY_PDF_CJK_FONT_PATH font from" in caplog.text
    assert "missing _PDF_CJK_SAMPLE glyphs" in caplog.text
    assert _PDF_CJK_SAMPLE[1:] in caplog.text


def test_register_pdf_font_rejects_notdef_cjk_glyphs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics

    DocExporter._registered_pdf_fonts.clear()
    registered: list[str] = []

    class _Face:
        charToGlyph = {ord(char): 0 for char in _PDF_CJK_SAMPLE}

    class _TTFont:
        def __init__(self, name: str, path: str) -> None:
            self.fontName = name
            self.face = _Face()

    font_path = tmp_path / "notdef-cjk.ttf"
    font_path.write_bytes(b"fake")
    monkeypatch.setenv("STUDY_PDF_CJK_FONT_PATH", str(font_path))
    monkeypatch.setattr("reportlab.pdfbase.ttfonts.TTFont", _TTFont)
    monkeypatch.setattr(
        pdfmetrics, "getFont", lambda name: (_ for _ in ()).throw(KeyError(name))
    )
    monkeypatch.setattr(
        pdfmetrics, "registerFont", lambda font: registered.append(font.fontName)
    )

    font_name = DocExporter(_MinimalStore())._register_pdf_font()

    assert font_name == "STSong-Light"
    assert registered == ["STSong-Light"]
    assert "does not expose common CJK glyphs" in caplog.text


def test_register_pdf_font_does_not_cache_user_font_when_registration_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics

    DocExporter._registered_pdf_fonts.clear()

    class _Face:
        charToGlyph = _sample_cjk_cmap()

    class _TTFont:
        def __init__(self, name: str, path: str) -> None:
            self.fontName = name
            self.face = _Face()

    def _get_font(name: str) -> Any:
        if name == "STSong-Light":
            return object()
        raise KeyError(name)

    def _register_font(font: Any) -> None:
        if str(getattr(font, "fontName", "")).startswith("CJK-User-"):
            raise RuntimeError("user font registration failed")

    font_path = tmp_path / "cjk.ttf"
    font_path.write_bytes(b"fake")
    monkeypatch.setenv("STUDY_PDF_CJK_FONT_PATH", str(font_path))
    monkeypatch.setattr("reportlab.pdfbase.ttfonts.TTFont", _TTFont)
    monkeypatch.setattr(pdfmetrics, "getFont", _get_font)
    monkeypatch.setattr(pdfmetrics, "registerFont", _register_font)

    with caplog.at_level("INFO"):
        font_name = DocExporter(_MinimalStore())._register_pdf_font()

    assert font_name == "STSong-Light"
    assert not any(name.startswith("CJK-User-") for name in DocExporter._registered_pdf_fonts)
    assert "PDF CJK font registered from" not in caplog.text
    assert "User CJK font registration failed" in caplog.text


def test_register_pdf_font_falls_back_to_helvetica_when_pdfmetrics_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("reportlab")
    DocExporter._registered_pdf_fonts.clear()
    import builtins

    real_import = builtins.__import__

    def _blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "reportlab.pdfbase" and args and "pdfmetrics" in (args[2] or ()):
            raise ImportError("pdfmetrics unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    assert DocExporter(_MinimalStore())._register_pdf_font() == "Helvetica"


def test_register_pdf_font_falls_back_to_helvetica_when_cid_font_registration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics

    DocExporter._registered_pdf_fonts.clear()
    monkeypatch.delenv("STUDY_PDF_CJK_FONT_PATH", raising=False)
    monkeypatch.setattr(
        pdfmetrics, "getFont", lambda name: (_ for _ in ()).throw(KeyError(name))
    )
    monkeypatch.setattr(
        pdfmetrics,
        "registerFont",
        lambda font: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert DocExporter(_MinimalStore())._register_pdf_font() == "Helvetica"


def test_doc_exporter_rejects_store_without_required_methods() -> None:
    with pytest.raises(TypeError, match="missing required methods"):
        DocExporter(object())  # type: ignore[arg-type]


def test_xmind_export_requires_explicit_enable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="XMind export is disabled"):
            DocExporter(store, config=DocExportConfig(xmind_enabled=False)).export(
                fmt="xmind"
            )
    finally:
        store.close()


def test_preview_export_uses_markdown_metadata_for_non_markdown_format(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        exported = DocExporter(
            store, config=DocExportConfig(xmind_enabled=True)
        ).export(
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
