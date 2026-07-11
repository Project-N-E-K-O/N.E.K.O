from __future__ import annotations

from pathlib import Path

import pytest

from main_logic.facts_sync import sync_worker
from main_logic.facts_sync.sync_worker import _select_unsynced_facts


pytestmark = pytest.mark.unit


def test_select_unsynced_facts_skips_private_and_redacted_entries() -> None:
    selected = _select_unsynced_facts(
        [
            {"hash": "public-12345678", "text": "safe", "importance": 7},
            {"hash": "private-12345678", "text": "secret", "importance": 9, "private": True},
            {"hash": "redacted-12345678", "text": "[redacted]", "importance": 9, "redacted": True},
        ],
        already_synced_hashes=set(),
    )

    assert selected == [
        {
            "fact_hash": "public-12345678",
            "text": "safe",
            "importance": 0.7,
            "redacted": False,
        }
    ]


def test_select_unsynced_facts_skips_malformed_importance() -> None:
    selected = _select_unsynced_facts(
        [
            {"hash": "bad-imp-12345678", "text": "dirty", "importance": "unknown"},
            {"hash": "ok-imp-12345678", "text": "fine", "importance": 8},
        ],
        already_synced_hashes=set(),
    )

    assert [row["fact_hash"] for row in selected] == ["ok-imp-12345678"]


def test_get_client_id_persists_fresh_default_before_returning(tmp_path, monkeypatch) -> None:
    saved: list[dict] = []

    class FakeConfigManager:
        cloudsave_local_state_path = tmp_path / "state" / "cloudsave_local_state.json"

        def load_cloudsave_local_state(self) -> dict:
            return {"client_id": "fresh-client-id"}

        def build_default_cloudsave_local_state(self) -> dict:
            raise AssertionError("loaded default already contains a client_id")

        def save_cloudsave_local_state(self, state: dict) -> None:
            saved.append(dict(state))

    monkeypatch.setattr(sync_worker, "get_config_manager", lambda: FakeConfigManager())

    assert sync_worker._get_client_id() == "fresh-client-id"
    assert saved == [{"client_id": "fresh-client-id"}]


def test_get_client_id_fails_closed_when_fresh_default_cannot_be_saved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeConfigManager:
        cloudsave_local_state_path = tmp_path / "state" / "cloudsave_local_state.json"

        def load_cloudsave_local_state(self) -> dict:
            return {"client_id": "volatile-client-id"}

        def build_default_cloudsave_local_state(self) -> dict:
            raise AssertionError("loaded default already contains a client_id")

        def save_cloudsave_local_state(self, _state: dict) -> None:
            raise OSError("disk unavailable")

    monkeypatch.setattr(sync_worker, "get_config_manager", lambda: FakeConfigManager())

    assert sync_worker._get_client_id() is None
