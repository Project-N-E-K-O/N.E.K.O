from __future__ import annotations

import json
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

        def ensure_cloudsave_client_credentials(self) -> tuple[str, str]:
            state = {"client_id": "fresh-client-id", "client_proof": "p" * 43}
            saved.append(state)
            return state["client_id"], state["client_proof"]

    monkeypatch.setattr(sync_worker, "get_config_manager", lambda: FakeConfigManager())

    assert sync_worker._get_client_id() == "fresh-client-id"
    assert saved == [{"client_id": "fresh-client-id", "client_proof": "p" * 43}]


def test_get_client_id_fails_closed_when_fresh_default_cannot_be_saved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeConfigManager:
        cloudsave_local_state_path = tmp_path / "state" / "cloudsave_local_state.json"

        def ensure_cloudsave_client_credentials(self) -> tuple[str, str]:
            raise OSError("disk unavailable")

    monkeypatch.setattr(sync_worker, "get_config_manager", lambda: FakeConfigManager())

    assert sync_worker._get_client_id() is None


@pytest.mark.asyncio
async def test_register_client_sends_persisted_binding_proof(monkeypatch) -> None:
    sent: list[dict] = []

    class Response:
        status_code = 204
        text = ""

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, **kwargs):
            sent.append(kwargs["json"])
            return Response()

    monkeypatch.setattr(sync_worker.httpx, "AsyncClient", FakeAsyncClient)
    sync_worker._client_registered.clear()

    assert await sync_worker._ensure_client_registered(
        "https://community.example",
        "fresh-client-id",
        "p" * 43,
    )
    assert sent == [{"client_id": "fresh-client-id", "client_proof": "p" * 43}]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [201, 204])
async def test_post_facts_batch_accepts_empty_success_response(
    monkeypatch,
    status_code: int,
) -> None:
    class EmptySuccessResponse:
        text = ""

        def __init__(self) -> None:
            self.status_code = status_code

        def json(self):
            raise ValueError("empty response")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            assert url == "https://community.example/api/facts/sync"
            assert kwargs["headers"]["X-Client-Id"] == "client-id"
            return EmptySuccessResponse()

    monkeypatch.setattr(sync_worker.httpx, "AsyncClient", FakeAsyncClient)

    ok, payload = await sync_worker._post_facts_batch(
        "https://community.example",
        "client-id",
        "Lanlan",
        [{"fact_hash": "hash-12345678", "text": "safe", "importance": 0.8}],
    )

    assert ok is True
    assert payload is None


@pytest.mark.asyncio
async def test_post_facts_batch_invalidates_cached_registration_on_unauthorized(
    monkeypatch,
) -> None:
    class UnauthorizedResponse:
        status_code = 401
        text = "client not registered"

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, **_kwargs):
            return UnauthorizedResponse()

    monkeypatch.setattr(sync_worker.httpx, "AsyncClient", FakeAsyncClient)
    cache_key = "https://community.example|client-id"
    sync_worker._client_registered[cache_key] = True

    ok, payload = await sync_worker._post_facts_batch(
        "https://community.example",
        "client-id",
        "Lanlan",
        [{"fact_hash": "hash-12345678", "text": "safe", "importance": 0.8}],
    )

    assert ok is False
    assert payload is None
    assert cache_key not in sync_worker._client_registered


@pytest.mark.asyncio
async def test_failed_facts_remain_retryable_after_diagnostic_threshold(
    tmp_path,
    monkeypatch,
) -> None:
    lanlan_dir = tmp_path / "memory" / "Lanlan"
    lanlan_dir.mkdir(parents=True)
    (lanlan_dir / "facts.json").write_text(
        json.dumps(
            [{"hash": "retry-hash-12345678", "text": "private memory", "importance": 8}]
        ),
        encoding="utf-8",
    )

    async def fail_batch(*_args, **_kwargs):
        return False, None

    monkeypatch.setattr(sync_worker, "MAX_FAILED_ATTEMPTS", 2)
    monkeypatch.setattr(sync_worker, "_post_facts_batch", fail_batch)

    for _attempt in range(3):
        await sync_worker._sync_one_lanlan(
            tmp_path / "memory",
            lanlan_dir,
            "client-id",
            "https://community.example",
        )

    state = json.loads(
        (lanlan_dir / "facts_sync_state.json").read_text(encoding="utf-8")
    )
    assert state["synced"] == []
    assert state["failed_counts"] == {"retry-hash-12345678": 2}

    pending_lines = (
        lanlan_dir / "facts_sync_pending.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(pending_lines) == 1
    diagnostic = json.loads(pending_lines[0])
    assert diagnostic["fact_hash"] == "retry-hash-12345678"
    assert diagnostic["attempts"] == 2
    assert "text_preview" not in diagnostic
    assert "private memory" not in pending_lines[0]
