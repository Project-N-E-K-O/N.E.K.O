from __future__ import annotations

from pathlib import Path
import json

import pytest

from main_logic.card_cache import puller


pytestmark = pytest.mark.unit


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

    monkeypatch.setattr(puller, "get_config_manager", lambda: FakeConfigManager())

    assert puller._get_client_id() == "fresh-client-id"
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

    monkeypatch.setattr(puller, "get_config_manager", lambda: FakeConfigManager())

    assert puller._get_client_id() is None


def test_load_cached_cards_returns_safe_newest_records(tmp_path, monkeypatch) -> None:
    memory_dir = tmp_path / "memory"
    cards_dir = memory_dir / "Lanlan" / "cards"
    cards_dir.mkdir(parents=True)
    (cards_dir / "card-1.json").write_text(
        json.dumps({"id": "card-1", "title": "Cloud card"}),
        encoding="utf-8",
    )
    (cards_dir / "invalid.json").write_text("[]", encoding="utf-8")

    class FakeConfigManager:
        pass

    manager = FakeConfigManager()
    manager.memory_dir = memory_dir
    monkeypatch.setattr(puller, "get_config_manager", lambda: manager)

    assert puller.load_cached_cards() == [{"id": "card-1", "title": "Cloud card"}]


def test_card_cache_file_stem_is_platform_independent_and_collision_safe() -> None:
    assert puller._card_cache_file_stem("good-card") == "good-card"
    assert puller._card_cache_file_stem("a:shared") == "a%3Ashared"
    assert puller._card_cache_file_stem("b:shared") == "b%3Ashared"
    assert puller._card_cache_file_stem("../shared") == "%2E%2E%2Fshared"
    assert puller._card_cache_file_stem("CON") == "%43ON"


@pytest.mark.asyncio
async def test_pull_skips_invalid_path_components_without_aborting_batch(
    tmp_path,
    monkeypatch,
) -> None:
    memory_dir = tmp_path / "memory"

    class FakeConfigManager:
        pass

    manager = FakeConfigManager()
    manager.memory_dir = memory_dir

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return [
                {"id": "bad-1", "lanlan_name": ""},
                {"id": "bad-2", "lanlan_name": "."},
                {"id": "bad\u0000", "lanlan_name": "Lanlan"},
                {"id": "a:shared", "lanlan_name": "Lanlan", "title": "A"},
                {"id": "b:shared", "lanlan_name": "Lanlan", "title": "B"},
                {"id": "good-card", "lanlan_name": "Lanlan", "title": "Good"},
            ]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, _url, **_kwargs):
            return Response()

    monkeypatch.setattr(puller, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(puller, "_get_client_id", lambda: "client-id")
    monkeypatch.setattr(puller, "get_config_manager", lambda: manager)
    monkeypatch.setattr(puller.httpx, "AsyncClient", FakeAsyncClient)

    assert await puller._pull_once() == 3
    assert json.loads(
        (memory_dir / "Lanlan" / "cards" / "a%3Ashared.json").read_text(
            encoding="utf-8"
        )
    )["id"] == "a:shared"
    assert json.loads(
        (memory_dir / "Lanlan" / "cards" / "b%3Ashared.json").read_text(
            encoding="utf-8"
        )
    )["id"] == "b:shared"
    assert json.loads(
        (memory_dir / "Lanlan" / "cards" / "good-card.json").read_text(
            encoding="utf-8"
        )
    )["title"] == "Good"
