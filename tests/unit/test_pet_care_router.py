"""Stage 2 local PetCare binding and snapshot contract tests."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main_routers.card_drop_router as card_drop
import main_routers.community_oauth as community_oauth
import main_routers.pet_care_router as pet_care
from services.pet_care import BindingServiceError, LocalPetCareBindingService


ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
UID_A = "a" * 32
UID_B = "b" * 32


class _ConfigManager:
    def __init__(self, root: Path):
        self.local_state_dir = root / "state"

    def ensure_local_state_directory(self):
        self.local_state_dir.mkdir(parents=True, exist_ok=True)
        return True

    def load_characters(self, *, require_authoritative=False):
        return {
            "猫娘": {
                "A": {"_reserved": {"character_uid": UID_A}},
                "B": {"_reserved": {"character_uid": UID_B}},
            }
        }


@pytest.fixture
def pet_app(tmp_path, monkeypatch):
    config_manager = _ConfigManager(tmp_path)
    monkeypatch.setattr(pet_care, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(card_drop, "_local_request_source_allowed", lambda _request: True)
    monkeypatch.setattr(
        community_oauth,
        "_account_request_identity",
        lambda _request: _async_value("local"),
    )

    async def logged_in():
        return {
            "logged_in": True,
            "snapshot": {"local_user_id": ACCOUNT_ID},
            "auth": {},
        }

    monkeypatch.setattr(community_oauth, "resolve_saved_oauth_status", logged_in)
    app = FastAPI()
    app.include_router(pet_care.router)
    return TestClient(app), config_manager


async def _async_value(value):
    return value


@pytest.mark.unit
def test_first_context_creates_binding_and_bind_is_idempotent(pet_app):
    client, config_manager = pet_app
    first = client.get("/api/pet-care/context", params={"character_uid": UID_A})
    second = client.get("/api/pet-care/context", params={"character_uid": UID_A})

    assert first.status_code == second.status_code == 200
    first_body, second_body = first.json(), second.json()
    assert first_body["binding_status"] == second_body["binding_status"] == "bound"
    assert first_body["pet_id"] == second_body["pet_id"]
    assert first_body["bound_role_id"] == second_body["bound_role_id"]
    assert first_body["created"] is True
    assert second_body["created"] is False
    bound = client.post("/api/pet-care/bind", json={"character_uid": UID_A}).json()
    repeated = client.post("/api/pet-care/bind", json={"character_uid": UID_A}).json()
    assert bound["binding_status"] == repeated["binding_status"] == "bound"
    assert bound["pet_id"] == repeated["pet_id"]
    assert bound["bound_role_id"] == repeated["bound_role_id"]
    assert (config_manager.local_state_dir / "pet_care" / "pet_care.sqlite3").exists()


@pytest.mark.unit
def test_other_role_is_unbound_until_explicit_bind_and_reuses_pet(pet_app):
    client, _config_manager = pet_app
    initial = client.get("/api/pet-care/context", params={"character_uid": UID_A}).json()
    other = client.get("/api/pet-care/context", params={"character_uid": UID_B}).json()
    rebound = client.post("/api/pet-care/bind", json={"character_uid": UID_B})
    old_role = client.get("/api/pet-care/context", params={"character_uid": UID_A}).json()

    assert other["binding_status"] == "unbound"
    assert other["pet_id"] is None
    assert rebound.status_code == 200
    rebound_body = rebound.json()
    assert rebound_body["binding_status"] == "bound"
    assert rebound_body["pet_id"] == initial["pet_id"]
    assert rebound_body["bound_role_id"] != initial["bound_role_id"]
    assert old_role["binding_status"] == "unbound"


@pytest.mark.unit
def test_context_does_not_accept_unverified_or_unknown_role(pet_app, monkeypatch):
    client, _config_manager = pet_app
    unknown = client.get("/api/pet-care/context", params={"character_uid": "c" * 32})
    invalid = client.get("/api/pet-care/context", params={"character_uid": "bad"})
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "character_not_found"
    assert invalid.status_code == 422

    async def logged_out():
        return {"logged_in": False, "snapshot": {}, "auth": {}}

    monkeypatch.setattr(community_oauth, "resolve_saved_oauth_status", logged_out)
    logged_out_response = client.get("/api/pet-care/context", params={"character_uid": UID_A})
    assert logged_out_response.status_code == 401
    assert logged_out_response.json()["detail"] == "login_required"


@pytest.mark.unit
def test_stage2_pet_endpoint_returns_complete_server_snapshot(pet_app):
    client, _config_manager = pet_app
    response = client.get(f"/api/pet-care/pets/{UID_A}")

    assert response.status_code == 200
    body = response.json()
    snapshot = body["snapshot"]
    assert snapshot["schema_version"] == 1
    assert snapshot["last_settled_at"]
    assert body["binding_status"] == "bound"
    assert snapshot["pet_id"] == body["pet_id"]
    assert snapshot["bound_role_id"] == body["bound_role_id"]
    assert snapshot["status_values"] == {
        "satiety": 75,
        "energy": 80,
        "cleanliness": 80,
        "mood": 64,
        "progress": 0,
    }
    assert set(snapshot["negative_status"]) == {"sick", "sulk"}
    assert snapshot["inventory"] == {}
    assert snapshot["tasks"] == {"daily": [], "state": []}
    assert snapshot["unlocks"] == []
    assert snapshot["exploration"] is None

    repeated = client.get(f"/api/pet-care/pets/{UID_A}").json()
    assert repeated["snapshot"] == snapshot


@pytest.mark.unit
def test_non_bound_role_returns_no_snapshot_from_pet_endpoint(pet_app):
    client, _config_manager = pet_app
    client.get(f"/api/pet-care/pets/{UID_A}")
    response = client.get(f"/api/pet-care/pets/{UID_B}")

    assert response.status_code == 200
    assert response.json()["binding_status"] == "unbound"
    assert response.json()["pet_id"] is None
    assert response.json()["snapshot"] is None


@pytest.mark.unit
def test_old_database_migration_preserves_binding_and_creates_snapshot(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    db_path = config_manager.local_state_dir / "pet_care" / "pet_care.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    pet_id = "legacy-pet"
    role_id = "legacy-role"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE pet_care_accounts (
                account_id TEXT PRIMARY KEY,
                pet_id TEXT NOT NULL UNIQUE,
                bound_role_id TEXT NOT NULL UNIQUE,
                bound_character_uid TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        )
        connection.execute(
            "INSERT INTO pet_care_accounts VALUES (?, ?, ?, ?, ?, ?)",
            (ACCOUNT_ID, pet_id, role_id, UID_A, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )

    service = LocalPetCareBindingService(config_manager)
    result = service.resolve(ACCOUNT_ID, UID_A)
    assert result.pet_id == pet_id
    assert result.bound_role_id == role_id
    assert result.snapshot is not None
    assert result.snapshot["pet_id"] == pet_id
    assert result.snapshot["bound_role_id"] == role_id

    with sqlite3.connect(db_path) as connection:
        account_count = connection.execute("SELECT COUNT(*) FROM pet_care_accounts").fetchone()[0]
        snapshot_count = connection.execute("SELECT COUNT(*) FROM pet_care_snapshots").fetchone()[0]
        operation_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'pet_care_operation_results'"
        ).fetchone()
    assert account_count == snapshot_count == 1
    assert operation_table is not None


@pytest.mark.unit
def test_snapshot_initial_values_are_configuration_driven(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "initial_status_values": {
                "satiety": 11,
                "energy": 22,
                "cleanliness": 33,
                "mood": 44,
                "progress": 5,
            }
        },
    )
    result = service.resolve(ACCOUNT_ID, UID_A)
    assert result.snapshot["status_values"] == {
        "satiety": 11,
        "energy": 22,
        "cleanliness": 33,
        "mood": 44,
        "progress": 5,
    }


@pytest.mark.unit
def test_snapshot_uses_config_manager_pet_care_json_when_available(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    calls = []

    def load_json_config(filename, default_value=None):
        calls.append((filename, default_value))
        return {"initial_status_values": {
            "satiety": 1, "energy": 2, "cleanliness": 3, "mood": 4, "progress": 5,
        }}

    config_manager.load_json_config = load_json_config
    service = LocalPetCareBindingService(config_manager)
    result = service.resolve(ACCOUNT_ID, UID_A)

    assert calls == [("pet_care.json", {})]
    assert result.snapshot["status_values"] == {
        "satiety": 1, "energy": 2, "cleanliness": 3, "mood": 4, "progress": 5,
    }


@pytest.mark.unit
def test_invalid_snapshot_config_fails_closed(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    with pytest.raises(BindingServiceError) as error:
        LocalPetCareBindingService(
            config_manager,
            snapshot_config={"initial_status_values": {"satiety": 101}},
        )
    assert getattr(error.value, "code", None) == "pet_care_config_invalid"


@pytest.mark.unit
def test_two_accounts_do_not_share_a_pet_for_the_same_local_role(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(config_manager)
    first = service.resolve(ACCOUNT_ID, UID_A)
    second = service.resolve("22222222-2222-4222-8222-222222222222", UID_A)
    assert first.pet_id != second.pet_id
    assert first.bound_role_id != second.bound_role_id
