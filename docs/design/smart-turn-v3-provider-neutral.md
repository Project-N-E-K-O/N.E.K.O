# Smart Turn v3 provider-neutral backend

This backend supersedes the implementation approach in PR #2187. It is built
from the current package-based `main` layout and intentionally does not connect
directly to Omni Realtime, Core, an ASR provider, or a user-visible setting.
Phase 3 integrates it through the production voice-input runtime. Every
official independent-ASR route must acquire and retain a Smart Turn lease for
the complete logical turn. Direct use of the lower-level `RealtimeAsrSession`
is not a supported product voice-input path.

## Endpoint authority

- Smart Turn is the only logical-turn endpoint authority for every production
  independent-ASR provider.
- Provider-native endpoints, including Soniox `<end>`, form physical transcript
  segments only. They cannot publish a logical final to a consumer.
- RNNoise and Silero may suppress idle uploads or find candidate pauses, but
  neither can replace Smart Turn.
- Provider buffer commit, hard timeout, maximum turn duration, and manual
  commit remain responsibilities of the ASR session/controller layer.

VAD emits only speech start, resumed speech, and candidate pause events. It is
also suitable for barge-in and connection lifecycle gating, but it never emits
`TURN_COMPLETE` or `FORCE_COMMIT`.

## Consumer-neutral voice input

`LLMSessionManager.bind_voice_input_consumer()` exposes the high-level final
text boundary needed by later game/plugin integration. A binding is inert:
MicLease remains the sole microphone-ownership authority, and the caller must
bind before changing the lease owner to `game`.

- `owner=core` keeps the existing Core transcript path.
- `owner=game` accepts PCM only while the exact captured consumer binding is
  still registered; otherwise the route remains fail-closed and suspended.
- The binding receives only a SmartTurn-authorized `VoiceTranscriptEvent`.
  It never receives PCM, provider partials, or physical segment finals.
- Consumer replacement or removal is forbidden while that owner holds
  MicLease. Lease changes invalidate queued PCM, the active turn, transcript
  reservations, and delayed callbacks before another target can become active.
- Consumer callback failure discards that delivery. It never falls back to
  Core, Omni, another ASR provider, or a browser speech recognizer.
- Provider selection remains centralized and follows the active Core/ASR route
  policy. A game or plugin cannot select Qwen, Soniox, or another provider.

Phase 3 supplies only this common binding contract and a fake-consumer
integration test. Registering concrete games, changing game UI, and removing
legacy browser `SpeechRecognition` are responsibilities of their own follow-up
integration changes.

## Audio contract

The package accepts a continuous stream of signed 16-bit little-endian, mono,
16 kHz PCM. It does not resample. Callers with 48 kHz capture must use the
project's stateful streaming resampler before this boundary; independently
resampling each capture chunk can create endpoint artifacts.

Smart Turn receives at most the trailing eight seconds. Short inputs are
left-padded. Whisper-compatible preprocessing is implemented with NumPy and is
golden-tested against the reviewed 80-bin mel bank and synthetic feature
statistics.

## Asynchronous detector and ordering contract

Production microphone ingestion never waits for Silero callbacks or Smart Turn
inference. Normalized PCM enters a queue bounded by both one second of audio
and 128 frames. Audio cannot consume the reserved control lane. Overflow
invalidates the complete candidate or active turn; it never drops a middle
frame and continues toward a partial transcript.

Silero remains serial, while Smart Turn evaluation uses one in-flight task and
at most one coalesced retry. Evaluation results re-enter the ordered detector
lane behind PCM that arrived before inference completed. A resumed-speech
activity revision therefore makes an older COMPLETE result stale.

Core handles identity-scoped detector events through its own serial dispatcher.
Provider commands use one independent-ASR dispatcher with this order:

```text
pre-roll / pending-connect -> real-time PCM -> SmartTurn seal
```

Hard mute, Focus suppression, game takeover, stop, route swap, and abort first
invalidate the ingress/turn identity. Queued writes then fail validation before
they can start. A write already in progress may finish, but no later write or
seal from that identity can begin.

These rules are safety contracts rather than resource optimizations. Disabling
`voice_input_resource_optimization_enabled` keeps the independent ASR
continuously active but does not disable Smart Turn or permit microphone PCM to
enter Core/Omni. Smart Turn not READY always implies zero provider wire audio.

## Assets and lifecycle

`data/vad_models/manifest.json` pins model revisions, authoritative URLs,
licenses, and SHA-256 digests. Run:

```text
uv run python tools/voice_eval/prepare_voice_turn_assets.py
```

The runtime is lazy and is loaded on the first candidate turn for every
production independent-ASR route. Concurrent loads are single-flight.
Missing/corrupt assets produce
`UNAVAILABLE`, which is distinct from a semantic `INCOMPLETE` result. Repeated
inference failures open a per-instance circuit breaker; constructing a new
instance permits recovery. Closing or failing the ASR session unloads its
Adapter and releases the corresponding runtime resources.

## Current verification

- Hermetic unit/concurrency/build-contract tests cover buffer bounds, model
  lifecycle, Silero state/context, stale results, candidate coalescing, close,
  asset SHA checks, and the Soniox-like capability path.
- Phase 3 additionally covers non-blocking detector submission, Smart Turn
  single-flight/coalescing, candidate rotation, pre-roll ordering, seal
  ordering, abort barriers, overflow recovery, and stale detector identities.
- Every Core routing safety test keeps `omni_mic_audio_bytes == 0`; consumers
  receive only the SmartTurn-authorized logical final.
- The pinned real models load successfully. One second of synthetic silence
  stays below Silero speech probability 0.05. Smart Turn golden outputs are
  checked for silence and a synthetic tone.
- On the Windows development machine, Smart Turn with two CPU threads and the
  CPU memory arena disabled measured about 26 ms P95 after warm-up. This is a
  local inference measurement, not ASR/network latency.

## Accuracy gate and known limitation

Synthetic fixtures validate the implementation contract, not conversational
accuracy. No product-quality claim is made for Chinese, English, or Japanese.
Before treating the integrated routes as product-quality, maintainers must run
`tools/voice_eval/evaluate_smart_turn_v3.py` on an authorized labelled set that
includes sentence-internal pauses, hesitation followed by continuation,
complete turns, keyboard noise, and barge-in. The report always includes all
four confusion-matrix cells and per-sample probabilities.
