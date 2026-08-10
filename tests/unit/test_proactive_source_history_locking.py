"""proactive_source_history.json must be persisted inside _source_history_lock."""

# 这是个跨角色的单文件：锁只盖内存不盖落盘时，(a) 两个并发投递会同时 os.replace 同一个
# 目标（Windows 上互相 PermissionError/WinError 5），(b) 快照在锁内取、写在锁外发，两次
# 投递抵达磁盘的顺序可以和取快照的顺序反过来，把新历史写回旧值。这里两条都钉住。

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
    # 从「完全没加载过」起步：_record_source_used 现在自己会先
    # _ensure_source_history_loaded，用例的 tmp_path 下没有历史文件，那次加载走
    # FileNotFoundError（正常的空历史），落到临界区的仍然只有本用例写进去的条目。
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
        # 这个用例里 _source_history_lock 只可能有一个持有者，locked() 是有效代理。
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
        # 让出事件循环：落盘在锁外时，另一个投递会在这里挤进来。
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

    # (a) 串行化：任一时刻只有一个写者在飞 → 不会有两个并发 os.replace 打同一目标。
    assert max_in_flight == 1
    # (b) 不反转：后落盘的必须是前一次的严格超集。写成「最终文件有 5 条」是恒真的，
    #     抓不到「快照顺序与写入顺序反过来」这个形态。
    assert len(landed) == 5
    for previous, current in zip(landed, landed[1:]):
        assert previous < current, f"落盘顺序反转: {sorted(previous)} 之后落了 {sorted(current)}"
    assert len(landed[-1]) == 5


@pytest.mark.asyncio
async def test_cancelling_a_record_does_not_release_the_lock_before_the_write_ends(
    monkeypatch, tmp_path
) -> None:
    """A cancelled _record_source_used must keep the lock until its in-flight write finishes."""
    # atomic_write_json_async 内部是 asyncio.to_thread，交出去就取消不掉。直接 await 的话，
    # 退出时的 cancel 会让 async with 在 CancelledError 穿过的一刻放锁，而那次 os.replace
    # 还在飞 —— 另一个投递就能进临界区并发 replace 同一个目标。
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
    # 不靠「固定 yield 几次」猜取消传播完没有：按真实时钟走一小段，期间每圈都复查。
    # 顺带把重复取消也覆盖掉 —— 超时取消之后再来一次应用退出，是真实会发生的组合。
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
    # 一旦有人给它加回 snapshot 形参，调用方就又能在锁内取快照、到锁外去写，顺序反转
    # 窗口重新出现。这条守卫的是那个设计决定本身，不是某一次的行为。
    import inspect

    parameters = inspect.signature(state._persist_source_history_unlocked).parameters
    assert set(parameters) == {"memory_dir"}
