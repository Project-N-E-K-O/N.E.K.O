# Phase 4B voice throttling contract

Phase 4B changes resource scheduling only. It does not change who owns an ASR
endpoint, how a provider publishes a final transcript, or the microphone PCM
wire format.

## Evidence boundary

The desktop 48 kHz audio pipeline emits one `RnnoiseEvidence` value for each
processed input chunk. The value contains the actual RNNoise `frame_count` and
the chunk's `peak`, `mean`, `last`, and streaming `ema`. A 16 kHz path or an
unavailable RNNoise runtime reports unavailable evidence rather than inventing
a one-frame sample.

Evidence follows normal routing, hot-swap buffering, and replay to the
independent ASR runtime. Core only transports the neutral DTO; it does not
import endpointing or choose a throttle action.

The production shadow counters are deliberately low-cardinality: action
counts, evidence-chunk counts, incomplete-chunk counts, and RNNoise/Silero
disagreement counts. They contain no provider key, transcript, audio, user
identity, or unbounded label.

## Wake and endpoint ownership

- With optimization off, PCM continues through the detector lifecycle. No
  synthetic `SPEECH_STARTED` event is emitted.
- In idle state, quiet RNNoise chunks may stop before lifecycle ingestion.
- RNNoise onset can prewarm transport without opening a speech candidate.
- Silero confirmation opens or resumes a candidate.
- Provider-authority streaming modes never load, pin, or evaluate SmartTurn;
  their provider remains the final endpoint authority.
- Still-supported manual streaming modes retain SmartTurn authority so their
  explicit provider commit path can seal the turn.
- Segmented providers also retain the configured SmartTurn authority.
- `WARM_IDLE` and `PREWARMING` buffers are bounded and preserve pre-roll.

Provider selection remains in the existing provider policy. Phase 4B policy
accepts no provider key and cannot choose a provider.

## Completion and successor safety

SmartTurn completion and provider final each consume an identity-scoped fence.
A stale or duplicate callback cannot complete a successor candidate. Provider
final is marked accepted only after the provider-candidate fence succeeds.

PCM arriving after a sealed turn is retained as bounded successor audio.
Completion, final, and DRAINING-overflow paths preserve or replay that audio
under session, lifecycle, detector, ingress, and generation fences. Detector
bindings are released with a fixed upper bound.

## Lifecycle expiry

`PREWARMING` and post-final `WARM_IDLE` have separate TTL scheduling. Every
timer captures the current epoch plus lifecycle, session, detector, and
transport identities. A timer that wakes after any identity changes is stale
and cannot close the successor.

`IndependentAsrRuntime.submit()` always returns `AsrSubmitResult`, including
the unavailable path. Callers do not infer lifecycle state from a bare
boolean.

## Evaluation boundary

`scripts/evaluate_speech_presence.py`
replays the online chunk contract and may also calculate continuous-window
RNNoise candidates offline. A continuous 100 ms result is experimental
benchmark evidence only; no production detector keeps that state.

The tool imports Silero only from
`main_logic.asr_client.endpointing.silero_vad` and defaults to
`main_logic/asr_client/endpointing/models`. It accepts an explicit labeled
device manifest and excludes local recording paths from its report.
