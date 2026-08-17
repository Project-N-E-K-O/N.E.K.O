from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from starlette.datastructures import Headers, UploadFile

import utils.document_upload as document_upload
from main_routers.avatar_drop_router import router as avatar_drop_router
from plugin.server.routes.documents import router as documents_router
from plugin.server.http_app import build_plugin_server_app
from tests.unit.test_document_parser import (
    _blank_pdf_bytes,
    _docx_bytes,
    _pdf_bytes,
    _pptx_bytes,
    _xlsx_bytes,
)
from utils.document_parser import MAX_DOCUMENT_BYTES


def _client(*routers) -> TestClient:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    return TestClient(app)


def _encrypted_pdf_bytes() -> bytes:
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret")
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.unit
def test_plugin_server_registers_hosted_document_parse_route():
    app = build_plugin_server_app()

    matching = [
        route
        for route in app.routes
        if getattr(route, "path", "") == "/api/documents/parse"
    ]

    assert len(matching) == 1
    assert "POST" in matching[0].methods


@pytest.mark.unit
def test_plugin_server_redirects_model_settings_to_main_server(monkeypatch):
    import config

    monkeypatch.setattr(config, "MAIN_SERVER_PORT", 49123)
    app = build_plugin_server_app()
    route = next(
        route for route in app.routes if getattr(route, "path", "") == "/api_key"
    )

    response = asyncio.run(route.endpoint())

    assert response.status_code == 307
    assert response.headers["location"] == "http://127.0.0.1:49123/api_key"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filename", "content_type", "data", "source_type", "expected_text"),
    [
        ("notes.pdf", "application/pdf", _pdf_bytes("PDF endpoint"), "pdf", "PDF endpoint"),
        (
            "notes.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx_bytes("DOCX endpoint"),
            "docx",
            "DOCX endpoint",
        ),
    ],
)
def test_documents_parse_returns_neutral_document_shape(
    filename,
    content_type,
    data,
    source_type,
    expected_text,
):
    response = _client(documents_router).post(
        "/api/documents/parse",
        files={"file": (filename, data, content_type)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    document = payload["document"]
    assert document["name"] == filename
    assert document["sourceType"] == source_type
    assert document["originalSize"] == len(data)
    assert document["chars"] == len(document["content"])
    assert document["encoding"] == "document-parser"
    assert document["truncated"] is False
    assert expected_text in document["content"]
    assert set(document) == {
        "name",
        "sourceType",
        "mime",
        "originalSize",
        "chars",
        "encoding",
        "truncated",
        "content",
        "meta",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filename", "content_type", "data", "code"),
    [
        ("legacy.doc", "application/msword", b"legacy", "legacy_office_unsupported"),
        ("macro.docm", "application/octet-stream", b"macro", "macro_document_unsupported"),
        ("sheet.xlsx", "application/octet-stream", _xlsx_bytes("cell"), "unsupported_document"),
        ("slides.pptx", "application/octet-stream", _pptx_bytes("slide"), "unsupported_document"),
        ("broken.pdf", "application/pdf", b"not a pdf", "invalid_pdf"),
        ("broken.docx", "application/octet-stream", b"not a zip", "invalid_ooxml"),
        ("scan.pdf", "application/pdf", _blank_pdf_bytes(), "no_readable_text"),
        (
            "encrypted.pdf",
            "application/pdf",
            _encrypted_pdf_bytes(),
            "encrypted_pdf_unsupported",
        ),
    ],
)
def test_documents_parse_rejects_out_of_scope_or_invalid_documents(
    filename,
    content_type,
    data,
    code,
):
    response = _client(documents_router).post(
        "/api/documents/parse",
        files={"file": (filename, data, content_type)},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {"code": code}


@pytest.mark.unit
def test_documents_parse_rejects_upload_larger_than_limit():
    response = _client(documents_router).post(
        "/api/documents/parse",
        files={
            "file": (
                "large.pdf",
                b"%PDF-" + b"x" * MAX_DOCUMENT_BYTES,
                "application/pdf",
            )
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "document_too_large"}


@pytest.mark.unit
def test_documents_parse_maps_internal_parser_errors_to_public_contract(monkeypatch):
    def fake_parser(filename, content_type, data):
        from utils.document_parser import DocumentParseError

        raise DocumentParseError("zip_uncompressed_too_large")

    monkeypatch.setattr(document_upload, "parse_document", fake_parser)
    response = _client(documents_router).post(
        "/api/documents/parse",
        files={"file": ("guarded.docx", _docx_bytes("ignored"), "application/octet-stream")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "document_parse_failed"}


@pytest.mark.unit
def test_shared_upload_runs_parser_off_event_loop_and_closes_upload(monkeypatch):
    calls = []

    async def fake_to_thread(func, *args):
        calls.append((func, args))
        return func(*args)

    monkeypatch.setattr(document_upload.asyncio, "to_thread", fake_to_thread)
    response = _client(documents_router).post(
        "/api/documents/parse",
        files={"file": ("threaded.docx", _docx_bytes("Threaded"), "application/octet-stream")},
    )

    assert response.status_code == 200
    assert calls
    assert calls[0][0] is document_upload.parse_document


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shared_upload_closes_original_file_after_parsing():
    upload = UploadFile(
        filename="ephemeral.docx",
        file=io.BytesIO(_docx_bytes("Ephemeral")),
        headers=Headers({"content-type": "application/octet-stream"}),
    )

    parsed = await document_upload.parse_uploaded_document(
        upload,
        allowed_document_types={"docx"},
    )

    assert parsed.document_type == "docx"
    assert upload.file.closed is True


@pytest.mark.unit
def test_new_and_avatar_drop_routes_share_parser_result_contract(monkeypatch):
    data = _docx_bytes("Shared result")
    client = _client(avatar_drop_router, documents_router)

    old_response = client.post(
        "/api/avatar-drop/parse-document",
        files={"file": ("shared.docx", data, "application/octet-stream")},
    )
    new_response = client.post(
        "/api/documents/parse",
        files={"file": ("shared.docx", data, "application/octet-stream")},
    )

    assert old_response.status_code == new_response.status_code == 200
    old_item = old_response.json()["item"]
    new_document = new_response.json()["document"]
    assert old_item["content"] == new_document["content"]
    assert old_item["documentType"] == new_document["sourceType"]
    assert old_item["truncated"] == new_document["truncated"]
    assert old_item["meta"] == new_document["meta"]


@pytest.mark.unit
def test_documents_parse_preserves_truncation_metadata(monkeypatch):
    def fake_parser(filename, content_type, data):
        return {
            "document_type": "docx",
            "content": "Extracted prefix",
            "truncated": True,
            "meta": {"pages": 41},
        }

    monkeypatch.setattr(document_upload, "parse_document", fake_parser)
    response = _client(documents_router).post(
        "/api/documents/parse",
        files={"file": ("long.docx", _docx_bytes("ignored"), "application/octet-stream")},
    )

    assert response.status_code == 200
    document = response.json()["document"]
    assert document["truncated"] is True
    assert document["content"] == "Extracted prefix"
    assert document["meta"] == {"pages": 41}
