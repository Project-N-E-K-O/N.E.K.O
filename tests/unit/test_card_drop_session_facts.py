from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main_routers.card_drop_router as C
import main_logic.card_forge_facts as F
from main_logic.card_forge_facts import ActiveNekoContext, build_forge_facts_payload

pytestmark = pytest.mark.unit


def test_card_drop_client_id_persists_fresh_default_before_returning(
    tmp_path, monkeypatch,
):
    import utils.config_manager as config_manager

    saved: list[dict] = []

    class FakeConfigManager:
        cloudsave_local_state_path = tmp_path / "state" / "cloudsave_local_state.json"

        def load_cloudsave_local_state(self) -> dict:
            return {"client_id": "fresh-client-id"}

        def build_default_cloudsave_local_state(self) -> dict:
            raise AssertionError("loaded default already contains a client_id")

        def save_cloudsave_local_state(self, state: dict) -> None:
            saved.append(dict(state))

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: FakeConfigManager())

    assert C._get_client_id() == "fresh-client-id"
    assert saved == [{"client_id": "fresh-client-id"}]


def test_card_drop_client_id_fails_closed_when_fresh_default_cannot_be_saved(
    tmp_path, monkeypatch,
):
    import utils.config_manager as config_manager

    class FakeConfigManager:
        cloudsave_local_state_path = tmp_path / "state" / "cloudsave_local_state.json"

        def load_cloudsave_local_state(self) -> dict:
            return {"client_id": "volatile-client-id"}

        def build_default_cloudsave_local_state(self) -> dict:
            raise AssertionError("loaded default already contains a client_id")

        def save_cloudsave_local_state(self, _state: dict) -> None:
            raise OSError("disk unavailable")

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: FakeConfigManager())

    assert C._get_client_id() is None


def test_packaged_facts_modules_use_package_qualified_imports():
    shared = importlib.import_module("main_logic.card_forge_facts")
    server = importlib.import_module("local_server.card_forge_server.server")

    assert callable(server.build_forge_facts_payload)
    assert server.build_forge_facts_payload is shared.build_forge_facts_payload
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

    context = F._build_context(
        manager,
        character_override="Other",
        runtime_character_hint="Other",
    )

    assert context.lanlan_name == "Active"
    assert context.facts_path == tmp_path / "Active" / "facts.json"
    assert context.lanlan_prompt == "active prompt"
    assert context.source == "neko-config"


def test_facts_context_fails_closed_without_valid_active_character(tmp_path, monkeypatch):
    monkeypatch.setenv("NEKO_FACTS_JSON", str(tmp_path / "debug-facts.json"))
    manager = _FakeConfigManager(tmp_path, "../Other", {"Other": "other prompt"})

    context = F._build_context(manager, runtime_character_hint="Other")

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
        runtime_character_hint="Lanlan",
        min_importance=0,
        limit=5,
    )

    assert [fact["id"] for fact in payload["facts"]] == ["public"]
    assert payload["excludedCount"] == 2


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


def test_sync_ticket_is_short_lived_and_single_use(client):
    ticket = _issue_sync_ticket(client)

    assert C._sync_ticket_is_valid(ticket)
    assert C._consume_sync_ticket(ticket)
    assert not C._sync_ticket_is_valid(ticket)
    assert not C._consume_sync_ticket(ticket)


def test_social_session_prefers_electron_user_data_and_clear_removes_legacy(tmp_path, monkeypatch):
    legacy_auth = tmp_path / "documents" / "N.E.K.O" / "community_auth.json"
    electron_root = tmp_path / "electron-user-data"
    monkeypatch.setattr(C, "_auth_path", lambda: legacy_auth)
    monkeypatch.setenv("NEKO_USER_DATA_DIR", str(electron_root))

    assert C._save_auth({"access_token": "token-a"})
    assert C._save_social_session("https://community.example", "token-a", "refresh-a")

    electron_session = electron_root / "social_session.json"
    assert json.loads(electron_session.read_text(encoding="utf-8")) == {
        "baseUrl": "https://community.example",
        "token": "token-a",
        "refresh_token": "refresh-a",
    }
    legacy_session = legacy_auth.parent / "social_session.json"
    legacy_session.write_text("{}", encoding="utf-8")

    assert C._clear_auth()

    assert not legacy_auth.exists()
    assert not electron_session.exists()
    assert not legacy_session.exists()


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
                {"user": {"display_name": "New User", "email": "new@example.com"}},
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
            {"display_name": "New User", "email": "new@example.com"},
        )

    assert exc_info.value.detail == "client_already_bound_to_other_user"
    assert writes == []  # Electron cannot observe even a transient wrong social_session token.
    assert json.loads(auth.read_text(encoding="utf-8")) == old_auth
    assert json.loads(session.read_text(encoding="utf-8")) == old_session


def test_sync_session_bind_ownership_conflict_returns_409_and_preserves_session(
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

    assert response.status_code == 409
    assert response.json() == {"detail": "client_already_bound_to_other_user"}
    assert response.headers["access-control-allow-origin"] == "https://community.example"
    assert json.loads(auth.read_text(encoding="utf-8")) == old_auth
    assert json.loads(session.read_text(encoding="utf-8")) == old_session


def test_local_login_bind_ownership_conflict_returns_409_and_preserves_session(
    client, tmp_path, monkeypatch,
):
    auth, session, old_auth, old_session = _existing_session_files(tmp_path, monkeypatch)
    login_out = {
        "tokens": {"access_token": "wrong-new-token", "refresh_token": "wrong-new-refresh"},
        "user": {"display_name": "New User", "email": "new@example.com"},
    }
    _cloud_client(monkeypatch, login=login_out)

    response = client.post(
        "/api/card-drop/login",
        json={"email": "new@example.com", "password": "password123"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "client_already_bound_to_other_user"}
    assert json.loads(auth.read_text(encoding="utf-8")) == old_auth
    assert json.loads(session.read_text(encoding="utf-8")) == old_session


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
        {"display_name": "New User", "email": "new@example.com"},
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
        {"display_name": "New User", "email": "new@example.com"},
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


def test_facts_requires_trusted_origin_and_matching_local_session(client, monkeypatch):
    monkeypatch.setattr(C, "_access_token", lambda: "local-token")

    async def fake_build(**kwargs):
        return {"character": kwargs["runtime_character_hint"], "facts": [{"id": "f1"}]}

    monkeypatch.setattr(C, "_build_local_forge_facts", fake_build)

    no_origin = client.get(
        "/api/card-drop/facts?runtime_character_hint=Lanlan",
        headers={"Authorization": "Bearer local-token"},
    )
    assert no_origin.status_code == 403

    wrong_token = client.get(
        "/api/card-drop/facts?runtime_character_hint=Lanlan",
        headers={"Origin": "https://community.example", "Authorization": "Bearer wrong"},
    )
    assert wrong_token.status_code == 401

    ok = client.get(
        "/api/card-drop/facts?runtime_character_hint=Lanlan&limit=5&min_importance=0",
        headers={"Origin": "https://community.example", "Authorization": "Bearer local-token"},
    )
    assert ok.status_code == 200
    assert ok.json() == {"character": "Lanlan", "facts": [{"id": "f1"}]}
    assert ok.headers["access-control-allow-origin"] == "https://community.example"


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
