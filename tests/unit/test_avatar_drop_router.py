from __future__ import annotations

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers.avatar_drop_router import router
from tests.unit.test_avatar_document_parser import _docx_bytes


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
def test_parse_document_endpoint_surfaces_parser_error_code():
    response = _client().post(
        "/api/avatar-drop/parse-document",
        files={"file": ("legacy.doc", b"legacy", "application/msword")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "legacy_office_unsupported"}
