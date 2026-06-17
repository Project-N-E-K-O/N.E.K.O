from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from utils.document_parser import (
    MAX_EXTRACTED_CHARS,
    DocumentParseError,
    _validate_zip_member_name,
    parse_document,
)


PARSER_SOURCE_PATH = Path(__file__).resolve().parents[2] / "utils" / "document_parser.py"
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


def _zip_bytes_with_backslash_member(members: dict[str, str | bytes], name: str) -> bytes:
    data = _zip_bytes(members)
    return data.replace(name.encode("utf-8"), name.replace("/", "\\").encode("utf-8"))


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


def _docx_bytes_with_directory_entry(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        archive.writestr("word/", b"")
        archive.writestr(
            "word/document.xml",
            (
                f'<w:document xmlns:w="{WORD_NS}"><w:body><w:p><w:r>'
                f"<w:t>{text}</w:t>"
                "</w:r></w:p></w:body></w:document>"
            ),
        )
    return buffer.getvalue()


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


def _docx_with_referenced_header_footer() -> bytes:
    header_rel_type = f"{OFFICE_REL_NS}/header"
    footer_rel_type = f"{OFFICE_REL_NS}/footer"
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "word/document.xml": (
            f'<w:document xmlns:w="{WORD_NS}" xmlns:r="{OFFICE_REL_NS}"><w:body>'
            f"{_word_paragraph_xml('Visible body')}"
            '<w:sectPr><w:headerReference r:id="rIdHeader"/>'
            '<w:footerReference r:id="rIdFooter"/></w:sectPr>'
            "</w:body></w:document>"
        ),
        "word/_rels/document.xml.rels": (
            f'<Relationships xmlns="{PACKAGE_REL_NS}">'
            f'<Relationship Id="rIdHeader" Type="{header_rel_type}" Target="header1.xml"/>'
            f'<Relationship Id="rIdFooter" Type="{footer_rel_type}" Target="footer1.xml"/>'
            f'<Relationship Id="rIdStale" Type="{header_rel_type}" Target="header2.xml"/>'
            "</Relationships>"
        ),
        "word/header1.xml": _word_part_xml("hdr", "Visible header"),
        "word/footer1.xml": _word_part_xml("ftr", "Visible footer"),
        "word/header2.xml": _word_part_xml("hdr", "Stale hidden header"),
    })


def _docx_with_hidden_run() -> bytes:
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "word/document.xml": (
            f'<w:document xmlns:w="{WORD_NS}"><w:body><w:p>'
            "<w:r><w:t>Visible text</w:t></w:r>"
            "<w:r><w:rPr><w:vanish/></w:rPr><w:t>Hidden draft</w:t></w:r>"
            "</w:p></w:body></w:document>"
        ),
    })


def _docx_with_vanish_disabled_run() -> bytes:
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "word/document.xml": (
            f'<w:document xmlns:w="{WORD_NS}"><w:body><w:p>'
            '<w:r><w:rPr><w:vanish w:val="0"/></w:rPr><w:t>Explicit visible zero</w:t></w:r>'
            '<w:r><w:rPr><w:vanish w:val="false"/></w:rPr><w:t>Explicit visible false</w:t></w:r>'
            '<w:r><w:rPr><w:vanish w:val="off"/></w:rPr><w:t>Explicit visible off</w:t></w:r>'
            "<w:r><w:rPr><w:vanish/></w:rPr><w:t>Hidden default</w:t></w:r>"
            "</w:p></w:body></w:document>"
        ),
    })


def _docx_with_referenced_notes() -> bytes:
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "word/document.xml": (
            f'<w:document xmlns:w="{WORD_NS}"><w:body>'
            "<w:p><w:r><w:t>Body text</w:t></w:r>"
            '<w:r><w:footnoteReference w:id="2"/></w:r>'
            '<w:r><w:endnoteReference w:id="3"/></w:r>'
            "</w:p></w:body></w:document>"
        ),
        "word/footnotes.xml": (
            f'<w:footnotes xmlns:w="{WORD_NS}">'
            '<w:footnote w:id="2"><w:p><w:r><w:t>Visible footnote</w:t></w:r></w:p></w:footnote>'
            '<w:footnote w:id="9"><w:p><w:r><w:t>Stale footnote</w:t></w:r></w:p></w:footnote>'
            "</w:footnotes>"
        ),
        "word/endnotes.xml": (
            f'<w:endnotes xmlns:w="{WORD_NS}">'
            '<w:endnote w:id="3"><w:p><w:r><w:t>Visible endnote</w:t></w:r></w:p></w:endnote>'
            '<w:endnote w:id="8"><w:p><w:r><w:t>Stale endnote</w:t></w:r></w:p></w:endnote>'
            "</w:endnotes>"
        ),
    })


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


def _xlsx_sparse_columns_bytes() -> bytes:
    rows = (
        '<row r="1">'
        '<c r="A1" t="inlineStr"><is><t>Name</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>Score</t></is></c>'
        "</row>"
    )
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "xl/workbook.xml": (
            f'<workbook xmlns="{SPREADSHEET_NS}" xmlns:r="{OFFICE_REL_NS}">'
            '<sheets><sheet name="Sparse" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{PACKAGE_REL_NS}"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{SPREADSHEET_NS}"><sheetData>{rows}</sheetData></worksheet>',
    })


def _xlsx_with_hidden_sheet_and_row() -> bytes:
    visible_rows = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Visible row</t></is></c></row>'
        '<row r="2" hidden="1"><c r="A2" t="inlineStr"><is><t>Hidden row</t></is></c></row>'
    )
    hidden_rows = '<row r="1"><c r="A1" t="inlineStr"><is><t>Hidden sheet</t></is></c></row>'
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "xl/workbook.xml": (
            f'<workbook xmlns="{SPREADSHEET_NS}" xmlns:r="{OFFICE_REL_NS}">'
            "<sheets>"
            '<sheet name="Visible" sheetId="1" r:id="rId1"/>'
            '<sheet name="Hidden" sheetId="2" state="hidden" r:id="rId2"/>'
            "</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<Relationships xmlns="{PACKAGE_REL_NS}">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{SPREADSHEET_NS}"><sheetData>{visible_rows}</sheetData></worksheet>',
        "xl/worksheets/sheet2.xml": f'<worksheet xmlns="{SPREADSHEET_NS}"><sheetData>{hidden_rows}</sheetData></worksheet>',
    })


def _xlsx_with_hidden_column() -> bytes:
    rows = (
        '<row r="1">'
        '<c r="A1" t="inlineStr"><is><t>Visible A</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Hidden B</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>Visible C</t></is></c>'
        "</row>"
    )
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "xl/workbook.xml": (
            f'<workbook xmlns="{SPREADSHEET_NS}" xmlns:r="{OFFICE_REL_NS}">'
            '<sheets><sheet name="Visible" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{PACKAGE_REL_NS}"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": (
            f'<worksheet xmlns="{SPREADSHEET_NS}">'
            '<cols><col min="2" max="2" hidden="1"/></cols>'
            f"<sheetData>{rows}</sheetData>"
            "</worksheet>"
        ),
    })


def _pptx_bytes(slide_text: str, notes_text: str = "") -> bytes:
    members: dict[str, str | bytes] = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "ppt/presentation.xml": (
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            f'xmlns:r="{OFFICE_REL_NS}"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>'
        ),
        "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{PACKAGE_REL_NS}"><Relationship Id="rId1" Target="slides/slide1.xml"/></Relationships>',
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


def _pptx_with_many_visible_and_hidden_tail(slide_count: int) -> bytes:
    slide_ids = "".join(
        f'<p:sldId id="{255 + index}" r:id="rId{index}"/>'
        for index in range(1, slide_count + 1)
    )
    rels = "".join(
        f'<Relationship Id="rId{index}" Target="slides/slide{index}.xml"/>'
        for index in range(1, slide_count + 1)
    )
    members: dict[str, str | bytes] = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "ppt/presentation.xml": (
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            f'xmlns:r="{OFFICE_REL_NS}"><p:sldIdLst>{slide_ids}</p:sldIdLst></p:presentation>'
        ),
        "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{PACKAGE_REL_NS}">{rels}</Relationships>',
    }
    for index in range(1, slide_count + 1):
        hidden = ' show="0"' if index > 41 else ""
        members[f"ppt/slides/slide{index}.xml"] = (
            f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}"{hidden}><a:t>Slide body {index}</a:t></p:sld>'
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


def _pptx_with_notes_on_skipped_slide(slide_count: int) -> bytes:
    notes_rel_type = f"{OFFICE_REL_NS}/notesSlide"
    slide_ids = "".join(
        f'<p:sldId id="{255 + index}" r:id="rId{index}"/>'
        for index in range(1, slide_count + 1)
    )
    rels = "".join(
        f'<Relationship Id="rId{index}" Target="slides/slide{index}.xml"/>'
        for index in range(1, slide_count + 1)
    )
    members: dict[str, str | bytes] = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "ppt/presentation.xml": (
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            f'xmlns:r="{OFFICE_REL_NS}"><p:sldIdLst>{slide_ids}</p:sldIdLst></p:presentation>'
        ),
        "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{PACKAGE_REL_NS}">{rels}</Relationships>',
    }
    for index in range(1, slide_count + 1):
        members[f"ppt/slides/slide{index}.xml"] = (
            f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Slide body {index}</a:t></p:sld>'
        )
    members[f"ppt/slides/_rels/slide{slide_count}.xml.rels"] = (
        f'<Relationships xmlns="{PACKAGE_REL_NS}">'
        f'<Relationship Id="rIdNotes" Type="{notes_rel_type}" Target="../notesSlides/notesSlide{slide_count}.xml"/>'
        "</Relationships>"
    )
    members[f"ppt/notesSlides/notesSlide{slide_count}.xml"] = (
        f'<p:notes xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Skipped slide notes</a:t></p:notes>'
    )
    return _zip_bytes(members)


def _pptx_with_hidden_slide(show_value: str = "0") -> bytes:
    notes_rel_type = f"{OFFICE_REL_NS}/notesSlide"
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "ppt/presentation.xml": (
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            f'xmlns:r="{OFFICE_REL_NS}"><p:sldIdLst>'
            '<p:sldId id="256" r:id="rId1"/>'
            '<p:sldId id="257" r:id="rId2"/>'
            "</p:sldIdLst></p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": (
            f'<Relationships xmlns="{PACKAGE_REL_NS}">'
            '<Relationship Id="rId1" Target="slides/slide1.xml"/>'
            '<Relationship Id="rId2" Target="slides/slide2.xml"/>'
            "</Relationships>"
        ),
        "ppt/slides/slide1.xml": f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Visible slide</a:t></p:sld>',
        "ppt/slides/slide2.xml": f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}" show="{show_value}"><a:t>Hidden slide</a:t></p:sld>',
        "ppt/slides/_rels/slide2.xml.rels": (
            f'<Relationships xmlns="{PACKAGE_REL_NS}">'
            f'<Relationship Id="rIdNotes" Type="{notes_rel_type}" Target="../notesSlides/notesSlide2.xml"/>'
            "</Relationships>"
        ),
        "ppt/notesSlides/notesSlide2.xml": f'<p:notes xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Hidden slide notes</a:t></p:notes>',
    })


def _reordered_pptx_bytes() -> bytes:
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "ppt/presentation.xml": (
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            f'xmlns:r="{OFFICE_REL_NS}"><p:sldIdLst>'
            '<p:sldId id="256" r:id="rId2"/>'
            '<p:sldId id="257" r:id="rId1"/>'
            "</p:sldIdLst></p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": (
            f'<Relationships xmlns="{PACKAGE_REL_NS}">'
            '<Relationship Id="rId1" Target="slides/slide1.xml"/>'
            '<Relationship Id="rId2" Target="slides/slide2.xml"/>'
            "</Relationships>"
        ),
        "ppt/slides/slide1.xml": f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Created first</a:t></p:sld>',
        "ppt/slides/slide2.xml": f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Presented first</a:t></p:sld>',
    })


def _reordered_pptx_notes_bytes() -> bytes:
    notes_rel_type = f"{OFFICE_REL_NS}/notesSlide"
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "ppt/presentation.xml": (
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            f'xmlns:r="{OFFICE_REL_NS}"><p:sldIdLst>'
            '<p:sldId id="256" r:id="rId2"/>'
            '<p:sldId id="257" r:id="rId1"/>'
            "</p:sldIdLst></p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": (
            f'<Relationships xmlns="{PACKAGE_REL_NS}">'
            '<Relationship Id="rId1" Target="slides/slide1.xml"/>'
            '<Relationship Id="rId2" Target="slides/slide2.xml"/>'
            "</Relationships>"
        ),
        "ppt/slides/slide1.xml": f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Created first</a:t></p:sld>',
        "ppt/slides/_rels/slide1.xml.rels": (
            f'<Relationships xmlns="{PACKAGE_REL_NS}">'
            f'<Relationship Id="rIdNotes1" Type="{notes_rel_type}" Target="../notesSlides/notesSlide1.xml"/>'
            "</Relationships>"
        ),
        "ppt/notesSlides/notesSlide1.xml": f'<p:notes xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Created first notes</a:t></p:notes>',
        "ppt/slides/slide2.xml": f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Presented first</a:t></p:sld>',
        "ppt/slides/_rels/slide2.xml.rels": (
            f'<Relationships xmlns="{PACKAGE_REL_NS}">'
            f'<Relationship Id="rIdNotes2" Type="{notes_rel_type}" Target="../notesSlides/notesSlide2.xml"/>'
            "</Relationships>"
        ),
        "ppt/notesSlides/notesSlide2.xml": f'<p:notes xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Presented first notes</a:t></p:notes>',
        "ppt/notesSlides/notesSlide99.xml": f'<p:notes xmlns:p="p" xmlns:a="{DRAWING_NS}"><a:t>Stale unreferenced notes</a:t></p:notes>',
    })


def _xlsx_with_empty_rows_before_data(empty_row_count: int) -> bytes:
    rows = "".join(f'<row r="{index}"/>' for index in range(1, empty_row_count + 1))
    rows += (
        f'<row r="{empty_row_count + 1}">'
        '<c r="A1" t="s"><v>0</v></c>'
        '<c r="B1"><v>7</v></c>'
        "</row>"
    )
    return _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "xl/workbook.xml": (
            f'<workbook xmlns="{SPREADSHEET_NS}" xmlns:r="{OFFICE_REL_NS}">'
            '<sheets><sheet name="Sparse" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{PACKAGE_REL_NS}"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst xmlns="{SPREADSHEET_NS}"><si><t>Real data</t></si></sst>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{SPREADSHEET_NS}"><sheetData>{rows}</sheetData></worksheet>',
    })


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
    with pytest.raises(DocumentParseError) as exc_info:
        parse_document(filename, "", data)
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
        parsed = parse_document(filename, "", data)

        assert parsed["document_type"] == document_type
        assert expected_text in parsed["content"]
        assert parsed["chars"] == len(parsed["content"])
        assert parsed["truncated"] is False


@pytest.mark.unit
def test_deduplicates_repeated_docx_long_text_parts():
    repeated = "Steam GitHub B站 QQ群 Discord 猫娘计划渠道说明 " * 8

    parsed = parse_document(
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
def test_docx_headers_and_footers_follow_document_relationships():
    parsed = parse_document("referenced.docx", "", _docx_with_referenced_header_footer())

    assert "Visible body" in parsed["content"]
    assert "Visible header" in parsed["content"]
    assert "Visible footer" in parsed["content"]
    assert "Stale hidden header" not in parsed["content"]


@pytest.mark.unit
def test_docx_skips_hidden_runs():
    parsed = parse_document("hidden-run.docx", "", _docx_with_hidden_run())

    assert "Visible text" in parsed["content"]
    assert "Hidden draft" not in parsed["content"]


@pytest.mark.unit
def test_docx_preserves_runs_when_vanish_is_disabled():
    parsed = parse_document("visible-run.docx", "", _docx_with_vanish_disabled_run())

    assert "Explicit visible zero" in parsed["content"]
    assert "Explicit visible false" in parsed["content"]
    assert "Explicit visible off" in parsed["content"]
    assert "Hidden default" not in parsed["content"]


@pytest.mark.unit
def test_docx_notes_follow_document_references():
    parsed = parse_document("referenced-notes.docx", "", _docx_with_referenced_notes())

    assert "Visible footnote" in parsed["content"]
    assert "Visible endnote" in parsed["content"]
    assert "Stale footnote" not in parsed["content"]
    assert "Stale endnote" not in parsed["content"]


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

    parsed = parse_document(
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

    parsed = parse_document(
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

    parsed = parse_document("alternate-content.pptx", "", _pptx_with_slide_xml(slide_xml))

    assert parsed["content"].count(visible) == 1


@pytest.mark.unit
def test_deduplicates_pptx_notes_that_repeat_slide_body():
    repeated = "日常使用 悬浮菜单操作 角色卡功能 模块说明 " * 8

    parsed = parse_document("duplicated.pptx", "", _pptx_bytes(repeated, repeated))

    assert parsed["content"].count(repeated.strip()) == 1
    assert "# Slide 1" in parsed["content"]
    assert "# Notes 1" not in parsed["content"]


@pytest.mark.unit
def test_pptx_slides_follow_presentation_order():
    parsed = parse_document("reordered.pptx", "", _reordered_pptx_bytes())

    assert parsed["content"].index("Presented first") < parsed["content"].index("Created first")


@pytest.mark.unit
def test_pptx_notes_follow_slide_relationship_order():
    parsed = parse_document("reordered-notes.pptx", "", _reordered_pptx_notes_bytes())

    assert parsed["content"].index("Presented first notes") < parsed["content"].index("Created first notes")
    assert "Stale unreferenced notes" not in parsed["content"]


@pytest.mark.unit
def test_pptx_preserves_runs_within_paragraphs():
    slide_xml = (
        f'<p:sld xmlns:p="p" xmlns:a="{DRAWING_NS}">'
        "<a:p>"
        "<a:r><a:t>Hel</a:t></a:r>"
        "<a:r><a:t>lo</a:t></a:r>"
        "</a:p>"
        "<a:p>"
        "<a:r><a:t>Second line</a:t></a:r>"
        "</a:p>"
        "</p:sld>"
    )

    parsed = parse_document("runs.pptx", "", _pptx_with_slide_xml(slide_xml))

    assert "Hello\nSecond line" in parsed["content"]
    assert "Hel\nlo" not in parsed["content"]


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
        "unsafe-backslash.docx",
        _zip_bytes(
            {
                "[Content_Types].xml": CONTENT_TYPES_XML,
                "..\\evil.xml": "x",
                "word/document.xml": f'<w:document xmlns:w="{WORD_NS}"/>',
            }
        ),
        "invalid_zip_member",
    )
    _assert_parse_error(
        "unsafe-dot-segment.docx",
        _zip_bytes(
            {
                "[Content_Types].xml": CONTENT_TYPES_XML,
                "./word/vbaProject.bin": b"macro",
                "word/document.xml": f'<w:document xmlns:w="{WORD_NS}"/>',
            }
        ),
        "invalid_zip_member",
    )
    _assert_parse_error(
        "macro-backslash.docx",
        _zip_bytes_with_backslash_member(
            {
                "[Content_Types].xml": CONTENT_TYPES_XML,
                "word/document.xml": f'<w:document xmlns:w="{WORD_NS}"><w:body><w:p><w:r><w:t>Safe text</w:t></w:r></w:p></w:body></w:document>',
                "word/vbaProject.bin": b"macro",
            },
            "word/vbaProject.bin",
        ),
        "macro_document_unsupported",
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
    _assert_parse_error(
        "utf16-entity.docx",
        _zip_bytes(
            {
                "[Content_Types].xml": CONTENT_TYPES_XML,
                "word/document.xml": (
                    '<!DOCTYPE foo [<!ENTITY x "boom">]>'
                    f'<w:document xmlns:w="{WORD_NS}"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>'
                ).encode("utf-16"),
            }
        ),
        "xml_entity_unsupported",
    )


@pytest.mark.unit
def test_rejects_raw_backslash_zip_member_names():
    with pytest.raises(DocumentParseError) as exc_info:
        _validate_zip_member_name("..\\evil.xml")

    assert exc_info.value.code == "invalid_zip_member"


@pytest.mark.unit
def test_allows_ooxml_zip_directory_entries():
    parsed = parse_document("directory-entry.docx", "", _docx_bytes_with_directory_entry("Directory ok"))

    assert "Directory ok" in parsed["content"]


@pytest.mark.unit
def test_rejects_blank_pdf_and_garbled_extracted_text():
    _assert_parse_error("blank.pdf", _blank_pdf_bytes(), "no_readable_text")
    _assert_parse_error("garbled.docx", _docx_bytes("\ufffd" * 20), "garbled_text")


@pytest.mark.unit
def test_marks_extracted_text_truncated_when_document_exceeds_budget():
    parsed = parse_document("long.docx", "", _docx_bytes("A" * (MAX_EXTRACTED_CHARS + 200)))

    assert parsed["truncated"] is True
    assert parsed["chars"] == MAX_EXTRACTED_CHARS
    assert parsed["content"].startswith("# Document\n")
    assert parsed["content"].endswith("A")


@pytest.mark.unit
def test_marks_pdf_truncated_after_first_40_pages():
    parsed = parse_document("many.pdf", "", _many_pdf_bytes(41))

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

    assert "len(reader.pages)" not in source
    assert "list(reader.pages)" not in source
    assert "enumerate(reader.pages" not in source
    assert "reader.pages[" not in source
    assert "_iter_pdf_pages_until(reader, page_limit)" in source


@pytest.mark.unit
def test_marks_xlsx_truncated_when_sheet_limit_is_exceeded():
    parsed = parse_document("many.xlsx", "", _xlsx_bytes("Shared", sheet_count=13))

    assert parsed["document_type"] == "xlsx"
    assert parsed["meta"]["sheets"] == 13
    assert parsed["truncated"] is True
    assert "# Sheet: Sheet 12" in parsed["content"]
    assert "# Sheet: Sheet 13" not in parsed["content"]


@pytest.mark.unit
def test_marks_xlsx_truncated_when_row_limit_is_exceeded():
    parsed = parse_document("many-rows.xlsx", "", _xlsx_bytes("Shared", row_count=805))

    assert parsed["document_type"] == "xlsx"
    assert parsed["truncated"] is True
    assert "Shared\t800" in parsed["content"]
    assert "Shared\t801" not in parsed["content"]
    assert "[Rows truncated]" in parsed["content"]


@pytest.mark.unit
def test_xlsx_empty_rows_do_not_consume_row_limit():
    parsed = parse_document("sparse.xlsx", "", _xlsx_with_empty_rows_before_data(805))

    assert parsed["truncated"] is False
    assert "Real data\t7" in parsed["content"]
    assert "[Rows truncated]" not in parsed["content"]


@pytest.mark.unit
def test_xlsx_preserves_sparse_cell_positions():
    parsed = parse_document("sparse-columns.xlsx", "", _xlsx_sparse_columns_bytes())

    assert "Name\t\tScore" in parsed["content"]
    assert "Name\tScore" not in parsed["content"]


@pytest.mark.unit
def test_xlsx_skips_hidden_sheets_and_rows():
    parsed = parse_document("hidden.xlsx", "", _xlsx_with_hidden_sheet_and_row())

    assert "Visible row" in parsed["content"]
    assert "Hidden row" not in parsed["content"]
    assert "Hidden sheet" not in parsed["content"]


@pytest.mark.unit
def test_xlsx_skips_hidden_columns():
    parsed = parse_document("hidden-column.xlsx", "", _xlsx_with_hidden_column())

    assert "Visible A" in parsed["content"]
    assert "Visible C" in parsed["content"]
    assert "Hidden B" not in parsed["content"]
    assert "Visible A\t\tVisible C" in parsed["content"]


@pytest.mark.unit
def test_xlsx_normalizes_relationship_targets_with_dot_segments():
    data = _zip_bytes({
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "xl/workbook.xml": (
            f'<workbook xmlns="{SPREADSHEET_NS}" xmlns:r="{OFFICE_REL_NS}">'
            '<sheets><sheet name="Dot" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<Relationships xmlns="{PACKAGE_REL_NS}">'
            '<Relationship Id="rId1" Target="./worksheets/sheet1.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": (
            f'<worksheet xmlns="{SPREADSHEET_NS}"><sheetData>'
            '<row r="1"><c r="A1" t="inlineStr"><is><t>Dot target</t></is></c></row>'
            "</sheetData></worksheet>"
        ),
    })

    parsed = parse_document("dot-target.xlsx", "", data)

    assert "Dot target" in parsed["content"]


@pytest.mark.unit
def test_xlsx_rows_are_not_materialized_before_limit():
    source = PARSER_SOURCE_PATH.read_text(encoding="utf-8")

    assert "ET.iterparse(io.BytesIO(data), events=(\"end\",))" in source
    assert 'rows = root.findall(".//" + _SPREADSHEET_NS + "row")' not in source


@pytest.mark.unit
def test_marks_pptx_truncated_after_first_40_slides():
    parsed = parse_document("many.pptx", "", _many_pptx_bytes(41))

    assert parsed["document_type"] == "pptx"
    assert parsed["meta"]["slides"] == 41
    assert parsed["truncated"] is True
    assert "# Slide 40" in parsed["content"]
    assert "Slide body 40" in parsed["content"]
    assert "# Slide 41" not in parsed["content"]
    assert "Slide body 41" not in parsed["content"]


@pytest.mark.unit
def test_pptx_visibility_scan_stops_after_truncation_sentinel():
    parsed = parse_document("many-visible-with-hidden-tail.pptx", "", _pptx_with_many_visible_and_hidden_tail(80))

    assert parsed["document_type"] == "pptx"
    assert parsed["meta"]["slides"] == 80
    assert parsed["truncated"] is True
    assert "Slide body 40" in parsed["content"]
    assert "Slide body 41" not in parsed["content"]
    assert "Slide body 80" not in parsed["content"]


@pytest.mark.unit
def test_pptx_notes_are_limited_to_parsed_slides():
    parsed = parse_document("overflow-notes.pptx", "", _pptx_with_notes_on_skipped_slide(41))

    assert parsed["document_type"] == "pptx"
    assert parsed["truncated"] is True
    assert "Slide body 40" in parsed["content"]
    assert "Slide body 41" not in parsed["content"]
    assert "Skipped slide notes" not in parsed["content"]


@pytest.mark.unit
def test_pptx_skips_hidden_slides_and_their_notes():
    parsed = parse_document("hidden-slide.pptx", "", _pptx_with_hidden_slide())

    assert "Visible slide" in parsed["content"]
    assert "Hidden slide" not in parsed["content"]
    assert "Hidden slide notes" not in parsed["content"]


@pytest.mark.unit
def test_pptx_skips_false_valued_hidden_slides_and_their_notes():
    parsed = parse_document("hidden-slide-false.pptx", "", _pptx_with_hidden_slide("false"))

    assert "Visible slide" in parsed["content"]
    assert "Hidden slide" not in parsed["content"]
    assert "Hidden slide notes" not in parsed["content"]


@pytest.mark.unit
def test_marks_pptx_truncated_after_first_40_notes():
    parsed = parse_document("many-notes.pptx", "", _many_pptx_notes_bytes(41))

    assert parsed["document_type"] == "pptx"
    assert parsed["meta"]["slides"] == 1
    assert parsed["truncated"] is True
    assert "# Notes 40" in parsed["content"]
    assert "Notes body 40" in parsed["content"]
    assert "# Notes 41" not in parsed["content"]
    assert "Notes body 41" not in parsed["content"]
