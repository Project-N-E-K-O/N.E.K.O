import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Response

from main_logic.topic.recommendation.store import RecommendationStore
from main_logic.topic.recommendation import maintenance
from config.topic_recommendation_settings import TopicRecommendationSettings
from main_routers import storage_location_router as routes


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    yield


async def wait_for_event_or_failure(event, task):
    """Expose an early task error instead of hanging on its missing event."""
    waiter = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait({waiter, task}, timeout=5,
                                    return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
            pytest.fail("The tested task finished before reaching its barrier")
        assert waiter in done, "The tested task did not reach its barrier within 5 seconds"
        await waiter
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.fixture
def bound_owner(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    anchor = tmp_path / "anchor"
    (anchor / "state").mkdir(parents=True)
    fence = anchor / "state" / "root_state.json"
    def write_mode(mode):
        fence.write_text(json.dumps({"version": 1, "mode": mode, "current_root": str(runtime)}), encoding="utf-8")
    write_mode("normal")
    manager = SimpleNamespace(app_docs_dir=runtime, committed_selected_root=runtime,
                              recovery_committed_root_unavailable=False,
                              recovery_committed_root_unavailable_override=False,
                              root_state_path=fence, ROOT_STATE_VERSION=1)
    changes = []
    service = SimpleNamespace(store=RecommendationStore.for_config_manager(manager),
                              settings=TopicRecommendationSettings(),
                              set_maintenance=changes.append,
                              flush_publications=AsyncMock(),
                              recover_after_maintenance=AsyncMock())
    current = [service]
    monkeypatch.setattr(maintenance, "get_recommendation_service", lambda: current[0])
    return service, current, changes, write_mode


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,inner", [
    ("post_storage_location_select", "_post_storage_location_select_locked"),
    ("post_storage_location_restart", "_post_storage_location_restart_locked"),
])
async def test_actual_route_pauses_before_entry_and_resumes_only_valid_root(bound_owner, monkeypatch, endpoint, inner):
    service, _, changes, _ = bound_owner
    async def operation(*_args):
        assert changes == [True]
        service.flush_publications.assert_awaited_once_with(allow_maintenance=True)
        await asyncio.sleep(0)
        assert changes == [True]
        return {"ok": True}
    monkeypatch.setattr(routes, inner, operation)
    result = await getattr(routes, endpoint)(None, Response())
    assert result == {"ok": True}
    assert changes == [True, False]
    service.recover_after_maintenance.assert_awaited_once_with()
    await service.store.close()


@pytest.mark.asyncio
async def test_pending_restart_never_resumes_on_http_success(bound_owner):
    service, _, changes, write_mode = bound_owner
    async def accepted():
        write_mode("maintenance_readonly")
        return {"ok": True, "result": "restart_initiated"}
    assert (await routes._run_recommendation_storage_mutation(accepted))["ok"]
    assert changes == [True]
    await service.store.close()


@pytest.mark.asyncio
async def test_failed_request_with_bad_recovery_fence_stays_paused(bound_owner):
    service, _, changes, write_mode = bound_owner
    async def failure():
        write_mode("deferred_init")
        raise OSError("controlled write failure")
    with pytest.raises(OSError):
        await routes._run_recommendation_storage_mutation(failure)
    assert changes == [True]
    await service.store.close()


@pytest.mark.asyncio
async def test_restored_root_after_rollback_releases_pause(bound_owner):
    service, _, changes, write_mode = bound_owner
    async def rolled_back():
        write_mode("maintenance_readonly")
        write_mode("normal")
        return {"ok": False}
    assert not (await routes._run_recommendation_storage_mutation(rolled_back))["ok"]
    assert changes == [True, False]
    await service.store.close()


@pytest.mark.asyncio
async def test_old_root_operation_never_resumes_replacement_owner(bound_owner):
    service, current, changes, _ = bound_owner
    replacement_changes = []
    async def replaced():
        current[0] = SimpleNamespace(set_maintenance=replacement_changes.append)
        return {"ok": False}
    await routes._run_recommendation_storage_mutation(replaced)
    assert changes == [True]
    assert not replacement_changes
    await service.store.close()


@pytest.mark.asyncio
async def test_route_without_feature_has_original_result(monkeypatch):
    monkeypatch.setattr(maintenance, "get_recommendation_service", lambda: None)
    async def operation():
        return "original"
    assert await routes._run_recommendation_storage_mutation(operation) == "original"


@pytest.mark.asyncio
async def test_independent_storage_and_cloud_claims_do_not_release_each_other(bound_owner):
    service, _, changes, _ = bound_owner
    first_entered, second_entered = asyncio.Event(), asyncio.Event()
    first_release, second_release = asyncio.Event(), asyncio.Event()
    async def transaction(entered, release):
        async with maintenance.recommendation_maintenance():
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=5)
    first = asyncio.create_task(transaction(first_entered, first_release))
    await wait_for_event_or_failure(first_entered, first)
    second = asyncio.create_task(transaction(second_entered, second_release))
    await wait_for_event_or_failure(second_entered, second)
    first_release.set()
    await asyncio.wait_for(first, timeout=5)
    assert changes == [True, True]
    second_release.set()
    await asyncio.wait_for(second, timeout=5)
    assert changes == [True, True, False]
    assert not maintenance._owners
    await service.store.close()


@pytest.mark.asyncio
async def test_old_fence_read_cannot_override_new_recovery_failure(bound_owner, monkeypatch):
    service, _, changes, _ = bound_owner
    first_read, finish_first_read = asyncio.Event(), asyncio.Event()
    reads = 0
    async def controlled_ready():
        nonlocal reads
        reads += 1
        if reads == 1:
            first_read.set()
            await asyncio.wait_for(finish_first_read.wait(), timeout=5)
            return True
        return False
    monkeypatch.setattr(service.store, "root_ready", controlled_ready)
    async def transaction():
        async with maintenance.recommendation_maintenance():
            pass
    first = asyncio.create_task(transaction())
    await wait_for_event_or_failure(first_read, first)
    await asyncio.wait_for(transaction(), timeout=5)
    finish_first_read.set()
    await asyncio.wait_for(first, timeout=5)
    assert changes == [True, True]
    assert not maintenance._owners
    await service.store.close()


@pytest.mark.asyncio
async def test_actual_cloud_download_pauses_before_async_fence_and_restores_strict_root(bound_owner, monkeypatch):
    from main_routers import cloudsave_router as cloud
    service, _, changes, write_mode = bound_owner
    entered, release = asyncio.Event(), asyncio.Event()
    @asynccontextmanager
    async def fence(*_args, **_kwargs):
        assert changes == [True], "pause must precede the fence's first await"
        service.flush_publications.assert_awaited_once_with(allow_maintenance=True)
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        write_mode("bootstrap_importing")
        try:
            yield
        finally:
            write_mode("normal")
    async def request_json():
        return {}
    async def completion(*_args, **_kwargs):
        assert changes == [True]
        return None
    async def enrich(value):
        return value
    monkeypatch.setattr(cloud, "get_config_manager", lambda: object())
    monkeypatch.setattr(cloud, "is_cloudsave_provider_available", lambda _cm: True)
    monkeypatch.setattr(cloud, "_local_character_exists", lambda *_args: False)
    monkeypatch.setattr(cloud, "_active_session_block_reason", lambda *_args: None)
    monkeypatch.setattr(cloud, "async_cloud_apply_fence", fence)
    monkeypatch.setattr(cloud, "import_cloudsave_character_unit", lambda *_args, **_kw: {"detail": {}})
    monkeypatch.setattr(cloud, "_complete_cloudsave_character_download", completion)
    monkeypatch.setattr(cloud, "build_cloudsave_character_detail", lambda *_args: {})
    monkeypatch.setattr(cloud, "_enrich_cloudsave_payload_with_workshop_status", enrich)
    monkeypatch.setattr(cloud, "_build_steam_autocloud_payload", lambda *_args: {})
    downloading = asyncio.create_task(cloud.post_cloudsave_character_download("A", SimpleNamespace(json=request_json)))
    await wait_for_event_or_failure(entered, downloading)
    assert changes == [True]
    assert not downloading.done()
    release.set()
    result = await asyncio.wait_for(downloading, timeout=5)
    assert result["success"]
    assert changes == [True, False]
    service.recover_after_maintenance.assert_awaited_once_with()
    assert not maintenance._owners
    await service.store.close()
