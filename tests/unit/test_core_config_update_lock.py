"""core_config.json read-modify-write: concurrent writers must not lose each other's updates.

Every writer (``/core_api`` save, the two memory toggles, the startup openclaw
migration) goes through ``utils.config_manager.json_update``.  Each test below
parks one writer in the middle of its operation, lets another writer commit,
then checks that both changes and the unrelated fields survive.
"""

import ast
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from tests.fake_clock import patch_module_clock
from utils.config_manager import json_update
from utils.config_manager.storage_roots import StorageRootsMixin


pytestmark = pytest.mark.unit

FILENAME = "core_config.json"
INITIAL = {
    "coreApi": "qwen",
    "assistApi": "qwen",
    "coreApiKey": "old-key",
    "enableCustomApi": True,
    "recent_memory_auto_review": True,
    "powerful_memory_enabled": True,
    "unrelated": {"nested": "keep"},
}
CORE_API_PAYLOAD = {
    "coreApi": "qwen",
    "assistApi": "qwen",
    "enableCustomApi": True,
    "coreApiKey": "new-key",
}
CORRUPT_PAYLOADS = [b"{broken", b"[]", b"null", b"\xff"]
CORRUPT_IDS = ["broken-json", "array", "null", "bad-encoding"]


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _block_first_save(manager, monkeypatch):
    """Park the first save_json_config inside the locked section until released."""
    entered = threading.Event()
    release = threading.Event()
    original = manager.save_json_config
    calls = []

    def save(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 1:
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test never released the parked write")
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "save_json_config", save)
    return entered, release


async def _wait_thread_event(event, timeout=5):
    assert await asyncio.to_thread(event.wait, timeout), "parked writer never started"


# ---------------------------------------------------------------------------
# The entry point itself
# ---------------------------------------------------------------------------


@pytest.fixture
def light_manager(tmp_path):
    """Real file loader/saver without initializing or migrating a user root."""
    manager = StorageRootsMixin.__new__(StorageRootsMixin)
    manager.docs_dir = tmp_path
    manager.app_docs_dir = tmp_path
    manager.load_root_state = lambda: {"mode": "normal"}
    manager.config_dir = tmp_path / "config"
    manager.project_config_dir = tmp_path / "project-config"
    # cloudsave_writable_transaction needs a local state root next to the docs root.
    manager.anchor_root = tmp_path / "anchor"
    manager.config_dir.mkdir()
    manager.project_config_dir.mkdir()
    return manager


def test_update_starts_from_empty_only_when_file_is_missing(light_manager):
    path = light_manager.config_dir / FILENAME

    light_manager.update_json_config(FILENAME, lambda cfg: cfg.update(a=1))

    assert _read(path) == {"a": 1}


@pytest.mark.parametrize("payload", CORRUPT_PAYLOADS, ids=CORRUPT_IDS)
def test_update_refuses_to_overwrite_an_unreadable_file(light_manager, payload):
    path = light_manager.config_dir / FILENAME
    path.write_bytes(payload)
    calls = []

    with pytest.raises(Exception):
        light_manager.update_json_config(FILENAME, lambda cfg: calls.append(cfg))

    assert calls == [], "mutator must not run on a document that failed to load"
    assert path.read_bytes() == payload


def test_update_skips_the_write_when_nothing_changed(light_manager, monkeypatch):
    path = light_manager.config_dir / FILENAME
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    monkeypatch.setattr(light_manager, "save_json_config", lambda *a, **k: pytest.fail("no-op wrote"))

    assert light_manager.update_json_config(FILENAME, lambda cfg: cfg.get("a")) == 1


def test_noop_update_still_honours_the_maintenance_fence(light_manager):
    """A write request that happens to change nothing must not report success during
    a cloud restore: the restore may replace the file right after."""
    from utils.cloudsave_runtime import MaintenanceModeError

    path = light_manager.config_dir / FILENAME
    before = json.dumps({"a": 1}).encode()
    path.write_bytes(before)
    light_manager.load_root_state = lambda: {"mode": "maintenance_readonly"}

    with pytest.raises(MaintenanceModeError):
        light_manager.update_json_config(FILENAME, lambda cfg: cfg.update(a=1))
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_cancelled_async_update_waits_for_its_worker_before_unwinding(light_manager):
    """Cancellation cannot stop the worker thread; the caller must not unwind (and drop
    its request-level locks) until the write has actually landed."""
    path = light_manager.config_dir / FILENAME
    path.write_text(json.dumps({"unrelated": "keep"}), encoding="utf-8")
    entered = threading.Event()
    release = threading.Event()

    def slow(cfg):
        entered.set()
        assert release.wait(5)
        cfg["written"] = True

    task = asyncio.create_task(light_manager.aupdate_json_config(FILENAME, slow))
    try:
        await _wait_thread_event(entered)
        task.cancel()
        await asyncio.sleep(0.1)
        assert not task.done(), "cancellation unwound while the worker was still writing"
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert _read(path) == {"unrelated": "keep", "written": True}


def test_noop_update_still_materializes_the_runtime_copy_of_a_project_fallback(light_manager):
    """Saving a value equal to the bundled project config must still pin the user's copy:
    otherwise a later app update to the bundled file silently changes the saved setting."""
    project = light_manager.project_config_dir / FILENAME
    runtime = light_manager.config_dir / FILENAME
    project.write_text(json.dumps({"recent_memory_auto_review": False, "a": 1}), encoding="utf-8")
    before = project.read_bytes()

    light_manager.update_json_config(FILENAME, lambda cfg: cfg.update(recent_memory_auto_review=False))

    assert _read(runtime) == {"recent_memory_auto_review": False, "a": 1}
    assert project.read_bytes() == before


def test_noop_update_without_any_file_writes_nothing(light_manager):
    light_manager.update_json_config(FILENAME, lambda cfg: cfg.get("a"))

    assert not (light_manager.config_dir / FILENAME).exists()


def test_update_is_refused_while_a_cloud_restore_holds_the_apply_lock(light_manager):
    """The cloud-save transaction spans read-modify-write, so a restore cannot slip in."""
    from utils.cloudsave_runtime import MaintenanceModeError
    from utils.cloudsave_runtime.fence import acquire_cloud_apply_lock, release_cloud_apply_lock

    path = light_manager.config_dir / FILENAME
    before = json.dumps({"a": 1}).encode()
    path.write_bytes(before)
    held = threading.Event()
    done = threading.Event()

    def restore():
        assert acquire_cloud_apply_lock(light_manager)
        held.set()
        done.wait(5)
        release_cloud_apply_lock(light_manager)

    restorer = threading.Thread(target=restore)
    restorer.start()
    try:
        assert held.wait(5)
        with pytest.raises(MaintenanceModeError):
            light_manager.update_json_config(FILENAME, lambda cfg: cfg.update(a=2))
        assert path.read_bytes() == before
    finally:
        done.set()
        restorer.join(5)
    light_manager.update_json_config(FILENAME, lambda cfg: cfg.update(a=2))
    assert _read(path) == {"a": 2}


def test_snapshot_read_never_overlaps_a_locked_write(light_manager):
    """A decision snapshot waits for an in-flight write instead of racing its os.replace."""
    path = light_manager.config_dir / FILENAME
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    entered = threading.Event()
    release = threading.Event()

    def slow(cfg):
        entered.set()
        assert release.wait(5)
        cfg["a"] = 2

    writer = threading.Thread(target=light_manager.update_json_config, args=(FILENAME, slow))
    writer.start()
    try:
        assert entered.wait(5)
        result = []
        reader = threading.Thread(
            target=lambda: result.append(json_update.load_json_config_snapshot(light_manager, FILENAME))
        )
        reader.start()
        reader.join(0.2)
        assert reader.is_alive(), "snapshot read ran while the write was still in progress"
    finally:
        release.set()
        writer.join(5)
    reader.join(5)
    assert result == [{"a": 2}]


def test_update_distinguishes_true_from_legacy_one(light_manager):
    """``True == 1`` in Python; repairing a legacy 1 must still be written."""
    path = light_manager.config_dir / FILENAME
    path.write_text(json.dumps({"flag": 1}), encoding="utf-8")

    light_manager.update_json_config(FILENAME, lambda cfg: cfg.update(flag=True))

    assert _read(path) == {"flag": True}


def test_update_writes_nothing_when_mutator_raises(light_manager):
    path = light_manager.config_dir / FILENAME
    before = json.dumps({"a": 1}).encode()
    path.write_bytes(before)

    def mutator(cfg):
        cfg["a"] = 2
        raise ValueError("rejected")

    with pytest.raises(ValueError):
        light_manager.update_json_config(FILENAME, mutator)
    assert path.read_bytes() == before
    # 锁已释放：下一次更新照常完成
    light_manager.update_json_config(FILENAME, lambda cfg: cfg.update(a=3))
    assert _read(path) == {"a": 3}


def test_nested_update_of_the_same_file_fails_instead_of_deadlocking(light_manager):
    def outer(cfg):
        light_manager.update_json_config(FILENAME, lambda inner: inner.update(x=1))

    with pytest.raises(RuntimeError):
        light_manager.update_json_config(FILENAME, outer)


def test_threaded_increments_lose_no_updates(light_manager):
    path = light_manager.config_dir / FILENAME
    path.write_text(json.dumps({"counter": 0, "unrelated": "keep"}), encoding="utf-8")
    threads, per_thread = 6, 25
    barrier = threading.Barrier(threads)

    def bump(cfg):
        cfg["counter"] += 1

    def run():
        barrier.wait(5)
        for _ in range(per_thread):
            light_manager.update_json_config(FILENAME, bump)

    workers = [threading.Thread(target=run) for _ in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
    assert not any(worker.is_alive() for worker in workers)
    assert _read(path) == {"counter": threads * per_thread, "unrelated": "keep"}


@pytest.mark.asyncio
async def test_async_update_waits_for_a_sync_holder_without_blocking_the_loop(light_manager):
    """Sync (startup) and async callers share one lock; waiting never stalls the loop."""
    path = light_manager.config_dir / FILENAME
    path.write_text(json.dumps({"unrelated": "keep"}), encoding="utf-8")
    entered = threading.Event()
    release = threading.Event()

    def slow_sync(cfg):
        entered.set()
        assert release.wait(5)
        cfg["sync"] = True

    holder = threading.Thread(target=light_manager.update_json_config, args=(FILENAME, slow_sync))
    holder.start()
    try:
        await _wait_thread_event(entered)
        pending = asyncio.create_task(
            light_manager.aupdate_json_config(FILENAME, lambda cfg: cfg.update({"async": True}))
        )
        ticks = 0
        for _ in range(5):
            await asyncio.sleep(0.02)
            ticks += 1
        assert ticks == 5
        assert not pending.done(), "async update must wait for the sync holder"
    finally:
        release.set()
        holder.join(5)
    await asyncio.wait_for(pending, 5)
    assert _read(path) == {"unrelated": "keep", "sync": True, "async": True}


# ---------------------------------------------------------------------------
# Real writers against the real ConfigManager
# ---------------------------------------------------------------------------


@pytest.fixture()
def config_manager(clean_user_data_dir, monkeypatch):
    from utils import config_manager as config_manager_module

    manager = config_manager_module.get_config_manager("N.E.K.O")
    manager.config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager_module, "get_config_manager", lambda *a, **k: manager)
    path = manager.config_dir / FILENAME
    original = path.read_bytes() if path.exists() else None
    manager.save_json_config(FILENAME, INITIAL)
    manager._core_config_cache = None
    yield manager
    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(original)
    manager._core_config_cache = None


@pytest.fixture()
def core_config_router(monkeypatch):
    from main_routers.config_router import core_config

    async def _noop(*args, **kwargs):
        return None

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, *args, **kwargs):
            return None

    monkeypatch.setattr(core_config, "get_session_manager", lambda: {})
    monkeypatch.setattr(core_config, "get_initialize_character_data", lambda: _noop)
    monkeypatch.setattr(core_config, "ensure_default_yui_voice_for_free_api", _noop)
    monkeypatch.setattr(core_config, "_auto_resolve_provider_urls_for_save", _noop)
    monkeypatch.setattr(core_config, "_core_api_save_lock", asyncio.Lock())

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return core_config


@pytest.fixture()
def memory_router(monkeypatch):
    from main_routers import memory_router as module

    monkeypatch.setattr(module, "_memory_toggle_write_lock", asyncio.Lock())
    return module


def _stub_powerful_migration(monkeypatch, *, block: bool):
    started = asyncio.Event()
    release = asyncio.Event()

    async def migrate(*args, **kwargs):
        started.set()
        if block:
            await release.wait()
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "count": 1})

    from utils import internal_http_client

    monkeypatch.setattr(internal_http_client, "get_internal_http_client", lambda: SimpleNamespace(post=migrate))
    return started, release


def _assert_core_api_applied(saved):
    assert saved["coreApiKey"] == "new-key"
    assert saved["unrelated"] == {"nested": "keep"}


@pytest.mark.asyncio
async def test_core_api_save_keeps_a_memory_toggle_saved_during_url_resolution(
    config_manager, core_config_router, memory_router, monkeypatch
):
    """The reported bug: /core_api resolves provider URLs on an old snapshot while the
    memory page turns auto-review off; writing the snapshot back used to turn it on again."""
    path = config_manager.config_dir / FILENAME
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_resolve(core_cfg, checked=None):
        started.set()
        await release.wait()
        core_cfg["resolvedProviderUrls"] = {"core:qwen": "https://resolved.example/v1"}
        return {"total": 1}

    monkeypatch.setattr(core_config_router, "_auto_resolve_provider_urls_for_save", slow_resolve)

    save = asyncio.create_task(core_config_router.update_core_config(_FakeRequest(CORE_API_PAYLOAD)))
    try:
        await asyncio.wait_for(started.wait(), 5)
        toggled = await asyncio.wait_for(
            memory_router.update_review_config(_FakeRequest({"enabled": False})), 5
        )
        assert toggled == {"success": True, "enabled": False}
        assert _read(path)["recent_memory_auto_review"] is False
    finally:
        release.set()
    response = await asyncio.wait_for(save, 5)

    assert response["success"] is True
    saved = _read(path)
    assert saved["recent_memory_auto_review"] is False, "API save reverted the memory toggle"
    assert saved["resolvedProviderUrls"] == {"core:qwen": "https://resolved.example/v1"}
    assert response["resolvedProviderUrls"] == saved["resolvedProviderUrls"]
    _assert_core_api_applied(saved)
    assert saved["powerful_memory_enabled"] is True


@pytest.mark.asyncio
async def test_overlapping_core_api_saves_run_one_after_another(
    config_manager, core_config_router, monkeypatch
):
    """Derived fields (resolvedProviderUrls, key-book moves) come from the snapshot, so two
    /core_api saves must not be field-merged: the second one has to start from the first's result."""
    path = config_manager.config_dir / FILENAME
    first_parked = asyncio.Event()
    release_first = asyncio.Event()
    seen_snapshots = []

    async def resolve(core_cfg, checked=None):
        seen_snapshots.append(dict(core_cfg))
        if len(seen_snapshots) == 1:
            first_parked.set()
            await release_first.wait()
        core_cfg["resolvedProviderUrls"] = {"core:qwen": f"https://resolved-{len(seen_snapshots)}.example/v1"}
        return {"total": 1}

    monkeypatch.setattr(core_config_router, "_auto_resolve_provider_urls_for_save", resolve)

    first = asyncio.create_task(core_config_router.update_core_config(_FakeRequest(CORE_API_PAYLOAD)))
    second = None
    try:
        await asyncio.wait_for(first_parked.wait(), 5)
        second = asyncio.create_task(
            core_config_router.update_core_config(_FakeRequest({**CORE_API_PAYLOAD, "coreApiKey": "second-key", "openclawTimeout": 30}))
        )
        await asyncio.sleep(0.2)
        assert not second.done(), "a second /core_api save must wait for the one in flight"
        assert len(seen_snapshots) == 1, "the second save must not take its snapshot early"
    finally:
        release_first.set()
    assert (await asyncio.wait_for(first, 5))["success"] is True
    assert (await asyncio.wait_for(second, 5))["success"] is True

    assert seen_snapshots[1]["resolvedProviderUrls"] == {"core:qwen": "https://resolved-1.example/v1"}, (
        "second save did not start from the first's result"
    )
    saved = _read(path)
    assert saved["coreApiKey"] == "second-key"
    assert saved["openclawTimeout"] == 30
    assert saved["resolvedProviderUrls"] == {"core:qwen": "https://resolved-2.example/v1"}
    assert saved["unrelated"] == {"nested": "keep"}


@pytest.mark.asyncio
async def test_core_api_save_lock_is_released_once_the_file_is_written(
    config_manager, core_config_router, monkeypatch
):
    """Post-save work (client notify / end_session / reload) can hang on a slow socket;
    it must not hold off the next save."""
    path = config_manager.config_dir / FILENAME
    reload_parked = asyncio.Event()
    release_reload = asyncio.Event()
    reloads = []

    async def slow_reload():
        reloads.append(True)
        if len(reloads) == 1:
            reload_parked.set()
            await release_reload.wait()

    monkeypatch.setattr(core_config_router, "get_initialize_character_data", lambda: slow_reload)

    first = asyncio.create_task(core_config_router.update_core_config(_FakeRequest(CORE_API_PAYLOAD)))
    try:
        await asyncio.wait_for(reload_parked.wait(), 5)
        assert _read(path)["coreApiKey"] == "new-key"
        second = await asyncio.wait_for(
            core_config_router.update_core_config(_FakeRequest({**CORE_API_PAYLOAD, "coreApiKey": "second-key"})), 5
        )
        assert second["success"] is True
        assert _read(path)["coreApiKey"] == "second-key"
    finally:
        release_reload.set()
    assert (await asyncio.wait_for(first, 5))["success"] is True


@pytest.mark.asyncio
async def test_slow_request_body_does_not_hold_off_other_core_api_saves(
    config_manager, core_config_router
):
    path = config_manager.config_dir / FILENAME
    body_started = asyncio.Event()
    release_body = asyncio.Event()

    class _SlowBodyRequest:
        async def json(self):
            body_started.set()
            await release_body.wait()
            return {**CORE_API_PAYLOAD, "coreApiKey": "slow-key"}

    slow = asyncio.create_task(core_config_router.update_core_config(_SlowBodyRequest()))
    try:
        await asyncio.wait_for(body_started.wait(), 5)
        fast = await asyncio.wait_for(core_config_router.update_core_config(_FakeRequest(CORE_API_PAYLOAD)), 5)
        assert fast["success"] is True
        assert _read(path)["coreApiKey"] == "new-key"
    finally:
        release_body.set()
    assert (await asyncio.wait_for(slow, 5))["success"] is True
    assert _read(path)["coreApiKey"] == "slow-key"


@pytest.mark.asyncio
async def test_cancelled_core_api_save_keeps_the_save_lock_until_its_write_lands(
    config_manager, core_config_router, monkeypatch
):
    path = config_manager.config_dir / FILENAME
    entered, release = _block_first_save(config_manager, monkeypatch)
    snapshots = []

    async def record(core_cfg, checked=None):
        snapshots.append(dict(core_cfg))
        return {}

    monkeypatch.setattr(core_config_router, "_auto_resolve_provider_urls_for_save", record)

    first = asyncio.create_task(core_config_router.update_core_config(_FakeRequest(CORE_API_PAYLOAD)))
    second = None
    try:
        await _wait_thread_event(entered)
        assert len(snapshots) == 1
        first.cancel()
        second = asyncio.create_task(
            core_config_router.update_core_config(_FakeRequest({**CORE_API_PAYLOAD, "openclawTimeout": 30}))
        )
        await asyncio.sleep(0.2)
        assert len(snapshots) == 1, "second save took its snapshot before the cancelled save's write landed"
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, 5)
    assert (await asyncio.wait_for(second, 5))["success"] is True
    assert snapshots[1]["coreApiKey"] == "new-key"
    saved = _read(path)
    assert saved["coreApiKey"] == "new-key"
    assert saved["openclawTimeout"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,key", [
    ("review_config", "recent_memory_auto_review"),
    ("powerful_memory_config", "powerful_memory_enabled"),
])
async def test_noop_memory_toggle_is_refused_in_maintenance_mode(
    config_manager, memory_router, monkeypatch, endpoint, key
):
    from utils.cloudsave_runtime import MaintenanceModeError

    path = config_manager.config_dir / FILENAME
    before = path.read_bytes()
    assert _read(path)[key] is True
    monkeypatch.setattr(config_manager, "load_root_state", lambda: {"mode": "maintenance_readonly"})
    handler = getattr(memory_router, f"update_{endpoint}")

    with pytest.raises(MaintenanceModeError):
        await handler(_FakeRequest({"enabled": True}))
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_noop_core_api_save_is_refused_in_maintenance_mode(
    config_manager, core_config_router, monkeypatch
):
    from utils.cloudsave_runtime import MaintenanceModeError

    path = config_manager.config_dir / FILENAME
    before = path.read_bytes()
    monkeypatch.setattr(config_manager, "load_root_state", lambda: {"mode": "maintenance_readonly"})

    with pytest.raises(MaintenanceModeError):
        await core_config_router.update_core_config(_FakeRequest({**CORE_API_PAYLOAD, "coreApiKey": "old-key"}))
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_powerful_toggle_migration_does_not_block_or_clobber_a_core_api_save(
    config_manager, core_config_router, memory_router, monkeypatch
):
    """The slow memory_server migration runs outside the file lock: /core_api commits
    while it is pending, and the toggle then writes on top of the fresh file."""
    path = config_manager.config_dir / FILENAME
    started, release = _stub_powerful_migration(monkeypatch, block=True)

    toggle = asyncio.create_task(
        memory_router.update_powerful_memory_config(_FakeRequest({"enabled": False}))
    )
    try:
        await asyncio.wait_for(started.wait(), 5)
        response = await asyncio.wait_for(
            core_config_router.update_core_config(_FakeRequest(CORE_API_PAYLOAD)), 5
        )
        assert response["success"] is True
        assert _read(path)["powerful_memory_enabled"] is True, "toggle saved before its migration"
    finally:
        release.set()
    assert await asyncio.wait_for(toggle, 5) == {"success": True, "enabled": False}

    saved = _read(path)
    assert saved["powerful_memory_enabled"] is False
    _assert_core_api_applied(saved)
    assert saved["recent_memory_auto_review"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["review_config", "powerful_memory_config"])
async def test_core_api_save_waits_for_a_memory_toggle_write_in_progress(
    config_manager, core_config_router, memory_router, monkeypatch, endpoint
):
    """A toggle parked inside its locked write holds off /core_api, which then applies
    its own fields on top instead of on the pre-toggle snapshot."""
    path = config_manager.config_dir / FILENAME
    key = "recent_memory_auto_review" if endpoint == "review_config" else "powerful_memory_enabled"
    _stub_powerful_migration(monkeypatch, block=False)
    entered, release = _block_first_save(config_manager, monkeypatch)
    handler = getattr(memory_router, f"update_{endpoint}")

    toggle = asyncio.create_task(handler(_FakeRequest({"enabled": False})))
    save = None
    try:
        await _wait_thread_event(entered)
        save = asyncio.create_task(core_config_router.update_core_config(_FakeRequest(CORE_API_PAYLOAD)))
        await asyncio.sleep(0.2)
        assert not save.done(), "/core_api must wait for the toggle's locked write"
        assert _read(path) == INITIAL
    finally:
        release.set()
    assert await asyncio.wait_for(toggle, 5) == {"success": True, "enabled": False}
    assert (await asyncio.wait_for(save, 5))["success"] is True

    saved = _read(path)
    assert saved[key] is False, "/core_api wrote back the pre-toggle value"
    _assert_core_api_applied(saved)


@pytest.mark.asyncio
async def test_memory_toggle_waits_for_a_core_api_write_in_progress(
    config_manager, core_config_router, memory_router, monkeypatch
):
    path = config_manager.config_dir / FILENAME
    entered, release = _block_first_save(config_manager, monkeypatch)

    save = asyncio.create_task(core_config_router.update_core_config(_FakeRequest(CORE_API_PAYLOAD)))
    toggle = None
    try:
        await _wait_thread_event(entered)
        toggle = asyncio.create_task(memory_router.update_review_config(_FakeRequest({"enabled": False})))
        await asyncio.sleep(0.2)
        assert not toggle.done(), "the toggle must wait for /core_api's locked write"
    finally:
        release.set()
    assert (await asyncio.wait_for(save, 5))["success"] is True
    assert await asyncio.wait_for(toggle, 5) == {"success": True, "enabled": False}

    saved = _read(path)
    assert saved["recent_memory_auto_review"] is False
    _assert_core_api_applied(saved)


@pytest.mark.asyncio
async def test_startup_migration_and_memory_toggle_keep_both_changes(
    config_manager, memory_router, monkeypatch
):
    """The sync startup path shares the lock with the async request path."""
    path = config_manager.config_dir / FILENAME
    config_manager.save_json_config(FILENAME, {**INITIAL, "openclawUrl": "http://127.0.0.1:8089"})
    entered, release = _block_first_save(config_manager, monkeypatch)

    toggle = asyncio.create_task(memory_router.update_review_config(_FakeRequest({"enabled": False})))
    migration = None
    try:
        await _wait_thread_event(entered)
        migration = asyncio.create_task(asyncio.to_thread(config_manager.migrate_openclaw_url_port))
        await asyncio.sleep(0.2)
        assert not migration.done(), "the migration must wait for the toggle's locked write"
    finally:
        release.set()
    assert await asyncio.wait_for(toggle, 5) == {"success": True, "enabled": False}
    assert await asyncio.wait_for(migration, 5) is True

    saved = _read(path)
    assert saved["openclawUrl"] == "http://127.0.0.1:8088"
    assert saved["recent_memory_auto_review"] is False
    assert saved["unrelated"] == {"nested": "keep"}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", CORRUPT_PAYLOADS, ids=CORRUPT_IDS)
async def test_core_api_save_refuses_to_overwrite_a_corrupt_file(
    config_manager, core_config_router, monkeypatch, payload
):
    path = config_manager.config_dir / FILENAME
    path.write_bytes(payload)

    async def must_not_resolve(*args, **kwargs):
        pytest.fail("a corrupt file must be rejected before the (networked) URL resolution")

    monkeypatch.setattr(core_config_router, "_auto_resolve_provider_urls_for_save", must_not_resolve)

    response = await core_config_router.update_core_config(_FakeRequest(CORE_API_PAYLOAD))

    assert response["success"] is False
    assert path.read_bytes() == payload


@pytest.mark.parametrize("payload", CORRUPT_PAYLOADS, ids=CORRUPT_IDS)
def test_startup_migration_leaves_a_corrupt_file_alone(config_manager, monkeypatch, payload):
    from utils.config_manager import core_config as core_config_module

    path = config_manager.config_dir / FILENAME
    path.write_bytes(payload)
    patch_module_clock(monkeypatch, core_config_module, sleep=lambda _s: pytest.fail("retried a corrupt file"))

    assert config_manager.migrate_openclaw_url_port() is False
    assert path.read_bytes() == payload


_WRITE_HELPERS = {
    "save_json_config", "atomic_write_json", "atomic_write_json_async",
    "atomic_write_text", "atomic_write_text_async", "atomic_write_bytes",
}


def _call_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _core_config_writes(source: str) -> list[int]:
    """Line numbers of write-helper calls whose arguments point at core_config.json.

    A call counts when it is one of ``_WRITE_HELPERS`` (directly, or handed to
    ``to_thread`` / ``run_in_executor``) and its arguments contain the
    ``"core_config.json"`` literal or a name assigned that literal anywhere in
    the module.  A path assembled from a loop variable is beyond a static scan.
    """
    tree = ast.parse(source)
    aliases = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(node.value, ast.Constant) and node.value.value == FILENAME
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }

    def mentions_core_config(nodes):
        return any(
            (isinstance(sub, ast.Constant) and sub.value == FILENAME)
            or (isinstance(sub, ast.Name) and sub.id in aliases)
            for node in nodes for sub in ast.walk(node)
        )

    hits = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        name = _call_name(call.func)
        args = [*call.args, *(kw.value for kw in call.keywords)]
        if name in ("to_thread", "run_in_executor"):
            fn_index = 0 if name == "to_thread" else 1
            if len(call.args) <= fn_index or _call_name(call.args[fn_index]) not in _WRITE_HELPERS:
                continue
            args = args[fn_index + 1:]
        elif name not in _WRITE_HELPERS:
            continue
        if mentions_core_config(args):
            hits.append(call.lineno)
    return hits


@pytest.mark.parametrize("snippet,expected", [
    ("cm.save_json_config('core_config.json', data)", 1),
    ("CORE = 'core_config.json'; cm.save_json_config(CORE, data)", 1),
    ("atomic_write_json(cfg_dir / 'core_config.json', data)", 1),
    ("await asyncio.to_thread(cm.save_json_config, 'core_config.json', data)", 1),
    ("loop.run_in_executor(None, atomic_write_json, path_for('core_config.json'), d)", 1),
    ("cm.load_json_config('core_config.json')", 0),
    ("cm.save_json_config('voice_storage.json', data)", 0),
    ("await asyncio.to_thread(cm.load_json_config, 'core_config.json')", 0),
])
def test_core_config_write_scanner_recognises_bypass_shapes(snippet, expected):
    """Keep the guard below honest: it must see the usual ways around the entry point."""
    source = "async def f():" + chr(10) + "    " + snippet + chr(10)
    assert len(_core_config_writes(source)) == expected


def test_every_core_config_writer_goes_through_the_locked_entry_point():
    """No production code may write core_config.json outside json_update."""
    from pathlib import Path

    root = Path(json_update.__file__).resolve().parents[2]
    sources = [*root.glob("*.py")]
    for package in ("app", "brain", "config", "main_logic", "main_routers", "memory", "plugin", "utils"):
        sources.extend(path for path in (root / package).rglob("*.py") if "tests" not in path.parts)
    assert len(sources) > 100, "source scan found almost nothing; the guard would pass vacuously"
    offenders = []
    for path in sources:
        try:
            hits = _core_config_writes(path.read_bytes().decode("utf-8-sig"))
        except (SyntaxError, ValueError, UnicodeDecodeError):
            continue
        offenders.extend(f"{path.relative_to(root).as_posix()}:{line}" for line in hits)
    assert offenders == []
