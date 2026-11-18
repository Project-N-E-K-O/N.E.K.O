"""Extract bounded complete text and honest locations from managed originals."""

from __future__ import annotations

import io
import logging
import re
import threading
from contextlib import contextmanager
from pathlib import PurePosixPath

from utils import document_parser as office

from .models import LibraryError, LibraryLimits


_PDF_PARSE_THREADS: dict[int, int] = {}
_PDF_LOG_LOCK = threading.Lock()


def _paragraphs(text: str, location: dict) -> list[dict]:
    """Keep repeated paragraphs; source numbering must not depend on deduplication."""
    return [
        {"text": value.strip(), "location": {**location, "paragraph": index}}
        for index, value in enumerate(re.split(r"\n\s*\n", text), 1)
        if value.strip()
    ]


def _decode(data: bytes) -> str:
    """Reuse attachment encoding rules with strict, non-lossy decoding."""
    encodings = ["utf-16"] if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "gb18030"]
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeError:
            continue
        # A binary file or replacement decoding must never become a completed document.
        controls = sum(ord(char) < 32 and char not in "\n\r\t" for char in text)
        if "\ufffd" not in text and "\x00" not in text and controls <= len(text) * 0.01:
            return text.replace("\r\n", "\n").replace("\r", "\n")
    raise LibraryError("invalid_text_encoding")


def _docx(data: bytes) -> list[dict]:
    """Reuse checked OOXML readers, without the attachment truncation or deduplication."""
    sections = []
    # These existing readers enforce ZIP/XML size, path, entity and macro policy.
    with office._open_checked_zip(data, "docx") as archive:
        office._require_member(archive, "word/document.xml")
        office._reject_macro_members(archive, "word/")
        root = office._parse_xml(office._read_xml_member(archive, "word/document.xml"))
        members = [("word/document.xml", root)]
        members += [
            (name, office._parse_xml(office._read_xml_member(archive, name)))
            for name in office._read_docx_header_footer_names(archive, root)
        ]
        for name, member in members:
            for index, paragraph in enumerate(member.iter(office._WORD_NS + "p"), 1):
                text = office._extract_word_text_from_root(paragraph).strip()
                if text:
                    sections.append({"text": text, "location": {"member": name, "paragraph": index}})
        for name, reference, tag in (
            ("word/footnotes.xml", "footnoteReference", "footnote"),
            ("word/endnotes.xml", "endnoteReference", "endnote"),
        ):
            if name in archive.namelist():
                ids = office._collect_word_note_reference_ids(root, office._WORD_NS + reference)
                text = office._extract_word_notes_text(
                    office._read_xml_member(archive, name), office._WORD_NS + tag, ids
                )
                sections.extend(_paragraphs(text, {"member": name}))
    return sections


def _pdf(data: bytes, limits: LibraryLimits) -> list[dict]:
    """Retain per-page text; encrypted, oversized and scanned inputs fail explicitly."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise LibraryError("encrypted_pdf_unsupported")
    count = office._get_pdf_declared_page_count(reader)
    if count is not None and count > limits.max_pdf_pages:
        raise LibraryError("document_too_many_pages")
    sections = []
    for index, page in enumerate(office._iter_pdf_pages_until(reader, limits.max_pdf_pages + 1), 1):
        if index > limits.max_pdf_pages:
            raise LibraryError("document_too_many_pages")
        text = page.extract_text() or ""
        if not text.strip() and page.get("/Resources", {}).get("/XObject"):
            raise LibraryError("scan_unsupported")
        sections.extend(_paragraphs(text, {"page": index}))
    return sections


def _redact_pdf_diagnostic(record):
    thread_id = record.thread
    with _PDF_LOG_LOCK:
        private = thread_id in _PDF_PARSE_THREADS
    # XObject operands and exception values come from the private PDF.
    # Keep the original source/level, but never format or retain that data.
    if private:
        record.msg = "knowledge_pdf_parser_warning"
        record.args = ()
        record.message = record.msg
        record.exc_info = record.exc_text = record.stack_info = None
    return True


@contextmanager
def _private_pdf_diagnostics():
    """Redact this parse's known pypdf page emitter, without silencing other threads."""
    thread_id = threading.get_ident()
    logger = logging.getLogger("pypdf._page")
    with _PDF_LOG_LOCK:
        # Share one filter while any private parser is active: removing a
        # per-call filter could skip a sibling filter during list iteration.
        if not _PDF_PARSE_THREADS:
            logger.addFilter(_redact_pdf_diagnostic)
        _PDF_PARSE_THREADS[thread_id] = _PDF_PARSE_THREADS.get(thread_id, 0) + 1
    try:
        yield
    finally:
        with _PDF_LOG_LOCK:
            _PDF_PARSE_THREADS[thread_id] -= 1
            if not _PDF_PARSE_THREADS[thread_id]:
                del _PDF_PARSE_THREADS[thread_id]
            if not _PDF_PARSE_THREADS:
                logger.removeFilter(_redact_pdf_diagnostic)


def parse_source(filename: str, data: bytes, limits: LibraryLimits) -> dict:
    """Parse an entire supported input or reject it; never return truncated success."""
    if not isinstance(data, bytes) or not data:
        raise LibraryError("empty_file")
    if len(data) > limits.max_document_bytes:
        raise LibraryError("document_too_large")
    # Both separator styles are stripped for display, never used as a storage path.
    name = PurePosixPath(str(filename).replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)[:160]
    extension = name.rsplit(".", 1)[-1].lower()
    if extension not in {"txt", "md", "markdown", "docx", "pdf"}:
        raise LibraryError("unsupported_document")
    try:
        if extension in {"txt", "md", "markdown"}:
            sections = _paragraphs(_decode(data), {})
        elif extension == "docx":
            sections = _docx(data)
        else:
            if not data.startswith(b"%PDF-"):
                raise LibraryError("invalid_pdf")
            with _private_pdf_diagnostics():
                sections = _pdf(data, limits)
    except LibraryError:
        raise
    except office.DocumentParseError as exc:
        raise LibraryError(exc.code) from exc
    except Exception as exc:
        raise LibraryError("document_parse_failed") from exc
    content = "\n\n".join(section["text"] for section in sections)
    if not content.strip():
        raise LibraryError("no_readable_text")
    if len(content) > limits.max_document_chars:
        raise LibraryError("document_text_too_large")
    return {"filename": name, "format": extension, "content": content, "sections": sections}
