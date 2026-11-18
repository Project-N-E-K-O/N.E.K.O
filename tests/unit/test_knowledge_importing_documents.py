"""Keep unified document text out of parser logs and preserve complete extraction."""

import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from knowledge.importing.documents import SourceError, SourceLimits, parse_source
import zipfile
from utils import document_parser as office


pytestmark = pytest.mark.unit_fast


def _pdf_with_text(operand=None):
    """Write actual readable PDF bytes, optionally invoking a missing XObject."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        NameObject("/XObject"): DictionaryObject(),
    })
    contents = b"BT /F1 12 Tf 72 720 Td (A complete private reference.) Tj ET\n"
    if operand:
        contents += ("/" + operand + " Do\n").encode("ascii")
    stream = DecodedStreamObject()
    stream.set_data(contents)
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    writer.close()
    return output.getvalue()


def test_real_pdf_xobject_warning_does_not_echo_document_controlled_operand(monkeypatch, caplog):
    """A recovered pypdf warning cannot disclose a PDF's private object name."""
    canary = "PRIVATE_PDF_XOBJECT_CANARY_1907"
    original_factory = logging.getLogRecordFactory()

    def cached_record(*args, **kwargs):
        record = original_factory(*args, **kwargs)
        if record.name == "pypdf._page":
            # An earlier log processor may have cached formatted exception text.
            record.message = record.exc_text = record.stack_info = canary
            record.exc_info = (RuntimeError, RuntimeError(canary), None)
        return record

    monkeypatch.setattr(logging, "_logRecordFactory", cached_record)
    caplog.set_level(logging.WARNING)
    parsed = parse_source("private-canary.pdf", _pdf_with_text(canary), SourceLimits())
    assert parsed["content"] == "A complete private reference."
    assert parsed["sections"][0]["location"] == {"page": 1, "paragraph": 1}
    warnings = [record for record in caplog.records if record.name == "pypdf._page"]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    assert warnings[0].getMessage() == "knowledge_pdf_parser_warning"
    assert warnings[0].message == "knowledge_pdf_parser_warning"
    assert warnings[0].args == ()
    assert warnings[0].exc_info is warnings[0].exc_text is warnings[0].stack_info is None
    assert canary not in caplog.text
    assert canary not in repr([(record.msg, record.args, record.exc_info) for record in caplog.records])


def test_normal_pdf_retains_text_and_removes_temporary_log_filter(caplog):
    """Normal extraction remains unchanged, and later diagnostics retain their values."""
    logger = logging.getLogger("pypdf._page")
    previous_filters = tuple(logger.filters)
    parsed = parse_source("normal.pdf", _pdf_with_text(), SourceLimits())
    assert parsed["content"] == "A complete private reference."
    assert tuple(logger.filters) == previous_filters
    assert not [record for record in caplog.records if record.name == "pypdf._page"]
    logger.warning("Ordinary parser diagnostic: %s", "OUTSIDE_PERSONAL_PARSE_1907")
    assert "OUTSIDE_PERSONAL_PARSE_1907" in caplog.text


def test_pdf_failure_removes_log_filter_after_a_recovered_warning(monkeypatch, caplog):
    """A failed private parse cannot leave redaction installed on its worker thread."""
    logger = logging.getLogger("pypdf._page")
    previous_filters = tuple(logger.filters)
    iterate = office._iter_pdf_pages_until

    def fail_after_page(reader, limit):
        yield from iterate(reader, limit)
        raise RuntimeError("PRIVATE_PARSE_EXCEPTION_1907")

    monkeypatch.setattr(office, "_iter_pdf_pages_until", fail_after_page)
    with pytest.raises(SourceError, match="document_parse_failed"):
        parse_source("private.pdf", _pdf_with_text("PRIVATE_FAILED_XOBJECT_1907"), SourceLimits())
    assert "knowledge_pdf_parser_warning" in caplog.text
    assert "PRIVATE_FAILED_XOBJECT_1907" not in caplog.text
    assert "PRIVATE_PARSE_EXCEPTION_1907" not in caplog.text
    assert tuple(logger.filters) == previous_filters
    logger.warning("Ordinary parser diagnostic: %s", "AFTER_PERSONAL_FAILURE_1907")
    assert "AFTER_PERSONAL_FAILURE_1907" in caplog.text


def test_private_pdf_scope_does_not_change_parallel_attachment_logs(monkeypatch, caplog):
    """An actual attachment parse in another thread keeps its original warning."""
    caller = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    iterate = office._iter_pdf_pages_until

    def pause_private_parse(reader, limit):
        if threading.get_ident() != caller:
            entered.set()
            assert release.wait(5), "private parser was not released"
        yield from iterate(reader, limit)

    monkeypatch.setattr(office, "_iter_pdf_pages_until", pause_private_parse)
    private = _pdf_with_text("PRIVATE_CONCURRENT_XOBJECT_1907")
    attachment = _pdf_with_text("ATTACHMENT_ORIGINAL_XOBJECT_1907")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(parse_source, "private.pdf", private, SourceLimits())
        try:
            assert entered.wait(5), "private parser did not enter its scope"
            parsed_attachment = office.parse_document("ordinary.pdf", "application/pdf", attachment)
        finally:
            release.set()
        parsed_private = future.result(timeout=5)
    assert "A complete private reference." in parsed_attachment["content"]
    assert parsed_private["content"] == "A complete private reference."
    assert "PRIVATE_CONCURRENT_XOBJECT_1907" not in caplog.text
    records = [record for record in caplog.records if record.name == "pypdf._page"]
    assert any(record.thread != caller and record.getMessage() == "knowledge_pdf_parser_warning" for record in records)
    assert any(record.thread == caller and "Impossible to decode XFormObject /ATTACHMENT_ORIGINAL_XOBJECT_1907" in record.getMessage() for record in records)


def test_overlapping_private_pdf_scope_exit_cannot_skip_another_parsers_filter(monkeypatch, caplog):
    """Force one scope to exit while another real PDF warning is being filtered."""
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    first_thread = [None]
    previous_filters = tuple(logging.getLogger("pypdf._page").filters)
    iterate = office._iter_pdf_pages_until
    original_factory = logging.getLogRecordFactory()

    def pause_first(reader, limit):
        if threading.get_ident() == first_thread[0]:
            entered.set()
            assert release.wait(5), "first private parser was not released"
        yield from iterate(reader, limit)

    class BarrierRecord(logging.LogRecord):
        def __getattribute__(self, name):
            # The first per-scope filter used to disappear from logger.filters
            # here, advancing its iterator past the second scope's filter.
            if name == "thread" and not self.__dict__.get("_privacy_barrier_read"):
                self.__dict__["_privacy_barrier_read"] = True
                release.set()
                assert finished.wait(5), "first private parser did not exit"
            return super().__getattribute__(name)

    def record_factory(*args, **kwargs):
        record = original_factory(*args, **kwargs)
        if record.name == "pypdf._page":
            record.__class__ = BarrierRecord
        return record

    monkeypatch.setattr(office, "_iter_pdf_pages_until", pause_first)
    monkeypatch.setattr(logging, "_logRecordFactory", record_factory)
    normal = _pdf_with_text()
    private = _pdf_with_text("PRIVATE_OVERLAP_XOBJECT_1907")

    def first_parse():
        first_thread[0] = threading.get_ident()
        try:
            return parse_source("first.pdf", normal, SourceLimits())
        finally:
            finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_parse)
        try:
            assert entered.wait(5), "first private parser did not enter its scope"
            second = pool.submit(parse_source, "second.pdf", private, SourceLimits())
            assert second.result(timeout=10)["content"] == "A complete private reference."
        finally:
            release.set()
        assert first.result(timeout=5)["content"] == "A complete private reference."
    assert "PRIVATE_OVERLAP_XOBJECT_1907" not in caplog.text
    assert "knowledge_pdf_parser_warning" in caplog.text
    assert tuple(logging.getLogger("pypdf._page").filters) == previous_filters


def test_text_decoding_and_complete_output():
    text = "第一段中文资料。\n\n第二段保留。\n\n第一段中文资料。"
    parsed = parse_source("notes.md", text.encode("utf-16"), SourceLimits())
    assert parsed["content"] == text
    assert [part["location"]["paragraph"] for part in parsed["sections"]] == [1, 2, 3]
    assert parse_source("old.txt", text.encode("gb18030"), SourceLimits())["content"] == text


def test_docx_preserves_repeated_paragraphs_and_reports_real_positions():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Repeated</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Repeated</w:t></w:r></w:p></w:body></w:document>',
        )
    parsed = parse_source("draft.docx", output.getvalue(), SourceLimits())
    assert parsed["content"] == "Repeated\n\nRepeated"
    assert parsed["sections"][1]["location"] == {"member": "word/document.xml", "paragraph": 2}


@pytest.mark.parametrize("filename,data,limits,reason", [
    ("empty.txt", b"", SourceLimits(), "empty_file"),
    ("large.txt", b"1234", SourceLimits(max_document_bytes=3), "document_too_large"),
    ("long.txt", b"1234", SourceLimits(max_document_chars=3), "document_text_too_large"),
    ("binary.txt", b"\x00\x00\x00\x01", SourceLimits(), "invalid_text_encoding"),
    ("table.xlsx", b"123", SourceLimits(), "unsupported_document"),
    ("wrong.pdf", b"123", SourceLimits(), "invalid_pdf"),
])
def test_document_failures_never_report_partial_success(filename, data, limits, reason):
    with pytest.raises(SourceError, match=reason):
        parse_source(filename, data, limits)
