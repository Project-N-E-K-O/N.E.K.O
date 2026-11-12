# -*- coding: utf-8 -*-
"""The sync connector never waits for the memory server (#3601).

Memory writes run on one ordered lane: a hot-reset /renew (whose summary may take minutes)
must not hold up the loop that forwards the live conversation and dispatches turn-check
requests, and must still reach the memory server before the next turn's /cache.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest


def _user(text):
    return {"type": "user", "data": {"data": text, "input_type": "transcript"}}


def _reply(text):
    return {"type": "json", "data": {"type": "gemini_response", "text": text, "isNewMessage": True}}


def _turn(user_text, reply_text):
    return [_user(user_text), _reply(reply_text), {"type": "system", "data": "turn end"}]


def _texts(payload):
    return [m["content"][0]["text"].split("] ")[-1] for m in payload]


async def _until(predicate, timeout=1.0):
    async def _poll():
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_poll(), timeout)


class _Memory:
    """Fake _post_memory_server; endpoints listed in ``blocked`` wait for ``release``."""

    def __init__(self, *, blocked=(), results=None):
        self.posts: list[tuple[str, list]] = []
        self.blocked = set(blocked)
        self.release = asyncio.Event()
        self.results = dict(results or {})

    async def __call__(self, endpoint, name, payload, *, timeout_s, language=None, render_language=None):
        self.posts.append((endpoint, list(payload)))
        if endpoint in self.blocked:
            await self.release.wait()
        result = self.results.get(endpoint, (True, "", {}))
        if isinstance(result, list):
            result = result.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def endpoints(self):
        return [endpoint for endpoint, _ in self.posts]


@pytest.fixture
def connector(monkeypatch):
    from main_logic import cross_server

    started = []

    def start(memory, *, status_callback=None):
        turn_check = AsyncMock(return_value=True)
        monkeypatch.setattr(cross_server, "_post_memory_server", memory)
        monkeypatch.setattr(cross_server, "_publish_turn_check_with_fallback", turn_check)
        queue = asyncio.Queue()
        task = asyncio.create_task(cross_server.run_sync_connector(
            queue, "Lan", config={"monitor": False, "bullet": False},
            status_callback=status_callback,
        ))
        started.append(task)
        return queue, turn_check

    yield start
    for task in started:
        task.cancel()


@pytest.mark.asyncio
async def test_hot_reset_does_not_hold_up_the_live_conversation(connector):
    memory = _Memory(blocked={"renew"})
    queue, turn_check = connector(memory)

    for item in _turn("u1", "a1"):
        queue.put_nowait(item)
    await _until(lambda: memory.endpoints() == ["cache"])
    queue.put_nowait({"type": "user", "data": {"data": "u2", "input_type": "transcript"}})
    queue.put_nowait({"type": "system", "data": "renew session"})
    await _until(lambda: memory.endpoints() == ["cache", "renew"])

    # The renew summary is still running; the next turn is processed meanwhile.
    for item in _turn("u3", "a3"):
        queue.put_nowait(item)
    await _until(lambda: turn_check.await_count == 2)
    assert memory.endpoints() == ["cache", "renew"]

    # Its /cache reaches the memory server only after the renew, as before.
    memory.release.set()
    await _until(lambda: memory.endpoints() == ["cache", "renew", "cache"])
    assert _texts(memory.posts[1][1]) == ["u2"]
    assert _texts(memory.posts[2][1]) == ["u3", "a3"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [(False, "HTTP 503", {}), httpx.ReadTimeout("slow summary")])
async def test_hot_reset_memory_failure_is_not_shown_to_the_user(connector, failure):
    memory = _Memory(results={"renew": failure})
    status = []
    queue, _ = connector(memory, status_callback=status.append)

    queue.put_nowait(_user("u1"))
    queue.put_nowait({"type": "system", "data": "renew session"})
    for item in _turn("u2", "a2"):
        queue.put_nowait(item)

    await _until(lambda: memory.endpoints() == ["renew", "cache"])
    assert status == []


@pytest.mark.asyncio
async def test_rejected_turn_end_cache_is_resent_with_the_next_write(connector):
    rejection = (False, "storage unavailable", {"status": "error", "write_started": False})
    memory = _Memory(results={"cache": [rejection, (True, "", {})]})
    queue, _ = connector(memory)

    for item in _turn("u1", "a1") + _turn("u2", "a2"):
        queue.put_nowait(item)
    await _until(lambda: memory.endpoints() == ["cache", "cache"])
    assert _texts(memory.posts[1][1]) == ["u1", "a1", "u2", "a2"]

    queue.put_nowait({"type": "system", "data": "renew session"})
    await _until(lambda: len(memory.posts) == 3)
    # Everything was accepted, so the hot reset only settles.
    assert memory.posts[2] == ("settle", [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "endpoint", "expected_texts"),
    [
        (httpx.ConnectTimeout("not sent"), "renew", ["u1", "a1"]),
        (httpx.ReadTimeout("possibly written"), "settle", []),
        ((False, "HTTP 503", {}), "settle", []),
    ],
)
async def test_hot_reset_carries_only_cache_writes_known_not_to_have_started(
    connector, failure, endpoint, expected_texts,
):
    memory = _Memory(results={"cache": failure})
    queue, _ = connector(memory)

    for item in _turn("u1", "a1"):
        queue.put_nowait(item)
    queue.put_nowait({"type": "system", "data": "renew session"})
    await _until(lambda: memory.endpoints() == ["cache", endpoint])
    assert _texts(memory.posts[1][1]) == expected_texts


@pytest.mark.asyncio
async def test_session_end_barrier_follows_the_settlement_without_blocking_the_loop(connector):
    # A session end carrying a callback is the language-switch barrier, which writes via /cache.
    memory = _Memory(blocked={"cache"})
    queue, turn_check = connector(memory)
    order = []
    completion = asyncio.get_running_loop().create_future()

    queue.put_nowait(_user("u1"))
    queue.put_nowait(_reply("a1"))
    queue.put_nowait({
        "type": "system", "data": "session end",
        "_after_memory_settlement": lambda: order.append("callback"),
        "_memory_settlement_done": completion,
    })
    await _until(lambda: memory.endpoints() == ["cache"])

    for item in _turn("u2", "a2"):
        queue.put_nowait(item)
    await _until(lambda: turn_check.await_count == 2)
    assert order == [] and not completion.done()

    memory.release.set()
    await asyncio.wait_for(completion, 1)
    assert order == ["callback"]
    assert _texts(memory.posts[0][1]) == ["u1", "a1"]
    await _until(lambda: memory.endpoints() == ["cache", "cache"])


def _avatar_turn(rank):
    return [_reply("喵"), {"type": "system", "data": "turn end", "meta": {
        "kind": "avatar_interaction", "memory_note": "摸头",
        "memory_dedupe_key": "head", "memory_dedupe_rank": rank,
    }}]


@pytest.mark.asyncio
async def test_two_failed_avatar_writes_do_not_suppress_the_next_interaction(connector):
    """A (rank 1) and B (rank 2, same key) are both queued before either write finishes.
    When both fail, neither may keep suppressing a later rank-1 interaction."""
    failed = (False, "HTTP 503", {})
    memory = _Memory(blocked={"cache"}, results={"cache": [failed, failed, (True, "", {})]})
    queue, _ = connector(memory)

    for item in _avatar_turn(1) + _avatar_turn(2):
        queue.put_nowait(item)
    await _until(lambda: memory.endpoints() == ["cache"])
    memory.release.set()
    await _until(lambda: memory.endpoints() == ["cache", "cache"])
    await asyncio.sleep(0.05)  # let B's rollback run

    for item in _avatar_turn(1):
        queue.put_nowait(item)
    await _until(lambda: memory.endpoints() == ["cache", "cache", "cache"])


@pytest.mark.asyncio
async def test_stopping_connector_sends_queued_settlement(monkeypatch):
    """A session end queued behind a slow write still goes out if the lane drains in time."""
    from main_logic import cross_server

    memory = _Memory(blocked={"renew"})
    monkeypatch.setattr(cross_server, "_post_memory_server", memory)
    monkeypatch.setattr(cross_server, "_publish_turn_check_with_fallback", AsyncMock(return_value=True))
    queue = asyncio.Queue()
    task = asyncio.create_task(cross_server.run_sync_connector(
        queue, "Lan", config={"monitor": False, "bullet": False},
    ))
    queue.put_nowait(_user("u1"))
    queue.put_nowait({"type": "system", "data": "renew session"})
    queue.put_nowait(_user("u2"))
    queue.put_nowait(_reply("a2"))
    queue.put_nowait({"type": "system", "data": "session end"})
    await _until(lambda: memory.endpoints() == ["renew"] and queue.empty())
    await asyncio.sleep(0.02)

    task.cancel()
    await asyncio.sleep(0.1)
    memory.release.set()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)

    assert memory.endpoints() == ["renew", "process"]
    assert _texts(memory.posts[1][1]) == ["u2", "a2"]


@pytest.mark.asyncio
async def test_memory_write_backlog_is_logged(connector, monkeypatch):
    from main_logic import cross_server

    monkeypatch.setattr(cross_server, "MEMORY_LANE_BACKLOG_WARN", 2)
    warnings = []
    monkeypatch.setattr(cross_server.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    memory = _Memory(blocked={"cache"})
    queue, _ = connector(memory)

    for i in range(4):
        for item in _turn(f"u{i}", f"a{i}"):
            queue.put_nowait(item)
    await _until(lambda: any("积压" in w for w in warnings))
    memory.release.set()


@pytest.mark.asyncio
async def test_drain_timeout_reports_the_write_in_flight(monkeypatch):
    from main_logic import cross_server

    monkeypatch.setattr(cross_server, "MEMORY_LANE_DRAIN_TIMEOUT_S", 0.1)
    warnings = []
    monkeypatch.setattr(cross_server.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    memory = _Memory(blocked={"renew"})
    monkeypatch.setattr(cross_server, "_post_memory_server", memory)
    queue = asyncio.Queue()
    task = asyncio.create_task(cross_server.run_sync_connector(
        queue, "Lan", config={"monitor": False, "bullet": False},
    ))
    queue.put_nowait(_user("u1"))
    queue.put_nowait({"type": "system", "data": "renew session"})
    await _until(lambda: memory.endpoints() == ["renew"])

    task.cancel()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
    assert any("执行中 1 个，排队中 0 个" in w for w in warnings)


@pytest.mark.asyncio
async def test_memory_flush_resolves_after_the_writes_queued_before_it(connector):
    memory = _Memory(blocked={"renew"})
    queue, _ = connector(memory)
    done = asyncio.get_running_loop().create_future()

    queue.put_nowait(_user("u1"))
    queue.put_nowait({"type": "system", "data": "renew session"})
    for item in _turn("u2", "a2"):
        queue.put_nowait(item)
    queue.put_nowait({"type": "system", "data": "memory flush", "_memory_flush_done": done})
    await _until(lambda: memory.endpoints() == ["renew"])
    await asyncio.sleep(0.02)
    assert not done.done()

    memory.release.set()
    await asyncio.wait_for(done, 1)
    assert memory.endpoints() == ["renew", "cache"]
