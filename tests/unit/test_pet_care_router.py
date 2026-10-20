"""Stage 2 local PetCare binding and snapshot contract tests."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from datetime import datetime, timedelta, timezone

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
def test_rebind_is_rejected_while_existing_pet_is_exploring(pet_app):
    client, config_manager = pet_app
    initial = client.get("/api/pet-care/context", params={"character_uid": UID_A}).json()
    service = LocalPetCareBindingService(config_manager)
    started = service.start_exploration(
        ACCOUNT_ID,
        UID_A,
        location_id="window-path",
        idempotency_key="bind-block-start-1",
    )

    response = client.post("/api/pet-care/bind", json={"character_uid": UID_B})

    assert response.status_code == 409
    assert response.json()["code"] == "binding_blocked_by_active_exploration"
    assert response.json()["detail"] == "binding_blocked_by_active_exploration"
    # The failed rebind is atomic: the same role, pet and active activity
    # remain authoritative and can be recalled before retrying the switch.
    still_bound = client.get("/api/pet-care/context", params={"character_uid": UID_A}).json()
    other_role = client.get("/api/pet-care/context", params={"character_uid": UID_B}).json()
    assert still_bound["binding_status"] == "bound"
    assert still_bound["pet_id"] == initial["pet_id"]
    assert still_bound["snapshot"]["exploration"]["exploration_id"] == started["exploration"]["exploration_id"]
    assert still_bound["snapshot"]["exploration"]["active"] is True
    assert other_role["binding_status"] == "unbound"


@pytest.mark.unit
def test_rebind_succeeds_after_exploration_is_service_confirmed_recalled(pet_app):
    client, config_manager = pet_app
    client.get("/api/pet-care/context", params={"character_uid": UID_A})
    service = LocalPetCareBindingService(config_manager)
    started = service.start_exploration(
        ACCOUNT_ID,
        UID_A,
        location_id="window-path",
        idempotency_key="bind-recall-start-1",
    )
    exploration_id = started["exploration"]["exploration_id"]
    service.recall_exploration(
        ACCOUNT_ID,
        UID_A,
        exploration_id=exploration_id,
        idempotency_key="bind-recall-1",
    )

    response = client.post("/api/pet-care/bind", json={"character_uid": UID_B})

    assert response.status_code == 200
    body = response.json()
    assert body["binding_status"] == "bound"
    assert body["snapshot"]["exploration"]["status"] == "recalled"


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
        "mood": 67,
        "progress": 0,
    }
    assert set(snapshot["negative_status"]) == {"sick", "sulk"}
    assert snapshot["inventory"] == {}
    assert snapshot["tasks"] == {"daily": [], "state": []}
    assert snapshot["unlocks"] == []
    assert snapshot["exploration"] is None

    repeated = client.get(f"/api/pet-care/pets/{UID_A}").json()
    repeated_snapshot = repeated["snapshot"]
    # Reads settle the server cursor.  A repeated request may cross a clock
    # second, so compare the durable business state and require monotonic time
    # instead of requiring the timestamp string to be byte-identical.
    assert repeated_snapshot["last_settled_at"] >= snapshot["last_settled_at"]
    for key in ("schema_version", "pet_id", "account_id", "bound_role_id", "status_values",
                "negative_status", "inventory", "tasks", "unlocks", "exploration"):
        assert repeated_snapshot[key] == snapshot[key]


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
        snapshot_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(pet_care_snapshots)").fetchall()
        }
        operation_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'pet_care_operation_results'"
        ).fetchone()
    assert account_count == snapshot_count == 1
    assert "cleaning_cooldowns_json" in snapshot_columns
    assert "activity_counters_json" in snapshot_columns
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
        "mood": 27,
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
        "satiety": 1, "energy": 2, "cleanliness": 3, "mood": 13, "progress": 5,
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
def test_invalid_inventory_config_fails_closed(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    with pytest.raises(BindingServiceError) as error:
        LocalPetCareBindingService(
            config_manager,
            snapshot_config={"initial_inventory": {"small_fish": 1.5}},
        )
    assert error.value.code == "pet_care_config_invalid"


@pytest.mark.unit
def test_stage4_small_fish_config_cannot_add_progress(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    with pytest.raises(BindingServiceError) as error:
        LocalPetCareBindingService(
            config_manager,
            snapshot_config={
                "care_actions": {"feeding": {"small_fish": {"progress_delta": 1}}},
            },
        )
    assert error.value.code == "pet_care_config_invalid"


@pytest.mark.unit
def test_stage4_additional_food_operation_is_service_configured(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "initial_inventory": {"tuna": 1},
            "care_actions": {
                "feeding": {
                    "tuna": {
                        "label": "金枪鱼",
                        "inventory_item": "tuna",
                        "satiety_delta": 10,
                        "progress_delta": 0,
                    },
                },
            },
        },
    )
    initial = service.resolve(ACCOUNT_ID, UID_A)
    result = service.care_action(
        ACCOUNT_ID,
        UID_A,
        category="feeding",
        content_id="tuna",
        idempotency_key="feed-tuna-1",
    )

    assert initial.care_actions["feeding"]["tuna"]["label"] == "金枪鱼"
    assert result["ok"] is True
    assert result["snapshot"]["inventory"]["tuna"] == 0
    assert result["snapshot"]["status_values"]["satiety"] == 85


@pytest.mark.unit
def test_two_accounts_do_not_share_a_pet_for_the_same_local_role(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(config_manager)
    first = service.resolve(ACCOUNT_ID, UID_A)
    second = service.resolve("22222222-2222-4222-8222-222222222222", UID_A)
    assert first.pet_id != second.pet_id
    assert first.bound_role_id != second.bound_role_id


@pytest.mark.unit
def test_stage4_elapsed_settlement_and_small_fish_action_are_server_confirmed(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={"initial_inventory": {"small_fish": 2}},
        clock=lambda: now[0],
    )
    context = service.resolve(ACCOUNT_ID, UID_A)
    assert context.snapshot["status_values"]["satiety"] == 75
    now[0] += timedelta(hours=2)
    settled = service.resolve(ACCOUNT_ID, UID_A).snapshot
    assert settled["status_values"]["satiety"] == 73
    assert settled["status_values"]["cleanliness"] == 78
    assert settled["status_values"]["energy"] == 88

    result = service.care_action(
        ACCOUNT_ID, UID_A, category="feeding", content_id="small_fish", idempotency_key="feed-1",
    )
    assert result["ok"] is True
    assert result["snapshot"]["status_values"]["satiety"] == 98
    assert result["snapshot"]["status_values"]["progress"] == 0
    assert result["snapshot"]["inventory"]["small_fish"] == 1
    repeated = service.care_action(
        ACCOUNT_ID, UID_A, category="feeding", content_id="small_fish", idempotency_key="feed-1",
    )
    assert repeated == result


@pytest.mark.unit
def test_stage4_settlement_persists_integer_status_values(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = LocalPetCareBindingService(config_manager, clock=lambda: now[0])
    service.resolve(ACCOUNT_ID, UID_A)
    now[0] += timedelta(hours=1, minutes=30)
    snapshot = service.resolve(ACCOUNT_ID, UID_A).snapshot
    assert snapshot["status_values"]["satiety"] == 74
    assert snapshot["status_values"]["cleanliness"] == 79
    assert snapshot["status_values"]["energy"] == 86
    assert all(isinstance(snapshot["status_values"][key], int) for key in
               ("satiety", "energy", "cleanliness", "mood", "progress"))


@pytest.mark.unit
def test_corrupt_snapshot_fails_closed(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(config_manager)
    context = service.resolve(ACCOUNT_ID, UID_A)
    db_path = config_manager.local_state_dir / "pet_care" / "pet_care.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE pet_care_snapshots SET status_values_json = ? WHERE pet_id = ?",
            ('{"satiety":"bad"}', context.pet_id),
        )
    with pytest.raises(BindingServiceError) as error:
        service.resolve(ACCOUNT_ID, UID_A)
    assert error.value.code == "pet_care_snapshot_invalid"


@pytest.mark.unit
def test_stage4_brushing_cooldown_is_persisted_and_rejects_duplicate_effect(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={"initial_status_values": {"cleanliness": 50}},
        clock=lambda: now[0],
    )
    service.resolve(ACCOUNT_ID, UID_A)
    first = service.care_action(
        ACCOUNT_ID, UID_A, category="cleaning", content_id="brushing", idempotency_key="brush-1",
    )
    assert first["ok"] is True
    assert first["snapshot"]["status_values"]["cleanliness"] == 80
    assert first["snapshot"]["tasks"] == {"daily": [], "state": []}
    assert first["snapshot"]["cleaning_cooldowns"]["brushing"] == "2026-01-01T00:00:00+00:00"
    now[0] += timedelta(minutes=1)
    service_again = LocalPetCareBindingService(config_manager, clock=lambda: now[0])
    rejected = service_again.care_action(
        ACCOUNT_ID, UID_A, category="cleaning", content_id="brushing", idempotency_key="brush-2",
    )
    assert rejected["ok"] is False
    assert rejected["code"] == "cleaning_cooldown"


@pytest.mark.unit
def test_stage4_action_rejects_activity_and_idempotency_hash_reuse(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={"initial_inventory": {"small_fish": 1}},
    )
    context = service.resolve(ACCOUNT_ID, UID_A)
    db_path = config_manager.local_state_dir / "pet_care" / "pet_care.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE pet_care_snapshots SET exploration_json = ? WHERE pet_id = ?",
            ('{"active":true}', context.pet_id),
        )
    with pytest.raises(BindingServiceError) as activity_error:
        service.care_action(
            ACCOUNT_ID, UID_A, category="feeding", content_id="small_fish", idempotency_key="activity-1",
        )
    assert activity_error.value.code == "activity_in_progress"

    # A key is bound to its request hash; reusing it for another operation is
    # rejected rather than silently applying a second business action.
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE pet_care_snapshots SET exploration_json = NULL WHERE pet_id = ?",
            (context.pet_id,),
        )
    service.care_action(
        ACCOUNT_ID, UID_A, category="feeding", content_id="small_fish", idempotency_key="same-key",
    )
    with pytest.raises(BindingServiceError) as hash_error:
        service.care_action(
            ACCOUNT_ID, UID_A, category="cleaning", content_id="brushing", idempotency_key="same-key",
        )
    assert hash_error.value.code == "idempotency_key_reused"


@pytest.mark.unit
def test_dev_fill_status_changes_only_three_basic_values_and_is_idempotent(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "initial_status_values": {
                "satiety": 11, "energy": 22, "cleanliness": 33,
                "mood": 27, "progress": 55,
            },
            "initial_inventory": {"small_fish": 2},
        },
    )
    service.resolve(ACCOUNT_ID, UID_A)

    result = service.debug_fill_status(ACCOUNT_ID, UID_A, idempotency_key="dev-fill-1")
    assert result["ok"] is True
    assert result["snapshot"]["status_values"] == {
        "satiety": 100, "energy": 100, "cleanliness": 100,
        "mood": 27, "progress": 55,
    }
    assert result["snapshot"]["inventory"] == {"small_fish": 2}
    assert result["snapshot"]["tasks"] == {"daily": [], "state": []}
    assert result["operation"]["kind"] == "debug_fill_status"

    repeated = service.debug_fill_status(ACCOUNT_ID, UID_A, idempotency_key="dev-fill-1")
    assert repeated == result


@pytest.mark.unit
def test_dev_fill_status_route_is_opt_in_and_returns_server_snapshot(pet_app, monkeypatch):
    client, config_manager = pet_app
    monkeypatch.setenv("NEKO_PET_CARE_DEV_TOOLS", "1")
    config_manager.pet_care_snapshot_config = {
        "initial_status_values": {
            "satiety": 1, "energy": 2, "cleanliness": 3, "mood": 4, "progress": 5,
        },
    }
    context = client.get("/api/pet-care/context", params={"character_uid": UID_A})
    assert context.status_code == 200
    assert context.json()["dev_tools"] == {"fill_status": True}

    response = client.post(
        f"/api/pet-care/pets/{UID_A}/debug/fill-status",
        json={"idempotency_key": "route-dev-fill-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dev_tools"] == {"fill_status": True}
    assert body["snapshot"]["status_values"] == {
        "satiety": 100, "energy": 100, "cleanliness": 100, "mood": 13, "progress": 5,
    }

    monkeypatch.delenv("NEKO_PET_CARE_DEV_TOOLS", raising=False)
    hidden = client.post(
        f"/api/pet-care/pets/{UID_A}/debug/fill-status",
        json={"idempotency_key": "route-dev-fill-2"},
    )
    assert hidden.status_code == 404
    assert hidden.json()["code"] == "dev_tools_unavailable"


@pytest.mark.unit
def test_stage4_care_action_route_returns_shared_snapshot_envelope(pet_app):
    client, config_manager = pet_app
    config_manager.pet_care_snapshot_config = {"initial_inventory": {"small_fish": 1}}
    client.get("/api/pet-care/context", params={"character_uid": UID_A})
    response = client.post(
        f"/api/pet-care/pets/{UID_A}/care-actions",
        json={
            "category": "feeding",
            "content_id": "small_fish",
            "idempotency_key": "route-feed-1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["binding_status"] == "bound"
    assert body["operation"]["status"] == "confirmed"
    assert body["snapshot"]["inventory"]["small_fish"] == 0
    assert body["settlement"]["satiety_decay_per_hour"] == 1
    assert body["result"]["snapshot"] == body["snapshot"]


@pytest.mark.unit
def test_stage5_tasks_are_manual_claims_with_one_idempotent_reward(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(config_manager)
    service.resolve(ACCOUNT_ID, UID_A)

    tasks = service.get_tasks(ACCOUNT_ID, UID_A)
    assert tasks["tasks"]["service_day"]
    assert tasks["tasks"]["items"][0]["status"] == "available"
    assert tasks["care_actions"]["feeding"]["small_fish"]["label"] == "小鱼干"
    task_id = tasks["tasks"]["items"][0]["id"]

    first = service.claim_task(ACCOUNT_ID, UID_A, task_id=task_id, idempotency_key="task-1")
    repeated = service.claim_task(ACCOUNT_ID, UID_A, task_id=task_id, idempotency_key="task-1")
    second_key = service.claim_task(ACCOUNT_ID, UID_A, task_id=task_id, idempotency_key="task-2")
    assert first["ok"] is True
    assert first == repeated
    assert second_key["ok"] is False
    assert second_key["code"] == "task_already_claimed"
    assert first["reward"] == {"item_id": "small_fish", "quantity": 1}
    assert first["snapshot"]["inventory"]["small_fish"] == 1


@pytest.mark.unit
def test_task_catalog_keeps_each_configured_task_independent(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "task_catalog": [
                {
                    "task_id": "task-a", "title": "可领取任务", "group_id": "configured-a",
                    "group_label": "自定义分组", "reset_policy": "daily",
                    "reward": {"item_id": "small_fish", "quantity": 1},
                },
                {
                    "task_id": "task-b", "title": "持续进度", "group_id": "configured-b",
                    "reset_policy": "persistent", "claimable": False,
                    "progress": 1, "target": 3, "reward": {},
                },
            ],
        },
    )
    service.resolve(ACCOUNT_ID, UID_A)
    tasks = service.get_tasks(ACCOUNT_ID, UID_A)["tasks"]["items"]
    assert [item["task_id"] for item in tasks] == ["task-a", "task-b"]
    assert tasks[0]["group_label"] == "自定义分组"
    assert tasks[0]["reset_policy"] == "daily"
    assert "progress" not in tasks[0]
    assert "target" not in tasks[0]
    assert tasks[1]["reset_policy"] == "persistent"
    first = service.claim_task(ACCOUNT_ID, UID_A, task_id="task-a", idempotency_key="catalog-a")
    assert first["ok"] is True
    assert first["snapshot"]["tasks"]["items"][1]["status"] == "available"
    with pytest.raises(BindingServiceError, match="task_unavailable"):
        service.claim_task(ACCOUNT_ID, UID_A, task_id="task-b", idempotency_key="catalog-b")


@pytest.mark.unit
def test_progress_task_cannot_be_claimed_before_target(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "task_catalog": [{
                "id": "progress-task", "title": "进度任务", "claimable": True,
                "progress": 1, "target": 3,
                "reward": {"item_id": "small_fish", "quantity": 1},
            }],
        },
    )
    service.resolve(ACCOUNT_ID, UID_A)
    with pytest.raises(BindingServiceError, match="task_not_complete"):
        service.claim_task(ACCOUNT_ID, UID_A, task_id="progress-task", idempotency_key="progress-task-1")


@pytest.mark.unit
def test_stage5_tasks_reset_at_the_next_service_day_without_repeating_same_day_reward(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    now = [datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)]
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={"service_day": {"timezone": "UTC", "start_hour": 0}},
        clock=lambda: now[0],
    )
    service.resolve(ACCOUNT_ID, UID_A)
    first_day = service.get_tasks(ACCOUNT_ID, UID_A)
    task_id = first_day["tasks"]["items"][0]["id"]
    claimed = service.claim_task(ACCOUNT_ID, UID_A, task_id=task_id, idempotency_key="day-1-task")
    assert claimed["snapshot"]["tasks"]["items"][0]["status"] == "claimed"

    now[0] += timedelta(minutes=2)
    second_day = service.get_tasks(ACCOUNT_ID, UID_A)
    assert second_day["tasks"]["service_day"] != first_day["tasks"]["service_day"]
    assert second_day["tasks"]["items"][0]["status"] == "available"
    assert second_day["snapshot"]["inventory"]["small_fish"] == 1


@pytest.mark.unit
def test_non_daily_task_keeps_state_across_service_day_until_period_rules_exist(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    now = [datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)]
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "service_day": {"timezone": "UTC", "start_hour": 0},
            "task_catalog": [{
                "id": "persistent-progress", "title": "持续进度", "claimable": False,
                "reset_policy": "weekly", "progress": 2, "target": 5,
                "reward": {},
            }],
        },
        clock=lambda: now[0],
    )
    service.resolve(ACCOUNT_ID, UID_A)
    first = service.get_tasks(ACCOUNT_ID, UID_A)
    assert first["tasks"]["items"][0]["progress"] == 2
    now[0] += timedelta(minutes=2)
    second = service.get_tasks(ACCOUNT_ID, UID_A)
    assert second["tasks"]["items"][0]["progress"] == 2
    assert second["tasks"]["items"][0]["reset_policy"] == "weekly"


@pytest.mark.unit
def test_task_catalog_addition_is_reconciled_without_waiting_for_next_service_day(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "task_catalog": [{
                "id": "daily-care-placeholder", "title": "领取小鱼干",
                "reward": {"item_id": "small_fish", "quantity": 1},
            }],
        },
    )
    service.resolve(ACCOUNT_ID, UID_A)
    first = service.get_tasks(ACCOUNT_ID, UID_A)
    assert [item["task_id"] for item in first["tasks"]["items"]] == ["daily-care-placeholder"]

    service.snapshot_config["task_catalog"].append({
        "id": "daily-yarn-play", "title": "毛线球", "claimable": False,
        "progress_source": {"category": "playing", "content_id": "yarn"},
        "reset_policy": "daily", "reward": {},
    })
    second = service.get_tasks(ACCOUNT_ID, UID_A)
    assert [item["task_id"] for item in second["tasks"]["items"]] == [
        "daily-care-placeholder", "daily-yarn-play",
    ]
    yarn = second["tasks"]["items"][1]
    assert yarn["claimable"] is False
    assert yarn["progress"] == 0


@pytest.mark.unit
def test_stage5_idempotency_key_cannot_be_reused_across_service_days(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    now = [datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)]
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={"service_day": {"timezone": "UTC", "start_hour": 0}},
        clock=lambda: now[0],
    )
    service.resolve(ACCOUNT_ID, UID_A)
    task_id = service.get_tasks(ACCOUNT_ID, UID_A)["tasks"]["items"][0]["id"]
    service.claim_task(ACCOUNT_ID, UID_A, task_id=task_id, idempotency_key="cross-day-task")
    service.daily_check_in(ACCOUNT_ID, UID_A, idempotency_key="cross-day-login")

    now[0] += timedelta(minutes=2)
    with pytest.raises(BindingServiceError, match="idempotency_key_reused"):
        service.claim_task(ACCOUNT_ID, UID_A, task_id=task_id, idempotency_key="cross-day-task")
    with pytest.raises(BindingServiceError, match="idempotency_key_reused"):
        service.daily_check_in(ACCOUNT_ID, UID_A, idempotency_key="cross-day-login")


@pytest.mark.unit
def test_stage5_rejects_configured_task_conditions_until_event_evaluator_exists(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "daily_tasks": [{
                "id": "condition-placeholder", "title": "待实现条件任务",
                "condition": {"event": "care_confirmed"},
                "reward": {"item_id": "small_fish", "quantity": 1},
            }],
        },
    )
    service.resolve(ACCOUNT_ID, UID_A)
    task_id = service.get_tasks(ACCOUNT_ID, UID_A)["tasks"]["items"][0]["id"]
    with pytest.raises(BindingServiceError, match="task_condition_unavailable"):
        service.claim_task(ACCOUNT_ID, UID_A, task_id=task_id, idempotency_key="condition-task")


@pytest.mark.unit
def test_stage5_daily_check_in_is_once_per_service_day_and_unlocks(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    now = [datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)]
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "daily_login": {"progress_delta": 4},
            "unlock_catalog": [{"id": "unlock-a", "threshold": 4, "name": "占位", "type": "content"}],
            "service_day": {"timezone": "UTC", "start_hour": 0},
        },
        clock=lambda: now[0],
    )
    service.resolve(ACCOUNT_ID, UID_A)
    first = service.daily_check_in(ACCOUNT_ID, UID_A, idempotency_key="login-1")
    same_day = service.daily_check_in(ACCOUNT_ID, UID_A, idempotency_key="login-2")
    assert first["progress_delta"] == 4
    assert same_day["progress_delta"] == 0
    assert first["snapshot"]["status_values"]["progress"] == 4
    assert first["unlocked"][0]["id"] == "unlock-a"
    assert first["snapshot"]["unlock_checkpoint"] == 4

    now[0] += timedelta(hours=1)
    next_day = service.daily_check_in(ACCOUNT_ID, UID_A, idempotency_key="login-3")
    assert next_day["progress_delta"] == 4
    assert next_day["snapshot"]["status_values"]["progress"] == 8
    assert len(next_day["snapshot"]["unlocks"]) == 1
    assert next_day["snapshot"]["unlock_checkpoint"] == 4


@pytest.mark.unit
def test_stage5_progress_is_cumulative_and_reaches_high_unlock_threshold(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "initial_status_values": {"progress": 119},
            "daily_login": {"progress_delta": 4},
            "unlock_catalog": [{"id": "unlock-high", "threshold": 120, "name": "高阈值占位", "type": "content"}],
        },
    )
    service.resolve(ACCOUNT_ID, UID_A)
    result = service.daily_check_in(ACCOUNT_ID, UID_A, idempotency_key="high-threshold-login")
    assert result["progress_delta"] == 4
    assert result["snapshot"]["status_values"]["progress"] == 123
    assert result["unlocked"][0]["id"] == "unlock-high"


@pytest.mark.unit
def test_stage5_routes_return_confirmed_snapshot_and_task_reward(pet_app):
    client, _config_manager = pet_app
    tasks_response = client.get(f"/api/pet-care/pets/{UID_A}/tasks")
    assert tasks_response.status_code == 200
    task_id = tasks_response.json()["tasks"]["items"][0]["id"]

    claim = client.post(
        f"/api/pet-care/pets/{UID_A}/tasks/{task_id}/claim",
        json={"idempotency_key": "route-task-1"},
    )
    assert claim.status_code == 200
    body = claim.json()
    assert body["reward"] == {"item_id": "small_fish", "quantity": 1}
    assert body["snapshot"]["inventory"]["small_fish"] == 1

    check_in = client.post(
        f"/api/pet-care/pets/{UID_A}/daily-check-in",
        json={"idempotency_key": "route-login-1"},
    )
    assert check_in.status_code == 200
    assert check_in.json()["progress_delta"] == 4


@pytest.mark.unit
def test_stage6_yarn_requires_user_response_and_counts_only_completed_play(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "initial_status_values": {"energy": 80, "cleanliness": 80, "progress": 0},
            "playing": {"yarn": {"daily_progress_limit": 2, "progress_delta": 4, "energy_delta": -8, "cleanliness_delta": -2}},
        },
    )
    service.resolve(ACCOUNT_ID, UID_A)
    task_snapshot = service.get_tasks(ACCOUNT_ID, UID_A)
    task_items = task_snapshot["tasks"]["items"]
    assert [item["task_id"] for item in task_items] == ["daily-care-placeholder", "daily-yarn-play"]
    assert task_items[1]["claimable"] is False
    assert task_items[1]["progress"] == 0
    assert task_items[1]["target"] == 2
    rejected = service.care_action(
        ACCOUNT_ID, UID_A, category="playing", content_id="yarn", idempotency_key="yarn-no-response",
        source="chat-yarn", activity_id="activity-no-response", user_initiated=True,
        cat_responded=False, terminal_status="no_response",
    )
    assert rejected["ok"] is False
    assert rejected["code"] == "play_no_response"
    assert rejected["snapshot"]["status_values"]["progress"] == 0
    assert rejected["snapshot"]["activity_counters"].get("playing", {}) == {}

    for index in range(3):
        result = service.care_action(
            ACCOUNT_ID, UID_A, category="playing", content_id="yarn", idempotency_key=f"yarn-{index}",
            source="chat-yarn", activity_id=f"activity-{index}", user_initiated=True,
            cat_responded=True, terminal_status="done",
        )
        assert result["ok"] is True
        assert result["operation"]["terminal_status"] == "done"
    assert result["snapshot"]["activity_counters"]["playing"]["yarn"] == 3
    assert result["snapshot"]["tasks"]["items"][1]["progress"] == 2
    assert result["snapshot"]["status_values"]["progress"] == 8
    assert result["play"]["remaining_progress_count"] == 0
    assert result["snapshot"]["status_values"]["energy"] == 56
    assert result["snapshot"]["status_values"]["cleanliness"] == 74


@pytest.mark.unit
def test_stage6_play_confirmation_contains_task_item_before_first_task_read(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "initial_status_values": {"energy": 80, "cleanliness": 80, "progress": 0},
            "playing": {"yarn": {"daily_progress_limit": 2, "progress_delta": 4, "energy_delta": -8, "cleanliness_delta": -2}},
        },
    )
    service.resolve(ACCOUNT_ID, UID_A)
    result = service.care_action(
        ACCOUNT_ID, UID_A, category="playing", content_id="yarn", idempotency_key="yarn-before-task-read",
        source="chat-yarn", activity_id="activity-before-task-read", user_initiated=True,
        cat_responded=True, terminal_status="done",
    )
    assert result["ok"] is True
    items = result["snapshot"]["tasks"]["items"]
    yarn = next(item for item in items if item["task_id"] == "daily-yarn-play")
    assert yarn["progress"] == 1
    assert yarn["target"] == 2
    follow_up = service.get_tasks(ACCOUNT_ID, UID_A)
    follow_up_yarn = next(item for item in follow_up["tasks"]["items"] if item["task_id"] == "daily-yarn-play")
    assert follow_up_yarn["progress"] == 1


@pytest.mark.unit
def test_stage6_same_activity_cannot_settle_twice_with_new_idempotency_key(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(config_manager)
    service.resolve(ACCOUNT_ID, UID_A)
    first = service.care_action(
        ACCOUNT_ID, UID_A, category="playing", content_id="yarn", idempotency_key="activity-key-1",
        source="chat-yarn", activity_id="same-activity", user_initiated=True,
        cat_responded=True, terminal_status="done",
    )
    repeated = service.care_action(
        ACCOUNT_ID, UID_A, category="playing", content_id="yarn", idempotency_key="activity-key-2",
        source="chat-yarn", activity_id="same-activity", user_initiated=True,
        cat_responded=True, terminal_status="done",
    )
    assert first["ok"] is True
    assert repeated == first
    assert repeated["snapshot"]["activity_counters"]["playing"]["yarn"] == 1


@pytest.mark.unit
def test_stage6_autonomous_play_and_non_done_terminal_never_settle(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(config_manager)
    service.resolve(ACCOUNT_ID, UID_A)
    for source, terminal, code in (
        ("cat_mind", "done", "play_source_not_allowed"),
        ("gravity-playground", "cancelled", "play_not_completed"),
        ("gravity-playground", "timeout", "play_not_completed"),
        ("untrusted-source", "done", "play_source_not_allowed"),
    ):
        result = service.care_action(
            ACCOUNT_ID, UID_A, category="playing", content_id="yarn", idempotency_key=f"reject-{source}-{terminal}",
            source=source, activity_id=f"{source}-{terminal}", user_initiated=True,
            cat_responded=True, terminal_status=terminal,
        )
        assert result["ok"] is False
        assert result["code"] == code
        assert result["snapshot"]["status_values"]["progress"] == 0
        assert result["snapshot"]["activity_counters"].get("playing", {}) == {}


@pytest.mark.unit
def test_stage6_consumable_play_uses_inventory_and_server_daily_limit(tmp_path):
    config_manager = _ConfigManager(tmp_path)
    service = LocalPetCareBindingService(
        config_manager,
        snapshot_config={
            "initial_inventory": {"teaser_wand": 2},
            "playing": {"teaser_wand": {"daily_limit": 1, "progress_delta": 8, "energy_delta": -10, "cleanliness_delta": -2, "inventory_item": "teaser_wand"}},
        },
    )
    service.resolve(ACCOUNT_ID, UID_A)
    first = service.care_action(
        ACCOUNT_ID, UID_A, category="playing", content_id="teaser_wand", idempotency_key="wand-1",
        source="pet-care-toy", activity_id="wand-activity-1", user_initiated=True,
        cat_responded=True, terminal_status="done",
    )
    second = service.care_action(
        ACCOUNT_ID, UID_A, category="playing", content_id="teaser_wand", idempotency_key="wand-2",
        source="pet-care-toy", activity_id="wand-activity-2", user_initiated=True,
        cat_responded=True, terminal_status="done",
    )
    assert first["ok"] is True
    assert first["snapshot"]["inventory"]["teaser_wand"] == 1
    assert second["ok"] is False
    assert second["code"] == "play_daily_limit"
    assert second["snapshot"]["inventory"]["teaser_wand"] == 1
    assert second["snapshot"]["status_values"]["progress"] == first["snapshot"]["status_values"]["progress"]


@pytest.mark.unit
def test_stage6_play_route_returns_play_actions_and_idempotent_result(pet_app):
    client, config_manager = pet_app
    config_manager.pet_care_snapshot_config = {"initial_inventory": {"teaser_wand": 1}}
    client.get("/api/pet-care/context", params={"character_uid": UID_A})
    payload = {
        "category": "playing", "content_id": "teaser_wand", "idempotency_key": "route-play-1",
        "source": "pet-care-toy", "activity_id": "route-activity-1",
        "user_initiated": True, "cat_responded": True, "terminal_status": "done",
    }
    response = client.post(f"/api/pet-care/pets/{UID_A}/care-actions", json=payload)
    repeated = client.post(f"/api/pet-care/pets/{UID_A}/care-actions", json=payload)
    assert response.status_code == 200
    assert repeated.status_code == 200
    assert response.json() == repeated.json()
    body = response.json()
    assert body["operation"]["status"] == "confirmed"
    assert body["operation"]["category"] == "playing"
    assert body["play_actions"]["teaser_wand"]["daily_count"] == 1
    assert body["snapshot"]["inventory"]["teaser_wand"] == 0
