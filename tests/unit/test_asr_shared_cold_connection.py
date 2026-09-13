"""Cold connection ownership, independent of prefix buffering."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit.test_core_independent_asr import (
    _Runtime,
    _install_ready_lifecycle,
    _selection,
)

pytestmark = pytest.mark.asyncio


def _connecting_runtime():
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    component = runtime._asr_runtime
    component._asr_session = None
    entered = asyncio.Event()
    release = asyncio.Event()

    async def connect():
        entered.set()
        await release.wait()

    candidate = SimpleNamespace(
        is_ready=True, connect=AsyncMock(side_effect=connect), close=AsyncMock(),
    )
    component._asr_session_factory = MagicMock(return_value=candidate)
    component._asr_transport_selection = _selection("qwen")
    return component, candidate, entered, release


async def test_prewarm_waiter_and_pcm_trigger_share_installed_owner():
    component, candidate, entered, release = _connecting_runtime()
    waiter = asyncio.create_task(component._restart_transport())
    await entered.wait()
    owner = component._asr_transport_task
    deadline = component.transport_connect_deadline
    assert component._ensure_transport_restart_task() is owner
    assert component.transport_connect_deadline == deadline
    release.set()
    await waiter
    candidate.connect.assert_awaited_once()
    candidate.close.assert_not_awaited()
    assert component._asr_session is candidate
    assert component.transport_connect_deadline is None


async def test_cancelling_one_waiter_does_not_cancel_shared_connect():
    component, candidate, entered, release = _connecting_runtime()
    waiter = asyncio.create_task(component._restart_transport())
    await entered.wait()
    owner = component._asr_transport_task
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert owner is not None and not owner.done()
    release.set()
    await component._restart_transport()
    assert component._asr_session is candidate
    candidate.connect.assert_awaited_once()
    candidate.close.assert_not_awaited()


async def test_owner_revocation_does_not_adopt_late_candidate():
    component, candidate, entered, release = _connecting_runtime()
    waiter = asyncio.create_task(component._restart_transport())
    await entered.wait()
    old_owner = component._asr_transport_task
    component._asr_transport_task = None
    component._asr_audio_generation += 1
    assert old_owner is not None
    old_owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert component._asr_session is None
    candidate.close.assert_awaited_once()
    release.set()


async def test_ready_timeout_is_bounded_and_does_not_retry_single_attempt(monkeypatch):
    import main_logic.asr_client.runtime as module

    monkeypatch.setattr(module, "_READY_TIMEOUT_SECONDS", 0.02)
    component, candidate, entered, release = _connecting_runtime()
    await asyncio.wait_for(component._restart_transport(), 1)
    candidate.connect.assert_awaited_once()
    candidate.close.assert_awaited_once()
    assert component._asr_session is None
    assert component.transport_connect_deadline is None


async def test_join_does_not_replace_fixed_budget():
    component, candidate, entered, release = _connecting_runtime()
    waiter = asyncio.create_task(component._restart_transport(max_attempts=1))
    await entered.wait()
    deadline = component.transport_connect_deadline
    assert deadline is not None
    owner = component._asr_transport_task
    assert component._ensure_transport_restart_task(max_attempts=20) is owner
    assert component.transport_connect_deadline == deadline
    release.set()
    await waiter


async def test_late_success_after_cancel_is_closed_and_cannot_be_adopted(monkeypatch):
    import main_logic.asr_client.runtime as module

    monkeypatch.setattr(module, "_READY_TIMEOUT_SECONDS", 0.02)
    component, candidate, entered, release = _connecting_runtime()

    async def stubborn_connect():
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    candidate.connect.side_effect = stubborn_connect
    await asyncio.wait_for(component._restart_transport(), 1)
    assert component._asr_session is None
    candidate.close.assert_awaited_once()
    assert component._asr_connect_cleanup_tasks
    release.set()
    # The tracked late completion must schedule its own close after connecting.
    while component._asr_connect_cleanup_tasks:
        await asyncio.gather(*tuple(component._asr_connect_cleanup_tasks),
                             return_exceptions=True)
    assert candidate.close.await_count == 2
    assert component._asr_session is None


async def test_stubborn_cleanup_stops_policy_retry_and_remains_tracked(monkeypatch):
    import main_logic.asr_client.runtime as module

    monkeypatch.setattr(module, "_CONNECT_CLEANUP_TIMEOUT_SECONDS", 0.02)
    component, candidate, entered, release = _connecting_runtime()
    candidate.connect.side_effect = RuntimeError("connect failed")

    async def stubborn_close():
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    candidate.close.side_effect = stubborn_close
    await asyncio.wait_for(component._restart_transport(max_attempts=3), 1)
    candidate.connect.assert_awaited_once()
    assert component._asr_connect_cleanup_tasks
    assert component._asr_session is None
    release.set()
    await asyncio.gather(*tuple(component._asr_connect_cleanup_tasks),
                         return_exceptions=True)


async def test_ready_candidate_still_joins_owner_until_prefix_preparation_finishes():
    component, candidate, entered, release = _connecting_runtime()

    async def adopted_but_preparing(_operation):
        component._asr_session = candidate
        entered.set()
        await release.wait()

    component._execute_transport_restart = adopted_but_preparing
    first = asyncio.create_task(component._restart_transport())
    await entered.wait()
    second = asyncio.create_task(component._restart_transport())
    await asyncio.sleep(0)
    assert not second.done()
    release.set()
    await asyncio.gather(first, second)


async def test_prewarm_owner_install_during_detector_admission_can_refresh_only_task():
    component, candidate, entered, release = _connecting_runtime()
    before = component._capture_runtime_identity(
        ingress_token=component._asr_current_ingress_token,
    )
    owner = component._ensure_transport_restart_task()
    await entered.wait()
    refreshed = component._refresh_connect_adopted_identity(before)
    assert refreshed is not None
    assert refreshed.transport_task is owner
    assert refreshed.session is before.session
    assert refreshed.transport_generation == before.transport_generation
    component._asr_audio_generation += 1
    assert component._refresh_connect_adopted_identity(before) is None
    release.set()
    await owner
