"""Teardown ownership: a cancelled caller must not strand an open connection.

Three teardown paths detached their resource first and awaited the slow part
afterwards, so a cancel landing in between took the only reference with it:

- ``OmniRealtimeClient.close()`` detaches ``self.ws`` before awaiting the
  arbiter shutdown (deliberately — no ticket may outlive the socket);
- ``_close_failed_transport()`` does the same on the fatal path;
- ``_close_gemini()`` cleared the SDK context manager in a ``finally``, which
  runs on cancel too, so a retry found nothing left to exit;
- ``_cleanup_pending_session_resources()`` cleared ``pending_session`` in a
  ``finally`` while ``_reset_preparation_state`` cancels its caller a *second*
  time when its 2s wait expires — landing inside that very close.

Every canceller here is internal: a hot-swap final task or background prep
task cancelled by a concurrent start/end_session. Reported as items 12-14 of
the #2602 index.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import main_logic.core as core_module
from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode


pytestmark = pytest.mark.unit


class _FakeWs:
    def __init__(self):
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1


def _make_client():
    return OmniRealtimeClient(
        base_url="wss://example.test/realtime",
        api_key="sk-test",
        model="qwen-omni-turbo-realtime",
        turn_detection_mode=TurnDetectionMode.MANUAL,
        api_type="qwen",
    )


def _gate_arbiter_shutdown(client):
    """Park the arbiter shutdown so a cancel lands after the ws was detached."""
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def _shutdown(reason):
        calls.append(reason)
        entered.set()
        await release.wait()

    client._response_arbiter.shutdown = _shutdown
    return entered, release, calls


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cancelled_close_still_closes_the_socket_it_detached():
    client = _make_client()
    ws = _FakeWs()
    client.ws = ws
    entered, release, calls = _gate_arbiter_shutdown(client)

    caller = asyncio.create_task(client.close())
    await asyncio.wait_for(entered.wait(), timeout=5)
    # The socket is already detached at this point — that is the whole window.
    assert client.ws is None

    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    release.set()
    # The retry a caller would make: it must await the same teardown rather
    # than see an empty ``self.ws`` and report success over an open socket.
    await asyncio.wait_for(client.close(), timeout=5)

    assert ws.close_calls == 1
    assert calls == ["realtime client closed"]


@pytest.mark.asyncio
async def test_cancelled_failed_transport_close_still_closes_the_socket():
    client = _make_client()
    ws = _FakeWs()
    client.ws = ws
    entered, release, calls = _gate_arbiter_shutdown(client)

    caller = asyncio.create_task(client._close_failed_transport("transport failed"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert client.ws is None
    # Latched before the teardown task runs: callers gate their sends on it.
    assert client._fatal_error_occurred is True

    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    release.set()
    await asyncio.wait_for(client._close_failed_transport("transport failed"), timeout=5)

    assert ws.close_calls == 1
    assert calls == ["transport failed"]


@pytest.mark.asyncio
async def test_repeated_close_runs_the_teardown_once():
    client = _make_client()
    ws = _FakeWs()
    client.ws = ws
    shutdown_calls = []

    async def _shutdown(reason):
        shutdown_calls.append(reason)

    client._response_arbiter.shutdown = _shutdown

    await client.close()
    await client.close()

    assert ws.close_calls == 1
    assert shutdown_calls == ["realtime client closed"]


@pytest.mark.asyncio
async def test_connect_rearms_close_ownership_for_the_new_socket():
    """The client object outlives a connection, so a finished teardown must not
    make the NEXT connection's close a no-op."""
    client = _make_client()
    first = _FakeWs()
    client.ws = first

    async def _shutdown(reason):
        return None

    client._response_arbiter.shutdown = _shutdown
    await client.close()
    assert first.close_calls == 1

    second = AsyncMock()
    with patch("websockets.connect", new_callable=AsyncMock, return_value=second):
        await client.connect(instructions="hi", native_audio=True)

    assert client.ws is second
    await client.close()
    second.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_close_inside_the_connect_window_does_not_latch_the_new_socket_shut():
    """Rearming at the top of connect() would leave the replacement socket
    behind a finished teardown: a close landing in the connect await window
    runs to completion against no socket, and every later close() just
    re-awaits that finished task."""
    client = _make_client()

    async def _shutdown(reason):
        return None

    client._response_arbiter.shutdown = _shutdown

    attached = AsyncMock()
    connecting = asyncio.Event()
    resume = asyncio.Event()

    async def _slow_connect(*args, **kwargs):
        connecting.set()
        await resume.wait()
        return attached

    with patch("websockets.connect", new=_slow_connect):
        connect_task = asyncio.create_task(
            client.connect(instructions="hi", native_audio=True)
        )
        await asyncio.wait_for(connecting.wait(), timeout=5)
        # An end_session racing the reconnect: nothing is attached yet, so this
        # close has no socket of its own to close.
        await asyncio.wait_for(client.close(), timeout=5)

        resume.set()
        await asyncio.wait_for(connect_task, timeout=5)

    assert client.ws is attached
    await asyncio.wait_for(client.close(), timeout=5)
    attached.close.assert_awaited_once_with()


# ── Gemini SDK context exit ──────────────────────────────────────────


class _GatedGeminiContext:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.exit_calls = 0

    async def __aexit__(self, *exc_info):
        self.exit_calls += 1
        self.entered.set()
        await self.release.wait()


@pytest.mark.asyncio
async def test_cancelled_gemini_exit_keeps_the_references_for_a_retry():
    client = _make_client()
    context = _GatedGeminiContext()
    client._gemini_context_manager = context
    client._gemini_session = object()
    client.ws = client._gemini_session

    caller = asyncio.create_task(client._close_gemini())
    await asyncio.wait_for(context.entered.wait(), timeout=5)

    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    # The SDK context exit never completed, so the references are the only way
    # back to it — dropping them orphans the session for good.
    assert client._gemini_context_manager is context
    assert client._gemini_session is not None

    context.release.set()
    await asyncio.wait_for(client._close_gemini(), timeout=5)

    assert context.exit_calls == 2
    assert client._gemini_context_manager is None
    assert client._gemini_session is None
    assert client.ws is None


@pytest.mark.asyncio
async def test_failing_gemini_exit_still_drops_the_references():
    """A raised (non-cancel) exit ran to its own conclusion; the SDK has no
    second attempt to offer, so the pre-existing behaviour stands."""
    client = _make_client()

    class _RaisingContext:
        async def __aexit__(self, *exc_info):
            raise RuntimeError("sdk exit failed")

    client._gemini_context_manager = _RaisingContext()
    client._gemini_session = object()

    await client._close_gemini()

    assert client._gemini_context_manager is None
    assert client._gemini_session is None
    assert client.ws is None


# ── Pending hot-swap session ─────────────────────────────────────────


class _GatedSession:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.close_calls = 0
        self.closed = False

    async def close(self):
        self.close_calls += 1
        self.entered.set()
        await self.release.wait()
        self.closed = True


def _make_prep_manager():
    mgr = object.__new__(core_module.LLMSessionManager)
    mgr.pending_session = None
    mgr._pending_session_close_tasks = set()
    mgr.background_preparation_task = None
    mgr.final_swap_task = None
    mgr.is_preparing_new_session = False
    mgr._require_context_append_current_delivery = False
    mgr.summary_triggered_time = None
    mgr.initial_cache_snapshot_len = 0
    mgr.initial_next_session_context_snapshot_len = 0
    mgr.message_cache_for_new_session = []
    mgr.pending_session_warmed_up_event = None
    mgr.pending_session_final_prime_complete_event = None
    mgr.pending_use_tts = None
    return mgr


async def _prep_task_shaped_like_production(mgr):
    """Same shape as ``_background_prepare_pending_session``: park, and clean
    the pending session up from the CancelledError handler."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await mgr._cleanup_pending_session_resources()
        raise


@pytest.mark.asyncio
async def test_second_cancel_of_the_cleanup_caller_does_not_abandon_the_close():
    mgr = _make_prep_manager()
    session = _GatedSession()
    mgr.pending_session = session

    prep = asyncio.create_task(_prep_task_shaped_like_production(mgr))
    await _settle()

    prep.cancel()
    await asyncio.wait_for(session.entered.wait(), timeout=5)
    # What _reset_preparation_state's expiring 2s wait does to the very task
    # that is running the cleanup.
    prep.cancel()
    await _settle()
    assert prep.done()

    session.release.set()
    await _settle()
    await _settle()

    assert session.closed, "the close lost its owner when its caller was cancelled"
    assert session.close_calls == 1
    assert mgr.pending_session is None


@pytest.mark.asyncio
async def test_reset_preparation_state_timeout_does_not_abandon_the_close():
    """Production topology: the real reset, the real 2s cap, a close that
    outlives it. The cap must bound only how long the reset waits."""
    mgr = _make_prep_manager()
    session = _GatedSession()
    mgr.pending_session = session

    prep = asyncio.create_task(_prep_task_shaped_like_production(mgr))
    mgr.background_preparation_task = prep
    await _settle()

    await asyncio.wait_for(mgr._reset_preparation_state(), timeout=10)

    assert mgr.background_preparation_task is None
    assert session.closed is False, "fixture check: the close must still be in flight"

    session.release.set()
    await _settle()
    await _settle()

    assert session.closed
    assert session.close_calls == 1
