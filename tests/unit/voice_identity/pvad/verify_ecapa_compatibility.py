"""Developer-only official-checkpoint comparison; requires the separate Torch env.

Run with uv run --no-project --python <development-env-python> this_file.py
<local-compat-model-directory> --output <report.json>. No downloads occur here.
The product runtime does not import Torch, Torchaudio, or SpeechBrain.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import sys
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))


def main():
    import onnxruntime as ort
    import torch
    from speechbrain.lobes.features import Fbank
    from speechbrain.lobes.models.ECAPA_TDNN import ECAPA_TDNN
    from speechbrain.processing.features import InputNormalization

    from main_logic.voice_identity.pvad import assets, models

    parser = argparse.ArgumentParser()
    parser.add_argument("resources", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.resources
    checkpoint_hash = "0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2"
    assert hashlib.sha256((root / "embedding_model.ckpt").read_bytes()).hexdigest() == checkpoint_hash
    export = root / "verified-export"
    export.mkdir(exist_ok=True)
    for source_name, asset in zip(("ecapa.onnx", "fbank.bin"), assets.ECAPA_ASSETS):
        shutil.copyfile(root / source_name, export / asset.filename)
        assets.verify_asset(export, asset)
    bank = np.fromfile(root / "fbank.bin", dtype="<f4").reshape(201, 80).T
    model = ECAPA_TDNN(
        input_size=80, channels=[1024, 1024, 1024, 1024, 3072],
        kernel_sizes=[5, 3, 3, 3, 1], dilations=[1, 2, 3, 4, 1],
        attention_channels=128, lin_neurons=192,
    )
    model.load_state_dict(torch.load(root / "embedding_model.ckpt", map_location="cpu", weights_only=True))
    model.eval()
    torch.set_num_threads(1)
    fbank = Fbank(n_mels=80)
    fbank.compute_fbanks.log_mel = False
    with torch.no_grad():
        official_bank = fbank.compute_fbanks(torch.eye(201)[None]).numpy()[0]
    fbank.compute_fbanks.log_mel = True
    assert np.array_equal(bank.T, official_bank)
    normalizer = InputNormalization(norm_type="sentence", std_norm=False)
    session = ort.InferenceSession(str(root / "ecapa.onnx"), models.session_options(),
                                   providers=["CPUExecutionProvider"])
    with wave.open(str(root / "example1.wav")) as handle:
        assert handle.getframerate() == 16000 and handle.getnchannels() == 1
        pcm = handle.readframes(handle.getnframes())
    original = np.frombuffer(pcm, dtype="<i2").astype(np.int32)
    cases = {
        "upstream_example": pcm,
        "speech_first_1_5s": pcm[:48000],
        "speech_first_3s": pcm[:96000],
        "quiet_speech": (original // 32).astype("<i2").tobytes(),
        "clipped_speech": (original * 30).clip(-32768, 32767).astype("<i2").tobytes(),
        "noise_1_5s": np.random.default_rng(42).integers(-3000, 3000, 24000, dtype=np.int16).astype("<i2").tobytes(),
        "noise_5s": np.random.default_rng(43).integers(-3000, 3000, 80000, dtype=np.int16).astype("<i2").tobytes(),
        "silence": bytes(48000),
    }
    results = []
    for name, audio in cases.items():
        signal = torch.from_numpy(np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768)[None]
        with torch.no_grad():
            official_features = normalizer(fbank(signal), torch.ones(1))
            expected = model(official_features, torch.ones(1)).numpy().reshape(-1).astype(np.float64)
        actual_features = models.ecapa_features(audio, bank)
        network_only = session.run(None, {"features": official_features.numpy(),
                                          "feature_lens": np.ones(1, np.float32)})[0].reshape(-1).astype(np.float64)
        expected /= np.linalg.norm(expected)
        network_only /= np.linalg.norm(network_only)
        if name == "silence":
            try:
                models.EcapaExtractor(export).extract(audio)
            except ValueError as error:
                assert str(error) == "insufficient_ecapa_audio"
            else:
                raise AssertionError("silence must not generate an owner reference")
            actual = network_only
        else:
            actual = models.EcapaExtractor(export).extract(audio).astype(np.float64)
            actual /= np.linalg.norm(actual)
        row = {
            "case": name, "samples": len(audio) // 2,
            "input_sha256": hashlib.sha256(audio).hexdigest(),
            "feature_max_abs_error": float(np.max(np.abs(actual_features - official_features.numpy()[0]))),
            "network_only_cosine": float(np.dot(network_only, expected)),
            "embedding_cosine": float(np.dot(actual, expected)),
            "embedding_max_abs_error": float(np.max(np.abs(actual - expected))),
            "reference_admissible": name != "silence",
        }
        assert row["network_only_cosine"] >= 0.99999, row
        assert row["embedding_cosine"] >= 0.99999, row
        assert row["feature_max_abs_error"] < 0.002, row
        results.append(row)
    report = {
        "purpose": "numeric compatibility, not speaker-identification accuracy",
        "python": platform.python_version(),
        "versions": {name: version(name) for name in ("numpy", "torch", "torchaudio", "speechbrain", "onnxruntime")},
        "checkpoint_sha256": checkpoint_hash,
        "assets": {asset.filename: asset.sha256 for asset in assets.ECAPA_ASSETS},
        "resource_revision": assets.ECAPA_RESOURCE_REVISION,
        "preprocessing_revision": assets.ECAPA_PREPROCESSING_REVISION,
        "bank_exact_equal": True, "results": results,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
