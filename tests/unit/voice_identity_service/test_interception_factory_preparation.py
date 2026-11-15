"""Preparation rejects incompatible input before any model owner is created."""

from dataclasses import replace

import pytest

from main_logic.voice_identity_service.interception_runtime import PrewireInterceptionFactory
from main_logic.voice_identity_service.prewire_gate.scheduler import ScorerCapabilities
from main_logic.voice_input.interception import InterceptionDecision
from tests.unit.voice_identity_service.test_interception_runtime import _config, _Scorer, _Classifier, _Tse


@pytest.mark.parametrize("changes", [
    {"max_held_pcm_bytes": 3200},
    {"window_samples": 14400, "scorer_capabilities": ScorerCapabilities(16000, 24000, (24000,), "model")},
])
def test_invalid_configuration_cannot_allocate_candidate_or_separator_resources(changes):
    allocations = []

    def allocate(stream):
        allocations.append(stream)
        raise AssertionError("model resources allocated before preparation")

    with pytest.raises(ValueError):
        PrewireInterceptionFactory(
            replace(_config(), **changes), score_backend=_Scorer(), classifier=_Classifier(),
            tse_factory=allocate, candidate_factory=allocate, candidate_source_factory=allocate,
        )
    assert allocations == []


@pytest.mark.asyncio
async def test_legal_preparation_reports_authority_without_allocating_model_until_input():
    allocations = []
    def allocate(stream):
        allocations.append(stream)
        return _Tse()
    prepared = PrewireInterceptionFactory(_config(), score_backend=_Scorer(), classifier=_Classifier(), tse_factory=allocate)
    assert prepared.is_available and not allocations and not prepared._runtimes
    runtime = prepared.create("generation", ingress_token=None)
    assert not allocations
    try:
        results = []
        for count in (1600, 400):
            results.append(await runtime.process(b"\x02\x00" * count, sample_rate_hz=16000,
                generation="generation", ingress_token=None, captured_at=None))
        assert len(allocations) == 1
        assert results[0].decision is InterceptionDecision.PENDING
        assert results[1].decision is InterceptionDecision.KEEP and results[1].pcm16
    finally:
        await runtime.close()
