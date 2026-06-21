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

## Canonical Concepts

### Runtime Timeline

Runtime Timeline is the ordered explanation of one event across the runtime. It should be derivable from existing facts such as `LiveEvent`, audit records, `PipelineStep`, `InteractionResult`, and future monitor signals.

Timeline entries should use stable stage names, an outcome, an optional skip reason, and a short privacy-safe message.

### Stage

Stage is the stable name of a point in the event lifecycle. Stage names are for developers, reviewers, tests, monitor signals, and future Dashboard labels.

Initial stage names:

- `ingest`
- `event_bus`
- `selection`
- `pipeline`
- `safety_guard`
- `dispatcher`
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

Expected outcomes: `selected`, `dropped`, `failed`.

### Pipeline

`core/pipeline.py` handles permission, identity resolution, profile write, once-per-UID gate, request building, safety output gate, dispatcher call, and result recording.

Expected outcomes: `skipped`, `failed`, `pushed`, `degraded`.

### Safety Guard

`core/safety_guard.py` is the mandatory guard for connection state, pause state, automatic trips, queue limits, and rate limits.

Expected outcomes: `skipped`, `degraded`, `failed`.

### Dispatcher

`adapters/neko_dispatcher.py` is the only output boundary. It must explain whether output was pushed, dry-run, skipped as non-deliverable, degraded to text-only, or failed.

Expected outcomes: `pushed`, `dry_run`, `skipped`, `failed`, `degraded`.

### Runtime

`core/runtime.py` owns lifecycle, config activation, action execution, hosted-ui context, and result snapshots. It should expose the latest privacy-safe runtime state to future Dashboard or Monitor surfaces.

Expected outcomes: `received`, `skipped`, `failed`, `degraded`.

### Dashboard

Dashboard consumes runtime state. It should explain the current state and latest event path without becoming the source of truth.

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
- New event types may add skip reasons only when existing reasons are too vague.
- New monitor signals should be stage-prefixed and privacy-safe.
- Runtime Timeline should remain compact enough for a reviewer to inspect in one PR.
- Dashboard may choose any layout, but it must answer the Dashboard Visibility questions above.
