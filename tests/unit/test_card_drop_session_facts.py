from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import main_routers.card_drop_router as C
import main_logic.card_forge_facts as F
from main_logic.card_forge_facts import ActiveNekoContext, build_forge_facts_payload

pytestmark = pytest.mark.unit

USER_A_ID = "11111111-1111-4111-8111-111111111111"
USER_B_ID = "22222222-2222-4222-8222-222222222222"


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
            "path": "/card-forge/active-character",
            "raw_path": b"/card-forge/active-character",
            "query_string": b"",
            "headers": headers,
        }
    )


@pytest.mark.asyncio
async def test_main_active_character_post_allows_native_and_local_origin(monkeypatch):
    from app.main_server import web_app

    snapshot: dict[str, str] = {}
    monkeypatch.setattr(web_app, "_card_forge_active_character", snapshot)

    native_response = await web_app.set_card_forge_active_character(
        _main_server_request(),
        {"name": "Native"},
    )
    local_response = await web_app.set_card_forge_active_character(
        _main_server_request(origin="http://localhost:48911"),
        {"name": "Local"},
    )

    assert native_response == {"ok": True}
    assert local_response == {"ok": True}
    assert snapshot == {"name": "Local"}


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
    monkeypatch.setattr(web_app, "_card_forge_active_character", snapshot)

    response = await web_app.set_card_forge_active_character(
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
    assert headers["Access-Control-Allow-Methods"] == "GET, OPTIONS"


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


def test_packaged_facts_modules_use_package_qualified_imports():
    shared = importlib.import_module("main_logic.card_forge_facts")
    server = importlib.import_module("local_server.card_forge_server.server")

    assert callable(server.build_forge_facts_payload)
    assert server.build_forge_facts_payload is shared.build_forge_facts_payload
    assert callable(shared.resolve_active_neko_context)


@pytest.mark.parametrize(
    ("environment", "expected_port"),
    [
        ({"NEKO_MAIN_SERVER_PORT": "43101", "MAIN_SERVER_PORT": "43102"}, 43101),
        ({"MAIN_SERVER_PORT": "43102"}, 43102),
        ({"NEKO_MAIN_SERVER_PORT": "invalid", "MAIN_SERVER_PORT": "43102"}, 43102),
        ({"NEKO_MAIN_SERVER_PORT": "70000", "MAIN_SERVER_PORT": "invalid"}, 48911),
    ],
)
def test_forge_main_active_character_url_tracks_main_server_port(
    monkeypatch, tmp_path, environment, expected_port
):
    server = importlib.import_module("local_server.card_forge_server.server")
    monkeypatch.setattr(
        server,
        "_main_server_port_config_path",
        lambda: tmp_path / "missing-port-config.json",
    )
    for key in (
        "NEKO_MAIN_ACTIVE_CHARACTER_URL",
        "NEKO_MAIN_SERVER_PORT",
        "MAIN_SERVER_PORT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    assert server._resolve_main_server_active_character_url() == (
        f"http://127.0.0.1:{expected_port}/card-forge/active-character"
    )


def test_forge_main_active_character_url_uses_electron_port_config(
    monkeypatch, tmp_path
):
    server = importlib.import_module("local_server.card_forge_server.server")
    port_config = tmp_path / "port_config.json"
    port_config.write_text('{"MAIN_SERVER_PORT": 43103}', encoding="utf-8")
    monkeypatch.setattr(server, "_main_server_port_config_path", lambda: port_config)
    for key in (
        "NEKO_MAIN_ACTIVE_CHARACTER_URL",
        "NEKO_MAIN_SERVER_PORT",
        "MAIN_SERVER_PORT",
    ):
        monkeypatch.delenv(key, raising=False)

    assert server._resolve_main_server_active_character_url() == (
        "http://127.0.0.1:43103/card-forge/active-character"
    )


def test_forge_main_active_character_url_allows_explicit_override(monkeypatch):
    server = importlib.import_module("local_server.card_forge_server.server")
    monkeypatch.setenv(
        "NEKO_MAIN_ACTIVE_CHARACTER_URL",
        "http://localhost:43103/custom-active-character",
    )
    monkeypatch.setenv("NEKO_MAIN_SERVER_PORT", "43101")

    assert server._resolve_main_server_active_character_url() == (
        "http://localhost:43103/custom-active-character"
    )


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


def test_card_face_lookup_rejects_path_traversal(tmp_path, monkeypatch):
    server = importlib.import_module("local_server.card_forge_server.server")
    card_faces = tmp_path / "card_faces"
    card_faces.mkdir()
    (card_faces / "Lanlan.png").write_bytes(b"valid")
    (tmp_path / "secret.png").write_bytes(b"secret")
    monkeypatch.setattr(server, "_card_face_dirs", lambda: [card_faces])

    assert server._find_card_face_path("Lanlan") == card_faces / "Lanlan.png"
    assert server._find_card_face_path("../secret") is None


def test_forge_server_uses_config_manager_app_data_paths(tmp_path, monkeypatch):
    server = importlib.import_module("local_server.card_forge_server.server")
    import utils.config_manager as config_manager

    config_dir = tmp_path / "platform-app-data" / "config"
    card_faces_dir = tmp_path / "platform-app-data" / "card_faces"
    config_dir.mkdir(parents=True)
    card_faces_dir.mkdir(parents=True)
    (config_dir / "characters.json").write_text(
        json.dumps({"当前猫娘": "Lanlan", "主人": {"档案名": "Master"}}),
        encoding="utf-8",
    )
    expected_face = card_faces_dir / "Lanlan.png"
    expected_face.write_bytes(b"valid")

    class FakeConfigManager:
        pass

    manager = FakeConfigManager()
    manager.config_dir = config_dir
    manager.card_faces_dir = card_faces_dir
    monkeypatch.delenv("NEKO_USER_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: manager)

    assert server._read_active_character_config_snapshot() == {
        "name": "Lanlan",
        "master_name": "Master",
    }
    assert card_faces_dir in server._card_face_dirs()
    assert server._find_card_face_path("Lanlan") == expected_face


def test_forge_active_character_endpoint_fails_closed_without_runtime_binding(monkeypatch):
    server = importlib.import_module("local_server.card_forge_server.server")

    monkeypatch.setattr(
        server,
        "_read_active_character_config_snapshot",
        lambda: {"name": "Lanlan", "master_name": "Master"},
    )
    monkeypatch.setattr(
        server,
        "_read_main_server_active_character_snapshot",
        lambda include_avatar=False: {},
    )

    with TestClient(server.app) as test_client:
        response = test_client.get("/forge/active-character")

    assert response.status_code == 200
    assert response.json()["name"] == ""
    assert response.json()["master_name"] == ""


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
def client(monkeypatch):
    monkeypatch.setenv("NEKO_SOCIAL_BASE_URL", "https://community.example")
    C._native_sync_tickets.clear()
    app = FastAPI()
    app.include_router(C.router)
    with TestClient(app, base_url="http://localhost:48911") as test_client:
        yield test_client
    C._native_sync_tickets.clear()


def _issue_sync_ticket(client: TestClient) -> str:
    response = client.get("/api/card-drop/sync-ticket")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    ticket = response.json()["sync_ticket"]
    assert len(ticket) >= 32
    return ticket


def test_local_credit_summary_is_same_origin_only_and_omits_credit_details(
    client, monkeypatch,
):
    from main_logic import forge_credit_ledger

    monkeypatch.setattr(
        forge_credit_ledger,
        "list_credits",
        lambda: {
            "count": 2,
            "credits": [
                {"id": "secret-a", "rarity": "SSR", "expires_at": "2026-07-15T00:00:00Z"},
                {"id": "secret-b", "rarity": "N", "expires_at": "2026-07-16T00:00:00Z"},
            ],
            "reservations": [{"operation_id": "secret-operation"}],
        },
    )

    allowed = client.get(
        "/api/card-drop/credits/local-summary",
        headers={"Origin": "http://localhost:48911"},
    )
    assert allowed.status_code == 200
    assert allowed.json() == {
        "count": 2,
        "next_expires_at": "2026-07-15T00:00:00Z",
    }
    assert allowed.headers["cache-control"] == "no-store"

    denied = client.get(
        "/api/card-drop/credits/local-summary",
        headers={"Origin": "https://community.example"},
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "origin_not_allowed"}


def test_credit_auth_failures_keep_validated_cors_headers(client, monkeypatch):
    async def auth_state(request):
        token = C._request_bearer_token(request)
        return "unavailable" if token == "unavailable-token" else "mismatch"

    monkeypatch.setattr(C, "_facts_request_auth_state", auth_state)

    mismatch = client.get(
        "/api/card-drop/credits",
        headers={
            "Origin": "https://community.example",
            "Authorization": "Bearer another-user-token",
        },
    )
    assert mismatch.status_code == 401
    assert mismatch.json() == {"detail": "local_session_mismatch"}
    assert mismatch.headers["access-control-allow-origin"] == "https://community.example"

    unavailable = client.get(
        "/api/card-drop/credits",
        headers={
            "Origin": "https://community.example",
            "Authorization": "Bearer unavailable-token",
        },
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "identity_verification_unavailable"}
    assert unavailable.headers["access-control-allow-origin"] == "https://community.example"

    denied = client.get(
        "/api/card-drop/credits",
        headers={
            "Origin": "https://attacker.example",
            "Authorization": "Bearer another-user-token",
        },
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "origin_not_allowed"}
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
    same_origin = client.get(
        "/api/card-drop/sync-ticket",
        headers={
            "Origin": "http://localhost:48911",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    assert evil_origin.status_code == 403
    assert blind_browser_get.status_code == 403
    assert same_origin.status_code == 200
    assert len(C._native_sync_tickets) == len(before) + 1


def test_bind_client_approval_uses_persisted_local_id_and_consumes_ticket(
    client, monkeypatch,
):
    actual_client_id = "00112233445566778899aabbccddeeff"
    actual_client_proof = "p" * 43
    challenge = "C" * 43
    sent: list[tuple[str, dict]] = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            sent.append((url, kwargs["json"]))
            return _CloudResponse(204, {})

    monkeypatch.setattr(
        C,
        "_get_client_credentials",
        lambda: (actual_client_id, actual_client_proof),
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
    assert replay.status_code == 403
    assert replay.json() == {"detail": "invalid_sync_ticket"}


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
    assert ok.headers["access-control-allow-methods"] == "GET, OPTIONS"
    assert ok.headers["access-control-allow-headers"] == "authorization, content-type"
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
