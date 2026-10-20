"""Regression and time-boundary tests for the Stage 8 service contract.

These tests deliberately use a controllable clock and a temporary SQLite
directory.  They exercise the durable session fence rather than the browser
or the real local PetCare archive.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.pet_care.binding_service import BindingServiceError, LocalPetCareBindingService


UTC = timezone.utc
CHARACTER_UID = "c" * 32


class _Config:
    def __init__(self, root: Path) -> None:
        self.local_state_dir = root

    def ensure_local_state_directory(self) -> bool:
        self.local_state_dir.mkdir(parents=True, exist_ok=True)
        return True


def _service(root: Path, now: list[datetime], **overrides: object) -> LocalPetCareBindingService:
    config: dict[str, object] = {
        "initial_status_values": {
            "satiety": 30,
            "energy": 35,
            "cleanliness": 25,
            "mood": 30,
            "progress": 100,
        },
        "initial_inventory": {"small_fish": 3, "medicine": 0, "teaser_wand": 0},
        "settlement": {
            "satiety_decay_per_hour": 0,
            "cleanliness_decay_per_hour": 0,
            "energy_recovery_per_hour": 0,
        },
        "offline_settlement": {
            "negative_threshold_hours": 48,
            "rollback_after_days": 5,
            "progress_rollback_per_day": 4,
            "protected_min_status": 80,
            "sick": {"probability": 1.0, "max_probability": 1.0},
            "sulk": {"probability": 1.0, "max_probability": 1.0},
        },
    }
    config.update(overrides)
    return LocalPetCareBindingService(
        _Config(root), snapshot_config=config, clock=lambda: now[0]
    )


def _settlements(root: Path) -> list[dict[str, object]]:
    database = root / "pet_care" / "pet_care.sqlite3"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT segment_id, session_id, previous_session_id, result_json "
            "FROM pet_care_offline_settlements ORDER BY created_at"
        ).fetchall()
    return [
        {
            "segment_id": row[0],
            "session_id": row[1],
            "previous_session_id": row[2],
            "result_json": json.loads(row[3]),
        }
        for row in rows
    ]


def _start_session(service: LocalPetCareBindingService, account: str, session: str) -> None:
    service.resolve(account, CHARACTER_UID, session_context={"session_id": session})


@pytest.mark.unit
def test_old_session_replay_is_rejected_after_restart_without_new_settlement(tmp_path: Path) -> None:
    """A previously accepted session must not become a new offline segment.

    The A -> B transition creates one durable receipt.  Replaying old A after
    a restart must be fenced before reconciliation; otherwise the later B
    retry can read an offline result as if it were an operation receipt.
    """

    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    service = _service(tmp_path, now)
    _start_session(service, "account", "session-a")
    assert service.mark_session_invalid("account", session_id="session-a") is True
    now[0] += timedelta(days=6)
    returned = service.resolve(
        "account", CHARACTER_UID, session_context={"session_id": "session-b"}
    )
    assert returned.reconciliation is not None
    assert len(_settlements(tmp_path)) == 1

    # Re-open the service to prove the fence is persisted, not process-local.
    now[0] += timedelta(days=2)
    reopened = _service(tmp_path, now)
    baseline_at_t2 = reopened.resolve(
        "account", CHARACTER_UID, session_context={"session_id": "session-b"}
    ).snapshot
    with pytest.raises(BindingServiceError) as error:
        reopened.resolve(
            "account", CHARACTER_UID, session_context={"session_id": "session-a"}
        )
    assert error.value.code == "session_stale"

    after = reopened.resolve(
        "account", CHARACTER_UID, session_context={"session_id": "session-b"}
    )
    assert after.snapshot == baseline_at_t2
    assert len(_settlements(tmp_path)) == 1


@pytest.mark.unit
def test_all_prior_sessions_are_fenced_after_multiple_replacements(tmp_path: Path) -> None:
    now = [datetime(2026, 1, 15, tzinfo=UTC)]
    service = _service(tmp_path, now)
    _start_session(service, "account", "session-a")
    now[0] += timedelta(hours=1)
    service.resolve("account", CHARACTER_UID, session_context={"session_id": "session-b"})
    now[0] += timedelta(hours=1)
    service.resolve("account", CHARACTER_UID, session_context={"session_id": "session-c"})
    current = service.resolve(
        "account", CHARACTER_UID, session_context={"session_id": "session-c"}
    ).snapshot

    for old_session in ("session-a", "session-b"):
        with pytest.raises(BindingServiceError) as error:
            service.resolve(
                "account", CHARACTER_UID, session_context={"session_id": old_session}
            )
        assert error.value.code == "session_stale"

    assert service.resolve(
        "account", CHARACTER_UID, session_context={"session_id": "session-c"}
    ).snapshot == current
    assert len(_settlements(tmp_path)) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("elapsed", "negative_expected"),
    [
        (timedelta(hours=47, minutes=59, seconds=59), False),
        (timedelta(hours=48), True),
    ],
)
def test_negative_state_threshold_is_exact_and_uses_service_time(
    tmp_path: Path, elapsed: timedelta, negative_expected: bool
) -> None:
    now = [datetime(2026, 2, 1, tzinfo=UTC)]
    service = _service(tmp_path, now)
    _start_session(service, "account", "session-a")
    # Explicit invalidation fixes the offline origin; the browser cannot
    # provide or alter this timestamp.
    assert service.mark_session_invalid("account", session_id="session-a") is True
    now[0] += elapsed
    result = service.resolve(
        "account", CHARACTER_UID, session_context={"session_id": "session-b"}
    )
    negative = result.snapshot["negative_status"]
    assert bool(negative["sick"]["active"]) is negative_expected
    assert bool(negative["sulk"]["active"]) is negative_expected
    assert result.reconciliation is not None
    assert result.reconciliation["duration_seconds"] == pytest.approx(elapsed.total_seconds())


@pytest.mark.unit
def test_five_day_progress_rollback_stops_at_unlock_checkpoint(tmp_path: Path) -> None:
    now = [datetime(2026, 3, 1, tzinfo=UTC)]
    service = _service(
        tmp_path,
        now,
        initial_status_values={
            "satiety": 30,
            "energy": 35,
            "cleanliness": 25,
            "mood": 30,
            "progress": 42,
        },
        initial_unlocks=[{"id": "checkpoint", "threshold": 30, "name": "checkpoint"}],
    )
    _start_session(service, "account", "session-a")
    # Resolve once with enough progress to materialize and persist the
    # checkpoint, then begin a six-day offline segment.
    baseline = service.resolve("account", CHARACTER_UID, session_context={"session_id": "session-a"})
    checkpoint = baseline.snapshot["unlock_checkpoint"]
    assert checkpoint >= 30
    assert service.mark_session_invalid("account", session_id="session-a") is True
    now[0] += timedelta(days=30)
    returned = service.resolve(
        "account", CHARACTER_UID, session_context={"session_id": "session-b"}
    )
    rollback = returned.reconciliation["rollback"] if returned.reconciliation else {}
    assert rollback["days"] == 25
    assert returned.snapshot["status_values"]["progress"] >= checkpoint
    assert returned.snapshot["unlock_checkpoint"] == checkpoint


@pytest.mark.unit
def test_exploration_complete_and_recall_are_terminal_and_replayable(tmp_path: Path) -> None:
    now = [datetime(2026, 4, 1, tzinfo=UTC)]
    service = _service(
        tmp_path,
        now,
        exploration={
            "locations": [
                {
                    "id": "window-path",
                    "name": "Window Path",
                    "duration_seconds": 10,
                    "cost": {},
                    "letters": [],
                    "reward": {"progress_delta": 3, "full_bonus_progress": 2},
                }
            ]
        },
    )
    _start_session(service, "account", "session-a")
    started = service.start_exploration(
        "account", CHARACTER_UID, location_id="window-path", idempotency_key="explore-start",
        session_context={"session_id": "session-a"},
    )
    exploration_id = started["exploration"]["exploration_id"]
    now[0] += timedelta(seconds=10)
    completed = service.complete_exploration(
        "account", CHARACTER_UID, exploration_id=exploration_id,
        idempotency_key="explore-complete", session_context={"session_id": "session-a"},
    )
    assert completed["exploration"]["status"] == "completed"
    assert service.complete_exploration(
        "account", CHARACTER_UID, exploration_id=exploration_id,
        idempotency_key="explore-complete", session_context={"session_id": "session-a"},
    ) == completed
    with pytest.raises(BindingServiceError) as error:
        service.recall_exploration(
            "account", CHARACTER_UID, exploration_id=exploration_id,
            idempotency_key="explore-recall-after-complete", session_context={"session_id": "session-a"},
        )
    assert error.value.code == "exploration_already_settled"

    # A separate activity confirms the recall branch and its terminal guard.
    started_again = service.start_exploration(
        "account", CHARACTER_UID, location_id="window-path", idempotency_key="explore-start-2",
        session_context={"session_id": "session-a"},
    )
    second_id = started_again["exploration"]["exploration_id"]
    recalled = service.recall_exploration(
        "account", CHARACTER_UID, exploration_id=second_id,
        idempotency_key="explore-recall", session_context={"session_id": "session-a"},
    )
    assert recalled["exploration"]["status"] == "recalled"
    assert service.recall_exploration(
        "account", CHARACTER_UID, exploration_id=second_id,
        idempotency_key="explore-recall", session_context={"session_id": "session-a"},
    ) == recalled


@pytest.mark.unit
def test_config_variant_keeps_snapshot_bounds_and_rejects_invalid_values(tmp_path: Path) -> None:
    now = [datetime(2026, 5, 1, tzinfo=UTC)]
    with pytest.raises(BindingServiceError) as error:
        _service(tmp_path / "invalid", now, offline_settlement={"protected_min_status": 101})
    assert error.value.code == "pet_care_config_invalid"

    service = _service(
        tmp_path / "variant",
        now,
        initial_status_values={
            "satiety": 0,
            "energy": 100,
            "cleanliness": 50,
            "mood": 1,
            "progress": 0,
        },
        mood_formula={
            "initial_interaction_index": 0,
            "interaction_decay_per_hour": 0,
            "weights": {"satiety": 0, "energy": 0, "cleanliness": 0, "interaction": 1},
            "jitter_range": 0,
        },
    )
    context = service.resolve("variant", CHARACTER_UID, session_context={"session_id": "v1"})
    values = context.snapshot["status_values"]
    assert values["satiety"] == 0
    assert values["energy"] == 100
    assert 0 <= values["mood"] <= 100
    assert context.snapshot["status_values"]["progress"] >= 0
