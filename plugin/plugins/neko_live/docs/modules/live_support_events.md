# live_support_events Module

## Purpose

`live_support_events` classifies Gift, Super Chat, and guard events received from the EventBus support lane. It exists so verified support events no longer fall through as ordinary danmaku or unverified text claims.

The module asks for one short appreciative line. It must not ask viewers for more gifts, SC, or guards; it must not create a ceremony, ranking, or reward promise.

## Owner And Contracts

- Module owner: `plugin.plugins.neko_live.modules.live_support_events.LiveSupportEventsModule`
- Input contract: a `LiveEvent` whose authoritative outer `type` is `gift`, `super_chat`, or `guard`; provider `raw` data may enrich fields but cannot downgrade that verified outer type.
- Output contract: every verified support tier retains the normal active pipeline/dispatcher path. In co-stream, the same event also updates a passive support shadow so later turns keep room continuity without treating that shadow as delivery evidence.
- Metadata contract: request metadata exposes `support_event_type`, `support_event_tier`, and `support_event_label`. In co-stream it additionally declares the host delivery policy (see "Delivery Policy" below).

## Data Flow

The provider ingest publishes a normalized `LiveEvent` to EventBus. `live_support_events` subscribes to `gift`, `super_chat`, and `guard`, projects only public support fields, preserves the event `trace_id`, and calls `ctx.handle_live_payload(payload)` without waiting for the ordinary danmaku selection window.

Before delivery, eligible verified support events enter one session-scoped scheduler. The scheduler serializes support requests, orders only pending items by fixed priority, merges `COMBO_SEND` updates, and deduplicates provider deliveries by a validated `provider_event_id`. It never interrupts a request or TTS line that has already started. Co-stream additionally updates the bounded `live_events` passive snapshot, but every verified tier still uses the same bounded active scheduler and normal dispatcher path.

`core/pipeline_routing.py` detects support event types before first-appearance or repeat-danmaku routing and selects `response_module_id="live_support_events"`.

`core/pipeline_requests.py` calls `ctx.live_support_events.build_request(event, identity, profile)`. The resulting request reuses recent context, viewer preference prompts, and live-event context, but sets `allow_avatar_image=False`.

## Safety Boundary

This module does not push messages directly. Support-event requests still pass through identity/profile preparation, pipeline steps, `safety_guard`, `neko_dispatcher`, audit records, `dry_run`, and runtime timeline projection. In co-stream, a high/milestone request takes the ordinary active dispatcher path: it is recorded as `pushed`, spends actual-output cooldown, and is delivered by the host as a normal proactive cue. The "must not enter callback or voice hot-swap queues" restriction applies only to the separate passive shadow, which stays a hidden `read` snapshot.

`pushed` means the request reached the host, not that the audience heard it. The current
`neko-live` host does not report an end-to-end playback lifecycle; UI and monitoring must
not translate `queued`/`pushed` into "NEKO already responded". The narrowed
[RFC #2491](https://github.com/Project-N-E-K-O/N.E.K.O/issues/2491) deliberately does not
add lifecycle or playback-completion reporting.

Raw Bilibili payloads are not exposed. `ViewerEvent.to_dict()` only projects support summary fields such as gift name, gift count, coin totals, and guard level.

Ordinary danmaku is never promoted to this module from text alone. Text that merely claims a gift or support action remains unverified danmaku and is blocked from thanks-style confirmation by the danmaku/output guards.

## Scheduling Contract

- Milestone: Super Chat and Guard events.
- High: verified Bilibili gold gifts with `gift_value >= 10000`.
- Medium: verified Bilibili gold gifts with `1000 <= gift_value < 10000`.
- Light: silver, free, unknown, and lower-value gifts.
- Solo stream and co-stream schedule every verified tier through the existing active path. Co-stream additionally keeps a passive support shadow; `read` context does not replace the active `respond` acknowledgement.
- Priority changes the next pending support event only. Active Pipeline or TTS work is not cancelled for priority.
- Equal priorities remain FIFO by local submission sequence.
- `provider_event_id` is the authoritative dedupe key when present. Processed IDs remain in a lazy-expiring 10-minute/4,096-entry session cache; this covers normal transport redelivery without retaining an entire long-running stream's event IDs. An event removed from the pending queue by a higher priority releases its provider ID (and combo tombstone, when applicable), because it was never dispatched and must remain retryable. `COMBO_SEND` is stateful: an identical delivery is ignored, while a monotonic count/value update with the same provider ID is allowed to advance the active combo. The short content fingerprint remains only an ingest fallback for callbacks without an event ID.
- `COMBO_SEND` updates share `(room, viewer, combo_id)` state, keep the maximum observed count/value, and finalize once on explicit end or after one second without growth. Identity fields from the first packet are immutable; conflicting updates fail closed. Active combos and timer tasks are bounded, while finalized combo keys stay in a bounded 10-minute/4,096-entry tombstone cache.
- Queue pressure admits a higher-priority event by removing the oldest pending event from the lowest available lower tier; this includes allowing a milestone to replace a pending high-value gift. Light events aggregate only when they have no authoritative provider event ID and their room, viewer, gift, coin type, and provider event type all match. Identified events remain individually retryable instead of entering an aggregate whose dedupe ownership cannot be recovered after eviction. No priority may exceed the hard pending limit (maximum 100); when no compatible aggregate or lower-priority victim exists, the newest event is rejected and reflected in aggregate overflow/drop counters.
- The pending limit follows the active `queue_limit` configuration without a hidden minimum. Runtime decreases affect new admissions only: already accepted items and active combos drain normally instead of being silently evicted, while `status().queue_limit` exposes the effective scheduler value.
- A failed dispatch is not retried, preventing duplicate thanks; it is recorded as `support.dispatch_failed` and subsequent support events continue normally. Audit-store failures are isolated from scheduling so an unavailable diagnostic side channel cannot strand the support queue.
- Starting, changing, or ending a live session clears queue, combo timers, finalized keys, and processed IDs. Cancelled workers remain tracked until `wait_idle()`/`close()` confirms they have exited; after `close()` the scheduler is sealed and rejects any late submission through a stale reference.

### Dispatch submission ownership

The scheduler assigns each admitted item an opaque local `task_id`, then atomically claims one `current` task under a single `asyncio.Lock`. The ID is scheduler bookkeeping only: it is not copied into the live payload, request metadata, Dispatcher call, or host message, because the current host has no correlated completion callback.

The existing worker remains the only normal consumer. Dispatch execution stays outside the ownership lock. A completion, exception, or cancellation may release the current slot only when it carries the same `_DispatchTask` identity that claimed it. The scheduler retains at most 32 sanitized dispatched-history records containing only task ID, event category, priority, generation, outcome, and ownership classification; it never retains viewer ID, nickname, message text, gift label, provider event ID, or raw payload in this history.

Submission finalization has three internal classifications:

- `current`: the finishing task is still the current owner in the active scheduler generation, so it releases the slot.
- `retroactive`: a known dispatched task finishes after reset, cancellation, or ownership rotation; it is audited but cannot release a newer task.
- `stray`: the scheduler cannot prove the finishing task belongs to the current slot or bounded history; it is warning-only and cannot mutate a newer owner.

Each finalization records `support.dispatch_submission_finalized` with sanitized task ID, event category, priority, classification, outcome, and optional exception type. `submitted` means the plugin-side Pipeline/Dispatcher submission awaitable returned. It does not mean the host generated audio, TTS started, browser playback began, or the audience heard the line.

## Decision Points

Approved implementation decisions for this cost-bearing state-machine change:

- **State and memory:** reuse the existing worker and bounded priority heap; add one `asyncio.Lock`, one current-task reference, and at most 32 sanitized history records (a few kilobytes).
- **Provider dedupe budget:** retain at most 4,096 processed provider IDs for ten minutes with lazy expiry and no cleanup task. This replaces the former 65,536-ID whole-session ceiling and requires no migration.
- **CPU / background work:** add no worker, timer, polling loop, retry loop, or network request. Ownership bookkeeping runs only when a support dispatch is claimed or finalized.
- **Interfaces:** keep ownership inside `SupportEventScheduler`; do not add a host completion API or inject task IDs into live payloads. Active acknowledgements remain `respond`; passive co-stream context remains `read`.
- **Failure policy:** release by exact task identity on success, exception, and cancellation; never retry automatically because a failed return cannot prove that the host did not already accept the message.
- **Alternative rejected:** a general queue in Dispatcher would create double queuing, extra latency, and ambiguous ownership across unrelated output families.
- **Rollout and rollback:** the change is in-memory and requires no migration. Reverting the scheduler, tests, status counters, and this contract restores the previous implicit single-worker ownership model.
- **Required evidence:** regression tests cover atomic single claim, old completion not releasing a new owner, `current` / `retroactive` / `stray`, priority/FIFO, bounded sanitized history, no-retry failure, reset, cancellation, close, and audit failure isolation.

## Delivery Policy

In co-stream the human host owns the floor, so a queued acknowledgement can be cut off or
go stale before it is spoken. The module declares only a bounded lifetime and
`interrupt_policy=drop`. It never asks the host to retry, compensate, replay, or select a
short form from an inferred floor state.

The current `neko-live` host exposes no plugin-visible terminal playback state. The
plugin therefore treats request submission as its final ownership boundary; TTL and drop
policy are host-facing declarations, not observed delivery outcomes.

| Key | Co-stream declaration | Contract status |
|---|---|---|
| `delivery_ttl_seconds` | 45 | Bounds how long a queued acknowledgement remains relevant. |
| `interrupt_policy` | `drop` | A failed or interrupted return cannot prove that the host did not already accept or speak the line. |

Rules:

- Solo stream declares neither field; it follows ordinary host behavior.
- Every co-stream support tier uses the same no-retry policy. Priority changes ordering, not delivery semantics.
- `delivery_key`, `compensation_text`, `compensation_ttl_seconds`, and `brief_text` are not emitted or passed through the plugin bridge.
- The passive support snapshot stores the verified fact and tier only. Whether an active acknowledgement was requested remains an audit outcome; it is not persisted into passive prompt state and is never presented as delivery evidence.
- Ordinary co-stream danmaku (`danmaku_response`) similarly declares `delivery_ttl_seconds=20` and `interrupt_policy=drop`.

Host-side terminal states and floor state remain outside the plugin boundary.

## Limitations

- Entry/follow events are still out of scope.
- The module only produces short thanks-style replies; it does not implement contribution rankings, reward logic, or privileged viewer treatment.
- The first fixed monetary thresholds currently use Bilibili's normalized `gold` coin totals. Other providers remain light unless their typed bridge supplies an equivalent verified coin contract.
- This field-test slice exposes only bounded in-memory aggregate status. It does not persist a gift ledger or diagnostic event log; persistent accounting remains a later, separately reviewed capability.
- Dispatch ownership ends at host submission. Neither `support.dispatch_submission_finalized` nor Dispatcher `pushed` proves model generation, TTS, browser playback, or audible completion.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_live/tests/test_runtime_live_controls.py::test_handle_live_payload_routes_gift_to_support_events plugin/plugins/neko_live/tests/test_runtime_live_controls.py::test_handle_live_payload_routes_support_events_through_pipeline -q
uv run pytest plugin/plugins/neko_live/tests/test_live_events.py plugin/plugins/neko_live/tests/test_bili_listener_lifecycle.py -q
uv run pytest plugin/plugins/neko_live/tests/test_live_support_scheduler.py -q
```

The broader solo-stream simulation covers Gift and SC flowing through `live_support_events` together with ordinary danmaku and hosting routes.

`test_live_support_scheduler.py` also locks the dispatch-submission ownership contract: concurrent single claim, exact-identity release, old-vs-new completion isolation, all three completion classifications, bounded payload-free history, cancellation/teardown cleanup, exception continuation, priority/FIFO, and no automatic retry.

### No floor-dependent short form

The plugin does not read human voice state and does not infer `held`, `pause`, or
`open`. It always submits the normal bounded acknowledgement. Any host-internal voice
gate may delay or drop that cue, but the plugin neither supplies `brief_text` nor retries
after an interruption.
