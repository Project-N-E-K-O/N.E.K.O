import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from main_logic.voice_turn.activity_evidence import RnnoiseEvidence
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
import main_logic.core.asr_runtime as core_asr_runtime_module
import main_logic.core as core_module

from tests.support.asr_fakes import (
    _Runtime,
)

from tests.support.core_asr_harness import (
    _GateAsyncLock,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_current_audio_pipeline_failure_blocks_once_without_pcm() -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime.is_hot_swap_imminent = False
    runtime.is_flushing_hot_swap_cache = False
    runtime.session.stream_audio = AsyncMock()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    runtime._asr_runtime.abort = AsyncMock()
    runtime._asr_runtime.submit = AsyncMock()
    runtime._voice_input_audio_pipeline.process = AsyncMock(
        side_effect=RuntimeError("soxr failed")
    )
    token = runtime._capture_ingress_token()
    message = {
        "input_type": "audio",
        "sample_rate_hz": 48_000,
        "data": [1] * 480,
    }

    await runtime._process_microphone_stream_data(
        message,
        ingress_token=token,
    )
    await runtime._process_microphone_stream_data(
        message,
        ingress_token=token,
    )

    assert runtime._asr_route_mode == "blocked"
    runtime._asr_runtime.abort.assert_awaited_once_with("audio_preprocessing_failed")
    runtime._asr_runtime.submit.assert_not_awaited()
    runtime.session.stream_audio.assert_not_awaited()
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert statuses == [
        {
            "code": "ASR_AUDIO_PREPROCESSING_FAILED",
            "details": {"provider": "glm", "session_epoch": 0},
        }
    ]


async def test_pipeline_failure_stops_accepting_pcm_at_ingress() -> None:
    """The latch drops frames before the queue, not after the worker dequeues.

    NATIVE route and the fixture's DEFAULT lease state, both load-bearing.
    The independent route reaches `_abort_independent_asr` on the way here and
    that invalidates the voice PCM sync, which closes the lease gate one step
    below the latch; and `_begin_voice_input_connection` also leaves the lease
    in a state that refuses PCM. Either one makes this test pass without ever
    exercising the latch -- the first version of it did exactly that, and the
    mutant survived.

    On the native route with a live lease nothing else stands in the way:
    `_voice_input_accepts_pcm` is lease-only and reads neither the route mode
    nor the latch, so a backpressured status send left the client free to fill
    the bounded queue -- and overflowing it takes the QueueFull path, which
    aborts the run all over again.
    """

    runtime = _Runtime()
    runtime.is_active = True
    runtime._set_microphone_route("native")
    runtime._asr_runtime.abort = AsyncMock()
    status_started = asyncio.Event()
    release_status = asyncio.Event()

    async def backpressured_status(_payload) -> None:
        status_started.set()
        await release_status.wait()

    runtime.send_status = AsyncMock(side_effect=backpressured_status)
    # Keep the worker from draining what the queue accepts, so the depth below
    # measures what ingress ADMITTED rather than what survived a race with it.
    runtime._ensure_audio_stream_worker = lambda: None

    failure = asyncio.create_task(
        runtime._fail_voice_input_pipeline(
            ingress_token=runtime._capture_ingress_token(),
            session_ref=runtime.session,
            audio_epoch=runtime._audio_stream_epoch,
            pipeline_ref=runtime._voice_input_audio_pipeline,
        )
    )
    await asyncio.wait_for(status_started.wait(), 1)

    # Premise: everything DOWNSTREAM of the latch would still take this PCM.
    # Without this the test can pass for the wrong reason.
    assert runtime._voice_input_accepts_pcm() is True

    for _ in range(4):
        await runtime._enqueue_audio_stream_data(
            {"input_type": "audio", "sample_rate_hz": 48_000, "data": [1] * 480}
        )

    assert runtime._audio_stream_queue.qsize() == 0, (
        "PCM arriving during the failure notice must be dropped at ingress "
        "rather than queued behind it"
    )

    release_status.set()
    await asyncio.wait_for(failure, 1)
    assert runtime._asr_route_mode == "blocked"


async def test_stale_audio_epoch_rejects_processed_rnnoise_evidence() -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime.is_hot_swap_imminent = False
    runtime.is_flushing_hot_swap_cache = False
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "qwen"
    evidence = RnnoiseEvidence(True, 3, 0.9, 0.6, 0.2, 0.55)
    processed = ProcessedVoiceFrame(
        pcm16=b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        speech_probability=evidence.peak,
        rnnoise_available=True,
        rnnoise_evidence=evidence,
    )
    runtime._voice_input_audio_pipeline.process = AsyncMock(return_value=processed)
    route_audio = AsyncMock(return_value=True)
    runtime._route_microphone_audio = route_audio

    await runtime._process_microphone_stream_data(
        {
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1] * 160,
        },
        ingress_token=runtime._capture_ingress_token(),
        audio_stream_epoch=runtime._audio_stream_epoch + 1,
    )

    runtime._voice_input_audio_pipeline.process.assert_awaited_once()
    route_audio.assert_not_awaited()


@pytest.mark.parametrize("initial_nr", [True, False])
async def test_start_pipeline_construction_failure_preserves_audio_contract(
    monkeypatch, initial_nr: bool,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime._close_independent_asr = AsyncMock()
    await runtime.apply_voice_input_noise_reduction(initial_nr)
    original = runtime._voice_input_audio_pipeline
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={
            "independentAsrEnabled": False,
            "noiseReductionEnabled": not initial_nr,
        }),
    )
    try:
        with monkeypatch.context() as failing:
            failing.setattr(
                core_asr_runtime_module,
                "VoiceInputAudioPipeline",
                MagicMock(side_effect=RuntimeError("pipeline construction failed")),
            )
            with pytest.raises(RuntimeError, match="pipeline construction failed"):
                await runtime._start_independent_asr_if_enabled("audio")

        assert runtime._voice_input_audio_pipeline is original
        assert runtime._voice_input_noise_reduction_enabled is initial_nr
        assert runtime._asr_route_mode == "blocked"
        frame = await original.process(b"\x01\x00" * 160, sample_rate_hz=16_000)
        assert frame.pcm16 == b"\x01\x00" * 160

        await runtime._start_independent_asr_if_enabled("audio")
        assert runtime._voice_input_audio_pipeline.nr_enabled is not initial_nr
        assert runtime._voice_input_noise_reduction_enabled is not initial_nr
        with pytest.raises(RuntimeError, match="VOICE_AUDIO_PIPELINE_CLOSED"):
            await original.process(b"\x01\x00" * 160, sample_rate_hz=16_000)
    finally:
        await runtime._voice_input_audio_pipeline.close()
        await asyncio.gather(*runtime._core_asr_cleanup_tasks, return_exceptions=True)


async def test_stale_start_waiting_for_pipeline_lock_cannot_replace_successor(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime._close_independent_asr = AsyncMock()
    gate = _GateAsyncLock()
    runtime._voice_input_pipeline_transition_lock = gate
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": False,
                "noiseReductionEnabled": False,
            }
        ),
    )

    starting = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(gate.requested.wait(), 1)
    runtime._begin_asr_route_operation()
    successor_pipeline = SimpleNamespace(
        nr_enabled=True,
        close=AsyncMock(),
    )
    runtime._voice_input_audio_pipeline = successor_pipeline
    gate.release.set()
    await asyncio.wait_for(starting, 1)

    assert runtime._voice_input_audio_pipeline is successor_pipeline
    assert runtime._voice_input_noise_reduction_enabled is True
    successor_pipeline.close.assert_not_awaited()


async def test_stale_close_waiting_for_pipeline_lock_cannot_replace_successor() -> None:
    runtime = _Runtime()
    runtime._asr_runtime.close = AsyncMock()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "old-provider"
    runtime._independent_asr_route_key = "old-core"
    gate = _GateAsyncLock()
    runtime._voice_input_pipeline_transition_lock = gate

    closing = asyncio.create_task(
        runtime._close_independent_asr(next_route_mode="blocked")
    )
    await asyncio.wait_for(gate.requested.wait(), 1)
    runtime._begin_asr_route_operation()
    successor_pipeline = SimpleNamespace(
        nr_enabled=True,
        close=AsyncMock(),
    )
    runtime._voice_input_audio_pipeline = successor_pipeline
    runtime._independent_asr_provider = "new-provider"
    runtime._independent_asr_route_key = "new-core"
    runtime._set_microphone_route("independent")
    gate.release.set()
    await asyncio.wait_for(closing, 1)

    assert runtime._voice_input_audio_pipeline is successor_pipeline
    assert runtime._independent_asr_provider == "new-provider"
    assert runtime._independent_asr_route_key == "new-core"
    assert runtime._asr_route_mode == "independent"
    successor_pipeline.close.assert_not_awaited()
    runtime._asr_runtime.close.assert_not_awaited()


async def test_noise_reduction_replacement_waits_for_pipeline_failure_revoke() -> None:
    class _ObservedAsyncLock:
        def __init__(self) -> None:
            self._lock = asyncio.Lock()
            self._requests = 0
            self.second_request = asyncio.Event()

        async def __aenter__(self):
            self._requests += 1
            if self._requests == 2:
                self.second_request.set()
            await self._lock.acquire()
            return self

        async def __aexit__(self, *_exc_info) -> None:
            self._lock.release()

    runtime = _Runtime()
    runtime.is_active = True
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    assert runtime._begin_voice_input_connection("socket-a") is True
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_synchronized = True
    transition_lock = _ObservedAsyncLock()
    runtime._voice_input_pipeline_transition_lock = transition_lock
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def block_first_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    runtime._asr_runtime.abort = AsyncMock(side_effect=block_first_abort)
    source_pipeline = runtime._voice_input_audio_pipeline
    failure = asyncio.create_task(
        runtime._fail_voice_input_pipeline(
            ingress_token=runtime._capture_ingress_token(),
            session_ref=runtime.session,
            audio_epoch=runtime._audio_stream_epoch,
            pipeline_ref=source_pipeline,
        )
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    replacement = asyncio.create_task(runtime.apply_voice_input_noise_reduction(False))
    await asyncio.wait_for(transition_lock.second_request.wait(), 1)
    release_abort.set()
    failure_result, replacement_result = await asyncio.wait_for(
        asyncio.gather(failure, replacement),
        1,
    )

    assert failure_result is None
    assert replacement_result is True
    assert runtime._asr_route_mode == "blocked"
    assert runtime._voice_lease_connection_id == ""
    assert runtime._voice_lease_owner == "none"
    assert runtime._voice_input_audio_pipeline is not source_pipeline
    assert runtime._voice_input_audio_pipeline.nr_enabled is False
    assert runtime._voice_input_pipeline_failed is False


async def test_pipeline_failure_still_revokes_after_a_bare_pipeline_swap() -> None:
    """A replacement that does not end the route must not skip the revoke.

    The mirror image of the case above. Replacing the pipeline clears
    ``_voice_input_pipeline_failed`` -- that is all a noise-reduction toggle
    does -- but it neither unblocks the route nor revokes the lease, so
    reading it as "someone else owns this failure now" leaves the microphone
    blocked forever with the lease still held. That is the race commit
    94c26715 was written for, and it is why the notify phase fences on the
    failure's own token instead: a replacement that genuinely retires this
    failure (a start, a close) advances the route operation generation, which
    ``_fail_closed_voice_route`` checks on its own.
    """

    runtime = _Runtime()
    runtime.is_active = True
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    assert runtime._begin_voice_input_connection("socket-a") is True
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_synchronized = True
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def delayed_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    runtime._asr_runtime.abort = AsyncMock(side_effect=delayed_abort)
    failure = asyncio.create_task(
        runtime._fail_voice_input_pipeline(
            ingress_token=runtime._capture_ingress_token(),
            session_ref=runtime.session,
            audio_epoch=runtime._audio_stream_epoch,
            pipeline_ref=runtime._voice_input_audio_pipeline,
        )
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    runtime._voice_input_audio_pipeline = SimpleNamespace(
        process=AsyncMock(),
        close=AsyncMock(),
    )
    runtime._voice_input_pipeline_failed = False

    release_abort.set()
    await asyncio.wait_for(failure, 1)

    runtime.send_status.assert_awaited()
    assert runtime._voice_lease_connection_id == ""
    assert runtime._voice_lease_owner == "none"


async def test_pipeline_toggle_during_failure_notify_keeps_ingress_closed() -> None:
    """A toggle must not reopen the microphone while the route is failing.

    `_voice_input_pipeline_failed` is the ingress gate, and ANY pipeline
    replacement clears it -- a noise-reduction toggle included. Once the
    notify phase left the transition lock, such a toggle can land while a
    backpressured status send is still in flight, i.e. while this failure
    still owns a blocked route whose lease has not been revoked. Frames would
    then be parsed, queued and run through the replacement DSP until the route
    discards them: bounded queue refilled, preprocessing burnt, ingress
    backpressure tripped, all during what must stay a fail-closed interval.
    """

    runtime = _Runtime()
    runtime.is_active = True
    runtime.is_hot_swap_imminent = False
    runtime.is_flushing_hot_swap_cache = False
    runtime.session.stream_audio = AsyncMock()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    assert runtime._begin_voice_input_connection("socket-a") is True
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_synchronized = True
    runtime._asr_runtime.abort = AsyncMock()
    runtime._asr_runtime.submit = AsyncMock()
    token = runtime._capture_ingress_token()
    status_started = asyncio.Event()
    release_status = asyncio.Event()

    async def backpressured_status(_payload) -> None:
        status_started.set()
        await release_status.wait()

    runtime.send_status = AsyncMock(side_effect=backpressured_status)
    failure = asyncio.create_task(
        runtime._fail_voice_input_pipeline(
            ingress_token=token,
            session_ref=runtime.session,
            audio_epoch=runtime._audio_stream_epoch,
            pipeline_ref=runtime._voice_input_audio_pipeline,
        )
    )
    await asyncio.wait_for(status_started.wait(), 1)

    assert await asyncio.wait_for(
        runtime.apply_voice_input_noise_reduction(False),
        1,
    ) is True
    # Premise: the toggle really did clear the old gate, so anything still
    # holding ingress closed is the committed failure's own latch.
    assert runtime._voice_input_pipeline_failed is False
    replacement = runtime._voice_input_audio_pipeline
    replacement.process = AsyncMock()

    # Captured HERE, not before the failure. A live client keeps sending PCM
    # with a current token, so `_ingress_token_matches` passes and the frame
    # reaches the DSP without any lease check in between -- which is the whole
    # point. A token snapshotted before the route was blocked would be dropped
    # by the token fence instead, and this test would prove nothing.
    live_token = runtime._capture_ingress_token()
    assert runtime._ingress_token_matches(live_token) is True

    await runtime._process_microphone_stream_data(
        {"input_type": "audio", "sample_rate_hz": 48_000, "data": [1] * 480},
        ingress_token=live_token,
    )

    replacement.process.assert_not_awaited()
    runtime._asr_runtime.submit.assert_not_awaited()
    runtime.session.stream_audio.assert_not_awaited()

    release_status.set()
    await asyncio.wait_for(failure, 1)
    assert runtime._asr_route_mode == "blocked"
    assert runtime._voice_lease_connection_id == ""


async def test_pipeline_failure_from_replaced_connection_is_silent() -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def delayed_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    runtime._asr_runtime.abort = AsyncMock(side_effect=delayed_abort)
    token = runtime._capture_ingress_token()
    failure = asyncio.create_task(
        runtime._fail_voice_input_pipeline(
            ingress_token=token,
            session_ref=runtime.session,
            audio_epoch=runtime._audio_stream_epoch,
            pipeline_ref=runtime._voice_input_audio_pipeline,
        )
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    assert runtime._begin_voice_input_connection("replacement-connection")
    replacement_lease_state = (
        runtime._voice_lease_connection_id,
        runtime._voice_lease_generation,
        runtime._voice_lease_owner,
        runtime._voice_lease_synchronized,
    )
    release_abort.set()
    await asyncio.wait_for(failure, 1)

    assert (
        runtime._voice_lease_connection_id,
        runtime._voice_lease_generation,
        runtime._voice_lease_owner,
        runtime._voice_lease_synchronized,
    ) == replacement_lease_state
    runtime.send_status.assert_not_awaited()
    runtime._asr_runtime.abort.assert_awaited_once_with("audio_preprocessing_failed")


@pytest.mark.parametrize(
    "changed_identity",
    [
        "lease_generation",
        "hard_mute",
        "focus_suppression",
        "game_takeover",
        "route_operation",
        "core_session",
    ],
)
async def test_stale_pipeline_failure_never_reports_to_current_identity(
    changed_identity: str,
) -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def delayed_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    runtime._asr_runtime.abort = AsyncMock(side_effect=delayed_abort)
    token = runtime._capture_ingress_token()
    source_pipeline = runtime._voice_input_audio_pipeline
    failure = asyncio.create_task(
        runtime._fail_voice_input_pipeline(
            ingress_token=token,
            session_ref=runtime.session,
            audio_epoch=runtime._audio_stream_epoch,
            pipeline_ref=source_pipeline,
        )
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    if changed_identity == "lease_generation":
        runtime._voice_lease_generation += 1
    elif changed_identity == "hard_mute":
        runtime._voice_lease_hard_muted = True
        runtime._voice_input_transition_generation += 1
    elif changed_identity == "focus_suppression":
        runtime._voice_lease_focus_suppressed = True
        runtime._voice_input_transition_generation += 1
    elif changed_identity == "game_takeover":
        runtime._voice_lease_owner = "game"
        runtime._voice_input_transition_generation += 1
    elif changed_identity == "route_operation":
        object.__setattr__(
            runtime,
            "_asr_route_operation_generation",
            runtime._asr_route_operation_generation + 1,
        )
    elif changed_identity == "core_session":
        runtime.session = SimpleNamespace(stream_audio=AsyncMock())
    else:
        raise AssertionError(changed_identity)

    release_abort.set()
    await asyncio.wait_for(failure, 1)

    runtime.send_status.assert_not_awaited()
    runtime._asr_runtime.abort.assert_awaited_once_with("audio_preprocessing_failed")


async def test_replaced_audio_pipeline_late_failure_is_silent() -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime.is_hot_swap_imminent = True
    runtime.is_flushing_hot_swap_cache = False
    runtime.session.stream_audio = AsyncMock()
    runtime._set_microphone_route("independent")
    runtime._asr_runtime.abort = AsyncMock()
    started = asyncio.Event()
    release = asyncio.Event()

    async def fail_late(*_args, **_kwargs):
        started.set()
        await release.wait()
        raise RuntimeError("old pipeline failed")

    old_pipeline = runtime._voice_input_audio_pipeline
    old_pipeline.process = AsyncMock(side_effect=fail_late)
    token = runtime._capture_ingress_token()
    processing = asyncio.create_task(
        runtime._process_microphone_stream_data(
            {
                "input_type": "audio",
                "sample_rate_hz": 48_000,
                "data": [1] * 480,
            },
            ingress_token=token,
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    runtime._voice_input_audio_pipeline = type(
        "ReplacementPipeline",
        (),
        {"process": AsyncMock(), "close": AsyncMock()},
    )()
    runtime._set_microphone_route("blocked")
    runtime._set_microphone_route("independent")
    release.set()
    await asyncio.wait_for(processing, 1)

    runtime._asr_runtime.abort.assert_not_awaited()
    runtime.session.stream_audio.assert_not_awaited()
    runtime.send_status.assert_not_awaited()
    assert runtime._voice_input_pipeline_failed is False


async def test_old_pipeline_failure_does_not_report_replacement_provider() -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime.is_hot_swap_imminent = True
    runtime.is_flushing_hot_swap_cache = False
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "provider-a"
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def block_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    runtime._asr_runtime.abort = AsyncMock(side_effect=block_abort)
    old_pipeline = runtime._voice_input_audio_pipeline
    old_pipeline.process = AsyncMock(side_effect=RuntimeError("soxr failed"))
    processing = asyncio.create_task(
        runtime._process_microphone_stream_data(
            {
                "input_type": "audio",
                "sample_rate_hz": 48_000,
                "data": [1] * 480,
            },
            ingress_token=runtime._capture_ingress_token(),
        )
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    runtime._voice_input_audio_pipeline = SimpleNamespace(
        process=AsyncMock(),
        close=AsyncMock(),
    )
    runtime._voice_input_pipeline_failed = False
    runtime._independent_asr_provider = "provider-b"
    runtime._set_microphone_route("independent")
    release_abort.set()
    await asyncio.wait_for(processing, 1)

    assert runtime._asr_route_mode == "independent"
    assert runtime._independent_asr_provider == "provider-b"
    assert runtime._voice_input_pipeline_failed is False
    runtime.send_status.assert_not_awaited()
    runtime._asr_runtime.abort.assert_awaited_once_with("audio_preprocessing_failed")


async def test_settings_read_failure_keeps_noise_reduction_enabled(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(side_effect=RuntimeError("settings unavailable")),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._voice_input_noise_reduction_enabled is True
    assert runtime._voice_input_audio_pipeline.nr_enabled is True
