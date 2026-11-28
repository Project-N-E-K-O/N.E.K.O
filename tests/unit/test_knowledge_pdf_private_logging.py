"""Compose private PDF redaction with external logging factory wrappers safely."""

import logging

import pytest

from services.knowledge.importing import documents


pytestmark = pytest.mark.unit_fast


def test_external_factory_wrapping_a_private_parse_survives_later_parses(monkeypatch, caplog):
    original_factory = logging.getLogRecordFactory()
    external_factory = None
    parses = 0

    def parse(*_, **__):
        nonlocal external_factory, parses
        parses += 1
        if parses == 1:
            # Libraries compose factories by retaining the one they replace.
            # Installing during the first private scope leaves this chain alive
            # after that scope exits, and later scopes must not retarget it.
            previous = logging.getLogRecordFactory()

            def factory(*args, **kwargs):
                record = previous(*args, **kwargs)
                record.external_marker = "external-metadata-preserved"
                return record

            external_factory = factory
            logging.setLogRecordFactory(factory)
        logging.getLogger("pypdf.new_child.emitter").warning("Private value: %s", "PRIVATE_FACTORY_CANARY")
        logging.getLogger("other_library.factory_review").warning("Ordinary value: %s", "PUBLIC_FACTORY_VALUE")
        return [{"text": "Readable reference.", "location": {"page": 1}}]

    monkeypatch.setattr(documents.office, "parse_document_sections", parse)
    caplog.set_level(logging.WARNING)
    try:
        for _ in range(3):
            assert documents.parse_source("notes.pdf", b"%PDF-synthetic", documents.SourceLimits())["content"] == "Readable reference."
            assert logging.getLogRecordFactory() is external_factory
        logging.getLogger("pypdf.new_child.emitter").warning("Outside value: %s", "PUBLIC_PDF_AFTER_PARSE")
    finally:
        # A red run must not leave its intentionally recursive global factory
        # installed when pytest formats and reports the failure.
        logging.setLogRecordFactory(original_factory)

    private = [record for record in caplog.records if record.name == "pypdf.new_child.emitter"]
    assert [record.getMessage() for record in private] == ["knowledge_pdf_parser_warning"] * 3 + [
        "Outside value: PUBLIC_PDF_AFTER_PARSE",
    ]
    ordinary = [record for record in caplog.records if record.name == "other_library.factory_review"]
    assert len(ordinary) == 3
    assert all(record.getMessage() == "Ordinary value: PUBLIC_FACTORY_VALUE" for record in ordinary)
    assert all(record.external_marker == "external-metadata-preserved" for record in private + ordinary)
    assert "PRIVATE_FACTORY_CANARY" not in caplog.text
    assert not documents._PDF_PARSE_THREADS
