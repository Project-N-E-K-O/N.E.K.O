from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from utils.avatar_document_parser import (
    MAX_EXTRACTED_CHARS,
    AvatarDocumentParseError,
    parse_avatar_document,
)


PARSER_SOURCE_PATH = Path(__file__).resolve().parents[2] / "utils" / "avatar_document_parser.py"
CONTENT_TYPES_XML = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _zip_bytes(members: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value.encode("utf-8") if isinstance(value, str) else value)
    return buffer.getvalue()


def _docx_bytes(text: str, extra_members: dict[str, str | bytes] | None = None) -> bytes:
    members: dict[str, str | bytes] = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "word/document.xml": (
            f'<w:document xmlns:w="{WORD_NS}"><w:body><w:p><w:r>'
            f"<w:t>{text}</w:t>"
            "</w:r></w:p></w:body></w:document>"
        ),
    }
    members.update(extra_members or {})
    return _zip_bytes(members)


def _word_part_xml(root_name: str, text: str) -> str:
    return (
        f'<w:{root_name} xmlns:w="{WORD_NS}"><w:p><w:r>'
        f"<w:t>{text}</w:t>"
        f"</w:r></w:p></w:{root_name}>"
    )


def _word_document_xml(body: str) -> str:
    return f'<w:document xmlns:w="{WORD_NS}"><w:body>{body}</w:body></w:document>'


def _word_paragraph_xml(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _xlsx_bytes(text: str, sheet_count: int = 1, row_count: int = 1) -> bytes:
    sheets = "".join(
        f'<sheet name="Sheet {index}" sheetId="{index}" r:id="rId{index}"/>'
        for index in range(1, sheet_count + 1)
    )
    rels = "".join(
        f'<Relationship Id="rId{index}" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    members: dict[str, str | bytes] = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "xl/workbook.xml": (
            f'<workbook xmlns="{SPREADSHEET_NS}" xmlns:r="{OFFICE_REL_NS}">'
            f"<sheets>{sheets}</sheets>"
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{PACKAGE_REL_NS}">{rels}</Relationships>',
        "xl/sharedStrings.xml": f'<sst xmlns="{SPREADSHEET_NS}"><si><t>{text}</t></si></sst>',
    }
    for index in range(1, sheet_count + 1):
        rows = "".join(
            f'<row r="{row_index}"><c r="A{row_index}" t="s"><v>0</v></c>'
            f'<c r="B{row_index}"><v>{42 if row_count == 1 else row_index}</v></c></row>'
            for row_index in range(1, row_count + 1)
        )
        members[f"xl/worksheets/sheet{index}.xml"] = (
            f'<worksheet xmlns="{SPREADSHEET_NS}"><sheetData>{rows}</sheetData></worksheet>'
        )
    return _zip_bytes(members)


def _pptx_bytes(slide_text: str, notes_text: str = "") -> bytes:
    members: dict[str, str | bytes] = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "ppt/presentation.xml": "<p:presentation xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"/>",
        "ppt/slides/slide1.xml": f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>{slide_text}</a:t></p:sld>',
    }
    if notes_text:
        members["ppt/notesSlides/notesSlide1.xml"] = (
            f'<p:notes xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>{notes_text}</a:t></p:notes>'
        )
    return _zip_bytes(members)


def _pptx_with_slide_xml(slide_xml: str) -> bytes:
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "ppt/presentation.xml": "<p:presentation xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"/>",
        "ppt/slides/slide1.xml": slide_xml,
    })


def _many_pptx_bytes(slide_count: int) -> bytes:
    members: dict[str, str | bytes] = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "ppt/presentation.xml": "<p:presentation xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"/>",
    }
    for index in range(1, slide_count + 1):
        members[f"ppt/slides/slide{index}.xml"] = (
            f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Slide body {index}</a:t></p:sld>'
        )
    return _zip_bytes(members)


def _many_pptx_notes_bytes(note_count: int) -> bytes:
    members: dict[str, str | bytes] = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "ppt/presentation.xml": "<p:presentation xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"/>",
        "ppt/slides/slide1.xml": f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Slide body</a:t></p:sld>',
    }
    for index in range(1, note_count + 1):
        members[f"ppt/notesSlides/notesSlide{index}.xml"] = (
            f'<p:notes xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Notes body {index}</a:t></p:notes>'
        )
    return _zip_bytes(members)


def _pdf_bytes(text: str = "Hello PDF") -> bytes:
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    return _make_pdf(objects)


def _many_pdf_bytes(page_count: int) -> bytes:
    page_refs = []
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index in range(1, page_count + 1):
        page_object_id = len(objects) + 1
        content_object_id = page_object_id + 1
        page_refs.append(f"{page_object_id} 0 R")
        stream = f"BT /F1 24 Tf 72 720 Td (PDF page {index}) Tj ET".encode("ascii")
        objects.append(
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
                + str(content_object_id).encode("ascii")
                + b" 0 R >>"
            )
        )
        objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {page_count} >>".encode("ascii")
    return _make_pdf(objects)


def _blank_pdf_bytes() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    return _make_pdf(objects)


def _make_pdf(objects: list[bytes]) -> bytes:
    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(output)


def _assert_parse_error(filename: str, data: bytes, code: str) -> None:
    with pytest.raises(AvatarDocumentParseError) as exc_info:
        parse_avatar_document(filename, "", data)
    assert exc_info.value.code == code


@pytest.mark.unit
def test_parse_supported_document_text_formats():
    cases = [
        ("sample.docx", _docx_bytes("Docx hello"), "docx", "Docx hello"),
        ("sample.xlsx", _xlsx_bytes("Xlsx hello"), "xlsx", "Xlsx hello\t42"),
        ("sample.pptx", _pptx_bytes("Slide hello", "Speaker notes"), "pptx", "Speaker notes"),
        ("sample.pdf", _pdf_bytes("Pdf hello"), "pdf", "Pdf hello"),
    ]

    for filename, data, document_type, expected_text in cases:
        parsed = parse_avatar_document(filename, "", data)

        assert parsed["document_type"] == document_type
        assert expected_text in parsed["content"]
        assert parsed["chars"] == len(parsed["content"])
        assert parsed["truncated"] is False


@pytest.mark.unit
def test_deduplicates_repeated_docx_long_text_parts():
    repeated = "Steam GitHub B站 QQ群 Discord 猫娘计划渠道说明 " * 8

    parsed = parse_avatar_document(
        "duplicated.docx",
        "",
        _docx_bytes(
            repeated,
            {
                "word/header1.xml": _word_part_xml("hdr", repeated),
                "word/footer1.xml": _word_part_xml("ftr", repeated),
            },
        ),
    )

    assert parsed["content"].count(repeated.strip()) == 1
    assert "# Header" not in parsed["content"]
    assert "# Footer" not in parsed["content"]


@pytest.mark.unit
def test_deduplicates_nested_docx_paragraph_text():
    repeated = "卡面图层 自定义贴纸 导出格式说明 " * 8
    document_xml = _word_document_xml(
        "<w:p>"
        "<w:r><w:t>文档开头</w:t></w:r>"
        "<w:r><w:txbxContent>"
        f"{_word_paragraph_xml(repeated)}"
        "</w:txbxContent></w:r>"
        "</w:p>"
    )

    parsed = parse_avatar_document(
        "nested.docx",
        "",
        _docx_bytes("placeholder", {"word/document.xml": document_xml}),
    )

    assert "文档开头" in parsed["content"]
    assert parsed["content"].count(repeated.strip()) == 1


@pytest.mark.unit
def test_ignores_docx_alternate_content_fallback_text():
    visible = "Steam: https://store.steampowered.com/app/4099310/__NEKO/"
    document_xml = _word_document_xml(
        f'<mc:AlternateContent xmlns:mc="{MC_NS}">'
        "<mc:Choice Requires=\"wps\">"
        f"{_word_paragraph_xml(visible)}"
        "</mc:Choice>"
        "<mc:Fallback>"
        f"{_word_paragraph_xml(visible)}"
        "</mc:Fallback>"
        "</mc:AlternateContent>"
        + _word_paragraph_xml("后续新增的卡面图层与自定义贴纸说明")
    )

    parsed = parse_avatar_document(
        "alternate-content.docx",
        "",
        _docx_bytes("placeholder", {"word/document.xml": document_xml}),
    )

    assert parsed["content"].count(visible) == 1
    assert "后续新增的卡面图层与自定义贴纸说明" in parsed["content"]


@pytest.mark.unit
def test_ignores_pptx_alternate_content_fallback_text():
    visible = "卡面图层 自定义贴纸 导出PNG和nekocfg"
    slide_xml = (
        f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}" xmlns:mc="{MC_NS}">'
        "<mc:AlternateContent>"
        "<mc:Choice Requires=\"p14\">"
        f"<a:t>{visible}</a:t>"
        "</mc:Choice>"
        "<mc:Fallback>"
        f"<a:t>{visible}</a:t>"
        "</mc:Fallback>"
        "</mc:AlternateContent>"
        "</p:sld>"
    )

    parsed = parse_avatar_document("alternate-content.pptx", "", _pptx_with_slide_xml(slide_xml))

    assert parsed["content"].count(visible) == 1


@pytest.mark.unit
def test_deduplicates_pptx_notes_that_repeat_slide_body():
    repeated = "日常使用 悬浮菜单操作 角色卡功能 模块说明 " * 8

    parsed = parse_avatar_document("duplicated.pptx", "", _pptx_bytes(repeated, repeated))

    assert parsed["content"].count(repeated.strip()) == 1
    assert "# Slide 1" in parsed["content"]
    assert "# Notes 1" not in parsed["content"]


@pytest.mark.unit
def test_rejects_legacy_macro_and_embedded_macro_office_documents():
    _assert_parse_error("legacy.doc", b"legacy", "legacy_office_unsupported")
    _assert_parse_error("macro.docm", b"PK\x03\x04", "macro_document_unsupported")
    _assert_parse_error(
        "embedded.docx",
        _docx_bytes("Safe text", {"word/vbaProject.bin": b"macro"}),
        "macro_document_unsupported",
    )


@pytest.mark.unit
def test_rejects_zip_path_traversal_and_xml_entities():
    _assert_parse_error(
        "unsafe.docx",
        _zip_bytes(
            {
                "[Content_Types].xml": CONTENT_TYPES_XML,
                "../evil.xml": "x",
                "word/document.xml": f'<w:document xmlns:w="{WORD_NS}"/>',
            }
        ),
        "invalid_zip_member",
    )
    _assert_parse_error(
        "entity.docx",
        _zip_bytes(
            {
                "[Content_Types].xml": CONTENT_TYPES_XML,
                "word/document.xml": (
                    '<!DOCTYPE foo [<!ENTITY x "boom">]>'
                    f'<w:document xmlns:w="{WORD_NS}"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>'
                ),
            }
        ),
        "xml_entity_unsupported",
    )


@pytest.mark.unit
def test_rejects_blank_pdf_and_garbled_extracted_text():
    _assert_parse_error("blank.pdf", _blank_pdf_bytes(), "no_readable_text")
    _assert_parse_error("garbled.docx", _docx_bytes("\ufffd" * 20), "garbled_text")


@pytest.mark.unit
def test_marks_extracted_text_truncated_when_document_exceeds_budget():
    parsed = parse_avatar_document("long.docx", "", _docx_bytes("A" * (MAX_EXTRACTED_CHARS + 200)))

    assert parsed["truncated"] is True
    assert parsed["chars"] == MAX_EXTRACTED_CHARS
    assert parsed["content"].startswith("# Document\n")
    assert parsed["content"].endswith("A")


@pytest.mark.unit
def test_marks_pdf_truncated_after_first_40_pages():
    parsed = parse_avatar_document("many.pdf", "", _many_pdf_bytes(41))

    assert parsed["document_type"] == "pdf"
    assert parsed["meta"]["pages"] == 41
    assert parsed["truncated"] is True
    assert "# Page 40" in parsed["content"]
    assert "PDF page 40" in parsed["content"]
    assert "# Page 41" not in parsed["content"]
    assert "PDF page 41" not in parsed["content"]


@pytest.mark.unit
def test_pdf_pages_are_not_materialized_before_limit():
    source = PARSER_SOURCE_PATH.read_text(encoding="utf-8")

    assert "list(reader.pages)" not in source
    assert "for index, page in enumerate(reader.pages, start=1):" in source


@pytest.mark.unit
def test_marks_xlsx_truncated_when_sheet_limit_is_exceeded():
    parsed = parse_avatar_document("many.xlsx", "", _xlsx_bytes("Shared", sheet_count=13))

    assert parsed["document_type"] == "xlsx"
    assert parsed["meta"]["sheets"] == 13
    assert parsed["truncated"] is True
    assert "# Sheet: Sheet 12" in parsed["content"]
    assert "# Sheet: Sheet 13" not in parsed["content"]


@pytest.mark.unit
def test_marks_xlsx_truncated_when_row_limit_is_exceeded():
    parsed = parse_avatar_document("many-rows.xlsx", "", _xlsx_bytes("Shared", row_count=805))

    assert parsed["document_type"] == "xlsx"
    assert parsed["truncated"] is True
    assert "Shared\t800" in parsed["content"]
    assert "Shared\t801" not in parsed["content"]
    assert "[Rows truncated]" in parsed["content"]


@pytest.mark.unit
def test_xlsx_rows_are_not_materialized_before_limit():
    source = PARSER_SOURCE_PATH.read_text(encoding="utf-8")

    assert "ET.iterparse(io.BytesIO(data), events=(\"end\",))" in source
    assert 'rows = root.findall(".//" + _SPREADSHEET_NS + "row")' not in source


@pytest.mark.unit
def test_marks_pptx_truncated_after_first_40_slides():
    parsed = parse_avatar_document("many.pptx", "", _many_pptx_bytes(41))

    assert parsed["document_type"] == "pptx"
    assert parsed["meta"]["slides"] == 41
    assert parsed["truncated"] is True
    assert "# Slide 40" in parsed["content"]
    assert "Slide body 40" in parsed["content"]
    assert "# Slide 41" not in parsed["content"]
    assert "Slide body 41" not in parsed["content"]


@pytest.mark.unit
def test_marks_pptx_truncated_after_first_40_notes():
    parsed = parse_avatar_document("many-notes.pptx", "", _many_pptx_notes_bytes(41))

    assert parsed["document_type"] == "pptx"
    assert parsed["meta"]["slides"] == 1
    assert parsed["truncated"] is True
    assert "# Notes 40" in parsed["content"]
    assert "Notes body 40" in parsed["content"]
    assert "# Notes 41" not in parsed["content"]
    assert "Notes body 41" not in parsed["content"]
