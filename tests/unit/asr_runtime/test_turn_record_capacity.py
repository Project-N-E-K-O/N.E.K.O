from unittest.mock import AsyncMock
import pytest
from main_logic.core.multimodal_turn import _MAX_LIVE_TURN_RECORDS
from main_logic.asr_client.lifecycle import VoiceTurnToken
from main_logic.voice_turn.contracts import VoiceTranscriptEvent

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


@pytest.mark.unit
async def test_successor_prepares_do_not_evict_a_still_running_final() -> None:
    """A record is removed by its own dispatch, never by a successor's prepare.

    An accepted final can sit inside handle_input_transcript for a while (bounded
    visual-validation join, provider submit). Meanwhile provider VAD can prepare
    several successor utterances. Evicting the oldest record to make room drops
    the identity that in-flight final needs, so the user's whole sentence is
    neither stored nor submitted.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    running = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=401)
    running_id = f"asr-{running.ingress.session_epoch}-{running.turn_id}"
    runtime._begin_core_multimodal_turn(running_id, running)
    running_record = runtime._core_multimodal_turns[running_id]

    for turn_id in (402, 403, 404):
        token = VoiceTurnToken(
            ingress=runtime._capture_ingress_token(), turn_id=turn_id
        )
        runtime._begin_core_multimodal_turn(
            f"asr-{token.ingress.session_epoch}-{token.turn_id}", token
        )

    assert runtime._core_multimodal_turns.get(running_id) is running_record

    # 它自己的 dispatch 收尾时才该消失。
    runtime._abandon_core_voice_turn(running_id, session_ref=None)
    assert running_id not in runtime._core_multimodal_turns


@pytest.mark.unit
async def test_a_dispatching_record_outlives_the_cap() -> None:
    """The cap must never be the thing that drops an accepted final.

    Raising the limit only moves the failure to a higher overlap count. What
    decides eviction is whether that record's own dispatch has finished -- the
    dict is bounded by removals from each dispatch's own finally, and a run of
    prepares long enough to hit the cap must skip anything mid-dispatch.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    running = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=501)
    running_id = f"asr-{running.ingress.session_epoch}-{running.turn_id}"
    runtime._begin_core_multimodal_turn(running_id, running)
    running_record = runtime._core_multimodal_turns[running_id]
    running_record.dispatch_started = True

    # 远多于上限的后继 prepare。
    for turn_id in range(502, 502 + _MAX_LIVE_TURN_RECORDS * 3):
        token = VoiceTurnToken(
            ingress=runtime._capture_ingress_token(), turn_id=turn_id
        )
        runtime._begin_core_multimodal_turn(
            f"asr-{token.ingress.session_epoch}-{token.turn_id}", token
        )

    assert runtime._core_multimodal_turns.get(running_id) is running_record
    # 没在派发的那些仍然有界。
    assert len(runtime._core_multimodal_turns) <= _MAX_LIVE_TURN_RECORDS


@pytest.mark.unit
async def test_all_records_mid_dispatch_keeps_them_past_the_cap() -> None:
    """When nothing is evictable the cap yields, it does not pick a victim.

    Every record in the dict belongs to a final that is still being dispatched,
    so evicting any of them drops a sentence the user already finished. Going
    over the cap is the lesser failure: unbounded growth would mean a dispatch
    that never returns, which is a different bug and must not be papered over
    by discarding speech.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    for turn_id in range(701, 701 + _MAX_LIVE_TURN_RECORDS + 4):
        token = VoiceTurnToken(
            ingress=runtime._capture_ingress_token(), turn_id=turn_id
        )
        record_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
        runtime._begin_core_multimodal_turn(record_id, token)
        # 每一条都立刻进入派发，于是永远没有可淘汰的记录。
        runtime._core_multimodal_turns[record_id].dispatch_started = True

    assert len(runtime._core_multimodal_turns) == _MAX_LIVE_TURN_RECORDS + 4
    assert all(
        record.dispatch_started
        for record in runtime._core_multimodal_turns.values()
    )
    # 各自的 dispatch 收尾时才回落到界内。
    for record_id in list(runtime._core_multimodal_turns)[:4]:
        runtime._abandon_core_voice_turn(record_id, session_ref=None)
    assert len(runtime._core_multimodal_turns) == _MAX_LIVE_TURN_RECORDS


@pytest.mark.unit
async def test_the_real_dispatch_marks_its_record_before_it_can_be_evicted() -> None:
    """The flag has to be set by the dispatch itself, not only in a test.

    A guard that only checks the eviction predicate passes even when nothing
    ever sets the flag; this drives the actual final through
    ``_dispatch_core_asr_transcript`` and lets a long run of successor prepares
    land while it is suspended.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    runtime._asr_route_mode = "independent"
    runtime.session.submit_external_voice_turn = AsyncMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    accepted = runtime.handle_input_transcript
    seen_mid_dispatch = {}

    async def accept_then_let_successors_pile_up(*args, **kwargs):
        result = await accepted(*args, **kwargs)
        for turn_id_n in range(601, 601 + _MAX_LIVE_TURN_RECORDS * 2):
            successor = VoiceTurnToken(
                ingress=runtime._capture_ingress_token(),
                turn_id=turn_id_n,
            )
            runtime._begin_core_multimodal_turn(
                f"asr-{successor.ingress.session_epoch}-{successor.turn_id}",
                successor,
            )
        seen_mid_dispatch["record"] = runtime._core_multimodal_turns.get(turn_id)
        return result

    runtime.handle_input_transcript = accept_then_let_successors_pile_up

    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(
            turn_token=token,
            provider="openai",
            text="the sentence that must not be dropped",
        )
    )

    assert seen_mid_dispatch["record"] is record
    runtime.session.submit_external_voice_turn.assert_awaited_once()
    # 自己的 finally 摘掉它。
    assert turn_id not in runtime._core_multimodal_turns
