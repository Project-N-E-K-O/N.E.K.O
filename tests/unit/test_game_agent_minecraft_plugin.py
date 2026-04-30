# -*- coding: utf-8 -*-
"""Unit tests for the game_agent_minecraft plugin's service layer.

Covers the in-process behaviour without spinning up a real WebSocket
or the user_plugin_server: error paths, the ``asyncio.Event`` bridge
between the WS callback and the ``@llm_tool`` handler, the ``busy`` /
``overwrite`` semantics, and timeout handling. The plugin facade
(``__init__.py``) is exercised separately via the SDK's auto-register
pipeline (already covered by ``test_plugin_llm_tool_sdk.py``).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClient:
    """Stand-in for ``GameAgentClient`` — captures send_task calls and
    lets the test drive on_task_finished from the outside."""

    def __init__(self, *, send_task_returns: bool = True) -> None:
        self.is_connected = True
        self.sent: list[str] = []
        self._send_returns = send_task_returns
        # The service plugs callbacks in via the constructor in the
        # real client; the test patches ``GameAgentService._client``
        # directly with this fake, so callbacks are invoked manually.
        self.on_task_finished = None

    async def send_task(self, task: str) -> bool:
        self.sent.append(task)
        return self._send_returns

    async def stop(self) -> None:
        self.is_connected = False


def _make_service(*, push_calls: list | None = None):
    """Create a service with no real client. Tests that need to drive
    ``execute_minecraft_task`` plug a ``_FakeClient`` in afterwards via
    monkeypatching, since the public ``start()`` would launch real WS
    code."""
    from plugin.plugins.game_agent_minecraft.service import GameAgentService

    captured = push_calls if push_calls is not None else []

    def fake_push(**kwargs):
        captured.append(kwargs)

    service = GameAgentService(logger=None, push_message_fn=fake_push)
    return service, captured


# ---------------------------------------------------------------------------
# configure() — defensive parsing
# ---------------------------------------------------------------------------


def test_configure_uses_defaults_when_keys_missing():
    service, _ = _make_service()
    service.configure({})
    status = service.get_status()
    # ws_url default mirrored from plugin.toml
    assert status["ws_url"].startswith("ws://localhost")


def test_configure_clamps_invalid_numeric():
    service, _ = _make_service()
    service.configure({
        "ws_url": "ws://example:1234",
        "task_timeout_seconds": "not a number",
        "system_prompt_interval_seconds": -5.0,
        "screenshot_cache_size": "abc",
    })
    # Bad strings fall back to defaults; negative interval clamps to ≥1.
    status = service.get_status()
    assert status["ws_url"] == "ws://example:1234"
    # No way to inspect internal _task_timeout from the public API, but
    # the fact that it didn't raise is the contract.


# ---------------------------------------------------------------------------
# execute_minecraft_task — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_rejects_empty_task():
    service, _ = _make_service()
    out = await service.execute_minecraft_task(task="")
    assert out["is_error"] is True
    assert out["error"] == "INVALID_TASK"


@pytest.mark.asyncio
async def test_execute_returns_not_started_when_no_client():
    service, _ = _make_service()
    out = await service.execute_minecraft_task(task="mine 10 logs")
    assert out["is_error"] is True
    assert out["error"] == "NOT_STARTED"


@pytest.mark.asyncio
async def test_execute_returns_disconnected_when_send_fails():
    service, _ = _make_service()
    service._client = _FakeClient(send_task_returns=False)
    out = await service.execute_minecraft_task(task="mine 10 logs")
    assert out["is_error"] is True
    assert out["error"] == "AGENT_DISCONNECTED"
    # Critical: ``_pending`` was rolled back so subsequent calls don't
    # see "busy" against an event nothing will ever set.
    assert service._pending is None
    # And ``_task_finished`` was reset back to True — without that, the
    # autonomous loop's "skip when busy" gate and the system prompt's
    # "正在进行的操作" branch would behave as if a phantom task were
    # still running.
    assert service._task_finished is True


# ---------------------------------------------------------------------------
# execute_minecraft_task — happy path via task_finished callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_completes_when_task_finished_fires():
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    async def driver():
        # Wait until the handler has set ``_pending``, then fire the
        # task_finished callback so the handler's await wakes up.
        for _ in range(50):
            if service._pending is not None:
                break
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("handler never set _pending")
        await service._on_task_finished({"status": "ok", "text": "done"})

    runner = asyncio.create_task(driver())
    out = await service.execute_minecraft_task(task="mine 10 logs")
    await runner

    assert out == {"status": "ok", "query": "mine 10 logs"}
    assert service._pending is None


# ---------------------------------------------------------------------------
# execute_minecraft_task — timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_times_out_when_no_finish_arrives():
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 0.1})
    service._client = _FakeClient()

    out = await service.execute_minecraft_task(task="dig forever")
    assert out["status"] == "timeout"
    assert out["query"] == "dig forever"
    assert "Not finished" in out["reason"]
    # Slot freed so a subsequent call isn't permanently busy.
    assert service._pending is None


# ---------------------------------------------------------------------------
# execute_minecraft_task — busy / overwrite semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_call_returns_busy_when_overwrite_false():
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # Start a long-running task in the background — never resolved.
    long_runner = asyncio.create_task(
        service.execute_minecraft_task(task="long task")
    )
    # Wait for _pending to be set.
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)

    # Concurrent call without overwrite gets busy.
    out = await service.execute_minecraft_task(task="other task", overwrite=False)
    assert out["result"] == "busy"
    assert out["currently_executing"] == "long task"

    # Cancel the long runner so the test doesn't hang.
    long_runner.cancel()
    try:
        await long_runner
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_overwrite_interrupts_old_task_with_status():
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # Start old task.
    old_runner = asyncio.create_task(
        service.execute_minecraft_task(task="old task")
    )
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)

    # Issue overwrite — should kick old task into "interrupted" return.
    new_runner = asyncio.create_task(
        service.execute_minecraft_task(task="new task", overwrite=True)
    )

    # Old should resolve quickly with interrupted status.
    old_out = await asyncio.wait_for(old_runner, timeout=2.0)
    assert old_out["status"] == "interrupted"
    assert old_out["query"] == "old task"
    assert "Overwritten" in old_out["reason"]

    # New task is still pending until we fire task_finished.
    for _ in range(50):
        if service._pending is not None and service._pending.task_text == "new task":
            break
        await asyncio.sleep(0.01)

    # The agent will (eventually) emit a delayed task_finished for the
    # *old* task before the new one's frame arrives. Per the FIFO drop
    # rule, that first frame is swallowed and the new task's frame
    # resolves the runner.
    await service._on_task_finished({"status": "ok", "text": "old finished late"})
    assert service._pending is not None  # still waiting
    await service._on_task_finished({"status": "ok", "text": "new finished"})
    new_out = await asyncio.wait_for(new_runner, timeout=2.0)
    assert new_out == {"status": "ok", "query": "new task"}


# ---------------------------------------------------------------------------
# Screenshot / log callbacks — pushed via push_message v2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_screenshot_callback_pushes_image_part():
    import base64
    service, push_calls = _make_service()
    service.configure({"stream_screenshots_to_llm": True})

    # Fake 1x1 PNG (minimal valid bytes).
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    payload = base64.b64encode(png_bytes).decode("ascii")
    await service._on_screenshot(payload, "png")

    assert len(push_calls) == 1
    pc = push_calls[0]
    assert pc["visibility"] == []
    assert pc["ai_behavior"] == "read"
    assert len(pc["parts"]) == 1
    part = pc["parts"][0]
    assert part["type"] == "image"
    assert part["mime"] == "image/png"
    assert part["data"] == png_bytes


@pytest.mark.asyncio
async def test_screenshot_streaming_disabled_caches_only():
    service, push_calls = _make_service()
    service.configure({"stream_screenshots_to_llm": False})

    import base64
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
    await service._on_screenshot(payload, "png")
    assert push_calls == []
    # But the bytes should be cached for the next system-prompt burst.
    assert service.get_status()["screenshot_cache_size"] == 1


@pytest.mark.asyncio
async def test_system_prompt_replay_preserves_per_frame_mime():
    """Cached screenshots may have heterogeneous mimes (PNG-converted
    PNG vs. JPEG-passed-through if Pillow conversion failed). The
    autonomous-loop replay must use each frame's actual mime, not a
    hardcoded image/png — otherwise downstream ``stream_image`` would
    receive mis-tagged JPEG bytes."""
    service, push_calls = _make_service()
    service.configure({"stream_screenshots_to_llm": False})  # cache only

    # Manually plant cache entries with different mimes (skipping
    # _on_screenshot decode path so we can choose mimes deterministically).
    service._screenshot_cache.append((b"<png-bytes>", "image/png"))
    service._screenshot_cache.append((b"<jpeg-bytes>", "image/jpeg"))
    # Need at least one cached log line so the loop's "nothing to say"
    # gate doesn't short-circuit.
    service._log_cache.append("test event")

    await service._fire_system_prompt()
    assert len(push_calls) == 1
    parts = push_calls[0]["parts"]
    image_parts = [p for p in parts if p["type"] == "image"]
    assert len(image_parts) == 2
    mimes = [p["mime"] for p in image_parts]
    assert mimes == ["image/png", "image/jpeg"]
    # And the bytes survived round-trip too.
    datas = [p["data"] for p in image_parts]
    assert datas == [b"<png-bytes>", b"<jpeg-bytes>"]


@pytest.mark.asyncio
async def test_log_cache_is_bounded():
    """Without a cap, an idle ``skip_system_prompt_if_busy=True`` plus a
    chatty agent would balloon the log cache without bound. The cap
    drops oldest lines when full so memory stays flat."""
    service, _ = _make_service()
    service.configure({})

    # Push more lines than the cap. The cap is an internal constant
    # (200 at time of writing); we use a generous multiple so the test
    # doesn't go stale if the constant grows modestly.
    cap_estimate = 200
    overflow_count = cap_estimate * 3
    for i in range(overflow_count):
        await service._on_log(f"line {i}")

    cached = list(service._log_cache)
    # Cap held; we only kept "the most recent N" lines, not all of them.
    assert len(cached) < overflow_count
    assert len(cached) <= cap_estimate
    # And the survivors are the most recent ones, not the oldest.
    assert cached[-1] == f"line {overflow_count - 1}"


@pytest.mark.asyncio
async def test_log_callback_tracks_task_state_from_strings():
    service, _ = _make_service()
    service.configure({})

    await service._on_log("action selection: chop wood")
    assert service._task_finished is False
    await service._on_log("task run ended")
    assert service._task_finished is True
    await service._on_log("Connection lost and re-established.")
    assert service._task_finished is True


# ---------------------------------------------------------------------------
# stop() resolves pending callers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_unblocks_pending_handler():
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 30.0})
    service._client = _FakeClient()

    runner = asyncio.create_task(
        service.execute_minecraft_task(task="long task")
    )
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)

    await service.stop()
    out = await asyncio.wait_for(runner, timeout=2.0)
    assert out["status"] == "interrupted"
    assert "shutting down" in out["reason"].lower()


# ---------------------------------------------------------------------------
# Stale task_finished filtering — protocol has no task IDs, so a delayed
# completion frame for an abandoned task must not be misattributed to the
# new pending task.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_task_finished_after_timeout_is_dropped():
    """After a task times out, a delayed task_finished frame for it
    should be swallowed instead of resolving the *next* call."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 0.1})
    service._client = _FakeClient()

    # Task A times out — leaves a "stale frame" debt of 1.
    out_a = await service.execute_minecraft_task(task="task A")
    assert out_a["status"] == "timeout"
    assert service._stale_task_finishes_to_drop == 1

    # Late task_finished for A arrives — should be dropped, not
    # attached to anything.
    await service._on_task_finished({"status": "ok", "text": "A done late"})
    assert service._stale_task_finishes_to_drop == 0
    # No pending task got falsely resolved.
    assert service._pending is None


@pytest.mark.asyncio
async def test_stale_task_finished_after_overwrite_is_dropped():
    """After overwrite, a delayed task_finished for the *old* task must
    not be matched to the *new* pending task (which has its own id-less
    frame coming later)."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # Start old task.
    old_runner = asyncio.create_task(
        service.execute_minecraft_task(task="old task")
    )
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)

    # Overwrite — old runner resolves with "interrupted", drop counter += 1.
    new_runner = asyncio.create_task(
        service.execute_minecraft_task(task="new task", overwrite=True)
    )
    old_out = await asyncio.wait_for(old_runner, timeout=2.0)
    assert old_out["status"] == "interrupted"
    assert service._stale_task_finishes_to_drop == 1

    # Wait for the new task's pending slot.
    for _ in range(50):
        if service._pending is not None and service._pending.task_text == "new task":
            break
        await asyncio.sleep(0.01)

    # Late task_finished for OLD task arrives first — must be dropped,
    # NOT attached to the new task.
    await service._on_task_finished({"status": "ok", "text": "old done late"})
    assert service._stale_task_finishes_to_drop == 0
    # New task is still pending (drop didn't resolve it).
    assert service._pending is not None
    assert service._pending.task_text == "new task"

    # Now the real frame for the new task arrives — should resolve normally.
    await service._on_task_finished({"status": "ok", "text": "new done"})
    new_out = await asyncio.wait_for(new_runner, timeout=2.0)
    assert new_out == {"status": "ok", "query": "new task"}


# ---------------------------------------------------------------------------
# Cancellation cleanup — when the outer SDK timeout cancels the handler,
# self._pending must not be left dangling.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_clears_pending_slot():
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 30.0})
    service._client = _FakeClient()

    runner = asyncio.create_task(
        service.execute_minecraft_task(task="long task")
    )
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)

    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner
    assert service._pending is None
    # And the drop counter was bumped so a delayed frame doesn't bind
    # to whatever task comes next.
    assert service._stale_task_finishes_to_drop == 1


# ---------------------------------------------------------------------------
# reload_config_live — transport-affecting keys trigger restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_config_live_no_restart_when_not_running():
    """Pure config update before start() — just mutates state, never
    restarts (because there's nothing to restart)."""
    service, _ = _make_service()
    service.configure({"ws_url": "ws://localhost:48909"})
    restarted = await service.reload_config_live({"ws_url": "ws://localhost:48910"})
    assert restarted is False
    assert service.get_status()["ws_url"] == "ws://localhost:48910"


@pytest.mark.asyncio
async def test_reload_config_live_restarts_on_ws_url_change():
    """When a live client exists and ws_url changes, the service does
    a stop+start cycle so the new URL takes effect."""
    service, _ = _make_service()
    service.configure({"ws_url": "ws://localhost:48909"})

    # Plug a fake "running" state without launching real WS code:
    # the reload path treats ``self._client is not None`` as "running",
    # and ``stop()`` / ``start()`` deal with whatever's there.
    fake_client = _FakeClient()
    service._client = fake_client

    # Patch start() to track invocations without spinning up a real
    # WebSocket connection.
    start_calls: list[str] = []

    async def fake_start():
        start_calls.append(service._ws_url)
        service._client = _FakeClient()

    service.start = fake_start  # type: ignore[method-assign]

    restarted = await service.reload_config_live({"ws_url": "ws://example:9999"})
    assert restarted is True
    assert start_calls == ["ws://example:9999"]
    assert service.get_status()["ws_url"] == "ws://example:9999"


@pytest.mark.asyncio
async def test_reload_config_live_no_restart_for_pure_data_keys():
    """Changing only timeouts / intervals doesn't tear down the
    transport — those are read on every tick."""
    service, _ = _make_service()
    service.configure({"ws_url": "ws://localhost:48909", "task_timeout_seconds": 25.0})
    service._client = _FakeClient()

    # Track that start() was NOT called.
    start_calls: list[str] = []

    async def fake_start():
        start_calls.append(service._ws_url)

    service.start = fake_start  # type: ignore[method-assign]

    restarted = await service.reload_config_live({
        "ws_url": "ws://localhost:48909",  # unchanged
        "task_timeout_seconds": 60.0,
    })
    assert restarted is False
    assert start_calls == []
