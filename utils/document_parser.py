# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

from __future__ import annotations

import io
import posixpath
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
MAX_XLSX_COLUMNS = 16384
MAX_PPTX_SLIDES = 40
MIN_DEDUP_TEXT_CHARS = 80


class DocumentParseError(ValueError):
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
_MC_NS = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_SPREADSHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_OFFICE_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def parse_document(filename: str, content_type: str, data: bytes) -> dict[str, Any]:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise DocumentParseError("empty_file")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentParseError("document_too_large")

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
        raise DocumentParseError("unsupported_document")

    content = _clean_text(result["content"])
    _validate_text_quality(content)
    if not content:
        raise DocumentParseError("no_readable_text")
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
        raise DocumentParseError("legacy_office_unsupported")
    if ext in {"docm", "xlsm", "pptm"}:
        raise DocumentParseError("macro_document_unsupported")
    if data.startswith(b"%PDF-"):
        return "pdf"
    if ext == "pdf" or mime == "application/pdf":
        raise DocumentParseError("invalid_pdf")
    if ext in {"docx", "xlsx", "pptx"}:
        if not data.startswith(b"PK\x03\x04") and not data.startswith(b"PK\x05\x06") and not data.startswith(b"PK\x07\x08"):
            raise DocumentParseError("invalid_ooxml")
        return ext
    raise DocumentParseError("unsupported_document")


def _parse_pdf(data: bytes) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - depends on environment
        raise DocumentParseError("pdf_parser_unavailable") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise DocumentParseError("invalid_pdf") from exc
    if getattr(reader, "is_encrypted", False):
        raise DocumentParseError("encrypted_pdf_unsupported")

    total_pages = _get_pdf_declared_page_count(reader)
    budget = _TextBudget()
    parts: list[str] = []
    seen_text_keys: set[str] = set()
    observed_pages = 0
    page_limit = MAX_PDF_PAGES if total_pages and total_pages > MAX_PDF_PAGES else MAX_PDF_PAGES + 1
    for index, page in enumerate(_iter_pdf_pages_until(reader, page_limit), start=1):
        observed_pages = index
        if index > MAX_PDF_PAGES:
            budget.truncated = True
            break
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        _add_unique_text_part(budget, parts, seen_text_keys, f"Page {index}", text)
        if budget.truncated:
            break
    if total_pages is not None and total_pages > MAX_PDF_PAGES:
        budget.truncated = True
    return {
        "content": "\n\n".join(parts),
        "truncated": budget.truncated,
        "meta": {"pages": total_pages if total_pages is not None else observed_pages},
    }


def _get_pdf_declared_page_count(reader: Any) -> int | None:
    try:
        pages = _resolve_pdf_object(reader.root_object["/Pages"])
        count = int(pages.get("/Count"))
        return count if count >= 0 else None
    except Exception:
        return None


def _iter_pdf_pages_until(reader: Any, limit: int) -> Any:
    try:
        from pypdf._page import PageObject
        from pypdf.generic import DictionaryObject, IndirectObject, NameObject
    except Exception as exc:  # pragma: no cover - pypdf import already checked by caller
        raise DocumentParseError("pdf_parser_unavailable") from exc

    inheritable_attrs = tuple(NameObject(name) for name in ("/Resources", "/MediaBox", "/CropBox", "/Rotate"))
    emitted = 0

    def object_key(reference: Any, resolved: Any) -> tuple[Any, ...]:
        if isinstance(reference, IndirectObject):
            return ("ref", reference.idnum, reference.generation)
        return ("obj", id(resolved))

    def walk(node_ref: Any, inherited: dict[Any, Any], seen_pages_nodes: set[tuple[Any, ...]]) -> Any:
        nonlocal emitted
        if emitted >= limit:
            return
        node = _resolve_pdf_object(node_ref)
        if not isinstance(node, DictionaryObject):
            raise DocumentParseError("invalid_pdf")

        node_type = str(node.get("/Type", ""))
        if not node_type:
            node_type = "/Page" if "/Kids" not in node else "/Pages"

        if node_type == "/Pages":
            key = object_key(node_ref, node)
            if key in seen_pages_nodes:
                raise DocumentParseError("invalid_pdf")
            next_seen = set(seen_pages_nodes)
            next_seen.add(key)
            next_inherited = dict(inherited)
            for attr in inheritable_attrs:
                if attr in node:
                    next_inherited[attr] = node[attr]
            kids = node.get("/Kids")
            if kids is None:
                raise DocumentParseError("invalid_pdf")
            for child in kids:
                yield from walk(child, next_inherited, next_seen)
                if emitted >= limit:
                    break
        elif node_type == "/Page":
            page = PageObject(reader, node_ref if isinstance(node_ref, IndirectObject) else None)
            page.update(node)
            for attr, value in inherited.items():
                if attr not in page:
                    page[attr] = value
            emitted += 1
            yield page
        else:
            raise DocumentParseError("invalid_pdf")

    root_pages_ref = reader.root_object["/Pages"]
    yield from walk(root_pages_ref, {}, set())


def _resolve_pdf_object(value: Any) -> Any:
    if hasattr(value, "get_object") and callable(value.get_object):
        return value.get_object()
    return value


def _parse_docx(data: bytes) -> dict[str, Any]:
    with _open_checked_zip(data, "docx") as archive:
        _require_member(archive, "word/document.xml")
        _reject_macro_members(archive, "word/")
        budget = _TextBudget()
        parts: list[str] = []
        seen_text_keys: set[str] = set()

        document_xml = _read_xml_member(archive, "word/document.xml")
        document_root = _parse_xml(document_xml)
        _add_unique_text_part(
            budget,
            parts,
            seen_text_keys,
            "Document",
            _extract_word_text_from_root(document_root),
        )

        for name in _read_docx_header_footer_names(archive, document_root):
            if budget.truncated:
                break
            text = _extract_word_text(_read_xml_member(archive, name))
            _add_unique_text_part(budget, parts, seen_text_keys, _docx_member_label(name), text)

        note_parts = (
            ("word/footnotes.xml", "Footnotes", _WORD_NS + "footnoteReference", _WORD_NS + "footnote"),
            ("word/endnotes.xml", "Endnotes", _WORD_NS + "endnoteReference", _WORD_NS + "endnote"),
        )
        for name, label, reference_tag, note_tag in note_parts:
            if budget.truncated or name not in archive.namelist():
                continue
            note_ids = _collect_word_note_reference_ids(document_root, reference_tag)
            if not note_ids:
                continue
            text = _extract_word_notes_text(_read_xml_member(archive, name), note_tag, note_ids)
            _add_unique_text_part(budget, parts, seen_text_keys, label, text)
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
            raise DocumentParseError("xlsx_no_sheets")
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
        seen_text_keys: set[str] = set()
        slide_names = _read_pptx_slide_names(archive)
        visible_slide_names = _filter_visible_pptx_slide_names(
            archive,
            slide_names,
            limit=MAX_PPTX_SLIDES + 1,
        )
        parsed_slide_names = visible_slide_names[:MAX_PPTX_SLIDES]
        slides_truncated = len(visible_slide_names) > MAX_PPTX_SLIDES
        for index, name in enumerate(parsed_slide_names, start=1):
            xml_bytes = _read_xml_member(archive, name)
            text = _extract_drawing_text(xml_bytes)
            _add_unique_text_part(budget, parts, seen_text_keys, f"Slide {index}", text)
            if budget.truncated:
                break
        max_fallback_note_index = (
            len(parsed_slide_names)
            if slides_truncated or len(visible_slide_names) < len(slide_names)
            else None
        )
        note_names = _read_pptx_note_names(
            archive,
            parsed_slide_names,
            max_fallback_note_index=max_fallback_note_index,
        )
        for index, name in enumerate(note_names[:MAX_PPTX_SLIDES], start=1):
            if budget.truncated:
                break
            text = _extract_drawing_text(_read_xml_member(archive, name))
            _add_unique_text_part(budget, parts, seen_text_keys, f"Notes {index}", text)
        if slides_truncated or len(note_names) > MAX_PPTX_SLIDES:
            budget.truncated = True
        return {
            "content": "\n\n".join(parts),
            "truncated": budget.truncated,
            "meta": {"slides": len(slide_names)},
        }


def _open_checked_zip(data: bytes, document_type: str) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise DocumentParseError("invalid_ooxml") from exc

    try:
        names = archive.namelist()
        if len(names) > MAX_ZIP_ENTRIES:
            raise DocumentParseError("zip_too_many_entries")
        total_size = 0
        for info in archive.infolist():
            # Some ZIP writers preserve a Windows separator in member names.
            # A backslash path is otherwise invalid, but a vbaProject member is
            # semantically a macro payload and must keep the stronger macro
            # rejection code used by the UI/security policy.
            if "\\" in info.filename and info.filename.replace("\\", "/").lower().endswith(
                "/vbaproject.bin"
            ):
                raise DocumentParseError("macro_document_unsupported")
            _validate_zip_member_name(info.filename)
            total_size += max(0, int(info.file_size or 0))
            if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise DocumentParseError("zip_uncompressed_too_large")
        if "[Content_Types].xml" not in names:
            raise DocumentParseError(f"invalid_{document_type}")
        return archive
    except Exception:
        archive.close()
        raise


def _validate_zip_member_name(name: str) -> None:
    value = str(name or "")
    if "\\" in value:
        raise DocumentParseError("invalid_zip_member")
    raw_parts = value.split("/")
    if raw_parts and raw_parts[-1] == "":
        raw_parts = raw_parts[:-1]
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise DocumentParseError("invalid_zip_member")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DocumentParseError("invalid_zip_member")


def _require_member(archive: zipfile.ZipFile, name: str) -> None:
    if name not in archive.namelist():
        raise DocumentParseError("invalid_ooxml")


def _reject_macro_members(archive: zipfile.ZipFile, prefix: str) -> None:
    for name in archive.namelist():
        lowered = name.lower()
        if lowered.startswith(prefix) and lowered.endswith("vbaproject.bin"):
            raise DocumentParseError("macro_document_unsupported")


def _read_xml_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_MEMBER_BYTES:
        raise DocumentParseError("xml_member_too_large")
    data = archive.read(name)
    if _has_xml_entity_declaration(data):
        raise DocumentParseError("xml_entity_unsupported")
    return data


def _parse_xml(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise DocumentParseError("invalid_xml") from exc


def _has_xml_entity_declaration(data: bytes) -> bool:
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        return True

    text = _decode_xml_guard_text(data).casefold()
    return "<!doctype" in text or "<!entity" in text


def _decode_xml_guard_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    elif data[:2] == b"<\x00":
        encodings = ("utf-16-le",)
    elif data[:2] == b"\x00<":
        encodings = ("utf-16-be",)
    else:
        encodings = (_xml_declared_encoding(data), "utf-8-sig")

    for encoding in encodings:
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeError):
            continue
    return data.decode("utf-8", errors="ignore")


def _xml_declared_encoding(data: bytes) -> str:
    header = data[:256].decode("ascii", errors="ignore")
    match = re.search(r'encoding=["\']([^"\']+)["\']', header, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _read_docx_header_footer_names(archive: zipfile.ZipFile, document: ET.Element) -> list[str]:
    names = set(archive.namelist())
    rels_path = "word/_rels/document.xml.rels"
    if rels_path not in names:
        return []

    rels: dict[str, str] = {}
    rel_root = _parse_xml(_read_xml_member(archive, rels_path))
    for rel in rel_root.findall(_REL_NS + "Relationship"):
        rel_id = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        rel_type = rel.attrib.get("Type", "")
        if rel_id and target and (
            rel_type.endswith("/header") or rel_type.endswith("/footer")
        ):
            rels[rel_id] = _resolve_docx_target(target, "word")

    ordered: list[str] = []
    for element in document.iter():
        if element.tag not in {_WORD_NS + "headerReference", _WORD_NS + "footerReference"}:
            continue
        path = rels.get(element.attrib.get(_OFFICE_REL_NS + "id", ""))
        if path in names and path not in ordered:
            ordered.append(path)
    return ordered


def _resolve_docx_target(target: str, base_dir: str) -> str:
    cleaned = str(target or "").replace("\\", "/")
    if cleaned.startswith("/"):
        return posixpath.normpath(cleaned.lstrip("/"))
    if cleaned.startswith("word/"):
        return posixpath.normpath(cleaned)
    return posixpath.normpath(posixpath.join(base_dir, cleaned))


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
    return _extract_word_text_from_root(_parse_xml(xml_bytes))


def _extract_word_text_from_root(root: ET.Element) -> str:
    lines: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag == _MC_NS + "Fallback":
            return
        if node.tag == _WORD_NS + "p":
            line = _extract_word_paragraph_text(node).strip()
            if line:
                lines.append(line)
        for child in node:
            walk(child)

    walk(root)
    return "\n".join(lines)


def _collect_word_note_reference_ids(root: ET.Element, reference_tag: str) -> set[str]:
    return {
        value for value in (
            element.attrib.get(_WORD_NS + "id", "")
            for element in root.iter(reference_tag)
        )
        if value
    }


def _extract_word_notes_text(xml_bytes: bytes, note_tag: str, note_ids: set[str]) -> str:
    root = _parse_xml(xml_bytes)
    lines: list[str] = []
    for note in root.findall(note_tag):
        if note.attrib.get(_WORD_NS + "id", "") not in note_ids:
            continue
        text = _extract_word_text_from_root(note)
        if text:
            lines.append(text)
    return "\n".join(lines)


def _extract_word_paragraph_text(paragraph: ET.Element) -> str:
    chunks: list[str] = []

    def walk(node: ET.Element) -> None:
        for child in node:
            if child.tag == _MC_NS + "Fallback":
                continue
            if child.tag == _WORD_NS + "r" and _word_run_is_hidden(child):
                continue
            if child.tag == _WORD_NS + "p":
                continue
            if child.tag == _WORD_NS + "t" and child.text:
                chunks.append(child.text)
            elif child.tag == _WORD_NS + "tab":
                chunks.append("\t")
            elif child.tag == _WORD_NS + "br":
                chunks.append("\n")
            walk(child)

    walk(paragraph)
    return "".join(chunks)


def _word_run_is_hidden(run: ET.Element) -> bool:
    properties = run.find(_WORD_NS + "rPr")
    if properties is None:
        return False
    vanish = properties.find(_WORD_NS + "vanish")
    if vanish is None:
        return False
    value = vanish.attrib.get(_WORD_NS + "val", vanish.attrib.get("val"))
    return value is None or value.strip().casefold() not in {"0", "false", "off", "no"}


def _office_bool_is_false(value: object) -> bool:
    return str(value or "").strip().casefold() in {"0", "false", "off", "no"}


def _add_unique_text_part(
    budget: _TextBudget,
    parts: list[str],
    seen_text_keys: set[str],
    label: str,
    text: str,
) -> None:
    value = _clean_text(text)
    if not value:
        return
    key = _text_dedup_key(value)
    if key:
        if key in seen_text_keys:
            return
        seen_text_keys.add(key)
    budget.add(parts, f"# {label}\n{value}" if label else value)


def _text_dedup_key(text: str) -> str:
    normalized = re.sub(r"\s+", " ", _clean_text(text)).strip().casefold()
    if len(normalized) < MIN_DEDUP_TEXT_CHARS:
        return ""
    return normalized


def _read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _parse_xml(_read_xml_member(archive, "xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall(_SPREADSHEET_NS + "si"):
        values.append("".join(node.text or "" for node in item.iter(_SPREADSHEET_NS + "t")))
    return values


def _read_pptx_slide_names(archive: zipfile.ZipFile) -> list[str]:
    names = archive.namelist()
    fallback = sorted(
        (name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
        key=_natural_key,
    )
    rels_path = "ppt/_rels/presentation.xml.rels"
    if rels_path not in names:
        return fallback

    rels = {}
    rel_root = _parse_xml(_read_xml_member(archive, rels_path))
    for rel in rel_root.findall(_REL_NS + "Relationship"):
        rel_id = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if rel_id and target:
            rels[rel_id] = _resolve_pptx_target(target, "ppt")

    presentation = _parse_xml(_read_xml_member(archive, "ppt/presentation.xml"))
    ordered: list[str] = []
    for element in presentation.iter():
        if not str(element.tag).endswith("}sldId"):
            continue
        rel_id = element.attrib.get(_OFFICE_REL_NS + "id", "")
        path = rels.get(rel_id)
        if path in names:
            ordered.append(path)
    return ordered or fallback


def _filter_visible_pptx_slide_names(
    archive: zipfile.ZipFile,
    slide_names: list[str],
    *,
    limit: int | None = None,
) -> list[str]:
    visible: list[str] = []
    for name in slide_names:
        try:
            root = _parse_xml(_read_xml_member(archive, name))
        except DocumentParseError:
            raise
        if _office_bool_is_false(root.attrib.get("show")):
            continue
        visible.append(name)
        if limit is not None and len(visible) >= limit:
            break
    return visible


def _read_pptx_note_names(
    archive: zipfile.ZipFile,
    slide_names: list[str],
    *,
    max_fallback_note_index: int | None = None,
) -> list[str]:
    names = set(archive.namelist())
    ordered: list[str] = []
    for slide_name in slide_names:
        rels_path = _pptx_rels_path(slide_name)
        if rels_path not in names:
            continue
        rel_root = _parse_xml(_read_xml_member(archive, rels_path))
        for rel in rel_root.findall(_REL_NS + "Relationship"):
            target = rel.attrib.get("Target", "")
            rel_type = rel.attrib.get("Type", "")
            if not target or not rel_type.endswith("/notesSlide"):
                continue
            note_path = _resolve_pptx_target(target, posixpath.dirname(slide_name))
            if note_path in names and note_path not in ordered:
                ordered.append(note_path)

    if ordered:
        return ordered
    fallback = sorted(
        (name for name in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", name)),
        key=_natural_key,
    )
    if max_fallback_note_index is None:
        return fallback
    return [
        name for name in fallback
        if _pptx_numbered_part_index(name) <= max_fallback_note_index
    ]


def _pptx_rels_path(member_name: str) -> str:
    directory, filename = posixpath.split(member_name)
    return posixpath.join(directory, "_rels", f"{filename}.rels")


def _pptx_numbered_part_index(member_name: str) -> int:
    match = re.search(r"(\d+)\.xml$", member_name)
    return int(match.group(1)) if match else 0


def _resolve_pptx_target(target: str, base_dir: str) -> str:
    cleaned = str(target or "").replace("\\", "/")
    if cleaned.startswith("/"):
        return posixpath.normpath(cleaned.lstrip("/"))
    if cleaned.startswith("ppt/"):
        return posixpath.normpath(cleaned)
    return posixpath.normpath(posixpath.join(base_dir, cleaned))


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
        if str(sheet.attrib.get("state", "")).casefold() in {"hidden", "veryhidden"}:
            continue
        rel_id = sheet.attrib.get(_OFFICE_REL_NS + "id", "")
        path = rels.get(rel_id)
        if not path:
            continue
        sheets.append({"name": sheet.attrib.get("name", ""), "path": path})
    return sheets


def _resolve_xlsx_target(target: str) -> str:
    cleaned = str(target or "").replace("\\", "/").lstrip("/")
    if cleaned.startswith("xl/"):
        return posixpath.normpath(cleaned)
    return posixpath.normpath(posixpath.join("xl", cleaned))


def _extract_xlsx_sheet_text(
    archive: zipfile.ZipFile,
    path: str,
    shared_strings: list[str],
) -> tuple[str, bool]:
    if path not in archive.namelist():
        return "", False
    data = _read_xml_member(archive, path)
    lines: list[str] = []
    hidden_column_ranges: list[tuple[int, int]] = []
    truncated = False
    rows_seen = 0
    try:
        for _event, row in ET.iterparse(io.BytesIO(data), events=("end",)):
            if row.tag == _SPREADSHEET_NS + "col":
                hidden_range = _xlsx_hidden_column_range(row)
                if hidden_range is not None:
                    hidden_column_ranges.append(hidden_range)
                row.clear()
                continue
            if row.tag != _SPREADSHEET_NS + "row":
                continue
            if str(row.attrib.get("hidden", "")).casefold() in {"1", "true"}:
                row.clear()
                continue
            values: list[str] = []
            for cell in row.findall(_SPREADSHEET_NS + "c"):
                value = _xlsx_cell_text(cell, shared_strings)
                column_index = _xlsx_cell_column_index(cell.attrib.get("r", ""))
                if column_index is None:
                    values.append(value)
                    continue
                if _xlsx_column_is_hidden(column_index, hidden_column_ranges):
                    continue
                while len(values) <= column_index:
                    values.append("")
                values[column_index] = value
            while values and not values[-1]:
                values.pop()
            if not any(values):
                row.clear()
                continue
            if rows_seen >= MAX_XLSX_ROWS_PER_SHEET:
                truncated = True
                row.clear()
                break
            rows_seen += 1
            lines.append("\t".join(values))
            row.clear()
    except ET.ParseError as exc:
        raise DocumentParseError("invalid_xml") from exc
    if truncated:
        lines.append("[Rows truncated]")
    return "\n".join(lines), truncated


def _xlsx_hidden_column_range(column: ET.Element) -> tuple[int, int] | None:
    if str(column.attrib.get("hidden", "")).casefold() not in {"1", "true"}:
        return None
    try:
        start = int(column.attrib.get("min", "0")) - 1
        end = int(column.attrib.get("max", "0")) - 1
    except (TypeError, ValueError):
        return None
    if start < 0 or end < start or end >= MAX_XLSX_COLUMNS:
        return None
    return start, end


def _xlsx_column_is_hidden(
    column_index: int,
    hidden_column_ranges: list[tuple[int, int]],
) -> bool:
    return any(start <= column_index <= end for start, end in hidden_column_ranges)


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


def _xlsx_cell_column_index(reference: str) -> int | None:
    match = re.match(r"^([A-Za-z]+)", str(reference or ""))
    if not match:
        return None
    index = 0
    for char in match.group(1).upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    if index <= 0 or index > MAX_XLSX_COLUMNS:
        return None
    return index - 1


def _child_text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    return child.text if child is not None and child.text else ""


def _extract_drawing_text(xml_bytes: bytes) -> str:
    root = _parse_xml(xml_bytes)
    lines: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag == _MC_NS + "Fallback":
            return
        if node.tag == _A_NS + "p":
            line = _extract_drawing_paragraph_text(node).strip()
            if line:
                lines.append(line)
            return
        if node.tag == _A_NS + "t" and node.text:
            lines.append(node.text.strip())
        for child in node:
            walk(child)

    walk(root)
    return "\n".join(line for line in lines if line)


def _extract_drawing_paragraph_text(paragraph: ET.Element) -> str:
    chunks: list[str] = []

    def walk(node: ET.Element) -> None:
        for child in node:
            if child.tag == _MC_NS + "Fallback":
                continue
            if child.tag == _A_NS + "p":
                continue
            if child.tag == _A_NS + "t" and child.text:
                chunks.append(child.text)
            elif child.tag == _A_NS + "tab":
                chunks.append("\t")
            elif child.tag == _A_NS + "br":
                chunks.append("\n")
            walk(child)

    walk(paragraph)
    return "".join(chunks)


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
        raise DocumentParseError("no_readable_text")
    replacement_count = text.count("\ufffd")
    if replacement_count > 16 or replacement_count / max(1, len(text)) > 0.005:
        raise DocumentParseError("garbled_text")
