"""Rejected output selections cannot leave a pending interval in the gate."""

import pytest

from main_logic.voice_identity_service.prewire_gate.contracts import (
    PrewireDecisionState as State,
)
from main_logic.voice_identity_service.prewire_gate.candidate_contracts import (
    CandidateSelection,
)
from main_logic.voice_identity_service.prewire_gate.gate import (
    PrewireGate, PrewireGateError, PrewireGateIdentityError,
)
from main_logic.voice_identity_service.prewire_gate.scheduler import (
    ControlledScoringScheduler, ScoringWindowPlan,
)
from tests.unit.voice_identity_service.test_candidate_identity import (
    binding, spec, Scorer,
)

pytestmark = pytest.mark.runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("state,error", [(State.STALE, PrewireGateIdentityError), (State.UNAVAILABLE, PrewireGateError)])
async def test_rejected_output_selection_is_atomic_and_allows_valid_same_interval(state, error):
    backend = Scorer()
    scheduler = ControlledScoringScheduler(
        backend, window_plan=ScoringWindowPlan((8,)),
        max_outstanding_jobs=2, max_buffered_pcm_bytes=32,
        deadline_seconds=1, close_timeout_seconds=.1,
    )
    gate = PrewireGate(
        scheduler, window_samples=8, step_samples=4, guard_samples=2,
        max_held_pcm_bytes=32, scoring_parameters_digest="a" * 64,
        required_consistent_observations=1,
    )
    bound, interval = binding(), spec()
    gate.open_stream(
        bound.stream, profile_generation=bound.profile_generation,
        model_generation=bound.model_generation, config_generation=bound.config_generation,
    )
    try:
        gate.append_pcm(bound.stream, start_sample=0, pcm16=bytes(16))
        rejected = CandidateSelection(bound, interval, state, "selection_rejected", ())
        with pytest.raises(Exception) as rejection:
            gate.submit_candidate_interval(interval, rejected, expected_binding=bound)
        assert gate.get_interval_record(interval.identity) is None
        assert isinstance(rejection.value, error)
        assert str(rejection.value) == "selection_rejected"
        assert gate.pending_interval_count == 0
        assert gate.held_pcm_bytes == 16
        assert backend.calls == []
        valid = CandidateSelection(bound, interval, State.DROP, "no_output_owner", ())
        plan = gate.submit_candidate_interval(interval, valid, expected_binding=bound)
        assert plan is not None
        assert len(plan.events) == 1 and plan.events[0].kind == "gap"
        gate.claim(plan)
        assert gate.get_interval_record(interval.identity).decision is State.DROP
        assert gate.pending_interval_count == 0
        assert gate.held_pcm_bytes == 8
    finally:
        await gate.close()
