The `speechbrain_features.npz` fixture contains a frozen SpeechBrain filterbank
and 151x80 expected features for deterministic synthetic PCM. It contains no
recorded speech. Generate its PCM with NumPy `default_rng(428).integers(-15000,
15000, 24000, dtype=np.int16)` and calculate the oracle with SpeechBrain 1.0.3
`Fbank(n_mels=80)` followed by `InputNormalization(norm_type="sentence",
std_norm=False)`, using Torch 2.5.1 CPU. The bank is from the pinned optional ECAPA
export described in the model's third-party notice (Apache-2.0).

`verify_ecapa_compatibility.py` is a developer-only reproducibility check that
accepts the local FireRed-compatible checkpoint, exported ONNX, bank, and upstream
example WAV. It verifies the original checkpoint/asset hashes, compares the bank
exactly, isolates ONNX with official features, then runs the production extractor
on normal, short, quiet, clipped and noise cases. Silence must not be enrolled.
This establishes numerical equivalence, not target-speaker accuracy or the
acceptability of the noisy/clipped input for the upstream enrollment workflow.
