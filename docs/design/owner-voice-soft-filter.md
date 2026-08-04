# Owner Voice Soft Filter (Beta v1)

## Scope

This phase introduces a pure, provider-neutral policy that evaluates Speaker
Shadow observations plus an opt-in ASR composition layer. The policy itself
does not own or mutate ASR, Core, CAM++, or application services, and its
`HYPOTHETICAL_REJECT` result remains non-authoritative in isolation.

When an `OwnerVoiceAsrCompositionFactory` is explicitly injected, the
composition correlates that result with the current authoritative detector
candidate and may issue a `CandidateRejectionRequest`. The ASR runtime accepts
only an exact session, audio, transport, turn, detector-candidate, profile, and
filter identity match. An accepted request releases the reserved final,
invalidates and detaches the provider transport, aborts queued audio, and
suppresses only the rejected candidate's tail until its matching pause resets
the detector. Stale or incomplete identity, already-paused candidates, model
or cleanup failures, and teardown all fail open while abandoning any prepared
Core turn exactly once.

This repository phase does not install that factory in application startup and
does not introduce profile persistence, service-location, configuration, UI,
or runtime profile hot-swap behavior.

## Fixed beta-v1 rule

The policy accepts observations only at two exact checkpoints:

- 1,500 ms
- 3,000 ms

It emits `HYPOTHETICAL_REJECT` only when both observations belong to the same
active detector epoch, candidate generation, and profile generation, and both
finite similarity scores are strictly below `0.40`.

Every other case emits `FORWARD`, including a score equal to `0.40`, a missing
checkpoint, an invalid score, a disabled evaluation, or a detector/candidate/
profile identity mismatch. This is a fail-open beta policy, not an identity
claim.

## State and dependency boundary

Only the first low observation is retained. Pending state is keyed by the full
detector epoch, candidate generation, and profile generation, bounded by a
caller-selected positive capacity (256 by default), and evicts the oldest
candidate when full. Candidate completion may explicitly forget its state,
and session teardown may reset all pending state.

`main_logic.voice_identity.policy` uses only built-in Python types. It imports
neither ASR nor Core nor a model runtime, and it does not expose raw embeddings
or a product-facing similarity API. No profile persistence, registry, service
locator, runtime profile swap, configuration, UI, or API is introduced here.
