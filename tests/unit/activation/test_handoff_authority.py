from __future__ import annotations
import asyncio
import pytest
from main_logic.voice_input.activation import ActivationState
from tests.support.asr_fakes import _Runtime

from tests.support.activation_handoff_fakes import (
    _Factory,
    _Session,
    _until,
)

from tests.unit.activation._scenarios import (
    _harness,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_waiting_handoff_cannot_grant_short_speech_authority(route):
    async with _harness(route, active=False) as h:
        generation = h.activation.generation
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        await h.feed(3_000)
        await h.promote(ticket, target)
        await h.feed(3_001)
        assert h.activation.state is ActivationState.WAITING
        assert h.activation.generation == generation
        assert len(h.factory.runtimes) == 1
        assert h.factory.scorers[0].calls == 0
        assert h.deliveries == []


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_idle_expiry_during_handoff_keeps_prior_authorized_output_only(route):
    async with _harness(route) as h:
        runtime = h.activation
        deadline = runtime.idle_deadline
        assert deadline is not None
        h.clock.value = deadline - 0.1
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        original = h.pcm.copy()
        authorized = await h.feed(3_000, voice=False)
        assert runtime.idle_deadline == deadline
        h.clock.value = deadline
        await runtime.tick()
        assert runtime.state is ActivationState.WAITING
        assert runtime.pending_output_bytes == len(authorized)
        await h.promote(ticket, target)
        await _until(lambda: h.pcm == original + [authorized])
        assert runtime.state is ActivationState.WAITING
        h.clock.value += 0.1
        await h.feed(3_001)
        assert h.pcm == original + [authorized]
        assert runtime.state is ActivationState.WAITING
        assert h.factory.scorers[0].calls == 1


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_session_replacement_without_ticket_still_retires_authority(route):
    async with _harness(route) as h:
        old_runtime = h.activation
        original = h.pcm.copy()
        h.manager.session = h.session("unapproved")
        await h.manager._reconcile_independent_asr_after_core_change()
        await h.feed(3_000)
        assert h.pcm == original
        assert h.manager._voice_session_activation_runtime is not old_runtime
        assert (
            h.manager._voice_session_activation_runtime.state
            is not ActivationState.ACTIVE
        )
        await _until(lambda: h.factory.scorers[0].closed)


async def test_disabled_activation_does_not_create_handoff_authority():
    manager = _Runtime()
    manager.session = _Session("source", [])
    assert await manager._begin_voice_activation_handoff(_Session("target", [])) is None
    assert manager._voice_session_activation_runtime is None


@pytest.mark.parametrize("route", ["native", "independent"])
@pytest.mark.parametrize(
    "attribute",
    [
        "_voice_lease_generation",
        "_audio_stream_epoch",
        "_microphone_route_generation",
        "_voice_session_activation_profile_revision",
        "_voice_session_activation_permission_revision",
        "_voice_session_activation_policy_revision",
        "_asr_route_operation_generation",
        "_voice_input_audio_pipeline",
        "_voice_lease_owner",
    ],
)
async def test_changed_authority_or_audio_contract_cannot_commit_ticket(
    route, attribute
):
    async with _harness(route) as h:
        original = h.pcm.copy()
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        await h.feed(3_000)
        previous = getattr(h.manager, attribute)
        replacement = previous + 1 if isinstance(previous, int) else object()
        setattr(h.manager, attribute, replacement)
        assert not h.manager._voice_activation_handoff_is_current(ticket)
        h.manager.session = target
        assert not await h.manager._commit_voice_activation_handoff(ticket)
        assert h.activation.state is ActivationState.UNAVAILABLE
        assert h.pcm == original
        target.stream_audio.assert_not_awaited()


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_duplicate_begin_does_not_replace_the_original_barrier_owner(route):
    async with _harness(route) as h:
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        assert (
            await h.manager._begin_voice_activation_handoff(h.session("other")) is False
        )
        assert h.manager._voice_activation_handoff_is_current(ticket)
        assert h.activation.output_paused
        await h.promote(ticket, target)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_expired_ticket_cannot_install_target_or_extend_idle_deadline(route):
    async with _harness(route) as h:
        target = h.session("target")
        deadline = h.activation.idle_deadline
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        ticket.deadline = asyncio.get_running_loop().time() - 0.1
        assert not h.manager._voice_activation_handoff_is_current(ticket)
        assert not await h.manager._commit_voice_activation_handoff(ticket)
        assert h.activation.idle_deadline == deadline
        assert h.activation.state is ActivationState.ACTIVE
        assert not h.activation.output_paused
        assert h.manager.session is not target


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_late_old_output_cannot_change_new_authority_or_its_pending_audio(route):
    async with _harness(route) as h:
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        old_runtime = h.activation
        source = h.manager.session

        async def late_old_write(value, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # The transport has already accepted the write. Its delayed
                # completion can outlive local cancellation of the old grant.
                cancelled.set()
                await release.wait()
            if route == "native":
                await source._stream(value)
                return None
            return await h.submit(value, **kwargs)

        transport = (
            source.stream_audio if route == "native" else h.manager._asr_runtime.submit
        )
        transport.side_effect = late_old_write
        old_pcm = await h.feed(3_000)
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        old_writer = old_runtime._output_task
        successor_factory = _Factory(h.clock)
        h.manager.session = h.session("successor")
        await h.manager.set_voice_session_activation_factory(
            successor_factory, activation_generation="profile"
        )
        if route == "independent":
            transport.side_effect = h.submit
        ticket = None
        try:
            await asyncio.wait_for(cancelled.wait(), timeout=2.0)
            for index in range(15):
                h.clock.value += 0.1
                await h.feed(4_000 + index)
            successor = successor_factory.runtimes[0]
            await _until(lambda: successor.state is ActivationState.ACTIVE)
            assert successor.generation != old_runtime.generation
            target = h.session("next-target")
            ticket = await h.manager._begin_voice_activation_handoff(target)
            assert ticket is not None and ticket is not False
            pending = await h.feed(5_000)
            new_generation = successor.generation
            deadline = successor.idle_deadline
            status = h.manager._voice_session_activation_status
            status_revision = h.manager._voice_session_activation_status_revision
            release.set()
            await asyncio.wait_for(asyncio.shield(old_writer), timeout=2.0)
            assert successor.state is ActivationState.ACTIVE
            assert successor.generation == new_generation
            assert successor.idle_deadline == deadline
            assert successor.pending_output_bytes == len(pending)
            assert h.manager._voice_session_activation_runtime is successor
            assert not h.manager._voice_session_activation_degraded
            assert h.manager._voice_session_activation_status == status
            assert (
                h.manager._voice_session_activation_status_revision == status_revision
            )
            assert successor_factory.scorers[0].calls == 1
            assert not successor_factory.scorers[0].closed
            await h.promote(ticket, target)
            await _until(lambda: h.pcm[-1] == pending)
            assert h.pcm.count(old_pcm) == h.pcm.count(pending) == 1
        finally:
            release.set()
            if ticket is not None:
                await h.manager._abort_voice_activation_handoff(
                    ticket, reason="test_cleanup"
                )
            await asyncio.wait_for(
                asyncio.gather(old_writer, return_exceptions=True), timeout=2.0
            )
