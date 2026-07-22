"""Opt-in smoke checks against the pinned, locally prepared CAM++ asset."""

from __future__ import annotations

import os
from pathlib import Path

import kaldi_native_fbank as knf
import numpy as np
import pytest

from main_logic.asr_client.campplus import (
    CampPlusAssetError,
    CampPlusEmbeddingModel,
    CampPlusSpeakerShadowFactory,
    build_campplus_speaker_profile,
    compute_campplus_features,
    resolve_verified_campplus_asset,
)


try:
    _MODEL_PATH = resolve_verified_campplus_asset()
except CampPlusAssetError:
    if os.environ.get("NEKO_RELEASE_BUILD") == "1":
        raise
    _MODEL_PATH = None

needs_model = pytest.mark.skipif(
    _MODEL_PATH is None,
    reason="run prepare_speaker_model.py",
)


def _fixed_pcm16() -> bytes:
    samples = np.arange(24_000, dtype=np.float64)
    waveform = (
        0.18 * np.sin(2 * np.pi * 173 * samples / 16_000)
        + 0.07 * np.sin(2 * np.pi * 641 * samples / 16_000)
    )
    return np.rint(waveform * 32767).astype("<i2").tobytes()


def _official_features(pcm16: bytes) -> np.ndarray:
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
    options = knf.FbankOptions()
    options.frame_opts.dither = 0
    options.frame_opts.samp_freq = 16_000
    options.frame_opts.snip_edges = True
    options.mel_opts.num_bins = 80
    fbank = knf.OnlineFbank(options)
    fbank.accept_waveform(16_000, samples)
    fbank.input_finished()
    features = np.stack(
        [fbank.get_frame(index) for index in range(fbank.num_frames_ready)],
        axis=0,
    ).astype(np.float32)
    features -= features.mean(axis=0, keepdims=True)
    return features


@needs_model
def test_real_campplus_outputs_normalized_192_embedding() -> None:
    model = CampPlusEmbeddingModel(asset_dir=Path(_MODEL_PATH).parent)
    assert model.load()
    try:
        embedding = model.embedding_from_pcm16(_fixed_pcm16(), sample_rate_hz=16_000)
    finally:
        model.close()

    assert embedding.shape == (192,)
    assert np.isfinite(embedding).all()
    assert np.linalg.norm(embedding) == pytest.approx(1.0, abs=1e-6)


@needs_model
def test_real_embedding_matches_official_frontend() -> None:
    import onnxruntime as ort

    pcm16 = _fixed_pcm16()
    official = _official_features(pcm16)
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


@needs_model
@pytest.mark.asyncio
async def test_real_model_factory_emits_observation_without_execution_authority() -> None:
    pcm16 = _fixed_pcm16()
    enrollment_model = CampPlusEmbeddingModel(asset_dir=Path(_MODEL_PATH).parent)
    assert enrollment_model.load()
    try:
        profile = build_campplus_speaker_profile(
            enrollment_model,
            [pcm16, pcm16, pcm16],
            sample_rate_hz=16_000,
            profile_revision=9,
        )
    finally:
        enrollment_model.close()

    observations = []
    factory = CampPlusSpeakerShadowFactory(
        enabled=True,
        profile=profile,
        asset_dir=Path(_MODEL_PATH).parent,
        on_observation=observations.append,
    )
    runtime = factory()
    assert runtime is not None
    assert runtime.submit(pcm16, sample_rate_hz=16_000, candidate=(1, 9))
    await runtime.wait_idle()
    await runtime.close()
    metrics = runtime.snapshot()
    profile.close()

    assert len(observations) == 1
    assert observations[0].candidate == (1, 9)
    assert observations[0].similarity >= 0.99999
    assert all(not blocked for _, blocked in observations[0].would_block)
    assert metrics["backend_load_count"] == 1
    assert metrics["backend_inference_count"] == 1
    assert metrics["backend_unload_count"] == 1
    assert metrics["backend_session_rss_delta_bytes"] < 60_000_000
