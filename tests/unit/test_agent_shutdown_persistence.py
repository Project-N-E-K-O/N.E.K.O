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
    from utils import token_tracker
    from utils.token_tracker import reporting

    monkeypatch.setattr(token_tracker, "get_config_manager", lambda: SimpleNamespace(config_dir=tmp_path))
    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", True)
    tracker = token_tracker.TokenTracker()
    atexit.unregister(tracker._atexit_save)
    tracker.record("test-model", 2, 1, 3)
    monkeypatch.setattr(token_tracker.TokenTracker, "get_instance", classmethod(lambda cls: tracker))
    events = []
    stopped = threading.Event()

    async def phase(name):
        events.append(name)
        if name == "host":
            stopped.set()
        # Like the real ``release_keys``: True when nothing is left held.
        return True if name == "keys" else None

    modules = SimpleNamespace(
        agent_bridge=SimpleNamespace(stop=lambda: phase("bridge")),
        _persistent_tasks=set(), _background_tasks=set(),
    )
    monkeypatch.setattr(api_runtime, "Modules", modules)
    provider = SimpleNamespace(
        shut_down=lambda: events.append("tools-stop"),
        browser_unclosed=lambda: False,
        kill_browser=lambda: events.append("browser-killed"),
        release_keys=lambda: phase("keys"),
        release_keys_unwaited=lambda: events.append("keys-unwaited"),
    )
    monkeypatch.setattr(api_runtime, "builtin_provider", provider)
    monkeypatch.setattr(api_runtime, "_loop_stall_watchdog", SimpleNamespace(stop=lambda: events.append("watchdog-stop")))
    timers = []

    def timer(seconds, callback):
        current = threading.Timer(seconds, callback)
        timers.append(current)
        return current

    monkeypatch.setattr(api_runtime, "threading", SimpleNamespace(Timer=timer))
    for name, label in [
        ("shutdown_nekoclaw_host", "host"),
        ("_ensure_plugin_lifecycle_stopped", "plugin"),
        ("_stop_embedded_user_plugin_server", "embedded"),
        ("_close_browser", "browser"),
        ("_emit_agent_status_update", "status"),
    ]:
        monkeypatch.setattr(api_runtime, name, lambda label=label: phase(label))
    fixture = SimpleNamespace(api=api_runtime, tracker=tracker, events=events, stopped=stopped, modules=modules, timers=timers)
    yield fixture
    for current in timers:
        current.cancel()
        current.join(2)


def assert_cleaned(fixture):
    assert fixture.events[:2] == ["tools-stop", "watchdog-stop"]
    assert set(fixture.events) >= {"host", "plugin", "embedded", "bridge", "browser", "keys", "status"}
    assert "keys-unwaited" not in fixture.events, "only for a skipped release step"
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
            assert fixture.events[:2] == ["tools-stop", "watchdog-stop"]
            assert set(fixture.events) >= {"host", "plugin", "embedded", "bridge", "browser", "keys"}
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


@pytest.mark.parametrize("name", ["shutdown_nekoclaw_host", "_ensure_plugin_lifecycle_stopped", "_close_browser"])
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


@pytest.mark.asyncio
async def test_browser_last_resort_survives_slow_host_cleanup(shutdown_fixture, monkeypatch):
    from config import nekoclaw_settings

    fixture = shutdown_fixture
    entered, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(nekoclaw_settings, "BROWSER_KILL_AT_SHUTDOWN_SECONDS", .05)

    async def host():
        entered.set()
        await release.wait()
        fixture.events.append("host")

    monkeypatch.setattr(fixture.api, "shutdown_nekoclaw_host", host)
    task = asyncio.create_task(fixture.api.shutdown())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        async with asyncio.timeout(2):
            while "browser-killed" not in fixture.events:
                await asyncio.sleep(.01)
        assert not task.done(), "the host must still be blocked when the timer fires"
        release.set()
        await asyncio.wait_for(task, 2)
        assert "browser" not in fixture.events, "do not reopen/close a browser after its kill deadline"
        assert "keys" in fixture.events and "status" in fixture.events
        assert fixture.tracker._storage_path.exists()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await fixture.tracker.asave()


@pytest.mark.asyncio
async def test_held_keys_are_released_before_a_later_step_spends_the_cancel_budget(shutdown_fixture, monkeypatch):
    from utils.shutdown_steps import ShutdownCancellationBudget

    fixture = shutdown_fixture
    monkeypatch.setattr(fixture.api, "ShutdownCancellationBudget", lambda seconds: ShutdownCancellationBudget(.02))
    entered, release = asyncio.Event(), asyncio.Event()

    async def plugin():
        # Ignores cancellation until released, spending the whole budget.
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setattr(fixture.api, "_ensure_plugin_lifecycle_stopped", plugin)
    task = asyncio.create_task(fixture.api.shutdown())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel("exit")
        done, _ = await asyncio.wait({task}, timeout=1)
        assert done
        assert "browser" not in fixture.events, "later steps are skipped once the budget is spent"
        assert fixture.events.index("keys") > fixture.events.index("host")
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await fixture.tracker.asave()


@pytest.mark.parametrize("first_result", [True, False])
@pytest.mark.asyncio
async def test_held_keys_are_tried_again_after_the_browser_only_when_left_held(shutdown_fixture, monkeypatch, first_result):
    fixture = shutdown_fixture
    results = iter([first_result, True])

    async def release_keys():
        fixture.events.append("keys")
        return next(results)

    monkeypatch.setattr(fixture.api.builtin_provider, "release_keys", release_keys)
    try:
        await asyncio.wait_for(fixture.api.shutdown(), 2)
        keys = [i for i, event in enumerate(fixture.events) if event == "keys"]
        if first_result:
            assert len(keys) == 1
        else:
            assert len(keys) == 2
            assert keys[0] < fixture.events.index("browser") < keys[1]
    finally:
        await fixture.tracker.asave()


@pytest.mark.asyncio
async def test_held_keys_are_not_tried_again_past_the_retry_deadline(shutdown_fixture, monkeypatch):
    from config import nekoclaw_settings

    fixture = shutdown_fixture
    monkeypatch.setattr(nekoclaw_settings, "KEYS_RETRY_UNTIL_SHUTDOWN_SECONDS", 0.0)

    async def release_keys():
        fixture.events.append("keys")
        return False

    monkeypatch.setattr(fixture.api.builtin_provider, "release_keys", release_keys)
    try:
        await asyncio.wait_for(fixture.api.shutdown(), 2)
        assert fixture.events.count("keys") == 1
        assert "status" in fixture.events, "skipping the retry goes on with the rest"
        assert fixture.tracker._storage_path.exists()
    finally:
        await fixture.tracker.asave()


@pytest.mark.asyncio
async def test_agent_submits_stats_after_cancel_budget_expires(shutdown_fixture, monkeypatch):
    from utils.shutdown_steps import ShutdownCancellationBudget

    fixture = shutdown_fixture
    monkeypatch.setattr(fixture.api, "ShutdownCancellationBudget", lambda seconds: ShutdownCancellationBudget(.02))
    entered, release = asyncio.Event(), asyncio.Event()
    submitted = []
    original_submit = fixture.tracker.submit_save

    def submit(**kwargs):
        future = original_submit(**kwargs)
        submitted.append(future)
        return future

    async def host():
        entered.set()
        await release.wait()

    monkeypatch.setattr(fixture.tracker, "submit_save", submit)
    monkeypatch.setattr(fixture.api, "shutdown_nekoclaw_host", host)
    task = asyncio.create_task(fixture.api.shutdown())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel("exit")
        done, _ = await asyncio.wait({task}, timeout=.5)
        assert done, "saving must not extend the shared shutdown budget"
        with pytest.raises(asyncio.CancelledError, match="exit"):
            task.result()
        assert len(submitted) == 1
        # The host took the whole budget: the release step was skipped, the
        # release itself was still handed off.
        assert "keys" not in fixture.events
        assert "keys-unwaited" in fixture.events
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if submitted:
            await asyncio.wrap_future(submitted[0])
    assert json.loads(fixture.tracker._storage_path.read_text())["recent_records"][0]["tt"] == 3
