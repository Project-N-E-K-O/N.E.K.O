from unittest.mock import AsyncMock, MagicMock
import pytest
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
@pytest.mark.parametrize("delivery", ["direct_atomic", "handoff_required"])
async def test_ownership_lost_between_the_freeze_check_and_the_provider_call(
    delivery,
) -> None:
    """One check up front is not enough; every await is another window.

    Between the post-freeze check and the actual provider call there is still
    the transcript send, preview restoration, the swap barrier and (on the
    handoff path) preparing a replacement session. A successor prepared in any
    of those windows owns the frames, so the last synchronous point before the
    call has to look again.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    runtime._asr_route_mode = "independent"
    runtime.session.submit_multimodal_turn = AsyncMock()
    runtime.session.submit_external_voice_turn = AsyncMock()
    runtime._handoff_to_offline_vlm_and_submit = AsyncMock(return_value=True)
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]
    assert runtime._stage_independent_visual_frame(
        "frame-of-the-old-turn",
        source="screen",
        request_id="screen-1",
        captured_at=record.started_at,
    )

    def _take_ownership_then_report_delivery():
        # 这一步排在冻结后那次检查**之后**、真正调 provider 之前。
        successor = VoiceTurnToken(
            ingress=runtime._capture_ingress_token(),
            turn_id=token.turn_id + 1,
        )
        runtime._begin_core_multimodal_turn(
            f"asr-{successor.ingress.session_epoch}-{successor.turn_id}",
            successor,
        )
        return delivery

    runtime.session.get_multimodal_turn_delivery = MagicMock(
        side_effect=_take_ownership_then_report_delivery
    )

    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(
            turn_token=token,
            provider="openai",
            text="look here",
        )
    )

    assert record.invalidated.is_set()
    runtime.session.submit_multimodal_turn.assert_not_awaited()
    runtime._handoff_to_offline_vlm_and_submit.assert_not_awaited()
    runtime.session.submit_external_voice_turn.assert_awaited_once()
    assert "look here" in runtime.session.submit_external_voice_turn.await_args.args
