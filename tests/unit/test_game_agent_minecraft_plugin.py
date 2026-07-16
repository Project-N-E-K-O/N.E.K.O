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

    async def send_task(self, task: str, *, task_id: str = "") -> bool:
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
    assert service._task_timeout == 90.0  # default
    assert service._system_prompt_interval == 1.0  # clamped from -5
    assert service._screenshot_cache_size == 3  # default
    assert service._reconnect_interval == 5.0  # default


def test_configure_clamps_task_timeout_below_sdk_ceiling():
    """``@llm_tool(timeout=300.0)`` is the SDK wrapper ceiling. The
    service must clamp configured ``task_timeout_seconds`` strictly
    *below* that with a meaningful buffer (≥ 1s) so its structured
    ``{status: "timeout"}`` response reliably fires before the
    wrapper cancels the handler — anything within 300s would race
    the wrapper's cancel."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 600.0})
    # Strict <= 295.0 (5s buffer below 300s SDK ceiling). If
    # implementation regresses to a thinner buffer (e.g. 299.5),
    # this test catches it.
    assert service._task_timeout <= 295.0
    assert 300.0 - service._task_timeout >= 1.0, (
        "buffer below SDK ceiling must be at least 1s"
    )
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

        async def send_task(self, task, *, task_id=""):
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
async def test_task_finished_text_propagates_to_tool_result():
    """The agent's free-text completion message (``text``/``data``/
    ``message`` field on the task_finished frame) must reach the
    LLM as part of the tool result — not just status/query."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    async def driver():
        for _ in range(50):
            if service._pending is not None:
                break
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("handler never set _pending")
        await service._on_task_finished({
            "status": "ok",
            "text": "Mined 10 oak logs, inventory full.",
        })

    runner = asyncio.create_task(driver())
    out = await service.execute_minecraft_task(task="mine 10 oak logs")
    await runner

    assert out["status"] == "ok"
    assert out["query"] == "mine 10 oak logs"
    assert out["text"] == "Mined 10 oak logs, inventory full."


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
        # Frame without ``text`` — text propagation is covered by a
        # dedicated test; here we only verify the basic resolve-on-
        # ``task_finished`` contract.
        await service._on_task_finished({"status": "ok"})

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
    service, push_calls = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()
    # This test exercises the interrupt-with-status path in isolation; the
    # separate anti-thrash floor (_OVERWRITE_MIN_SURVIVAL_S, exercised by its
    # own test) would otherwise reject this immediate overwrite. Disable it
    # here so the overwrite reaches the interrupt branch deterministically.
    service._OVERWRITE_MIN_SURVIVAL_S = 0.0

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
    old_id = service._pending.task_id

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
    new_id = service._pending.task_id

    # The agent emits a delayed ``task_finished`` for the *old* task
    # before the new one finishes. With task_id echo (the protocol
    # mc-agent actually uses), this routes to the retroactive cue
    # path — the new pending slot is untouched and the dialog LLM
    # gets a "your earlier 「old task」 actually finished" cue.
    push_calls.clear()
    await service._on_task_finished(
        {"status": "ok", "text": "old finished late", "task_id": old_id}
    )
    assert service._pending is not None  # still the new task
    assert service._pending.task_id == new_id
    # The retroactive cue should have fired. Match locale-agnostically:
    # the cue is localized via prompts.t() (depends on service._lang),
    # but the task_text is interpolated raw and therefore stable across
    # locales.
    assert any(
        "old task" in (p.get("text") or "")
        for call in push_calls
        for p in (call.get("parts") or [])
    ), "retroactive completion cue was not pushed for the old task"

    # Now mc-agent reports the new task's completion, echoing its id.
    push_calls.clear()
    await service._on_task_finished({"status": "ok", "task_id": new_id})
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
    # Frames are now downscaled + re-encoded to JPEG so the pushed payload
    # stays under the message_plane cap (the old JPEG→lossless-PNG path
    # ballooned frames to multi-MB and got silently dropped at ingest).
    assert part["mime"] == "image/jpeg"
    assert isinstance(part["data"], bytes) and len(part["data"]) > 0
    # The bytes must be a valid JPEG that round-trips through Pillow.
    from PIL import Image
    import io
    with Image.open(io.BytesIO(part["data"])) as _im:
        assert _im.format == "JPEG"


@pytest.mark.asyncio
async def test_screenshot_high_entropy_frame_fits_byte_budget():
    """A high-detail frame that exceeds the byte budget at the default
    edge/quality must be stepped down until it fits. Capping resolution +
    quality alone is insufficient because the wire payload base64-encodes the
    frame AND carries a raw copy (~2.3x raw + envelope vs the 256KB cap)."""
    import base64
    import io
    from PIL import Image

    service, push_calls = _make_service()
    budget = 100 * 1024
    service.configure({
        "stream_screenshots_to_llm": True,
        "screenshot_max_bytes": budget,
    })

    # Random noise compresses terribly — a 1500x1500 noise frame is multi-100KB
    # at q80, well over the budget, forcing the quality/edge step-down loop.
    import os
    noise = Image.frombytes("RGB", (1500, 1500), os.urandom(1500 * 1500 * 3))
    raw = io.BytesIO()
    noise.save(raw, format="PNG")
    payload = base64.b64encode(raw.getvalue()).decode("ascii")

    await service._on_screenshot(payload, "png")

    assert len(push_calls) == 1
    part = push_calls[0]["parts"][0]
    assert part["type"] == "image" and part["mime"] == "image/jpeg"
    # The whole point: the pushed frame respects the raw-bytes budget.
    assert len(part["data"]) <= budget, f"frame {len(part['data'])} > budget {budget}"
    with Image.open(io.BytesIO(part["data"])) as _im:
        assert _im.format == "JPEG"


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
async def test_system_prompt_bundles_only_latest_frame_with_mime():
    """The autonomous burst bundles ONLY the most recent cached frame (stacking
    several into one push blows the message_plane payload cap — each frame is
    base64'd and the legacy binary_data carries a raw copy). It must also use
    that frame's actual mime, not a hardcoded image/png, or downstream
    ``stream_image`` would receive mis-tagged bytes."""
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
    # Only the latest frame is sent, with its own mime preserved.
    assert len(image_parts) == 1
    assert image_parts[0]["mime"] == "image/jpeg"
    assert image_parts[0]["data"] == b"<jpeg-bytes>"
    # The cache is fully drained so stale frames aren't re-sent next burst.
    assert service.get_status()["screenshot_cache_size"] == 0


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
async def test_log_connection_lost_wakes_pending_handler():
    """If a task is pending when the agent reconnects, its
    ``task_finished`` will never arrive (agent's task queue was
    wiped). The handler must be woken with an "interrupted" verdict
    so it doesn't sit blocked on ``event.wait`` until
    ``task_timeout_seconds`` expires."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 30.0})
    service._client = _FakeClient()

    runner = asyncio.create_task(service.execute_minecraft_task(task="long task"))
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("_pending was never set")

    await service._on_log("Connection lost and re-established.")

    # Pending was woken with an interrupted verdict.
    out = await asyncio.wait_for(runner, timeout=2.0)
    assert out["status"] == "interrupted"
    assert "connection bounced" in out["reason"].lower()
    # Slot is free for the next call.
    assert service._pending is None


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
    # Note: ``Connection lost and re-established.`` intentionally
    # behaves differently when a task is pending — it wakes the
    # handler with "interrupted" because the agent's task queue
    # was wiped. That contract is pinned in
    # ``test_log_connection_lost_wakes_pending_handler``.

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


# ---------------------------------------------------------------------------
# task_finished routing — with task_id correlation the plugin decides one of
# three outcomes per frame: wake the current pending handler, emit a
# retroactive completion cue for a historical (overwritten) task, or drift
# (no pending + unknown id).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_task_id_correlation_resolves_out_of_order():
    """When the agent echoes ``task_id`` on ``task_finished``, the plugin
    matches the id to ``_pending`` and accepts the frame regardless of
    arrival order (the old FIFO drop counter is gone — each frame is
    classified by id, never by arrival sequence)."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    runner = asyncio.create_task(
        service.execute_minecraft_task(task="B")
    )
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("_pending was never set")
    b_id = service._pending.task_id
    assert b_id  # service generated a task_id

    await service._on_task_finished({"status": "ok", "task_id": b_id})
    out = await asyncio.wait_for(runner, timeout=2.0)
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_unknown_task_id_with_pending_does_not_resolve_pending():
    """A ``task_finished`` echoing an id we never dispatched (e.g.
    leftover from a previous session that somehow leaked through) must
    not be misattributed to the current pending task. With no history
    match and no FIFO assumption, the frame is logged as drift; the
    pending runner keeps waiting for its own id-matched frame."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    runner = asyncio.create_task(
        service.execute_minecraft_task(task="B")
    )
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("_pending was never set")
    b_id = service._pending.task_id

    # Seed a known-good inventory snapshot from before the ghost frame
    # arrives, so we can assert the ghost doesn't overwrite it.
    service._last_inventory = {"dirt": 5}
    service._last_inventory_at = 1234.0

    # Frame echoing some other id — not in history, doesn't match pending.
    # It carries inventory but that inventory belongs to whatever session
    # produced the ghost id, not ours. Must not leak into our cache.
    await service._on_task_finished({
        "status": "ok", "task_id": "ghost-id", "inventory": {"diamond": 99},
    })
    assert service._pending is not None
    assert service._pending.task_text == "B"
    # Runner future must still be pending — a ghost task_id must not
    # accidentally event.set() the wrong slot. (Belt-and-braces with
    # the _pending check above: catches a future regression where the
    # slot is correctly preserved but the runner's event fires anyway.)
    assert not runner.done(), "ghost task_id must not resolve current runner"
    # Cache untouched — ghost frame's inventory must not have overwritten
    # the seed we set above.
    assert service._last_inventory == {"dirt": 5}
    assert service._last_inventory_at == 1234.0

    # Real B completion still resolves the runner.
    await service._on_task_finished({"status": "ok", "task_id": b_id})
    out = await asyncio.wait_for(runner, timeout=2.0)
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_historical_task_id_emits_retroactive_cue():
    """A late ``task_finished`` for a task that was dispatched and then
    overwritten/abandoned routes to the retroactive cue path: a
    push_message is sent so the dialog LLM learns the earlier action
    completed, but the current pending slot is untouched."""
    service, push_calls = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # Start + complete one task to seed the dispatched history with a
    # known id, then start a second task so there's something current
    # to defend against accidental overwrite.
    runner1 = asyncio.create_task(service.execute_minecraft_task(task="first"))
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("first task never claimed pending slot")
    first_id = service._pending.task_id
    await service._on_task_finished({"status": "ok", "task_id": first_id})
    await asyncio.wait_for(runner1, timeout=2.0)

    # Now a fresh second task in flight.
    runner2 = asyncio.create_task(service.execute_minecraft_task(task="second"))
    for _ in range(50):
        if service._pending is not None and service._pending.task_text == "second":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("second task never claimed pending slot")
    second_id = service._pending.task_id

    push_calls.clear()
    # A surprise late frame echoing first_id arrives (e.g. mc-agent
    # buffered something). Routes to retroactive cue.
    await service._on_task_finished(
        {"status": "ok", "text": "first done late", "task_id": first_id}
    )
    # The second task's pending slot is untouched.
    assert service._pending is not None
    assert service._pending.task_id == second_id
    # And runner2 must still be pending — the retroactive cue path
    # must never accidentally event.set() the current pending slot.
    assert not runner2.done(), "historical task_id must not resolve current runner"
    # And a retroactive cue was pushed referencing the first task.
    # Match locale-agnostically — the cue is localized, but task_text
    # is interpolated raw and stable across locales.
    assert any(
        "first" in (p.get("text") or "")
        for call in push_calls
        for p in (call.get("parts") or [])
    ), "retroactive cue was not pushed for the historical task"

    # Cleanup: resolve the second task so the runner doesn't dangle.
    await service._on_task_finished({"status": "ok", "task_id": second_id})
    await asyncio.wait_for(runner2, timeout=2.0)


@pytest.mark.asyncio
async def test_no_pending_no_id_marks_idle():
    """Drift case: a ``task_finished`` arrives with no task_id and no
    pending slot (e.g. residue from before a stop+start cycle). With
    nothing to wake, the only state change is flipping ``_task_finished``
    to True so the autonomous loop's busy gate can resume nudging."""
    service, _ = _make_service()
    service.configure({})
    service._task_finished = False  # simulate stuck-flag scenario
    assert service._pending is None

    await service._on_task_finished({"status": "ok", "text": "stray"})
    assert service._task_finished is True


@pytest.mark.asyncio
async def test_idless_frame_after_seen_id_drops_to_stray():
    """Modern mc-agent always echoes task_id. Once we've latched on
    that fact (``_seen_task_id_echo``), an id-less ``task_finished``
    is anomalous (e.g. stale completion from a task we already
    overwrote) and must NOT FIFO-resolve the current pending — doing
    so would silently misroute the stale payload onto a newer task.
    """
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # First task: A, completes with task_id → latches _seen_task_id_echo.
    runner_a = asyncio.create_task(service.execute_minecraft_task(task="A"))
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("task A never claimed pending slot")
    a_id = service._pending.task_id
    await service._on_task_finished({"status": "ok", "task_id": a_id})
    await asyncio.wait_for(runner_a, timeout=2.0)
    assert service._seen_task_id_echo is True

    # Second task: B is now in flight (legitimately claimed pending).
    runner_b = asyncio.create_task(service.execute_minecraft_task(task="B"))
    for _ in range(50):
        if service._pending is not None and service._pending.task_text == "B":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("task B never claimed pending slot")
    b_id = service._pending.task_id

    # A stale id-less completion arrives (would have FIFO-resolved B
    # under the old fallback). With the latch, this routes to stray.
    await service._on_task_finished({"status": "ok", "text": "stale!"})
    assert service._pending is not None
    assert service._pending.task_id == b_id
    assert not runner_b.done(), \
        "id-less frame after seen task_id echo must not resolve pending"

    # B's real completion still resolves cleanly.
    await service._on_task_finished({"status": "ok", "task_id": b_id})
    out = await asyncio.wait_for(runner_b, timeout=2.0)
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_seen_task_id_latch_resets_on_ws_restart():
    """The ``_seen_task_id_echo`` latch must clear on ``service.stop()``
    so a reconnect to a different mc-agent version (e.g. user
    downgraded to a legacy build that doesn't echo task_id) re-learns
    from the next frame. Without the reset, post-restart id-less
    completions from the legacy agent would be misrouted to ``stray``
    and the runner would only resolve on its 120s timeout."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # Session 1: modern agent (echoes task_id) — latches True.
    runner1 = asyncio.create_task(service.execute_minecraft_task(task="modern"))
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("modern task never claimed pending slot")
    modern_id = service._pending.task_id
    await service._on_task_finished({"status": "ok", "task_id": modern_id})
    await asyncio.wait_for(runner1, timeout=2.0)
    assert service._seen_task_id_echo is True

    # WS restart (config reload, ws_url change, or transport drop).
    await service.stop()
    assert service._seen_task_id_echo is False, \
        "_seen_task_id_echo must reset on stop() so reconnect re-learns"

    # Session 2: legacy agent (never echoes task_id) — FIFO must still
    # fire so the new pending isn't stranded.
    service._client = _FakeClient()
    runner2 = asyncio.create_task(service.execute_minecraft_task(task="legacy"))
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("legacy task never claimed pending slot")

    await service._on_task_finished({"status": "ok", "text": "done"})
    out = await asyncio.wait_for(runner2, timeout=2.0)
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_foreign_task_id_does_not_latch_legacy_off():
    """The ``_seen_task_id_echo`` latch must only flip on frames that
    are proven to be ours (current pending or dispatched history).
    A foreign id from a leaked frame (another client on the same WS
    endpoint, buffered prior-session frame, mc-agent restart
    crossover) lands in ``unknown`` and must NOT flip the latch —
    otherwise a legacy agent that never echoes its own ids would
    be permanently locked out of the FIFO fallback by a single
    foreign-id frame, leaving every subsequent task on a 120s
    timeout.
    """
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    # Foreign-id frame arrives before any of our tasks (no pending,
    # not in dispatched_history) → bucket unknown → latch must stay False.
    await service._on_task_finished(
        {"status": "ok", "task_id": "leaked-from-another-client"}
    )
    assert service._seen_task_id_echo is False, \
        "foreign task_id (unknown bucket) must not flip the latch"

    # Now a legacy id-less task: should still FIFO-resolve because
    # the latch was never flipped.
    runner = asyncio.create_task(service.execute_minecraft_task(task="legacy"))
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("legacy task never claimed pending slot")

    await service._on_task_finished({"status": "ok", "text": "done"})
    out = await asyncio.wait_for(runner, timeout=2.0)
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_idless_frame_legacy_agent_still_uses_fifo():
    """Genuine legacy mc-agent that never echoes task_id keeps the
    FIFO fallback — without ``_seen_task_id_echo`` ever latching,
    an id-less ``task_finished`` correctly resolves the current
    pending so legacy users aren't stuck on a 120s timeout."""
    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 5.0})
    service._client = _FakeClient()

    runner = asyncio.create_task(service.execute_minecraft_task(task="legacy"))
    for _ in range(50):
        if service._pending is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("legacy task never claimed pending slot")

    # Legacy agent reply: no task_id. Latch is still False.
    assert service._seen_task_id_echo is False
    await service._on_task_finished({"status": "ok", "text": "done"})
    out = await asyncio.wait_for(runner, timeout=2.0)
    assert out["status"] == "ok"


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

        async def send_task(self, task, *, task_id=""):
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


# ---------------------------------------------------------------------------
# i18n: every prompt key is fully translated across all 7 supported locales.
# Catches forgotten translations + drift between locale tables.
# ---------------------------------------------------------------------------


def test_task_tool_description_preserves_master_words_and_coordinates():
    """The mounted tool must preserve explicit master instructions verbatim."""
    from plugin.plugins.game_agent_minecraft import (
        MINECRAFT_TASK_DESCRIPTION,
        MINECRAFT_TASK_SCHEMA,
    )

    description = MINECRAFT_TASK_DESCRIPTION
    task_schema_description = (
        MINECRAFT_TASK_SCHEMA["properties"]["task"]["description"]
    )

    assert "relay the exact original wording" in description
    assert "Do not translate, paraphrase" in description
    assert "NEVER invent, infer, choose, or include in-game coordinates" in description
    assert "unless master explicitly asked" in description
    assert "master's most recent message" in description
    assert "must not come from an earlier master message" in description
    assert "NEVER call back, recover, or re-dispatch" in description
    assert "has not already been dispatched" in task_schema_description
    assert "exact original wording" in task_schema_description
    assert "unless master explicitly asked for coordinates" in task_schema_description
    assert "English with specific targets" not in description
    assert "exact coordinates, specific block" not in description

    from plugin.plugins.game_agent_minecraft import prompts

    assert "dispatch a fresh task" not in prompts.t(
        "COMPLETION_FOLLOWUP_BLOCKED", lang="en"
    )
    assert "different coordinates" not in prompts.t(
        "COMPLETION_FOLLOWUP_BLOCKED", lang="en"
    )
    for key in (
        "COMPLETION_FOLLOWUP_BLOCKED",
        "COMPLETION_FOLLOWUP_SUCCESS",
        "COMPLETION_FOLLOWUP_FAILED",
        "RETROACTIVE_FOLLOWUP",
    ):
        cue = prompts.t(key, lang="en")
        assert "unfinished explicit instruction" not in cue
        assert "next step" not in cue


def test_prompts_have_all_seven_locales():
    """Every entry in PROMPTS must carry a non-empty translation for
    each supported language (zh, en, ja, ko, ru, es, pt). Missing keys
    would otherwise silently fall back to EN and hide translation gaps
    from non-EN users."""
    from plugin.plugins.game_agent_minecraft import prompts

    # Pin the expected locale set so the "seven locales" promise can't
    # be silently regressed by editing SUPPORTED_LANGS down to six.
    assert set(prompts.SUPPORTED_LANGS) == {
        "zh", "en", "ja", "ko", "ru", "es", "pt",
    }
    missing: list[str] = []
    for key, bundle in prompts.PROMPTS.items():
        for lang in prompts.SUPPORTED_LANGS:
            if not bundle.get(lang):
                missing.append(f'{key}[{lang}]')
    assert not missing, (
        'Missing/empty translations in PROMPTS: ' + ', '.join(missing)
    )


def test_prompts_t_formats_placeholders():
    r"""prompts.t() must accept \*\*fmt and substitute via str.format,
    while leaving {{MASTER_NAME}} escaped for downstream main_server
    substitution."""
    from plugin.plugins.game_agent_minecraft import prompts

    out_en = prompts.t('TASK_BUSY_HINT', lang='en', current='mine stone')
    assert 'mine stone' in out_en
    # Downstream placeholder must survive str.format intact.
    assert '{MASTER_NAME}' in out_en
    assert '{{MASTER_NAME}}' not in out_en


def test_prompts_t_falls_back_to_english_on_missing_locale():
    """Unknown locales must resolve to EN, not raise."""
    from plugin.plugins.game_agent_minecraft import prompts

    out = prompts.t('TASK_NOT_CONNECTED', lang='xx-NOSUCH')
    en = prompts.t('TASK_NOT_CONNECTED', lang='en')
    assert out == en


# ---------------------------------------------------------------------------
# Status vocab tolerance — mc-agent now emits ``failed`` / ``superseded``
# alongside ``ok`` / ``interrupted``. The plugin must tolerate every value
# (no crash — status is an opaque string everywhere) AND frame the new ones
# correctly: ``superseded`` is a non-completion, NOT a failure, and a late
# non-completion frame must not be narrated as "actually finished".
# ---------------------------------------------------------------------------


def _make_facade(lang: str = "en"):
    """Build just enough of the plugin facade to exercise
    ``_format_completion_cue`` without the SDK ctx / WebSocket. That method
    only reads ``self._lang`` + module-level prompts, so bypass __init__
    (same object.__new__ trick used elsewhere in the suite for handler-only
    methods)."""
    from plugin.plugins.game_agent_minecraft import GameAgentMinecraftPlugin

    facade = object.__new__(GameAgentMinecraftPlugin)
    facade._lang = lang
    return facade


# " superseded " padded exercises the .strip() normalization; SUPERSEDED the
# .lower(). Both must classify into the interrupted bucket, which [NO-SWITCH-CUE]
# suppresses entirely (see _format_completion_cue's ``if is_interrupted``).
@pytest.mark.parametrize(
    "status", ["superseded", "interrupted", "SUPERSEDED", " superseded "]
)
def test_completion_cue_interrupted_is_suppressed(status):
    """``interrupted`` / ``superseded`` is a task the LLM (or user) already
    replaced on purpose — [NO-SWITCH-CUE] emits no completion cue at all so it
    can't nudge another dispatch (the observed task-switch retry storm). The
    case/whitespace variants must still land in that suppressed bucket."""
    facade = _make_facade("en")
    cue = facade._format_completion_cue({"status": status, "query": "mine 10 logs"})

    assert cue == ""


@pytest.mark.parametrize("status", ["failed", "FAILED"])
def test_completion_cue_failed_still_reads_as_failure(status):
    """Regression guard: ``failed`` must stay in the genuine-failure bucket
    (it's not swept into the neutral interrupted carve-out); case-insensitive."""
    from plugin.plugins.game_agent_minecraft import prompts

    facade = _make_facade("en")
    cue = facade._format_completion_cue(
        {"status": status, "query": "reach the village"}
    )

    assert prompts.t("HEAD_VERB_FAILED", lang="en") in cue
    assert prompts.t("COMPLETION_FOLLOWUP_FAILED", lang="en") in cue


# "OK"/"ok " pin the .strip().lower() normalization for the success branch —
# a case- or whitespace-sensitive regression would mislabel a success as failure.
@pytest.mark.parametrize("status", ["ok", "OK", "ok "])
def test_completion_cue_ok_still_reads_as_success(status):
    """Regression guard: the new interrupted branch must not disturb the
    success path (no detail → no blocked-marker rewrite)."""
    from plugin.plugins.game_agent_minecraft import prompts

    facade = _make_facade("en")
    cue = facade._format_completion_cue({"status": status, "query": "place a torch"})

    assert prompts.t("HEAD_VERB_SUCCESS", lang="en") in cue
    assert prompts.t("COMPLETION_FOLLOWUP_SUCCESS", lang="en") in cue


def test_completion_cue_ok_but_blocked_text_reads_as_blocked():
    """status='ok' whose feedback text signals a blocked action (mc-agent's
    'ok but really stuck' quirk) must be re-labeled blocked, not success —
    guards the shared ``text_signals_blocked`` marker detection."""
    from plugin.plugins.game_agent_minecraft import prompts

    facade = _make_facade("en")
    cue = facade._format_completion_cue(
        {"status": "ok", "query": "walk to 博士", "text": "could not find path to 博士"}
    )

    assert prompts.t("HEAD_VERB_BLOCKED", lang="en") in cue
    assert prompts.t("COMPLETION_FOLLOWUP_BLOCKED", lang="en") in cue
    assert prompts.t("COMPLETION_FOLLOWUP_SUCCESS", lang="en") not in cue


# ``failed`` / ``timeout`` are genuine non-completions the plugin still narrates
# (honestly, via the 'ended without completing' header). ``interrupted`` /
# ``superseded`` are handled separately below — [NO-SWITCH-CUE] drops them.
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "timeout"])
async def test_retroactive_cue_non_completion_does_not_claim_finished(status):
    """A late ``task_finished`` for an overwritten/abandoned task whose
    status is a non-completion must use the honest 'ended without completing'
    header — never RETROACTIVE_HEADER's 'actually finished', which would
    fabricate a success the character would then narrate."""
    from plugin.plugins.game_agent_minecraft import prompts

    service, push_calls = _make_service()
    service.set_lang("en")
    # A task the plugin already moved on from, still tracked in history.
    service._remember_dispatched("hist-1", "build a cobblestone wall")
    service._seen_task_id_echo = True  # modern agent that echoes task_id

    await service._on_task_finished(
        {"status": status, "task_id": "hist-1", "text": "stopped partway"}
    )

    blob = "\n".join(
        p.get("text") or ""
        for call in push_calls
        for p in (call.get("parts") or [])
    )
    # Cue fired (task_text is interpolated raw, stable across locales).
    assert "build a cobblestone wall" in blob
    ended = prompts.t(
        "RETROACTIVE_HEADER_ENDED", lang="en",
        task_text="build a cobblestone wall", status=status,
    )
    finished = prompts.t(
        "RETROACTIVE_HEADER", lang="en",
        task_text="build a cobblestone wall", status=status,
    )
    assert ended in blob
    assert finished not in blob


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["superseded", "interrupted"])
async def test_retroactive_cue_interrupted_superseded_is_suppressed(status):
    """[NO-SWITCH-CUE]: a late ``interrupted`` / ``superseded`` frame is a task
    the LLM already replaced on purpose. Re-surfacing it retroactively only
    nudges another dispatch (the retry storm), so no cue fires at all — the
    task text is never surfaced."""
    service, push_calls = _make_service()
    service.set_lang("en")
    service._remember_dispatched("hist-1", "build a cobblestone wall")
    service._seen_task_id_echo = True

    await service._on_task_finished(
        {"status": status, "task_id": "hist-1", "text": "stopped partway"}
    )

    blob = "\n".join(
        p.get("text") or ""
        for call in push_calls
        for p in (call.get("parts") or [])
    )
    assert "build a cobblestone wall" not in blob


@pytest.mark.asyncio
async def test_retroactive_cue_ok_still_claims_finished():
    """Regression guard: a genuine late completion (``ok``) keeps the
    'actually finished' header — the whole reason the retroactive cue
    exists is to tell the character the action she gave up on really did complete."""
    from plugin.plugins.game_agent_minecraft import prompts

    service, push_calls = _make_service()
    service.set_lang("en")
    service._remember_dispatched("hist-2", "smelt 8 iron")
    service._seen_task_id_echo = True

    await service._on_task_finished(
        {"status": "ok", "task_id": "hist-2", "text": "done late"}
    )

    blob = "\n".join(
        p.get("text") or ""
        for call in push_calls
        for p in (call.get("parts") or [])
    )
    finished = prompts.t(
        "RETROACTIVE_HEADER", lang="en",
        task_text="smelt 8 iron", status="ok",
    )
    ended = prompts.t(
        "RETROACTIVE_HEADER_ENDED", lang="en",
        task_text="smelt 8 iron", status="ok",
    )
    assert finished in blob
    assert ended not in blob


@pytest.mark.asyncio
async def test_retroactive_cue_ok_but_blocked_text_does_not_claim_finished():
    """A late status='ok' frame whose text signals a blocked action must NOT
    assert 'actually finished' — the same 'ok but really stuck' quirk the live
    completion cue guards. Otherwise the cue says 'it actually finished' while its
    own feedback line shows 'could not find path'."""
    from plugin.plugins.game_agent_minecraft import prompts

    service, push_calls = _make_service()
    service.set_lang("en")
    service._remember_dispatched("hist-3", "walk to 博士")
    service._seen_task_id_echo = True

    await service._on_task_finished(
        {"status": "ok", "task_id": "hist-3", "text": "could not find path to 博士"}
    )

    blob = "\n".join(
        p.get("text") or ""
        for call in push_calls
        for p in (call.get("parts") or [])
    )
    ended = prompts.t(
        "RETROACTIVE_HEADER_ENDED", lang="en",
        task_text="walk to 博士", status="ok",
    )
    finished = prompts.t(
        "RETROACTIVE_HEADER", lang="en",
        task_text="walk to 博士", status="ok",
    )
    assert ended in blob
    assert finished not in blob


@pytest.mark.asyncio
async def test_busy_flag_cleared_on_dispatched_task_completion():
    """[BUSY] A dispatched task's terminal ``task_finished`` must clear the
    ``_agent_busy`` flag set by an earlier bot_status_nl. Without this the
    keep-going nudge stays suppressed for the full ``_agent_busy_ttl`` while
    the agent sits idle after completing, leaving a silent avatar for ~1min."""
    import time
    from plugin.plugins.game_agent_minecraft.service import PendingTask

    service, _ = _make_service()
    service._seen_task_id_echo = True
    service._pending = PendingTask(
        task_text="mine 10 logs", event=asyncio.Event(),
        start_time=0.0, task_id="t1",
    )
    # An in-flight bot_status_nl reported the agent busy; no later frame arrives.
    service._agent_busy = True
    service._agent_busy_at = time.time()

    await service._on_task_finished({"status": "ok", "task_id": "t1"})

    assert service._pending is None
    assert service._agent_busy is False
    assert service._mc_agent_busy() is False


@pytest.mark.asyncio
async def test_connection_bounce_wakes_pending_even_when_deduped():
    """[CONTROL-LOG] A repeated 'Connection lost and re-established.' inside the
    dedup window must still wake a pending task with the interrupted verdict.
    The control-log state handling runs before the dedup early-return, so a
    second reconnect during a pending task isn't dropped — otherwise the handler
    would sit until ``task_timeout_seconds``."""
    import time
    from plugin.plugins.game_agent_minecraft.service import PendingTask

    service, _ = _make_service()
    pending = PendingTask(
        task_text="dig straight down", event=asyncio.Event(),
        start_time=0.0, task_id="t2",
    )
    service._pending = pending
    # Same control log already seen moments ago → the dedup gate would drop it
    # if state handling ran after the dedup return.
    service._recent_log_times["Connection lost and re-established."] = time.time()

    await service._on_log("Connection lost and re-established.")

    assert service._pending is None
    assert pending.result.get("status") == "interrupted"
    assert pending.event.is_set()


@pytest.mark.asyncio
async def test_busy_flag_cleared_on_task_timeout():
    """[BUSY] A dispatched task that ends via the local timeout path (no
    task_finished ever arrives) must also clear _agent_busy — otherwise the
    keep-going nudge stays suppressed for the full TTL after the timeout."""
    import time

    service, _ = _make_service()
    service.configure({"task_timeout_seconds": 30.0})
    service._client = _FakeClient()
    service._task_timeout = 0.05  # force the timeout path fast
    service._agent_busy = True
    service._agent_busy_at = time.time()

    out = await service.execute_minecraft_task(task="mine forever")

    assert out["status"] == "timeout"
    assert service._agent_busy is False
    assert service._mc_agent_busy() is False


@pytest.mark.asyncio
async def test_busy_flag_cleared_on_connection_bounce():
    """[BUSY] A connection bounce ends the pending task via _on_log and anchors
    the recovery keep-going nudge — it must also clear _agent_busy, or that
    nudge stays suppressed for the full TTL after the bounce."""
    import time
    from plugin.plugins.game_agent_minecraft.service import PendingTask

    service, _ = _make_service()
    service._pending = PendingTask(
        task_text="build a wall", event=asyncio.Event(),
        start_time=0.0, task_id="t3",
    )
    service._agent_busy = True
    service._agent_busy_at = time.time()

    await service._on_log("Connection lost and re-established.")

    assert service._pending is None
    assert service._agent_busy is False
    assert service._mc_agent_busy() is False


@pytest.mark.asyncio
async def test_completion_text_with_command_syntax_is_filtered():
    """[ANTI-PARROT] A task_finished whose free-text carries mc-agent command
    syntax (goToCoordinates(...) etc.) must not reach _log_cache /
    RECENT_EVENTS_BLOCK or the completion payload — the _on_log anti-parrot
    filter only covers log frames, so completion text is sanitized at its
    derivation point too, else the dialog LLM re-dispatches the raw command."""
    from plugin.plugins.game_agent_minecraft.service import PendingTask

    service, _ = _make_service()
    service._seen_task_id_echo = True
    pending = PendingTask(
        task_text="go mine", event=asyncio.Event(), start_time=0.0, task_id="t4",
    )
    service._pending = pending

    await service._on_task_finished(
        {"status": "ok", "task_id": "t4",
         "text": "Finished goToCoordinates(118,64,-115,1)"}
    )

    # Task still resolved as a success...
    assert pending.result.get("status") == "ok"
    assert pending.event.is_set()
    # ...but the command-syntax text never reaches model-visible sinks.
    assert all("goToCoordinates" not in ln for ln in service._log_cache)
    assert "goToCoordinates" not in (pending.result.get("text") or "")


@pytest.mark.asyncio
async def test_completion_ok_with_command_syntax_keeps_block_signal():
    """[ANTI-PARROT] Scrubbing command syntax must NOT strip a co-located block
    signal: a status='ok' completion whose text is
    'Failed goToCoordinates(...): could not find path' must keep 'could not find
    path' so text_signals_blocked still fires (else the blocked action reads as a
    success), while the command syntax is still removed."""
    from plugin.plugins.game_agent_minecraft.service import (
        PendingTask,
        text_signals_blocked,
    )

    service, _ = _make_service()
    service._seen_task_id_echo = True
    pending = PendingTask(
        task_text="walk to base", event=asyncio.Event(), start_time=0.0, task_id="t5",
    )
    service._pending = pending

    await service._on_task_finished(
        {"status": "ok", "task_id": "t5",
         "text": "Failed goToCoordinates(1,2,3): could not find path"}
    )

    out_text = pending.result.get("text") or ""
    assert "goToCoordinates" not in out_text        # command syntax scrubbed
    assert "could not find path" in out_text          # block signal preserved
    assert text_signals_blocked(out_text)             # → blocked, not success


@pytest.mark.asyncio
async def test_busy_flag_cleared_on_task_run_ended_log_when_idle():
    """[BUSY] The 'task run ended' terminal log (no pending) marks the service
    finished — it must also clear _agent_busy, matching the other terminal
    paths, or the keep-going nudge stays suppressed for the full TTL."""
    import time

    service, _ = _make_service()
    service._agent_busy = True
    service._agent_busy_at = time.time()
    assert service._pending is None

    await service._on_log("task run ended")

    assert service._task_finished is True
    assert service._agent_busy is False
    assert service._mc_agent_busy() is False


@pytest.mark.asyncio
async def test_busy_flag_cleared_on_stray_task_finished_when_idle():
    """[BUSY] An autonomous/stray task_finished (no pending, no known id) marks
    the service idle — it must also clear _agent_busy, or the recovery keep-going
    nudge is suppressed for the full TTL after an autonomous completion."""
    import time

    service, _ = _make_service()
    service._agent_busy = True
    service._agent_busy_at = time.time()
    assert service._pending is None

    await service._on_task_finished(
        {"status": "ok", "text": "did an autonomous thing"}
    )

    assert service._task_finished is True
    assert service._agent_busy is False
    assert service._mc_agent_busy() is False


@pytest.mark.asyncio
async def test_stale_bot_status_does_not_rearm_busy_after_terminal():
    """[BUSY-RACE] A busy bot_status_nl that lags a completion (arrives within the
    re-arm grace after a terminal marked idle) must NOT re-arm _agent_busy — else
    recovery stalls for the full TTL while the agent is already idle."""
    service, _ = _make_service()
    service._seen_task_id_echo = True
    service._pending = None

    await service._on_task_finished({"status": "ok", "text": "done"})
    assert service._agent_busy is False

    # Untagged busy status lagging the completion — no tag, no pending → the
    # conservative policy does not re-arm it.
    await service._on_bot_status({"text": "mining", "skill": "mine", "kind": "cmd"})

    assert service._agent_busy is False
    assert service._mc_agent_busy() is False


@pytest.mark.asyncio
async def test_untagged_busy_rearms_while_task_pending():
    """A busy bot_status_nl re-arms _agent_busy while a dispatched task is pending
    — the pending task is the correlation, even for an untagged frame."""
    from plugin.plugins.game_agent_minecraft.service import PendingTask

    service, _ = _make_service()
    service._pending = PendingTask(
        task_text="mine ore", event=asyncio.Event(), start_time=0.0, task_id="tp",
    )

    await service._on_bot_status({"text": "working", "skill": "mine", "kind": "cmd"})

    assert service._agent_busy is True
    assert service._mc_agent_busy() is True


@pytest.mark.asyncio
async def test_stale_dispatched_status_does_not_rearm_when_idle():
    """A dispatched-command status frame with no pending task is a stale trailing
    report of the just-finished dispatch — it must not re-arm busy, regardless of
    arrival time (a content correlation, not a timing guess)."""
    service, _ = _make_service()
    service._pending = None
    service._agent_busy = False

    await service._on_bot_status(
        {"text": "🎯[按指令] goToCoordinates", "skill": "goto", "kind": "cmd"}
    )

    assert service._agent_busy is False
    assert service._mc_agent_busy() is False


@pytest.mark.asyncio
async def test_untagged_busy_does_not_rearm_when_idle():
    """[BUSY-RACE] An untagged busy bot_status_nl with no pending task can't be
    correlated to real work (stale or unattributable) — the conservative policy
    does not re-arm busy at any delay, so recovery isn't stranded for the TTL."""
    service, _ = _make_service()
    service._pending = None
    service._agent_busy = False

    await service._on_bot_status({"text": "still going", "skill": "mine", "kind": "cmd"})

    assert service._agent_busy is False
    assert service._mc_agent_busy() is False


@pytest.mark.asyncio
async def test_autonomous_status_rearms_busy_without_pending():
    """An autonomous status frame reflects genuine ongoing activity — it re-arms
    busy even with no pending task (autonomy is its own correlation)."""
    service, _ = _make_service()
    service._pending = None
    service._agent_busy = False

    await service._on_bot_status(
        {"text": "🤖[自主] exploring", "skill": "explore", "kind": "auto"}
    )

    assert service._agent_busy is True
