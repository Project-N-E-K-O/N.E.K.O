"""``create_run`` honours ``idempotency_key`` (issue #3322).

A caller whose ``POST /runs`` response was lost retries with the same key;
the retry must get the original run back instead of executing the entry a
second time. Requests without a key, or with a different key / entry, keep
creating a fresh run each time.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin._types.models import RunCreateRequest
from plugin.runs import manager


@pytest.fixture
def runs(monkeypatch):
    """Fresh stores plus a fake trigger that counts executions per entry."""
    store = manager.InMemoryRunStore()
    monkeypatch.setattr(manager, "_run_store", store)
    monkeypatch.setattr(manager, "_export_store", manager.InMemoryExportStore())
    monkeypatch.setattr(manager, "_emit_runs", lambda *_a, **_k: None)
    monkeypatch.setattr(manager, "_emit_export", lambda *_a, **_k: None)
    monkeypatch.setattr(manager, "_active_run_tasks", {})

    calls: list[str] = []
    gate = SimpleNamespace(event=None, entered=asyncio.Event())

    async def fake_trigger(*, plugin_id, entry_id, args, task_id, client_host):
        calls.append(entry_id)
        gate.entered.set()
        if gate.event is not None:
            await gate.event.wait()
        return SimpleNamespace(plugin_response={"success": True, "data": {"ok": 1}})

    monkeypatch.setattr(manager, "trigger_plugin", fake_trigger)
    return SimpleNamespace(store=store, calls=calls, gate=gate)


def _req(*, key=None, entry_id="send"):
    return RunCreateRequest(plugin_id="demo", entry_id=entry_id, args={}, idempotency_key=key)


async def _drain():
    tasks = list(manager._active_run_tasks.values())
    if tasks:
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_retry_after_lost_response_executes_once(runs):
    first = await manager.create_run(_req(key="call-1"), client_host=None)
    await _drain()  # the run finished; its response never reached the caller

    retry = await manager.create_run(_req(key="call-1"), client_host=None)
    await _drain()

    assert retry.run_id == first.run_id
    assert retry.status == "succeeded"
    assert runs.calls == ["send"]
    assert len(manager.list_runs()) == 1


@pytest.mark.asyncio
async def test_retry_while_first_run_is_still_executing_reuses_it(runs):
    runs.gate.event = asyncio.Event()
    first = await manager.create_run(_req(key="call-1"), client_host=None)
    await runs.gate.entered.wait()  # the run is executing, blocked inside the entry
    assert runs.calls == ["send"]

    retry = await manager.create_run(_req(key="call-1"), client_host=None)
    assert retry.run_id == first.run_id
    assert retry.status == "running"

    runs.gate.event.set()
    await _drain()
    assert runs.calls == ["send"]


@pytest.mark.asyncio
async def test_concurrent_requests_with_same_key_create_one_run(runs):
    runs.gate.event = asyncio.Event()
    results = await asyncio.gather(
        manager.create_run(_req(key="call-1"), client_host=None),
        manager.create_run(_req(key="call-1"), client_host=None),
    )
    runs.gate.event.set()
    await _drain()
    assert results[0].run_id == results[1].run_id
    assert runs.calls == ["send"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second",
    [
        pytest.param({"key": "call-2"}, id="different-key"),
        pytest.param({"key": "call-1", "entry_id": "other"}, id="same-key-other-entry"),
    ],
)
async def test_different_key_or_entry_creates_new_run(runs, second):
    first = await manager.create_run(_req(key="call-1"), client_host=None)
    other = await manager.create_run(_req(**second), client_host=None)
    await _drain()
    assert other.run_id != first.run_id
    assert len(runs.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, ""], ids=["none", "empty"])
async def test_without_key_every_request_creates_a_run(runs, key):
    a = await manager.create_run(_req(key=key), client_host=None)
    b = await manager.create_run(_req(key=key), client_host=None)
    await _drain()
    assert a.run_id != b.run_id
    assert a.status == b.status == "queued"
    assert runs.calls == ["send", "send"]


@pytest.mark.asyncio
async def test_key_stops_matching_once_its_run_is_evicted(runs, monkeypatch):
    store = manager.InMemoryRunStore(max_completed=1)
    monkeypatch.setattr(manager, "_run_store", store)

    first = await manager.create_run(_req(key="call-1"), client_host=None)
    await _drain()
    # A second finished run pushes the first one out of the store.
    await manager.create_run(_req(key="call-2"), client_host=None)
    await _drain()
    assert manager.get_run(first.run_id) is None

    again = await manager.create_run(_req(key="call-1"), client_host=None)
    await _drain()
    assert again.run_id != first.run_id
    assert runs.calls == ["send", "send", "send"]
    # Evicted runs leave no index entries behind: only the newest run is held.
    assert store._by_idempotency == {("demo", "send", "call-1"): again.run_id}
