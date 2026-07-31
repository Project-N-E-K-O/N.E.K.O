# -*- coding: utf-8 -*-
"""Regression tests for the recent.json file lock (issue #2528).

Covers the two defects the lock was introduced for:
(a) concurrent writers to the same recent.json overwrite each other / fail
    outright on Windows;
(b) a failed persist used to drop the batch on the floor — the next call
    reloaded the on-disk copy and the in-memory messages vanished.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from memory.recent import CompressedRecentHistoryManager, _compute_review_capacity
from utils import recent_file
from utils.cloudsave_runtime import MaintenanceModeError
from utils.llm_client import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    messages_from_dict,
    messages_to_dict,
)


# ─────────────── fixtures / helpers ───────────────


@pytest.fixture(autouse=True)
def _reset_recent_file_locks():
    """Clear the module-level lock registry around every test.

    Each test uses its own ``tmp_path``, so keys never collide; this only keeps
    a leaked/held lock from one test out of the next one.
    """
    recent_file._LOCKS.clear()
    recent_file._PENDING.clear()
    recent_file._REDIRECTS.clear()
    recent_file._DELETED.clear()
    recent_file._GENERATIONS.clear()
    yield
    recent_file._LOCKS.clear()
    recent_file._PENDING.clear()
    recent_file._REDIRECTS.clear()
    recent_file._DELETED.clear()
    recent_file._GENERATIONS.clear()


@pytest.fixture(autouse=True)
def _patch_cloudsave(monkeypatch):
    monkeypatch.setattr(
        "memory.recent.assert_cloudsave_writable",
        lambda *a, **kw: None,
    )


class _FakeConfig:
    """Only supplies the character recent path that update_history needs."""

    def __init__(self, lanlan_name: str, recent_path: str):
        self._lanlan_name = lanlan_name
        self._recent_path = recent_path

    async def aget_character_data(self):
        return (None, None, None, None, {}, None, None, None,
                {self._lanlan_name: self._recent_path})

    def get_character_data(self):
        return (None, None, None, None, {}, None, None, None,
                {self._lanlan_name: self._recent_path})


def _make_manager(tmp_path: Path, lanlan_name: str = "Xiaoba", *, path: str | None = None):
    recent_path = path or str(tmp_path / "recent.json")
    mgr = object.__new__(CompressedRecentHistoryManager)
    mgr._config_manager = _FakeConfig(lanlan_name, recent_path)
    mgr.max_history_length = 4
    mgr.compress_threshold = 5
    mgr.log_file_path = {lanlan_name: recent_path}
    mgr.name_mapping = {"human": "Master", "ai": lanlan_name, "system": "SYSTEM_MESSAGE"}
    mgr.user_histories = {}
    return mgr, lanlan_name, recent_path


def _write_disk(path: str, messages: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(messages_to_dict(messages), f, ensure_ascii=False)


def _read_disk(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return messages_from_dict(json.load(f))


def _widen_write_window(monkeypatch, seconds: float = 0.02):
    """Make each persist take measurably long so interleaving is reachable.

    Without this the read-modify-write is a handful of microseconds and a 2-core
    CI box would almost never schedule two workers inside it.
    """
    real = recent_file.atomic_write_json

    def _slow(path, payload, **kwargs):
        time.sleep(seconds)
        return real(path, payload, **kwargs)

    monkeypatch.setattr(recent_file, "atomic_write_json", _slow)


def _run_with_wide_pool(coro_factory, workers: int = 8):
    """Run a coroutine with a default executor big enough for real concurrency.

    Python's default ``ThreadPoolExecutor`` is ``min(32, cpu+4)``; on a 2-core
    runner that is 6 workers, and queueing would hide the very interleaving
    these tests are about.
    """
    async def _main():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=workers))
        return await coro_factory()

    return asyncio.run(_main())


# ─────────────── T1: concurrent update_history keeps every batch ───────────────


def test_concurrent_update_history_keeps_every_batch(tmp_path, monkeypatch):
    """Six concurrent appends against one file must all survive."""
    _widen_write_window(monkeypatch)
    mgr, name, path = _make_manager(tmp_path)
    batches = [[HumanMessage(content=f"batch-{i}")] for i in range(6)]

    async def _go():
        await asyncio.gather(*[
            mgr.update_history(batch, name, compress=False) for batch in batches
        ])

    _run_with_wide_pool(_go)

    disk = _read_disk(path)
    assert len(disk) == 6, f"每批都必须落盘，实际 {[m.content for m in disk]}"
    assert {m.content for m in disk} == {f"batch-{i}" for i in range(6)}
    assert [m.content for m in mgr.user_histories[name]] == [m.content for m in disk]
    assert mgr._pending_batches(name) == []


# ─────────────── T2: readers must not break writers (Windows) ───────────────


_HAMMER_WRITES = 20


def _hammer_readers_vs_writer(path: str, read_once) -> tuple[list[str], list[str]]:
    """Spin reader threads against a fixed number of writes; return (write, read) errors.

    Readers must spin rather than run a fixed count: a reader that finishes early
    leaves the writer alone and the collision window disappears. Measured on this
    box, spinning readers against unlocked reads fail 95/100 writes; with the
    lock in place, 0/100.
    """
    payload = [{"type": "human", "data": {"content": "x" * 300}} for _ in range(200)]
    recent_file.write_recent_payload(path, payload)

    stop = threading.Event()
    read_errors: list[str] = []
    write_errors: list[str] = []

    def _reader():
        while not stop.is_set():
            try:
                read_once()
            except Exception as exc:  # noqa: BLE001 - 测试要看到任何异常
                read_errors.append(repr(exc))

    def _writer():
        try:
            for _ in range(_HAMMER_WRITES):
                try:
                    recent_file.write_recent_payload(path, payload)
                except Exception as exc:  # noqa: BLE001
                    write_errors.append(repr(exc))
        finally:
            stop.set()

    threads = [threading.Thread(target=_reader, daemon=True) for _ in range(3)]
    threads.append(threading.Thread(target=_writer))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert stop.is_set(), "writer 没跑完，用例本身失效"
    return write_errors, read_errors


@pytest.mark.skipif(os.name != "nt", reason="裸读打断 os.replace 是 Windows 专属回归")
def test_locked_readers_do_not_break_concurrent_writers(tmp_path):
    """Readers take the same lock, so concurrent reads cannot fail the writers.

    On Windows a plain ``open()`` on the target makes ``os.replace`` raise
    PermissionError even with file_utils' busy-retry enabled. CI runs on
    windows-latest, so this actually executes there.
    """
    path = str(tmp_path / "recent.json")
    write_errors, read_errors = _hammer_readers_vs_writer(
        path, lambda: recent_file.read_recent_text(path),
    )
    assert write_errors == [], f"读者进锁后写不该失败，实际 {write_errors[:3]}"
    assert read_errors == [], f"读也不该失败，实际 {read_errors[:3]}"


@pytest.mark.skipif(os.name != "nt", reason="裸读打断 os.replace 是 Windows 专属回归")
def test_manager_reads_go_through_the_file_lock(tmp_path):
    """The manager's own read path must be inside the lock, not just the writes."""
    mgr, name, path = _make_manager(tmp_path)
    write_errors, read_errors = _hammer_readers_vs_writer(
        path, lambda: mgr._read_history_locked(path, name),
    )
    assert write_errors == [], f"manager 的读必须进锁，实际写失败 {write_errors[:3]}"
    assert read_errors == [], f"读也不该失败，实际 {read_errors[:3]}"


def test_concurrent_readers_and_writers_lose_nothing(tmp_path, monkeypatch):
    """End-to-end: mixed aget/update over one file loses no batch."""
    _widen_write_window(monkeypatch, seconds=0.01)
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, [HumanMessage(content="seed")])

    async def _go():
        writers = [
            mgr.update_history([HumanMessage(content=f"w{i}")], name, compress=False)
            for i in range(6)
        ]
        readers = [mgr.aget_recent_history(name) for _ in range(6)]
        return await asyncio.gather(*writers, *readers)

    _run_with_wide_pool(_go, workers=16)

    disk = _read_disk(path)
    assert len(disk) == 7, f"seed + 6 批全部必须在盘上，实际 {len(disk)}"
    assert mgr._pending_batches(name) == [], "任何一次写失败都会在 pending 里留痕"


def test_waiting_writer_is_rejected_after_character_delete(tmp_path):
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, [HumanMessage(content="old")])
    lock = recent_file.recent_file_lock(path)
    lock.acquire()
    def _waiting_writer():
        asyncio.run(mgr.update_history(
            [HumanMessage(content="stale-writer")], name, compress=False,
        ))

    writer = threading.Thread(target=_waiting_writer)
    writer.start()
    time.sleep(0.05)
    Path(path).unlink()
    recent_file.mark_recent_deleted([path])
    lock.release()
    writer.join(3)

    assert not writer.is_alive()
    assert not Path(path).exists()
    with recent_file.recent_file_lock(path):
        assert recent_file.get_recent_pending_unlocked(path) == []

    recent_file.clear_recent_deletions([path])
    asyncio.run(mgr.update_history(
        [HumanMessage(content="new-character")], name, compress=False,
    ))
    assert [message.content for message in _read_disk(path)] == ["new-character"]


def test_waiting_writer_through_renamed_alias_is_rejected_after_delete(tmp_path):
    old_path = tmp_path / "A" / "recent.json"
    new_path = tmp_path / "B" / "recent.json"
    new_path.parent.mkdir(parents=True)
    recent_file.write_recent_payload(new_path, [])
    recent_file.redirect_recent_paths([old_path], new_path)
    lock = recent_file.recent_file_lock(new_path)
    lock.acquire()
    errors = []

    def _waiting_writer():
        try:
            recent_file.write_recent_payload(old_path, [{"content": "stale"}])
        except recent_file.RecentFileDeletedError as exc:
            errors.append(exc)

    writer = threading.Thread(target=_waiting_writer)
    writer.start()
    time.sleep(0.05)
    redirects, deletion_scope, _ = (
        recent_file.fence_recent_deletions_and_clear_redirects([new_path])
    )
    new_path.unlink()
    lock.release()
    writer.join(3)

    assert not writer.is_alive()
    assert len(errors) == 1
    assert os.path.normcase(os.path.abspath(old_path)) in deletion_scope
    assert os.path.normcase(os.path.abspath(new_path)) in deletion_scope
    assert redirects
    assert not old_path.exists()
    assert not new_path.exists()


def test_reused_name_rejects_writer_waiting_on_old_redirect_target(tmp_path):
    old_path = tmp_path / "A" / "recent.json"
    renamed_path = tmp_path / "B" / "recent.json"
    renamed_path.parent.mkdir(parents=True)
    recent_file.write_recent_payload(renamed_path, [{"content": "old-character"}])
    recent_file.redirect_recent_paths([old_path], renamed_path)
    lock = recent_file.recent_file_lock(renamed_path)
    lock.acquire()
    errors = []

    def _old_writer():
        try:
            recent_file.write_recent_payload(old_path, [{"content": "delayed-old-turn"}])
        except recent_file.RecentFileDeletedError as exc:
            errors.append(exc)

    writer = threading.Thread(target=_old_writer)
    writer.start()
    time.sleep(0.05)
    recent_file.activate_recent_paths([old_path])
    recent_file.write_recent_payload(old_path, [{"content": "new-character"}])
    lock.release()
    writer.join(3)

    assert not writer.is_alive()
    assert len(errors) == 1
    assert json.loads(old_path.read_text(encoding="utf-8")) == [
        {"content": "new-character"},
    ]
    assert json.loads(renamed_path.read_text(encoding="utf-8")) == [
        {"content": "old-character"},
    ]


def test_update_captures_generation_before_first_await(tmp_path):
    """A write blocked on config refresh must retain its starting identity."""
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, [HumanMessage(content="old-character")])
    config_entered = threading.Event()
    release_config = threading.Event()

    class _BlockingConfig(_FakeConfig):
        memory_dir = tmp_path

        async def aget_character_data(self):
            config_entered.set()
            assert await asyncio.to_thread(release_config.wait, 3)
            return await super().aget_character_data()

    mgr._config_manager = _BlockingConfig(name, path)
    writer = threading.Thread(
        target=lambda: asyncio.run(mgr.update_history(
            [HumanMessage(content="pre-activation-turn")], name, compress=False,
        )),
    )
    writer.start()
    assert config_entered.wait(3)

    recent_file.activate_recent_paths([path])
    recent_file.write_recent_payload(
        path, messages_to_dict([HumanMessage(content="authoritative-import")]),
    )
    release_config.set()
    writer.join(3)

    assert not writer.is_alive()
    assert [message.content for message in _read_disk(path)] == ["authoritative-import"]
    assert recent_file.get_recent_pending(path) == []


def test_async_read_cannot_reset_a_new_identity_after_config_await(tmp_path):
    """A stale reader must not repair malformed bytes owned by a new identity."""
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, [HumanMessage(content="old-character")])
    config_entered = threading.Event()
    release_config = threading.Event()
    result = []

    class _BlockingConfig(_FakeConfig):
        memory_dir = tmp_path

        async def aget_character_data(self):
            config_entered.set()
            assert await asyncio.to_thread(release_config.wait, 3)
            return await super().aget_character_data()

    mgr._config_manager = _BlockingConfig(name, path)
    reader = threading.Thread(
        target=lambda: result.extend(asyncio.run(mgr.aget_recent_history(name))),
    )
    reader.start()
    assert config_entered.wait(3)

    recent_file.activate_recent_paths([path])
    malformed = "{new-identity-malformed"
    Path(path).write_text(malformed, encoding="utf-8")
    release_config.set()
    reader.join(3)

    assert not reader.is_alive()
    assert result == []
    assert Path(path).read_text(encoding="utf-8") == malformed


def test_sync_read_cannot_reset_a_new_identity_during_config_fetch(tmp_path):
    """A stale synchronous reader must not repair bytes after identity activation."""
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, [HumanMessage(content="old-character")])
    config_entered = threading.Event()
    release_config = threading.Event()
    result = []

    class _BlockingConfig(_FakeConfig):
        memory_dir = tmp_path

        def get_character_data(self):
            config_entered.set()
            assert release_config.wait(3)
            return super().get_character_data()

    mgr._config_manager = _BlockingConfig(name, path)
    reader = threading.Thread(
        target=lambda: result.extend(mgr.get_recent_history(name)),
    )
    reader.start()
    assert config_entered.wait(3)

    recent_file.activate_recent_paths([path])
    malformed = "{new-sync-identity-malformed"
    Path(path).write_text(malformed, encoding="utf-8")
    release_config.set()
    reader.join(3)

    assert not reader.is_alive()
    assert result == []
    assert Path(path).read_text(encoding="utf-8") == malformed


def test_generation_rollback_restores_only_pretransaction_writers(tmp_path):
    """Rollback accepts the restored cohort and rejects the transient cohort."""
    path = tmp_path / "Role" / "recent.json"
    path.parent.mkdir()
    recent_file.write_recent_payload(path, [])
    path_lock = recent_file.recent_file_lock(path)
    path_lock.acquire()
    entered: list[str] = []
    errors: dict[str, Exception] = {}

    def _access(label):
        try:
            with recent_file.recent_file_access(path):
                entered.append(label)
        except Exception as exc:  # noqa: BLE001 - asserted in the main thread
            errors[label] = exc

    before = threading.Thread(target=_access, args=("before",))
    before.start()
    time.sleep(0.05)
    redirects, scope, deleted, generations = recent_file.activate_recent_paths([path])
    transient_generation = recent_file.capture_recent_generation(path)
    during = threading.Thread(target=_access, args=("during",))
    during.start()
    time.sleep(0.05)

    recent_file.restore_recent_registry_state(
        list(scope), redirects, deleted, generations,
    )
    path_lock.release()
    before.join(3)
    during.join(3)

    assert entered == ["before"]
    assert isinstance(errors.get("during"), recent_file.RecentFileDeletedError)
    assert recent_file.capture_recent_generation(path)[1] == 0
    recent_file.activate_recent_paths([path])
    assert recent_file.capture_recent_generation(path) != transient_generation


def test_generation_token_cannot_be_reused_for_another_path(tmp_path):
    first = tmp_path / "A" / "recent.json"
    second = tmp_path / "B" / "recent.json"
    token = recent_file.capture_recent_generation(first)

    with pytest.raises(ValueError, match="different path"):
        with recent_file.recent_file_access(second, expected_generation=token):
            pass


def test_older_rollback_cannot_clobber_a_newer_activation(tmp_path):
    path = tmp_path / "Role" / "recent.json"
    first = recent_file.activate_recent_paths([path])
    newer = recent_file.activate_recent_paths([path])
    newer_token = recent_file.capture_recent_generation(path)

    with pytest.raises(recent_file.RecentGenerationConflictError):
        recent_file.restore_recent_registry_state(
            list(first[1]), first[0], first[2], first[3],
        )

    assert recent_file.capture_recent_generation(path) == newer_token
    recent_file.restore_recent_registry_state(
        list(newer[1]), newer[0], newer[2], newer[3],
    )
    assert recent_file.capture_recent_generation(path)[1] == first[3][
        recent_file._lock_key(path)
    ][1]


# ─────────────── T3: failed persist keeps the batch and flushes it later ───────────────


def test_failed_persist_keeps_batch_and_next_call_flushes_it(tmp_path, monkeypatch):
    """Defect (b): a failed write must not make the batch disappear."""
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, [HumanMessage(content="A"), HumanMessage(content="B")])

    real = recent_file.atomic_write_json
    state = {"failed": False}

    def _fail_once(p, payload, **kwargs):
        if not state["failed"]:
            state["failed"] = True
            raise OSError("simulated disk failure")
        return real(p, payload, **kwargs)

    monkeypatch.setattr(recent_file, "atomic_write_json", _fail_once)

    asyncio.run(mgr.update_history([HumanMessage(content="C")], name, compress=False))

    assert state["failed"] is True
    assert [m.content for m in _read_disk(path)] == ["A", "B"], "写失败 ⟹ 目标未被替换"
    assert [m.content for m in mgr.user_histories[name]] == ["A", "B", "C"]
    assert [m.content for m in mgr._pending_batches(name)] == ["C"]

    asyncio.run(mgr.update_history([HumanMessage(content="D")], name, compress=False))

    assert [m.content for m in _read_disk(path)] == ["A", "B", "C", "D"], \
        "下一次落盘必须把 C 补上，而不是让磁盘旧内容赢"
    assert mgr._pending_batches(name) == []


# ─────────────── CS-2: the compression splice re-reads under the lock ───────────────


def test_compression_splice_keeps_messages_appended_during_compression(tmp_path):
    """A batch persisted while the compression LLM runs must survive the splice.

    The appending writer is a SECOND manager instance (the UI editor, or the
    post-reload instance), so the compressing instance's in-memory view does not
    contain the new message. That is what forces the splice to re-read the file
    inside its critical section instead of trusting the pre-LLM view.
    """
    path = str(tmp_path / "recent.json")
    mgr_a, name, _ = _make_manager(tmp_path, path=path)
    mgr_b, _, _ = _make_manager(tmp_path, path=path)
    seeded = [HumanMessage(content=f"m{i}") for i in range(7)]
    _write_disk(path, seeded)

    gate = asyncio.Event()
    memo = SystemMessage(content="先前对话的备忘录: compressed")

    async def _blocking_compress(messages, lanlan_name, detailed=False):
        await gate.wait()
        return (memo, "compressed")

    setattr(mgr_a, "compress_history", _blocking_compress)

    async def _go():
        task = asyncio.create_task(
            mgr_a.update_history([HumanMessage(content="trigger")], name, compress=True)
        )
        await asyncio.sleep(0)
        await mgr_b.update_history([HumanMessage(content="Z")], name, compress=False)
        assert [m.content for m in _read_disk(path)][-1] == "Z"
        gate.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(_go())

    final = [m.content for m in _read_disk(path)]
    # 被压掉的是 m0..m4；m5/m6/trigger 既不在 memo 里也不该被切片丢掉。
    assert final == [memo.content, "m5", "m6", "trigger", "Z"], \
        f"压缩期间落盘的消息与未压缩中段都必须保留，实际 {final}"


# ─────────────── T4: an unreadable file is never overwritten ───────────────


def test_unreadable_file_never_writes_and_never_wipes(tmp_path, monkeypatch):
    """A transient read failure must not be laundered into "history is empty"."""
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, [HumanMessage(content="A"), HumanMessage(content="B")])
    before = Path(path).read_bytes()

    def _unreadable(p, **kwargs):
        raise PermissionError("simulated read failure")

    monkeypatch.setattr(recent_file, "read_recent_text_unlocked", _unreadable)

    # 全新 manager：user_histories 里没有这个角色 = 视图未知，不做任何
    # 「历史已经在内存里」的前置假设。
    assert name not in mgr.user_histories

    asyncio.run(mgr.update_history([HumanMessage(content="C")], name, compress=False))

    assert Path(path).read_bytes() == before, "读不出来时绝不能写盘"
    assert [m.content for m in mgr._pending_batches(name)] == ["C"]


def test_unreadable_reads_keep_process_pending_visible_without_duplicates(tmp_path, monkeypatch):
    mgr, name, path = _make_manager(tmp_path)
    fresh_mgr, _, _ = _make_manager(tmp_path, path=path)
    _write_disk(path, [HumanMessage(content="disk")])

    def _write_failed(*args, **kwargs):
        raise PermissionError("simulated write failure")

    monkeypatch.setattr(recent_file, "atomic_write_json", _write_failed)
    asyncio.run(mgr.update_history([HumanMessage(content="pending-1")], name, compress=False))

    def _unreadable(*args, **kwargs):
        raise PermissionError("simulated read failure")

    monkeypatch.setattr(recent_file, "read_recent_text_unlocked", _unreadable)
    assert [m.content for m in asyncio.run(mgr.aget_recent_history(name))] == [
        "disk", "pending-1",
    ]
    assert [m.content for m in asyncio.run(fresh_mgr.aget_recent_history(name))] == [
        "pending-1",
    ]

    asyncio.run(mgr.update_history([HumanMessage(content="pending-2")], name, compress=False))
    assert [m.content for m in asyncio.run(mgr.aget_recent_history(name))] == [
        "disk", "pending-1", "pending-2",
    ]
    assert [m.content for m in asyncio.run(fresh_mgr.aget_recent_history(name))] == [
        "pending-1", "pending-2",
    ]


# ─────────────── T5: the invariant the no-dedup design rests on ───────────────


def test_atomic_write_failure_leaves_target_byte_identical(tmp_path, monkeypatch):
    """``atomic_write_json`` raising ⟹ the target file was NOT replaced.

    The whole "just re-append the pending batch, no dedup needed" design rests
    on this, so it is pinned explicitly even though the code under test is
    utils.file_utils rather than this PR's code.
    """
    target = tmp_path / "recent.json"
    target.write_text(json.dumps([{"keep": "me"}]), encoding="utf-8")
    before = target.read_bytes()

    import utils.file_utils as file_utils

    def _boom(*a, **k):
        raise PermissionError("target busy")

    monkeypatch.setattr(file_utils.os, "replace", _boom)

    with pytest.raises(PermissionError):
        recent_file.write_recent_payload(target, [{"clobbered": True}])

    assert target.read_bytes() == before
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"tmp 必须被清掉，残留 {leftovers}"


# ─────────────── T6: the compression callback runs with no file lock held ───────────────


def test_compress_callback_runs_with_no_file_lock_held(tmp_path, monkeypatch):
    """``_notify_compress_done`` must never be invoked from inside a critical section.

    memory_server's dead-letter branch calls ``enforce_hard_cap`` straight from
    that callback, and ``enforce_hard_cap`` takes the same non-reentrant
    ``threading.Lock``. Moving the callback into any critical section is an
    un-timeout-able deadlock on a worker thread.
    """
    mgr, name, path = _make_manager(tmp_path)
    seeded = [HumanMessage(content=f"m{i}" * 40) for i in range(8)]
    _write_disk(path, seeded)
    observed: dict[str, Any] = {}

    async def _failed_compress(*a, **k):
        return None

    setattr(mgr, "compress_history", _failed_compress)

    async def _callback(lanlan_name, snapshot, ok, detailed):
        observed["ok"] = ok
        observed["locked"] = recent_file.recent_file_lock(path).locked()
        # 真的走一遍 dead-letter 分支做的事：拿同一把锁写盘。
        await mgr.enforce_hard_cap(lanlan_name)

    # 硬上限压到必然触发裁剪，让回调里的 enforce_hard_cap 真的要写盘
    import memory.recent as recent_mod
    monkeypatch.setattr(recent_mod, "RECENT_HARD_CAP_TOKENS", 20)

    async def _go():
        # wait_for 兜底：一旦回调被挪进临界区，enforce_hard_cap 会在 worker
        # 线程上永久死锁（threading.Lock 不可重入），没有超时就是挂死。
        await asyncio.wait_for(
            mgr.update_history(
                [HumanMessage(content="new")], name,
                on_compress_done=_callback,
            ),
            timeout=5,
        )

    asyncio.run(_go())

    assert observed.get("ok") is False, "压缩失败必须以 ok=False 回调"
    assert observed.get("locked") is False, "回调运行时不得持有文件锁"
    # 裁剪真的落了盘 —— 证明它确实拿到了锁并写成功，不是静默 no-op。
    assert len(_read_disk(path)) < 9


# ─────────────── T7: the lock outlives the manager instance ───────────────


def test_two_manager_instances_serialise_writes(tmp_path, monkeypatch):
    """Two managers over one file (reload window) must still exclude each other."""
    _widen_write_window(monkeypatch)
    path = str(tmp_path / "recent.json")
    mgr_a, name, _ = _make_manager(tmp_path, path=path)
    mgr_b, _, _ = _make_manager(tmp_path, path=path)
    _write_disk(path, [HumanMessage(content="seed")])

    async def _go():
        await asyncio.gather(
            mgr_a.update_history([HumanMessage(content="from-a")], name, compress=False),
            mgr_b.update_history([HumanMessage(content="from-b")], name, compress=False),
        )

    _run_with_wide_pool(_go)

    contents = [m.content for m in _read_disk(path)]
    assert sorted(contents) == ["from-a", "from-b", "seed"], \
        f"新旧实例的写都必须在盘上，实际 {contents}"


# ─────────────── T8: pending survives reload but not authoritative replacement ───────────────


def test_fresh_manager_after_reload_flushes_previous_pending(tmp_path, monkeypatch):
    """A fresh manager must retain the per-file unpersisted batch."""
    mgr1, name, path = _make_manager(tmp_path)
    _write_disk(path, [HumanMessage(content="A")])

    def _boom(*a, **k):
        raise OSError("simulated disk failure")

    real_atomic_write = recent_file.atomic_write_json
    monkeypatch.setattr(recent_file, "atomic_write_json", _boom)
    asyncio.run(mgr1.update_history([HumanMessage(content="B")], name, compress=False))
    assert [m.content for m in mgr1._pending_batches(name)] == ["B"]

    mgr2, _, _ = _make_manager(tmp_path, path=path)
    assert [m.content for m in mgr2._pending_batches(name)] == ["B"]

    monkeypatch.setattr(recent_file, "atomic_write_json", real_atomic_write)
    asyncio.run(mgr2.update_history([HumanMessage(content="C")], name, compress=False))
    assert [m.content for m in _read_disk(path)] == ["A", "B", "C"]


@pytest.mark.parametrize("seed_disk", [False, True])
def test_read_includes_pending_after_failed_persist(tmp_path, monkeypatch, seed_disk):
    """Normal and missing-file reads must preserve the unpersisted visible batch."""
    mgr, name, path = _make_manager(tmp_path)
    expected = ["pending"]
    if seed_disk:
        _write_disk(path, [HumanMessage(content="disk")])
        expected.insert(0, "disk")

    def _boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(recent_file, "atomic_write_json", _boom)
    asyncio.run(
        mgr.update_history([HumanMessage(content="pending")], name, compress=False)
    )

    visible = asyncio.run(mgr.aget_recent_history(name))
    assert [message.content for message in visible] == expected
    assert [message.content for message in mgr._pending_batches(name)] == ["pending"]


def test_authoritative_replace_discards_previous_pending(tmp_path, monkeypatch):
    """A user replacement must not resurrect an older failed append."""
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, [HumanMessage(content="A")])

    def _boom(*a, **k):
        raise OSError("simulated disk failure")

    real_atomic_write = recent_file.atomic_write_json
    monkeypatch.setattr(recent_file, "atomic_write_json", _boom)
    asyncio.run(mgr.update_history([HumanMessage(content="stale")], name, compress=False))
    assert [m.content for m in mgr._pending_batches(name)] == ["stale"]

    monkeypatch.setattr(recent_file, "atomic_write_json", real_atomic_write)
    recent_file.write_recent_payload(
        path, messages_to_dict([HumanMessage(content="replacement")]),
    )
    asyncio.run(mgr.update_history([HumanMessage(content="new")], name, compress=False))
    assert [m.content for m in _read_disk(path)] == ["replacement", "new"]


def test_character_rename_moves_pending_to_new_recent_path(tmp_path):
    """Renaming storage must keep an unpersisted batch attached to the file."""
    from utils.character_memory import rename_character_memory_storage

    old_path = tmp_path / "Old" / "recent.json"
    old_path.parent.mkdir()
    _write_disk(str(old_path), [HumanMessage(content="disk")])
    with recent_file.recent_file_lock(old_path):
        recent_file.set_recent_pending_unlocked(
            old_path, [HumanMessage(content="Old说：pending")],
        )

    class _RenameConfig:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    rename_character_memory_storage(_RenameConfig(), "Old", "New")
    new_path = tmp_path / "New" / "recent.json"
    assert [m.content for m in recent_file.get_recent_pending(old_path)] == ["New说：pending"]
    assert [m.content for m in recent_file.get_recent_pending(new_path)] == ["New说：pending"]

    mgr, name, _ = _make_manager(tmp_path, "New", path=str(new_path))
    asyncio.run(mgr.update_history([HumanMessage(content="next")], name, compress=False))
    assert [m.content for m in _read_disk(str(new_path))] == ["disk", "New说：pending", "next"]


def test_character_rename_pending_snapshot_restores_on_rollback(tmp_path):
    """A larger rename transaction can restore the exact pre-move pending state."""
    from utils.character_memory import rename_character_memory_storage

    old_path = tmp_path / "Old" / "recent.json"
    old_path.parent.mkdir()
    _write_disk(str(old_path), [HumanMessage(content="disk")])
    original = HumanMessage(content="Old说：pending")
    with recent_file.recent_file_lock(old_path):
        recent_file.set_recent_pending_unlocked(old_path, [original])

    class _RenameConfig:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    result = rename_character_memory_storage(_RenameConfig(), "Old", "New")
    from utils.character_memory import rollback_character_recent_rename
    rollback_character_recent_rename(result)

    new_path = tmp_path / "New" / "recent.json"
    assert [m.content for m in recent_file.get_recent_pending(old_path)] == ["Old说：pending"]
    assert recent_file.get_recent_pending(new_path) == []


def test_rename_into_reused_name_invalidates_obsolete_redirect(tmp_path):
    """A rename target reused later must own its physical recent file."""
    from utils.character_memory import rename_character_memory_storage

    reused_path = tmp_path / "A" / "recent.json"
    former_target = tmp_path / "B" / "recent.json"
    source_path = tmp_path / "C" / "recent.json"
    former_target.parent.mkdir()
    source_path.parent.mkdir()
    _write_disk(str(former_target), [HumanMessage(content="belongs-to-B")])
    _write_disk(str(source_path), [HumanMessage(content="belongs-to-C")])
    recent_file.redirect_recent_paths([reused_path], former_target)

    class _RenameConfig:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    rename_character_memory_storage(_RenameConfig(), "C", "A")
    recent_file.write_recent_payload(reused_path, [{"owner": "new-A"}])

    with open(reused_path, encoding="utf-8") as handle:
        assert json.load(handle) == [{"owner": "new-A"}]
    assert [m.content for m in _read_disk(str(former_target))] == ["belongs-to-B"]


def test_reused_rename_target_activates_every_storage_layout(tmp_path):
    """Nested and flat aliases in both roots must reject obsolete writers."""
    from utils.character_memory import (
        list_character_recent_paths,
        rename_character_memory_storage,
    )

    runtime_root = tmp_path / "runtime"
    project_root = tmp_path / "project"

    class _RenameConfig:
        memory_dir = runtime_root
        project_memory_dir = project_root

    source_path = runtime_root / "C" / "recent.json"
    former_target = runtime_root / "B" / "recent.json"
    source_path.parent.mkdir(parents=True)
    former_target.parent.mkdir(parents=True)
    _write_disk(str(source_path), [HumanMessage(content="belongs-to-C")])
    _write_disk(str(former_target), [HumanMessage(content="belongs-to-B")])
    reused_paths = list_character_recent_paths(_RenameConfig(), "A")
    assert len(reused_paths) == 4
    recent_file.redirect_recent_paths(reused_paths, former_target)
    old_tokens = {
        path: recent_file.capture_recent_generation(path)
        for path in reused_paths
    }

    rename_character_memory_storage(_RenameConfig(), "C", "A")

    for path, token in old_tokens.items():
        with pytest.raises(recent_file.RecentFileDeletedError):
            with recent_file.recent_file_access(
                path, expected_generation=token,
            ):
                pass
    assert [message.content for message in _read_disk(str(former_target))] == [
        "belongs-to-B",
    ]


def test_reused_rename_waits_for_access_already_inside_redirect_target(tmp_path):
    """Activation cannot commit while an obsolete alias still owns its target lock."""
    from utils.character_memory import rename_character_memory_storage

    runtime_root = tmp_path / "runtime"
    project_root = tmp_path / "project"

    class _RenameConfig:
        memory_dir = runtime_root
        project_memory_dir = project_root

    alias_path = project_root / "A" / "recent.json"
    former_target = runtime_root / "B" / "recent.json"
    source_path = runtime_root / "C" / "recent.json"
    former_target.parent.mkdir(parents=True)
    source_path.parent.mkdir(parents=True)
    _write_disk(str(former_target), [HumanMessage(content="belongs-to-B")])
    _write_disk(str(source_path), [HumanMessage(content="belongs-to-C")])
    recent_file.redirect_recent_paths([alias_path], former_target)
    access_entered = threading.Event()
    release_access = threading.Event()

    def _inside_old_access():
        with recent_file.recent_file_access(alias_path) as resolved_path:
            access_entered.set()
            assert release_access.wait(3)
            recent_file.write_recent_payload_unlocked(
                resolved_path, [{"content": "old-access-finished-first"}],
            )

    old_access = threading.Thread(target=_inside_old_access)
    old_access.start()
    assert access_entered.wait(3)
    rename = threading.Thread(
        target=rename_character_memory_storage,
        args=(_RenameConfig(), "C", "A"),
    )
    rename.start()
    time.sleep(0.05)
    assert rename.is_alive(), "rename must wait for the resolved physical target lock"

    release_access.set()
    old_access.join(3)
    rename.join(3)

    assert not old_access.is_alive()
    assert not rename.is_alive()
    assert json.loads(former_target.read_text(encoding="utf-8")) == [
        {"content": "old-access-finished-first"},
    ]
    assert [message.content for message in _read_disk(
        str(runtime_root / "A" / "recent.json"),
    )] == ["belongs-to-C"]


def test_rename_rollback_restores_target_redirect(tmp_path):
    """A failed rename must preserve routing for the still-unused target name."""
    from utils.character_memory import (
        rename_character_memory_storage,
        rollback_character_recent_rename,
    )

    reused_path = tmp_path / "A" / "recent.json"
    former_target = tmp_path / "B" / "recent.json"
    source_path = tmp_path / "C" / "recent.json"
    former_target.parent.mkdir()
    source_path.parent.mkdir()
    _write_disk(str(former_target), [HumanMessage(content="belongs-to-B")])
    _write_disk(str(source_path), [HumanMessage(content="belongs-to-C")])
    recent_file.redirect_recent_paths([reused_path], former_target)

    class _RenameConfig:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    result = rename_character_memory_storage(_RenameConfig(), "C", "A")
    rollback_character_recent_rename(result)
    recent_file.write_recent_payload(reused_path, [{"owner": "still-B"}])

    with open(former_target, encoding="utf-8") as handle:
        assert json.load(handle) == [{"owner": "still-B"}]


def test_chained_rename_rollback_restores_source_redirect(tmp_path):
    """Rolling back B-to-C must preserve an earlier A-to-B redirect."""
    from utils.character_memory import (
        rename_character_memory_storage,
        rollback_character_recent_rename,
    )

    old_alias = tmp_path / "A" / "recent.json"
    source_path = tmp_path / "B" / "recent.json"
    target_path = tmp_path / "C" / "recent.json"
    source_path.parent.mkdir()
    _write_disk(str(source_path), [HumanMessage(content="belongs-to-B")])
    recent_file.redirect_recent_paths([old_alias], source_path)

    class _RenameConfig:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    result = rename_character_memory_storage(_RenameConfig(), "B", "C")
    rollback_character_recent_rename(result)

    assert recent_file._resolve_key_unlocked(recent_file._lock_key(old_alias)) == (
        recent_file._lock_key(source_path)
    )
    assert recent_file._resolve_key_unlocked(recent_file._lock_key(target_path)) == (
        recent_file._lock_key(target_path)
    )


def test_new_character_name_invalidates_obsolete_redirect(tmp_path):
    """Every creation path can detach a name that was previously renamed away."""
    from utils.character_memory import clear_character_recent_redirects

    reused_path = tmp_path / "A" / "recent.json"
    former_target = tmp_path / "B" / "recent.json"
    older_source = tmp_path / "X" / "recent.json"
    former_target.parent.mkdir()
    _write_disk(str(former_target), [HumanMessage(content="belongs-to-B")])
    recent_file.redirect_recent_paths([older_source], reused_path)
    recent_file.redirect_recent_paths([reused_path], former_target)

    class _CreateConfig:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    clear_character_recent_redirects(_CreateConfig(), "A")
    recent_file.write_recent_payload(reused_path, [{"owner": "new-A"}])
    recent_file.write_recent_payload(older_source, [{"owner": "old-X"}])

    with open(reused_path, encoding="utf-8") as handle:
        assert json.load(handle) == [{"owner": "new-A"}]
    with open(older_source, encoding="utf-8") as handle:
        assert json.load(handle) == [{"owner": "old-X"}]
    assert [m.content for m in _read_disk(str(former_target))] == ["belongs-to-B"]


def test_character_rename_holds_recent_locks_across_physical_move(tmp_path, monkeypatch):
    """The directory move and pending migration form one recent-file transaction."""
    import utils.character_memory as character_memory

    old_path = tmp_path / "Old" / "recent.json"
    old_path.parent.mkdir()
    _write_disk(str(old_path), [HumanMessage(content="disk")])

    class _RenameConfig:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    entered = threading.Event()
    release = threading.Event()
    real_merge = character_memory._merge_directories

    def _blocking_merge(source, target):
        if source == old_path.parent:
            entered.set()
            assert release.wait(3)
        return real_merge(source, target)

    monkeypatch.setattr(character_memory, "_merge_directories", _blocking_merge)
    worker = threading.Thread(
        target=character_memory.rename_character_memory_storage,
        args=(_RenameConfig(), "Old", "New"),
    )
    worker.start()
    assert entered.wait(3)
    writer_errors: list[Exception] = []

    def _stale_writer():
        try:
            recent_file.write_recent_payload(old_path, [{"writer": "stale-old-path"}])
        except Exception as exc:  # noqa: BLE001 - surfaced in the main test thread
            writer_errors.append(exc)

    writer = threading.Thread(target=_stale_writer)
    writer.start()
    time.sleep(0.05)
    assert writer.is_alive(), "旧路径写者必须先被改名事务锁挡住"
    release.set()
    worker.join(3)
    writer.join(3)
    assert not worker.is_alive()
    assert not writer.is_alive()
    assert writer_errors == []
    assert not old_path.exists()
    with open(tmp_path / "New" / "recent.json", encoding="utf-8") as handle:
        assert json.load(handle) == [{"writer": "stale-old-path"}]


def test_delete_holds_lock_until_rollback_before_accepting_later_pending(tmp_path):
    """A delete transaction must restore state before a later writer can enter."""
    from utils.character_memory import (
        delete_character_memory_storage,
        rollback_character_recent_delete,
    )

    recent_path = tmp_path / "Role" / "recent.json"
    recent_path.parent.mkdir()
    _write_disk(str(recent_path), [HumanMessage(content="disk")])
    with recent_file.recent_file_lock(recent_path):
        recent_file.set_recent_pending_unlocked(
            recent_path, [HumanMessage(content="before-delete")],
        )

    class _DeleteConfig:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    _, transaction = delete_character_memory_storage(
        _DeleteConfig(),
        "Role",
        capture_pending=True,
        keep_recent_locks=True,
    )
    entered = threading.Event()

    def _later_writer():
        with recent_file.recent_file_access(recent_path) as resolved_path:
            current = recent_file.get_recent_pending_unlocked(resolved_path)
            recent_file.set_recent_pending_unlocked(
                resolved_path,
                current + [HumanMessage(content="during-delete")],
            )
            entered.set()

    writer = threading.Thread(target=_later_writer)
    writer.start()
    time.sleep(0.05)
    assert not entered.is_set()

    rollback_character_recent_delete(transaction)
    writer.join(3)
    assert not writer.is_alive()
    assert [m.content for m in recent_file.get_recent_pending(recent_path)] == [
        "before-delete", "during-delete",
    ]


def test_delete_rollback_restores_redirect_chain(tmp_path):
    """A failed delete of B must restore every stale path that still targets B."""
    from utils.character_memory import (
        delete_character_memory_storage,
        rollback_character_recent_delete,
    )

    old_alias = tmp_path / "A" / "recent.json"
    recent_path = tmp_path / "B" / "recent.json"
    recent_path.parent.mkdir()
    _write_disk(str(recent_path), [HumanMessage(content="belongs-to-B")])
    recent_file.redirect_recent_paths([old_alias], recent_path)

    class _DeleteConfig:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    _, transaction = delete_character_memory_storage(
        _DeleteConfig(),
        "B",
        capture_pending=True,
        keep_recent_locks=True,
    )
    rollback_character_recent_delete(transaction)

    assert recent_file._resolve_key_unlocked(recent_file._lock_key(old_alias)) == (
        recent_file._lock_key(recent_path)
    )


def test_released_delete_transaction_can_rollback_after_config_failure(tmp_path):
    from utils.character_memory import (
        delete_character_memory_storage,
        rollback_character_recent_delete,
    )

    alias_path = tmp_path / "Old" / "recent.json"
    recent_path = tmp_path / "Role" / "recent.json"
    recent_path.parent.mkdir()
    _write_disk(str(recent_path), [HumanMessage(content="disk")])
    recent_file.redirect_recent_paths([alias_path], recent_path)
    with recent_file.recent_file_lock(recent_path):
        recent_file.set_recent_pending_unlocked(
            recent_path, [HumanMessage(content="pending-before-delete")],
        )

    class _DeleteConfig:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    _, transaction = delete_character_memory_storage(
        _DeleteConfig(), "Role", capture_pending=True,
    )
    with pytest.raises(recent_file.RecentFileDeletedError):
        recent_file.write_recent_payload(recent_path, [])

    rollback_character_recent_delete(transaction)
    assert [message.content for message in recent_file.get_recent_pending(recent_path)] == [
        "pending-before-delete",
    ]
    recent_path.parent.mkdir(parents=True, exist_ok=True)
    recent_file.write_recent_payload(alias_path, [{"content": "after-rollback"}])

    assert json.loads(recent_path.read_text(encoding="utf-8")) == [
        {"content": "after-rollback"},
    ]


# ─────────────── T9: review persist failure must report exactly ('failed', None) ───────────────


class _ReviewLLM:
    def __init__(self, corrected: list[dict]):
        self._payload = json.dumps(
            {"explanation": "x", "corrected_dialogue": corrected}, ensure_ascii=False,
        )

    async def ainvoke(self, prompt: str, **kwargs: Any) -> Any:
        class _R:
            content = self._payload

        return _R()

    async def aclose(self) -> None:
        return None


def _review_snapshot() -> list:
    return [
        SystemMessage(content="先前对话的备忘录: memo"),
        HumanMessage(content="hi 1"),
        AIMessage(content="ai 1"),
        HumanMessage(content="hi 2"),
        AIMessage(content="ai 2"),
    ]


def _review_corrected() -> list[dict]:
    return [
        {"role": "SYSTEM_MESSAGE", "content": "先前对话的备忘录: memo"},
        {"role": "Master", "content": "hi 1 fixed"},
        {"role": "Xiaoba", "content": "ai 1"},
        {"role": "Master", "content": "hi 2"},
        {"role": "Xiaoba", "content": "ai 2"},
    ]


def test_review_persist_failure_returns_failed_exactly(tmp_path, monkeypatch):
    """A failed review persist must report ('failed', None) — never 'white'.

    'white' runs ``_mutate_review_white``, which clears the cutoff fingerprint
    and the failure backoff and deliberately does NOT refresh ``last_review_ts``,
    so every /process would re-run a whole review LLM round with no circuit
    breaker. Hence the exact-equality assertion.
    """
    snapshot = _review_snapshot()
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, snapshot)
    setattr(mgr, "_get_review_llm", lambda: _ReviewLLM(_review_corrected()))

    def _boom(*a, **k):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(recent_file, "atomic_write_json", _boom)

    result = asyncio.run(mgr.review_history(name, snapshot=list(snapshot)))

    assert result == ('failed', None)


def test_review_unreadable_current_returns_failed_exactly(tmp_path, monkeypatch):
    """Same contract when the commit cannot read the file at all."""
    snapshot = _review_snapshot()
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, snapshot)
    setattr(mgr, "_get_review_llm", lambda: _ReviewLLM(_review_corrected()))

    def _unreadable(p, **kwargs):
        raise PermissionError("simulated read failure")

    monkeypatch.setattr(recent_file, "read_recent_text_unlocked", _unreadable)

    result = asyncio.run(mgr.review_history(name, snapshot=list(snapshot)))

    assert result == ('failed', None)


# ─────────────── T10: review commit is one atomic RMW ───────────────


def test_review_commit_does_not_lose_messages_appended_during_review(tmp_path, monkeypatch):
    """A message persisted while the review LLM runs must survive the patch.

    Two independent claims are asserted:
    (1) the appended message and the patch coexist on disk — proving the commit
        works off the current file, not off the pre-LLM snapshot;
    (2) the commit's read AND its write both observe the file lock as held —
        proving they are one critical section rather than two.
    """
    snapshot = _review_snapshot()
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, snapshot)

    lock_at_read: list[bool] = []
    lock_at_write: list[bool] = []
    real_read = recent_file.read_recent_text_unlocked
    real_write = recent_file.atomic_write_json

    def _spy_read(p, **kwargs):
        lock_at_read.append(recent_file.recent_file_lock(path).locked())
        return real_read(p, **kwargs)

    def _spy_write(p, payload, **kwargs):
        lock_at_write.append(recent_file.recent_file_lock(path).locked())
        return real_write(p, payload, **kwargs)

    monkeypatch.setattr(recent_file, "read_recent_text_unlocked", _spy_read)
    monkeypatch.setattr(recent_file, "atomic_write_json", _spy_write)

    gate = asyncio.Event()

    class _BlockingLLM(_ReviewLLM):
        async def ainvoke(self, prompt: str, **kwargs: Any) -> Any:
            await gate.wait()
            return await super().ainvoke(prompt, **kwargs)

    setattr(mgr, "_get_review_llm", lambda: _BlockingLLM(_review_corrected()))

    async def _go():
        review_task = asyncio.create_task(mgr.review_history(name, snapshot=list(snapshot)))
        await asyncio.sleep(0)
        # review LLM 卡在 gate 上时插一条新消息并确认已上盘
        await mgr.update_history([HumanMessage(content="Z")], name, compress=False)
        assert [m.content for m in _read_disk(path)][-1] == "Z"
        gate.set()
        return await asyncio.wait_for(review_task, timeout=5)

    status, fingerprint = asyncio.run(_go())

    assert status == "patched"
    assert fingerprint is not None
    final = [m.content for m in _read_disk(path)]
    assert final[-1] == "Z", f"review 期间落盘的消息必须保留，实际 {final}"
    assert "hi 1 fixed" in final, f"review 的修改必须应用，实际 {final}"
    assert lock_at_read and all(lock_at_read), "所有读都必须在锁内发生"
    assert lock_at_write and all(lock_at_write), "所有写都必须在锁内发生"


# ─────────────── T11: the main-server writers share the same lock ───────────────


def test_main_server_recent_writers_share_the_memory_server_lock(tmp_path, monkeypatch):
    """The three main_server writers must go through the same per-path lock."""
    _widen_write_window(monkeypatch, seconds=0.01)
    path = str(tmp_path / "recent.json")
    mgr, name, _ = _make_manager(tmp_path, path=path)
    _write_disk(path, [HumanMessage(content="A: seed")])

    from utils.character_memory import rewrite_recent_file_character_name

    errors: list[BaseException] = []

    def _hammer_rename():
        # 改名路径 = 锁内读 + 锁内写。来回改让它每轮都真的落一次盘；它写回的是
        # 自己刚在锁里读到的内容，所以不该吞掉任何并发 append。
        try:
            for i in range(4):
                old, new = ("A", "B") if i % 2 == 0 else ("B", "A")
                rewrite_recent_file_character_name(Path(path), old, new)
        except BaseException as exc:  # noqa: BLE001 - 测试要看到任何异常
            errors.append(exc)

    def _hammer_read():
        try:
            for _ in range(8):
                recent_file.read_recent_text(path)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    async def _go():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=16))
        await asyncio.gather(
            mgr.update_history([HumanMessage(content="w0")], name, compress=False),
            mgr.update_history([HumanMessage(content="w1")], name, compress=False),
            asyncio.to_thread(_hammer_rename),
            asyncio.to_thread(_hammer_read),
        )

    asyncio.run(_go())

    assert errors == [], f"并发路径不得抛异常，实际 {errors}"
    contents = [m.content for m in _read_disk(path)]
    assert "w0" in contents and "w1" in contents, \
        f"两批 update_history 都必须在盘上，实际 {contents}"
    assert sum(c.endswith("seed") for c in contents) == 1, \
        f"改名路径不得复制或丢失原有消息，实际 {contents}"


# ─────────────── T12: no writer bypasses the single entry point ───────────────


_SKIP_DIRS = {
    ".venv", "venv", "node_modules", "tests", "deps", "build", "dist",
    "__pycache__", ".git", ".claude", ".pytest_cache", ".ruff_cache",
    "steamworks", "frontend", "docs",
}


def _iter_project_sources() -> list[Path]:
    """Every first-party .py file, pruning vendored/test trees while walking."""
    root = Path(__file__).resolve().parents[2]
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".py"):
                out.append(Path(dirpath) / filename)
    return out


_WRITE_PRIMITIVES = {
    "atomic_write_json",
    "atomic_write_json_async",
    "atomic_write_text",
    "atomic_write_text_async",
}


def _call_name(node) -> str:
    import ast

    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _segment(lines: list[str], node) -> str:
    """Source text of one AST node.

    Hand-rolled instead of ``ast.get_source_segment`` because that one re-splits
    the whole file per call, which turns this scan quadratic.
    """
    end_lineno = getattr(node, "end_lineno", None)
    if end_lineno is None:
        return ""
    start = node.lineno - 1
    if start == end_lineno - 1:
        return lines[start][node.col_offset:node.end_col_offset]
    chunk = [lines[start][node.col_offset:]]
    chunk.extend(lines[start + 1:end_lineno - 1])
    chunk.append(lines[end_lineno - 1][:node.end_col_offset])
    return "".join(chunk)


def test_no_recent_json_writer_bypasses_the_lock_module():
    """Auto-discover recent.json write sites; only the lock module may hold one.

    Discovery is by walking the tree, not by comparing against a hardcoded
    list — a newly added writer trips this without anyone remembering to update
    the test.

    A write-primitive call counts as a recent.json write when its destination
    expression does NOT mention ``meta`` (recent_meta.json is a different file)
    and either
      (a) the destination expression mentions ``recent`` (following one level of
          local assignment), or
      (b) the enclosing function — or the class it lives in — mentions the
          literal ``recent.json``.
    Signal (b) at class scope is what catches a new write added inside
    ``CompressedRecentHistoryManager`` whose destination is just ``file_path``.
    """
    import ast

    allowed = {Path("utils") / "recent_file.py"}
    root = Path(__file__).resolve().parents[2]
    violations: list[str] = []

    for source_path in _iter_project_sources():
        rel = source_path.relative_to(root)
        if rel in allowed:
            continue
        try:
            source = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "atomic_write" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        lines = source.splitlines(keepends=True)

        # 类作用域信号：类体里出现 recent.json 字面量 → 类里所有方法的落盘都算
        # 「写 recent.json」，除非目标表达式明说是 meta。
        recent_scope_funcs: set[int] = set()
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            if "recent.json" not in _segment(lines, cls):
                continue
            for member in ast.walk(cls):
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    recent_scope_funcs.add(id(member))

        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = list(ast.walk(func))
            writes = [
                n for n in body
                if isinstance(n, ast.Call) and _call_name(n) in _WRITE_PRIMITIVES and n.args
            ]
            if not writes:
                continue
            in_recent_scope = (
                "recent.json" in _segment(lines, func) or id(func) in recent_scope_funcs
            )
            # 一级局部赋值解析：把 `x = <expr>` 记下来，好让 write(x, ...) 也能
            # 看见目标表达式里的 "recent"。
            assigned: dict[str, str] = {}
            for node in body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                        and isinstance(node.targets[0], ast.Name):
                    assigned[node.targets[0].id] = _segment(lines, node.value)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                        and node.value is not None:
                    assigned[node.target.id] = _segment(lines, node.value)

            for node in writes:
                dest = _segment(lines, node.args[0])
                resolved = f"{dest} {assigned.get(dest, '')}".lower()
                if "meta" in resolved:
                    continue  # recent_meta.json 是另一个文件，不归这把锁管
                named = set(re.findall(r"[\w-]+\.json", resolved))
                if named and "recent.json" not in named:
                    continue  # 目标表达式明说了是别的文件（如 characters.json）
                if "recent" in resolved or in_recent_scope:
                    violations.append(
                        f"{rel.as_posix()}:{node.lineno} in {func.name}() -> {_call_name(node)}({dest})"
                    )

    assert violations == [], (
        "recent.json 的写必须收口到 utils/recent_file.py 的加锁入口，"
        f"发现绕过的写点：{violations}"
    )


# ─────────────── T13: the hard-cap tokenizer stays out of the lock ───────────────


def test_enforce_hard_cap_tokenizes_outside_the_lock(tmp_path, monkeypatch):
    """Full-history tokenization must never happen while the file lock is held."""
    mgr, name, path = _make_manager(tmp_path)
    history = [SystemMessage(content="memo")] + [
        HumanMessage(content=f"original message {i} with some length") for i in range(12)
    ]
    mgr.user_histories[name] = list(history)
    _write_disk(path, history)

    import memory.recent as recent_mod
    monkeypatch.setattr(recent_mod, "RECENT_HARD_CAP_TOKENS", 20)

    import utils.tokenize as tokenize_mod
    real_count = tokenize_mod.count_tokens
    lock_states: list[bool] = []

    def _spy(text, *a, **k):
        lock_states.append(recent_file.recent_file_lock(path).locked())
        return real_count(text, *a, **k)

    monkeypatch.setattr(tokenize_mod, "count_tokens", _spy)

    asyncio.run(mgr.enforce_hard_cap(name))

    assert lock_states, "本用例必须真的触发 tokenize"
    assert not any(lock_states), "全量 tokenize 不得在临界区内发生"
    assert len(_read_disk(path)) < len(history), "裁剪结果必须落盘"


def test_compression_splice_repeated_anchor_keeps_uncompressed_tail(tmp_path):
    """A repeated K-message anchor must not redirect CS-2 into appended data."""
    mgr, name, path = _make_manager(tmp_path)
    snapshot = [HumanMessage(content="same") for _ in range(3)]
    uncompressed_tail = [AIMessage(content=f"tail-{i}") for i in range(3)]
    appended = [HumanMessage(content="same") for _ in range(3)]
    _write_disk(path, snapshot + uncompressed_tail + appended)

    status = mgr._splice_compressed_locked(
        path, name, snapshot, SystemMessage(content="memo"),
    )

    assert status == "merged"
    assert [m.content for m in _read_disk(path)] == [
        "memo", "tail-0", "tail-1", "tail-2", "same", "same", "same",
    ]


def test_review_anchor_rejects_same_prefix_with_different_full_content():
    """The 50-character display prefix is not sufficient proof of identity."""
    prefix = "x" * 50
    snapshot = [
        SystemMessage(content="old memo"),
        HumanMessage(content=prefix + "old-1"),
        AIMessage(content=prefix + "old-2"),
        HumanMessage(content=prefix + "old-3"),
    ]
    current = [
        SystemMessage(content="new memo"),
        HumanMessage(content=prefix + "new-1"),
        AIMessage(content=prefix + "new-2"),
        HumanMessage(content=prefix + "new-3"),
    ]

    assert _compute_review_capacity(snapshot, current) == (0, None)


def test_splice_write_failure_notifies_compression_failure(tmp_path, monkeypatch):
    """A failed CS-2 commit must not clear backup-compression backoff as success."""
    mgr, name, path = _make_manager(tmp_path)
    _write_disk(path, [HumanMessage(content=f"m{i}") for i in range(7)])

    async def _compress(*args, **kwargs):
        return (SystemMessage(content="memo"), "memo")

    setattr(mgr, "compress_history", _compress)
    monkeypatch.setattr(mgr, "_splice_compressed_locked", lambda *args: "failed")
    observed = []

    async def _callback(_name, _snapshot, ok, _detailed):
        observed.append(ok)

    asyncio.run(mgr.update_history(
        [HumanMessage(content="new")], name, on_compress_done=_callback,
    ))

    assert observed == [False]


def test_merge_backup_memo_rereads_after_fence_before_write(tmp_path, monkeypatch):
    """A batch appended while the cloudsave fence is checked must survive merge."""
    mgr, name, path = _make_manager(tmp_path)
    writer, _, _ = _make_manager(tmp_path, path=path)
    batch = [HumanMessage(content=f"old-{i}") for i in range(4)]
    _write_disk(path, batch)
    entered = threading.Event()
    release = threading.Event()

    def _blocking_fence(*args, **kwargs):
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr("memory.recent.assert_cloudsave_writable", _blocking_fence)

    async def _go():
        task = asyncio.create_task(
            mgr.merge_backup_memo(name, list(batch), SystemMessage(content="memo")),
        )
        assert await asyncio.to_thread(entered.wait, 3)
        await asyncio.to_thread(
            writer._append_and_persist_locked,
            path,
            name,
            [HumanMessage(content="append-during-merge")],
        )
        release.set()
        return await task

    assert asyncio.run(_go()) == "merged"
    assert [m.content for m in _read_disk(path)] == ["memo", "append-during-merge"]


def test_hard_cap_retries_when_disk_changes_before_commit(tmp_path, monkeypatch):
    """Hard-cap trimming must not overwrite a batch appended after tokenization."""
    mgr, name, path = _make_manager(tmp_path)
    writer, _, _ = _make_manager(tmp_path, path=path)
    history = [HumanMessage(content=f"old-{i} with enough text" * 10) for i in range(8)]
    _write_disk(path, history)
    entered = threading.Event()
    release = threading.Event()
    fence_calls = 0
    fence_guard = threading.Lock()

    def _blocking_first_fence(*args, **kwargs):
        nonlocal fence_calls
        with fence_guard:
            fence_calls += 1
            first = fence_calls == 1
        if first:
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr("memory.recent.assert_cloudsave_writable", _blocking_first_fence)
    monkeypatch.setattr("memory.recent.RECENT_HARD_CAP_TOKENS", 20)

    async def _go():
        task = asyncio.create_task(mgr.enforce_hard_cap(name))
        assert await asyncio.to_thread(entered.wait, 3)
        await asyncio.to_thread(
            writer._append_and_persist_locked,
            path,
            name,
            [HumanMessage(content="append-during-trim")],
        )
        release.set()
        await task

    asyncio.run(_go())
    assert _read_disk(path)[-1].content == "append-during-trim"


def test_hard_cap_commits_disk_plus_pending_and_clears_pending(tmp_path, monkeypatch):
    mgr, name, path = _make_manager(tmp_path)
    disk = [HumanMessage(content=f"disk-{i} with enough text" * 10) for i in range(6)]
    pending = [HumanMessage(content=f"pending-{i} with enough text" * 10) for i in range(2)]
    _write_disk(path, disk)
    with recent_file.recent_file_lock(path):
        recent_file.set_recent_pending_unlocked(path, pending)
    monkeypatch.setattr("memory.recent.RECENT_HARD_CAP_TOKENS", 20)

    asyncio.run(mgr.enforce_hard_cap(name))

    persisted = _read_disk(path)
    assert [message.content for message in persisted[-2:]] == [
        message.content for message in pending
    ]
    assert len(persisted) < len(disk) + len(pending)
    assert recent_file.get_recent_pending(path) == []
    assert [message.content for message in mgr.user_histories[name]] == [
        message.content for message in persisted
    ]


def test_hard_cap_write_failure_preserves_pending(tmp_path, monkeypatch):
    mgr, name, path = _make_manager(tmp_path)
    disk = [HumanMessage(content=f"disk-{i} with enough text" * 10) for i in range(6)]
    pending = [HumanMessage(content="pending-must-survive" * 10)]
    _write_disk(path, disk)
    with recent_file.recent_file_lock(path):
        recent_file.set_recent_pending_unlocked(path, pending)
    monkeypatch.setattr("memory.recent.RECENT_HARD_CAP_TOKENS", 20)
    monkeypatch.setattr(
        recent_file,
        "write_recent_payload_unlocked",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("simulated failure")),
    )

    asyncio.run(mgr.enforce_hard_cap(name))

    assert [message.content for message in _read_disk(path)] == [
        message.content for message in disk
    ]
    assert [message.content for message in recent_file.get_recent_pending(path)] == [
        message.content for message in pending
    ]


def test_hard_cap_cannot_trim_a_new_identity_with_identical_bytes(tmp_path, monkeypatch):
    mgr, name, path = _make_manager(tmp_path)
    history = [HumanMessage(content=f"same-{i} with enough text" * 10) for i in range(6)]
    _write_disk(path, history)
    gate_entered = threading.Event()
    release_gate = threading.Event()

    def _blocking_gate(*args, **kwargs):
        gate_entered.set()
        assert release_gate.wait(3)

    monkeypatch.setattr("memory.recent.RECENT_HARD_CAP_TOKENS", 20)
    monkeypatch.setattr("memory.recent.assert_cloudsave_writable", _blocking_gate)
    errors = []

    def _run_hard_cap():
        try:
            asyncio.run(mgr.enforce_hard_cap(name))
        except Exception as exc:  # noqa: BLE001 - asserted in the main thread
            errors.append(exc)

    worker = threading.Thread(target=_run_hard_cap)
    worker.start()
    assert gate_entered.wait(3)

    recent_file.activate_recent_paths([path])
    recent_file.write_recent_payload(path, messages_to_dict(history))
    release_gate.set()
    worker.join(3)

    assert not worker.is_alive()
    assert errors == []
    assert [message.content for message in _read_disk(path)] == [
        message.content for message in history
    ]


def test_reset_maintenance_error_still_reaches_update_caller(tmp_path, monkeypatch):
    """The reset fence keeps the pre-lock MaintenanceModeError contract."""
    mgr, name, path = _make_manager(tmp_path)
    Path(path).write_text("{broken", encoding="utf-8")
    calls = 0

    def _fence(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise MaintenanceModeError(
                "maintenance_readonly", operation="reset", target="recent.json",
            )

    monkeypatch.setattr("memory.recent.assert_cloudsave_writable", _fence)

    with pytest.raises(MaintenanceModeError):
        asyncio.run(mgr.update_history([HumanMessage(content="new")], name, compress=False))
    assert Path(path).read_text(encoding="utf-8") == "{broken"


# ─────────────── lock identity ───────────────


def test_lock_is_keyed_by_file_not_by_name(tmp_path):
    """The same file reached through different spellings shares one lock."""
    target = tmp_path / "recent.json"
    a = recent_file.recent_file_lock(str(target))
    b = recent_file.recent_file_lock(target)
    c = recent_file.recent_file_lock(str(tmp_path / "sub" / ".." / "recent.json"))
    assert a is b is c
    other = recent_file.recent_file_lock(str(tmp_path / "other.json"))
    assert other is not a


@pytest.mark.skipif(os.name != "nt", reason="Windows path identity is case-insensitive")
def test_lock_key_normalizes_windows_path_case(tmp_path):
    """Case aliases of one Windows path must share the same lock."""
    target = str(tmp_path / "Recent.JSON")
    assert recent_file.recent_file_lock(target) is recent_file.recent_file_lock(target.swapcase())


def test_lock_registry_is_thread_safe(tmp_path):
    """Concurrent first-touch of one path must not mint two locks."""
    target = str(tmp_path / "recent.json")
    seen: list[threading.Lock] = []
    barrier = threading.Barrier(8)

    def _grab():
        barrier.wait()
        seen.append(recent_file.recent_file_lock(target))

    threads = [threading.Thread(target=_grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(lock) for lock in seen}) == 1


@pytest.mark.asyncio
async def test_recent_file_route_maps_deleted_identity_to_not_found(
    tmp_path, monkeypatch,
):
    """A read racing with character deletion preserves the route's 404 contract."""
    import main_routers.memory_router as memory_router
    import utils.config_manager as config_manager_module

    recent_path = tmp_path / "Role" / "recent.json"
    recent_path.parent.mkdir()
    recent_path.write_text("[]", encoding="utf-8")

    class _Config:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    def _raise_deleted(_path):
        raise recent_file.RecentFileDeletedError("deleted during read")

    monkeypatch.setattr(config_manager_module, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(memory_router, "read_recent_text", _raise_deleted)

    response = await memory_router.get_recent_file("recent_Role.json")

    assert response.status_code == 404
    assert json.loads(response.body)["error"] == "文件不存在"
