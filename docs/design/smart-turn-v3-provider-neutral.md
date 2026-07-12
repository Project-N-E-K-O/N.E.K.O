# Smart Turn v3 provider-neutral backend

This backend supersedes the implementation approach in PR #2187. It is built
from the current package-based `main` layout and intentionally does not connect
to Omni Realtime, Core, an ASR provider, or a user-visible setting.

## Endpoint authority

- A provider with an authoritative semantic endpoint (for example Soniox
  `<end>`) must not construct or call Smart Turn.
- An ASR provider without that capability may use common VAD to find a
  candidate pause and ask Smart Turn for `COMPLETE` or `INCOMPLETE`.
- Provider buffer commit, hard timeout, maximum turn duration, and manual
  commit remain responsibilities of the future ASR Controller.

VAD emits only speech start, resumed speech, and candidate pause events. It is
also suitable for barge-in and connection lifecycle gating, but it never emits
`TURN_COMPLETE` or `FORCE_COMMIT`.

## Audio contract

The package accepts a continuous stream of signed 16-bit little-endian, mono,
16 kHz PCM. It does not resample. Callers with 48 kHz capture must use the
project's stateful streaming resampler before this boundary; independently
resampling each capture chunk can create endpoint artifacts.

Smart Turn receives at most the trailing eight seconds. Short inputs are
left-padded. Whisper-compatible preprocessing is implemented with NumPy and is
golden-tested against the reviewed 80-bin mel bank and synthetic feature
statistics.

## Assets and lifecycle

`data/vad_models/manifest.json` pins model revisions, authoritative URLs,
licenses, and SHA-256 digests. Run:

```text
python tools/voice_eval/prepare_voice_turn_assets.py
```

The runtime is lazy and disabled by default. Concurrent loads are
single-flight. Missing/corrupt assets produce `UNAVAILABLE`, which is distinct
from a semantic `INCOMPLETE` result. Repeated inference failures open a
per-instance circuit breaker; constructing a new instance permits recovery.

## Current verification

- Hermetic unit/concurrency/build-contract tests cover buffer bounds, model
  lifecycle, Silero state/context, stale results, candidate coalescing, close,
  asset SHA checks, and the Soniox-like capability path.
- The pinned real models load successfully. One second of synthetic silence
  stays below Silero speech probability 0.05. Smart Turn golden outputs are
  checked for silence and a synthetic tone.
- On the Windows development machine, Smart Turn with two CPU threads and the
  CPU memory arena disabled measured about 26 ms P95 after warm-up. This is a
  local inference measurement, not ASR/network latency.

## Accuracy gate and known limitation

Synthetic fixtures validate the implementation contract, not conversational
accuracy. No product-quality claim is made for Chinese, English, or Japanese.
Before enabling the backend, maintainers must run
`tools/voice_eval/evaluate_smart_turn_v3.py` on an authorized labelled set that
includes sentence-internal pauses, hesitation followed by continuation,
complete turns, keyboard noise, and barge-in. The report always includes all
four confusion-matrix cells and per-sample probabilities.
