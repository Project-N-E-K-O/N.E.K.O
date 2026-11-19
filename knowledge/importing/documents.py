"""Extract bounded complete text and honest locations from managed originals."""

from __future__ import annotations

import logging
import re
import threading
from contextlib import contextmanager
from pathlib import PurePosixPath
from dataclasses import dataclass

from utils import document_parser as office

from ..models import KnowledgePackError as SourceError


@dataclass(frozen=True)
class SourceLimits:
    """Independent import limits without changing attachment defaults."""

    max_document_bytes: int = 16 * 1024 * 1024
    max_document_chars: int = 256_000
    max_pdf_pages: int = 40


_PDF_PARSE_THREADS: dict[int, int] = {}
_PDF_LOG_LOCK = threading.Lock()
_PDF_RECORD_FACTORY = None


def _private_pdf_record(*args, **kwargs):
    """Redact every pypdf child emitter before any handler formats its record."""
    record = _PDF_RECORD_FACTORY(*args, **kwargs)
    if record.name == "pypdf" or record.name.startswith("pypdf."):
        _redact_pdf_diagnostic(record)
    return record


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
    raise SourceError("invalid_text_encoding")


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
    """Redact all private pypdf emitters, without changing other threads' logs."""
    global _PDF_RECORD_FACTORY
    thread_id = threading.get_ident()
    with _PDF_LOG_LOCK:
        # Logger filters do not cover child loggers during propagation. One
        # shared record factory covers even dynamically created pypdf emitters.
        if not _PDF_PARSE_THREADS:
            _PDF_RECORD_FACTORY = logging.getLogRecordFactory()
            logging.setLogRecordFactory(_private_pdf_record)
        _PDF_PARSE_THREADS[thread_id] = _PDF_PARSE_THREADS.get(thread_id, 0) + 1
    try:
        yield
    finally:
        with _PDF_LOG_LOCK:
            _PDF_PARSE_THREADS[thread_id] -= 1
            if not _PDF_PARSE_THREADS[thread_id]:
                del _PDF_PARSE_THREADS[thread_id]
            if not _PDF_PARSE_THREADS:
                if logging.getLogRecordFactory() is _private_pdf_record:
                    logging.setLogRecordFactory(_PDF_RECORD_FACTORY)
                # An unrelated logging thread may already be inside the old
                # factory wrapper; keep its delegate valid after uninstall.


def parse_source(filename: str, data: bytes, limits: SourceLimits) -> dict:
    """Parse an entire supported input or reject it; never return truncated success."""
    if not isinstance(data, bytes) or not data:
        raise SourceError("empty_file")
    if len(data) > limits.max_document_bytes:
        raise SourceError("document_too_large")
    # Both separator styles are stripped for display, never used as a storage path.
    name = PurePosixPath(str(filename).replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    extension = name.rsplit(".", 1)[-1].lower()
    if extension not in {"txt", "md", "markdown", "docx", "pdf"}:
        raise SourceError("unsupported_document")
    # Recognize the complete suffix before bounding the display name.
    suffix = "." + extension
    name = name[:160 - len(suffix)] + suffix if len(name) > 160 else name
    parser_limits = office.DocumentLimits(max_document_bytes=limits.max_document_bytes,
                                          max_extracted_chars=limits.max_document_chars,
                                          max_pdf_pages=limits.max_pdf_pages)
    try:
        if extension in {"txt", "md", "markdown"}:
            text = _decode(data)
            # Reject guaranteed oversize text before allocating paragraph/location records.
            if len(text) > limits.max_document_chars:
                raise SourceError("document_text_too_large")
            sections = _paragraphs(text, {})
        elif extension == "docx":
            sections = office.parse_document_sections(name, data, limits=parser_limits)
        else:
            if not data.startswith(b"%PDF-"):
                raise SourceError("invalid_pdf")
            with _private_pdf_diagnostics():
                sections = office.parse_document_sections(name, data, limits=parser_limits)
    except SourceError:
        raise
    except office.DocumentParseError as exc:
        raise SourceError(exc.code) from exc
    except Exception as exc:
        raise SourceError("document_parse_failed") from exc
    content = "\n\n".join(section["text"] for section in sections)
    if not content.strip():
        raise SourceError("no_readable_text")
    if len(content) > limits.max_document_chars:
        raise SourceError("document_text_too_large")
    return {"filename": name, "format": extension, "content": content, "sections": sections}
