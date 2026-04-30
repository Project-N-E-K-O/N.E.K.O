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
    await service._on_task_finished({"status": "ok"})
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
