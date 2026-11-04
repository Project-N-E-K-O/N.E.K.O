# -*- coding: utf-8 -*-
"""A cold-start /new_dialog preempts the summary LLM held under the settle lock (#3601).

Starting a conversation waits for /new_dialog, which takes the per-character settle lock;
/renew and /settle hold that lock across the summary LLM (30 s timeout x 3 retries, up to
three rounds). The summary is abandoned like a failed one instead: its messages are already
persisted and background compression takes over.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.recent import CompressedRecentHistoryManager
from utils.llm_client import AIMessage, HumanMessage, SystemMessage, messages_from_dict, messages_to_dict


class _FakeConfig:
    def __init__(self, lanlan_name: str, recent_path: str):
        self._lanlan_name = lanlan_name
        self._recent_path = recent_path

    async def aget_character_data(self):
        return (None, None, None, None, {}, None, None, None, {self._lanlan_name: self._recent_path})


@pytest.fixture(autouse=True)
def _patch_cloudsave(monkeypatch):
    monkeypatch.setattr("memory.recent.assert_cloudsave_writable", lambda *a, **kw: None)


@pytest.fixture
def runtime(monkeypatch):
    from app.memory_server import runtime as memory_runtime

    monkeypatch.setattr(memory_runtime, "_settle_locks", {})
    monkeypatch.setattr(memory_runtime, "_settle_preemption_waiters", {})
    monkeypatch.setattr(memory_runtime, "_settle_preemptible_sections", {})
    return memory_runtime


class _HangingSummary:
    """compress_history stand-in that never finishes on its own."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls = 0
        self.cancelled = False

    async def __call__(self, *_args, **_kwargs):
        self.calls += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _make_manager(tmp_path: Path, name: str = "Neko") -> CompressedRecentHistoryManager:
    recent_path = str(tmp_path / "recent.json")
    mgr = object.__new__(CompressedRecentHistoryManager)
    mgr._config_manager = _FakeConfig(name, recent_path)
    mgr.max_history_length = 4
    mgr.compress_threshold = 5
    mgr.log_file_path = {name: recent_path}
    mgr.name_mapping = {"human": "Master", "ai": name, "system": "SYSTEM_MESSAGE"}
    mgr.user_histories = {name: []}
    old = [HumanMessage(content=f"old user {i}") if i % 2 == 0 else AIMessage(content=f"old ai {i}")
           for i in range(5)]
    with open(recent_path, "w", encoding="utf-8") as f:
        json.dump(messages_to_dict(old), f, ensure_ascii=False)
    return mgr


def _read_recent(mgr: CompressedRecentHistoryManager, name: str = "Neko") -> list:
    with open(mgr.log_file_path[name], encoding="utf-8") as f:
        return messages_from_dict(json.load(f))


class _CompressDone:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def __call__(self, name, snapshot, ok, detailed, admission_generation=None):
        self.calls.append((name, len(snapshot), ok, detailed))


@pytest.mark.asyncio
async def test_preempted_summary_is_abandoned_to_background_compression(tmp_path):
    mgr = _make_manager(tmp_path)
    summary = _HangingSummary()
    mgr.compress_history = summary
    done = _CompressDone()
    preempted = asyncio.get_running_loop().create_future()

    task = asyncio.create_task(mgr.update_history(
        [AIMessage(content="new ai")], "Neko", detailed=True,
        on_compress_done=done, preempted=preempted,
    ))
    await asyncio.wait_for(summary.started.wait(), 1)
    preempted.set_result(None)
    await asyncio.wait_for(task, 1)

    assert summary.cancelled
    # Handed to background compression the way a failed summary is.
    assert done.calls == [("Neko", 3, False, True)]
    # The new message was persisted before the summary started and is not lost.
    assert [m.content for m in _read_recent(mgr)][-1] == "new ai"
    assert len(_read_recent(mgr)) == 6


@pytest.mark.asyncio
async def test_summary_entered_after_preemption_never_calls_the_llm(tmp_path):
    mgr = _make_manager(tmp_path)
    summary = _HangingSummary()
    mgr.compress_history = summary
    done = _CompressDone()
    preempted = asyncio.get_running_loop().create_future()
    preempted.set_result(None)

    await asyncio.wait_for(mgr.update_history(
        [AIMessage(content="new ai")], "Neko", detailed=True,
        on_compress_done=done, preempted=preempted,
    ), 1)

    assert summary.calls == 0
    assert done.calls == [("Neko", 3, False, True)]


@pytest.mark.asyncio
async def test_unpreempted_summary_is_spliced_as_before(tmp_path):
    mgr = _make_manager(tmp_path)

    async def _summary(*_args, **_kwargs):
        return SystemMessage(content="memo"), "memo"

    mgr.compress_history = _summary
    done = _CompressDone()
    preempted = asyncio.get_running_loop().create_future()

    await mgr.update_history(
        [AIMessage(content="new ai")], "Neko", detailed=True,
        on_compress_done=done, preempted=preempted,
    )

    assert done.calls == [("Neko", 3, True, True)]
    assert _read_recent(mgr)[0].content == "memo"


@pytest.mark.asyncio
async def test_cold_start_takes_the_settle_lock_without_waiting_for_the_summary(tmp_path, runtime):
    mgr = _make_manager(tmp_path)
    summary = _HangingSummary()
    mgr.compress_history = summary
    done = _CompressDone()
    lock = runtime._get_settle_lock("Neko")

    async def renew():
        async with lock:
            with runtime._settle_summary_preemption("Neko") as preempted:
                await mgr.update_history(
                    [AIMessage(content="new ai")], "Neko", detailed=True,
                    on_compress_done=done, preempted=preempted,
                )

    renew_task = asyncio.create_task(renew())
    await asyncio.wait_for(summary.started.wait(), 1)

    # A hot-swap /new_dialog does not preempt: it waits for the summary.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(lock.acquire(), 0.1)

    with runtime._preempting_settle_summaries("Neko"):
        await asyncio.wait_for(lock.acquire(), 1)
        lock.release()

    await asyncio.wait_for(renew_task, 1)
    assert summary.cancelled
    assert done.calls == [("Neko", 3, False, True)]
    assert runtime._settle_preemption_waiters == {}
    assert runtime._settle_preemptible_sections == {}


@pytest.mark.asyncio
async def test_summary_queued_ahead_of_a_cold_start_starts_preempted(tmp_path, runtime):
    """A /renew already waiting for the lock when the cold start arrives gets it first
    (FIFO), so its summary must skip the LLM too, not only one already running."""
    mgr = _make_manager(tmp_path)
    summary = _HangingSummary()
    mgr.compress_history = summary
    lock = runtime._get_settle_lock("Neko")
    await lock.acquire()  # e.g. a /cache in progress

    async def renew():
        async with lock:
            with runtime._settle_summary_preemption("Neko") as preempted:
                await mgr.update_history(
                    [AIMessage(content="new ai")], "Neko", detailed=True,
                    on_compress_done=_CompressDone(), preempted=preempted,
                )

    acquired = asyncio.Event()

    async def cold_start():
        with runtime._preempting_settle_summaries("Neko"):
            async with lock:
                acquired.set()

    renew_task = asyncio.create_task(renew())
    await asyncio.sleep(0)
    cold_task = asyncio.create_task(cold_start())
    await asyncio.sleep(0)
    lock.release()

    await asyncio.wait_for(asyncio.gather(renew_task, cold_task), 1)
    assert acquired.is_set()
    assert summary.calls == 0


@pytest.mark.asyncio
async def test_other_characters_summaries_are_not_preempted(runtime):
    with runtime._settle_summary_preemption("Other") as preempted:
        with runtime._preempting_settle_summaries("Neko"):
            assert not preempted.done()
        with runtime._preempting_settle_summaries("Other"):
            assert preempted.done()


def _route_patches(memory_server, update_history):
    fake_config = MagicMock()
    fake_config.aload_characters = AsyncMock(return_value={"猫娘": {"测试角色": {}}})
    fake_recent = MagicMock()
    fake_recent.update_history = update_history
    fake_time = MagicMock()
    fake_time.astore_conversation = AsyncMock(return_value=None)
    return [
        patch.object(memory_server.runtime, "_config_manager", fake_config),
        patch.object(memory_server.runtime, "embedding_warmup_worker", None),
        patch.object(memory_server.runtime, "time_manager", fake_time),
        patch.object(memory_server.runtime, "recent_history_manager", fake_recent),
        patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", AsyncMock()),
        patch.object(memory_server.gates, "_aclear_review_clean", AsyncMock()),
        patch.object(memory_server.review, "maybe_spawn_review", AsyncMock()),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["process_conversation_for_renew", "settle_conversation"])
async def test_renew_and_settle_summaries_are_preemptible(runtime, endpoint):
    from app import memory_server

    seen = []

    async def update_history(*_args, preempted=None, **_kwargs):
        seen.append(preempted)
        assert preempted in runtime._settle_preemptible_sections["测试角色"]
        assert runtime._get_settle_lock("测试角色").locked()

    request = memory_server.HistoryRequest(input_history=json.dumps([]))
    patches = _route_patches(memory_server, update_history)
    for p in patches:
        p.start()
    try:
        await getattr(memory_server, endpoint)(request, "测试角色")
    finally:
        for p in reversed(patches):
            p.stop()

    assert len(seen) == 1 and isinstance(seen[0], asyncio.Future)
    assert runtime._settle_preemptible_sections == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("preempt_summary, expected", [(True, 1), (False, None)])
async def test_new_dialog_preempts_only_when_asked(runtime, monkeypatch, preempt_summary, expected):
    from app.memory_server import routes

    seen = []

    async def fake_new_dialog(name, *_args):
        seen.append(runtime._settle_preemption_waiters.get(name))
        return "ok"

    monkeypatch.setattr(routes, "_new_dialog", fake_new_dialog)
    assert await routes.new_dialog("Neko", preempt_summary=preempt_summary) == "ok"
    assert seen == [expected]
    assert runtime._settle_preemption_waiters == {}
