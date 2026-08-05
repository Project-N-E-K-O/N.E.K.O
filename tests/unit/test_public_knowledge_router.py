from __future__ import annotations

import hashlib
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge.api import KnowledgeEntry, canonical_pack_bytes
from knowledge.builtin import open_builtin_knowledge
from knowledge.engine.store import KnowledgeStore


def _entry(title: str, source: str = "source:chime") -> KnowledgeEntry:
    return KnowledgeEntry(
        title=title,
        terms={"alias": (), "recognition": ()},
        tags=(source, "type:reference"),
        summary="A compact summary",
        content="Meaning\n- A typical use",
    )


def _client(monkeypatch, tmp_path) -> TestClient:
    import main_routers.public_knowledge_router as module

    monkeypatch.setattr(
        module,
        "get_config_manager",
        lambda: SimpleNamespace(knowledge_dir=tmp_path),
    )
    monkeypatch.setattr(module, "_validate_mutation", lambda *_args: None)
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app)


def _pack(pack_id: str = "fixture-pack") -> dict:
    return {
        "schema_version": 1,
        "pack_id": pack_id,
        "collection_id": "meme",
        "source": {
            "name": "Fixture",
            "homepage": "https://example.invalid/fixture",
            "license": "CC0-1.0",
        },
        "entries": [
            {
                "title": "market phrase",
                "terms": {"alias": [], "recognition": []},
                "tags": ["type:reference"],
                "summary": "Market meaning",
                "content": "Meaning\n- Market use",
            }
        ],
    }


def test_management_lists_builtin_collections_and_entries(monkeypatch, tmp_path):
    service = open_builtin_knowledge(tmp_path)
    KnowledgeStore(service.database_path("meme")).upsert(_entry("meme fixture"))
    client = _client(monkeypatch, tmp_path)

    collections = client.get("/api/public-knowledge/collections").json()
    listing = client.get(
        "/api/public-knowledge/entries",
        params={"collection": "meme", "limit": "10"},
    ).json()
    detail = client.get(
        "/api/public-knowledge/entry",
        params={
            "collection": "meme",
            "source": "chime",
            "title": "meme fixture",
        },
    ).json()

    assert {item["collection_id"] for item in collections["collections"]} == {
        "meme",
        "corpora",
    }
    assert listing["items"][0]["source"]["name"] == "CHIME"
    assert listing["items"][0]["disabled"] is False
    assert detail["entry"]["content"] == "Meaning\n- A typical use"


def test_disable_restore_and_auto_context_are_local_mutations(monkeypatch, tmp_path):
    service = open_builtin_knowledge(tmp_path)
    KnowledgeStore(service.database_path("meme")).upsert(_entry("toggle fixture"))
    client = _client(monkeypatch, tmp_path)

    disabled = client.post(
        "/api/public-knowledge/entry/disabled",
        json={
            "collection": "meme",
            "source": "chime",
            "title": "toggle fixture",
            "disabled": True,
        },
    ).json()
    listing = client.get(
        "/api/public-knowledge/entries",
        params={"collection": "meme"},
    ).json()
    auto = client.post(
        "/api/public-knowledge/collection/auto-context",
        json={"collection": "meme", "enabled": False},
    ).json()

    assert disabled == {"ok": True, "disabled": True, "disabled_entries": 1}
    assert listing["items"][0]["disabled"] is True
    assert auto == {"ok": True, "collection": "meme", "auto_context": False}


def test_pack_import_toggle_remove_round_trip(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pack = _pack()

    installed = client.post(
        "/api/public-knowledge/packs/import",
        json={"pack": pack},
    ).json()
    toggled = client.post(
        "/api/public-knowledge/packs/auto-context",
        json={"collection": "meme", "pack_id": "fixture-pack", "enabled": True},
    ).json()
    packs = client.get(
        "/api/public-knowledge/packs",
        params={"collection": "meme"},
    ).json()
    removed = client.post(
        "/api/public-knowledge/packs/remove",
        json={"collection": "meme", "pack_id": "fixture-pack"},
    ).json()

    assert installed["ok"] is True
    assert toggled == {"ok": True, "auto_context": True}
    assert packs["packs"][0]["pack_id"] == "fixture-pack"
    assert removed == {"ok": True, "removed_entries": 1}


def test_subscription_handoff_verifies_hash(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pack = _pack("market-fixture")
    digest = hashlib.sha256(canonical_pack_bytes(pack)).hexdigest()
    payload = {
        "protocol_version": 1,
        "subscription": {
            "provider": "plugin-market",
            "remote_id": "knowledge/market-fixture",
            "version": "1.0.0",
            "channel": "stable",
            "artifact_sha256": digest,
        },
        "pack": pack,
    }

    accepted = client.post(
        "/api/public-knowledge/subscriptions/apply",
        json=payload,
    ).json()
    payload["subscription"]["artifact_sha256"] = "0" * 64
    rejected = client.post(
        "/api/public-knowledge/subscriptions/apply",
        json=payload,
    ).json()

    assert accepted["ok"] is True
    assert accepted["remote_id"] == "knowledge/market-fixture"
    assert rejected["issues"][0]["code"] == "artifact_hash_mismatch"


def test_validation_failures_are_stable_and_content_free(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    invalid_id = client.get(
        "/api/public-knowledge/packs",
        params={"collection": "../secret"},
    ).json()
    invalid_pack = client.post(
        "/api/public-knowledge/packs/import",
        json={"pack": {"schema_version": 1, "pack_id": "secret value"}},
    ).json()
    invalid_page = client.get(
        "/api/public-knowledge/entries",
        params={"collection": "meme", "limit": "101"},
    ).json()

    assert invalid_id["issues"] == [
        {
            "path": "collection",
            "code": "invalid_identifier",
            "message": "must be a valid knowledge identifier",
        }
    ]
    assert invalid_pack["issues"][0]["path"] == "pack_id"
    assert "secret value" not in str(invalid_pack)
    assert invalid_page["issues"][0]["code"] == "out_of_range"


def test_pack_request_body_limit_is_enforced_before_validation(
    monkeypatch,
    tmp_path,
):
    import main_routers.public_knowledge_router as module

    monkeypatch.setattr(module, "MAX_PACK_BYTES", 32)
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/public-knowledge/packs/import",
        content=b"{" + b" " * (module._PACK_ENVELOPE_OVERHEAD_BYTES + 33),
        headers={"content-type": "application/json"},
    ).json()

    assert response["issues"][0]["code"] == "body_too_large"


def test_diagnostics_expose_no_query_or_entry_content(monkeypatch, tmp_path):
    import main_routers.public_knowledge_router as module

    monkeypatch.setattr(
        module,
        "list_recent_knowledge_routes",
        lambda: (
            {
                "timestamp": "2026-08-04T00:00:00Z",
                "collection_id": "meme",
                "entry_title": "",
                "source_tag": "",
                "match_mode": "strong",
                "card_delivered": True,
                "result": "matched",
                "error_type": "",
            },
        ),
    )
    client = _client(monkeypatch, tmp_path)

    payload = client.get("/api/public-knowledge/diagnostics/recent").json()

    assert payload["items"][0]["entry_title"] == ""
    assert "query" not in payload["items"][0]


def test_mutations_require_local_origin_and_csrf(monkeypatch, tmp_path):
    import main_routers.public_knowledge_router as module

    monkeypatch.setattr(
        module,
        "get_config_manager",
        lambda: SimpleNamespace(knowledge_dir=tmp_path),
    )
    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app)

    response = client.post(
        "/api/public-knowledge/collection/auto-context",
        json={"collection": "meme", "enabled": False},
    )

    assert response.status_code == 403
    payload = response.json()
    assert payload["ok"] is False
    assert payload["issues"][0]["code"] == "csrf_validation_failed"
