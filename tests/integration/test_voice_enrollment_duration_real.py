"""Strict #3347 acceptance: missing native/model assets fail, never skip.

Run explicitly after prepare_voice_turn_assets.py and prepare_speaker_model.py.
The service, normalizer, RNNoise, Silero and CAM++ are production implementations;
only temporary-store key wrapping and runtime activation acknowledgements are
controlled. Synthetic fixtures cannot establish physical-microphone quality.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import wave

import numpy as np
import pytest

from main_logic.asr_client.endpointing.asset_manifest import resolve_verified_assets
from main_logic.asr_client.speaker_shadow.asset_manifest import resolve_verified_campplus_asset
from main_logic.asr_client.speaker_shadow.campplus import CampPlusEmbeddingModel
from main_logic.voice_identity_service.audio_contract import OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID
from main_logic.voice_identity_service.enrollment import (
    SileroEnrollmentSpeechValidator,
    enrollment_audio_diagnostics,
)
from main_logic.voice_identity_service.enrollment_audio import EnrollmentAudioNormalizer
from main_logic.voice_identity_service.preference_store import VoiceIdentityPreferenceStore
from main_logic.voice_identity_service.profile_store import VoiceIdentityProfileStore
from main_logic.voice_identity_service.resource_manager import _check_audio
from main_logic.voice_identity_service.service import VoiceIdentityService
from main_logic.voice_input.suppression import VoiceInputSuppressionController
from tests.unit.voice_identity_service.test_profile_store import _TestKeyProtector


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/voice_identity/issue_3347"
VAD_ASSETS = ROOT / "main_logic/asr_client/endpointing/models"
SPEAKER_ASSETS = ROOT / "main_logic/asr_client/speaker_shadow/models"
HASHES = {
    "rate4": "e5d885c02eb35fd05cecb2ee88828e105ffafe34ec49eb02d04ffc7574a1fe0c",
    "rate6": "2f312a0db5f30ce47070befbba54002c56114d5be09c549720cbdffe18de933c",
    "different_speaker": "12e73e9f3aa277b621d2a1d107d9ddc5e25669d78668bd70fea6f9fd7400e732",
}


def _samples(name: str) -> np.ndarray:
    path = FIXTURES / f"{name}.wav"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == HASHES[name]
    with wave.open(str(path), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 48_000)
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").copy()


def _upload(name: str, *, gain: float = 1.0, seconds: int = 3) -> bytes:
    captured = _samples(name)
    received = min(len(captured), seconds * 48_000)
    assert received // 480 * 480 >= 72_000
    output = np.zeros(seconds * 48_000, dtype="<i2")
    output[:received] = np.rint(captured[:received].astype(np.float64) * gain).clip(-32768, 32767).astype("<i2")
    return output.tobytes()


@pytest.fixture(scope="module", autouse=True)
def _require_pinned_resources():
    # Resolve the explicit checkout assets; do not silently use an unrelated cache.
    resolve_verified_assets(("silero_vad.onnx",), override=VAD_ASSETS)
    resolve_verified_campplus_asset(SPEAKER_ASSETS)


@pytest.mark.asyncio
@pytest.mark.parametrize("nr_enabled", [True, False])
@pytest.mark.parametrize("name", ["rate4", "rate6"])
@pytest.mark.parametrize("gain", [0.25, 0.5, 1.0, 2.0])
async def test_short_sentence_reaches_real_speaker_embedding(name, gain, nr_enabled):
    raw = _upload(name, gain=gain)
    normalized = await EnrollmentAudioNormalizer(nr_enabled=nr_enabled).normalize(
        raw, sample_rate_hz=48_000, target_samples=48_000,
    )
    validator = SileroEnrollmentSpeechValidator(asset_dir=VAD_ASSETS)
    model = CampPlusEmbeddingModel(asset_dir=SPEAKER_ASSETS)
    try:
        assert await validator.load(), "Silero must be prepared, not skipped"
        result = await validator.validate_pcm16(normalized)
        assert result.active_window_count >= 47
        diagnostics = enrollment_audio_diagnostics(normalized)
        if gain <= 0.5 or (name == "rate6" and gain == 1.0):
            # Counterexample: these accepted clips failed the old RMS gate.
            assert diagnostics["active_seconds"] < 1.5
        assert model.load(), "CAM++ must be prepared, not skipped"
        embedding = model.embedding_from_pcm16(normalized, sample_rate_hz=16_000)
        try:
            assert embedding.shape == (192,)
            assert np.isfinite(embedding).all()
            assert np.linalg.norm(embedding) == pytest.approx(1.0, abs=1e-5)
        finally:
            embedding.fill(0)
    finally:
        model.close()
        await validator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("nr_enabled", [True, False])
async def test_trial_uses_real_shared_validator_for_both_short_sentences(nr_enabled):
    for name in ("rate4", "rate6"):
        result = await _check_audio(_upload(name, gain=0.25), nr_enabled)
        assert result["accepted"] is True
        assert result["reason"] is None
        assert result["diagnostics"]["active_seconds"] < 1.5


@pytest.mark.asyncio
@pytest.mark.parametrize("nr_enabled", [True, False])
@pytest.mark.parametrize("kind", ["silence", "tone", "noise", "one_second_padded"])
async def test_real_silero_still_rejects_non_speech_and_padded_short_speech(kind, nr_enabled):
    samples = np.zeros(144_000, dtype="<i2")
    if kind == "tone":
        samples[:] = np.rint(3276 * np.sin(2 * np.pi * 220 * np.arange(len(samples)) / 48_000))
    elif kind == "noise":
        samples[:] = np.random.default_rng(3347).integers(-3000, 3001, len(samples), dtype=np.int16)
    elif kind == "one_second_padded":
        samples[:48_000] = _samples("rate4")[:48_000]
    result = await _check_audio(samples.tobytes(), nr_enabled)
    assert result["accepted"] is False
    assert result["reason"] == "no_speech_detected"


class _RecordingCampPlus(CampPlusEmbeddingModel):
    def __init__(self):
        super().__init__(asset_dir=SPEAKER_ASSETS)
        self.input_lengths: list[int] = []

    def embedding_from_pcm16(self, pcm16, *, sample_rate_hz):
        self.input_lengths.append(len(pcm16))
        return super().embedding_from_pcm16(pcm16, sample_rate_hz=sample_rate_hz)


@pytest.mark.asyncio
@pytest.mark.parametrize("nr_enabled", [True, False])
@pytest.mark.parametrize("holdout", ["rate6", "different_speaker"])
async def test_real_four_segment_service_keeps_speaker_gate(tmp_path, nr_enabled, holdout):
    model = _RecordingCampPlus()
    store = VoiceIdentityProfileStore(tmp_path / "profile", key_protector=_TestKeyProtector())

    async def acknowledge(*_args, **_kwargs):
        return True

    service = VoiceIdentityService(
        store,
        VoiceIdentityPreferenceStore(tmp_path / "preference"),
        VoiceInputSuppressionController(acknowledge, acknowledge, default_ttl_seconds=45, hard_ttl_seconds=45),
        lambda: model,
        acknowledge,
        model_timeout_seconds=20,
        enrollment_ttl_seconds=45,
        speech_validator_factory=lambda: SileroEnrollmentSpeechValidator(asset_dir=VAD_ASSETS),
        enrollment_noise_reduction_enabled=nr_enabled,
    )
    try:
        await service.initialize()
        session = await service.start_enrollment()
        for index, gain in enumerate((0.25, 0.5, 1.0), 1):
            status = await service.submit_enrollment_segment(
                session.enrollment_id, "fixed-fixture-owner", index,
                _upload("rate4", gain=gain), sample_rate_hz=48_000,
                audio_contract_id=OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
            )
            assert status.enrollment.next_segment_index == index + 1
        assert status.enrollment.phase == "verifying"
        assert await store.aload() is None
        status = await service.submit_enrollment_segment(
            session.enrollment_id, "fixed-fixture-owner", 4,
            _upload(holdout, gain=0.5, seconds=5), sample_rate_hz=48_000,
            audio_contract_id=OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
        )
        assert model.input_lengths == [96_000, 96_000, 96_000, 48_000, 96_000, 160_000]
        assert status.verification is not None
        assert status.verification.passed is (holdout == "rate6")
        saved = await store.aload()
        try:
            assert (saved is not None) is (holdout == "rate6")
        finally:
            if saved is not None:
                saved.close()
    finally:
        await service.close()
