# Live Audience Session

## Purpose

`live_audience_session` provides a small, provider-neutral summary for one listening session. It powers the default “This stream” view without reusing the cross-session viewer profile store or the bounded pipeline result list.

The module counts engaged viewers, danmaku, support events, and successful NEKO outputs. It also keeps a compact recent-viewer projection so the streamer can inspect one viewer in a modal without turning the page into a raw event console.

## Ownership And Lifecycle

- `modules/live_audience_session/__init__.py` owns all in-memory counters and recent-viewer details.
- `core/runtime_live_listener.py` starts a new session only after the live provider starts successfully, and finishes it when listening stops.
- Finishing a session preserves the final summary for review. Starting the next session resets all counters.
- `core/runtime_dashboard.py` exposes the public snapshot as `live_session`.
- The module subscribes to provider-neutral EventBus events: `danmaku`, `gift`, `super_chat`, `guard`, and pipeline `result`.

The module never chooses output, bypasses the pipeline, changes safety decisions, or writes viewer profiles.

## Public Contract And Privacy

The dashboard projection contains only aggregate counters and at most 30 recent viewer rows. A row may contain nickname, per-session counters, last event type, last interaction time, and a session-scoped opaque `viewer_key`.

Raw UID values, raw messages, gifts payloads, credentials, and event payloads are not exposed or persisted. Viewer keys use a per-session keyed digest and cannot be correlated across sessions. Exact unique-viewer tracking is capped at 5000; detailed in-memory viewer state is capped at 100 rows. Unknown event types degrade to a generic interaction label.

## Performance Budget

- Event updates are O(1) dictionary/counter operations.
- No timer, polling loop, network request, production dependency, disk write, or logger is added.
- The UI reuses the existing dashboard refresh cycle.
- The normal audience page renders four summary cards and a maximum of 30 rows. Long-term profile details open on demand in a modal.

## UI Boundary

The audience page has two internal tabs:

- **This stream**: session counters and recent interacting viewers.
- **Viewer profiles**: long-term safe derived profiles, shown as a compact summary table with a detail modal.

Pipeline traces, recent results, UID lookup, and sandbox records remain developer-only. The first version intentionally does not claim online viewer count, watch time, new/returning status, rankings, charts, or unanswered-message queues because the provider-neutral input contract does not support them reliably.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_live/tests/test_live_audience_session.py plugin/plugins/neko_live/tests/test_runtime_live_controls.py plugin/plugins/neko_live/tests/test_smoke.py -q
uv run pytest plugin/plugins/neko_live/tests -q
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_live
```

Tests cover EventBus aggregation, lifecycle reset/retention, bounded viewer projections, dashboard projection, listener start/stop integration, modular/compat panel parity, and the eight-locale UI contract.

## Rollback

Remove the module registration, listener lifecycle calls, dashboard `live_session` projection, and audience session UI together. No migration or data cleanup is required because the module owns no persistent state. The existing viewer profile store and live pipeline remain unchanged.
