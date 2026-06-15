# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET


MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_ZIP_ENTRIES = 3000
MAX_ZIP_UNCOMPRESSED_BYTES = 80 * 1024 * 1024
MAX_XML_MEMBER_BYTES = 12 * 1024 * 1024
MAX_EXTRACTED_CHARS = 32000
MAX_PDF_PAGES = 40
MAX_XLSX_SHEETS = 12
MAX_XLSX_ROWS_PER_SHEET = 800
MAX_PPTX_SLIDES = 40


class AvatarDocumentParseError(ValueError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass
class _TextBudget:
    limit: int = MAX_EXTRACTED_CHARS
    used: int = 0
    truncated: bool = False

    def add(self, parts: list[str], text: str) -> None:
        if self.truncated:
            return
        value = _clean_text(text)
        if not value:
            return
        remaining = self.limit - self.used
        if remaining <= 0:
            self.truncated = True
            return
        if len(value) > remaining:
            parts.append(value[:remaining].rstrip())
            self.used = self.limit
            self.truncated = True
            return
        parts.append(value)
        self.used += len(value)


_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_SPREADSHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_OFFICE_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def parse_avatar_document(filename: str, content_type: str, data: bytes) -> dict[str, Any]:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise AvatarDocumentParseError("empty_file")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise AvatarDocumentParseError("document_too_large")

    document_type = _detect_document_type(filename, content_type, bytes(data))
    if document_type == "pdf":
        result = _parse_pdf(bytes(data))
    elif document_type == "docx":
        result = _parse_docx(bytes(data))
    elif document_type == "xlsx":
        result = _parse_xlsx(bytes(data))
    elif document_type == "pptx":
        result = _parse_pptx(bytes(data))
    else:
        raise AvatarDocumentParseError("unsupported_document")

    content = _clean_text(result["content"])
    _validate_text_quality(content)
    if not content:
        raise AvatarDocumentParseError("no_readable_text")
    return {
        "document_type": document_type,
        "content": content,
        "chars": len(content),
        "truncated": bool(result.get("truncated")),
        "meta": result.get("meta") or {},
    }


def _detect_document_type(filename: str, content_type: str, data: bytes) -> str:
    lower_name = str(filename or "").lower()
    ext = lower_name.rsplit(".", 1)[-1] if "." in lower_name else ""
    mime = str(content_type or "").lower()
    if ext in {"doc", "xls", "ppt"}:
        raise AvatarDocumentParseError("legacy_office_unsupported")
    if ext in {"docm", "xlsm", "pptm"}:
        raise AvatarDocumentParseError("macro_document_unsupported")
    if data.startswith(b"%PDF-"):
        return "pdf"
    if ext == "pdf" or mime == "application/pdf":
        raise AvatarDocumentParseError("invalid_pdf")
    if ext in {"docx", "xlsx", "pptx"}:
        if not data.startswith(b"PK\x03\x04") and not data.startswith(b"PK\x05\x06") and not data.startswith(b"PK\x07\x08"):
            raise AvatarDocumentParseError("invalid_ooxml")
        return ext
    raise AvatarDocumentParseError("unsupported_document")


def _parse_pdf(data: bytes) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - depends on environment
        raise AvatarDocumentParseError("pdf_parser_unavailable") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise AvatarDocumentParseError("invalid_pdf") from exc
    if getattr(reader, "is_encrypted", False):
        raise AvatarDocumentParseError("encrypted_pdf_unsupported")

    try:
        total_pages = len(reader.pages)
    except Exception:
        total_pages = None
    budget = _TextBudget()
    parts: list[str] = []
    observed_pages = 0
    for index, page in enumerate(reader.pages, start=1):
        observed_pages = index
        if index > MAX_PDF_PAGES:
            budget.truncated = True
            break
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            budget.add(parts, f"# Page {index}\n{text}")
        if budget.truncated:
            break
    if total_pages is not None and total_pages > MAX_PDF_PAGES:
        budget.truncated = True
    return {
        "content": "\n\n".join(parts),
        "truncated": budget.truncated,
        "meta": {"pages": total_pages if total_pages is not None else observed_pages},
    }


def _parse_docx(data: bytes) -> dict[str, Any]:
    with _open_checked_zip(data, "docx") as archive:
        _require_member(archive, "word/document.xml")
        _reject_macro_members(archive, "word/")
        budget = _TextBudget()
        parts: list[str] = []
        for name in _docx_text_member_names(archive):
            xml_bytes = _read_xml_member(archive, name)
            text = _extract_word_text(xml_bytes)
            if text:
                label = _docx_member_label(name)
                budget.add(parts, f"# {label}\n{text}" if label else text)
            if budget.truncated:
                break
        return {
            "content": "\n\n".join(parts),
            "truncated": budget.truncated,
            "meta": {},
        }


def _parse_xlsx(data: bytes) -> dict[str, Any]:
    with _open_checked_zip(data, "xlsx") as archive:
        _require_member(archive, "xl/workbook.xml")
        _reject_macro_members(archive, "xl/")
        shared_strings = _read_xlsx_shared_strings(archive)
        sheets = _read_xlsx_sheets(archive)
        if not sheets:
            raise AvatarDocumentParseError("xlsx_no_sheets")
        budget = _TextBudget()
        parts: list[str] = []
        for sheet_index, sheet in enumerate(sheets[:MAX_XLSX_SHEETS], start=1):
            text, rows_truncated = _extract_xlsx_sheet_text(archive, sheet["path"], shared_strings)
            if text:
                name = sheet["name"] or f"Sheet {sheet_index}"
                budget.add(parts, f"# Sheet: {name}\n{text}")
            if rows_truncated:
                budget.truncated = True
            if budget.truncated:
                break
        if len(sheets) > MAX_XLSX_SHEETS:
            budget.truncated = True
        return {
            "content": "\n\n".join(parts),
            "truncated": budget.truncated,
            "meta": {"sheets": len(sheets)},
        }


def _parse_pptx(data: bytes) -> dict[str, Any]:
    with _open_checked_zip(data, "pptx") as archive:
        _require_member(archive, "ppt/presentation.xml")
        _reject_macro_members(archive, "ppt/")
        budget = _TextBudget()
        parts: list[str] = []
        slide_names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=_natural_key,
        )
        for index, name in enumerate(slide_names[:MAX_PPTX_SLIDES], start=1):
            xml_bytes = _read_xml_member(archive, name)
            text = _extract_drawing_text(xml_bytes)
            if text:
                budget.add(parts, f"# Slide {index}\n{text}")
            if budget.truncated:
                break
        if len(slide_names) > MAX_PPTX_SLIDES:
            budget.truncated = True
        note_names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", name)),
            key=_natural_key,
        )
        for index, name in enumerate(note_names[:MAX_PPTX_SLIDES], start=1):
            if budget.truncated:
                break
            text = _extract_drawing_text(_read_xml_member(archive, name))
            if text:
                budget.add(parts, f"# Notes {index}\n{text}")
        return {
            "content": "\n\n".join(parts),
            "truncated": budget.truncated,
            "meta": {"slides": len(slide_names)},
        }


def _open_checked_zip(data: bytes, document_type: str) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise AvatarDocumentParseError("invalid_ooxml") from exc

    try:
        names = archive.namelist()
        if len(names) > MAX_ZIP_ENTRIES:
            raise AvatarDocumentParseError("zip_too_many_entries")
        total_size = 0
        for info in archive.infolist():
            _validate_zip_member_name(info.filename)
            total_size += max(0, int(info.file_size or 0))
            if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise AvatarDocumentParseError("zip_uncompressed_too_large")
        if "[Content_Types].xml" not in names:
            raise AvatarDocumentParseError(f"invalid_{document_type}")
        return archive
    except Exception:
        archive.close()
        raise


def _validate_zip_member_name(name: str) -> None:
    path = PurePosixPath(str(name or ""))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise AvatarDocumentParseError("invalid_zip_member")


def _require_member(archive: zipfile.ZipFile, name: str) -> None:
    if name not in archive.namelist():
        raise AvatarDocumentParseError("invalid_ooxml")


def _reject_macro_members(archive: zipfile.ZipFile, prefix: str) -> None:
    for name in archive.namelist():
        lowered = name.lower()
        if lowered.startswith(prefix) and lowered.endswith("vbaproject.bin"):
            raise AvatarDocumentParseError("macro_document_unsupported")


def _read_xml_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_MEMBER_BYTES:
        raise AvatarDocumentParseError("xml_member_too_large")
    data = archive.read(name)
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise AvatarDocumentParseError("xml_entity_unsupported")
    return data


def _parse_xml(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise AvatarDocumentParseError("invalid_xml") from exc


def _docx_text_member_names(archive: zipfile.ZipFile) -> list[str]:
    names = archive.namelist()
    ordered = ["word/document.xml"]
    ordered.extend(sorted(
        name for name in names
        if re.fullmatch(r"word/header\d+\.xml", name) or re.fullmatch(r"word/footer\d+\.xml", name)
    ))
    ordered.extend(name for name in ("word/footnotes.xml", "word/endnotes.xml") if name in names)
    return [name for name in ordered if name in names]


def _docx_member_label(name: str) -> str:
    if name == "word/document.xml":
        return "Document"
    if "header" in name:
        return "Header"
    if "footer" in name:
        return "Footer"
    if "footnotes" in name:
        return "Footnotes"
    if "endnotes" in name:
        return "Endnotes"
    return ""


def _extract_word_text(xml_bytes: bytes) -> str:
    root = _parse_xml(xml_bytes)
    lines: list[str] = []
    for paragraph in root.iter(_WORD_NS + "p"):
        chunks: list[str] = []
        for node in paragraph.iter():
            if node.tag == _WORD_NS + "t" and node.text:
                chunks.append(node.text)
            elif node.tag == _WORD_NS + "tab":
                chunks.append("\t")
            elif node.tag == _WORD_NS + "br":
                chunks.append("\n")
        line = "".join(chunks).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _parse_xml(_read_xml_member(archive, "xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall(_SPREADSHEET_NS + "si"):
        values.append("".join(node.text or "" for node in item.iter(_SPREADSHEET_NS + "t")))
    return values


def _read_xlsx_sheets(archive: zipfile.ZipFile) -> list[dict[str, str]]:
    workbook = _parse_xml(_read_xml_member(archive, "xl/workbook.xml"))
    rels = {}
    rels_path = "xl/_rels/workbook.xml.rels"
    if rels_path in archive.namelist():
        rel_root = _parse_xml(_read_xml_member(archive, rels_path))
        for rel in rel_root.findall(_REL_NS + "Relationship"):
            rel_id = rel.attrib.get("Id", "")
            target = rel.attrib.get("Target", "")
            if rel_id and target:
                rels[rel_id] = _resolve_xlsx_target(target)

    sheets: list[dict[str, str]] = []
    sheets_root = workbook.find(_SPREADSHEET_NS + "sheets")
    if sheets_root is None:
        return sheets
    for sheet in sheets_root.findall(_SPREADSHEET_NS + "sheet"):
        rel_id = sheet.attrib.get(_OFFICE_REL_NS + "id", "")
        path = rels.get(rel_id)
        if not path:
            continue
        sheets.append({"name": sheet.attrib.get("name", ""), "path": path})
    return sheets


def _resolve_xlsx_target(target: str) -> str:
    cleaned = str(target or "").lstrip("/")
    if cleaned.startswith("xl/"):
        return cleaned
    return "xl/" + cleaned


def _extract_xlsx_sheet_text(
    archive: zipfile.ZipFile,
    path: str,
    shared_strings: list[str],
) -> tuple[str, bool]:
    if path not in archive.namelist():
        return "", False
    data = _read_xml_member(archive, path)
    lines: list[str] = []
    truncated = False
    rows_seen = 0
    try:
        for _event, row in ET.iterparse(io.BytesIO(data), events=("end",)):
            if row.tag != _SPREADSHEET_NS + "row":
                continue
            if rows_seen >= MAX_XLSX_ROWS_PER_SHEET:
                truncated = True
                break
            rows_seen += 1
            values: list[str] = []
            for cell in row.findall(_SPREADSHEET_NS + "c"):
                values.append(_xlsx_cell_text(cell, shared_strings))
            while values and not values[-1]:
                values.pop()
            if any(values):
                lines.append("\t".join(values))
            row.clear()
    except ET.ParseError as exc:
        raise AvatarDocumentParseError("invalid_xml") from exc
    if truncated:
        lines.append("[Rows truncated]")
    return "\n".join(lines), truncated


def _xlsx_cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "s":
        value = _child_text(cell, _SPREADSHEET_NS + "v")
        try:
            return shared_strings[int(value)]
        except Exception:
            return ""
    if cell_type == "inlineStr":
        inline = cell.find(_SPREADSHEET_NS + "is")
        if inline is None:
            return ""
        return "".join(node.text or "" for node in inline.iter(_SPREADSHEET_NS + "t")).strip()
    value = _child_text(cell, _SPREADSHEET_NS + "v")
    if value:
        return value.strip()
    formula = _child_text(cell, _SPREADSHEET_NS + "f")
    if formula:
        return "=" + formula.strip()
    return ""


def _child_text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    return child.text if child is not None and child.text else ""


def _extract_drawing_text(xml_bytes: bytes) -> str:
    root = _parse_xml(xml_bytes)
    values = [node.text or "" for node in root.iter(_A_NS + "t") if node.text]
    text = "\n".join(value.strip() for value in values if value.strip())
    return text


def _natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def _clean_text(text: str) -> str:
    value = str(text or "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]+", "", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    return value.strip()


def _validate_text_quality(text: str) -> None:
    if not text.strip():
        raise AvatarDocumentParseError("no_readable_text")
    replacement_count = text.count("\ufffd")
    if replacement_count > 16 or replacement_count / max(1, len(text)) > 0.005:
        raise AvatarDocumentParseError("garbled_text")
