from dataclasses import replace

import pytest

from main_logic.voice_identity_service.candidate_identity import CandidateAudio, CandidateBatch, CandidateBinding
from main_logic.voice_identity_service.candidate_source import CandidateAudioBuffer, CandidateBatchBuffer, CandidateSourceUnavailable
from main_logic.voice_identity_service.prewire_gate.contracts import PrewireIntervalIdentity, PrewireIntervalSpec, PrewireStreamKey, SampleRange

pytestmark = pytest.mark.runtime


def bound():
    return CandidateBinding(PrewireStreamKey("session", 1), "profile", "scorer", "config", "separator", "reference", "a" * 64, "b" * 64)


def interval(start=0, segment=1):
    full = SampleRange(start, start + 8)
    identity = PrewireIntervalIdentity(bound().stream, segment, full, "profile", "scorer", "config")
    return PrewireIntervalSpec(identity, full, SampleRange(start, start + 6), SampleRange(start, start + 4), False, False, False)


def batch(start=0, segment=1, count=2):
    spec = interval(start, segment)
    return CandidateBatch(bound(), spec, tuple(CandidateAudio(bound(), str(i), spec.scoring_range, bytes((i, 0)) * 8) for i in range(count)))


def test_single_source_exact_pcm_slices_and_chunking_invariance():
    whole = CandidateAudioBuffer(bound(), max_buffered_pcm_bytes=64)
    split = CandidateAudioBuffer(bound(), max_buffered_pcm_bytes=64)
    pcm = b"".join(i.to_bytes(2, "little") for i in range(12))
    whole.append_pcm(start_sample=0, pcm16=pcm)
    split.append_pcm(start_sample=0, pcm16=pcm[:6])
    split.append_pcm(start_sample=3, pcm16=pcm[6:])
    for start, segment in ((0, 1), (4, 2)):
        assert whole.batch_for(interval(start, segment), bound()) == split.batch_for(interval(start, segment), bound())
        assert whole.batch_for(interval(start, segment), bound()).candidates[0].pcm16 == pcm[start * 2:(start + 8) * 2]
    whole.discard_before(4)
    assert whole.buffered_pcm_bytes == 16
    with pytest.raises(CandidateSourceUnavailable):
        whole.batch_for(interval(), bound())
    assert whole.batch_for(interval(4, 2), bound()).candidates[0].pcm16 == pcm[8:24]
    whole.clear()
    split.clear()
    assert whole.buffered_pcm_bytes == split.buffered_pcm_bytes == 0


def test_source_missing_pcm_wrong_identity_discontinuity_and_capacity_do_not_fallback():
    source = CandidateAudioBuffer(bound(), max_buffered_pcm_bytes=16)
    with pytest.raises(CandidateSourceUnavailable):
        source.batch_for(interval(), bound())
    with pytest.raises(CandidateSourceUnavailable):
        source.append_pcm(start_sample=1, pcm16=b"\x01\x00" * 8)
    source.append_pcm(start_sample=0, pcm16=b"\x01\x00" * 8)
    with pytest.raises(CandidateSourceUnavailable):
        source.append_pcm(start_sample=8, pcm16=b"\x01\x00")
    with pytest.raises(CandidateSourceUnavailable):
        source.batch_for(interval(), replace(bound(), separator_generation="other"))
    source.clear()
    with pytest.raises(CandidateSourceUnavailable):
        source.append_pcm(start_sample=8, pcm16=b"\x01\x00")


def test_batch_source_handles_zero_and_dual_candidates_then_rejects_late_republication():
    source = CandidateBatchBuffer(bound(), max_buffered_pcm_bytes=32, max_batches=2)
    zero, dual = batch(count=0), batch(4, 2)
    source.push_batch(zero)
    source.push_batch(dual)
    assert source.batch_for(interval(), bound()).candidates == ()
    assert source.batch_for(interval(4, 2), bound()) == dual
    assert source.buffered_pcm_bytes == 32
    with pytest.raises(CandidateSourceUnavailable):
        source.push_batch(zero)
    source.discard_before(4)
    with pytest.raises(CandidateSourceUnavailable):
        source.push_batch(zero)
    source.discard_before(8)
    assert source.buffered_pcm_bytes == 0
    with pytest.raises(CandidateSourceUnavailable):
        source.batch_for(interval(4, 2), bound())
    source.clear()


def test_batch_source_capacity_counts_empty_batches_and_pcm():
    source = CandidateBatchBuffer(bound(), max_buffered_pcm_bytes=16, max_batches=1)
    with pytest.raises(CandidateSourceUnavailable):
        source.push_batch(batch())
    source.push_batch(batch(count=0))
    with pytest.raises(CandidateSourceUnavailable):
        source.push_batch(batch(4, 2, count=0))
    with pytest.raises(CandidateSourceUnavailable):
        source.batch_for(interval(1, 2), bound())
    source.clear()
