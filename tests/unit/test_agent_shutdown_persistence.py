"""Real Agent shutdown keeps cleanup independent of stats IO and cancellation."""

import asyncio
import atexit
import json
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture
def shutdown_fixture(tmp_path, monkeypatch):
    from app.agent_server import api_runtime
    import utils.token_tracker as token_tracker
    from utils.token_tracker import reporting

    monkeypatch.setattr(token_tracker, "get_config_manager", lambda: SimpleNamespace(config_dir=tmp_path))
    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", True)
    tracker = token_tracker.TokenTracker()
    atexit.unregister(tracker._atexit_save)
    tracker.record("test-model", 2, 1, 3)
    monkeypatch.setattr(token_tracker.TokenTracker, "get_instance", classmethod(lambda cls: tracker))
    events = []
    stopped = threading.Event()

    def stop(name):
        events.append(name)
        stopped.set()

    async def phase(name):
        events.append(name)

    modules = SimpleNamespace(
        computer_use=SimpleNamespace(cancel_running=lambda: stop("computer-stop"), wait_for_completion=lambda timeout: stopped.wait(timeout)),
        browser_use=SimpleNamespace(cancel_running=lambda: stop("browser-stop")),
        task_executor=SimpleNamespace(router=SimpleNamespace(aclose=lambda: phase("router"))),
        agent_bridge=SimpleNamespace(stop=lambda: phase("bridge")),
        _persistent_tasks=set(), _background_tasks=set(), active_computer_use_async_task=None,
    )
    monkeypatch.setattr(api_runtime, "Modules", modules)
    for name, label in [
        ("_ensure_plugin_lifecycle_stopped", "plugin"),
        ("_stop_embedded_user_plugin_server", "embedded"),
        ("_close_browser_use_adapter", "browser"),
        ("_emit_agent_status_update", "status"),
    ]:
        monkeypatch.setattr(api_runtime, name, lambda label=label: phase(label))
    return SimpleNamespace(api=api_runtime, tracker=tracker, events=events, stopped=stopped, modules=modules)


def assert_cleaned(fixture):
    assert fixture.events[:2] == ["computer-stop", "browser-stop"]
    assert set(fixture.events) >= {"plugin", "embedded", "router", "bridge", "browser", "status"}
    assert fixture.modules.agent_bridge is None
    assert not fixture.modules._persistent_tasks
    assert not fixture.modules._background_tasks


@pytest.mark.asyncio
async def test_agent_shutdown_releases_full_default_pool_before_saving(shutdown_fixture):
    fixture = shutdown_fixture
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: None)
    count = loop._default_executor._max_workers
    entered = threading.Barrier(count + 1)

    def work():
        entered.wait(5)
        fixture.stopped.wait(5)

    workers = [loop.run_in_executor(None, work) for _ in range(count)]
    task = None
    try:
        entered.wait(5)
        task = asyncio.create_task(fixture.api.shutdown())
        await asyncio.wait_for(asyncio.shield(task), 2)
        assert_cleaned(fixture)
        assert fixture.tracker._storage_path.exists()
    finally:
        fixture.stopped.set()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*workers)
        await fixture.tracker.asave()


@pytest.mark.asyncio
async def test_file_lock_and_repeated_cancellation_preserve_cleanup_and_disk(shutdown_fixture):
    from utils.token_tracker._shared import _file_lock
    from utils.token_tracker import TokenTracker

    fixture = shutdown_fixture
    tracker = fixture.tracker
    tracker._storage_dir.mkdir(parents=True, exist_ok=True)
    task = None
    try:
        with _file_lock(tracker._lock_file_path):
            task = asyncio.create_task(fixture.api.shutdown())
            # save swaps out dirty deltas immediately before taking the real
            # file lock. It cannot finish until this lock is released.
            async with asyncio.timeout(3):
                while tracker._dirty:
                    await asyncio.sleep(.01)
            assert not task.done()
            assert fixture.events[:2] == ["computer-stop", "browser-stop"]
            assert set(fixture.events) >= {"plugin", "embedded", "router", "bridge", "browser"}
            task.cancel("first")
            await asyncio.sleep(.02)
            task.cancel("second")
            await asyncio.sleep(.02)
            assert not task.done(), "the save thread still owns unpersisted deltas"
        with pytest.raises(asyncio.CancelledError, match="first"):
            await asyncio.wait_for(task, 2)
        assert_cleaned(fixture)
        persisted = json.loads(tracker._storage_path.read_text())
        assert persisted["recent_records"][0]["tt"] == 3
        restored = TokenTracker()
        atexit.unregister(restored._atexit_save)
        restored.save()
        assert json.loads(tracker._storage_path.read_text()) == persisted
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # Drain the independent save worker before deleting the fixture files.
        await tracker.asave()


@pytest.mark.parametrize("name", ["_ensure_plugin_lifecycle_stopped", "_close_browser_use_adapter"])
@pytest.mark.asyncio
async def test_cancellation_during_cleanup_still_runs_later_steps(shutdown_fixture, monkeypatch, name):
    fixture = shutdown_fixture
    entered, release = asyncio.Event(), asyncio.Event()
    original = getattr(fixture.api, name)

    async def blocked():
        entered.set()
        await release.wait()
        await original()

    async def background():
        try:
            await asyncio.Event().wait()
        finally:
            fixture.events.append("background-stopped")

    worker = asyncio.create_task(background())
    fixture.modules._background_tasks.add(worker)
    await asyncio.sleep(0)
    monkeypatch.setattr(fixture.api, name, blocked)
    task = asyncio.create_task(fixture.api.shutdown())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert_cleaned(fixture)
        assert "background-stopped" in fixture.events
        assert fixture.tracker._storage_path.exists()
    finally:
        release.set()
        task.cancel()
        worker.cancel()
        await asyncio.gather(task, worker, return_exceptions=True)
        await fixture.tracker.asave()
