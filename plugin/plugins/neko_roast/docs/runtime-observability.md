# NEKO Live Runtime Observability

This document is the canonical source for NEKO Live runtime observability language. It defines what future code, reviews, monitor views, and dashboard surfaces must be able to explain. It does not implement Monitor, change Dashboard layout, or add Gift / SC / Guard behavior.

## Purpose

Runtime observability must answer five questions:

- Did the live event enter NEKO Live?
- Which stage handled it last?
- Why was it selected, skipped, failed, degraded, or pushed?
- Did the dispatcher actually send output, dry-run it, or skip it?
- What can Dashboard show without exposing private data?

## Non-goals

- Do not define a concrete Dashboard layout.
- Do not require a new storage backend.
- Do not replace `stores/audit_store.py` or existing `PipelineStep` / `InteractionResult` fields in this phase.
- Do not implement Monitor in this document.
- Do not add Gift / SC / Guard product behavior.
- Do not introduce a Scenario state machine, Detector / Arbiter architecture, critical hard preemption, FIFO output queue, or output path that bypasses the NEKO Live main chain.

## Reference Principles

NEKO Live may borrow decision-chain principles from the Warthunder reference project, but it must not copy the battle logic or adopt its runtime architecture.

Core rule: 每次猫猫只该说一句；这句话为什么是它，系统必须解释得清楚。

Phase 2B uses three reference principles:

- No FIFO queue: live events are strong real-time input. NEKO Live should not make the cat repeat stale messages from an output queue. Within a selection window, the runtime should keep or select the single event most worth speaking about.
- High-value events may rank higher: future SC, Guard, and important gift events may receive higher selection priority than ordinary danmaku. High priority must not bypass Safety Guard, directly call Dispatcher, skip cooldown policy, or ignore `dry_run`.
- `dry_run` must explain the complete chain: even without real output, runtime observability must record whether the event was received, entered Selection, who won, who lost, why candidates lost, whether Pipeline started, whether Safety Guard passed, and whether Dispatcher ended as `dry_run`, `pushed`, or `failed`.

NEKO Live may also borrow the health rows observation model from the Warthunder reference project, but it must not copy Warthunder refresh groups such as `fast`, `map`, `events`, or `mapimg`. Health rows in NEKO Live must describe the NEKO Live main chain: Live Ingest -> EventBus -> Selection -> Pipeline -> Safety Guard -> Dispatcher -> Config Store.

## Implementation Checkpoint

Updated: 2026-06-21

Phase 2C is intentionally paused at a stable backend-observability checkpoint:

- Completed: Dispatcher Outcome standardization distinguishes `dispatcher.dry_run`, `dispatcher.pushed`, `dispatcher.failed`, and `dispatcher.skipped`.
- Completed: Selection Decision Chain records the selected candidate and privacy-safe dropped candidates with skip reasons.
- Completed: Runtime Health Rows are exposed from `runtime.dashboard_state()` as `health_rows`.
- Not implemented: Runtime Timeline Projection is not exposed yet.
- Not implemented: Event-level `trace_id` is not standardized yet.
- Not implemented: Dashboard rendering for Runtime Health Rows or Runtime Timeline is not part of this checkpoint.
- Not implemented: Monitor signal emission is not part of this checkpoint.

If the original Runtime Observability direction resumes, the next required slice is Event Trace ID Standardization. Runtime Timeline Projection should not be implemented by guessing with UID, event type, or timestamp proximity.

## Canonical Concepts

### Runtime Timeline

Runtime Timeline is the ordered explanation of one event across the runtime. It should be derivable from existing facts such as `LiveEvent`, audit records, `PipelineStep`, `InteractionResult`, and future monitor signals.

Timeline entries should use stable stage names, an outcome, an optional skip reason, and a short privacy-safe message.

Runtime Timeline is a projection of runtime facts. It must not become a second source of truth or a separate event-routing system.

Current implementation status: not implemented. A reliable projection requires a privacy-safe event correlation key that can flow through `LiveEvent`, Selection audit, `ViewerEvent`, and `InteractionResult`.

### Runtime Timeline Projection

Runtime Timeline Projection is the privacy-safe view assembled from existing runtime facts for reviewers, monitor views, and Dashboard surfaces.

Projection rules:

- Project from the NEKO Live main chain: EventBus -> Selection -> Pipeline -> Runtime -> Dashboard.
- Include enough information to explain why exactly one event became the spoken candidate.
- Include losing candidates only as redacted candidate metadata, outcome, and skip reason.
- Preserve `dry_run` as a full lifecycle explanation, not as a shortcut around the chain.
- Do not project raw payloads, cookies, tokens, avatar bytes, or private chat content.

### Stage

Stage is the stable name of a point in the event lifecycle. Stage names are for developers, reviewers, tests, monitor signals, and future Dashboard labels.

Initial stage names:

- `ingest`
- `event_bus`
- `selection`
- `pipeline`
- `safety_guard`
- `dispatcher`
- `config_store`
- `runtime`
- `dashboard`

### Event Outcome

Event Outcome describes what happened at a stage.

Initial outcomes:

- `received`: event entered a stage.
- `published`: event was emitted to the next boundary.
- `selected`: event won a selection window.
- `dropped`: event lost a selection window or was intentionally ignored before pipeline.
- `skipped`: expected guardrail stop; no output should happen.
- `failed`: unexpected error or broken dependency.
- `degraded`: fallback path was used, but the system kept running.
- `pushed`: dispatcher produced real output.
- `dry_run`: dispatcher intentionally did not produce real output.

Use `skipped` for expected policy decisions and `failed` for exceptional behavior.

### Selection Decision Chain

Selection Decision Chain is the ordered explanation of how one candidate wins a selection window and why the other candidates lose.

Rules:

- Selection must choose at most one winner per window for the roast pipeline.
- Selection must not behave like a FIFO queue of stale live events.
- Losing candidates should receive a stable skip reason.
- Priority may influence ranking, but it must remain inside the normal Selection -> Pipeline -> Safety Guard -> Dispatcher path.
- The chain should be compact enough for Dashboard and reviewers to answer: who won, who lost, and why.

### Skip Reason

Skip Reason is a stable key explaining why a stage did not continue toward output. It is not user-facing copy. UI may map it to localized labels later.

Rules:

- Use lowercase dot-separated keys.
- Keep reasons stable once published.
- Prefer specific but reusable reasons.
- Do not include raw payloads, nicknames, cookies, tokens, avatar bytes, or base64.
- If a reason is only meaningful inside one stage, prefix it with that stage or boundary.

Initial skip reasons:

- `input.uid_required`
- `permission.developer_tools_disabled`
- `runtime.disconnected`
- `safety.paused`
- `safety.tripped`
- `safety.queue_limit`
- `safety.rate_limited`
- `viewer.already_roasted`
- `selection.lower_score`
- `selection.lower_priority`
- `selection.window_reset`
- `selection.flush_failed`
- `pipeline.identity_failed`
- `pipeline.request_failed`
- `dispatcher.dry_run`
- `dispatcher.non_deliverable`
- `dispatcher.push_failed`
- `profile.mark_roasted_failed`
- `config.persist_timeout`
- `config.persist_failed`

### Dispatcher Outcome

Dispatcher Outcome is the final output-boundary result for an event that reaches Dispatcher.

Initial dispatcher outcomes:

- `dry_run`: Dispatcher was reached, but real output was intentionally disabled.
- `pushed`: Dispatcher produced real output through the approved output boundary.
- `skipped`: Dispatcher intentionally produced no output for a known policy reason.
- `failed`: Dispatcher attempted the output boundary and hit an unexpected error.

High-value events must still end in one of these outcomes. They must not directly produce output outside Dispatcher.

### High-value Event Priority Contract

High-value Event Priority Contract defines how future SC, Guard, and important gift events may influence Selection without bypassing NEKO Live guardrails.

Contract:

- High-value events may receive higher ranking weight than ordinary danmaku.
- Higher priority only affects Selection ranking unless a future design explicitly updates this document.
- Higher priority does not bypass Safety Guard, cooldown policy, `dry_run`, Dispatcher, or privacy rules.
- Higher priority does not create critical hard preemption.
- Higher priority must still produce explainable winner and loser records through the Selection Decision Chain.

### Monitor Signal

Monitor Signal is a stable operational event name that future monitor code may emit or derive. Phase 2B only defines names and meanings; it does not implement an emitter.

Initial monitor signals:

- `live.listener_started`
- `live.listener_stopped`
- `live.listener_error`
- `event.received`
- `event.published`
- `event.no_subscriber`
- `event.handler_failed`
- `selection.candidate_buffered`
- `selection.decision_recorded`
- `selection.selected`
- `selection.dropped`
- `selection.flush_failed`
- `pipeline.started`
- `pipeline.skipped`
- `pipeline.failed`
- `pipeline.pushed`
- `safety.paused`
- `safety.resumed`
- `safety.tripped`
- `safety.degraded`
- `dispatcher.dry_run`
- `dispatcher.pushed`
- `dispatcher.failed`
- `runtime.config_changed`
- `runtime.config_persist_timeout`
- `runtime.config_persist_failed`

### Runtime Health Row

Runtime Health Row is the compact status projection for one critical runtime boundary. It answers whether that boundary is still refreshing and where the chain appears to be stuck. It is inspired by Warthunder-style health rows, but NEKO Live defines rows around its own main chain rather than Warthunder polling groups.

Runtime Health Row is not a new execution model, queue, scheduler, or source of truth. It must be derived from existing runtime facts, audit records, interaction results, and future monitor signals.

Current implementation status: backend projection implemented. `runtime.dashboard_state()` exposes the initial rows as `health_rows`; Dashboard rendering and Monitor emission remain future work.

Initial health rows:

- `live_ingest`: `last_event_age`
- `event_bus`: `last_publish_age`
- `selection`: `last_decision_age`
- `pipeline`: `last_run_age`
- `safety_guard`: `current_state`, `cooldown_remaining`
- `dispatcher`: `last_outcome_age`
- `config_store`: `last_persist_age`, `last_error`

Initial row fields:

- `id`: stable row id.
- `stage`: matching lifecycle stage when applicable.
- `status`: compact state such as `healthy`, `idle`, `degraded`, `blocked`, or `failed`.
- `count`: optional monotonic count for successful refreshes or observations.
- `age_sec`: optional age since the last successful refresh or relevant observation.
- `last_outcome`: optional latest outcome key.
- `last_skip_reason`: optional latest skip reason key.
- `last_error`: optional redacted error category or reason key.
- `privacy_safe_summary`: optional short redacted summary.

Dashboard and Monitor surfaces may render these rows in any layout, but they must preserve the meaning: each row explains whether one critical NEKO Live boundary is refreshing, stale, blocked, degraded, or failed.

### Dashboard Visibility

Dashboard Visibility defines what the Dashboard must eventually be able to explain, not how it must look.

Dashboard should be able to answer:

- Is NEKO Live listening to a room?
- Is output paused, tripped, degraded, dry-run, or live?
- What was the latest event type and lifecycle stage?
- Why did the latest event not produce output?
- Which event won Selection, and why were other candidates dropped?
- Did Pipeline reach Safety Guard and Dispatcher?
- Did Dispatcher push, dry-run, skip, or fail?
- Is each critical health row refreshing, and which row appears stuck?

Dashboard must not show raw payloads, cookies, tokens, avatar bytes, base64 images, or unredacted private data.

## Event Lifecycle

### ingest

`bili_live_ingest` receives Bilibili live data and normalizes it into `LiveEvent`. This stage should explain whether the listener is started, stopped, errored, or receiving events.

Expected outcomes: `received`, `published`, `failed`, `degraded`.

### EventBus

`core/event_bus.py` publishes `LiveEvent` by type to subscribers. This stage should explain whether an event was published, had no subscriber, or hit an isolated handler failure.

Expected outcomes: `published`, `failed`, `dropped`.

### Selection

`modules/live_events` buffers candidates during the cooldown window and selects one event for the roast pipeline. This stage should explain selected candidates, dropped candidates, scoring failures, reset windows, and flush failures.

Selection is not a FIFO queue. It should explain the Selection Decision Chain for the window: the winning candidate, losing candidates, priority or score differences, and stable skip reasons.

Expected outcomes: `selected`, `dropped`, `failed`.

### Pipeline

`core/pipeline.py` handles permission, identity resolution, profile write, once-per-UID gate, request building, safety output gate, dispatcher call, and result recording.

Expected outcomes: `skipped`, `failed`, `pushed`, `degraded`.

### Safety Guard

`core/safety_guard.py` is the mandatory guard for connection state, pause state, automatic trips, queue limits, and rate limits.

Expected outcomes: `skipped`, `degraded`, `failed`.

### Dispatcher

`adapters/neko_dispatcher.py` is the only output boundary. It must explain whether output was pushed, dry-run, skipped as non-deliverable, degraded to text-only, or failed.

`dry_run` is a Dispatcher Outcome, not an early exit. A `dry_run` event should still explain the earlier lifecycle stages that led to Dispatcher.

Expected outcomes: `pushed`, `dry_run`, `skipped`, `failed`, `degraded`.

### Runtime

`core/runtime.py` owns lifecycle, config activation, action execution, hosted-ui context, and result snapshots. It should expose the latest privacy-safe runtime state to future Dashboard or Monitor surfaces.

Expected outcomes: `received`, `skipped`, `failed`, `degraded`.

### Dashboard

Dashboard consumes runtime state. It should explain the current state and latest event path without becoming the source of truth.

Dashboard should eventually be able to show Runtime Health Rows so operators can distinguish "current config is set" from "each critical boundary is still refreshing".

Expected outcomes: read-only visibility only.

## Privacy Rules

- Do not expose raw live payloads in monitor signals or Dashboard state.
- Do not expose cookies, tokens, login credentials, or encrypted credential material.
- Do not expose avatar bytes or base64 data.
- Prefer UID, event type, stage, outcome, reason key, and redacted short messages.
- Audit and monitor data should be enough to debug the lifecycle without reconstructing private chat content.

## Reviewer Checklist

For any future PR touching runtime behavior, event handling, output, monitor, or dashboard visibility, reviewers should check:

- Every new event path has a stage and outcome.
- Expected non-output paths use a stable skip reason.
- Unexpected failures use `failed`, not `skipped`.
- Safety Guard and Dispatcher remain explicit lifecycle stages.
- Dashboard visibility is derived from runtime state, not raw payloads.
- Privacy rules are preserved.
- New reasons or signals are added to this document before use.

## Future Extension Rules

- Gift / SC / Guard handlers should reuse the same stage, outcome, skip reason, and monitor signal language.
- Gift / SC / Guard priority must follow the High-value Event Priority Contract.
- New event types may add skip reasons only when existing reasons are too vague.
- New monitor signals should be stage-prefixed and privacy-safe.
- Runtime Timeline should remain compact enough for a reviewer to inspect in one PR.
- Runtime Timeline Projection must wait for Event Trace ID Standardization; do not infer event identity only from UID, event type, or timestamp proximity.
- Runtime Health Rows should stay aligned with the NEKO Live main chain and must not copy Warthunder polling group names.
- Dashboard may choose any layout, but it must answer the Dashboard Visibility questions above.
- Future designs must not add FIFO output queues, Scenario state machines, Detector / Arbiter routing, critical hard preemption, or direct output paths that bypass the NEKO Live main chain without a separate architecture review.
