import asyncio
import copy
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_logic.topic.recommendation.contracts import RecommendationError
from main_logic.topic.recommendation.store import MAX_STATE_BYTES, RecommendationStore

CAT = "character_" + "a" * 32
OTHER_CAT = "character_" + "b" * 32


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    yield


@pytest.fixture
def root(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    return runtime


def path_for(root, cat=CAT):
    return root / "state" / "recommendation" / cat / "state.json"


async def save(store, state, guard=None):
    return await store.commit(CAT, state, expected_epoch=state["state_epoch"],
                              expected_revision=state["revision"], guard=guard)


@pytest.mark.asyncio
async def test_read_missing_has_stable_epoch_without_filesystem_side_effect(root):
    store = RecommendationStore(lambda: root)
    state = await store.load(CAT)
    assert state == await store.load(CAT)
    state["subjects"].append({"id": "caller mutation"})
    assert not (await store.load(CAT))["subjects"]
    assert not (root / "state").exists()
    await store.close()


@pytest.mark.asyncio
async def test_atomic_restart_cas_and_character_isolation(root):
    store = RecommendationStore(lambda: root)
    initial = await store.load(CAT)
    state = copy.deepcopy(initial)
    state["subjects"] = [{"id": "drawing", "summary": "finish a drawing"}]
    committed = await save(store, state)
    assert committed["revision"] == 1
    with pytest.raises(RecommendationError, match="revision_conflict"):
        await save(store, initial)
    other = await store.load(OTHER_CAT)
    assert not other["subjects"]
    assert not path_for(root, OTHER_CAT).exists()
    await store.close()
    restarted = RecommendationStore(lambda: root)
    assert await restarted.load(CAT) == committed
    assert restarted.root_generation != store.root_generation
    await restarted.close()


@pytest.mark.asyncio
async def test_reset_is_persistent_idempotent_and_old_commit_cannot_revive(root):
    store = RecommendationStore(lambda: root)
    old = await store.load(CAT)
    old["restrictions"] = [{"id": "r", "scope": "drawing"}]
    old = await save(store, old)
    receipt = await store.reset(CAT, expected_epoch=old["state_epoch"], request_id="reset-one")
    assert receipt["state_epoch"] != old["state_epoch"]
    assert receipt == await store.reset(CAT, expected_epoch=old["state_epoch"], request_id="reset-one")
    with pytest.raises(RecommendationError, match="epoch_conflict"):
        await store.reset(CAT, expected_epoch=old["state_epoch"], request_id="reset-two")
    with pytest.raises(RecommendationError, match="epoch_conflict"):
        await save(store, old)
    await store.close()
    restarted = RecommendationStore(lambda: root)
    assert receipt == await restarted.reset(CAT, expected_epoch=old["state_epoch"], request_id="reset-one")
    assert not (await restarted.load(CAT))["restrictions"]
    await restarted.close()


@pytest.mark.asyncio
async def test_same_request_with_different_input_conflicts(root):
    store = RecommendationStore(lambda: root)
    initial = await store.load(CAT)
    receipt = await store.reset(CAT, expected_epoch=initial["state_epoch"], request_id="one")
    with pytest.raises(RecommendationError, match="epoch_conflict"):
        await store.reset(CAT, expected_epoch=receipt["state_epoch"], request_id="one")
    await store.close()


@pytest.mark.asyncio
async def test_invalid_and_unreadable_state_is_never_overwritten(root, monkeypatch):
    store = RecommendationStore(lambda: root)
    state = await store.load(CAT)
    target = path_for(root)
    target.parent.mkdir(parents=True)
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(RecommendationError, match="state_corrupt"):
        await save(store, state)
    assert target.read_text() == "{broken"
    with pytest.raises(RecommendationError, match="state_corrupt"):
        await store.reset(CAT, expected_epoch=state["state_epoch"], request_id="one")
    target.write_text(json.dumps(state), encoding="utf-8")
    original_open = os.open
    def denied(path, *args, **kwargs):
        if Path(path) == target:
            raise PermissionError("fixture")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await save(store, state)
    assert json.loads(target.read_text())["revision"] == 0
    await store.close()


@pytest.mark.asyncio
async def test_whole_serialized_state_and_record_limits(root):
    store = RecommendationStore(lambda: root)
    state = await store.load(CAT)
    state["opaque"] = "x" * MAX_STATE_BYTES
    with pytest.raises(RecommendationError, match="capacity_exhausted"):
        await save(store, state)
    assert not path_for(root).exists()
    state.pop("opaque")
    state["restrictions"] = [{"id": str(index)} for index in range(129)]
    with pytest.raises(RecommendationError, match="capacity_exhausted"):
        await save(store, state)
    assert not path_for(root).exists()
    target = path_for(root)
    target.write_bytes(b" " * (MAX_STATE_BYTES + 1))
    with pytest.raises(RecommendationError, match="capacity_exhausted"):
        await store.load(CAT)
    assert target.stat().st_size == MAX_STATE_BYTES + 1
    await store.close()


@pytest.mark.asyncio
async def test_capacity_rejection_preserves_all_existing_restrictions(root):
    store = RecommendationStore(lambda: root)
    state = await store.load(CAT)
    state["restrictions"] = [{"subject_id": str(index), "scope": "subject", "expires_at": 9999999999}
                             for index in range(128)]
    current = await save(store, state)
    proposed = copy.deepcopy(current)
    proposed["restrictions"].append({"subject_id": "new", "scope": "subject", "expires_at": 9999999999})
    with pytest.raises(RecommendationError, match="capacity_exhausted"):
        await save(store, proposed)
    assert await store.load(CAT) == current
    await store.close()


@pytest.mark.asyncio
async def test_missing_selected_root_never_creates_anchor_fallback(tmp_path):
    missing = tmp_path / "missing-selected"
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    store = RecommendationStore(lambda: missing)
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await store.load(CAT)
    assert not missing.exists()
    assert not tuple(anchor.iterdir())
    await store.close()


@pytest.mark.asyncio
async def test_root_change_and_maintenance_fail_closed(root, tmp_path):
    selected = [root]
    writable = [True]
    store = RecommendationStore(lambda: selected[0], lambda: writable[0])
    state = await store.load(CAT)
    writable[0] = False
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await save(store, state)
    writable[0] = True
    next_root = tmp_path / "new"
    next_root.mkdir()
    selected[0] = next_root
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await save(store, state)
    assert not tuple(next_root.iterdir())
    await store.close()


@pytest.mark.asyncio
async def test_deleted_identity_cannot_be_recreated_by_late_worker(root):
    store = RecommendationStore(lambda: root)
    state = await store.load(CAT)
    await save(store, state)
    await store.delete(CAT)
    with pytest.raises(RecommendationError, match="character_deleted"):
        await save(store, state)
    assert not path_for(root).exists()
    assert (await store.load(OTHER_CAT))["character_id"] == OTHER_CAT
    await store.close()


@pytest.mark.asyncio
async def test_delete_missing_character_does_not_create_directories(root):
    store = RecommendationStore(lambda: root)
    await store.delete(CAT)
    assert not (root / "state").exists()
    with pytest.raises(RecommendationError, match="character_deleted"):
        await store.load(CAT)
    await store.close()


@pytest.mark.asyncio
async def test_reset_and_delete_remove_only_known_crash_temporaries(root):
    store = RecommendationStore(lambda: root)
    original = await save(store, await store.load(CAT))
    directory = path_for(root).parent
    stale = directory / f".{CAT}.{'c' * 32}.tmp"
    stale.write_text("unpublished previous personal summary")
    unrelated = directory / "unrelated.txt"
    unrelated.write_text("outside the module contract")
    await store.reset(CAT, expected_epoch=original["state_epoch"], request_id="clean")
    assert not stale.exists()
    assert unrelated.exists()
    stale.write_text("crash after another unpublished write")
    await store.delete(CAT)
    assert not stale.exists()
    assert not path_for(root).exists()
    assert unrelated.exists()
    await store.close()


@pytest.mark.asyncio
async def test_reset_cleanup_failure_does_not_report_success_or_overwrite_state(root, monkeypatch):
    store = RecommendationStore(lambda: root)
    original = await save(store, await store.load(CAT))
    stale = path_for(root).parent / f".{CAT}.{'c' * 32}.tmp"
    stale.write_text("previous personal summary")
    actual = Path.unlink
    def denied(self, *args, **kwargs):
        if self == stale:
            raise PermissionError("controlled temporary cleanup failure")
        return actual(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await store.reset(CAT, expected_epoch=original["state_epoch"], request_id="clean")
    assert await store.load(CAT) == original
    assert stale.exists()
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["../outside", "", "character_a", "character_" + "A" * 32])
async def test_invalid_identity_never_becomes_path(root, identifier):
    store = RecommendationStore(lambda: root)
    with pytest.raises(RecommendationError, match="invalid_character_id"):
        await store.load(identifier)
    assert not tuple(root.iterdir())
    await store.close()


@pytest.mark.asyncio
async def test_second_writer_and_real_cross_process_lock(root):
    store = RecommendationStore(lambda: root)
    state = await save(store, await store.load(CAT))
    competitor = RecommendationStore(lambda: root)
    same = await competitor.load(CAT)
    with pytest.raises(RecommendationError, match="writer_unavailable"):
        await save(competitor, same)
    code = '''import os, sys
f=open(sys.argv[1], "r+b", buffering=0)
try:
 if os.name=="nt":
  import msvcrt
  msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
 else:
  import fcntl
  fcntl.flock(f.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
except OSError:
 sys.exit(23)
sys.exit(0)
'''
    lock = str(path_for(root).parent.parent / ".writer.lock")
    result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", code, lock], capture_output=True)
    assert result.returncode == 23, result.stderr
    await store.close()
    result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", code, lock], capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (await save(competitor, state))["revision"] == 2
    await competitor.close()


def fsync_barrier(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    actual = os.fsync
    def block(fd):
        entered.set()
        assert release.wait(5), "controlled test barrier timed out"
        actual(fd)
    monkeypatch.setattr(os, "fsync", block)
    return entered, release


@pytest.mark.asyncio
async def test_disable_during_physical_write_prevents_atomic_publication(root, monkeypatch):
    store = RecommendationStore(lambda: root)
    state = await store.load(CAT)
    enabled = [True]
    entered, release = fsync_barrier(monkeypatch)
    task = asyncio.create_task(save(store, state, lambda: enabled[0]))
    assert await asyncio.to_thread(entered.wait, 5)
    enabled[0] = False
    release.set()
    with pytest.raises(RecommendationError, match="stale_operation"):
        await task
    assert not path_for(root).exists()
    assert not list(path_for(root).parent.glob("*.tmp"))
    await store.close()


@pytest.mark.asyncio
async def test_cancel_close_waits_for_physical_worker_before_writer_handoff(root, monkeypatch):
    store = RecommendationStore(lambda: root)
    state = await store.load(CAT)
    entered, release = fsync_barrier(monkeypatch)
    task = asyncio.create_task(save(store, state))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    closing = asyncio.create_task(store.close())
    await asyncio.sleep(0)
    assert not closing.done()
    competitor = RecommendationStore(lambda: root)
    competitor_state = await competitor.load(CAT)
    with pytest.raises(RecommendationError, match="writer_unavailable"):
        await save(competitor, competitor_state)
    release.set()
    await closing
    assert not path_for(root).exists()
    # The owner released its physical writer; the other incarnation can commit.
    await save(competitor, competitor_state)
    await competitor.close()


@pytest.mark.asyncio
async def test_replace_failure_leaves_original_state_and_no_partial_file(root, monkeypatch):
    store = RecommendationStore(lambda: root)
    previous = await save(store, await store.load(CAT))
    def fail(*_args):
        raise OSError("fixture replace failure")
    monkeypatch.setattr(os, "replace", fail)
    updated = copy.deepcopy(previous)
    updated["subjects"] = [{"id": "new"}]
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await save(store, updated)
    assert await store.load(CAT) == previous
    assert not list(path_for(root).parent.glob("*.tmp"))
    await store.close()


@pytest.mark.asyncio
async def test_linked_parent_is_rejected(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "state").symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        result = await asyncio.to_thread(subprocess.run,
            ["cmd", "/c", "mklink", "/J", str(root / "state"), str(outside)], capture_output=True)
        assert result.returncode == 0, result.stderr
    store = RecommendationStore(lambda: root)
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await store.load(CAT)
    assert not tuple(outside.iterdir())
    await store.close()


def config_manager_for(root, tmp_path):
    anchor = tmp_path / "anchor"
    (anchor / "state").mkdir(parents=True)
    fence_path = anchor / "state" / "root_state.json"
    fence_path.write_text(json.dumps({"version": 1, "mode": "normal", "current_root": str(root)}), encoding="utf-8")
    return SimpleNamespace(app_docs_dir=root, committed_selected_root=root,
                           recovery_committed_root_unavailable=False,
                           recovery_committed_root_unavailable_override=False,
                           root_state_path=fence_path, ROOT_STATE_VERSION=1)


@pytest.mark.asyncio
async def test_config_factory_requires_real_committed_root_and_fence(root, tmp_path):
    manager = config_manager_for(root, tmp_path)
    store = RecommendationStore.for_config_manager(manager)
    state = await store.load(CAT)
    assert not (root / "state").exists()
    manager.recovery_committed_root_unavailable = True
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await save(store, state)
    manager.recovery_committed_root_unavailable = False
    manager.app_docs_dir = manager.root_state_path.parent.parent
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await save(store, state)
    manager.app_docs_dir = root
    manager.root_state_path.write_text("broken", encoding="utf-8")
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await store.load(CAT)
    manager.root_state_path.unlink()
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await store.load(CAT)
    assert not manager.root_state_path.exists()
    assert not (root / "state").exists()
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fence,code", [
    ({"version": 1, "mode": "maintenance_readonly", "current_root": "unchanged"}, "maintenance"),
    ({"version": 1, "mode": "normal", "current_root": "other"}, "store_unavailable"),
    ({"mode": "normal", "current_root": "unchanged"}, "store_unavailable"),
    ({"version": 1, "mode": "normal", "current_root": ""}, "store_unavailable"),
])
async def test_config_factory_recovery_and_identity_fences(root, tmp_path, fence, code):
    manager = config_manager_for(root, tmp_path)
    if fence.get("current_root") == "unchanged":
        fence["current_root"] = str(root)
    manager.root_state_path.write_text(json.dumps(fence), encoding="utf-8")
    store = RecommendationStore.for_config_manager(manager)
    with pytest.raises(RecommendationError, match=code):
        await store.load(CAT)
    assert not (root / "state").exists()
    await store.close()


@pytest.mark.asyncio
async def test_config_fence_is_rechecked_before_physical_replace(root, tmp_path, monkeypatch):
    manager = config_manager_for(root, tmp_path)
    store = RecommendationStore.for_config_manager(manager)
    initial = await store.load(CAT)
    entered, release = fsync_barrier(monkeypatch)
    writing = asyncio.create_task(save(store, initial))
    assert await asyncio.to_thread(entered.wait, 5)
    manager.root_state_path.write_text(json.dumps({"version": 1, "mode": "maintenance_readonly", "current_root": str(root)}), encoding="utf-8")
    release.set()
    with pytest.raises(RecommendationError, match="maintenance"):
        await writing
    assert not path_for(root).exists()
    await store.close()


@pytest.mark.asyncio
async def test_close_blocks_reads_and_cannot_be_reopened(root):
    store = RecommendationStore(lambda: root)
    await store.close()
    with pytest.raises(RecommendationError, match="store_unavailable"):
        await store.load(CAT)
    with pytest.raises(RecommendationError, match="invalid_request"):
        await store.reset(CAT, expected_epoch="", request_id="../bad")
