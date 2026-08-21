from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

import main_logic.asr_client.runtime as runtime_module
from main_logic.asr_client.candidate_control import CandidateRejectionOutcome
from main_logic.asr_client.endpointing.detector import DetectorCandidateKey
from main_logic.asr_client.lifecycle import (
    FinalKey,
    VoiceInputLifecycleController,
    VoiceLifecycleEvent,
    VoiceRouteMode,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.asr_client.runtime import AsrRuntimeCallbacks, IndependentAsrRuntime
from main_logic.asr_client.speaker_shadow.contracts import (
    SpeakerShadowCandidateKey,
)
from main_logic.voice_turn.contracts import VoiceTurnToken


class _RejectionLease:
    def __init__(self, detector: object, turn_token: VoiceTurnToken) -> None:
        self.candidate = DetectorCandidateKey(7, 11)
        self.turn_token = turn_token
        self._detector = detector
        self.commit_calls = 0
        self.commit_result = True

    def belongs_to(self, detector: object) -> bool:
        return detector is self._detector

    def commit(self) -> bool:
        self.commit_calls += 1
        return self.commit_result


class _RejectionDetector:
    def __init__(self) -> None:
        self.lease: _RejectionLease | None = None
        self.prepare_entered = asyncio.Event()
        self.prepare_release = asyncio.Event()
        self.block_prepare = False
        self.reset = AsyncMock()
        self.replace_speaker_verifier = AsyncMock()
        self.close = AsyncMock()

    async def prepare_candidate_rejection(self, _candidate):
        self.prepare_entered.set()
        if self.block_prepare:
            await self.prepare_release.wait()
        return self.lease


def _callbacks(*, abandoned: AsyncMock | None = None) -> AsrRuntimeCallbacks:
    return AsrRuntimeCallbacks(
        display_name=lambda: "candidate-rejection-test",
        on_prepare_turn=AsyncMock(return_value=True),
        on_partial=AsyncMock(),
        on_final=AsyncMock(),
        on_turn_abandoned=abandoned or AsyncMock(),
        on_failure=AsyncMock(),
        on_status=AsyncMock(),
        on_lifecycle=AsyncMock(),
    )


def _install_active_candidate(
    runtime: IndependentAsrRuntime,
    detector: _RejectionDetector,
) -> tuple[SimpleNamespace, VoiceInputLifecycleController, VoiceTurnToken]:
    lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("glm", "manual"),
        shadow_mode=False,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = session
    runtime._asr_provider = "glm"
    runtime._asr_lifecycle = lifecycle
    runtime._asr_detector = detector
    runtime._asr_current_ingress_token = runtime.capture_ingress_token(
        connection_id="connection",
        lease_generation=1,
        route_generation=1,
    )
    turn_token = VoiceTurnToken(
        ingress=runtime._asr_current_ingress_token,
        turn_id=lifecycle.snapshot.turn_id,
    )
    detector.lease = _RejectionLease(detector, turn_token)
    runtime._asr_partial_turn_token = turn_token
    runtime._asr_turn_prepared = True
    runtime._speaker_verifier_activation_generation = "profile-generation"
    assert runtime._asr_audio_dispatcher.activate(turn_token, session, b"") is True
    final_key = FinalKey.from_turn(turn_token)
    assert runtime._asr_transcript_dispatcher.try_reserve(final_key) is True
    runtime._asr_reserved_final_key = final_key
    runtime._ensure_transport_restart_task = MagicMock()
    return session, lifecycle, turn_token


def _shadow_candidate() -> SpeakerShadowCandidateKey:
    return SpeakerShadowCandidateKey(7, 3, "provider_candidate")


def test_rejection_request_outside_event_loop_fails_open() -> None:
    runtime = IndependentAsrRuntime(_callbacks())
    runtime._speaker_verifier_activation_generation = "profile-generation"

    assert not runtime.request_speaker_candidate_rejection(
        _shadow_candidate(),
        activation_generation="profile-generation",
    )


async def _close_dispatchers(runtime: IndependentAsrRuntime) -> None:
    await runtime._asr_audio_dispatcher.close()
    runtime._asr_transcript_dispatcher.invalidate_all()


async def test_verifier_factory_hot_replaces_active_detector_and_closes_old() -> None:
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    runtime._asr_detector = detector
    old_factory = MagicMock()
    old_factory.close = MagicMock()
    runtime._speaker_verifier_factory = old_factory
    runtime._speaker_verifier_activation_generation = "old-profile"
    shadow = SimpleNamespace(close=AsyncMock())
    factory = MagicMock(return_value=shadow)

    updated = await runtime.set_speaker_verifier_factory(
        factory,
        activation_generation="new-profile",
    )

    assert updated is True
    detector.replace_speaker_verifier.assert_awaited_once_with(shadow)
    assert runtime._speaker_verifier_factory is factory
    assert runtime._speaker_verifier_activation_generation == "new-profile"
    old_factory.close.assert_called_once_with()


async def test_verifier_factory_failure_is_detached_and_preserves_activation() -> None:
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    detector.replace_speaker_verifier.side_effect = RuntimeError("swap failed")
    runtime._asr_detector = detector
    old_factory = MagicMock()
    runtime._speaker_verifier_factory = old_factory
    runtime._speaker_verifier_activation_generation = "old-profile"
    shadow = SimpleNamespace(close=AsyncMock())
    factory = MagicMock(return_value=shadow)

    updated = await runtime.set_speaker_verifier_factory(
        factory,
        activation_generation="new-profile",
    )

    assert updated is False
    shadow.close.assert_awaited_once_with()
    assert runtime._speaker_verifier_factory is old_factory
    assert runtime._speaker_verifier_activation_generation == "old-profile"


async def test_verifier_detach_failure_still_revokes_old_activation() -> None:
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    detector.replace_speaker_verifier.side_effect = RuntimeError("detach failed")
    runtime._asr_detector = detector
    old_factory = MagicMock()
    old_factory.close = MagicMock()
    runtime._speaker_verifier_factory = old_factory
    runtime._speaker_verifier_activation_generation = "old-profile"

    updated = await runtime.set_speaker_verifier_factory(
        None,
        activation_generation="revoked-profile",
    )

    assert updated is False
    assert runtime._speaker_verifier_factory is None
    assert runtime._speaker_verifier_activation_generation == "revoked-profile"
    old_factory.close.assert_called_once_with()
    assert not runtime.request_speaker_candidate_rejection(
        _shadow_candidate(),
        activation_generation="old-profile",
    )


async def test_candidate_rejection_applies_and_recovers_next_transport() -> None:
    abandoned = AsyncMock()
    runtime = IndependentAsrRuntime(_callbacks(abandoned=abandoned))
    detector = _RejectionDetector()
    session, _lifecycle, turn_token = _install_active_candidate(runtime, detector)

    outcome = await runtime._reject_speaker_candidate(
        _shadow_candidate(),
        activation_generation="profile-generation",
    )

    assert outcome is CandidateRejectionOutcome.APPLIED
    assert detector.lease is not None and detector.lease.commit_calls == 1
    session.close.assert_awaited_once_with()
    detector.reset.assert_awaited_once_with()
    abandoned.assert_awaited_once_with(turn_token)
    assert runtime._asr_candidate_rejection is None
    runtime._ensure_transport_restart_task.assert_called_once_with()
    await _close_dispatchers(runtime)


@pytest.mark.parametrize("stale_cause", ["final", "provider_endpoint", "profile"])
async def test_candidate_rejection_forwards_when_authority_is_stale(
    stale_cause: str,
) -> None:
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    session, lifecycle, _turn_token = _install_active_candidate(runtime, detector)
    if stale_cause == "final":
        lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    elif stale_cause == "provider_endpoint":
        runtime._asr_provider_candidate_fence = object()
    else:
        runtime._speaker_verifier_activation_generation = "replacement-profile"

    outcome = await runtime._reject_speaker_candidate(
        _shadow_candidate(),
        activation_generation="profile-generation",
    )

    assert outcome is CandidateRejectionOutcome.STALE
    assert detector.lease is not None and detector.lease.commit_calls == 0
    session.close.assert_not_awaited()
    detector.reset.assert_not_awaited()
    await _close_dispatchers(runtime)


@pytest.mark.parametrize("stale_cause", ["transport", "profile"])
async def test_candidate_rejection_rechecks_fences_after_prepare_await(
    stale_cause: str,
) -> None:
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    detector.block_prepare = True
    session, lifecycle, _turn_token = _install_active_candidate(runtime, detector)
    task = asyncio.create_task(
        runtime._reject_speaker_candidate(
            _shadow_candidate(),
            activation_generation="profile-generation",
        )
    )
    await asyncio.wait_for(detector.prepare_entered.wait(), 1)
    if stale_cause == "transport":
        lifecycle.invalidate_transport()
    else:
        runtime._speaker_verifier_activation_generation = "replacement-profile"
    detector.prepare_release.set()

    outcome = await asyncio.wait_for(task, 1)

    assert outcome is CandidateRejectionOutcome.STALE
    assert detector.lease is not None and detector.lease.commit_calls == 0
    session.close.assert_not_awaited()
    await _close_dispatchers(runtime)


async def test_cleanup_failure_keeps_drop_and_watchdog_releases_suppression(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_CANDIDATE_REJECTION_WATCHDOG_SECONDS",
        0.0,
    )
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    detector.reset.side_effect = [RuntimeError("reset failed"), None]
    session, _lifecycle, _turn_token = _install_active_candidate(runtime, detector)
    shadow = SimpleNamespace(close=AsyncMock())
    runtime._speaker_verifier_factory = MagicMock(return_value=shadow)

    outcome = await runtime._reject_speaker_candidate(
        _shadow_candidate(),
        activation_generation="profile-generation",
    )

    async def wait_until_released() -> None:
        while runtime._asr_candidate_rejection is not None:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_released(), 1.0)

    assert outcome is CandidateRejectionOutcome.APPLIED_CLEANUP_DEGRADED
    assert detector.lease is not None and detector.lease.commit_calls == 1
    session.close.assert_awaited_once_with()
    assert detector.replace_speaker_verifier.await_args_list == [
        call(None),
        call(shadow),
    ]
    assert detector.reset.await_count == 2
    assert runtime._asr_candidate_rejection is None
    runtime._ensure_transport_restart_task.assert_called_once_with()
    await _close_dispatchers(runtime)


async def test_rejection_watchdog_retries_verifier_reinstall(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_CANDIDATE_REJECTION_WATCHDOG_SECONDS",
        0.0,
    )
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    first_shadow = SimpleNamespace(close=AsyncMock())
    second_shadow = SimpleNamespace(close=AsyncMock())
    detector.reset.side_effect = [RuntimeError("reset failed"), None]
    detector.replace_speaker_verifier.side_effect = [
        None,
        RuntimeError("reinstall failed"),
        None,
    ]
    session, _lifecycle, _turn_token = _install_active_candidate(runtime, detector)
    runtime._speaker_verifier_factory = MagicMock(
        side_effect=[first_shadow, second_shadow]
    )

    outcome = await runtime._reject_speaker_candidate(
        _shadow_candidate(),
        activation_generation="profile-generation",
    )

    async def wait_until_released() -> None:
        while runtime._asr_candidate_rejection is not None:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_released(), 1.0)
    assert outcome is CandidateRejectionOutcome.APPLIED_CLEANUP_DEGRADED
    assert detector.replace_speaker_verifier.await_args_list == [
        call(None),
        call(first_shadow),
        call(second_shadow),
    ]
    first_shadow.close.assert_awaited_once_with()
    second_shadow.close.assert_not_awaited()
    assert not runtime._speaker_verifier_degraded
    assert runtime._asr_candidate_rejection is None
    session.close.assert_awaited_once_with()
    await _close_dispatchers(runtime)


async def test_rejection_watchdog_bounds_stuck_recovery_and_resumes_asr(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_CANDIDATE_REJECTION_WATCHDOG_SECONDS",
        0.0,
    )
    monkeypatch.setattr(
        runtime_module,
        "_CANDIDATE_REJECTION_RECOVERY_STEP_TIMEOUT_SECONDS",
        0.02,
    )
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    detector.reset.side_effect = [RuntimeError("reset failed"), None]

    async def never_replace(_shadow) -> None:
        await asyncio.Event().wait()

    detector.replace_speaker_verifier.side_effect = never_replace
    _install_active_candidate(runtime, detector)
    runtime._speaker_verifier_factory = MagicMock(
        side_effect=lambda: SimpleNamespace(close=AsyncMock())
    )
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    outcome = await runtime._reject_speaker_candidate(
        _shadow_candidate(),
        activation_generation="profile-generation",
    )

    async def wait_until_released() -> None:
        while runtime._asr_candidate_rejection is not None:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_released(), 0.5)
    assert loop.time() - started_at < 0.2
    assert outcome is CandidateRejectionOutcome.APPLIED_CLEANUP_DEGRADED
    assert runtime._speaker_verifier_degraded
    assert runtime._asr_candidate_rejection is None
    runtime._ensure_transport_restart_task.assert_called_once_with()
    await _close_dispatchers(runtime)


async def test_late_rejection_cleanup_does_not_reset_recovered_detector(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_CANDIDATE_REJECTION_WATCHDOG_SECONDS",
        0.0,
    )
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    session, _lifecycle, _turn_token = _install_active_candidate(runtime, detector)
    close_started = asyncio.Event()
    close_release = asyncio.Event()

    async def blocking_close() -> None:
        close_started.set()
        await close_release.wait()

    session.close = AsyncMock(side_effect=blocking_close)
    rejection_task = asyncio.create_task(
        runtime._reject_speaker_candidate(
            _shadow_candidate(),
            activation_generation="profile-generation",
        )
    )
    await asyncio.wait_for(close_started.wait(), 1.0)

    async def wait_until_recovered() -> None:
        while runtime._asr_candidate_rejection is not None:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_recovered(), 1.0)
    close_release.set()
    outcome = await asyncio.wait_for(rejection_task, 1.0)

    assert outcome is CandidateRejectionOutcome.APPLIED_CLEANUP_DEGRADED
    assert detector.reset.await_count == 1
    runtime._ensure_transport_restart_task.assert_called_once_with()
    await _close_dispatchers(runtime)


async def test_close_cancels_and_joins_owned_rejection_task() -> None:
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    detector.block_prepare = True
    _install_active_candidate(runtime, detector)

    assert runtime.request_speaker_candidate_rejection(
        _shadow_candidate(),
        activation_generation="profile-generation",
    )
    await asyncio.wait_for(detector.prepare_entered.wait(), 1)
    tasks = tuple(runtime._asr_rejection_tasks)

    await asyncio.wait_for(runtime.close(), 1)

    assert tasks and all(task.done() for task in tasks)
    assert runtime._asr_rejection_tasks == set()
    detector.close.assert_awaited_once_with()
