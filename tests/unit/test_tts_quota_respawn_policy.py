"""Timed TTS respawn policy after a free-server rejection.

A spent quota is only restored when the server's per-IP 24h window rolls
over, and the server never says when. Timed respawns were rejected every
~14.5s for hours. The policy pinned here: no timed respawn for quota (the
next reply's implicit respawn is the retry), doubling delays for transient
rate limits, the old fixed delay for everything else.
"""

import asyncio
import json
import queue
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.core import LLMSessionManager
from main_logic.core._shared import (
    TTS_RATE_LIMIT_MAX_RESPAWN_DELAY_SECONDS,
    TTS_RESPAWN_DELAY_SECONDS,
)


@pytest.fixture(autouse=True)
def _no_telemetry(monkeypatch):
    # The handler's __error__ arm counts tts_error and reads the TokenTracker
    # singleton, which would otherwise register an atexit usage report.
    import utils.instrument
    import utils.token_tracker

    monkeypatch.setattr(utils.instrument, "counter", lambda *_a, **_k: None)
    monkeypatch.setattr(
        utils.token_tracker.TokenTracker,
        "get_instance",
        classmethod(lambda cls: MagicMock()),
    )


def _make_mgr():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.tts_response_queue = queue.Queue()
    mgr.current_speech_id = "sid-current"
    mgr.tts_cache_lock = asyncio.Lock()
    mgr.tts_ready = False
    mgr.tts_pending_chunks = []
    mgr._last_tts_error_code = ""
    mgr._tts_retry_notify_count = 0
    mgr._tts_respawn_task = None
    mgr._bg_tasks = set()
    mgr.session = object()
    mgr.use_tts = True
    mgr.is_active = True
    mgr._tts_active_provider_key = None
    mgr.send_status = AsyncMock()
    mgr._respawn_tts_worker = MagicMock()
    mgr.flushed = []

    async def flush():
        async with mgr.tts_cache_lock:
            mgr.flushed.append(list(mgr.tts_pending_chunks))
            mgr.tts_pending_chunks.clear()

    mgr._flush_tts_pending_chunks = flush
    return mgr


def _error(code):
    return ("__error__", json.dumps({"code": code, "data": {"message": "server close"}}))


async def _wait_for(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


async def _drain(mgr):
    """Run the handler until it has consumed everything queued so far."""
    task = asyncio.create_task(LLMSessionManager.tts_response_handler(mgr))
    try:
        assert await _wait_for(mgr.tts_response_queue.empty)
        # The last item was dequeued; give its handling a few loop turns.
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.fixture
def respawn_delays(monkeypatch):
    """Record timed-respawn sleeps and let them elapse immediately."""
    delays = []
    real_sleep = asyncio.sleep

    async def sleep(seconds, *args, **kwargs):
        if seconds >= TTS_RESPAWN_DELAY_SECONDS:
            delays.append(seconds)
            return await real_sleep(0)
        return await real_sleep(seconds, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    return delays


@pytest.mark.asyncio
async def test_quota_not_ready_schedules_no_timed_respawn(respawn_delays):
    mgr = _make_mgr()
    mgr.tts_pending_chunks = [("sid-old", "rejected reply")]
    mgr.tts_response_queue.put(_error("API_QUOTA_TIME"))
    mgr.tts_response_queue.put(("__ready__", False))

    await _drain(mgr)

    assert mgr._tts_respawn_task is None
    assert respawn_delays == []
    mgr._respawn_tts_worker.assert_not_called()
    assert mgr._tts_quota_blocked is True
    assert mgr.tts_pending_chunks == []
    assert json.loads(mgr.send_status.await_args.args[0])["code"] == "API_QUOTA_TIME"


@pytest.mark.asyncio
async def test_quota_does_not_block_the_implicit_respawn_on_the_next_reply():
    mgr = _make_mgr()
    mgr.tts_response_queue.put(_error("API_QUOTA_TIME"))
    mgr.tts_response_queue.put(("__ready__", False))

    await _drain(mgr)

    # The next reply's first chunk calls _respawn_tts_worker; its only gate
    # on the error code is NO_RETRY_TTS_CODES.
    from main_logic.core._shared import NO_RETRY_TTS_CODES

    assert mgr._last_tts_error_code == "API_QUOTA_TIME"
    assert mgr._last_tts_error_code not in NO_RETRY_TTS_CODES


@pytest.mark.asyncio
async def test_recovery_after_quota_replays_only_the_reply_that_retried():
    mgr = _make_mgr()
    mgr._tts_quota_blocked = True
    # Tail chunks of an earlier rejected reply, then the reply whose first
    # chunk triggered the successful respawn.
    mgr.tts_pending_chunks = [
        ("sid-stale", "tail of a rejected reply"),
        ("sid-live", "first chunk"),
        ("sid-live", "second chunk"),
    ]
    mgr.tts_response_queue.put(("__ready__", True))

    await _drain(mgr)

    assert mgr.flushed == [[("sid-live", "first chunk"), ("sid-live", "second chunk")]]
    assert mgr._tts_quota_blocked is False


@pytest.mark.asyncio
async def test_rate_limit_respawn_delay_doubles_and_resets_on_ready(respawn_delays):
    mgr = _make_mgr()
    for _ in range(3):
        mgr.tts_response_queue.put(_error("API_RATE_LIMIT"))
        mgr.tts_response_queue.put(("__ready__", False))
    await _drain(mgr)
    assert await _wait_for(lambda: len(respawn_delays) == 3)

    mgr.tts_response_queue.put(("__ready__", True))
    mgr.tts_response_queue.put(_error("API_RATE_LIMIT"))
    mgr.tts_response_queue.put(("__ready__", False))
    mgr.tts_ready = False
    await _drain(mgr)
    assert await _wait_for(lambda: len(respawn_delays) == 4)

    base = TTS_RESPAWN_DELAY_SECONDS
    assert respawn_delays == [base, base * 2, base * 4, base]


@pytest.mark.asyncio
async def test_rate_limit_respawn_delay_is_capped(respawn_delays):
    mgr = _make_mgr()
    mgr._tts_rate_limit_backoff_level = 10
    mgr.tts_response_queue.put(_error("API_RATE_LIMIT"))
    mgr.tts_response_queue.put(("__ready__", False))

    await _drain(mgr)
    assert await _wait_for(lambda: len(respawn_delays) == 1)

    assert respawn_delays == [TTS_RATE_LIMIT_MAX_RESPAWN_DELAY_SECONDS]


@pytest.mark.asyncio
async def test_other_failures_keep_the_fixed_respawn_delay(respawn_delays):
    mgr = _make_mgr()
    mgr.tts_response_queue.put(_error("TTS_CONNECTION_FAILED"))
    mgr.tts_response_queue.put(("__ready__", False))
    mgr.tts_response_queue.put(_error("TTS_CONNECTION_FAILED"))
    mgr.tts_response_queue.put(("__ready__", False))

    await _drain(mgr)
    assert await _wait_for(lambda: len(respawn_delays) >= 1)

    assert set(respawn_delays) == {TTS_RESPAWN_DELAY_SECONDS}


@pytest.mark.asyncio
async def test_access_denied_is_never_retried(respawn_delays):
    mgr = _make_mgr()
    mgr.tts_response_queue.put(_error("API_ACCESS_DENIED"))
    mgr.tts_response_queue.put(("__ready__", False))

    await _drain(mgr)

    assert respawn_delays == []
    assert mgr._tts_respawn_task is None
    assert json.loads(mgr.send_status.await_args.args[0])["code"] == "API_ACCESS_DENIED"
