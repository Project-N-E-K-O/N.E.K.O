"""Activation-aware native final-swap lifecycle regression tests."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from main_logic.core._shared import _ORPHAN_SESSION_REAPER_TASKS
from tests.unit.test_hot_swap_cancellation import (
    _FakeSession,
    _drain_task,
    _make_swap_manager,
)


async def _noop(*_args, **_kwargs):
    return None


def _install_activation_handoff(mgr, source, target, events, *, deadline_s=1.0):
    mgr._voice_session_activation_factory = object()
    mgr._asr_route_mode = "native"
    mgr._reconcile_independent_asr_after_core_change = _noop
    source.can_handoff_voice_input = lambda: True
    ticket_box = {}

    async def begin(candidate):
        assert candidate is target
        ticket = SimpleNamespace(
            source_session=source,
            target_session=target,
            deadline=asyncio.get_running_loop().time() + deadline_s,
            irreversible=False,
            consumed=False,
        )
        ticket_box["ticket"] = ticket
        events.append("begin")
        return ticket

    def current(ticket, *, allow_promoted=False):
        if ticket.consumed:
            return False
        expected = (source, target) if allow_promoted else (source,)
        return mgr.session in expected

    def irreversible(ticket):
        assert mgr.session is source
        ticket.irreversible = True
        events.append("irreversible")
        return True

    async def commit(ticket):
        assert ticket.irreversible and mgr.session is target
        events.append("commit")
        ticket.consumed = True
        return True

    async def abort(ticket, *, reason):
        events.append(f"abort:{reason}")
        ticket.consumed = True

    mgr._begin_voice_activation_handoff = begin
    mgr._voice_activation_handoff_is_current = current
    mgr._mark_voice_activation_handoff_irreversible = irreversible
    mgr._commit_voice_activation_handoff = commit
    mgr._abort_voice_activation_handoff = abort
    return ticket_box


@pytest.mark.asyncio
async def test_native_activation_swap_commits_after_close_promote_and_reconcile():
    mgr = _make_swap_manager()
    events = []

    class Source(_FakeSession):
        async def close(self):
            events.append("source-close")
            await super().close()

    source = Source("source")
    target = _FakeSession("target")
    mgr.session = source
    mgr.pending_session = target
    mgr.is_hot_swap_imminent = True
    mgr.is_active = True
    mgr.message_handler_task = None

    async def reconcile():
        assert mgr.session is target
        events.append("reconcile")

    async def flush():
        events.append("flush")

    mgr._reconcile_independent_asr_after_core_change = reconcile
    mgr._flush_hot_swap_audio_cache = flush
    _install_activation_handoff(mgr, source, target, events)
    # Install the observable post-promote actions after the common hook helper.
    mgr._reconcile_independent_asr_after_core_change = reconcile

    try:
        await mgr._perform_final_swap_sequence()
        assert mgr.session is target
        assert source.closed is True
        assert target.closed is False
        assert events.index("begin") < events.index("irreversible")
        assert events.index("irreversible") < events.index("source-close")
        assert events.index("source-close") < events.index("reconcile")
        assert events.index("reconcile") < events.index("commit")
        assert events.index("commit") < events.index("flush")
    finally:
        await _drain_task(mgr.message_handler_task)


@pytest.mark.asyncio
async def test_native_activation_old_close_that_swallows_cancel_cannot_exceed_deadline():
    mgr = _make_swap_manager()
    events = []
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    baseline = set(_ORPHAN_SESSION_REAPER_TASKS)

    class CancellationResistantSource(_FakeSession):
        async def close(self):
            close_started.set()
            while not release_close.is_set():
                try:
                    await release_close.wait()
                except asyncio.CancelledError:
                    continue
            self.closed = True

    source = CancellationResistantSource("source")
    target = _FakeSession("target")
    mgr.session = source
    mgr.pending_session = target
    mgr.is_hot_swap_imminent = True
    mgr.is_active = True
    mgr.message_handler_task = None
    _install_activation_handoff(
        mgr,
        source,
        target,
        events,
        deadline_s=0.03,
    )

    started_at = time.monotonic()
    try:
        await asyncio.wait_for(mgr._perform_final_swap_sequence(), timeout=0.5)
        assert close_started.is_set()
        assert time.monotonic() - started_at < 0.5
        assert mgr.session is source
        assert target.closed is True
        assert "commit" not in events
        assert any(event.startswith("abort:") for event in events)
    finally:
        release_close.set()
        for _ in range(20):
            if set(_ORPHAN_SESSION_REAPER_TASKS) <= baseline:
                break
            await asyncio.sleep(0.01)
        assert set(_ORPHAN_SESSION_REAPER_TASKS) <= baseline
