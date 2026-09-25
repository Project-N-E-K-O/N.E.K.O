# Local short-voice admission replay, 2026-09-24

## Scope and reproducibility

Measured in the integration worktree based on `88d77dbdd`, with this change's
experimental admission-policy and gate implementation present in the worktree.

`scripts/evaluate_short_voice_admission.py` evaluates local recordings without
opening an ASR connection, submitting a transcript, cancelling a response, or
creating LLM/TTS output. It uses the production Silero model and admission gate.
No original audio or transcription is included in this document or its numerical
report. Filenames identify supplied fixtures, **not verified ground truth**.

The following source files were replayed locally with user authorization:

| Fixture | SHA-256 of source M4A | Decoded duration |
| --- | --- | --- |
| `停.m4a` | `34798cc1f222d64a03abee47291630b600771532644de73564b420b443f11105` | 5.184 s |
| `环境音噪音.m4a` | `9df355b3b9734509206955439c61cdba0dc3739981ad213b04c929d1d6a14992` | approximately 34.027 s |

Two paths are compared separately:

1. Decode to 16 kHz PCM16 mono and send directly to continuous Silero inference.
2. Decode to 48 kHz PCM16 mono, then use the real `AudioProcessor` with its current
   RNNoise, AGC, limiter and streaming 48-to-16 kHz resampler before Silero.

Both use 10 ms source packets. RNNoise was available in the 48 kHz run. Its
absence in the direct path is recorded as unavailable evidence, not probability
zero. The paths differ in preprocessing as well as decoding rate; differences
must not be attributed to sample rate alone.

Each path performs model inference once. Identical probability windows are then
replayed through the production gate for legacy configuration and experimental
short-speech minima of 128, 160 and 192 ms. This avoids model-state differences
between policy comparisons. The model stays continuous across the whole clip.
Gate-local pause/seal behavior is preserved; no provider endpoints are invented.
Consequently, counts below are **gate admission starts**, not actual cloud turns.

No silence is appended. No resampler EOF flush is injected. An unfinished
RNNoise frame, FIR tail or partial 512-sample model window remains pending and
is recorded as such. This accelerated replay also does not reproduce
wall-clock-triggered AudioProcessor silence resets of real-time playback.

Example invocation from the repository, using an existing environment:

```powershell
uv run --no-sync python scripts/evaluate_short_voice_admission.py `
  --recording 'C:/Users/ALEXGREENO/Documents/录音/停.m4a' `
  --recording 'C:/Users/ALEXGREENO/Documents/录音/环境音噪音.m4a' `
  --output 'C:/Users/ALEXGREENO/Desktop/CODE/wake-word-artifacts/short-voice-admission-20260924.json'
```

PyAV and the verified local Silero assets must already be present; the tool does
not install packages, download models, or upload fixtures. Inputs are bounded
to 120 seconds of decoded audio. The report contains source hashes, numerical
model windows, evidence, requested local audio ranges and decision positions.

## Results

| Fixture / path | Legacy starts | Experimental 128 ms | 160 ms | 192 ms | New short-path starts |
| --- | ---: | ---: | ---: | ---: | ---: |
| `停`, direct 16 kHz | 1 | 1 | 1 | 1 | 0 |
| `停`, processed 48 kHz | 1 | 1 | 1 | 1 | 0 |
| `环境音噪音`, direct 16 kHz | 6 | 6 | 6 | 6 | 0 |
| `环境音噪音`, processed 48 kHz | 10 | 10 | 10 | 10 | 0 |

The supplied `停` recording **already passes the legacy ordinary 224 ms path**:

| Path | Candidate interval at admission, 16 kHz samples | Ordinary voiced evidence | Source audio available by |
| --- | --- | ---: | ---: |
| Direct 16 kHz | `[51200, 54784)` | 3584 samples / 224 ms | 3.43 s |
| Processed 48 kHz | `[51712, 55296)` | 3584 samples / 224 ms | 3.50 s |

These values are measured input positions, **not ASR recognition latency or
response interruption latency**. They are not measured from an annotated word
onset/end. All four policies admit at the same position within each path. This
fixture therefore does not reproduce a short-candidate admission failure and
does not demonstrate a benefit from enabling the experimental short path.

The longer clip contains numerous high-Silero-probability segments. Its filename
does not establish that those segments contain no speech. The 6/10 starts are
not verified false positives. Experiments change some pending-candidate rejection
reasons, but do not add short-path admissions on this clip. There is no evidence
here to select 128, 160 or 192 ms as a validated deployment threshold.

## Focused evaluator checks and remaining work

Four focused evaluator tests verify:

- Original PCM/sample continuity and partial-model-window accounting.
- Streaming processor invocation without tail padding or forced EOF flush.
- A controlled 128 ms high-probability core plus 128 ms low tail admits only via
  the real experimental gate; legacy does not admit that constructed trace.
- EOF without the required low tail remains pending, rather than becoming an
  artificial speech end.

The constructed trace is a contract test, not a real acoustic positive sample.
No full test suite is required by this tool. Local measurements do not justify
default enablement. Real recordings of `喂`, light speech, short controls and
annotated non-speech sounds are still needed, including 16/48 kHz device paths,
normal/playing-TTS states and both endpoint modes. Actual ASR transcription,
takeover, TTS interruption timing, webpage/Electron behavior and cloud costs
cannot be established by this offline replay.
