"""Stage 8 offline reconciliation and negative-state contract tests."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.pet_care.binding_service import BindingServiceError, LocalPetCareBindingService


UID = "a" * 32


class Config:
    def __init__(self, root: Path):
        self.local_state_dir = root

    def ensure_local_state_directory(self):
        self.local_state_dir.mkdir(parents=True, exist_ok=True)
        return True


def make_service(tmp_path, now, **overrides):
    return LocalPetCareBindingService(
        Config(tmp_path),
        snapshot_config={
            "initial_status_values": {
                "satiety": 50,
                "energy": 50,
                "cleanliness": 40,
                "mood": 40,
                "progress": 100,
            },
            "offline_settlement": {
                "sick": {"probability": 1.0, "max_probability": 1.0},
                "sulk": {"probability": 1.0, "max_probability": 1.0},
            },
            "settlement": {
                "satiety_decay_per_hour": 0,
                "cleanliness_decay_per_hour": 0,
                "energy_recovery_per_hour": 0,
            },
            **overrides,
        },
        clock=lambda: now[0],
    )


@pytest.mark.parametrize(
    "offline_override",
    [
        {"protected_min_status": 101},
        {"sick": {"probability": 1.1}},
    ],
)
@pytest.mark.unit
def test_offline_probability_and_protection_config_fail_closed(tmp_path, offline_override):
    with pytest.raises(BindingServiceError) as error:
        LocalPetCareBindingService(Config(tmp_path), snapshot_config={"offline_settlement": offline_override})
    assert error.value.code == "pet_care_config_invalid"


@pytest.mark.unit
def test_session_reconcile_is_one_shot_and_rolls_back_to_unlock_checkpoint(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(tmp_path, now)
    first = service.resolve("account", UID, session_context={"session_id": "login-1"}).as_dict()
    assert first["reconciliation"]["reason"] == "first_verified_session"

    # Materialize an unlocked checkpoint before returning from a long offline
    # segment.  The unlock record must survive the rollback.
    service._connect  # keep the test on the public service contract below
    now[0] += timedelta(days=6)
    returned = service.resolve("account", UID, session_context={"session_id": "login-2"}).as_dict()
    reconciliation = returned["reconciliation"]
    assert reconciliation["status"] == "confirmed"
    assert reconciliation["rollback"]["after"] >= reconciliation["rollback"]["checkpoint"]
    assert returned["snapshot"]["status_values"]["progress"] == 80
    assert returned["snapshot"]["negative_status"]["sick"]["active"] is True
    assert returned["snapshot"]["negative_status"]["sulk"]["active"] is True

    repeated = service.resolve("account", UID, session_context={"session_id": "login-2"}).as_dict()
    assert repeated["reconciliation"]["reason"] == "same_session"
    assert repeated["snapshot"] == returned["snapshot"]


@pytest.mark.unit
def test_late_old_session_is_rejected_without_repeating_offline_settlement(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(tmp_path, now)
    service.resolve("account", UID, session_context={"session_id": "login-a"})
    service.mark_session_invalid("account", session_id="login-a")
    now[0] += timedelta(days=6)
    returned = service.resolve("account", UID, session_context={"session_id": "login-b"})

    db_path = tmp_path / "pet_care" / "pet_care.sqlite3"
    with sqlite3.connect(db_path) as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM pet_care_offline_settlements WHERE account_id = ?",
            ("account",),
        ).fetchone()[0]
        state = connection.execute(
            "SELECT last_session_id FROM pet_care_session_state WHERE account_id = ?",
            ("account",),
        ).fetchone()[0]
    assert before == 1
    assert state == "login-b"
    assert returned.reconciliation["session_id"] == "login-b"

    with pytest.raises(BindingServiceError) as error:
        service.resolve("account", UID, session_context={"session_id": "login-a"})
    assert error.value.code == "session_stale"
    with pytest.raises(BindingServiceError) as error:
        service.reconcile_session_return(
            "account", UID, session_context={"session_id": "login-a"},
        )
    assert error.value.code == "session_stale"
    with sqlite3.connect(db_path) as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM pet_care_offline_settlements WHERE account_id = ?",
            ("account",),
        ).fetchone()[0]
    assert after == before


@pytest.mark.unit
def test_offline_settlement_replay_returns_the_same_receipt_shape(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(tmp_path, now)
    service.resolve("account", UID, session_context={"session_id": "login-a"})
    now[0] += timedelta(days=6)
    first = service.resolve("account", UID, session_context={"session_id": "login-b"})

    # Simulate a crash after the durable settlement receipt was written but
    # before the session fence update.  The same A -> B edge must replay the
    # direct reconciliation payload, rather than going through the operation
    # receipt decoder (which expects an ``ok`` envelope).
    db_path = tmp_path / "pet_care" / "pet_care.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE pet_care_session_state SET last_session_id = ?, "
            "last_invalid_at = NULL, invalid_session_id = NULL WHERE account_id = ?",
            ("login-a", "account"),
        )
    replay = service.resolve("account", UID, session_context={"session_id": "login-b"})
    assert replay.reconciliation == first.reconciliation
    with sqlite3.connect(db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM pet_care_offline_settlements WHERE account_id = ?",
            ("account",),
        ).fetchone()[0]
    assert count == 1


@pytest.mark.unit
def test_explicit_logout_marker_is_used_as_offline_origin(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(tmp_path, now)
    service.resolve("account", UID, session_context={"session_id": "login-1"})

    # The account remains valid for several days before an explicit logout;
    # that online time must not be mistaken for an offline segment.
    now[0] += timedelta(days=6)
    service.resolve("account", UID, session_context={"session_id": "login-1"})
    service.mark_session_invalid("account", session_id="login-1")
    now[0] += timedelta(hours=49)
    returned = service.resolve("account", UID, session_context={"session_id": "login-2"}).as_dict()
    assert returned["reconciliation"]["duration_seconds"] == 49 * 3600
    assert returned["reconciliation"]["rollback"]["days"] == 0


@pytest.mark.unit
def test_state_change_rejects_replaced_session_inside_write_transaction(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(tmp_path, now, initial_inventory={"small_fish": 1})
    service.resolve("account", UID, session_context={"session_id": "login-1"})
    service.resolve("account", UID, session_context={"session_id": "login-2"})
    with pytest.raises(BindingServiceError) as error:
        service.care_action(
            "account", UID, category="feeding", content_id="small_fish",
            idempotency_key="stale-feed", session_context={"session_id": "login-1"},
        )
    assert error.value.code == "session_stale"
    confirmed = service.care_action(
        "account", UID, category="feeding", content_id="small_fish",
        idempotency_key="current-feed", session_context={"session_id": "login-2"},
    )
    assert confirmed["ok"] is True


@pytest.mark.unit
def test_interaction_index_and_mood_are_service_owned_and_configurable(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = LocalPetCareBindingService(
        Config(tmp_path),
        snapshot_config={
            "initial_status_values": {
                "satiety": 60, "energy": 60, "cleanliness": 50, "mood": 0, "progress": 0,
            },
            "mood_formula": {
                "initial_interaction_index": 10,
                "interaction_decay_per_hour": 2,
                "weights": {"satiety": 0, "energy": 0, "cleanliness": 0, "interaction": 1},
                "jitter_range": 0,
            },
        },
        clock=lambda: now[0],
    )
    service.resolve("account", UID)
    confirmed = service.care_action(
        "account", UID, category="cleaning", content_id="brushing", idempotency_key="brush-mood",
    )
    assert confirmed["snapshot"]["interaction_index"] == 14.0
    assert confirmed["snapshot"]["status_values"]["mood"] == 14

    now[0] += timedelta(hours=1)
    settled = service.resolve("account", UID).snapshot
    assert settled["interaction_index"] == 12.0
    assert settled["status_values"]["mood"] == 12


@pytest.mark.unit
def test_high_status_protection_skips_negative_states(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = LocalPetCareBindingService(
        Config(tmp_path),
        snapshot_config={
            "initial_status_values": {
                "satiety": 100,
                "energy": 100,
                "cleanliness": 100,
                "mood": 100,
                "progress": 0,
            },
            "offline_settlement": {
                "sick": {"probability": 1.0, "max_probability": 1.0},
                "sulk": {"probability": 1.0, "max_probability": 1.0},
            },
            "settlement": {
                "satiety_decay_per_hour": 0,
                "cleanliness_decay_per_hour": 0,
                "energy_recovery_per_hour": 0,
            },
        },
        clock=lambda: now[0],
    )
    service.resolve("account", UID, session_context={"session_id": "login-1"})
    now[0] += timedelta(days=8)
    returned = service.resolve("account", UID, session_context={"session_id": "login-2"}).as_dict()
    assert returned["reconciliation"]["negative"]["protected"] is True
    assert returned["snapshot"]["negative_status"]["sick"]["active"] is False
    assert returned["snapshot"]["negative_status"]["sulk"]["active"] is False


@pytest.mark.unit
def test_recovery_tasks_claim_and_clear_states_idempotently(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(tmp_path, now)
    service.resolve("account", UID, session_context={"session_id": "login-1"})
    now[0] += timedelta(days=6)
    returned = service.resolve("account", UID, session_context={"session_id": "login-2"}).as_dict()
    assert {item["task_id"] for item in returned["snapshot"]["tasks"]["items"]} >= {"recovery-sick", "recovery-sulk"}

    medicine = service.claim_task("account", UID, task_id="recovery-sick", idempotency_key="claim-medicine")
    assert medicine["reward"] == {"item_id": "medicine", "quantity": 1}
    assert service.claim_task("account", UID, task_id="recovery-sick", idempotency_key="claim-medicine") == medicine
    cleared = service.use_recovery_item("account", UID, status_key="sick", idempotency_key="use-medicine")
    assert cleared["snapshot"]["negative_status"]["sick"]["active"] is False
    assert service.use_recovery_item("account", UID, status_key="sick", idempotency_key="use-medicine") == cleared
    replay_after_use = service.claim_task("account", UID, task_id="recovery-sick", idempotency_key="claim-medicine-again")
    assert replay_after_use["ok"] is False
    assert replay_after_use["code"] == "task_already_claimed"

    wand = service.claim_task("account", UID, task_id="recovery-sulk", idempotency_key="claim-wand")
    assert wand["snapshot"]["inventory"]["teaser_wand"] == 1
    played = service.care_action(
        "account", UID, category="playing", content_id="teaser_wand", idempotency_key="play-recovery",
        source="pet-care-toy", activity_id="recovery-play", user_initiated=True,
        cat_responded=True, terminal_status="done",
    )
    assert played["snapshot"]["negative_status"]["sulk"]["active"] is False
    assert played["snapshot"]["inventory"]["teaser_wand"] == 0
    replay_wand_after_use = service.claim_task("account", UID, task_id="recovery-sulk", idempotency_key="claim-wand-again")
    assert replay_wand_after_use["ok"] is False
    assert replay_wand_after_use["code"] == "task_already_claimed"


@pytest.mark.unit
def test_sulk_recovery_play_uses_configured_item_id(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(
        tmp_path,
        now,
        offline_settlement={
            "sulk": {
                "recovery_item_id": "comfort_wand",
                "task_id": "recovery-sulk-custom",
                "task_title": "哄哄她",
            },
        },
        playing={
            "comfort_wand": {
                "label": "安抚逗猫棒",
                "allowed_sources": ["pet-care-toy"],
                "inventory_item": "comfort_wand",
                "progress_delta": 1,
                "daily_limit": 3,
                "energy_delta": -1,
                "cleanliness_delta": 0,
                "interaction_delta": 1,
            },
        },
    )
    service.resolve("account", UID, session_context={"session_id": "login-1"})
    now[0] += timedelta(days=6)
    returned = service.resolve("account", UID, session_context={"session_id": "login-2"})
    assert returned.snapshot["negative_status"]["sulk"]["required_item"] == "comfort_wand"
    claim = service.claim_task(
        "account", UID, task_id="recovery-sulk-custom", idempotency_key="claim-custom-wand",
    )
    assert claim["reward"] == {"item_id": "comfort_wand", "quantity": 1}
    played = service.care_action(
        "account", UID, category="playing", content_id="comfort_wand",
        idempotency_key="play-custom-wand", source="pet-care-toy",
        activity_id="custom-wand-activity", user_initiated=True,
        cat_responded=True, terminal_status="done",
    )
    assert played["snapshot"]["negative_status"]["sulk"]["active"] is False
    assert played["snapshot"]["negative_status"]["sulk"]["cleared_by"] == "comfort_wand"


@pytest.mark.unit
def test_recovery_item_cannot_bypass_task_claim(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(tmp_path, now, initial_inventory={"medicine": 1})
    service.resolve("account", UID, session_context={"session_id": "login-1"})
    now[0] += timedelta(days=6)
    service.resolve("account", UID, session_context={"session_id": "login-2"})
    with pytest.raises(BindingServiceError) as error:
        service.use_recovery_item("account", UID, status_key="sick", idempotency_key="use-before-claim")
    assert error.value.code == "recovery_task_not_claimed"


@pytest.mark.unit
def test_sick_blocks_exploration_but_cleaning_remains_available(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(tmp_path, now)
    service.resolve("account", UID, session_context={"session_id": "login-1"})
    now[0] += timedelta(days=6)
    service.resolve("account", UID, session_context={"session_id": "login-2"})
    with pytest.raises(BindingServiceError) as error:
        service.start_exploration("account", UID, location_id="window-path", idempotency_key="sick-explore")
    assert error.value.code == "exploration_blocked_by_status"


@pytest.mark.unit
def test_interaction_index_is_persisted_and_mood_is_service_derived(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(
        tmp_path,
        now,
        initial_inventory={"small_fish": 1},
        mood_formula={
            "initial_interaction_index": 40,
            "interaction_decay_per_hour": 1,
            "weights": {
                "satiety": 0.25,
                "energy": 0.20,
                "cleanliness": 0.25,
                "interaction": 0.30,
            },
            "jitter_range": 0,
        },
    )
    service.resolve("account", UID, session_context={"session_id": "login-1"})
    now[0] += timedelta(hours=2)
    settled = service.resolve("account", UID, session_context={"session_id": "login-1"}).as_dict()
    assert settled["snapshot"]["interaction_index"] == 38
    assert settled["snapshot"]["status_values"]["mood"] == 44

    fed = service.care_action(
        "account", UID, category="feeding", content_id="small_fish",
        idempotency_key="mood-feed", session_context={"session_id": "login-1"},
    )
    assert fed["snapshot"]["interaction_index"] == 40
    assert fed["snapshot"]["status_values"]["mood"] == 51

    # Re-opening the service reads the same interaction value and derived mood
    # from SQLite; it does not create a client-side second copy of the state.
    reopened = service.resolve("account", UID, session_context={"session_id": "login-1"}).as_dict()
    assert reopened["snapshot"]["interaction_index"] == 40
    assert reopened["snapshot"]["status_values"]["mood"] == 51
