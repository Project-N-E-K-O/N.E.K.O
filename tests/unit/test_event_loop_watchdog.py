import asyncio
import logging
import os
import threading
import time

import pytest

from utils.event_loop_watchdog import (
    DEFAULT_THRESHOLD_S,
    HEARTBEAT_TASK_NAME,
    THREAD_NAME,
    THRESHOLD_ENV,
    EventLoopStallWatchdog,
    _short_path,
    resolve_stall_threshold,
    start_loop_stall_watchdog_from_env,
)


THRESHOLD_S = 0.2
BLOCK_S = 0.8


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def stall_logger():
    logger = logging.getLogger(f"test.loop_stall.{time.monotonic_ns()}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _ListHandler()
    logger.addHandler(handler)
    yield logger, handler
    logger.removeHandler(handler)


def _messages(handler, level=logging.WARNING):
    return [r.getMessage() for r in handler.records if r.levelno >= level]


def _stack_dumps(handler):
    return [m for m in _messages(handler) if "event loop blocked" in m]


def _block_loop_in_sync_code():
    secret_user_text = "USER_TEXT_SENTINEL_e3f1"
    time.sleep(BLOCK_S)
    return secret_user_text


def _block_loop_somewhere_else():
    time.sleep(BLOCK_S)


async def _run_with_watchdog(logger, body, **kwargs):
    watchdog = EventLoopStallWatchdog(
        asyncio.get_running_loop(), logger=logger, threshold_s=THRESHOLD_S, **kwargs
    )
    watchdog.start()
    try:
        await asyncio.sleep(0.15)
        await body()
        # Let the heartbeat resume and the checker notice the recovery.
        await asyncio.sleep(0.4)
    finally:
        watchdog.stop()
    return watchdog


def test_sync_block_logs_loop_thread_stack_and_recovery(stall_logger):
    logger, handler = stall_logger

    async def body():
        _block_loop_in_sync_code()

    asyncio.run(_run_with_watchdog(logger, body))

    dumps = _stack_dumps(handler)
    assert len(dumps) == 1
    dump = dumps[0]
    assert "_block_loop_in_sync_code" in dump
    assert "test_event_loop_watchdog.py:" in dump
    assert "USER_TEXT_SENTINEL" not in dump
    # Only the loop thread is sampled, not the watchdog thread itself.
    assert "utils/event_loop_watchdog.py:" not in dump
    recovered = [m for m in _messages(handler) if "recovered after" in m]
    assert len(recovered) == 1
    blocked_ms = int(recovered[0].split("~")[1].split(" ")[0])
    assert BLOCK_S * 1000 * 0.75 <= blocked_ms <= BLOCK_S * 1000 * 2
    assert all("USER_TEXT_SENTINEL" not in r.getMessage() for r in handler.records)


def test_healthy_loop_logs_nothing(stall_logger):
    logger, handler = stall_logger

    async def body():
        for _ in range(20):
            await asyncio.sleep(0.05)
            time.sleep(0.01)

    asyncio.run(_run_with_watchdog(logger, body))

    assert handler.records == []


def test_same_stall_site_is_rate_limited(stall_logger):
    logger, handler = stall_logger

    async def body():
        _block_loop_in_sync_code()
        await asyncio.sleep(0.3)
        _block_loop_in_sync_code()
        await asyncio.sleep(0.3)
        _block_loop_somewhere_else()

    asyncio.run(_run_with_watchdog(logger, body, report_interval_s=30.0))

    dumps = _stack_dumps(handler)
    assert len(dumps) == 2
    assert "_block_loop_in_sync_code" in dumps[0]
    assert "_block_loop_somewhere_else" in dumps[1]
    # The suppressed episode's recovery goes to DEBUG only.
    assert len([m for m in _messages(handler) if "recovered after" in m]) == 2
    assert len([m for m in _messages(handler, logging.DEBUG) if "report suppressed" in m]) == 1


def test_rate_limit_window_expiry_reports_suppressed_count(stall_logger):
    logger, handler = stall_logger

    async def body():
        for _ in range(3):
            _block_loop_in_sync_code()
            await asyncio.sleep(0.3)

    asyncio.run(_run_with_watchdog(logger, body, report_interval_s=1.5))

    dumps = _stack_dumps(handler)
    assert len(dumps) == 2
    assert "similar stall" not in dumps[0]
    assert "1 similar stall(s) suppressed" in dumps[1]


def test_stop_leaves_no_thread_or_heartbeat(stall_logger):
    logger, handler = stall_logger

    async def main():
        watchdog = EventLoopStallWatchdog(asyncio.get_running_loop(), logger=logger, threshold_s=THRESHOLD_S)
        watchdog.start()
        await asyncio.sleep(0.05)
        assert watchdog.running
        watchdog.stop()
        watchdog.stop()  # idempotent
        await asyncio.sleep(0)
        assert not watchdog.running
        return [t for t in asyncio.all_tasks() if t.get_name() == HEARTBEAT_TASK_NAME and not t.done()]

    assert asyncio.run(main()) == []
    assert not any(t.name == THREAD_NAME for t in threading.enumerate())
    assert handler.records == []


def test_closed_loop_without_stop_is_not_reported(stall_logger):
    logger, handler = stall_logger
    holder = {}

    async def main():
        holder["watchdog"] = EventLoopStallWatchdog(
            asyncio.get_running_loop(), logger=logger, threshold_s=THRESHOLD_S
        )
        holder["watchdog"].start()
        await asyncio.sleep(0.05)

    asyncio.run(main())
    time.sleep(THRESHOLD_S * 3)
    holder["watchdog"].stop()

    assert _stack_dumps(handler) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_THRESHOLD_S),
        ("", DEFAULT_THRESHOLD_S),
        ("2500", 2.5),
        ("10", 0.1),
        ("0", None),
        ("-5", None),
        ("off", None),
        (" OFF ", None),
    ],
)
def test_resolve_stall_threshold(raw, expected):
    assert resolve_stall_threshold(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "nan", "inf", "-inf", "1e400"])
def test_resolve_stall_threshold_rejects_garbage(raw):
    with pytest.raises(ValueError):
        resolve_stall_threshold(raw)


def test_from_env_disabled_starts_nothing(stall_logger):
    logger, handler = stall_logger

    async def main():
        return start_loop_stall_watchdog_from_env(
            asyncio.get_running_loop(), logger=logger, environ={THRESHOLD_ENV: "0"}
        )

    assert asyncio.run(main()) is None
    assert not any(t.name == THREAD_NAME for t in threading.enumerate())


def test_from_env_invalid_value_falls_back_to_default(stall_logger):
    logger, handler = stall_logger

    async def main():
        watchdog = start_loop_stall_watchdog_from_env(
            asyncio.get_running_loop(), logger=logger, environ={THRESHOLD_ENV: "soon"}
        )
        try:
            return watchdog.threshold_s
        finally:
            watchdog.stop()

    assert asyncio.run(main()) == DEFAULT_THRESHOLD_S
    assert any("ignoring invalid" in m for m in _messages(handler))


def _watchdog_threads():
    return [t for t in threading.enumerate() if t.name == THREAD_NAME and t.is_alive()]


def test_same_loop_shares_one_watchdog_until_last_owner_stops(stall_logger):
    logger, handler = stall_logger

    async def main():
        loop = asyncio.get_running_loop()
        first = start_loop_stall_watchdog_from_env(loop, logger=logger, label="main_server", environ={})
        second = start_loop_stall_watchdog_from_env(loop, logger=logger, label="agent_server", environ={})
        assert second is first
        assert first.label == "main_server+agent_server"
        assert len(_watchdog_threads()) == 1
        first.stop()
        assert first.running  # agent_server still holds it
        second.stop()
        await asyncio.sleep(0)
        assert not first.running
        assert _watchdog_threads() == []
        # A later start on the same loop gets a fresh watchdog.
        third = start_loop_stall_watchdog_from_env(loop, logger=logger, label="agent_server", environ={})
        try:
            assert third is not first and third.running
        finally:
            third.stop()

    asyncio.run(main())
    assert _watchdog_threads() == []
    assert any("shares the watchdog" in m for m in _messages(handler, logging.INFO))


def test_different_loops_each_get_their_own_watchdog(stall_logger):
    logger, _handler = stall_logger
    started = threading.Event()
    release = threading.Event()
    other = {}

    def other_loop_thread():
        async def main():
            other["watchdog"] = start_loop_stall_watchdog_from_env(
                asyncio.get_running_loop(), logger=logger, label="agent_server", environ={}
            )
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            other["watchdog"].stop()

        asyncio.run(main())

    async def main():
        mine = start_loop_stall_watchdog_from_env(
            asyncio.get_running_loop(), logger=logger, label="main_server", environ={}
        )
        thread = threading.Thread(target=other_loop_thread)
        thread.start()
        try:
            assert started.wait(5)
            assert other["watchdog"] is not mine
            assert len(_watchdog_threads()) == 2
        finally:
            release.set()
            thread.join(5)
            mine.stop()

    asyncio.run(main())
    assert _watchdog_threads() == []


def test_paths_outside_known_roots_do_not_leak_user_names():
    home = os.path.expanduser("~")
    _short_path.cache_clear()
    try:
        under_home = _short_path(os.path.join(home, "scripts", "helper.py"))
        elsewhere = _short_path(os.path.join(os.sep, "srv", "alice", "work", "helper.py"))
        in_project = _short_path(__file__)
    finally:
        _short_path.cache_clear()
    assert under_home == "~/scripts/helper.py"
    assert elsewhere == ".../helper.py"
    assert os.path.basename(home) not in under_home
    assert "alice" not in elsewhere
    assert in_project == "tests/unit/test_event_loop_watchdog.py"
