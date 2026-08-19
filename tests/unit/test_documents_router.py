from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from starlette.datastructures import Headers, UploadFile

import utils.document_upload as document_upload
from main_routers.avatar_drop_router import router as avatar_drop_router
from plugin.server.routes.documents import (
    parse_document_upload,
    router as documents_router,
)
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
@pytest.mark.parametrize(
    "browser_header",
    [
        {"Origin": "https://attacker.example"},
        {"Referer": "https://attacker.example/drive-by"},
    ],
)
def test_documents_parse_rejects_untrusted_browser_origin_before_parsing(
    browser_header,
):
    # Route-level coverage must not start plugin hosts, whose process-wide
    # storage-root export would leak into unrelated unit tests.
    client = TestClient(
        build_plugin_server_app(), base_url="http://127.0.0.1:48916"
    )
    try:
        response = client.post(
            "/api/documents/parse",
            headers=browser_header,
            files={"file": ("attack.pdf", _blank_pdf_bytes(), "application/pdf")},
        )
    finally:
        client.close()

    assert response.status_code == 403
    assert response.json()["detail"] == {"code": "untrusted_origin"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_documents_parse_rejects_untrusted_origin_before_reading_multipart_body():
    route = next(
        route
        for route in documents_router.routes
        if getattr(route, "path", "") == "/api/documents/parse"
    )
    assert route.dependant.body_params == []

    body_read = False

    async def receive():
        nonlocal body_read
        body_read = True
        raise AssertionError("untrusted request body must not be read")

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/documents/parse",
            "headers": [
                (b"host", b"127.0.0.1:48916"),
                (b"origin", b"https://attacker.example"),
            ],
            "scheme": "http",
            "server": ("127.0.0.1", 48916),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
            "query_string": b"",
        },
        receive,
    )

    with pytest.raises(HTTPException) as raised:
        await parse_document_upload(request)

    assert raised.value.status_code == 403
    assert raised.value.detail == {"code": "untrusted_origin"}
    assert body_read is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_documents_parse_stops_streaming_when_file_part_exceeds_limit():
    boundary = b"neko-document-boundary"
    prefix = (
        b"--"
        + boundary
        + b'\r\nContent-Disposition: form-data; name="file"; filename="large.pdf"'
        + b"\r\nContent-Type: application/pdf\r\n\r\n"
    )
    file_at_limit = b"%PDF-" + b"x" * (MAX_DOCUMENT_BYTES - 5)
    overflow = b"y" * 1024
    suffix = b"\r\n--" + boundary + b"--\r\n"
    messages = [
        {"type": "http.request", "body": prefix, "more_body": True},
        {"type": "http.request", "body": file_at_limit, "more_body": True},
        {"type": "http.request", "body": overflow, "more_body": True},
        {"type": "http.request", "body": suffix, "more_body": False},
    ]
    receive_count = 0

    async def receive():
        nonlocal receive_count
        message = messages[receive_count]
        receive_count += 1
        return message

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/documents/parse",
            "headers": [
                (b"host", b"127.0.0.1:48916"),
                (b"origin", b"http://127.0.0.1:48916"),
                (
                    b"content-type",
                    b"multipart/form-data; boundary=" + boundary,
                ),
            ],
            "scheme": "http",
            "server": ("127.0.0.1", 48916),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
            "query_string": b"",
        },
        receive,
    )

    with pytest.raises(HTTPException) as raised:
        await parse_document_upload(request)

    assert raised.value.status_code == 400
    assert raised.value.detail == {"code": "document_too_large"}
    assert receive_count == 3


@pytest.mark.unit
def test_documents_parse_returns_public_code_for_multipart_shape_errors():
    response = _client(documents_router).post(
        "/api/documents/parse",
        data={"unexpected": "field"},
        files={"unexpected_file": ("notes.pdf", b"not parsed", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "document_parse_failed"}


@pytest.mark.unit
def test_documents_parse_allows_loopback_browser_origin():
    # Keep this at route level for the same storage-root isolation guarantee.
    client = TestClient(
        build_plugin_server_app(), base_url="http://127.0.0.1:48916"
    )
    try:
        response = client.post(
            "/api/documents/parse",
            headers={"Origin": "http://localhost:48911"},
            files={
                "file": (
                    "notes.pdf",
                    _pdf_bytes("Loopback endpoint"),
                    "application/pdf",
                )
            },
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert "Loopback endpoint" in response.json()["document"]["content"]


@pytest.mark.unit
def test_plugin_server_redirects_model_settings_to_main_server(monkeypatch):
    import config

    monkeypatch.setattr(config, "MAIN_SERVER_PORT", 49123)
    app = build_plugin_server_app()
    route = next(
        route for route in app.routes if getattr(route, "path", "") == "/api_key"
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/api_key",
            "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:48916")],
            "server": ("127.0.0.1", 48916),
        }
    )
    response = asyncio.run(route.endpoint(request))

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
