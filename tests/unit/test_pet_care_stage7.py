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


def make_service(tmp_path, now):
    return LocalPetCareBindingService(
        Config(tmp_path),
        snapshot_config={
            "initial_status_values": {"satiety": 100, "energy": 100, "cleanliness": 100, "mood": 80},
            "exploration": {
                "locations": [{
                    "id": "test-path",
                    "name": "测试小路",
                    "duration_seconds": 60,
                    "costs": {"satiety": 10, "energy": 20, "cleanliness": 5},
                    "letter_image_url": "/static/assets/neko-idle/exploration-window-path.svg",
                    "letters": [{"id": "one", "text": "一"}, {"id": "two", "text": "二"}, {"id": "three", "text": "三"}],
                    "reward": {"progress_delta": 20, "full_bonus_progress": 4, "drops": [{"item_id": "teaser_wand", "min": 1, "max": 1}]},
                }]
            },
        },
        clock=lambda: now[0],
    )


@pytest.mark.unit
def test_stage7_start_persists_activity_and_costs_atomically(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(tmp_path, now)
    service.resolve("account", UID)

    result = service.start_exploration("account", UID, location_id="test-path", idempotency_key="start-1")
    assert result["exploration"]["status"] == "away"
    assert result["snapshot"]["status_values"]["satiety"] == 90
    assert result["snapshot"]["status_values"]["energy"] == 80
    assert result["snapshot"]["status_values"]["cleanliness"] == 95
    # The selected pool is server-owned.  An active response exposes only
    # letters already revealed by service time, never the unrevealed pool.
    assert result["exploration"]["letters"] == []
    assert result["snapshot"]["exploration"]["letters"] == []
    assert "random_seed" not in result["exploration"]
    assert result["letters"] == []
    assert result["result"]["exploration"]["letters"] == []
    assert "random_seed" not in result["result"]["exploration"]
    assert result["result"]["snapshot"]["exploration"]["letters"] == []
    status = service.get_exploration("account", UID, exploration_id=result["exploration"]["exploration_id"])
    assert status["snapshot"]["exploration"]["letters_revealed"] == []
    assert status["exploration"]["letters"] == []
    assert service.start_exploration("account", UID, location_id="test-path", idempotency_key="start-1") == result


@pytest.mark.unit
def test_stage7_recall_uses_server_time_and_is_idempotent(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(tmp_path, now)
    service.resolve("account", UID)
    started = service.start_exploration("account", UID, location_id="test-path", idempotency_key="start-1")
    now[0] += timedelta(seconds=30)
    result = service.recall_exploration(
        "account", UID, exploration_id=started["exploration"]["exploration_id"], idempotency_key="recall-1"
    )
    assert result["exploration"]["status"] == "recalled"
    assert result["rewards"]["ratio"] == 0.5
    assert result["snapshot"]["inventory"].get("teaser_wand", 0) == 0
    assert service.recall_exploration(
        "account", UID, exploration_id=started["exploration"]["exploration_id"], idempotency_key="recall-1"
    ) == result


@pytest.mark.unit
def test_stage7_complete_requires_full_duration_and_survives_restart(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = make_service(tmp_path, now)
    service.resolve("account", UID)
    started = service.start_exploration("account", UID, location_id="test-path", idempotency_key="start-1")
    with pytest.raises(BindingServiceError) as error:
        service.complete_exploration("account", UID, exploration_id=started["exploration"]["exploration_id"], idempotency_key="complete-early")
    assert getattr(error.value, "code", None) == "exploration_not_complete"
    now[0] += timedelta(seconds=60)
    restarted = make_service(tmp_path, now)
    status = restarted.get_exploration("account", UID, exploration_id=started["exploration"]["exploration_id"])
    assert status["exploration"]["completion_ratio"] == 1
    completed = restarted.complete_exploration("account", UID, exploration_id=started["exploration"]["exploration_id"], idempotency_key="complete-1")
    assert completed["exploration"]["status"] == "completed"
    assert completed["rewards"]["full_bonus"] > 0
    assert completed["letters"]
    assert all(letter["image_url"].endswith("exploration-window-path.svg") for letter in completed["letters"])
