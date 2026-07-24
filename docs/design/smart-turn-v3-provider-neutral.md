# Smart Turn v3 provider-neutral backend

This backend supersedes the implementation approach in PR #2187. It is built
from the current package-based `main` layout and intentionally does not connect
directly to Omni Realtime, Core, an ASR provider, or a user-visible setting.
Phase 3 integrates it through the production voice-input runtime for ASR
providers that need a complete audio segment before transcription. Streaming
providers with a native endpoint do not load Smart Turn. Direct use of the
lower-level `RealtimeAsrSession` is not a supported product voice-input path.

## Endpoint authority

- Streaming ASR uses the provider's native endpoint as the logical-turn
  authority. This includes Qwen and OpenAI `server_vad`, Soniox `<end>`, and
  the provider endpoint modes implemented by Grok and Step.
- Segmented ASR uses Smart Turn to seal one logical turn before the session
  commits one or more bounded physical requests. GLM and Gemini use this path.
- OpenAI uses provider `server_vad`, rejects client-side manual commits, and
  does not load N.E.K.O SmartTurn.
- RNNoise and Silero may suppress idle uploads and wake a streaming transport,
  but neither decides the logical end of a provider-endpointed turn.
- Provider buffer commit, hard timeout, maximum turn duration, and manual
  commit remain responsibilities of the ASR session/controller layer.

VAD emits only speech start, resumed speech, and candidate pause events. It is
also suitable for barge-in and connection lifecycle gating, but it never emits
`TURN_COMPLETE` or `FORCE_COMMIT`.

## Consumer-neutral voice input

`main_logic.voice_input` separates microphone ownership from transcript
delivery. MicLease remains the sole microphone-resource authority, while
`VoiceInputRegistry` activates a registry-issued consumer handle. Consumer
activation never reacquires MicLease and no routing API accepts an arbitrary
owner string.

- Core registers the built-in `core_chat` and `game` consumers and activates
  `core_chat` by default. A lease transition into or out of game mode selects
  the corresponding built-in handle.
- The game adapter calls the shared `utils.game_route_state` dispatcher, so it
  reaches only an active game route without introducing a
  `main_logic -> main_routers` dependency.
- Each logical utterance pins the active handle. Partial and final events use
  that captured route; switching or closing the consumer invalidates the route,
  and delayed events are discarded instead of falling back to chat.
- Consumer capabilities decide whether partial and final events are accepted.
  Game accepts final transcripts only, so game partials never leak into the
  ordinary chat preview.
- A final route is consumed before its callback runs. Duplicate finals and
  callback failures therefore cannot reach a second consumer.
- `PluginVoiceInputRegistrar` is a Core-side SPI only. A host-fixed plugin
  namespace can register a lifecycle-bound consumer, but this phase does not
  expose an SDK, IPC transport, plugin process lifecycle, or MicLease access.
- Provider selection remains centralized and follows the active Core/ASR route
  policy. A game or plugin cannot select Qwen, Soniox, or another provider.

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

When that ingress queue is full, Core rejects the current frame, clears every
pending frame, and invokes the identity-scoped backpressure handler. The
handler invalidates the candidate or active turn and its audio generation
before another frame can be routed. A separate overflow inside the detector's
adapter queue clears candidate bindings and installs a serialized reset
barrier; every submission returns `BACKPRESSURE` until that reset completes.
Only a frame carrying the then-current ingress identity may start the next
candidate. Boundary coverage lives in
`test_audio_stream_queue_clears_whole_candidate_when_full`,
`test_active_audio_queue_overflow_aborts_turn_then_resumes_local_listen`, and
`test_overflow_reset_rejects_audio_until_barrier_finishes`.

Silero remains serial, while Smart Turn evaluation uses one in-flight task and
at most one coalesced retry. Evaluation results re-enter the ordered detector
lane behind PCM that arrived before inference completed. A resumed-speech
activity revision therefore makes an older COMPLETE result stale.

Core handles identity-scoped detector events through its own serial dispatcher.
Provider commands use one independent-ASR dispatcher with one of these orders:

```text
streaming: pre-roll / pending-connect -> real-time PCM -> provider endpoint/final
segmented: pre-roll / pending-connect -> real-time PCM -> Smart Turn seal -> commit
```

Hard mute, Focus suppression, game takeover, stop, route swap, and abort first
invalidate the ingress/turn identity. Queued writes then fail validation before
they can start. A write already in progress may finish, but no later write or
seal from that identity can begin.

These rules are safety contracts rather than resource optimizations. Disabling
`voice_input_resource_optimization_enabled` keeps the independent ASR
continuously active but does not permit microphone PCM to enter Core/Omni. On a
segmented route, Smart Turn not READY implies zero provider wire audio. On a
streaming route, Smart Turn readiness is irrelevant and provider endpointing
continues to own the turn boundary.

## Assets and lifecycle

`data/vad_models/manifest.json` pins model revisions, authoritative URLs,
licenses, and SHA-256 digests. Run:

```text
uv run python tools/voice_eval/prepare_voice_turn_assets.py
```

The runtime is lazy and is loaded on the first candidate turn only for a
Smart Turn endpointed route. Concurrent loads are single-flight.
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
  receive only the logical final authorized by the selected route.
- The pinned real models load successfully. One second of synthetic silence
  stays below Silero speech probability 0.05. Smart Turn golden outputs are
  checked for silence and a synthetic tone.
- On the Windows development machine, Smart Turn with two CPU threads and the
  CPU memory arena disabled measured about 26 ms P95 after warm-up. This is a
  local inference measurement, not ASR/network latency.

## Outstanding real-service acceptance

- Grok's provider-endpoint worker is `implemented` after valid-credential
  single-turn, reconnecting multi-turn, and continuous multi-turn WSS acceptance.
  Provider-native Smart Turn remains an optional later quality evaluation and
  does not load N.E.K.O SmartTurn.
- Qwen Intl uses its own credential slot, but permission/scope validation with
  a real international credential is still pending.
- Soniox still needs the overseas real-speech language/noise matrix and the
  Electron interaction pass. Domestic preference must be decided from measured
  end-to-end latency; it is not inferred from a synthetic RTT estimate.
- Gemini's higher mainland-China latency is treated as a regional network
  characteristic; prior pressure runs reached 9/10 and must not be represented
  as a Smart Turn regression.

## Accuracy gate and known limitation

Synthetic fixtures validate the implementation contract, not conversational
accuracy. No product-quality claim is made for Chinese, English, or Japanese.
Existing human-speech tests show that English and Japanese sentence-internal
pauses still need tuning. The current acceptance target is reliable recovery;
it does not claim to eliminate every roughly 500 ms premature split.
Before treating the integrated routes as product-quality, maintainers must run
`tools/voice_eval/evaluate_smart_turn_v3.py` on an authorized labelled set that
includes sentence-internal pauses, hesitation followed by continuation,
complete turns, keyboard noise, and barge-in. The report always includes all
four confusion-matrix cells and per-sample probabilities.

No numeric product-quality threshold has been approved yet. Before any future
approval run, the product owner must pre-register, for every language and
route, the minimum labelled sample count, metric thresholds, maximum permitted
premature-split count, and confidence method. Missing pre-registered criteria
or failure of any criterion blocks product-quality approval. Remediation must
tune the detector or route without weakening fail-closed behavior, then rerun
the complete registered matrix; a partial rerun cannot clear the gate.
