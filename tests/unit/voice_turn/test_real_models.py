"""Opt-in checks against the pinned ONNX assets prepared from manifest.json."""

from pathlib import Path

import numpy as np
import pytest

from main_logic.voice_turn.asset_manifest import AssetManifestError, resolve_verified_assets
from main_logic.voice_turn.silero_vad import SileroVad
from main_logic.voice_turn.smart_turn_v3 import SmartTurnV3


try:
    _ASSET_DIR, _, _ = resolve_verified_assets(("silero_vad.onnx", "smart_turn_v3.onnx"))
except AssetManifestError:
    _ASSET_DIR = None

needs_models = pytest.mark.skipif(_ASSET_DIR is None, reason="run prepare_voice_turn_assets.py")


@needs_models
def test_real_silero_model_rejects_one_second_of_silence():
    runtime = SileroVad(enabled=True, asset_dir=Path(_ASSET_DIR))
    assert runtime.load()
    probabilities = runtime.process_pcm16(np.zeros(16_000, dtype=np.int16).tobytes())
    assert len(probabilities) == 31
    assert max(probabilities) < 0.05
    runtime.close()


@needs_models
def test_real_smart_turn_model_matches_reviewed_golden_outputs():
    runtime = SmartTurnV3(enabled=True, asset_dir=Path(_ASSET_DIR))
    assert runtime.load()
    silence = np.zeros(32_000, dtype=np.float32)
    samples = np.arange(32_000)
    tone = (0.1 * np.sin(2 * np.pi * 220 * samples / 16_000)).astype(np.float32)
    assert runtime.predict_probability(silence) == pytest.approx(0.9870367, abs=1e-4)
    assert runtime.predict_probability(tone) == pytest.approx(0.05968675, abs=1e-4)
    runtime.close()
