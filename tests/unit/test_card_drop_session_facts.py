from __future__ import annotations

import asyncio
import importlib
import json
import logging
import threading
from pathlib import Path

import pytest
import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import main_routers.card_drop_router as C
import main_logic.card_forge_facts as F
from main_logic.card_forge_facts import ActiveNekoContext, build_forge_facts_payload

pytestmark = pytest.mark.unit

USER_A_ID = "11111111-1111-4111-8111-111111111111"
USER_B_ID = "22222222-2222-4222-8222-222222222222"


@pytest.mark.parametrize(
    "malformed_origin",
    [
        "http://localhost:notaport",
        "http://localhost:65536",
        "http://[::1",
    ],
)
def test_same_originish_rejects_malformed_ports(malformed_origin):
    assert C._same_originish(malformed_origin, "http://localhost:48911") is False


def _main_server_request(*, method: str = "POST", origin: str = "") -> Request:
    headers = [(b"host", b"127.0.0.1:48911")]
    if origin:
        headers.append((b"origin", origin.encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "server": ("127.0.0.1", 48911),
            "client": ("127.0.0.1", 50000),
            "root_path": "",
            "path": "/api/card-drop/active-character",
            "raw_path": b"/api/card-drop/active-character",
            "query_string": b"",
            "headers": headers,
        }
    )


@pytest.mark.asyncio
async def test_main_active_character_post_allows_native_and_local_origin(monkeypatch):
    from app.main_server import web_app

    snapshot: dict[str, str] = {}
    monkeypatch.setattr(web_app, "_card_drop_active_character", snapshot)

    native_response = await web_app.set_card_drop_active_character(
        _main_server_request(),
        {"name": "Native"},
    )
    local_response = await web_app.set_card_drop_active_character(
        _main_server_request(origin="http://localhost:48911"),
        {"name": "Local"},
    )

    assert native_response == {"ok": True}
    assert local_response == {"ok": True}
    assert snapshot == {"name": "Local"}


@pytest.mark.asyncio
async def test_main_active_character_get_falls_back_to_configured_catgirl(monkeypatch):
    from app.main_server import web_app

    monkeypatch.setattr(web_app, "_card_drop_active_character", {})

    async def fallback_identity():
        return "YUI", "Human"

    monkeypatch.setattr(
        web_app,
        "_fallback_active_character_identity",
        fallback_identity,
    )

    response = await web_app.get_card_drop_active_character(
        _main_server_request(),
        include_avatar=True,
    )

    assert response.status_code == 200
    assert response.body
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["name"] == "YUI"
    assert payload["master_name"] == "Human"
    assert "dataUrl" not in payload
    assert "characterReferenceDataUrl" not in payload


def test_main_active_character_exposes_only_canonical_card_drop_routes():
    from app.main_server import web_app

    routes = {
        (route.path, method)
        for route in web_app.app.routes
        for method in getattr(route, "methods", set())
    }
    for method in ("GET", "POST", "OPTIONS"):
        assert ("/api/card-drop/active-character", method) in routes
        assert ("/card-forge/active-character", method) not in routes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"name": "Character B"}, {"name": "Character B"}),
        (
            {"name": "Character B", "dataUrl": "avatar-b"},
            {"name": "Character B", "dataUrl": "avatar-b"},
        ),
        (
            {"name": "Character B", "characterReferenceDataUrl": "reference-b"},
            {"name": "Character B", "characterReferenceDataUrl": "reference-b"},
        ),
        (
            {"name": "Character A"},
            {
                "name": "Character A",
                "dataUrl": "avatar-a",
                "characterReferenceDataUrl": "reference-a",
            },
        ),
    ],
)
async def test_main_active_character_name_change_clears_only_stale_avatar_fields(
    monkeypatch,
    payload,
    expected,
):
    from app.main_server import web_app

    snapshot = {
        "name": "Character A",
        "dataUrl": "avatar-a",
        "characterReferenceDataUrl": "reference-a",
    }
    monkeypatch.setattr(web_app, "_card_drop_active_character", snapshot)

    response = await web_app.set_card_drop_active_character(
        _main_server_request(),
        payload,
    )

    assert response == {"ok": True}
    assert snapshot == expected


@pytest.mark.asyncio
async def test_main_active_character_model_change_clears_stale_images_for_same_name(
    monkeypatch,
):
    from app.main_server import web_app

    snapshot = {
        "name": "N.E.K.O",
        "modelType": "live2d",
        "modelKey": "live2d:/models/old/model.json",
        "dataUrl": "old-avatar",
        "characterReferenceDataUrl": "old-reference",
    }
    monkeypatch.setattr(web_app, "_card_drop_active_character", snapshot)

    response = await web_app.set_card_drop_active_character(
        _main_server_request(),
        {
            "name": "N.E.K.O",
            "modelType": "pngtuber",
            "modelKey": "pngtuber:/user_pngtuber/new/idle.png",
            "dataUrl": "new-avatar",
        },
    )

    assert response == {"ok": True}
    assert snapshot == {
        "name": "N.E.K.O",
        "modelType": "pngtuber",
        "modelKey": "pngtuber:/user_pngtuber/new/idle.png",
        "dataUrl": "new-avatar",
    }


@pytest.mark.asyncio
async def test_main_active_character_type_only_change_clears_prior_model_key(
    monkeypatch,
):
    from app.main_server import web_app

    snapshot = {
        "name": "N.E.K.O",
        "modelType": "live2d",
        "modelKey": "live2d:/models/old/model.json",
        "dataUrl": "old-avatar",
        "characterReferenceDataUrl": "old-reference",
    }
    monkeypatch.setattr(web_app, "_card_drop_active_character", snapshot)

    response = await web_app.set_card_drop_active_character(
        _main_server_request(),
        {"name": "N.E.K.O", "modelType": "vrm"},
    )

    assert response == {"ok": True}
    assert snapshot == {"name": "N.E.K.O", "modelType": "vrm"}


@pytest.mark.asyncio
async def test_main_active_character_rejects_stale_model_revision_before_mutation(
    monkeypatch,
):
    from app.main_server import web_app

    snapshot = {
        "name": "N.E.K.O",
        "modelType": "pngtuber",
        "modelKey": "pngtuber:new-model",
        "modelRevision": 200,
        "dataUrl": "new-avatar",
        "characterReferenceDataUrl": "new-reference",
    }
    monkeypatch.setattr(web_app, "_card_drop_active_character", snapshot)

    response = await web_app.set_card_drop_active_character(
        _main_server_request(),
        {
            "name": "N.E.K.O",
            "modelType": "live2d",
            "modelKey": "live2d:old-model",
            "modelRevision": 100,
            "dataUrl": "old-avatar",
            "characterReferenceDataUrl": "old-reference",
        },
    )

    assert response == {"ok": False, "stale": True}
    assert snapshot == {
        "name": "N.E.K.O",
        "modelType": "pngtuber",
        "modelKey": "pngtuber:new-model",
        "modelRevision": 200,
        "dataUrl": "new-avatar",
        "characterReferenceDataUrl": "new-reference",
    }


@pytest.mark.asyncio
async def test_main_active_character_get_exposes_model_identity(monkeypatch):
    from app.main_server import web_app

    monkeypatch.setattr(
        web_app,
        "_card_drop_active_character",
        {
            "name": "N.E.K.O",
            "modelType": "pngtuber",
            "modelKey": "pngtuber:/user_pngtuber/neko/idle.png",
        },
    )

    response = await web_app.get_card_drop_active_character(
        _main_server_request(),
        include_avatar=False,
    )
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["modelType"] == "pngtuber"
    assert payload["modelKey"] == "pngtuber:/user_pngtuber/neko/idle.png"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "https://community.example"],
)
async def test_main_active_character_post_rejects_cross_origin_before_mutation(
    monkeypatch,
    origin,
):
    from app.main_server import web_app

    monkeypatch.setenv("NEKO_SOCIAL_BASE_URL", "https://community.example")
    snapshot = {"name": "Before"}
    monkeypatch.setattr(web_app, "_card_drop_active_character", snapshot)

    response = await web_app.set_card_drop_active_character(
        _main_server_request(origin=origin),
        {"name": "After", "dataUrl": "private-avatar-data"},
    )

    assert response.status_code == 403
    assert json.loads(response.body) == {"detail": "origin_not_allowed"}
    assert snapshot == {"name": "Before"}


def test_main_active_character_read_cors_remains_social_origin_only(monkeypatch):
    from app.main_server import web_app

    monkeypatch.setenv("NEKO_SOCIAL_BASE_URL", "https://community.example")

    headers = web_app._active_character_cors_headers(
        _main_server_request(method="GET", origin="https://community.example")
    )

    assert headers is not None
    assert headers["Access-Control-Allow-Origin"] == "https://community.example"
    assert headers["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"


def test_card_drop_client_id_persists_fresh_default_before_returning(
    tmp_path, monkeypatch,
):
    import utils.config_manager as config_manager

    saved: list[dict] = []

    class FakeConfigManager:
        cloudsave_local_state_path = tmp_path / "state" / "cloudsave_local_state.json"

        def ensure_cloudsave_client_credentials(self) -> tuple[str, str]:
            state = {"client_id": "fresh-client-id", "client_proof": "p" * 43}
            saved.append(state)
            return state["client_id"], state["client_proof"]

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: FakeConfigManager())

    assert C._get_client_id() == "fresh-client-id"
    assert saved == [{"client_id": "fresh-client-id", "client_proof": "p" * 43}]


def test_card_drop_client_id_fails_closed_when_fresh_default_cannot_be_saved(
    tmp_path, monkeypatch,
):
    import utils.config_manager as config_manager

    class FakeConfigManager:
        cloudsave_local_state_path = tmp_path / "state" / "cloudsave_local_state.json"

        def ensure_cloudsave_client_credentials(self) -> tuple[str, str]:
            raise OSError("disk unavailable")

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: FakeConfigManager())

    assert C._get_client_id() is None


def test_packaged_facts_module_exposes_shared_entrypoints():
    shared = importlib.import_module("main_logic.card_forge_facts")

    assert callable(shared.build_forge_facts_payload)
    assert callable(shared.resolve_active_neko_context)


def test_packaging_manifests_collect_shared_card_forge_module():
    project = Path(__file__).resolve().parents[2]

    pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")
    desktop = (project / ".github" / "workflows" / "build-desktop.yml").read_text(
        encoding="utf-8"
    )
    linux = (project / ".github" / "workflows" / "build-desktop-linux.yml").read_text(
        encoding="utf-8"
    )

    assert '"main_logic"' in pyproject
    include = "--include-package=main_logic"
    assert include in desktop
    assert include in linux


class _FakeConfigManager:
    def __init__(self, memory_dir: Path, active: str, prompts: dict[str, str]):
        self.memory_dir = memory_dir
        self._active = active
        self._prompts = prompts

    def get_character_data(self):
        return ("Master", self._active, None, None, None, self._prompts)


def test_facts_context_uses_only_validated_active_character(tmp_path):
    manager = _FakeConfigManager(
        tmp_path,
        "Active",
        {"Active": "active prompt", "Other": "other prompt"},
    )

    context = F._build_context(manager)

    assert context.lanlan_name == "Active"
    assert context.facts_path == tmp_path / "Active" / "facts.json"
    assert context.lanlan_prompt == "active prompt"
    assert context.source == "neko-config"


def test_facts_context_honors_validated_character_override(tmp_path):
    manager = _FakeConfigManager(
        tmp_path,
        "Active",
        {"Active": "active prompt", "Other": "other prompt"},
    )

    context = F._build_context(manager, character_override="Other")

    assert context.lanlan_name == "Other"
    assert context.facts_path == tmp_path / "Other" / "facts.json"
    assert context.lanlan_prompt == "other prompt"
    assert context.source == "character-override"


def test_facts_context_fails_closed_without_valid_active_character(tmp_path, monkeypatch):
    monkeypatch.setenv("NEKO_FACTS_JSON", str(tmp_path / "debug-facts.json"))
    manager = _FakeConfigManager(tmp_path, "../Other", {"Other": "other prompt"})

    context = F._build_context(manager)

    assert context.lanlan_name == ""
    assert context.facts_path is None
    assert context.lanlan_prompt == ""
    assert context.source == "unresolved"


def test_facts_selector_normalizes_malformed_importance():
    facts, stats = F._select_forge_facts_with_stats(
        [{"id": "dirty", "text": "safe", "importance": "unknown"}],
        min_importance=0,
        limit=5,
    )

    assert stats["filteredCount"] == 1
    assert facts[0]["importance"] == 0


@pytest.mark.asyncio
async def test_facts_url_failure_log_does_not_expose_credentials(monkeypatch, caplog):
    class FakeResponse:
        status_code = 503

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(F.httpx, "AsyncClient", FakeAsyncClient)
    caplog.set_level("WARNING", logger="neko.card_forge_facts")

    assert await F._fetch_facts_from_url(
        "https://user:password@example.com/facts?api_key=top-secret"
    ) is None
    assert "configured URL returned 503" in caplog.text
    assert "password" not in caplog.text
    assert "top-secret" not in caplog.text


@pytest.mark.asyncio
async def test_shared_facts_selector_filters_private_and_redacted_memory(
    tmp_path, monkeypatch
):
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(
        json.dumps(
            [
                {"id": "public", "text": "safe", "importance": 7},
                {"id": "private", "text": "secret", "importance": 9, "private": True},
                {"id": "redacted", "text": "masked", "importance": 9, "redacted": True},
            ]
        ),
        encoding="utf-8",
    )

    async def fake_context(*_args, **_kwargs):
        return ActiveNekoContext(
            master_name="Master",
            lanlan_name="Lanlan",
            memory_dir=tmp_path,
            facts_path=facts_path,
            source="test",
        )

    monkeypatch.setattr("main_logic.card_forge_facts.resolve_active_neko_context", fake_context)
    payload = await build_forge_facts_payload(
        runtime_character_hint=" Lanlan ",
        min_importance=0,
        limit=5,
    )

    assert [fact["id"] for fact in payload["facts"]] == ["public"]
    assert payload["excludedCount"] == 2
    assert payload["runtimeCharacterHintUsed"] is True


@pytest.mark.asyncio
async def test_shared_facts_file_parsing_and_selection_run_off_event_loop(
    tmp_path,
    monkeypatch,
):
    facts_path = tmp_path / "facts.json"
    archive_path = tmp_path / "facts_archive.json"
    facts_path.write_text(
        json.dumps([{"id": "active", "text": "safe", "importance": 8}]),
        encoding="utf-8",
    )
    archive_path.write_text(
        json.dumps(
            [
                {
                    "id": "archive",
                    "text": "old",
                    "importance": 8,
                    "created_at": "2020-01-01T00:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )

    async def fake_context(*_args, **_kwargs):
        return ActiveNekoContext(
            master_name="Master",
            lanlan_name="Lanlan",
            memory_dir=tmp_path,
            facts_path=facts_path,
            source="test",
        )

    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []
    original_load = F._load_facts_json
    original_select = F._select_forge_facts_with_stats
    original_archive_select = F._select_archive_facts

    def tracked_load(path):
        worker_threads.append(threading.get_ident())
        return original_load(path)

    def tracked_select(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        return original_select(*args, **kwargs)

    def tracked_archive_select(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        return original_archive_select(*args, **kwargs)

    monkeypatch.setattr(F, "resolve_active_neko_context", fake_context)
    monkeypatch.setattr(F, "_load_facts_json", tracked_load)
    monkeypatch.setattr(F, "_select_forge_facts_with_stats", tracked_select)
    monkeypatch.setattr(F, "_select_archive_facts", tracked_archive_select)

    payload = await F.build_forge_facts_payload(
        runtime_character_hint="Lanlan",
        min_importance=0,
        limit=5,
    )

    assert payload["returnedCount"] == 2
    # The archive selector delegates to the shared selector, so the exact
    # count includes that nested call as well as both file reads.
    assert len(worker_threads) >= 4
    assert all(thread_id != event_loop_thread for thread_id in worker_threads)


@pytest.mark.asyncio
async def test_shared_facts_selector_rejects_mismatched_runtime_character(
    tmp_path, monkeypatch
):
    async def fake_context(*_args, **_kwargs):
        return ActiveNekoContext(
            master_name="Master",
            lanlan_name="Lanlan",
            memory_dir=tmp_path,
            facts_path=tmp_path / "Lanlan" / "facts.json",
            source="test",
        )

    def unexpected_facts_read(_path):
        raise AssertionError("mismatched runtime character must fail before reading facts")

    async def unexpected_remote_fetch(_url):
        raise AssertionError("mismatched runtime character must fail before fetching facts")

    monkeypatch.setattr("main_logic.card_forge_facts.resolve_active_neko_context", fake_context)
    monkeypatch.setattr("main_logic.card_forge_facts._load_facts_json", unexpected_facts_read)
    monkeypatch.setattr("main_logic.card_forge_facts._fetch_facts_from_url", unexpected_remote_fetch)

    payload = await build_forge_facts_payload(
        runtime_character_hint="Other",
        min_importance=0,
        limit=5,
    )

    assert payload["character"] == ""
    assert payload["facts"] == []
    assert payload["runtimeCharacterHintUsed"] is False
    assert payload["fallbackReason"] == "runtime_character_hint_missing"
    assert payload["error"] == "active_neko_runtime_not_linked"


@pytest.fixture
def client(monkeypatch, tmp_path):
    from main_routers import community_oauth

    monkeypatch.setenv("NEKO_SOCIAL_BASE_URL", "https://community.example")
    # Keep the suite off the developer's real credential file.
    monkeypatch.setattr(C, "_auth_path", lambda: tmp_path / "community_auth.json")

    async def current_desktop_status():
        snapshot = await asyncio.to_thread(C._desktop_session_snapshot)
        return {
            "logged_in": bool(snapshot),
            "snapshot": snapshot,
            "auth": {},
        }

    monkeypatch.setattr(
        community_oauth,
        "resolve_saved_oauth_status",
        current_desktop_status,
    )
    C._native_sync_tickets.clear()
    C._native_delegates.clear()
    app = FastAPI()
    app.include_router(C.router)
    with TestClient(app, base_url="http://localhost:48911") as test_client:
        yield test_client
    C._native_sync_tickets.clear()
    C._native_delegates.clear()


def _issue_sync_ticket(client: TestClient) -> str:
    response = client.get(
        "/api/card-drop/sync-ticket",
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    ticket = response.json()["sync_ticket"]
    assert len(ticket) >= 32
    return ticket


def _delegate_session(
    *,
    local_user_id: str = USER_A_ID,
    access_token: str = "desktop-token-a",
) -> dict[str, str]:
    return {
        "base_url": "https://community.example",
        "access_token": access_token,
        "local_user_id": local_user_id,
        "auth_source": "oauth",
    }


def _issue_delegate_from_local_ui(
    client: TestClient,
    monkeypatch,
    snapshot: dict[str, str],
) -> str:
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshot)
    response = client.get(
        "/api/card-drop/native-delegate",
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["scopes"] == ["facts:read"]
    assert "local_user_id" not in payload
    return payload["native_delegate"]


def _delegate_request_headers(token: str, *, local_user_id: str = USER_A_ID) -> dict:
    return {
        "Origin": "https://community.example",
        "Authorization": f"Bearer {token}",
        "X-Neko-Local-User-Id": local_user_id,
    }


def test_native_delegate_requires_same_origin_fetch_metadata_and_hides_principal(
    client,
    monkeypatch,
):
    snapshot = _delegate_session()
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshot)

    headerless = client.get("/api/card-drop/native-delegate")
    cross_site = client.get(
        "/api/card-drop/native-delegate",
        headers={
            "Origin": "https://community.example",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    same_origin = client.get(
        "/api/card-drop/native-delegate",
        headers={
            "Origin": "http://localhost:48911",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    assert headerless.status_code == 403
    assert cross_site.status_code == 403
    assert same_origin.status_code == 200
    assert "local_user_id" not in same_origin.json()
    assert same_origin.headers["cache-control"] == "no-store"


def test_native_delegate_handoff_is_local_ui_only_and_validates_return_url(
    client,
    monkeypatch,
):
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: _delegate_session())

    assert C._handoff_return_url(
        "https://community.example/cards?tab=forge#discarded",
        "https://community.example",
    ) == "https://community.example/cards?tab=forge"
    assert (
        C._handoff_return_url(
            "https://evil.example/cards",
            "https://community.example",
        )
        is None
    )

    headerless = client.get(
        "/api/card-drop/native-delegate/handoff",
        follow_redirects=False,
    )
    allowed = client.get(
        "/api/card-drop/native-delegate/handoff",
        params={"return_to": "https://community.example/cards?tab=forge"},
        headers={"Sec-Fetch-Site": "same-origin"},
        follow_redirects=False,
    )

    assert headerless.status_code == 403
    assert allowed.status_code == 302
    assert allowed.headers["location"].startswith(
        "https://community.example/cards?tab=forge#native_delegate="
    )


@pytest.mark.parametrize("endpoint", ["native-delegate", "sync-ticket"])
def test_native_proof_backfills_a_verified_legacy_desktop_session(
    client,
    tmp_path,
    monkeypatch,
    endpoint,
):
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    auth.write_text(
        json.dumps(
            {
                "access_token": "legacy-desktop-token",
                "refresh_token": "legacy-desktop-refresh",
            }
        ),
        encoding="utf-8",
    )
    social.write_text(
        json.dumps(
            {
                "baseUrl": "https://community.example",
                "token": "legacy-desktop-token",
                "refresh_token": "legacy-desktop-refresh",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)
    seen = _identity_http_client(
        monkeypatch,
        {"legacy-desktop-token": (USER_A_ID, "oauth")},
    )

    response = client.get(
        f"/api/card-drop/{endpoint}",
        headers={"Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 200
    assert seen == [("get", "legacy-desktop-token")]
    saved = json.loads(social.read_text(encoding="utf-8"))
    assert saved["local_user_id"] == USER_A_ID
    assert saved["auth_source"] == "oauth"
    if endpoint == "native-delegate":
        assert C._native_delegate_entry(response.json()["native_delegate"]) is not None
    else:
        redeemed = client.post(
            "/api/card-drop/social-session-init",
            headers={"Origin": "https://community.example"},
            json={"sync_ticket": response.json()["sync_ticket"]},
        )
        assert redeemed.status_code == 200
        assert redeemed.json()["access_token"] == "legacy-desktop-token"


def test_native_delegate_is_bound_to_the_refreshed_oauth_session(
    client,
    monkeypatch,
):
    from main_routers import community_oauth

    current = {
        "snapshot": _delegate_session(access_token="expired-desktop-token"),
    }
    refreshed = _delegate_session(access_token="refreshed-desktop-token")
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: current["snapshot"])

    async def refresh_before_issuance():
        current["snapshot"] = refreshed
        return {"logged_in": True, "snapshot": refreshed, "auth": {}}

    async def fake_build(**kwargs):
        return {"character": kwargs["runtime_character_hint"], "facts": []}

    monkeypatch.setattr(
        community_oauth,
        "resolve_saved_oauth_status",
        refresh_before_issuance,
    )
    monkeypatch.setattr(C, "_build_local_forge_facts", fake_build)

    issued = client.get(
        "/api/card-drop/native-delegate",
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert issued.status_code == 200
    token = issued.json()["native_delegate"]
    assert C._native_delegate_entry(token)["session_fingerprint"] == (
        C._desktop_session_fingerprint(refreshed)
    )

    facts = client.post(
        "/api/card-drop/facts/query",
        json={"runtime_character_hint": "Lanlan"},
        headers=_delegate_request_headers(token),
    )
    assert facts.status_code == 200


def test_native_delegate_is_invalidated_by_logout_or_account_switch(
    client,
    monkeypatch,
):
    current = {"snapshot": _delegate_session()}
    monkeypatch.setattr(
        C,
        "_desktop_session_snapshot",
        lambda: current["snapshot"],
    )
    token = _issue_delegate_from_local_ui(
        client,
        monkeypatch,
        current["snapshot"],
    )
    # Restore the mutable lookup after the issuance helper installs a fixed snapshot.
    monkeypatch.setattr(
        C,
        "_desktop_session_snapshot",
        lambda: current["snapshot"],
    )
    monkeypatch.setattr(
        C,
        "_build_local_forge_facts",
        lambda **_kwargs: pytest.fail("invalid delegate must not read facts"),
    )

    current["snapshot"] = _delegate_session(
        local_user_id=USER_B_ID,
        access_token="desktop-token-b",
    )
    switched = client.post(
        "/api/card-drop/facts/query",
        json={"runtime_character_hint": "Lanlan"},
        headers=_delegate_request_headers(token),
    )
    assert switched.status_code == 401
    assert C._native_delegate_entry(token) is None

    current["snapshot"] = _delegate_session()
    logout_token = _issue_delegate_from_local_ui(
        client,
        monkeypatch,
        current["snapshot"],
    )
    monkeypatch.setattr(
        C,
        "_desktop_session_snapshot",
        lambda: current["snapshot"],
    )
    current["snapshot"] = None
    logged_out = client.post(
        "/api/card-drop/facts/query",
        json={"runtime_character_hint": "Lanlan"},
        headers=_delegate_request_headers(logout_token),
    )
    assert logged_out.status_code == 401
    assert C._native_delegate_entry(logout_token) is None


def test_expired_native_delegate_does_not_fall_back_to_cloud_auth(
    client,
    monkeypatch,
):
    snapshot = _delegate_session()
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshot)
    token = _issue_delegate_from_local_ui(client, monkeypatch, snapshot)
    entry = C._native_delegate_entry(token)
    assert entry is not None
    # _native_delegate_entry 返回副本（防止调用方改写共享注册表），直接过期注册表条目。
    C._native_delegates[C._sync_ticket_digest(token)]["expires_at"] = 0

    async def unexpected_cloud_auth(_base, _token):
        pytest.fail("expired native delegates must not be retried as cloud tokens")

    monkeypatch.setattr(C, "_request_matches_desktop_session", unexpected_cloud_auth)
    monkeypatch.setattr(
        C,
        "_build_local_forge_facts",
        lambda **_kwargs: pytest.fail("expired delegate must not read facts"),
    )

    response = client.post(
        "/api/card-drop/facts/query",
        json={"runtime_character_hint": "Lanlan"},
        headers=_delegate_request_headers(token),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "local_session_mismatch"}
    assert C._native_delegate_entry(token) is None


def test_explicit_empty_delegate_scopes_do_not_escalate(client, monkeypatch):
    snapshot = _delegate_session()
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshot)
    token = C._issue_native_delegate(
        local_user_id=USER_A_ID,
        audience="https://community.example",
        session_fingerprint=C._desktop_session_fingerprint(snapshot),
        scopes=frozenset(),
    )

    response = client.post(
        "/api/card-drop/facts/query",
        json={"runtime_character_hint": "Lanlan"},
        headers=_delegate_request_headers(token),
    )

    assert response.status_code == 401
    assert C._native_delegate_entry(token)["scopes"] == frozenset()


def test_local_credit_routes_are_retired_for_delegates(
    client,
    monkeypatch,
):
    snapshot = _delegate_session()
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshot)
    token = _issue_delegate_from_local_ui(client, monkeypatch, snapshot)

    read = client.get(
        "/api/card-drop/credits",
        headers=_delegate_request_headers(token),
    )
    mutate = client.post(
        "/api/card-drop/credits/credit-a/reservations",
        json={"operation_id": "operation-a"},
        headers=_delegate_request_headers(token),
    )

    assert read.status_code == mutate.status_code == 410
    assert read.json() == mutate.json() == {"detail": "cloud_forge_credits_required"}


def test_retired_credit_routes_ignore_delegate_principal_and_local_ledger(
    client,
    tmp_path,
    monkeypatch,
):
    from main_logic import forge_credit_ledger

    monkeypatch.setenv("NEKO_USER_DATA_DIR", str(tmp_path))
    forge_credit_ledger.grant_credit(
        {
            "trigger_type": "emotion_combo",
            "idem_key": "delegate-owner-isolation",
        },
        rarity="SR",
    )
    credit_id = forge_credit_ledger.list_credits()["credits"][0]["id"]
    ledger_before = forge_credit_ledger.list_credits()
    operation_id = "33333333-3333-4333-8333-333333333333"
    card_id = "44444444-4444-4444-8444-444444444444"

    session_a = _delegate_session()
    token_a = _issue_delegate_from_local_ui(client, monkeypatch, session_a)
    headers_a = _delegate_request_headers(token_a)
    reserved = client.post(
        f"/api/card-drop/credits/{credit_id}/reservations",
        json={"operation_id": operation_id},
        headers=headers_a,
    )
    replay = client.post(
        f"/api/card-drop/credits/{credit_id}/reservations",
        json={"operation_id": operation_id},
        headers=headers_a,
    )
    visible_to_a = client.get(
        "/api/card-drop/credits",
        headers=headers_a,
    )

    session_b = _delegate_session(
        local_user_id=USER_B_ID,
        access_token="desktop-token-b",
    )
    token_b = _issue_delegate_from_local_ui(client, monkeypatch, session_b)
    headers_b = _delegate_request_headers(token_b, local_user_id=USER_B_ID)
    visible_to_b = client.get(
        "/api/card-drop/credits",
        headers=headers_b,
    )
    commit_by_b = client.post(
        (
            f"/api/card-drop/credits/{credit_id}/reservations/"
            f"{operation_id}/commit"
        ),
        json={"card_id": card_id},
        headers=headers_b,
    )
    release_by_b = client.delete(
        f"/api/card-drop/credits/{credit_id}/reservations/{operation_id}",
        headers=headers_b,
    )

    responses = [reserved, replay, visible_to_a, visible_to_b, commit_by_b, release_by_b]
    assert all(response.status_code == 410 for response in responses)
    assert all(
        response.json() == {"detail": "cloud_forge_credits_required"}
        for response in responses
    )
    assert forge_credit_ledger.list_credits() == ledger_before


def test_retired_credit_routes_ignore_refreshed_desktop_bearer(
    client,
    tmp_path,
    monkeypatch,
):
    from main_logic import forge_credit_ledger

    monkeypatch.setenv("NEKO_USER_DATA_DIR", str(tmp_path))
    _write_v2_desktop_session(
        tmp_path,
        monkeypatch,
        token="desktop-token-after-refresh",
        local_user_id=USER_A_ID,
    )
    _identity_http_client(
        monkeypatch,
        {
            "browser-token-before-refresh": (USER_A_ID, "oauth"),
            "browser-token-after-refresh": (USER_A_ID, "oauth"),
        },
    )
    forge_credit_ledger.grant_credit(
        {
            "trigger_type": "emotion_combo",
            "idem_key": "desktop-refresh-owner",
        },
        rarity="R",
    )
    credit_id = forge_credit_ledger.list_credits()["credits"][0]["id"]
    ledger_before = forge_credit_ledger.list_credits()
    operation_id = "55555555-5555-4555-8555-555555555555"
    card_id = "66666666-6666-4666-8666-666666666666"

    def bearer_headers(token: str) -> dict[str, str]:
        return {
            "Origin": "https://community.example",
            "Authorization": f"Bearer {token}",
        }

    reserved = client.post(
        f"/api/card-drop/credits/{credit_id}/reservations",
        json={"operation_id": operation_id},
        headers=bearer_headers("browser-token-before-refresh"),
    )
    replay_after_refresh = client.post(
        f"/api/card-drop/credits/{credit_id}/reservations",
        json={"operation_id": operation_id},
        headers=bearer_headers("browser-token-after-refresh"),
    )
    visible_after_refresh = client.get(
        "/api/card-drop/credits",
        headers=bearer_headers("browser-token-after-refresh"),
    )
    committed = client.post(
        (
            f"/api/card-drop/credits/{credit_id}/reservations/"
            f"{operation_id}/commit"
        ),
        json={"card_id": card_id},
        headers=bearer_headers("browser-token-after-refresh"),
    )
    commit_replay = client.post(
        (
            f"/api/card-drop/credits/{credit_id}/reservations/"
            f"{operation_id}/commit"
        ),
        json={"card_id": card_id},
        headers=bearer_headers("browser-token-after-refresh"),
    )

    responses = [
        reserved,
        replay_after_refresh,
        visible_after_refresh,
        committed,
        commit_replay,
    ]
    assert all(response.status_code == 410 for response in responses)
    assert all(
        response.json() == {"detail": "cloud_forge_credits_required"}
        for response in responses
    )
    assert forge_credit_ledger.list_credits() == ledger_before


def test_card_drop_capabilities_are_exact_origin_and_no_store(client):
    allowed = client.get(
        "/api/card-drop/capabilities",
        headers={"Origin": "https://community.example"},
    )
    denied = client.get(
        "/api/card-drop/capabilities",
        headers={"Origin": "https://evil.example"},
    )

    assert allowed.status_code == 200
    assert allowed.headers["cache-control"] == "no-store"
    assert allowed.headers["access-control-allow-origin"] == (
        "https://community.example"
    )
    assert allowed.json() == {
        "protocol": "neko-card-drop",
        "version": 1,
        "active_character": {"path": "/api/card-drop/active-character"},
        "facts": {
            "query_path": "/api/card-drop/facts/query",
            "method": "POST",
            "max_exclude_hashes": 200,
        },
        "credits": {"authority": "cloud"},
        "delegate": {
            "scopes": ["facts:read"],
            "principal_header": "x-neko-local-user-id",
        },
    }
    assert denied.status_code == 403


def test_facts_post_query_is_bounded_and_passes_validated_lists(
    client,
    monkeypatch,
):
    snapshot = _delegate_session()
    token = _issue_delegate_from_local_ui(client, monkeypatch, snapshot)
    seen: list[dict] = []

    async def fake_build(**kwargs):
        seen.append(kwargs)
        return {"character": kwargs["runtime_character_hint"], "facts": []}

    monkeypatch.setattr(C, "_build_local_forge_facts", fake_build)
    headers = _delegate_request_headers(token)

    valid = client.post(
        "/api/card-drop/facts/query",
        json={
            "runtime_character_hint": "Lanlan",
            "exclude_hashes": ["hash-a", "hash-b"],
            "exclude_fact_ids": ["fact-a"],
        },
        headers=headers,
    )
    too_many = client.post(
        "/api/card-drop/facts/query",
        json={"exclude_hashes": [f"hash-{index}" for index in range(201)]},
        headers=headers,
    )
    too_long = client.post(
        "/api/card-drop/facts/query",
        json={"exclude_hashes": ["h" * 129]},
        headers=headers,
    )

    assert valid.status_code == 200
    assert seen == [
        {
            "runtime_character_hint": "Lanlan",
            "min_importance": 0,
            "include_absorbed": True,
            "limit": 5,
            "exclude_fact_ids": "fact-a",
            "exclude_hashes": "hash-a,hash-b",
        }
    ]
    assert too_many.status_code == 422
    assert too_many.json() == {"detail": "exclude_hashes_too_many_items"}
    assert too_long.status_code == 422
    assert too_long.json() == {"detail": "exclude_hashes_item_invalid"}
    assert too_many.headers["access-control-allow-origin"] == (
        "https://community.example"
    )


def test_local_credit_summary_is_retired(client):
    response = client.get(
        "/api/card-drop/credits/local-summary",
        headers={"Origin": "https://community.example"},
    )
    assert response.status_code == 410
    assert response.json() == {"detail": "cloud_forge_credits_required"}
    assert response.headers["access-control-allow-origin"] == "https://community.example"


def test_retired_credit_grant_and_summary_support_cors_preflight(client):
    grant_headers = {
        "Origin": "https://community.example",
        "Access-Control-Request-Method": "POST",
    }
    summary_headers = {
        "Origin": "https://community.example",
        "Access-Control-Request-Method": "GET",
    }
    grant = client.options("/api/card-drop/credits/grant", headers=grant_headers)
    summary = client.options(
        "/api/card-drop/credits/local-summary", headers=summary_headers,
    )

    assert grant.status_code == summary.status_code == 200
    assert grant.headers["access-control-allow-origin"] == "https://community.example"
    assert summary.headers["access-control-allow-origin"] == "https://community.example"
    assert "POST" in grant.headers["access-control-allow-methods"]
    assert "GET" in summary.headers["access-control-allow-methods"]


def test_retired_credit_routes_keep_validated_cors_headers(client, monkeypatch):
    async def no_scoped_delegate(_request, _required_scope):
        return C._BrowserAuth(None)

    async def auth_state(_base, token):
        state = "unavailable" if token == "unavailable-token" else "mismatch"
        return C._BrowserAuth(state)

    monkeypatch.setattr(C, "_scoped_native_auth", no_scoped_delegate)
    monkeypatch.setattr(C, "_request_desktop_session_auth", auth_state)

    mismatch = client.get(
        "/api/card-drop/credits",
        headers={
            "Origin": "https://community.example",
            "Authorization": "Bearer another-user-token",
        },
    )
    assert mismatch.status_code == 410
    assert mismatch.json() == {"detail": "cloud_forge_credits_required"}
    assert mismatch.headers["access-control-allow-origin"] == "https://community.example"

    unavailable = client.get(
        "/api/card-drop/credits",
        headers={
            "Origin": "https://community.example",
            "Authorization": "Bearer unavailable-token",
        },
    )
    assert unavailable.status_code == 410
    assert unavailable.json() == {"detail": "cloud_forge_credits_required"}
    assert unavailable.headers["access-control-allow-origin"] == "https://community.example"

    denied = client.get(
        "/api/card-drop/credits",
        headers={
            "Origin": "https://attacker.example",
            "Authorization": "Bearer another-user-token",
        },
    )
    assert denied.status_code == 410
    assert denied.json() == {"detail": "cloud_forge_credits_required"}
    assert "access-control-allow-origin" not in denied.headers


def test_sync_ticket_is_short_lived_and_single_use(client):
    ticket = _issue_sync_ticket(client)

    assert C._sync_ticket_is_valid(ticket)
    assert C._consume_sync_ticket(ticket)
    assert not C._sync_ticket_is_valid(ticket)
    assert not C._consume_sync_ticket(ticket)


def test_sync_ticket_rejects_cross_site_browser_churn(client):
    before = dict(C._native_sync_tickets)

    evil_origin = client.get(
        "/api/card-drop/sync-ticket",
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    blind_browser_get = client.get(
        "/api/card-drop/sync-ticket",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    # A headerless caller models a native local process: it must not be able
    # to mint the ticket that transitively redeems the Desktop OAuth bearer.
    headerless_native = client.get("/api/card-drop/sync-ticket")
    same_origin = client.get(
        "/api/card-drop/sync-ticket",
        headers={
            "Origin": "http://localhost:48911",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    assert evil_origin.status_code == 403
    assert blind_browser_get.status_code == 403
    assert headerless_native.status_code == 403
    assert same_origin.status_code == 200
    assert len(C._native_sync_tickets) == len(before) + 1


def test_auth_status_rejects_cross_site_before_cloud_lookup(client, monkeypatch):
    from main_routers import community_oauth

    calls = 0

    async def resolve_saved_oauth_status():
        nonlocal calls
        calls += 1
        return {"logged_in": False, "snapshot": None, "auth": {}}

    monkeypatch.setattr(
        community_oauth,
        "resolve_saved_oauth_status",
        resolve_saved_oauth_status,
    )

    evil_origin = client.get(
        "/api/card-drop/auth-status",
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    blind_browser_get = client.get(
        "/api/card-drop/auth-status",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    same_origin = client.get(
        "/api/card-drop/auth-status",
        headers={
            "Origin": "http://localhost:48911",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    assert evil_origin.status_code == 403
    assert blind_browser_get.status_code == 403
    assert same_origin.status_code == 200
    assert calls == 1


def test_bind_client_approval_uses_persisted_local_id_and_consumes_ticket(
    client, monkeypatch,
):
    actual_client_id = "00112233445566778899aabbccddeeff"
    actual_client_proof = "p" * 43
    challenge = "C" * 43
    sent: list[tuple[str, dict]] = []
    thread_ids: dict[str, int] = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            thread_ids["event_loop"] = threading.get_ident()
            sent.append((url, kwargs["json"]))
            return _CloudResponse(204, {})

    def get_client_credentials():
        thread_ids["credentials"] = threading.get_ident()
        return actual_client_id, actual_client_proof

    monkeypatch.setattr(
        C,
        "_get_client_credentials",
        get_client_credentials,
    )
    monkeypatch.setattr(C.httpx, "AsyncClient", FakeAsyncClient)
    ticket = _issue_sync_ticket(client)

    response = client.post(
        "/api/card-drop/bind-client/approve",
        headers={"Origin": "https://community.example"},
        json={
            "binding_challenge": challenge,
            "sync_ticket": ticket,
            "client_id": "attacker-controlled-id-is-ignored",
        },
    )
    replay = client.post(
        "/api/card-drop/bind-client/approve",
        headers={"Origin": "https://community.example"},
        json={"binding_challenge": challenge, "sync_ticket": ticket},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers["access-control-allow-origin"] == "https://community.example"
    assert sent == [
        (
            "https://community.example/api/clients/bind-approval",
            {
                "client_id": actual_client_id,
                "binding_challenge": challenge,
                "client_proof": actual_client_proof,
            },
        )
    ]
    assert thread_ids["credentials"] != thread_ids["event_loop"]
    assert replay.status_code == 403
    assert replay.json() == {"detail": "invalid_sync_ticket"}


def test_social_session_init_hands_desktop_oauth_to_community_origin(client, monkeypatch):
    snapshot = {
        **_delegate_session(),
        "refresh_token": "desktop-refresh-a",
        "auth_public_url": "https://auth.example",
        "client_id": "neko-servers-desktop-dev",
    }
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshot)
    ticket = _issue_sync_ticket(client)

    denied = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://evil.example"},
        json={"sync_ticket": ticket},
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "origin_not_allowed"}
    assert C._sync_ticket_is_valid(ticket)

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://community.example"
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "access_token": "desktop-token-a",
        "local_user_id": USER_A_ID,
        "auth_public_url": "https://auth.example",
        "client_id": "neko-servers-desktop-dev",
        "bind": {"bound": True, "error": None},
    }
    # Desktop stays the sole owner of the refresh-token family.
    assert "desktop-refresh-a" not in response.text

    replay = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )
    assert replay.status_code == 403
    assert replay.json() == {"detail": "invalid_sync_ticket"}


def test_social_session_init_rejects_a_request_without_origin(client, monkeypatch):
    monkeypatch.setattr(
        C,
        "_desktop_session_snapshot",
        lambda: {**_delegate_session(), "refresh_token": "desktop-refresh-a"},
    )
    ticket = _issue_sync_ticket(client)

    response = client.post(
        "/api/card-drop/social-session-init",
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "origin_required"}
    assert "desktop-token-a" not in response.text
    assert C._sync_ticket_is_valid(ticket)


def test_social_session_init_rejects_a_plaintext_non_loopback_base(client, monkeypatch):
    monkeypatch.setenv("NEKO_SOCIAL_BASE_URL", "http://community.example")
    monkeypatch.setattr(
        C,
        "_desktop_session_snapshot",
        lambda: {
            **_delegate_session(),
            "base_url": "http://community.example",
            "refresh_token": "desktop-refresh-a",
        },
    )
    ticket = _issue_sync_ticket(client)

    preflight = client.options(
        "/api/card-drop/social-session-init",
        headers={"Origin": "http://community.example"},
    )
    assert preflight.status_code == 403
    assert preflight.json() == {"detail": "insecure_transport"}

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "http://community.example"},
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "insecure_transport"}
    assert "desktop-token-a" not in response.text
    assert C._sync_ticket_is_valid(ticket)


def test_social_session_init_allows_a_loopback_http_base(client, monkeypatch):
    monkeypatch.setenv("NEKO_SOCIAL_BASE_URL", "http://localhost:3000")
    monkeypatch.setattr(
        C,
        "_desktop_session_snapshot",
        lambda: {**_delegate_session(), "base_url": "http://127.0.0.1:3000"},
    )
    ticket = _issue_sync_ticket(client)

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "http://localhost:3000"},
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 200
    assert response.json()["access_token"] == "desktop-token-a"


def test_social_session_init_keeps_the_ticket_when_identity_is_unavailable(
    client,
    monkeypatch,
):
    outcome = {"value": (None, "unavailable")}

    async def resolved_snapshot():
        return outcome["value"]

    monkeypatch.setattr(C, "_native_delegate_session_snapshot", resolved_snapshot)
    # The consume fence re-reads the session file directly; keep it consistent
    # with the resolved identity so the retry can succeed.
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: _delegate_session())
    ticket = _issue_sync_ticket(client)

    unavailable = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "identity_verification_unavailable"}
    assert C._sync_ticket_is_valid(ticket)

    outcome["value"] = (_delegate_session(), "")
    retry = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )
    assert retry.status_code == 200
    assert retry.json()["access_token"] == "desktop-token-a"


def test_social_session_init_rejects_a_snapshot_from_another_environment(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        C,
        "_desktop_session_snapshot",
        lambda: {
            **_delegate_session(),
            "base_url": "https://production.example",
            "refresh_token": "prod-refresh",
            "auth_public_url": "https://auth.production.example",
            "client_id": "neko-servers-desktop-prod",
        },
    )
    ticket = _issue_sync_ticket(client)

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "desktop_login_required"}
    assert "prod-refresh" not in response.text
    assert "desktop-token-a" not in response.text


def test_social_session_init_repairs_a_failed_desktop_bind(
    client,
    monkeypatch,
    tmp_path,
):
    from main_routers import community_oauth

    monkeypatch.setattr(C, "_desktop_session_snapshot", _delegate_session)
    auth = tmp_path / "community_auth.json"
    auth.write_text(
        json.dumps({"bind": {"bound": False, "error": "cloud_unreachable"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    bound = []

    async def record_guest_bind(social_base, access_token):
        bound.append((social_base, access_token))
        return {"bound": True, "error": None}

    monkeypatch.setattr(community_oauth, "_oauth_guest_bind", record_guest_bind)
    ticket = _issue_sync_ticket(client)

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 200
    assert bound == [("https://community.example", "desktop-token-a")]
    assert response.json()["bind"] == {"bound": True, "error": None}


def test_social_session_init_rejects_a_session_replaced_during_the_bind_retry(
    client,
    monkeypatch,
    tmp_path,
):
    """A logout/account switch during the bind round trip must not ship the bearer."""
    from main_routers import community_oauth

    auth = tmp_path / "community_auth.json"
    auth.write_text(
        json.dumps({"bind": {"bound": False, "error": "cloud_unreachable"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)

    snapshots = [_delegate_session()]
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshots[-1])

    async def switch_account_mid_bind(_social_base, _access_token):
        # Desktop logs out while the cloud bind is in flight.
        snapshots.append({**_delegate_session(), "access_token": ""})
        return {"bound": True, "error": None}

    monkeypatch.setattr(community_oauth, "_oauth_guest_bind", switch_account_mid_bind)
    ticket = _issue_sync_ticket(client)

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "desktop_login_required"}
    assert "desktop-token-a" not in response.text
    # The handoff never happened, so the ticket must survive for a retry.
    assert C._sync_ticket_is_valid(ticket)


def test_social_session_init_refuses_to_consume_when_logout_wins_the_lock(
    client,
    monkeypatch,
):
    """A desktop logout landing between verification and consumption must not ship the bearer."""
    from contextlib import contextmanager

    state = {"token": "desktop-token-a"}

    def mutable_snapshot():
        return {**_delegate_session(), "access_token": state["token"]}

    monkeypatch.setattr(C, "_desktop_session_snapshot", mutable_snapshot)

    real_lock = C._social_session_lock

    @contextmanager
    def logout_under_lock(path):
        with real_lock(path):
            # Desktop logs out while the endpoint holds the consume fence.
            state["token"] = ""
            yield

    ticket = _issue_sync_ticket(client)
    monkeypatch.setattr(C, "_social_session_lock", logout_under_lock)

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "desktop_login_required"}
    assert "desktop-token-a" not in response.text
    assert C._sync_ticket_is_valid(ticket)


def test_sync_ticket_registry_survives_cross_thread_churn():
    """Issue on the loop thread while a worker consumes: no iteration races."""
    import threading as _threading

    errors: list[Exception] = []

    def churn_issue():
        try:
            for _ in range(200):
                C._issue_sync_ticket()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def churn_consume():
        try:
            for _ in range(200):
                C._consume_sync_ticket(C._issue_sync_ticket())
                C._sync_ticket_is_valid("not-a-real-ticket-value-ignored")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        _threading.Thread(target=churn_issue),
        _threading.Thread(target=churn_consume),
        _threading.Thread(target=churn_consume),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(C._native_sync_tickets) <= C._SYNC_TICKET_MAX_ACTIVE


def test_persist_repaired_bind_accepts_a_stale_auth_mirror(tmp_path, monkeypatch):
    """A refresh that updated social_session.json but failed its mirror write
    must not strand the repaired bind behind the mirror's older token."""
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    auth.write_text(
        json.dumps(
            {
                "access_token": "mirror-stale-token",
                "local_user_id": USER_A_ID,
                "auth_source": "oauth",
                "bind": {"bound": False, "error": "cloud_unreachable"},
            }
        ),
        encoding="utf-8",
    )
    social.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "baseUrl": "https://community.example",
                "token": "authoritative-token",
                "access_token": "authoritative-token",
                "local_user_id": USER_A_ID,
                "auth_source": "oauth",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)

    C._persist_repaired_bind(
        "authoritative-token", {"bound": True, "error": None}
    )
    persisted = json.loads(auth.read_text(encoding="utf-8"))
    assert persisted["bind"] == {"bound": True, "error": None}

    # An account switch (the authoritative file also moved on) still wins.
    auth.write_text(
        json.dumps(
            {
                "access_token": "mirror-stale-token",
                "local_user_id": USER_A_ID,
                "auth_source": "oauth",
                "bind": {"bound": False, "error": "cloud_unreachable"},
            }
        ),
        encoding="utf-8",
    )
    social.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "baseUrl": "https://community.example",
                "token": "switched-account-token",
                "access_token": "switched-account-token",
                "local_user_id": USER_A_ID,
                "auth_source": "oauth",
            }
        ),
        encoding="utf-8",
    )
    C._persist_repaired_bind(
        "authoritative-token", {"bound": True, "error": None}
    )
    preserved = json.loads(auth.read_text(encoding="utf-8"))
    assert preserved["bind"] == {"bound": False, "error": "cloud_unreachable"}


def test_clear_auth_waits_for_the_social_session_lock(tmp_path, monkeypatch):
    """Deleting the auth mirror must fence repaired-bind writers on the lock."""
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    auth.write_text(json.dumps({"access_token": "token-a"}), encoding="utf-8")
    social.write_text(
        json.dumps({"token": "token-a", "access_token": "token-a"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)

    # 一个 repaired-bind 写者在持有 social-session 锁：clear 必须在锁上等到
    # 超时并报告失败，而不是绕过锁把镜像删掉（约 2 秒锁超时）。
    with C._social_session_lock(social):
        assert C._clear_auth() is False
    assert auth.exists()
    assert social.exists()

    # 锁释放后重试即可正常清理。
    assert C._clear_auth() is True
    assert not auth.exists()
    assert not social.exists()


def test_clear_auth_fences_a_legacy_identity_write(tmp_path, monkeypatch):
    """A paused legacy writer must finish before logout deletes either path."""
    from concurrent.futures import ThreadPoolExecutor

    auth = tmp_path / "legacy" / "community_auth.json"
    legacy = auth.with_name("social_session.json")
    primary = tmp_path / "desktop" / "social_session.json"
    auth.parent.mkdir()
    auth.write_text(json.dumps({"access_token": "token-a"}), encoding="utf-8")
    legacy.write_text(json.dumps({"token": "token-a"}), encoding="utf-8")
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setenv("NEKO_USER_DATA_DIR", str(primary.parent))
    assert C._social_session_paths() == [primary, legacy]

    writing = threading.Event()
    release = threading.Event()
    clearing = threading.Event()
    real_write = C._write_private_json
    real_lock = C._social_session_lock

    def pause_write(path, data):
        if path == legacy:
            writing.set()
            assert release.wait(5)
        real_write(path, data)

    def observed_lock(path):
        if writing.is_set():
            clearing.set()
        return real_lock(path)

    monkeypatch.setattr(C, "_write_private_json", pause_write)
    monkeypatch.setattr(C, "_social_session_lock", observed_lock)
    snapshot = {"access_token": "token-a"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(C._persist_session_identity_metadata, snapshot, USER_A_ID, "oauth")
        try:
            assert writing.wait(5)
            logout = pool.submit(C._clear_auth)
            assert clearing.wait(5)
            assert not logout.done()
        finally:
            release.set()
        assert writer.result(timeout=5)
        assert logout.result(timeout=5)

    assert C._load_social_session() is None
    assert C._load_auth() is None
    assert not primary.exists()
    assert not legacy.exists()
    # A lookup that finishes after logout must not recreate the companion file.
    assert not C._persist_session_identity_metadata(snapshot, USER_A_ID, "oauth")
    assert C._desktop_session_snapshot() is None


def test_clear_auth_preserves_tickets_issued_after_unlock(client, tmp_path, monkeypatch):
    """Logout invalidates old tickets before a guest can mint a fresh proof."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager

    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    auth.write_text(json.dumps({"access_token": "token-a"}), encoding="utf-8")
    social.write_text(json.dumps({"token": "token-a"}), encoding="utf-8")
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)
    old_ticket = C._issue_sync_ticket_for_session()
    unlocked = threading.Event()
    issued = threading.Event()
    cleanup_thread = {}
    real_locks = C._social_session_locks

    @contextmanager
    def pause_after_logout_unlock(paths):
        with real_locks(paths):
            yield
        if threading.get_ident() == cleanup_thread.get("id"):
            unlocked.set()
            assert issued.wait(5)

    def logout():
        cleanup_thread["id"] = threading.get_ident()
        return C._clear_auth()

    monkeypatch.setattr(C, "_social_session_locks", pause_after_logout_unlock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        cleanup = pool.submit(logout)
        try:
            assert unlocked.wait(5)
            new_ticket = C._issue_sync_ticket_for_session()
        finally:
            issued.set()
        assert cleanup.result(timeout=5)

    assert C._desktop_session_snapshot() is None
    assert not C._sync_ticket_is_valid(old_ticket)
    assert C._sync_ticket_is_valid(new_ticket)
    assert C._consume_sync_ticket(new_ticket)


def test_social_session_init_rejects_a_token_revoked_during_the_bind_retry(
    client,
    monkeypatch,
    tmp_path,
):
    """A cloud-side revocation during the bind must not burn the ticket."""
    from main_routers import community_oauth

    monkeypatch.setattr(C, "_desktop_session_snapshot", _delegate_session)
    state = {"revoked": False}

    async def revoking_resolve():
        logged_in = not state["revoked"]
        return {
            "logged_in": logged_in,
            "snapshot": _delegate_session() if logged_in else None,
            "auth": {},
        }

    monkeypatch.setattr(
        community_oauth, "resolve_saved_oauth_status", revoking_resolve
    )
    auth = tmp_path / "community_auth.json"
    auth.write_text(
        json.dumps(
            {
                "access_token": "desktop-token-a",
                "bind": {"bound": False, "error": "cloud_unreachable"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)

    async def binding_guest_bind(_social_base, _access_token):
        state["revoked"] = True
        return {"bound": True, "error": None}

    monkeypatch.setattr(community_oauth, "_oauth_guest_bind", binding_guest_bind)
    ticket = _issue_sync_ticket(client)

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "desktop_login_required"}
    assert "desktop-token-a" not in response.text
    assert C._sync_ticket_is_valid(ticket)


def test_social_session_init_skips_the_bind_retry_when_the_session_was_replaced(
    client,
    monkeypatch,
    tmp_path,
):
    """A superseded account must not reach the cloud bind even in the repair path."""
    from main_routers import community_oauth

    reads = {"count": 0}

    def aging_snapshot():
        # The first resolution (outer snapshot) sees account A; every later
        # read — including the revalidation — sees the replaced session B.
        reads["count"] += 1
        token = "desktop-token-a" if reads["count"] <= 2 else "desktop-token-b"
        return {**_delegate_session(), "access_token": token}

    monkeypatch.setattr(C, "_desktop_session_snapshot", _delegate_session)
    auth = tmp_path / "community_auth.json"
    auth.write_text(
        json.dumps(
            {
                "access_token": "desktop-token-a",
                "bind": {"bound": False, "error": "cloud_unreachable"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)

    async def unexpected_guest_bind(_social_base, _access_token):
        raise AssertionError("a superseded session must not reach the cloud bind")

    monkeypatch.setattr(community_oauth, "_oauth_guest_bind", unexpected_guest_bind)
    ticket = _issue_sync_ticket(client)
    monkeypatch.setattr(C, "_desktop_session_snapshot", aging_snapshot)

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "desktop_login_required"}
    assert "desktop-token-a" not in response.text
    assert C._sync_ticket_is_valid(ticket)
    persisted = json.loads(auth.read_text(encoding="utf-8"))
    assert persisted["bind"] == {"bound": False, "error": "cloud_unreachable"}


def test_social_session_init_persists_a_terminal_bind_conflict(
    client,
    monkeypatch,
    tmp_path,
):
    """A retry that settles on an ownership conflict must not stay a transient failure."""
    from main_routers import community_oauth

    monkeypatch.setattr(C, "_desktop_session_snapshot", _delegate_session)
    auth = tmp_path / "community_auth.json"
    auth.write_text(
        json.dumps(
            {
                "access_token": "desktop-token-a",
                "bind": {"bound": False, "error": "cloud_unreachable"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)

    async def conflicting_guest_bind(_social_base, _access_token):
        return {"bound": False, "error": C._BIND_OWNERSHIP_CONFLICT}

    monkeypatch.setattr(community_oauth, "_oauth_guest_bind", conflicting_guest_bind)
    ticket = _issue_sync_ticket(client)

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 200
    assert response.json()["bind"] == {"bound": False, "error": C._BIND_OWNERSHIP_CONFLICT}
    # /auth-status must report the settled conflict instead of the stale
    # transient error, or every later handoff repeats the bind round trip.
    persisted = json.loads(auth.read_text(encoding="utf-8"))
    assert persisted["bind"] == {"bound": False, "error": C._BIND_OWNERSHIP_CONFLICT}


def test_social_session_init_does_not_rebind_a_settled_desktop_client(
    client,
    monkeypatch,
    tmp_path,
):
    from main_routers import community_oauth

    monkeypatch.setattr(C, "_desktop_session_snapshot", _delegate_session)
    auth = tmp_path / "community_auth.json"
    monkeypatch.setattr(C, "_auth_path", lambda: auth)

    async def unexpected_guest_bind(social_base, access_token):
        raise AssertionError("a settled bind must not cost another cloud round trip")

    monkeypatch.setattr(community_oauth, "_oauth_guest_bind", unexpected_guest_bind)

    for bind in (
        {"bound": True, "error": None},
        {"bound": False, "error": C._BIND_OWNERSHIP_CONFLICT},
    ):
        auth.write_text(json.dumps({**_delegate_session(), "bind": bind}), encoding="utf-8")
        ticket = _issue_sync_ticket(client)

        response = client.post(
            "/api/card-drop/social-session-init",
            headers={"Origin": "https://community.example"},
            json={"sync_ticket": ticket},
        )

        assert response.status_code == 200
        assert response.json()["access_token"] == "desktop-token-a"
        assert response.json()["bind"] == bind


def test_social_session_init_keeps_a_concurrent_refresh_when_persisting_bind(
    client,
    monkeypatch,
    tmp_path,
):
    """A refresh landing during the bind call owns the tokens; do not roll it back."""
    from main_routers import community_oauth

    monkeypatch.setattr(C, "_desktop_session_snapshot", _delegate_session)
    auth = tmp_path / "community_auth.json"
    auth.write_text(
        json.dumps(
            {
                "access_token": "desktop-token-a",
                "refresh_token": "desktop-refresh-old",
                "bind": {"bound": False, "error": "cloud_unreachable"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)

    async def rotate_then_bind(social_base, access_token):
        # Desktop refreshes while the bind cloud call is still in flight.
        auth.write_text(
            json.dumps(
                {
                    "access_token": "desktop-token-rotated",
                    "refresh_token": "desktop-refresh-new",
                    "bind": {"bound": False, "error": "cloud_unreachable"},
                }
            ),
            encoding="utf-8",
        )
        return {"bound": True, "error": None}

    monkeypatch.setattr(community_oauth, "_oauth_guest_bind", rotate_then_bind)
    ticket = _issue_sync_ticket(client)

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 200
    # The rotated credentials must survive; a whole-record rewrite would have
    # restored the pre-bind snapshot and broken the next refresh.
    persisted = json.loads(auth.read_text(encoding="utf-8"))
    assert persisted["access_token"] == "desktop-token-rotated"
    assert persisted["refresh_token"] == "desktop-refresh-new"


def test_social_session_init_prefers_the_saved_issuer_over_process_defaults(
    client,
    monkeypatch,
    tmp_path,
):
    """A social_session.json predating the issuer fields still has the auth mirror."""
    monkeypatch.setattr(C, "_desktop_session_snapshot", _delegate_session)
    auth = tmp_path / "community_auth.json"
    auth.write_text(
        json.dumps(
            {
                "access_token": "desktop-token-a",
                "auth_public_url": "https://auth.custom.example",
                "client_id": "neko-servers-desktop-custom",
                "bind": {"bound": True, "error": None},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    ticket = _issue_sync_ticket(client)

    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )

    assert response.status_code == 200
    payload = response.json()
    # The process defaults describe a different issuer than the handed-over token.
    assert payload["auth_public_url"] == "https://auth.custom.example"
    assert payload["client_id"] == "neko-servers-desktop-custom"


def test_social_session_init_requires_oauth_desktop_identity(client, monkeypatch):
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: None)
    ticket = _issue_sync_ticket(client)
    missing = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": ticket},
    )
    assert missing.status_code == 409
    assert missing.json() == {"detail": "desktop_login_required"}

    monkeypatch.setattr(
        C,
        "_desktop_session_snapshot",
        lambda: {**_delegate_session(), "auth_source": "legacy"},
    )
    legacy_ticket = _issue_sync_ticket(client)
    legacy = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": legacy_ticket},
    )
    assert legacy.status_code == 409
    assert legacy.json() == {"detail": "legacy_session_not_supported"}


def test_bind_client_approval_rejects_origin_before_consuming_ticket(client, monkeypatch):
    ticket = _issue_sync_ticket(client)
    monkeypatch.setattr(
        C.httpx,
        "AsyncClient",
        lambda *args, **kwargs: pytest.fail("cloud must not be contacted"),
    )

    denied = client.post(
        "/api/card-drop/bind-client/approve",
        headers={"Origin": "https://evil.example"},
        json={"binding_challenge": "C" * 43, "sync_ticket": ticket},
    )

    assert denied.status_code == 403
    assert C._sync_ticket_is_valid(ticket)


def test_social_session_prefers_electron_user_data_and_clear_removes_legacy(tmp_path, monkeypatch):
    legacy_auth = tmp_path / "documents" / "N.E.K.O" / "community_auth.json"
    electron_root = tmp_path / "electron-user-data"
    monkeypatch.setattr(C, "_auth_path", lambda: legacy_auth)
    monkeypatch.setenv("NEKO_USER_DATA_DIR", str(electron_root))

    assert C._save_auth({"access_token": "token-a"})
    assert C._save_social_session(
        "https://community.example",
        "token-a",
        "refresh-a",
        local_user_id=USER_A_ID,
        auth_source="legacy",
    )

    electron_session = electron_root / "social_session.json"
    assert json.loads(electron_session.read_text(encoding="utf-8")) == {
        "schema_version": 2,
        "baseUrl": "https://community.example",
        "token": "token-a",
        "access_token": "token-a",
        "local_user_id": USER_A_ID,
        "auth_source": "legacy",
        "refresh_token": "refresh-a",
    }
    legacy_session = legacy_auth.parent / "social_session.json"
    legacy_session.write_text("{}", encoding="utf-8")

    assert C._clear_auth()

    assert not legacy_auth.exists()
    assert not electron_session.exists()
    assert not legacy_session.exists()


def test_access_token_prefers_authoritative_refreshed_social_session(tmp_path, monkeypatch):
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    auth.write_text(
        json.dumps({"access_token": "stale-community-token"}),
        encoding="utf-8",
    )
    social.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "baseUrl": "https://community.example",
                "token": "refreshed-desktop-token",
                "refresh_token": "desktop-refresh",
                "local_user_id": USER_A_ID,
                "auth_source": "oauth",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)

    assert C._access_token() == "refreshed-desktop-token"


def test_social_session_writer_respects_desktop_cross_process_lock(
    tmp_path, monkeypatch,
):
    social = tmp_path / "social_session.json"
    original = {
        "schema_version": 2,
        "baseUrl": "https://community.example",
        "token": "desktop-refresh-in-flight",
        "local_user_id": USER_A_ID,
        "auth_source": "oauth",
    }
    social.write_text(json.dumps(original), encoding="utf-8")
    lock_path = Path(f"{social}{C._SOCIAL_SESSION_LOCK_SUFFIX}")
    lock_path.write_text(
        json.dumps({"token": "desktop-owner", "created_at": 1}),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_SOCIAL_SESSION_LOCK_TIMEOUT_SEC", 0)

    assert not C._save_social_session(
        "https://community.example",
        "backend-login",
        "backend-refresh",
        local_user_id=USER_B_ID,
        auth_source="oauth",
    )
    assert json.loads(social.read_text(encoding="utf-8")) == original
    assert lock_path.exists()

    lock_path.unlink()
    assert C._save_social_session(
        "https://community.example",
        "backend-login",
        "backend-refresh",
        local_user_id=USER_B_ID,
        auth_source="oauth",
    )
    assert json.loads(social.read_text(encoding="utf-8"))["token"] == "backend-login"
    assert not lock_path.exists()


def test_sync_session_clear_is_origin_restricted_and_private_network_aware(
    client, tmp_path, monkeypatch,
):
    auth = tmp_path / "community_auth.json"
    session = tmp_path / "social_session.json"
    auth.write_text("{}", encoding="utf-8")
    session.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: session)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: session)
    monkeypatch.setattr(C, "_access_token", lambda: "token-a")

    denied = client.post(
        "/api/card-drop/sync-session",
        headers={"Origin": "https://evil.example"},
        json={"clear": True, "base_url": "https://community.example"},
    )
    assert denied.status_code == 403
    assert auth.exists() and session.exists()

    allowed = client.post(
        "/api/card-drop/sync-session",
        headers={
            "Origin": "https://community.example",
            "Access-Control-Request-Private-Network": "true",
        },
        json={
            "logout": True,
            "base_url": "https://community.example",
            "access_token": "token-a",
            "sync_ticket": _issue_sync_ticket(client),
        },
    )
    assert allowed.status_code == 200
    assert allowed.json() == {"ok": True, "cleared": True}
    assert allowed.headers["access-control-allow-origin"] == "https://community.example"
    assert allowed.headers["access-control-allow-private-network"] == "true"
    assert not auth.exists() and not session.exists()


def test_sync_session_logout_cannot_clear_another_accounts_session(
    client, tmp_path, monkeypatch,
):
    auth = tmp_path / "community_auth.json"
    session = tmp_path / "social_session.json"
    auth.write_text('{"access_token":"account-a"}', encoding="utf-8")
    session.write_text('{"token":"account-a"}', encoding="utf-8")
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: session)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: session)
    monkeypatch.setattr(C, "_access_token", lambda: "account-a")

    response = client.post(
        "/api/card-drop/sync-session",
        headers={"Origin": "https://community.example"},
        json={
            "clear": True,
            "base_url": "https://community.example",
            "access_token": "account-b",
            "sync_ticket": _issue_sync_ticket(client),
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "local_session_mismatch"}
    assert response.headers["access-control-allow-origin"] == "https://community.example"
    assert auth.exists() and session.exists()


class _CloudResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _identity_http_client(
    monkeypatch,
    identities: dict[str, tuple[str, str] | int | str],
    *,
    bind_status: int = 200,
):
    seen: list[tuple[str, str]] = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            assert url == "https://community.example/api/users/me"
            authorization = kwargs["headers"]["Authorization"]
            token = authorization.removeprefix("Bearer ")
            seen.append(("get", token))
            result = identities.get(token, 401)
            if result == "unavailable":
                raise OSError("network unavailable")
            if isinstance(result, int):
                return _CloudResponse(result, {"detail": "rejected"})
            local_user_id, auth_source = result
            return _CloudResponse(
                200,
                {
                    "auth_source": auth_source,
                    "user": {
                        "id": local_user_id,
                        "display_name": "Verified User",
                        "email": "verified@example.com",
                    },
                },
            )

        async def post(self, url, **kwargs):
            assert url == "https://community.example/api/auth/bind-client"
            seen.append(("post", kwargs["headers"]["Authorization"].removeprefix("Bearer ")))
            return _CloudResponse(bind_status, {})

    monkeypatch.setattr(C.httpx, "AsyncClient", FakeAsyncClient)
    return seen


def _write_v2_desktop_session(
    tmp_path,
    monkeypatch,
    *,
    token: str = "desktop-token",
    refresh_token: str = "desktop-refresh",
    local_user_id: str = USER_A_ID,
    auth_source: str = "oauth",
):
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    auth.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "access_token": token,
                "refresh_token": refresh_token,
                "local_user_id": local_user_id,
                "auth_source": auth_source,
                "user": {"id": local_user_id},
            }
        ),
        encoding="utf-8",
    )
    social.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "baseUrl": "https://community.example",
                "token": token,
                "refresh_token": refresh_token,
                "local_user_id": local_user_id,
                "auth_source": auth_source,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)
    return auth, social


def test_platform_web_token_sync_is_rejected_without_writes_or_ticket_consumption(
    client,
    tmp_path,
    monkeypatch,
    caplog,
):
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)
    seen = _identity_http_client(
        monkeypatch,
        {"opaque-platform-access-secret": (USER_A_ID, "oauth")},
    )
    writes: list[tuple[Path, dict]] = []
    monkeypatch.setattr(C, "_write_private_json", lambda path, data: writes.append((path, data)))
    ticket = _issue_sync_ticket(client)
    caplog.set_level(logging.DEBUG, logger="neko.card_drop")

    response = client.post(
        "/api/card-drop/sync-session",
        headers={"Origin": "https://community.example"},
        json={
            "base_url": "https://community.example",
            "access_token": "opaque-platform-access-secret",
            "refresh_token": "platform-refresh-secret",
            "sync_ticket": ticket,
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "platform_token_native_sync_forbidden"}
    assert C._sync_ticket_is_valid(ticket)
    assert writes == []
    assert not auth.exists()
    assert not social.exists()
    assert seen == [("get", "opaque-platform-access-secret")]
    assert "opaque-platform-access-secret" not in caplog.text
    assert "platform-refresh-secret" not in caplog.text


def test_legacy_web_sync_persists_v2_identity_metadata(client, tmp_path, monkeypatch):
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_get_client_id", lambda: None)
    _identity_http_client(
        monkeypatch,
        {"legacy-access": (USER_A_ID, "legacy")},
    )

    response = client.post(
        "/api/card-drop/sync-session",
        headers={"Origin": "https://community.example"},
        json={
            "base_url": "https://community.example",
            "access_token": "legacy-access",
            "refresh_token": "legacy-refresh",
            "sync_ticket": _issue_sync_ticket(client),
        },
    )

    assert response.status_code == 200
    social_data = json.loads(social.read_text(encoding="utf-8"))
    auth_data = json.loads(auth.read_text(encoding="utf-8"))
    assert social_data == {
        "schema_version": 2,
        "baseUrl": "https://community.example",
        "token": "legacy-access",
        "access_token": "legacy-access",
        "local_user_id": USER_A_ID,
        "auth_source": "legacy",
        "refresh_token": "legacy-refresh",
    }
    assert auth_data["schema_version"] == 2
    assert auth_data["local_user_id"] == USER_A_ID
    assert auth_data["auth_source"] == "legacy"
    assert auth_data["user"]["id"] == USER_A_ID


def test_sync_session_status_is_exact_cors_read_only_and_identity_scoped(
    client,
    tmp_path,
    monkeypatch,
):
    auth, social = _write_v2_desktop_session(tmp_path, monkeypatch)
    original_auth = auth.read_text(encoding="utf-8")
    original_social = social.read_text(encoding="utf-8")
    seen = _identity_http_client(
        monkeypatch,
        {
            "same-user-new-token": (USER_A_ID, "oauth"),
            "other-user-token": (USER_B_ID, "oauth"),
        },
    )

    preflight = client.options(
        "/api/card-drop/sync-session/status",
        headers={
            "Origin": "https://community.example",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    same_user = client.get(
        "/api/card-drop/sync-session/status",
        headers={
            "Origin": "https://community.example",
            "Authorization": "Bearer same-user-new-token",
        },
    )
    other_user = client.get(
        "/api/card-drop/sync-session/status",
        headers={
            "Origin": "https://community.example",
            "Authorization": "Bearer other-user-token",
        },
    )
    lookalike_origin = client.get(
        "/api/card-drop/sync-session/status",
        headers={
            "Origin": "https://community.example.evil",
            "Authorization": "Bearer same-user-new-token",
        },
    )

    assert preflight.status_code == 200
    assert preflight.json() == {"ok": True, "synced": False}
    assert preflight.headers["access-control-allow-methods"] == "GET, OPTIONS"
    assert preflight.headers["access-control-allow-headers"] == "authorization"
    assert preflight.headers["access-control-allow-private-network"] == "true"
    assert same_user.status_code == 200
    assert same_user.json() == {"ok": True, "synced": True}
    assert same_user.headers["access-control-allow-origin"] == "https://community.example"
    assert same_user.headers["cache-control"] == "no-store"
    assert other_user.status_code == 200
    assert other_user.json() == {"ok": True, "synced": False}
    assert lookalike_origin.status_code == 403
    assert lookalike_origin.json() == {"ok": True, "synced": False}
    assert seen == [
        ("get", "same-user-new-token"),
        ("get", "other-user-token"),
    ]
    assert auth.read_text(encoding="utf-8") == original_auth
    assert social.read_text(encoding="utf-8") == original_social


def test_sync_session_status_safely_backfills_legacy_desktop_identity(
    client,
    tmp_path,
    monkeypatch,
):
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    auth.write_text(
        json.dumps(
            {
                "access_token": "old-desktop-token",
                "refresh_token": "old-desktop-refresh",
                "user": {"display_name": "Old User"},
            }
        ),
        encoding="utf-8",
    )
    social.write_text(
        json.dumps(
            {
                "baseUrl": "https://community.example",
                "token": "old-desktop-token",
                "refresh_token": "old-desktop-refresh",
                "session_generation": 7,
                "refresh_manager_extension": "preserve-me",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)
    seen = _identity_http_client(
        monkeypatch,
        {
            "browser-proof-token": (USER_A_ID, "oauth"),
            "old-desktop-token": (USER_A_ID, "legacy"),
        },
    )

    response = client.get(
        "/api/card-drop/sync-session/status",
        headers={
            "Origin": "https://community.example",
            "Authorization": "Bearer browser-proof-token",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "synced": True}
    assert seen == [
        ("get", "browser-proof-token"),
        ("get", "old-desktop-token"),
    ]
    social_data = json.loads(social.read_text(encoding="utf-8"))
    auth_data = json.loads(auth.read_text(encoding="utf-8"))
    assert social_data["schema_version"] == 2
    assert social_data["token"] == "old-desktop-token"
    assert social_data["refresh_token"] == "old-desktop-refresh"
    assert social_data["session_generation"] == 7
    assert social_data["refresh_manager_extension"] == "preserve-me"
    assert social_data["local_user_id"] == USER_A_ID
    assert social_data["auth_source"] == "legacy"
    assert auth_data["access_token"] == "old-desktop-token"
    assert auth_data["local_user_id"] == USER_A_ID
    assert auth_data["auth_source"] == "legacy"
    assert "browser-proof-token" not in social.read_text(encoding="utf-8")
    assert "browser-proof-token" not in auth.read_text(encoding="utf-8")


def test_identity_backfill_refuses_to_overwrite_a_rotated_desktop_session(
    tmp_path,
    monkeypatch,
):
    social = tmp_path / "social_session.json"
    rotated = {
        "schema_version": 2,
        "baseUrl": "https://community.example",
        "token": "rotated-access",
        "refresh_token": "rotated-refresh",
        "session_generation": 8,
    }
    social.write_text(json.dumps(rotated), encoding="utf-8")
    monkeypatch.setattr(C, "_auth_path", lambda: None)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)

    saved = C._persist_session_identity_metadata(
        {
            "base_url": "https://community.example",
            "access_token": "stale-access",
            "refresh_token": "stale-refresh",
        },
        USER_A_ID,
        "legacy",
    )

    assert saved is False
    assert json.loads(social.read_text(encoding="utf-8")) == rotated


def _cloud_client(monkeypatch, *, login: dict | None = None):
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            assert url.endswith("/api/users/me")
            return _CloudResponse(
                200,
                {
                    "auth_source": "legacy",
                    "user": {
                        "id": USER_A_ID,
                        "display_name": "New User",
                        "email": "new@example.com",
                    },
                },
            )

        async def post(self, url, **kwargs):
            if url.endswith("/api/auth/login") or url.endswith("/api/auth/register"):
                assert login is not None
                return _CloudResponse(200, login)
            assert url.endswith("/api/auth/bind-client")
            return _CloudResponse(409, {"detail": "client_already_bound_to_other_user"})

    monkeypatch.setattr(C.httpx, "AsyncClient", FakeAsyncClient)


def _existing_session_files(tmp_path, monkeypatch):
    auth = tmp_path / "community_auth.json"
    session = tmp_path / "social_session.json"
    old_auth = {
        "access_token": "old-token",
        "refresh_token": "old-refresh",
        "user": {"display_name": "Old User", "email": "old@example.com"},
        "bind": {"bound": True, "error": None},
    }
    old_session = {
        "baseUrl": "https://community.example",
        "token": "old-token",
        "refresh_token": "old-refresh",
    }
    auth.write_text(json.dumps(old_auth), encoding="utf-8")
    session.write_text(json.dumps(old_session), encoding="utf-8")
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: session)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: session)
    monkeypatch.setattr(C, "_get_client_id", lambda: "local-client-id")
    return auth, session, old_auth, old_session


@pytest.mark.asyncio
async def test_bind_ownership_conflict_never_publishes_new_token(tmp_path, monkeypatch):
    auth, session, old_auth, old_session = _existing_session_files(tmp_path, monkeypatch)
    _cloud_client(monkeypatch)
    writes: list[tuple[Path, dict]] = []
    original_write = C._write_private_json

    def track_write(path, data):
        writes.append((path, data))
        original_write(path, data)

    monkeypatch.setattr(C, "_write_private_json", track_write)

    with pytest.raises(C._ClientBindingConflict) as exc_info:
        await C._store_session(
            "https://community.example",
            "wrong-new-token",
            "wrong-new-refresh",
            {"id": USER_A_ID, "display_name": "New User", "email": "new@example.com"},
        )

    assert exc_info.value.detail == "client_already_bound_to_other_user"
    assert writes == []  # Electron cannot observe even a transient wrong social_session token.
    assert json.loads(auth.read_text(encoding="utf-8")) == old_auth
    assert json.loads(session.read_text(encoding="utf-8")) == old_session


def test_sync_session_skips_legacy_client_binding_and_replaces_session(
    client, tmp_path, monkeypatch,
):
    auth, session, old_auth, old_session = _existing_session_files(tmp_path, monkeypatch)
    _cloud_client(monkeypatch)

    missing_ticket = client.post(
        "/api/card-drop/sync-session",
        headers={"Origin": "https://community.example"},
        json={
            "base_url": "https://community.example",
            "access_token": "wrong-new-token",
            "refresh_token": "wrong-new-refresh",
        },
    )
    assert missing_ticket.status_code == 403
    assert missing_ticket.json() == {"detail": "invalid_sync_ticket"}

    response = client.post(
        "/api/card-drop/sync-session",
        headers={"Origin": "https://community.example"},
        json={
            "base_url": "https://community.example",
            "access_token": "wrong-new-token",
            "refresh_token": "wrong-new-refresh",
            "sync_ticket": _issue_sync_ticket(client),
        },
    )

    assert response.status_code == 200
    assert response.json()["bind"] == {
        "bound": False,
        "error": None,
        "skipped": "native_session_sync",
    }
    assert response.headers["access-control-allow-origin"] == "https://community.example"
    saved_auth = json.loads(auth.read_text(encoding="utf-8"))
    saved_session = json.loads(session.read_text(encoding="utf-8"))
    assert saved_auth["access_token"] == "wrong-new-token"
    assert saved_auth["refresh_token"] == "wrong-new-refresh"
    assert saved_auth["bind"]["skipped"] == "native_session_sync"
    assert saved_session["token"] == "wrong-new-token"
    assert saved_session["refresh_token"] == "wrong-new-refresh"


def test_legacy_local_login_returns_410(client, tmp_path, monkeypatch):
    auth, session, old_auth, old_session = _existing_session_files(tmp_path, monkeypatch)

    response = client.post(
        "/api/card-drop/login",
        json={
            "email": "new@example.com",
            "password": "password123",
            "sync_ticket": _issue_sync_ticket(client),
        },
    )

    assert response.status_code == 410
    assert response.json() == {"detail": "legacy_community_login_removed"}
    assert json.loads(auth.read_text(encoding="utf-8")) == old_auth
    assert json.loads(session.read_text(encoding="utf-8")) == old_session


@pytest.mark.parametrize(
    "path",
    [
        "/api/card-drop/login",
        "/api/card-drop/register",
    ],
)
def test_legacy_password_auth_returns_410(client, path):
    response = client.post(
        path,
        json={"email": "user@example.com", "password": "password123"},
    )
    assert response.status_code == 410
    assert response.json() == {"detail": "legacy_community_login_removed"}


@pytest.mark.asyncio
async def test_recoverable_bind_error_still_persists_validated_login(tmp_path, monkeypatch):
    auth = tmp_path / "community_auth.json"
    session = tmp_path / "social_session.json"
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: session)
    monkeypatch.setattr(C, "_get_client_id", lambda: None)

    bind = await C._store_session(
        "https://community.example",
        "new-token",
        "new-refresh",
        {"id": USER_A_ID, "display_name": "New User", "email": "new@example.com"},
    )

    assert bind == {"bound": False, "error": "client_not_registered"}
    assert json.loads(auth.read_text(encoding="utf-8"))["access_token"] == "new-token"
    assert json.loads(session.read_text(encoding="utf-8"))["token"] == "new-token"


@pytest.mark.asyncio
async def test_store_session_offloads_local_credential_io(monkeypatch):
    event_loop_thread = threading.get_ident()
    worker_threads: dict[str, int] = {}

    def get_client_id():
        worker_threads["client_id"] = threading.get_ident()
        return None

    def save_auth(_payload):
        worker_threads["auth"] = threading.get_ident()
        return True

    def save_social_session(*_args, **_kwargs):
        worker_threads["social"] = threading.get_ident()
        return True

    monkeypatch.setattr(C, "_get_client_id", get_client_id)
    monkeypatch.setattr(C, "_save_auth", save_auth)
    monkeypatch.setattr(C, "_save_social_session", save_social_session)

    bind = await C._store_session(
        "https://community.example",
        "new-token",
        "new-refresh",
        {"id": USER_A_ID, "display_name": "New User", "email": "new@example.com"},
    )

    assert bind == {"bound": False, "error": "client_not_registered"}
    assert set(worker_threads) == {"client_id", "auth", "social"}
    assert all(thread_id != event_loop_thread for thread_id in worker_threads.values())


@pytest.mark.asyncio
async def test_store_session_reports_partial_local_save_failure(tmp_path, monkeypatch):
    auth = tmp_path / "community_auth.json"
    session = tmp_path / "social_session.json"
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: session)
    monkeypatch.setattr(C, "_get_client_id", lambda: None)
    original_write = C._write_private_json

    def fail_social_write(path, data):
        if path == session:
            raise OSError("disk full")
        original_write(path, data)

    monkeypatch.setattr(C, "_write_private_json", fail_social_write)

    bind = await C._store_session(
        "https://community.example",
        "new-token",
        "new-refresh",
        {"id": USER_A_ID, "display_name": "New User", "email": "new@example.com"},
    )

    assert bind == {
        "bound": False,
        "error": "client_not_registered",
        "local_save_failed": True,
    }
    assert json.loads(auth.read_text(encoding="utf-8"))["bind"] == bind
    assert not session.exists()


def test_sync_session_clear_offloads_local_credential_io(client, monkeypatch):
    ticket = _issue_sync_ticket(client)
    original_consume = C._consume_sync_ticket
    thread_ids: dict[str, int] = {}

    def consume_sync_ticket(value):
        thread_ids.setdefault("event_loop", threading.get_ident())
        return original_consume(value)

    def access_token():
        thread_ids["access"] = threading.get_ident()
        return "token-a"

    def clear_auth():
        thread_ids["clear"] = threading.get_ident()
        return True

    monkeypatch.setattr(C, "_consume_sync_ticket", consume_sync_ticket)
    monkeypatch.setattr(C, "_access_token", access_token)
    monkeypatch.setattr(C, "_clear_auth", clear_auth)

    response = client.post(
        "/api/card-drop/sync-session",
        headers={"Origin": "https://community.example"},
        json={
            "clear": True,
            "base_url": "https://community.example",
            "access_token": "token-a",
            "sync_ticket": ticket,
        },
    )

    assert response.status_code == 200
    assert thread_ids["access"] != thread_ids["event_loop"]
    assert thread_ids["clear"] != thread_ids["event_loop"]


def test_sync_session_clear_reports_local_delete_failure(client, tmp_path, monkeypatch):
    auth = tmp_path / "community_auth.json"
    auth.write_text('{"access_token":"token-a"}', encoding="utf-8")
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: None)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: None)
    monkeypatch.setattr(C, "_access_token", lambda: "token-a")
    monkeypatch.setattr(
        Path,
        "unlink",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError("busy")),
    )

    response = client.post(
        "/api/card-drop/sync-session",
        headers={"Origin": "https://community.example"},
        json={
            "clear": True,
            "base_url": "https://community.example",
            "access_token": "token-a",
            "sync_ticket": _issue_sync_ticket(client),
        },
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "local_clear_failed", "cleared": False}
    assert auth.exists()


def test_local_logout_requires_local_origin_and_single_use_ticket(client, monkeypatch):
    clear_calls = 0

    def clear_auth():
        nonlocal clear_calls
        clear_calls += 1
        return True

    monkeypatch.setattr(C, "_clear_auth", clear_auth)

    missing_ticket = client.post("/api/card-drop/logout")
    assert missing_ticket.status_code == 403
    assert missing_ticket.json() == {"detail": "invalid_sync_ticket"}

    ticket = _issue_sync_ticket(client)
    denied = client.post(
        "/api/card-drop/logout",
        headers={"Origin": "https://evil.example"},
        json={"sync_ticket": ticket},
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "origin_not_allowed"}
    assert C._sync_ticket_is_valid(ticket)

    allowed = client.post(
        "/api/card-drop/logout",
        headers={"Origin": "http://127.0.0.1:48911"},
        json={"syncTicket": ticket},
    )
    assert allowed.status_code == 200
    assert allowed.json() == {"logged_in": False}

    replay = client.post(
        "/api/card-drop/logout",
        headers={"Origin": "http://localhost:48911"},
        json={"sync_ticket": ticket},
    )
    assert replay.status_code == 403
    assert replay.json() == {"detail": "invalid_sync_ticket"}
    assert clear_calls == 1


def test_local_logout_reports_local_delete_failure(client, monkeypatch):
    monkeypatch.setattr(C, "_clear_auth", lambda: False)

    response = client.post(
        "/api/card-drop/logout",
        json={"sync_ticket": _issue_sync_ticket(client)},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "local_clear_failed"}


def test_facts_requires_trusted_origin_and_same_validated_user(
    client,
    tmp_path,
    monkeypatch,
    caplog,
):
    auth, social = _write_v2_desktop_session(tmp_path, monkeypatch)
    seen = _identity_http_client(
        monkeypatch,
        {
            "same-user-new-token": (USER_A_ID, "oauth"),
            "other-user-token": (USER_B_ID, "oauth"),
            "unavailable-token": "unavailable",
        },
    )
    build_calls = 0

    async def fake_build(**kwargs):
        nonlocal build_calls
        build_calls += 1
        return {"character": kwargs["runtime_character_hint"], "facts": [{"id": "f1"}]}

    monkeypatch.setattr(C, "_build_local_forge_facts", fake_build)
    caplog.set_level(logging.DEBUG, logger="neko.card_drop")

    no_origin = client.get(
        "/api/card-drop/facts?runtime_character_hint=Lanlan",
        headers={"Authorization": "Bearer same-user-new-token"},
    )
    assert no_origin.status_code == 403

    wrong_token = client.get(
        "/api/card-drop/facts?runtime_character_hint=Lanlan",
        headers={
            "Origin": "https://community.example",
            "Authorization": "Bearer other-user-token",
        },
    )
    assert wrong_token.status_code == 401

    ok = client.get(
        "/api/card-drop/facts?runtime_character_hint=Lanlan&limit=5&min_importance=0",
        headers={
            "Origin": "https://community.example",
            "Authorization": "Bearer same-user-new-token",
        },
    )
    assert ok.status_code == 200
    assert ok.json() == {"character": "Lanlan", "facts": [{"id": "f1"}]}
    assert ok.headers["access-control-allow-origin"] == "https://community.example"

    unavailable = client.get(
        "/api/card-drop/facts?runtime_character_hint=Lanlan",
        headers={
            "Origin": "https://community.example",
            "Authorization": "Bearer unavailable-token",
        },
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "identity_verification_unavailable"}
    assert build_calls == 1
    assert seen == [
        ("get", "other-user-token"),
        ("get", "same-user-new-token"),
        ("get", "unavailable-token"),
    ]
    persisted = auth.read_text(encoding="utf-8") + social.read_text(encoding="utf-8")
    for sensitive in (
        "other-user-token",
        "same-user-new-token",
        "unavailable-token",
    ):
        assert sensitive not in persisted
        assert sensitive not in caplog.text


def test_facts_preflight_allows_only_configured_community_origin(client):
    ok = client.options(
        "/api/card-drop/facts",
        headers={
            "Origin": "https://community.example",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-methods"] == "GET, POST, OPTIONS"
    assert ok.headers["access-control-allow-headers"] == (
        "authorization, content-type, x-neko-local-user-id"
    )
    assert ok.headers["access-control-allow-private-network"] == "true"

    denied = client.options(
        "/api/card-drop/facts",
        headers={"Origin": "https://evil.example"},
    )
    assert denied.status_code == 403


def test_callback_access_logs_suppress_sensitive_query_parameters(caplog):
    from utils.logger_config import create_main_server_filter

    access_logger = logging.getLogger("uvicorn.access")
    callback_filter = create_main_server_filter()
    access_logger.addFilter(callback_filter)
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    try:
        for path in (
            "/oauth/callback",
            "/api/card-drop/oauth/callback",
            "/api/card-drop/steam-callback",
        ):
            access_logger.info(
                '127.0.0.1 - "GET %s?code=secret-code&state=secret-state'
                '&access_token=secret-token HTTP/1.1" 200',
                path,
            )
    finally:
        access_logger.removeFilter(callback_filter)

    assert "secret-code" not in caplog.text
    assert "secret-state" not in caplog.text
    assert "secret-token" not in caplog.text


@pytest.mark.asyncio
async def test_archive_pick_excludes_rows_still_present_in_active_facts(
    tmp_path, monkeypatch
):
    """A half-committed row must not be offered as a "distant" archive memory.

    ``FactStore._archive_absorbed`` writes facts_archive.json before
    facts.json, so an interrupted commit leaves the row in both files until
    the next successful archive pass. The exclusion set therefore has to come
    from the whole active file, not from the handful of facts this call
    happened to pick: any row that was not picked would otherwise come back
    through the archive branch while still being live.
    """
    facts_path = tmp_path / "facts.json"
    archive_path = tmp_path / "facts_archive.json"
    active = [
        {
            "id": f"f{i}",
            "text": f"fact {i}",
            "importance": 8,
            "hash": f"h{i}",
            "created_at": "2020-01-01T00:00:00Z",
        }
        for i in range(6)
    ]
    facts_path.write_text(json.dumps(active), encoding="utf-8")
    # 归档里是全部 6 条的副本 = 最坏情况的半提交：无论这次抽中哪 5 条，
    # 剩下那条都会从归档侧漏回来。
    archive_path.write_text(json.dumps(active), encoding="utf-8")

    async def fake_context(*_args, **_kwargs):
        return ActiveNekoContext(
            master_name="Master",
            lanlan_name="Lanlan",
            memory_dir=tmp_path,
            facts_path=facts_path,
            source="test",
        )

    monkeypatch.setattr(F, "resolve_active_neko_context", fake_context)
    payload = await build_forge_facts_payload(
        runtime_character_hint="Lanlan",
        min_importance=0,
        limit=5,
    )

    assert payload["returnedCount"] == 5
    assert payload["archiveRawCount"] == 6
    assert payload["totalMemoryCount"] == 6
    assert payload["archiveFilteredCount"] == 0, payload["archiveFilteredCount"]
    sources = [fact["sourceCollection"] for fact in payload["facts"]]
    assert "facts_archive" not in sources, sources


@pytest.mark.asyncio
async def test_archive_pick_excludes_trust_arbitration_losers(
    tmp_path, monkeypatch,
):
    facts_path = tmp_path / "facts.json"
    archive_path = tmp_path / "facts_archive.json"
    facts_path.write_text("[]", encoding="utf-8")
    archive_path.write_text(json.dumps([{
        "id": "rejected",
        "text": "rejected high importance claim",
        "importance": 10,
        "created_at": "2020-01-01T00:00:00Z",
        "arbitration_archived_at": "2026-08-01T00:00:00Z",
        "arbitration_reason": "trust_superseded",
    }]), encoding="utf-8")

    async def fake_context(*_args, **_kwargs):
        return ActiveNekoContext(
            master_name="Master", lanlan_name="Lanlan",
            memory_dir=tmp_path, facts_path=facts_path, source="test",
        )

    monkeypatch.setattr(F, "resolve_active_neko_context", fake_context)
    payload = await build_forge_facts_payload(
        runtime_character_hint="Lanlan", min_importance=0, limit=5,
    )
    assert payload["facts"] == []
    assert payload["archiveRawCount"] == 1
    assert payload["archiveFilteredCount"] == 0


def test_repaired_bind_cannot_change_a_newer_account_mirror(tmp_path, monkeypatch):
    auth = tmp_path / "community_auth.json"
    mirror = {"access_token": "account-b", "local_user_id": USER_B_ID,
              "auth_source": "oauth", "bind": {"bound": False, "error": "B-error"}}
    auth.write_text(json.dumps(mirror), encoding="utf-8")
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: tmp_path / "social_session.json")
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: _delegate_session())
    C._persist_repaired_bind(_delegate_session()["access_token"], {"bound": True})
    assert json.loads(auth.read_text()) == mirror


@pytest.mark.parametrize("initial_session", [None, "account-a"])
def test_old_sync_ticket_cannot_disclose_a_later_account(client, monkeypatch, initial_session):
    snapshot = _delegate_session() if initial_session else None
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshot)
    ticket = _issue_sync_ticket(client)
    snapshot = {**_delegate_session(), "local_user_id": USER_B_ID, "access_token": "account-b"}
    response = client.post("/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"}, json={"sync_ticket": ticket})
    assert response.status_code == 403
    assert response.json() == {"detail": "invalid_sync_ticket"}
    # The low-level final consumer also checks the minting session, closing a
    # switch after preflight and preserving guest-bind-only tickets' purpose.
    assert C._consume_sync_ticket_for_verified_session(ticket, "account-b") == "invalid"


def test_sync_ticket_is_redeemable_after_refreshing_an_expired_desktop_session(
    client, monkeypatch,
):
    from main_routers import community_oauth

    current = {"snapshot": _delegate_session(access_token="expired-desktop-token")}
    refreshed = _delegate_session(access_token="refreshed-desktop-token")
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: current["snapshot"])

    async def refresh_saved_session():
        current["snapshot"] = refreshed
        return {"logged_in": True, "snapshot": refreshed, "auth": {}}

    monkeypatch.setattr(community_oauth, "resolve_saved_oauth_status", refresh_saved_session)
    ticket = _issue_sync_ticket(client)
    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"}, json={"sync_ticket": ticket},
    )
    assert response.status_code == 200
    assert response.json()["access_token"] == "refreshed-desktop-token"
    assert not C._sync_ticket_is_valid(ticket)


@pytest.mark.parametrize("foreign_bind", [
    {"bound": True, "error": None},
    {"bound": False, "error": C._BIND_OWNERSHIP_CONFLICT},
])
def test_social_session_init_ignores_a_newer_accounts_bind(
    client, monkeypatch, tmp_path, foreign_bind,
):
    from main_routers import community_oauth

    auth_path = tmp_path / "community_auth.json"
    mirror = {**_delegate_session(local_user_id=USER_B_ID, access_token="account-b"),
              "auth_public_url": "https://foreign-issuer.example", "client_id": "foreign-client",
              "bind": foreign_bind}
    auth_path.write_text(json.dumps(mirror), encoding="utf-8")
    monkeypatch.setattr(C, "_auth_path", lambda: auth_path)
    monkeypatch.setattr(C, "_social_session_path", lambda: tmp_path / "social_session.json")
    monkeypatch.setattr(C, "_desktop_session_snapshot", _delegate_session)
    bound = []

    async def bind_current_account(_base, token):
        bound.append(token)
        return {"bound": False, "error": "cloud_unreachable"}

    monkeypatch.setattr(community_oauth, "_oauth_guest_bind", bind_current_account)
    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": _issue_sync_ticket(client)},
    )
    assert response.status_code == 200
    assert bound == ["desktop-token-a"]
    assert response.json()["bind"] == {"bound": False, "error": "cloud_unreachable"}
    assert response.json()["auth_public_url"] != "https://foreign-issuer.example"
    assert response.json()["client_id"] != "foreign-client"
    assert json.loads(auth_path.read_text()) == mirror


@pytest.mark.parametrize("settled_bind", [
    {"bound": True, "error": None},
    {"bound": False, "error": C._BIND_OWNERSHIP_CONFLICT},
])
def test_social_session_init_reuses_a_repaired_bind_with_a_stale_mirror(
    client, monkeypatch, tmp_path, settled_bind,
):
    from main_routers import community_oauth

    auth = tmp_path / "community_auth.json"
    mirror = {**_delegate_session(access_token="mirror-token"),
              "refresh_token": "mirror-refresh",
              "bind": {"bound": False, "error": "cloud_unreachable"}}
    auth.write_text(json.dumps(mirror), encoding="utf-8")
    monkeypatch.setattr(C, "_social_session_path", lambda: tmp_path / "social_session.json")
    monkeypatch.setattr(C, "_desktop_session_snapshot", _delegate_session)
    binds = []

    async def bind_account(_base, access):
        binds.append(access)
        return settled_bind

    monkeypatch.setattr(community_oauth, "_oauth_guest_bind", bind_account)
    for _ in range(2):
        response = client.post(
            "/api/card-drop/social-session-init",
            headers={"Origin": "https://community.example"},
            json={"sync_ticket": _issue_sync_ticket(client)},
        )
        assert response.status_code == 200
        assert response.json()["bind"] == settled_bind
    assert binds == ["desktop-token-a"]
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: _delegate_session(
        access_token="rotated-authoritative",
    ))
    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"},
        json={"sync_ticket": _issue_sync_ticket(client)},
    )
    assert response.status_code == 200
    assert binds == ["desktop-token-a", "rotated-authoritative"]
    # Repairing a bind must not roll back a mirror whose credentials may have
    # advanced first during a same-account two-file publication.
    persisted = json.loads(auth.read_text(encoding="utf-8"))
    assert persisted["access_token"] == mirror["access_token"]
    assert persisted["refresh_token"] == mirror["refresh_token"]


def test_social_session_init_validates_a_settled_session_once(client, monkeypatch):
    from main_routers import community_oauth

    monkeypatch.setattr(C, "_desktop_session_snapshot", _delegate_session)
    validations = []

    async def validate_session():
        validations.append(True)
        return {"logged_in": True, "snapshot": _delegate_session(), "auth": {}}

    monkeypatch.setattr(community_oauth, "resolve_saved_oauth_status", validate_session)
    ticket = _issue_sync_ticket(client)
    validations.clear()
    response = client.post(
        "/api/card-drop/social-session-init",
        headers={"Origin": "https://community.example"}, json={"sync_ticket": ticket},
    )
    assert response.status_code == 200
    assert len(validations) == 1


@pytest.mark.parametrize("replacement", [
    {"access_token": "replacement-token"},
    {"refresh_token": "replacement-refresh"},
    {"base_url": "https://another-community.example"},
    {"local_user_id": USER_B_ID},
    {"auth_source": "legacy"},
])
@pytest.mark.asyncio
async def test_native_session_snapshot_rejects_a_replacement_after_cloud_validation(
    monkeypatch, replacement,
):
    from main_routers import community_oauth

    async def validated_previous_session():
        return {"logged_in": True, "snapshot": _delegate_session(), "auth": {}}

    monkeypatch.setattr(community_oauth, "resolve_saved_oauth_status", validated_previous_session)
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: {**_delegate_session(), **replacement})
    snapshot, _failure = await C._native_delegate_session_snapshot()
    assert snapshot is None


@pytest.mark.parametrize("missing_metadata", [
    {"local_user_id": "", "auth_source": ""},
    {"local_user_id": ""},
    {"auth_source": ""},
])
@pytest.mark.asyncio
async def test_native_session_snapshot_accepts_concurrent_identity_backfill(
    monkeypatch, missing_metadata,
):
    from main_routers import community_oauth

    async def validated_session_before_backfill():
        return {
            "logged_in": True,
            "snapshot": {**_delegate_session(), **missing_metadata},
            "auth": {},
        }

    monkeypatch.setattr(community_oauth, "resolve_saved_oauth_status", validated_session_before_backfill)
    monkeypatch.setattr(C, "_desktop_session_snapshot", _delegate_session)
    snapshot, failure = await C._native_delegate_session_snapshot()
    assert snapshot == _delegate_session()
    assert failure == ""
