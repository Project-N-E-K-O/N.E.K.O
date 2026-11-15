"""proactive_source_history.json must be persisted inside _source_history_lock."""

# This cross-character single file needs a lock covering disk writes, not only memory: (a) concurrent submissions
# can os.replace the same target simultaneously (PermissionError/WinError 5 on Windows); (b) snapshots taken
# inside the lock but written outside may land in reverse order, overwriting newer history. Pin both properties.

from __future__ import annotations

import asyncio

import pytest

from main_logic.proactive_chat import state


@pytest.fixture(autouse=True)
def _isolate_source_history():
    """Restore the module-level history between tests so cases cannot leak into each other."""
    saved = dict(state._source_history)
    saved_loaded = state._source_history_loaded
    saved_path = state._source_history_loaded_path
    state._source_history.clear()
    # Start from never-loaded state: _record_source_used now calls
    # _ensure_source_history_loaded itself. No history file exists under this test's tmp_path, so loading hits
    # FileNotFoundError (normal empty history); only entries written by this test reach the critical section.
    state._source_history_loaded = False
    state._source_history_loaded_path = None
    yield
    state._source_history.clear()
    state._source_history.update(saved)
    state._source_history_loaded = saved_loaded
    state._source_history_loaded_path = saved_path


@pytest.mark.asyncio
async def test_source_history_write_happens_inside_the_lock(monkeypatch, tmp_path) -> None:
    observed: list[tuple[bool, frozenset[str]]] = []

    async def fake_write(path, payload, **kwargs):
        # Only one holder of _source_history_lock is possible in this test, making locked() a valid proxy.
        observed.append((state._source_history_lock.locked(), frozenset(payload["entries"])))

    monkeypatch.setattr(state, "atomic_write_json_async", fake_write)

    await state._record_source_used(
        url="https://example.test/a",
        kind="web",
        title="a",
        memory_dir=tmp_path,
    )

    expected_hash = state._source_hash("https://example.test/a", "a")
    assert observed == [(True, frozenset({expected_hash}))]


@pytest.mark.asyncio
async def test_concurrent_source_records_serialize_and_never_reorder(
    monkeypatch, tmp_path
) -> None:
    in_flight = 0
    max_in_flight = 0
    landed: list[frozenset[str]] = []

    async def fake_write(path, payload, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        # Yield the event loop: if persistence is outside the lock, another submission can enter here.
        await asyncio.sleep(0)
        landed.append(frozenset(payload["entries"]))
        in_flight -= 1

    monkeypatch.setattr(state, "atomic_write_json_async", fake_write)

    await asyncio.gather(
        *[
            state._record_source_used(
                url=f"https://example.test/{index}",
                kind="web",
                title=f"t{index}",
                memory_dir=tmp_path,
            )
            for index in range(5)
        ]
    )

    # (a) Serialization: only one writer in flight at any instant, preventing concurrent os.replace on one target.
    assert max_in_flight == 1
    # (b) No inversion: later persistence must be a strict superset of the previous write. Merely asserting five final entries
    #     always passes and cannot detect reversed snapshot/write order.
    assert len(landed) == 5
    for previous, current in zip(landed, landed[1:]):
        assert previous < current, f"落盘顺序反转: {sorted(previous)} 之后落了 {sorted(current)}"
    assert len(landed[-1]) == 5


@pytest.mark.asyncio
async def test_cancelling_a_record_does_not_release_the_lock_before_the_write_ends(
    monkeypatch, tmp_path
) -> None:
    """A cancelled _record_source_used must keep the lock until its in-flight write finishes."""
    # atomic_write_json_async uses asyncio.to_thread, which cannot be cancelled once submitted. A direct await
    # lets exit cancellation release async with's lock as CancelledError propagates, while os.replace
    # remains in flight, allowing another submission to enter and concurrently replace the same target.
    write_started = asyncio.Event()
    let_write_finish = asyncio.Event()

    async def blocked_write(path, payload, **kwargs):
        write_started.set()
        await let_write_finish.wait()

    monkeypatch.setattr(state, "atomic_write_json_async", blocked_write)

    task = asyncio.create_task(
        state._record_source_used(
            url="https://example.test/a", kind="web", title="a", memory_dir=tmp_path
        )
    )
    await asyncio.wait_for(write_started.wait(), timeout=5)
    assert state._source_history_lock.locked(), "前置条件：写在飞的时候锁是持有状态"

    task.cancel()
    # Do not guess cancellation propagation with a fixed number of yields: observe real time briefly and recheck every iteration.
    # Also cover repeated cancellation: timeout cancellation followed by application exit is a real combination.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 0.1
    while loop.time() < deadline:
        await asyncio.sleep(0.005)
        task.cancel()
        assert state._source_history_lock.locked(), "写还在飞，锁不许因为取消就提前放掉"
        assert not task.done(), "写还没放行，任务不该已经收尾"

    let_write_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not state._source_history_lock.locked(), "写收尾之后锁必须放掉"


def test_source_history_persist_helper_takes_its_own_snapshot() -> None:
    """The persist helper takes no snapshot argument: snapshot and write are inseparable."""
    # Restoring a snapshot parameter would let callers snapshot inside the lock and write outside again,
    # reopening ordering inversions. This guards the design decision itself, not one observed execution.
    import inspect

    parameters = inspect.signature(state._persist_source_history_unlocked).parameters
    assert set(parameters) == {"memory_dir"}
