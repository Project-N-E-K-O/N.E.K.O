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
# Helpers
# ---------------------------------------------------------------------------


async def _wait_for_pending(service, *, predicate=None, timeout: float = 0.5):
    """Spin until the service has a pending task that matches
    ``predicate`` (or any pending task if predicate is None). Fails
    fast with a clear assertion if the timeout fires — without this
    fail-fast branch, a flaky test would surface as an unrelated
    AttributeError on ``service._pending.task_text`` later, hiding
    the real cause.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        pending = service._pending
        if pending is not None and (predicate is None or predicate(pending)):
            return pending
        await asyncio.sleep(0.01)
    # ``pytest.fail`` raises ``pytest.failed.Exception`` and never
    # returns, but its return type isn't annotated as ``NoReturn`` in
    # all pytest versions, so the static analyzer warns about a
    # potential implicit ``None`` fall-through. Make the
    # never-returns property explicit with ``raise AssertionError``.
    pytest.fail(
        f"_pending never satisfied predicate within {timeout}s; "
        f"current _pending={service._pending!r}"
    )
    raise AssertionError("unreachable")  # pragma: no cover


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
    status = service.get_status()
    assert status["ws_url"] == "ws://example:1234"
    # Bad strings fall back to defaults; negative values clamp to the
    # documented floor. Inspect the private attributes directly — we
    # already test against private state throughout this file, and
    # without these assertions a regression in the fallback or clamp
    # logic would silently let this test pass.
    assert service._task_timeout == 25.0  # default
    assert service._system_prompt_interval == 1.0  # clamped from -5
    assert service._screenshot_cache_size == 3  # default
    assert service._reconnect_interval == 5.0  # default


def test_configure_clamps_task_timeout_below_sdk_ceiling():
    """``@llm_tool(timeout=300.0)`` is the SDK wrapper ceiling. The
    service must clamp configured ``task_timeout_seconds`` *below*
    that so its structured ``{status: "timeout"}`` response can fire
    before the wrapper cancels the handler."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 600.0})
    # Ceiling - small buffer so service-internal fires first.
    assert service._task_timeout <= 300.0
    assert service._task_timeout > 0.0
    # And a normal value passes through.
    service.configure({"task_timeout_seconds": 90.0})
    assert service._task_timeout == 90.0


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
async def test_dispatch_race_honors_concurrent_verdict_over_disconnected():
    """If overwrite/stop/etc. wrote a verdict + set the event during
    the ``await send_task(...)`` suspension, the handler must surface
    that verdict instead of returning AGENT_DISCONNECTED — the rest
    of the system already recorded the task as 'interrupted', so a
    contradicting AGENT_DISCONNECTED tooltip would be wrong."""

    class _SlowSendClient:
        is_connected = True

        def __init__(self):
            self.released = asyncio.Event()
            self.return_value = False  # simulate "send failed"

        async def send_task(self, task):
            # Suspend long enough for the test to inject a verdict.
            await self.released.wait()
            return self.return_value

        async def stop(self):
            pass

    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    slow = _SlowSendClient()
    service._client = slow

    runner = asyncio.create_task(service.execute_minecraft_task(task="A"))

    # Wait for the handler to claim _pending, then race in: write
    # an interrupted verdict + set the event, while send_task is
    # still suspended.
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("service._pending was never set within poll budget")
    my_pending = service._pending
    assert my_pending is not None
    my_pending.result = {
        "status": "interrupted",
        "query": "A",
        "reason": "Overwritten by a new task.",
    }
    my_pending.event.set()

    # Now release send_task to return False ("disconnected"). Without
    # the fix, the handler would return AGENT_DISCONNECTED and lose
    # the interrupted verdict.
    slow.released.set()
    out = await asyncio.wait_for(runner, timeout=2.0)
    assert out == {
        "status": "interrupted",
        "query": "A",
        "reason": "Overwritten by a new task.",
    }


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
async def test_finished_result_not_overwritten_by_racing_stop():
    """``_on_task_finished`` must clear ``self._pending`` BEFORE
    setting the event, so a racing ``stop()`` that acquires the lock
    after this block exits can't see the still-completed PendingTask
    and overwrite its result with "interrupted". Without that
    ordering, the waiter (which shares the same PendingTask object)
    would read a corrupted result."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    runner = asyncio.create_task(service.execute_minecraft_task(task="A"))
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("_pending was never set")

    # Fire task_finished. After this returns, self._pending should be
    # cleared (so a racing stop() sees no pending task) but the
    # waiter's local PendingTask still has the {"status": "ok"}
    # result baked in.
    await service._on_task_finished({"status": "ok"})
    # ``self._pending`` was cleared inside the same lock block as
    # ``event.set()``, so a stop() coming in here can't mutate the
    # finished PendingTask's result.
    assert service._pending is None

    await service.stop()

    out = await asyncio.wait_for(runner, timeout=2.0)
    assert out == {"status": "ok", "query": "A"}, (
        "stop() must not have overwritten the task_finished verdict"
    )


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
    else:
        pytest.fail("service._pending was never set within poll budget")

    # Concurrent call without overwrite gets busy.
    out = await service.execute_minecraft_task(task="other task", overwrite=False)
    assert out["result"] == "busy"
    assert out["currently_executing"] == "long task"

    # Cancel the long runner so the test doesn't hang.
    long_runner.cancel()
    try:
        await long_runner
    except (asyncio.CancelledError, Exception):
        # We just cancelled it ourselves — both CancelledError and any
        # exception raised on the way out are expected and irrelevant
        # to the assertions above. Swallow so the test completes
        # cleanly.
        pass


@pytest.mark.asyncio
async def test_overwrite_only_accepts_true_canonical_bool():
    """The LLM may emit a non-canonical truthy value for ``overwrite``
    (string ``"true"``, integer ``1``, etc.). The strict ``is True``
    check ensures the destructive interrupt path only fires on the
    canonical boolean — anything else falls through to the safe
    ``"busy"`` response."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # Start a task to occupy the slot.
    long_runner = asyncio.create_task(
        service.execute_minecraft_task(task="incumbent")
    )
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("incumbent never claimed the slot")

    # ``overwrite="true"`` (string) — must NOT interrupt.
    out = await service.execute_minecraft_task(task="impostor", overwrite="true")
    assert out["result"] == "busy"
    assert out["currently_executing"] == "incumbent"

    # ``overwrite=1`` (truthy int) — same.
    out = await service.execute_minecraft_task(task="impostor", overwrite=1)
    assert out["result"] == "busy"

    # Sanity: the slot still holds the original task, not impostor.
    assert service._pending is not None
    assert service._pending.task_text == "incumbent"

    long_runner.cancel()
    try:
        await long_runner
    except (asyncio.CancelledError, Exception):
        # Cleanup-only swallow — we cancelled it ourselves.
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
    else:
        pytest.fail("service._pending was never set within poll budget")

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
    else:
        pytest.fail("_pending was never the new task within poll budget")

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

    # Read the actual cap off the deque rather than hardcoding it —
    # otherwise bumping the constant in the implementation would
    # spuriously fail this test even when the bounded-growth invariant
    # is intact.
    cap = service._log_cache.maxlen
    assert cap is not None and cap > 0
    overflow_count = cap * 3

    for i in range(overflow_count):
        await service._on_log(f"line {i}")

    cached = list(service._log_cache)
    assert len(cached) == cap, "cache should be at exactly the maxlen"
    # Survivors are the most recent ones, not the oldest.
    assert cached[-1] == f"line {overflow_count - 1}"
    assert cached[0] == f"line {overflow_count - cap}"


@pytest.mark.asyncio
async def test_screenshot_data_uri_jpeg_with_empty_encoding_picks_jpeg_mime(monkeypatch):
    """Some agents send ``data:image/jpeg;base64,...`` payloads with
    an empty ``encoding`` field. Without parsing the URI scheme, the
    handler defaults to PNG and tags JPEG bytes wrongly.

    We use ``monkeypatch.setitem`` to force the JPEG-passthrough
    branch (Pillow stubbed to fail) and rely on monkeypatch's
    auto-rollback so the fake module state doesn't bleed into other
    tests in the suite.
    """
    import base64
    import sys
    import types

    service, push_calls = _make_service()
    service.configure({})
    # Force the JPEG-passthrough branch by stubbing Pillow's
    # ``Image.open`` to raise. Without this, a real PIL would
    # re-encode JPEG → PNG and mask the mime-handling we're testing.
    fake_pil = types.ModuleType("PIL")
    fake_image = types.ModuleType("PIL.Image")
    def _open_raises(*_a, **_k):
        raise RuntimeError("Pillow stubbed for this test")
    fake_image.open = _open_raises  # type: ignore[attr-defined]
    fake_pil.Image = fake_image  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_image)

    jpeg_bytes = b"\xff\xd8\xff\xe0fakejpegmarker"
    payload = "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode()

    # encoding="" — without the data-URI mime parsing, we'd default
    # to PNG and silently mis-tag the JPEG bytes.
    await service._on_screenshot(payload, encoding="")

    assert len(push_calls) == 1
    parts = push_calls[0]["parts"]
    assert parts[0]["mime"] == "image/jpeg"
    assert parts[0]["data"] == jpeg_bytes


@pytest.mark.asyncio
async def test_log_heuristic_does_not_flip_when_pending_task_active():
    """An old task's late "task run ended" log must not flip
    ``_task_finished`` to True while a new task is in flight —
    otherwise the autonomous loop's busy gate breaks. We defer to
    the explicit ``task_finished`` frame's stale-frame filtering
    when a task is pending."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # Start a task — _pending populated, _task_finished=False.
    runner = asyncio.create_task(service.execute_minecraft_task(task="A"))
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("_pending was never set within poll budget")
    assert service._task_finished is False

    # An old task's late "task run ended" log arrives — must NOT
    # flip _task_finished while A is still pending.
    await service._on_log("task run ended (for some old task)")
    assert service._task_finished is False
    assert service._pending is not None  # still in flight

    # Same for connection-lost log.
    await service._on_log("Connection lost and re-established.")
    assert service._task_finished is False

    runner.cancel()
    try:
        await runner
    except (asyncio.CancelledError, Exception):
        # Cleanup-only swallow — we cancelled it ourselves so both
        # CancelledError and any incidental exception on the way out
        # are expected and irrelevant to the assertions above.
        pass


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
    else:
        pytest.fail("service._pending was never set within poll budget")

    await service.stop()
    out = await asyncio.wait_for(runner, timeout=2.0)
    assert out["status"] == "interrupted"
    assert "shutting down" in out["reason"].lower()


@pytest.mark.asyncio
async def test_stop_preserves_stale_frame_debt():
    """When stop() interrupts a pending task, it must bump the drop
    counter (not zero it). reload_config_live = stop+start; if the
    same agent buffers and redelivers the abandoned task's
    task_finished after reconnect, the counter ensures it doesn't
    bind to whatever the LLM picks next. Likewise, prior debt from
    earlier timeouts must survive stop() instead of being zeroed."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 30.0})
    service._client = _FakeClient()

    # Pre-populate a debt of 2 from earlier abandons.
    service._stale_task_finishes_to_drop = 2

    runner = asyncio.create_task(
        service.execute_minecraft_task(task="will be stopped")
    )
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("service._pending was never set within poll budget")

    await service.stop()
    out = await asyncio.wait_for(runner, timeout=2.0)
    assert out["status"] == "interrupted"
    # Counter should be 3: prior debt (2) preserved + this stop's
    # abandoned task (+1).
    assert service._stale_task_finishes_to_drop == 3


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
async def test_stale_task_finished_with_no_pending_marks_idle():
    """When a stale frame drops AND there's no current pending task,
    the agent has genuinely returned to idle. Set ``_task_finished``
    to True so the autonomous loop knows it can resume nudging —
    otherwise a flag stuck at False (from before stop()/abandon)
    would silently keep the busy gate engaged forever."""
    service, _ = _make_service()
    service.configure({})
    # Pre-conditions: a task is "in flight" from the loop's perspective
    # (flag stuck at False) and we have one pending stale-frame drop
    # but no actual pending task — the typical post-stop+restart shape.
    service._stale_task_finishes_to_drop = 1
    service._task_finished = False
    assert service._pending is None

    await service._on_task_finished({"status": "ok", "text": "late frame for abandoned task"})
    assert service._stale_task_finishes_to_drop == 0
    # Critical: the flag flipped to True even though the frame was
    # dropped — agent is idle, busy gate must reflect that.
    assert service._task_finished is True


@pytest.mark.asyncio
async def test_stale_task_finished_with_pending_keeps_flag_false():
    """Counter-test to the above: when dropping a stale frame WHILE a
    real task is pending, ``_task_finished`` must stay False (existing
    invariant — pinned by ``test_stale_task_finished_does_not_flip_…``).
    Combined with the prior test, this defines the rule: flip on
    drop only when ``_pending is None``."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # Pre-bump drop counter as if a prior task had been abandoned.
    service._stale_task_finishes_to_drop = 1

    # Now start a fresh task — _pending=B, _task_finished=False.
    runner = asyncio.create_task(service.execute_minecraft_task(task="B"))
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("B never claimed the slot")
    assert service._task_finished is False

    # Stale drop while B is in flight — must NOT flip the flag.
    await service._on_task_finished({"status": "ok", "text": "old"})
    assert service._task_finished is False
    assert service._pending is not None
    assert service._pending.task_text == "B"

    runner.cancel()
    try:
        await runner
    except (asyncio.CancelledError, Exception):
        # Cleanup-only swallow.
        pass


@pytest.mark.asyncio
async def test_stale_task_finished_does_not_pollute_log_cache():
    """Stale frames must not contribute their text to ``_log_cache``
    either — otherwise the next system prompt would surface "old task
    done" alongside a still-running new task, contradicting itself."""
    service, _ = _make_service()
    service.configure({})
    # Pre-bump drop counter as if a prior task had been abandoned.
    service._stale_task_finishes_to_drop = 1

    await service._on_task_finished({
        "status": "ok",
        "text": "old task completion message — should NOT appear in cache",
    })
    # Frame was dropped → text must not be cached.
    assert list(service._log_cache) == []
    assert service._stale_task_finishes_to_drop == 0


@pytest.mark.asyncio
async def test_stale_task_finished_does_not_flip_task_finished_flag():
    """Dropping a stale frame must not set ``_task_finished = True``,
    which would leak the abandoned task's completion state into the
    *current* in-flight task and break the autonomous loop's busy
    gate."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # Start a task → _task_finished flips to False → time it out so we
    # accumulate a drop.
    service.configure({"task_timeout_seconds": 0.1})
    out = await service.execute_minecraft_task(task="A")
    assert out["status"] == "timeout"
    assert service._stale_task_finishes_to_drop == 1

    # Start a fresh task — _task_finished is False again (in flight).
    service.configure({"task_timeout_seconds": 5.0})
    runner = asyncio.create_task(service.execute_minecraft_task(task="B"))
    for _ in range(50):
        if service._pending is not None and service._pending.task_text == "B":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("_pending was never task B within poll budget")
    assert service._task_finished is False

    # Stale frame for A arrives → must be dropped, must NOT flip
    # _task_finished to True (B is still running).
    await service._on_task_finished({"status": "ok", "text": "A done late"})
    assert service._stale_task_finishes_to_drop == 0
    assert service._task_finished is False
    assert service._pending is not None
    assert service._pending.task_text == "B"

    # Real B completion now flips the flag.
    await service._on_task_finished({"status": "ok", "text": "B done"})
    assert service._task_finished is True
    out_b = await asyncio.wait_for(runner, timeout=2.0)
    assert out_b == {"status": "ok", "query": "B"}


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
    else:
        pytest.fail("service._pending was never set within poll budget")

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
    else:
        pytest.fail("_pending was never the new task within poll budget")

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
async def test_cancellation_during_send_task_clears_pending_slot():
    """The cancellation handler around ``event.wait()`` only catches
    cancels that land *after* dispatch. Cancellation during the
    ``send_task`` await itself (e.g. plugin shutdown sweeps tasks
    while the WS roundtrip is in flight) was previously a leak —
    ``_pending`` would dangle and every subsequent call would return
    'busy' against an event nothing would set."""

    class _SlowSendClient:
        is_connected = True

        def __init__(self):
            self.released = asyncio.Event()

        async def send_task(self, task):
            # Block until cancelled — the test cancels the runner
            # while we're suspended here.
            await self.released.wait()
            return True

        async def stop(self):
            pass

    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 30.0})
    service._client = _SlowSendClient()

    runner = asyncio.create_task(service.execute_minecraft_task(task="A"))
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("_pending was never set within poll budget")

    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner

    assert service._pending is None
    assert service._task_finished is True
    # NOT bumped — ``send_task`` was cancelled before completing, so
    # the agent never received the task and won't emit a stale frame
    # for it. Bumping would silently swallow the next legitimate
    # ``task_finished`` from a future task.
    assert service._stale_task_finishes_to_drop == 0


@pytest.mark.asyncio
async def test_overwrite_during_send_task_bumps_via_late_dispatch_flag():
    """Re-architected: drive the slow-send fake step by step to verify
    the late-dispatch bump fires when overwrite sets the flag."""

    class _SlowSendClient:
        is_connected = True

        def __init__(self):
            self.gates: list[asyncio.Event] = []
            self.sent_tasks: list[str] = []

        async def send_task(self, task):
            gate = asyncio.Event()
            self.gates.append(gate)
            await gate.wait()
            self.sent_tasks.append(task)
            return True

        async def stop(self):
            pass

    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    slow = _SlowSendClient()
    service._client = slow

    # 1. Start old task. send_task suspends on gates[0].
    old_runner = asyncio.create_task(service.execute_minecraft_task(task="old"))
    for _ in range(50):
        if len(slow.gates) >= 1 and service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("old's send_task never reached the gate")
    old_pending = service._pending
    assert old_pending.dispatched is False
    assert service._stale_task_finishes_to_drop == 0

    # 2. Overwrite arrives. send_task for new will suspend on gates[1].
    new_runner = asyncio.create_task(
        service.execute_minecraft_task(task="new", overwrite=True)
    )
    for _ in range(50):
        if len(slow.gates) >= 2:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("new's send_task never reached the gate")

    # 3. After overwrite ran inside the lock:
    #    - old's event was set + result=interrupted
    #    - bump_on_late_dispatch was armed (because dispatched=False)
    #    - drop counter NOT yet bumped
    assert old_pending.bump_on_late_dispatch is True
    assert service._stale_task_finishes_to_drop == 0

    # 4. Release old's send_task. Handler runs: dispatched=True →
    #    sees bump_on_late_dispatch → bumps counter → is_set() → returns.
    slow.gates[0].set()
    old_out = await asyncio.wait_for(old_runner, timeout=2.0)
    assert old_out["status"] == "interrupted"
    assert service._stale_task_finishes_to_drop == 1, (
        "bump must have happened on late dispatch"
    )

    # 5. Release new's send_task and let new run normally to confirm
    #    the slot is healthy.
    slow.gates[1].set()
    # Wait for new to enter event.wait, then drive task_finished.
    for _ in range(50):
        if service._pending is not None and service._pending.task_text == "new":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("new never settled into pending state")

    # 6. The agent's frame for old (the abandoned one) arrives first
    #    — should be dropped via the counter we just bumped, NOT
    #    misattributed to new.
    await service._on_task_finished({"status": "ok", "text": "old's late frame"})
    assert service._stale_task_finishes_to_drop == 0
    assert service._pending is not None  # new still pending

    # 7. New's real frame arrives → resolves new.
    await service._on_task_finished({"status": "ok", "text": "new done"})
    new_out = await asyncio.wait_for(new_runner, timeout=2.0)
    assert new_out == {"status": "ok", "query": "new"}


@pytest.mark.asyncio
async def test_overwrite_does_not_bump_drop_counter_for_undispatched_old():
    """If the OLD task hadn't reached past ``send_task`` yet when
    overwrite arrives, the agent never received it and won't emit a
    stale frame. Bumping the drop counter in that case would silently
    swallow the NEW task's legitimate completion frame.

    We can't observe this directly via the public path (overwrite
    inside the lock makes the race unreachable in production), but
    the underlying invariant is: ``_stale_task_finishes_to_drop``
    increments must gate on ``PendingTask.dispatched``. Verify by
    pre-seeding an undispatched pending state and overwriting it."""
    from plugin.plugins.game_agent_minecraft.service import PendingTask

    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # Plant an undispatched pending task in the slot directly. This
    # simulates the corner where OLD's handler claimed _pending but
    # was suspended inside ``send_task`` and never returned.
    fake_old = PendingTask(
        task_text="undispatched-old",
        event=asyncio.Event(),
        start_time=0.0,
        dispatched=False,
    )
    service._pending = fake_old
    service._task_finished = False

    # New task arrives with overwrite=True — should set old's event,
    # claim the slot, and crucially NOT bump the drop counter.
    new_runner = asyncio.create_task(
        service.execute_minecraft_task(task="new", overwrite=True)
    )

    # Wait for the new task to claim the slot.
    for _ in range(50):
        if service._pending is not None and service._pending is not fake_old:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("new task never claimed the slot")

    # Old task's event was set → its handler would resolve, but we
    # injected it directly so there's no handler to drain. Just
    # verify the counter rule.
    assert service._stale_task_finishes_to_drop == 0, (
        "must not bump for undispatched abandoned task"
    )

    # Now drive the agent's task_finished for the new task — it
    # should resolve normally (NOT be swallowed as stale).
    await service._on_task_finished({"status": "ok", "text": "new done"})
    out = await asyncio.wait_for(new_runner, timeout=2.0)
    assert out == {"status": "ok", "query": "new"}


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
    else:
        pytest.fail("service._pending was never set within poll budget")

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
