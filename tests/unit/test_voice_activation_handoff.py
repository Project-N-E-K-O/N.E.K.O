"""Core connection handoff preserves only explicitly transferred voice authority.

These tests use the real Core routing mixin and activation controller/runtime.
Only provider I/O and speaker inference are replaced; no provider models or
physical microphone are required.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.voice_identity_service.activation_runtime import (
    VoiceSessionActivationRuntime,
)
from main_logic.voice_input.activation import ActivationState, VoiceActivationController
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus
from main_logic.asr_client.endpointing.detector_runtime import SmartTurnLease
from main_logic.voice_turn.contracts import VoiceTurnToken
from tests.unit.test_core_independent_asr import _CoreActivationScorer, _Runtime


pytestmark = pytest.mark.asyncio


class _Clock:
    value = 100.0

    def __call__(self) -> float:
        return self.value


class _Factory:
    activation_generation = "profile"

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.runtimes: list[VoiceSessionActivationRuntime] = []
        self.scorers: list[_CoreActivationScorer] = []

    def create(self, generation, output, *, status_callback=None):
        scorer = _CoreActivationScorer()
        runtime = VoiceSessionActivationRuntime(
            generation,
            scorer,
            output,
            controller=VoiceActivationController(clock=self.clock),
            status_callback=status_callback,
        )
        self.scorers.append(scorer)
        self.runtimes.append(runtime)
        return runtime

    def close(self) -> None:
        pass


class _Session:
    def __init__(self, name: str, deliveries: list[tuple[str, bytes]]) -> None:
        self.name = name
        self.can_handoff_voice_input = MagicMock(return_value=True)
        self.stream_audio = AsyncMock(side_effect=self._stream)
        self._deliveries = deliveries

    async def _stream(self, pcm: bytes) -> None:
        self._deliveries.append((self.name, pcm))


@dataclass
class _Harness:
    manager: _Runtime
    clock: _Clock
    factory: _Factory
    route: str
    deliveries: list[tuple[str, bytes]] = field(default_factory=list)

    @property
    def activation(self) -> VoiceSessionActivationRuntime:
        return self.factory.runtimes[0]

    @property
    def pcm(self) -> list[bytes]:
        return [pcm for _, pcm in self.deliveries]

    def session(self, name: str) -> _Session:
        return _Session(name, self.deliveries)

    async def submit(self, frame, **_kwargs) -> AsrSubmitResult:
        self.deliveries.append(("independent", frame.pcm16))
        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

    async def feed(self, marker: int, *, voice: bool = True) -> bytes:
        pcm = marker.to_bytes(2, "little", signed=True) * 1_600
        await self.manager._route_microphone_audio(
            pcm,
            sample_rate_hz=16_000,
            speech_probability=0.9 if voice else 0.0,
            received_at=self.clock.value,
            captured_at=self.clock.value,
        )
        await asyncio.sleep(0)
        return pcm

    async def promote(self, ticket, target: _Session) -> None:
        assert self.manager._voice_activation_handoff_is_current(ticket)
        assert self.manager._mark_voice_activation_handoff_irreversible(ticket)
        self.manager.session = target
        await self.manager._reconcile_independent_asr_after_core_change()
        assert self.manager._voice_activation_handoff_is_current(
            ticket, allow_promoted=True
        )
        assert await self.manager._commit_voice_activation_handoff(ticket)


async def _until(predicate) -> None:
    async with asyncio.timeout(2.0):
        while not predicate():
            await asyncio.sleep(0)


@asynccontextmanager
async def _harness(route: str, *, active: bool = True):
    manager = _Runtime()
    clock = _Clock()
    factory = _Factory(clock)
    harness = _Harness(manager, clock, factory, route)
    manager.is_active = True
    manager.core_api_type = "qwen"
    manager._independent_asr_route_key = "qwen"
    manager.session = harness.session("source")
    manager._set_microphone_route(route)
    manager._asr_runtime.submit = AsyncMock(side_effect=harness.submit)
    await manager.set_voice_session_activation_factory(
        factory, activation_generation="profile"
    )
    try:
        for index in range(15 if active else 1):
            clock.value = 100.0 + index / 10
            await harness.feed(2_000 + index)
        expected_state = ActivationState.ACTIVE if active else ActivationState.WAITING
        await _until(lambda: harness.activation.state is expected_state)
        if active:
            await _until(lambda: len(harness.deliveries) == 15)
        yield harness
    finally:
        await manager.set_voice_session_activation_factory(
            None, activation_generation="test-finished"
        )
        pending = tuple(manager._core_asr_cleanup_tasks)
        if pending:
            async with asyncio.timeout(2.0):
                await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_three_core_handoffs_keep_short_speech_once_without_rescoring(route):
    async with _harness(route) as h:
        runtime = h.activation
        generation = runtime.generation
        idle_task = runtime._idle_task
        receiver = h.manager._asr_runtime
        expected = h.pcm.copy()
        for turn in range(3):
            target = h.session(f"target-{turn}")
            deadline = runtime.idle_deadline
            ticket = await h.manager._begin_voice_activation_handoff(target)
            assert ticket is not None and ticket is not False
            assert runtime.state is ActivationState.ACTIVE
            assert runtime.idle_deadline == deadline
            h.clock.value += 0.1
            short = await h.feed(3_000 + turn * 2)
            assert len(h.pcm) == len(expected)
            assert runtime.pending_output_bytes == len(short)
            expected.append(short)
            deadline_after_voice = runtime.idle_deadline
            await h.promote(ticket, target)
            await _until(lambda: h.pcm == expected)
            assert not await h.manager._commit_voice_activation_handoff(ticket)
            h.clock.value += 0.1
            expected.append(await h.feed(3_001 + turn * 2))
            await _until(lambda: h.pcm == expected)
            assert runtime.idle_deadline >= deadline_after_voice
            assert runtime.generation == generation
            assert h.manager._voice_session_activation_runtime is runtime
            assert h.manager._asr_runtime is receiver
            assert runtime._idle_task is idle_task
            assert runtime.state is ActivationState.ACTIVE
            assert len(h.factory.runtimes) == len(h.factory.scorers) == 1
            assert h.factory.scorers[0].calls == 1
            assert not h.factory.scorers[0].closed
            if route == "native":
                assert h.deliveries[-2:] == [
                    (target.name, pcm) for pcm in expected[-2:]
                ]
        assert h.manager._voice_session_activation_sequence == 21
        assert len(h.pcm) == len(set(h.pcm)) == 21


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
async def test_handoff_settles_inflight_send_before_rebinding_and_never_replays_it(
    route,
):
    async with _harness(route) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        source = h.manager.session
        original = h.pcm.copy()

        async def held_send(value, **kwargs):
            entered.set()
            await release.wait()
            if route == "native":
                await source._stream(value)
                return None
            return await h.submit(value, **kwargs)

        transport = (
            source.stream_audio if route == "native" else h.manager._asr_runtime.submit
        )
        transport.side_effect = held_send
        inflight_pcm = await h.feed(3_000)
        await entered.wait()
        target = h.session("target")
        begin = asyncio.create_task(h.manager._begin_voice_activation_handoff(target))
        try:
            await _until(lambda: h.activation.output_paused)
            assert not begin.done()
            queued_pcm = await h.feed(3_001)
            assert h.manager.session is source
            assert h.pcm == original
            release.set()
            ticket = await asyncio.wait_for(begin, timeout=2.0)
            assert ticket is not None and ticket is not False
            assert h.pcm == original + [inflight_pcm]
            transport.side_effect = source._stream if route == "native" else h.submit
            await h.promote(ticket, target)
            await _until(lambda: h.pcm == original + [inflight_pcm, queued_pcm])
            assert h.activation.state is ActivationState.ACTIVE
            assert h.factory.scorers[0].calls == 1
            assert h.pcm.count(inflight_pcm) == h.pcm.count(queued_pcm) == 1
            if route == "native":
                assert h.deliveries[-2:] == [
                    ("source", inflight_pcm),
                    ("target", queued_pcm),
                ]
        finally:
            release.set()
            if not begin.done():
                begin.cancel()
            await asyncio.gather(begin, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_known_not_sent_frame_resumes_once_on_authorized_target(route):
    async with _harness(route) as h:
        original = h.pcm.copy()
        if route == "native":
            h.manager.session.stream_audio = None
        else:
            h.manager._asr_runtime.submit.side_effect = None
            h.manager._asr_runtime.submit.return_value = AsrSubmitResult(
                AsrSubmitStatus.STALE
            )
        pending = await h.feed(3_000)
        await _until(lambda: not h.activation.output_inflight)
        assert h.pcm == original
        assert h.activation.pending_output_bytes == len(pending)
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        h.manager._asr_runtime.submit.side_effect = h.submit
        await h.promote(ticket, target)
        await _until(lambda: h.pcm == original + [pending])
        assert h.activation.pending_output_bytes == 0
        assert h.pcm.count(pending) == 1


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_unknown_inflight_delivery_rejects_handoff_without_resending(route):
    async with _harness(route) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.pcm.copy()

        async def ambiguous_send(_value, **_kwargs):
            entered.set()
            await release.wait()
            raise RuntimeError("injected uncertain write")

        transport = (
            h.manager.session.stream_audio
            if route == "native"
            else h.manager._asr_runtime.submit
        )
        transport.side_effect = ambiguous_send
        await h.feed(3_000)
        await entered.wait()
        target = h.session("target")
        begin = asyncio.create_task(h.manager._begin_voice_activation_handoff(target))
        try:
            await _until(lambda: h.activation.output_paused)
            await h.feed(3_001)
            release.set()
            assert await asyncio.wait_for(begin, timeout=2.0) is False
            assert h.activation.state is ActivationState.UNAVAILABLE
            await h.feed(3_002)
            assert h.pcm == original
            target.stream_audio.assert_not_awaited()
        finally:
            release.set()
            if not begin.done():
                begin.cancel()
            await asyncio.gather(begin, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_revocation_while_settling_cannot_restore_old_authority(route):
    async with _harness(route) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.pcm.copy()

        async def held_send(_value, **_kwargs):
            entered.set()
            await release.wait()
            return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

        transport = (
            h.manager.session.stream_audio
            if route == "native"
            else h.manager._asr_runtime.submit
        )
        transport.side_effect = held_send
        await h.feed(3_000)
        await entered.wait()
        target = h.session("target")
        begin = asyncio.create_task(h.manager._begin_voice_activation_handoff(target))
        try:
            await _until(lambda: h.activation.output_paused)
            h.manager.require_voice_session_activation(activation_generation="revoked")
            release.set()
            assert await asyncio.wait_for(begin, timeout=2.0) is False
            await h.feed(3_001)
            assert h.manager._voice_session_activation_runtime is None
            assert h.manager._voice_session_activation_degraded
            assert h.pcm == original
            target.stream_audio.assert_not_awaited()
        finally:
            release.set()
            if not begin.done():
                begin.cancel()
            await asyncio.gather(begin, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_abort_before_old_close_restores_source_once_and_stale_ticket_is_harmless(
    route,
):
    async with _harness(route) as h:
        original = h.pcm.copy()
        source = h.manager.session
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        queued = await h.feed(3_000)
        await h.manager._abort_voice_activation_handoff(ticket, reason="prepare_failed")
        await _until(lambda: h.pcm == original + [queued])
        assert h.manager.session is source
        assert h.activation.state is ActivationState.ACTIVE
        assert not await h.manager._commit_voice_activation_handoff(ticket)
        next_ticket = await h.manager._begin_voice_activation_handoff(target)
        assert next_ticket is not None and next_ticket is not False
        await h.manager._abort_voice_activation_handoff(
            ticket, reason="late_old_cleanup"
        )
        assert h.manager._voice_activation_handoff_is_current(next_ticket)
        assert h.activation.output_paused
        await h.promote(next_ticket, target)
        assert h.pcm.count(queued) == 1


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


@pytest.mark.parametrize("capability", [False, None])
async def test_native_unsafe_or_missing_input_boundary_preserves_usable_source(
    capability,
):
    async with _harness("native") as h:
        source = h.manager.session
        source.can_handoff_voice_input = (
            MagicMock(return_value=False) if capability is False else None
        )
        assert (
            await h.manager._begin_voice_activation_handoff(h.session("target"))
            is False
        )
        assert h.manager.session is source
        assert h.activation.state is ActivationState.ACTIVE
        assert not h.activation.output_paused
        short = await h.feed(3_000)
        await _until(lambda: h.pcm[-1] == short)
        assert h.deliveries[-1] == ("source", short)


async def test_disabled_activation_does_not_create_handoff_authority():
    manager = _Runtime()
    manager.session = _Session("source", [])
    assert await manager._begin_voice_activation_handoff(_Session("target", [])) is None
    assert manager._voice_session_activation_runtime is None


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_cancelled_handoff_with_unknown_inflight_write_cannot_resume_old_grant(
    route,
):
    async with _harness(route) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.pcm.copy()

        async def held_send(_value, **_kwargs):
            entered.set()
            await release.wait()
            return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

        transport = (
            h.manager.session.stream_audio
            if route == "native"
            else h.manager._asr_runtime.submit
        )
        transport.side_effect = held_send
        await h.feed(3_000)
        await entered.wait()
        begin = asyncio.create_task(
            h.manager._begin_voice_activation_handoff(h.session("target"))
        )
        try:
            await _until(lambda: h.activation.output_paused)
            await h.feed(3_001)
            begin.cancel()
            with pytest.raises(asyncio.CancelledError):
                await begin
            assert h.activation.state is ActivationState.UNAVAILABLE
            assert h.manager._voice_activation_handoff is None
            release.set()
            await _until(lambda: not h.activation.output_inflight)
            await h.feed(3_002)
            assert h.activation.state is ActivationState.UNAVAILABLE
            assert h.pcm == original
            assert transport.await_count == 16
        finally:
            release.set()
            if not begin.done():
                begin.cancel()
            await asyncio.gather(begin, return_exceptions=True)


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
async def test_irreversible_failure_does_not_restore_source_or_replay_audio(route):
    async with _harness(route) as h:
        original = h.pcm.copy()
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        await h.feed(3_000)
        assert h.manager._mark_voice_activation_handoff_irreversible(ticket)
        await h.manager._abort_voice_activation_handoff(
            ticket, reason="new_session_failed"
        )
        assert h.activation.state is ActivationState.UNAVAILABLE
        assert not await h.manager._commit_voice_activation_handoff(ticket)
        await h.feed(3_001)
        assert h.pcm == original


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
async def test_revocation_while_commit_awaits_writer_lock_is_not_reported_as_success(
    route,
):
    async with _harness(route) as h:
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        h.manager.session = target
        resume = AsyncMock(wraps=h.activation.resume_output)
        h.activation.resume_output = resume
        await h.activation._lock.acquire()
        commit = asyncio.create_task(h.manager._commit_voice_activation_handoff(ticket))
        try:
            await _until(lambda: resume.await_count == 1)
            assert not commit.done()
            h.manager.require_voice_session_activation(activation_generation="revoked")
            h.activation._lock.release()
            assert await asyncio.wait_for(commit, timeout=2.0) is False
            assert h.manager._voice_session_activation_runtime is None
            assert h.manager._voice_session_activation_degraded
            target.stream_audio.assert_not_awaited()
        finally:
            if h.activation._lock.locked():
                h.activation._lock.release()
            if not commit.done():
                commit.cancel()
            await asyncio.gather(commit, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_verifying_handoff_preserves_one_scorer_result_without_early_activation(
    route,
):
    async with _harness(route, active=False) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        scorer = h.factory.scorers[0]
        original_score = scorer.score

        async def held_score(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original_score(*args, **kwargs)

        scorer.score = held_score
        expected = [int(2_000).to_bytes(2, "little", signed=True) * 1_600]
        for index in range(14):
            h.clock.value += 0.1
            expected.append(await h.feed(2_001 + index))
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        target = h.session("target")
        try:
            assert h.activation.state is ActivationState.VERIFYING
            ticket = await h.manager._begin_voice_activation_handoff(target)
            assert ticket is not None and ticket is not False
            await h.promote(ticket, target)
            assert h.activation.state is ActivationState.VERIFYING
            assert h.deliveries == []
            release.set()
            await _until(lambda: len(h.pcm) >= len(expected))
            assert h.pcm == expected
            assert h.activation.state is ActivationState.ACTIVE
            assert len(h.factory.runtimes) == len(h.factory.scorers) == 1
            assert scorer.calls == 1
            assert not scorer.closed
            if route == "native":
                assert {name for name, _pcm in h.deliveries} == {"target"}
        finally:
            release.set()


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_replaying_handoff_keeps_native_utterance_whole_and_independent_pcm_once(
    route,
):
    async with _harness(route, active=False) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        source = h.manager.session

        async def held_first_send(value, **kwargs):
            entered.set()
            await release.wait()
            if route == "native":
                await source._stream(value)
                return None
            return await h.submit(value, **kwargs)

        transport = (
            source.stream_audio if route == "native" else h.manager._asr_runtime.submit
        )
        transport.side_effect = held_first_send
        expected = [int(2_000).to_bytes(2, "little", signed=True) * 1_600]
        for index in range(14):
            h.clock.value += 0.1
            expected.append(await h.feed(2_001 + index))
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        target = h.session("target")
        begin = None
        try:
            assert h.activation.state is ActivationState.REPLAYING
            if route == "native":
                assert await h.manager._begin_voice_activation_handoff(target) is False
                assert not h.activation.output_paused
                release.set()
            else:
                begin = asyncio.create_task(
                    h.manager._begin_voice_activation_handoff(target)
                )
                await _until(lambda: h.activation.output_paused)
                release.set()
                ticket = await asyncio.wait_for(begin, timeout=2.0)
                assert ticket is not None and ticket is not False
                assert h.pcm == expected[:1]
                transport.side_effect = h.submit
                await h.promote(ticket, target)
            await _until(lambda: len(h.pcm) >= len(expected))
            assert h.pcm == expected
            assert h.activation.state is ActivationState.ACTIVE
            assert h.factory.scorers[0].calls == 1
            if route == "native":
                assert h.manager.session is source
                assert {name for name, _pcm in h.deliveries} == {"source"}
                ticket = await h.manager._begin_voice_activation_handoff(target)
                assert ticket is not None and ticket is not False
                await h.promote(ticket, target)
        finally:
            release.set()
            if begin is not None:
                if not begin.done():
                    begin.cancel()
                await asyncio.gather(begin, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_deadline_expiring_inside_resume_revokes_claims_before_queued_audio_leaves(
    route,
):
    async with _harness(route) as h:
        target = h.session("target")
        original = h.pcm.copy()
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        await h.feed(3_000)
        resume = h.activation.resume_output

        async def expire_after_resume(owner):
            resumed = await resume(owner)
            ticket.deadline = asyncio.get_running_loop().time() - 0.1
            return resumed

        h.activation.resume_output = expire_after_resume
        h.manager.session = target
        assert not await h.manager._commit_voice_activation_handoff(ticket)
        await _until(lambda: not h.activation.output_inflight)
        assert h.activation.state is ActivationState.UNAVAILABLE
        assert h.pcm == original
        assert h.manager._voice_session_activation_degraded
        target.stream_audio.assert_not_awaited()


@pytest.mark.parametrize("route", ["native", "independent"])
@pytest.mark.parametrize("outcome", ["unknown", "cancel"])
async def test_uncertain_remote_write_is_retired_before_disabled_audio_can_reuse_source(
    route, outcome
):
    async with _harness(route) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        remote_buffer: list[bytes] = []
        remote_closed = False
        source = h.manager.session
        receiver = h.manager._asr_runtime

        async def close_remote(*_args, **_kwargs):
            nonlocal remote_closed
            remote_closed = True

        source.close = AsyncMock(side_effect=close_remote)
        receiver.abort = AsyncMock(side_effect=close_remote)

        async def uncertain_write(value, **_kwargs):
            # An in-flight write can already own bytes at the receiver even
            # while its local coroutine has not yet learned the outcome.
            assert not remote_closed, "ordinary PCM reused a retired receiver"
            entered.set()
            await release.wait()
            pcm = value if route == "native" else value.pcm16
            remote_buffer.append(pcm)
            if outcome == "unknown":
                raise RuntimeError(
                    "write reached receiver but acknowledgement was lost"
                )
            return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

        transport = source.stream_audio if route == "native" else receiver.submit
        transport.side_effect = uncertain_write
        pending_pcm = await h.feed(3_000)
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        target = h.session("target")
        begin = asyncio.create_task(h.manager._begin_voice_activation_handoff(target))
        try:
            await _until(lambda: h.activation.output_paused)
            if outcome == "cancel":
                begin.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await begin
                release.set()
            else:
                release.set()
                assert await asyncio.wait_for(begin, timeout=2.0) is False
            await _until(lambda: not h.activation.output_inflight)
            assert remote_buffer == [pending_pcm]
            assert h.activation.state is ActivationState.UNAVAILABLE
            await h.manager.set_voice_session_activation_factory(
                None, activation_generation="user-disabled-protection"
            )
            await h.feed(3_001)
            assert remote_closed
            assert remote_buffer == [pending_pcm]
            assert transport.await_count == 16
            target.stream_audio.assert_not_awaited()
            if route == "native":
                source.close.assert_awaited_once()
                assert h.manager.session_closed_by_server
            else:
                receiver.abort.assert_awaited_once()
                assert h.manager._asr_route_mode == "blocked"
        finally:
            release.set()
            if not begin.done():
                begin.cancel()
            await asyncio.gather(begin, return_exceptions=True)


@pytest.mark.parametrize("resist_first_cancel", [False, True])
async def test_independent_handoff_abort_detaches_before_bounded_stuck_provider_close(
    resist_first_cancel,
):
    async with _harness("independent") as h:
        entered, release_write = asyncio.Event(), asyncio.Event()
        closing, release_close = asyncio.Event(), asyncio.Event()
        close_finished = asyncio.Event()
        receiver = h.manager._asr_runtime
        remote = h.session("asr-remote")

        async def stuck_close():
            closing.set()
            try:
                await release_close.wait()
            except asyncio.CancelledError:
                if resist_first_cancel:
                    await release_close.wait()
                raise
            finally:
                close_finished.set()

        async def uncertain_write(_value, **_kwargs):
            entered.set()
            await release_write.wait()
            raise RuntimeError("remote outcome unknown")

        remote.close = AsyncMock(side_effect=stuck_close)
        receiver._asr_session = remote
        detector = SimpleNamespace(release_endpointing=AsyncMock())
        lease = SmartTurnLease(
            VoiceTurnToken(h.manager._capture_ingress_token(), 1), detector, 7
        )
        receiver._asr_smart_turn_lease = lease
        audio_generation = receiver._asr_audio_generation
        receiver.submit.side_effect = uncertain_write
        await h.feed(3_000)
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        begin = asyncio.create_task(
            h.manager._begin_voice_activation_handoff(h.session("target"))
        )
        try:
            await _until(lambda: h.activation.output_paused)
            release_write.set()
            await asyncio.wait_for(closing.wait(), timeout=2.0)
            assert receiver._asr_session is None
            assert receiver._asr_audio_generation > audio_generation
            assert h.manager._asr_route_mode == "blocked"
            done, _ = await asyncio.wait({begin}, timeout=1.5)
            assert begin in done, (
                "handoff waited indefinitely for detached provider close"
            )
            assert begin.result() is False
            assert not release_close.is_set()
            assert h.activation.state is ActivationState.UNAVAILABLE
            await _until(lambda: not receiver._asr_owned_cleanup_tasks)
            assert close_finished.is_set()
            assert receiver._asr_smart_turn_lease is None
            assert lease._released
            detector.release_endpointing.assert_awaited_once_with(lease.token, 7)
        finally:
            release_write.set()
            release_close.set()
            await asyncio.wait_for(
                asyncio.gather(begin, return_exceptions=True), timeout=2.0
            )


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
