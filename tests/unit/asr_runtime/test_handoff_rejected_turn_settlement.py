"""PR #3089 settlement regressions relocated after PR #3078 split the suite."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from main_logic.asr_client.runtime import _CandidateRejectionSuppression
from tests.support.asr_fakes import _Runtime
from tests.support.core_asr_harness import _start_and_seal_turn

pytestmark = pytest.mark.runtime


async def test_rejected_transcript_submission_settles_the_prepared_turn() -> None:
    """A refused envelope must still release the turn's dispatch pause.

    Submission is where the preparation promise was to be handed to Core, and
    the prepared-turn slot is cleared just before it, so no later teardown can
    name this turn. What settles it is the voice-input registry cancelling the
    route, which abandons the turn through the Core-chat consumer. Should that
    owner stop covering this path, the pause would only be released by its own
    bound, and the warning doing so would describe a leak rather than this
    rejection.
    """

    runtime = _Runtime()
    runtime.session.prepare_external_voice_turn = AsyncMock()
    runtime.session.abandon_external_voice_turn = MagicMock()
    await _start_and_seal_turn(runtime)
    turn_id = runtime.session.prepare_external_voice_turn.await_args.kwargs["turn_id"]

    # The dispatcher refuses the envelope: its contract raises RuntimeError
    # once the slot backing this final is no longer reserved, which an
    # identity barrier can do while the final is still in flight.
    runtime._asr_runtime._asr_transcript_dispatcher.submit = MagicMock(
        side_effect=RuntimeError("ASR_TRANSCRIPT_SLOT_NOT_RESERVED"),
    )

    await runtime._handle_independent_asr_final(
        "hello",
        runtime._asr_session_epoch,
        "qwen",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    runtime.session.abandon_external_voice_turn.assert_called_once_with(turn_id)


async def test_teardown_settles_a_turn_parked_on_the_rejection_suppression() -> None:
    """A teardown must adopt the debt the rejection path left on the suppression.

    The candidate rejection clears the prepared slot inside the final lock and
    parks the promise on its suppression, then releases the lock to await the
    lease, the session close and the detector reset. A teardown landing in that
    window clears the suppression, which makes
    ``_complete_candidate_rejection`` return without notifying Core -- so the
    reset is the only settler left and must be able to name the turn.
    """

    runtime = _Runtime()
    runtime.session.prepare_external_voice_turn = AsyncMock()
    runtime.session.abandon_external_voice_turn = MagicMock()
    await _start_and_seal_turn(runtime)
    turn_id = runtime.session.prepare_external_voice_turn.await_args.kwargs["turn_id"]

    prepared = runtime._asr_prepared_turn_token
    assert prepared is not None

    runtime._asr_prepared_turn_token = None
    runtime._asr_candidate_rejection = _CandidateRejectionSuppression(
        request=MagicMock(),
        turn_token=prepared,
        final_key=MagicMock(),
        lifecycle=runtime._asr_lifecycle,
        detector=runtime._asr_detector,
    )

    runtime._settle_discarded_prepared_turn(runtime._reset_asr_turn_state())
    settling = tuple(runtime._asr_close_tasks)
    assert settling, "the reset found nobody to settle"
    await asyncio.gather(*settling)

    runtime.session.abandon_external_voice_turn.assert_called_once_with(turn_id)
