"""What still counts as reply work on an offline session, and who lets go of it.

- The idle session reset ends the session only when no reply work is left:
  not while a reply is guard-paused (``_is_responding`` down, generation
  live), still before its begin, or a cancelled one is finishing its tool
  handler. Ending the session there cuts that reply.
- The startup and cat-return greetings reuse an existing text session even
  while a reply is in progress on it; their claim refuses then. Restarting
  the session (the old auto-start branch) ended it under that reply.
- A reply's owner (Core's ``_ReplyTurn`` and its meta) is dropped by the
  client once the reply is closed, however it was closed.

Real ``OmniOfflineClient`` over a scripted provider where a reply runs.
"""
import asyncio
import gc
import time
import weakref
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.core as core_module
import main_logic.omni_offline_client._lifecycle as offline_lifecycle
from main_logic.core import lifecycle as core_lifecycle
from main_logic.core._shared import IDLE_SESSION_RESET_THRESHOLD_SECONDS
from main_logic.session_state import ProactivePhase
from tests.unit.test_core_game_route_memory_contract import _FakeConnectedWebSocket
from tests.unit.test_offline_reply_ownership import _parked_tool
from tests.unit.test_offline_turn_cancellation_e2e import _client, _text, _tool_calls

pytestmark = pytest.mark.unit

M = core_module.LLMSessionManager


@pytest.fixture(autouse=True)
def _no_token_tracker(monkeypatch):
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())


async def _drain(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


# ── The idle session reset waits for reply work ─────────────────────────────


@pytest.fixture
def idle_reset(monkeypatch):
    """Run the real idle reset loop, ticking on every loop turn, over a
    session whose user has been silent past the threshold."""
    monkeypatch.setattr(core_lifecycle, "IDLE_SESSION_RESET_CHECK_INTERVAL_SECONDS", 0)
    loops = []

    def start(session):
        mgr = SimpleNamespace(
            lanlan_name="L",
            is_active=True,
            session=session,
            _starting_session_count=0,
            _takeover_active=False,
            last_user_activity_time=time.time() - 2 * IDLE_SESSION_RESET_THRESHOLD_SECONDS,
            end_session=AsyncMock(),
        )
        loops.append(asyncio.ensure_future(M._idle_session_reset_loop(mgr)))
        return mgr

    yield start
    for loop in loops:
        loop.cancel()


@pytest.mark.parametrize("work", ["guard_paused", "before_begin", "cancelled_in_its_tool"])
async def test_the_idle_reset_waits_for_reply_work(idle_reset, monkeypatch, work):
    """A proactive reply after a long silence (or a reply cut by a dropped
    voice onset, which records no user activity) is still running: the reset
    must not end the session under it. It does once the work is done."""
    client = _client()
    mgr = idle_reset(client)
    during = []

    async def ticks():
        await _drain()
        during.append(mgr.end_session.await_count)

    if work == "guard_paused":
        async def paused():
            generation = client._active_response_generation
            client._pause_response_generation(generation)
            await ticks()
            client._resume_response_generation(generation)

        client.script = [[_text("前半，"), paused, _text("后半。"), _text("", "stop")]]
        await client.stream_text("hi")
    elif work == "before_begin":
        # A proactive reply with media fits its images before it begins.
        async def fit(images, _budget):
            await ticks()
            return list(images), None

        monkeypatch.setattr(offline_lifecycle, "fit_images_to_turn_budget", fit)
        client.script = [[_text("看到啦。"), _text("", "stop")]]
        await client.prompt_ephemeral("callback", images=["img-b64"])
    else:
        handler, entered, release = _parked_tool()
        client.on_tool_call = handler
        client.script = [[_text("我查一下"), _tool_calls("c1")]]
        cut = asyncio.ensure_future(client.stream_text("Q"))
        await asyncio.wait_for(entered.wait(), 1)
        assert await client.handle_interruption()
        assert not client.has_reply_in_progress()  # only its task is left
        await ticks()
        release.set()
        await cut

    assert during == [0]
    if work != "cancelled_in_its_tool":  # awaiting another task yields
        # Nothing has yielded since the reply call returned: no reset at
        # any point of the reply either.
        assert mgr.end_session.await_count == 0
    await _drain()
    assert mgr.end_session.await_count >= 1  # idle now: the reset goes ahead


async def test_the_idle_reset_still_waits_for_a_realtime_response(idle_reset):
    """A realtime session has only ``_is_responding``; it still holds the reset."""
    session = SimpleNamespace(_is_responding=True)
    mgr = idle_reset(session)
    await _drain()
    assert mgr.end_session.await_count == 0
    session._is_responding = False
    await _drain()
    assert mgr.end_session.await_count >= 1


# ── Greetings never restart a text session that is replying ─────────────────


def _greeting_runtime(monkeypatch, state):
    from tests.unit.test_startup_greeting_delivery import (
        _AntiRepeatDouble,
        _GreetingMemoryClient,
        _GreetingSession,
        _HistoryDouble,
        _make_manager,
        _patch_dependencies,
    )

    session = _GreetingSession(delivered=True)
    _set_reply_state(session, state)
    manager = _make_manager(session)
    manager.websocket = _FakeConnectedWebSocket()
    memory = _GreetingMemoryClient()
    if state == "ends_before_claim":
        real_get = memory.get

        async def get(url, *, timeout):
            if "/followup_topics/" in url:
                session._is_responding = False  # the reply finished meanwhile
            return await real_get(url, timeout=timeout)

        memory.get = get
    _patch_dependencies(
        monkeypatch, memory, _HistoryDouble(), _AntiRepeatDouble(), MagicMock()
    )
    return manager, session


def _set_reply_state(session, state):
    if state in ("streaming", "ends_before_claim"):
        session._is_responding = True
    elif state == "guard_paused":
        session._is_responding = False
        session._active_response_generation = 3


@pytest.mark.parametrize("state", ["streaming", "guard_paused", "ends_before_claim"])
async def test_the_startup_greeting_never_restarts_a_replying_text_session(
    monkeypatch, state,
):
    """start_session would end the live text session and cut its reply. The
    session is reused; the claim refuses while the reply is in progress and
    succeeds on the same session once it has ended."""
    manager, session = _greeting_runtime(monkeypatch, state)
    await M.trigger_greeting(manager)
    await asyncio.gather(*manager._post_commit_tasks)

    manager.start_session.assert_not_awaited()
    assert manager.session is session
    assert len(session.instructions) == (1 if state == "ends_before_claim" else 0)
    assert manager.state.phase is ProactivePhase.IDLE


@pytest.mark.parametrize("state", ["streaming", "guard_paused"])
async def test_the_cat_greeting_never_restarts_a_replying_text_session(state):
    from tests.unit.test_proactive_sm_integration import _FakeOmniOffline, _make_mgr

    session = _FakeOmniOffline(delivered=True)
    _set_reply_state(session, state)
    mgr = _make_mgr(session=session)
    mgr.websocket = _FakeConnectedWebSocket()
    await M.trigger_cat_greeting(mgr, 300, "cat1", False)

    mgr.start_session.assert_not_awaited()
    assert session.called_with == []
    assert mgr.state.phase is ProactivePhase.IDLE


# ── A closed reply's owner is not kept by the client ────────────────────────


class _Owner:
    """Stands in for Core's ``_ReplyTurn`` (weak-referenceable)."""


@pytest.mark.parametrize("closed_by", [
    "own_completion", "interruption", "claimed_completion", "displacement", "session_close",
])
async def test_a_closed_reply_leaves_no_owner_behind(closed_by):
    """However a reply was closed, the client drops the owner it was begun
    with; an interruption or a claim still hands that owner to its closer."""
    client = _client()
    owner = _Owner()
    other = _Owner()  # the displacing reply's, in that case
    handed = []

    async def interrupt():
        taken = await client.handle_interruption()
        handed.append(getattr(taken, "owner", None) is owner)

    if closed_by == "own_completion":
        client.script = [[_text("好。"), _text("", "stop")]]
        await client.stream_text("hi", reply_owner=owner)
        handed.append(True)
    elif closed_by == "interruption":
        client.script = [[_text("前半，"), interrupt, _text("x"), _text("", "stop")]]
        await client.stream_text("hi", reply_owner=owner)
    elif closed_by == "claimed_completion":
        async def cleanup(_seq):
            await interrupt()

        client._notify_reasoning_done = cleanup
        client.script = [[_text("摸头。"), _text("", "stop")]]
        await client.prompt_ephemeral(
            "avatar", completion_mode="response", persist_response=False,
            reply_owner=owner,
        )
    elif closed_by == "displacement":
        began = asyncio.Event()

        async def park():
            await began.wait()

        async def new_begins():
            began.set()
            await _drain()

        def displaced(kind):
            handed.append(getattr(kind, "owner", None) is owner)

        client.on_response_displaced = displaced
        client.script = [
            [_text("摸头，"), park, _text("还要"), _text("", "stop")],
            [new_begins, _text("好"), _text("", "stop")],
        ]
        avatar = asyncio.ensure_future(client.prompt_ephemeral(
            "avatar", completion_mode="response", persist_response=False,
            reply_owner=owner,
        ))
        await _drain()
        await client.stream_text("hi", reply_owner=other)
        await avatar
    else:
        async def session_close_cut():
            client._cancel_response_generation()  # what close() does

        client.script = [[_text("说到一半"), session_close_cut, _text("x"), _text("", "stop")]]
        await client.prompt_ephemeral(
            "greet", completion_mode="response", reply_owner=owner,
        )
        handed.append(True)

    assert handed == [True]
    released = [weakref.ref(owner), weakref.ref(other)]
    del owner, other
    gc.collect()
    assert [ref() for ref in released] == [None, None]
    assert client.is_idle()
