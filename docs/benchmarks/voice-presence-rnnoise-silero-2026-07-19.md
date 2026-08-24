# RNNoise / Silero speech-presence benchmark protocol

This dated page records the reproducible protocol carried forward from the
2026-07-19 experiment. It is not an evergreen performance promise and does not
claim that a new run was performed for this commit.

## Scope

The benchmark asks only whether a clip might contain speech. RNNoise and
Silero are resource-throttling evidence. They cannot publish a logical final,
replace provider endpoint authority, or decide which provider is selected.

The online replay consumes one record per microphone chunk:

- actual RNNoise frame count;
- peak, mean, last, and streaming EMA probabilities;
- a bounded adaptive baseline;
- low-cardinality action/count/disagreement shadow metrics.

It does not claim an online continuous-100 ms RNNoise state. The continuous
100 ms calculation remains an offline candidate experiment so that historical
results can be compared without silently changing production behavior.

## Corpus and split

Real-device evaluation uses a versioned JSON manifest with boolean labels,
anonymous device IDs, scenarios, and optional speech-onset timestamps. Audio
paths are resolved locally and are not written to the report.

Calibration and holdout are split by source group. All clean and SNR variants
from one source stay in the same partition. Positive strata retain locale;
negative strata retain scenario; real-device strata retain device and
scenario. Thresholds are selected from calibration only.

Repository tutorial TTS, synthetic noise, and game sound effects can be used
for offline exploration, but they must not be presented as real-device
evidence. A device-independent publication decision requires a separately
labeled device holdout.

## Runtime paths

- RNNoise uses the real 48 kHz `AudioProcessor` chain and captures its complete
  per-chunk evidence.
- Silero is imported from
  `main_logic.asr_client.endpointing.silero_vad`.
- Model assets default to
  `main_logic/asr_client/endpointing/models`.
- The benchmark does not restore `tools/voice_eval` or a legacy detector shim.

## Report interpretation

Report online and offline results in separate sections. At minimum include
speech recall, negative specificity, balanced accuracy, F1, calibration and
holdout counts, runtime revision, environment, CPU real-time factor, and
observed RSS delta.

A low clip-level false-positive rate is not equivalent to a low number of
false prewarms per idle hour. Before changing defaults, measure long idle
sessions, far-field speech, television and echo, overlapping speakers,
multiple microphones, low-end CPUs, and the packaged Electron runtime.

## Reproduction

```powershell
uv run python scripts/evaluate_speech_presence.py `
  --real-device-manifest .\voice-presence-real-device.json `
  --output .\speech-presence-report.json
```

The JSON manifest schema is:

```json
{
  "schema_version": 1,
  "clips": [
    {
      "id": "idle-fan-01",
      "path": "recordings/idle-fan-01.wav",
      "label": false,
      "device_id": "desktop-usb",
      "scenario": "real_idle_fan"
    }
  ]
}
```

Do not commit raw microphone recordings or manifests containing identifying
paths.
