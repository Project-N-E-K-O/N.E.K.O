"""Opt-in smoke checks against the pinned, locally prepared CAM++ asset."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CampPlusAssetError,
    resolve_verified_campplus_asset,
)
from main_logic.asr_client.speaker_shadow.campplus import (
    CampPlusBackendFactory,
    CampPlusEmbeddingModel,
    compute_campplus_features,
)
from main_logic.asr_client.speaker_shadow.contracts import (
    SpeakerShadowCandidateKey,
    SpeakerShadowConfig,
)
from main_logic.asr_client.speaker_shadow.runtime import SpeakerShadowRuntime


_OVERRIDE = os.environ.get("NEKO_CAMPPLUS_ASSET_DIR")
try:
    _MODEL_PATH = resolve_verified_campplus_asset(
        Path(_OVERRIDE) if _OVERRIDE else None
    )
except CampPlusAssetError:
    if os.environ.get("NEKO_RELEASE_BUILD") == "1":
        raise
    _MODEL_PATH = None

needs_model = pytest.mark.skipif(
    _MODEL_PATH is None,
    reason="run prepare_speaker_model.py or set NEKO_CAMPPLUS_ASSET_DIR",
)


def _fixed_pcm16() -> bytes:
    samples = np.arange(24_000, dtype=np.float64)
    waveform = (
        0.18 * np.sin(2 * np.pi * 173 * samples / 16_000)
        + 0.07 * np.sin(2 * np.pi * 641 * samples / 16_000)
    )
    return np.rint(waveform * 32767).astype("<i2").tobytes()


@needs_model
def test_real_campplus_outputs_deterministic_normalized_192_embedding() -> None:
    model = CampPlusEmbeddingModel(asset_dir=Path(_MODEL_PATH).parent)
    assert model.load()
    try:
        first = model.embedding_from_pcm16(_fixed_pcm16(), sample_rate_hz=16_000)
        second = model.embedding_from_pcm16(_fixed_pcm16(), sample_rate_hz=16_000)
    finally:
        model.close()

    assert first.shape == second.shape == (192,)
    assert np.isfinite(first).all()
    assert np.linalg.norm(first) == pytest.approx(1.0, abs=1e-6)
    assert float(np.dot(first, second)) >= 0.99999
    first.fill(0)
    second.fill(0)


@needs_model
def test_real_embedding_matches_official_frontend() -> None:
    knf = pytest.importorskip("kaldi_native_fbank")
    import onnxruntime as ort

    pcm16 = _fixed_pcm16()
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
    options = knf.FbankOptions()
    options.frame_opts.dither = 0
    options.frame_opts.samp_freq = 16_000
    options.frame_opts.snip_edges = True
    options.mel_opts.num_bins = 80
    fbank = knf.OnlineFbank(options)
    fbank.accept_waveform(16_000, samples)
    fbank.input_finished()
    official = np.stack(
        [fbank.get_frame(index) for index in range(fbank.num_frames_ready)],
        axis=0,
    ).astype(np.float32)
    official -= official.mean(axis=0, keepdims=True)
    native = compute_campplus_features(pcm16, sample_rate_hz=16_000)
    session = ort.InferenceSession(
        str(_MODEL_PATH),
        providers=["CPUExecutionProvider"],
    )
    official_embedding = session.run(
        ["embedding"], {"x": official[np.newaxis, ...]}
    )[0][0]
    native_embedding = session.run(
        ["embedding"], {"x": native[np.newaxis, ...]}
    )[0][0]
    official_embedding /= np.linalg.norm(official_embedding)
    native_embedding /= np.linalg.norm(native_embedding)

    assert official.shape == native.shape == (148, 80)
    assert np.max(np.abs(official - native)) <= 1e-3
    assert float(np.dot(official_embedding, native_embedding)) >= 0.99999
    official.fill(0)
    native.fill(0)
    official_embedding.fill(0)
    native_embedding.fill(0)


@needs_model
@pytest.mark.asyncio
async def test_real_factory_scores_through_spawned_observation_only_runtime() -> None:
    pcm16 = _fixed_pcm16()
    model = CampPlusEmbeddingModel(asset_dir=Path(_MODEL_PATH).parent)
    assert model.load()
    try:
        reference = model.embedding_from_pcm16(pcm16, sample_rate_hz=16_000)
    finally:
        model.close()
    factory = CampPlusBackendFactory(reference, asset_dir=Path(_MODEL_PATH).parent)
    reference.fill(0)
    observations = []

    async def observe(observation) -> None:
        observations.append(observation)

    runtime = SpeakerShadowRuntime(
        backend_factory=factory,
        config=SpeakerShadowConfig(enabled=True, idle_unload_seconds=10.0),
        on_observation=observe,
    )
    candidate = SpeakerShadowCandidateKey(
        detector_epoch=1,
        shadow_generation=1,
        scope="provider_candidate",
    )
    assert runtime.submit(pcm16, sample_rate_hz=16_000, candidate=candidate)
    assert runtime.finish_candidate(candidate)
    await runtime.wait_idle()
    await runtime.close()

    assert len(observations) == 1
    assert observations[0].candidate == candidate
    assert observations[0].similarity >= 0.99999
    metrics = runtime.snapshot()
    assert metrics["backend_process_count"] == 0
    assert metrics["worker_task_count"] == 0
    assert metrics["callback_task_count"] == 0
