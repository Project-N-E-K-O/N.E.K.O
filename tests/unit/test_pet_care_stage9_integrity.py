"""Stage 9 preflight checks for local PetCare storage integrity.

These tests keep the local SQLite mirror honest before the broader desktop
acceptance run: physical migrations are repeatable, relational identity wins
over JSON payloads, corrupt idempotency receipts fail closed, and concurrent
writes cannot consume one inventory item twice.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from services.pet_care import BindingServiceError, LocalPetCareBindingService


ACCOUNT = "account-stage9"
UID = "a" * 32
UID_B = "b" * 32


class Config:
    def __init__(self, root: Path):
        self.local_state_dir = root

    def ensure_local_state_directory(self):
        self.local_state_dir.mkdir(parents=True, exist_ok=True)
        return True


@pytest.mark.unit
def test_storage_schema_metadata_and_indexes_survive_reopen(tmp_path):
    config = Config(tmp_path)
    first = LocalPetCareBindingService(config)
    first.resolve(ACCOUNT, UID)

    db_path = tmp_path / "pet_care" / "pet_care.sqlite3"
    with sqlite3.connect(db_path) as connection:
        meta = connection.execute(
            "SELECT schema_version FROM pet_care_schema_meta WHERE schema_name = 'pet_care'"
        ).fetchone()
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(pet_care_operation_results)")
        }
    assert meta == (1,)
    assert "idx_pet_care_operations_created_at" in indexes

    # Every service instance runs the same migration guard; reopening must not
    # create duplicate columns or alter the existing pet identity.
    second = LocalPetCareBindingService(config)
    reopened = second.resolve(ACCOUNT, UID)
    assert reopened.pet_id == first.resolve(ACCOUNT, UID).pet_id


@pytest.mark.unit
def test_future_storage_schema_version_fails_closed_without_downgrade(tmp_path):
    config = Config(tmp_path)
    service = LocalPetCareBindingService(config)
    service.resolve(ACCOUNT, UID)
    db_path = tmp_path / "pet_care" / "pet_care.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE pet_care_schema_meta SET schema_version = ? WHERE schema_name = 'pet_care'",
            (LocalPetCareBindingService._STORAGE_SCHEMA_VERSION + 1,),
        )

    with pytest.raises(BindingServiceError) as error:
        service.resolve(ACCOUNT, UID)
    assert error.value.code == "pet_care_storage_schema_version_unsupported"
    with sqlite3.connect(db_path) as connection:
        stored = connection.execute(
            "SELECT schema_version FROM pet_care_schema_meta WHERE schema_name = 'pet_care'"
        ).fetchone()
    assert stored == (LocalPetCareBindingService._STORAGE_SCHEMA_VERSION + 1,)


@pytest.mark.unit
def test_snapshot_relational_owner_mismatch_fails_closed(tmp_path):
    config = Config(tmp_path)
    service = LocalPetCareBindingService(config)
    context = service.resolve(ACCOUNT, UID)
    db_path = tmp_path / "pet_care" / "pet_care.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE pet_care_snapshots SET account_id = ? WHERE pet_id = ?",
            ("other-account", context.pet_id),
        )

    with pytest.raises(BindingServiceError) as error:
        service.resolve(ACCOUNT, UID)
    assert error.value.code == "pet_care_snapshot_invalid"


@pytest.mark.unit
def test_corrupt_operation_receipt_fails_closed(tmp_path):
    config = Config(tmp_path)
    service = LocalPetCareBindingService(
        config, snapshot_config={"initial_inventory": {"small_fish": 1}}
    )
    service.resolve(ACCOUNT, UID)
    service.care_action(
        ACCOUNT,
        UID,
        category="feeding",
        content_id="small_fish",
        idempotency_key="receipt-corrupt",
    )
    db_path = tmp_path / "pet_care" / "pet_care.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE pet_care_operation_results SET result_json = ? "
            "WHERE account_id = ? AND idempotency_key = ?",
            ("[]", ACCOUNT, "receipt-corrupt"),
        )

    with pytest.raises(BindingServiceError) as error:
        service.care_action(
            ACCOUNT,
            UID,
            category="feeding",
            content_id="small_fish",
            idempotency_key="receipt-corrupt",
        )
    assert error.value.code == "pet_care_operation_result_invalid"


@pytest.mark.unit
def test_concurrent_writes_serialize_inventory_consumption(tmp_path):
    config = Config(tmp_path)
    service = LocalPetCareBindingService(
        config,
        snapshot_config={
            "initial_inventory": {"small_fish": 1},
            "initial_status_values": {"satiety": 0},
        },
    )
    service.resolve(ACCOUNT, UID)

    def feed(key: str):
        return service.care_action(
            ACCOUNT,
            UID,
            category="feeding",
            content_id="small_fish",
            idempotency_key=key,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(feed, ("concurrent-a", "concurrent-b")))

    assert sorted(result["ok"] for result in results) == [False, True]
    rejected = next(result for result in results if not result["ok"])
    assert rejected["code"] == "inventory_insufficient"
    final = service.resolve(ACCOUNT, UID).snapshot
    assert final["inventory"]["small_fish"] == 0


@pytest.mark.unit
def test_idempotency_hash_is_scoped_to_rebound_pet_role(tmp_path):
    config = Config(tmp_path)
    service = LocalPetCareBindingService(
        config,
        snapshot_config={"initial_inventory": {"small_fish": 2}},
    )
    first = service.resolve(ACCOUNT, UID)
    settled = service.care_action(
        ACCOUNT,
        UID,
        category="feeding",
        content_id="small_fish",
        idempotency_key="role-scoped-key",
    )
    assert settled["ok"] is True
    rebound = service.bind(ACCOUNT, UID_B)
    assert rebound.bound_role_id != first.bound_role_id
    assert rebound.snapshot["inventory"]["small_fish"] == 1

    with pytest.raises(BindingServiceError) as error:
        service.care_action(
            ACCOUNT,
            UID_B,
            category="feeding",
            content_id="small_fish",
            idempotency_key="role-scoped-key",
        )
    assert error.value.code == "idempotency_key_reused"
    current = service.resolve(ACCOUNT, UID_B).snapshot
    assert current["inventory"]["small_fish"] == 1


@pytest.mark.unit
def test_explicit_logout_allows_verified_new_session_to_rebind_same_pet(tmp_path):
    """A post-logout bind is a new session edge, not a stale replay."""
    config = Config(tmp_path)
    service = LocalPetCareBindingService(
        config,
        snapshot_config={
            "initial_inventory": {"small_fish": 2},
            "initial_status_values": {"satiety": 0},
        },
    )
    first = service.resolve(ACCOUNT, UID, session_context={"session_id": "session-a"})
    settled = service.care_action(
        ACCOUNT,
        UID,
        category="feeding",
        content_id="small_fish",
        idempotency_key="logout-bind-key",
        session_context={"session_id": "session-a"},
    )
    assert settled["ok"] is True
    assert service.mark_session_invalid(ACCOUNT, session_id="session-a") is True

    rebound = service.bind(
        ACCOUNT,
        UID_B,
        session_context={"session_id": "session-b"},
    )
    assert rebound.pet_id == first.pet_id
    assert rebound.bound_role_id != first.bound_role_id
    with pytest.raises(BindingServiceError) as error:
        service.care_action(
            ACCOUNT,
            UID_B,
            category="feeding",
            content_id="small_fish",
            idempotency_key="logout-bind-key",
            session_context={"session_id": "session-b"},
        )
    assert error.value.code == "idempotency_key_reused"
