import inspect
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from main_logic.asr_client.lifecycle import VoiceLifecycleState
from main_logic.voice_turn.contracts import SpeechActivityEvent

from tests.support.core_asr_harness import (
    _ReadyDetector,
    _install_ready_lifecycle,
    _start_and_seal_turn,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.unit_fast]


async def test_final_without_observed_pending_preserves_racing_next_onset() -> None:
    runtime = _Runtime()
    await _start_and_seal_turn(runtime, "gemini")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)

    await runtime._handle_independent_asr_final(
        "first",
        runtime._asr_session_epoch,
        "gemini",
    )

    # A next onset may be admitted after final acceptance but before cleanup.
    # Releasing the completed turn preserves that audio; a full reset loses it.
    detector.reset.assert_not_awaited()
    detector.release_deferred_turn.assert_awaited_once_with()


async def test_candidate_pause_defers_overlap_onset_without_ghost_wake() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    # Local VAD then observes a pause: the provider final that follows may be
    # the current utterance ending, so replaying the onset at that final would
    # wake a ghost turn. The onset converts into a completed-overlap credit
    # that only a later provider endpoint in WARM_IDLE can redeem.
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )
    assert runtime._asr_overlap_onset_token is None
    assert runtime._asr_overlap_completed_turns == 1

    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("hello", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    # No second endpoint arrived, so the credit must not wake anything.
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_turn_prepared is False
    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["hello"]
    assert runtime.handle_new_message.await_count == 1


async def test_completed_overlap_before_delayed_final_delivers_both_finals() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    assert runtime._asr_turn_prepared is True

    # Turn 2 both starts and reaches local silence while turn 1 is still
    # ACTIVE and prepared: its provider endpoint and final are queued in the
    # ordered FIFO behind turn 1's delayed final.
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )

    # Turn 1's ordered callbacks arrive: endpoint immediately before final.
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    # The completed overlap is not replayed yet: only turn 2's own provider
    # endpoint proves a queued turn exists.
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_turn_prepared is False

    # Turn 2's queued endpoint redeems the credit: the turn activates,
    # prepares, and seals so the final right behind it can deliver.
    await runtime._handle_independent_asr_endpoint(epoch)
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    await runtime._handle_independent_asr_final("second", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["first", "second"]
    assert runtime.handle_new_message.await_count == 2
    assert runtime._asr_overlap_completed_turns == 0


async def test_two_completed_overlaps_replay_in_order_after_delayed_final() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    # Turns 2 and 3 each start and reach local silence while turn 1 is still
    # ACTIVE: one completed-overlap credit accumulates per onset+pause cycle.
    for _ in range(2):
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_RESUMED,
            epoch,
        )
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.CANDIDATE_PAUSE,
            epoch,
        )
    assert runtime._asr_overlap_completed_turns == 2

    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    for text in ("second", "third"):
        await runtime._handle_independent_asr_endpoint(epoch)
        await runtime._handle_independent_asr_final(text, epoch, "openai")
        await runtime._wait_asr_transcript_dispatch_idle()

    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["first", "second", "third"]
    assert runtime.handle_new_message.await_count == 3
    assert runtime._asr_overlap_completed_turns == 0


async def test_hard_mute_clears_completed_overlap_credit() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    _install_ready_lifecycle(runtime, "openai")
    runtime._clear_audio_stream_queue = MagicMock()
    runtime.hot_swap_audio_cache = []
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )
    assert runtime._asr_overlap_completed_turns == 1

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            12,
            owner="core",
            hard_muted=True,
            focus_suppressed=False,
        )
        is True
    )
    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    # Hard mute tears the turn state down: neither the onset nor the credit
    # may survive to wake a replacement turn.
    assert runtime._asr_overlap_onset_token is None
    assert runtime._asr_overlap_completed_token is None
    assert runtime._asr_overlap_completed_turns == 0

    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("ghost", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    # Turn 1's preparation before the mute awaited handle_new_message once;
    # the muted ghost final must not deliver a transcript or a second turn.
    assert runtime.handle_input_transcript.await_count == 0
    assert runtime.handle_new_message.await_count == 1


@pytest.mark.unit
def test_overlap_replay_carries_the_real_onset_not_the_replay_instant() -> None:
    """The overlap replay happens long after the user actually resumed speaking.

    A provider-VAD successor utterance can reach Core while the previous turn
    is still ACTIVE; its onset is remembered and replayed only once the delayed
    final arrives. Stamping the replay instant as the onset would classify
    everything captured in between as "after the user spoke", so the successor
    utterance loses the frames it was actually about.
    """
    import inspect

    from main_logic.asr_client import runtime as asr_runtime_module

    source = inspect.getsource(asr_runtime_module).splitlines()

    record = [
        index
        for index, line in enumerate(source)
        if "self._asr_overlap_onset_token = self._asr_current_ingress_token" in line
    ]
    assert record, "overlap onset token is never recorded"
    for index in record:
        window = chr(10).join(source[index : index + 3])
        assert "self._asr_overlap_onset_at = detected_at" in window, (
            f"line {index + 1}: the overlap onset instant must be recorded "
            f"alongside its token, got: {window!r}"
        )

    # Match only SPEECH_RESUMED replay to _handle_independent_asr_activity,
    # never unrelated set literals containing the same enum.
    # Match overlap replay only, driven by code redeeming one completed-overlap credit.
    # The same enum is normally dispatched elsewhere at real event times, correctly using function-entry
    # clocks; those dispatches need not hand over onset times.
    # Overlap has two replay paths: credit redemption and direct replay on provider final.
    # Both must pass real speech onset to confirmation; fixing just one caused the previous omission.
    replay = [
        index
        for index, line in enumerate(source)
        if "await self._handle_independent_asr_activity(" in line
        and "SpeechActivityEvent.SPEECH_RESUMED," in source[index + 1]
    ]
    assert len(replay) >= 2, f"expected both overlap replay paths, got {len(replay)}"
    for index in replay:
        window = chr(10).join(source[max(0, index - 30) : index])
        # Credit redemption uses queue popleft (one timestamp per credit); direct replay uses
        # its captured overlap_onset_at. Both must hand it over.
        assert (
            "self._asr_pending_speech_onset_at = replay_onset_at" in window
            or "self._asr_pending_speech_onset_at = overlap_onset_at" in window
        ), (
            f"line {index + 1}: every overlap replay must hand the recorded "
            f"onset to the confirmation path, got: {window!r}"
        )


@pytest.mark.unit
async def test_live_onset_replay_waits_behind_queued_overlap_credits() -> None:
    """FIFO order decides who gets replayed, not who is newest.

    A completed onset/pause cycle (turn 2) and a still-live onset (turn 3) can
    coexist when turn 1's final is delayed. The provider FIFO still delivers
    turn 2's endpoint/final first, so replaying turn 3 right now hands turn 2's
    endpoint a turn-3 record: turn 2's transcript takes turn 3's visual window,
    and turn 3's own endpoint finds no credit left, dropping its final.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    # Turn 2: a full onset/pause cycle while turn 1 is ACTIVE -> one credit.
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )
    assert runtime._asr_overlap_completed_turns == 1
    # Turn 3: onset only -- the user is still speaking, so it stays in the
    # single slot instead of becoming a credit.
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    assert runtime._asr_overlap_onset_token is not None

    # Turn 1's delayed final. Turn 3 must NOT be replayed here.
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_overlap_completed_turns == 1
    assert runtime._asr_overlap_onset_token is not None

    # Turn 2 redeems its own credit, in its own FIFO slot.
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("second", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()
    # Credits are drained, so turn 3's onset finally gets its replay.
    assert runtime._asr_overlap_completed_turns == 0

    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("third", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["first", "second", "third"]
    assert runtime.handle_new_message.await_count == 3
    assert runtime._asr_overlap_onset_token is None
