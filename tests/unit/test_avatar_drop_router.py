from __future__ import annotations

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

import main_routers.avatar_drop_router as avatar_drop_router
from main_routers.avatar_drop_router import router
from tests.unit.test_document_parser import _docx_bytes


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.unit
def test_parse_document_endpoint_returns_text_item_for_supported_document():
    response = _client().post(
        "/api/avatar-drop/parse-document",
        files={
            "file": (
                "unsafe<> name.docx",
                _docx_bytes("Endpoint hello"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    item = payload["item"]
    assert payload["ok"] is True
    assert item["type"] == "text"
    assert item["name"] == "unsafe name.docx"
    assert item["documentType"] == "docx"
    assert item["encoding"] == "document-parser"
    assert item["truncated"] is False
    assert "Endpoint hello" in item["content"]
    assert item["chars"] == len(item["content"])


@pytest.mark.unit
def test_parse_document_endpoint_runs_parser_off_event_loop(monkeypatch):
    calls = []

    async def fake_to_thread(func, *args):
        calls.append((func, args))
        return func(*args)

    monkeypatch.setattr(avatar_drop_router.asyncio, "to_thread", fake_to_thread)

    response = _client().post(
        "/api/avatar-drop/parse-document",
        files={
            "file": (
                "threaded.docx",
                _docx_bytes("Threaded parser hello"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200
    assert "Threaded parser hello" in response.json()["item"]["content"]
    assert calls
    assert calls[0][0] is avatar_drop_router.parse_document
    assert calls[0][1][0] == "threaded.docx"


@pytest.mark.unit
def test_parse_document_endpoint_preserves_extension_after_filename_truncation():
    response = _client().post(
        "/api/avatar-drop/parse-document",
        files={
            "file": (
                ("a" * 180) + ".docx",
                _docx_bytes("Long name hello"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert len(item["name"]) == 160
    assert item["name"].endswith(".docx")
    assert item["documentType"] == "docx"
    assert "Long name hello" in item["content"]


@pytest.mark.unit
def test_parse_document_endpoint_keeps_truncated_content_locale_neutral(monkeypatch):
    def fake_parse_document(filename, content_type, data):
        return {
            "document_type": "docx",
            "content": "Truncated hello",
            "truncated": True,
            "meta": {},
        }

    monkeypatch.setattr(avatar_drop_router, "parse_document", fake_parse_document)

    response = _client().post(
        "/api/avatar-drop/parse-document",
        files={
            "file": (
                "truncated.docx",
                _docx_bytes("Ignored"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["truncated"] is True
    assert item["content"] == "Truncated hello"
    assert "内容已按长度限制截断" not in item["content"]
    assert item["chars"] == len("Truncated hello")


@pytest.mark.unit
def test_parse_document_endpoint_strips_c1_controls_from_filename():
    response = _client().post(
        "/api/avatar-drop/parse-document",
        files={
            "file": (
                "bad\x85name.docx",
                _docx_bytes("Filename hello"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["item"]["name"] == "badname.docx"


@pytest.mark.unit
def test_parse_document_endpoint_surfaces_parser_error_code():
    response = _client().post(
        "/api/avatar-drop/parse-document",
        files={"file": ("legacy.doc", b"legacy", "application/msword")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "legacy_office_unsupported"}
