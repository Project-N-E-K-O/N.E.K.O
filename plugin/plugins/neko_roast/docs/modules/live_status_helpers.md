# Live Status Helpers

## Purpose

The live status helpers turn connection state, recent activity, cooldowns, and solo-hosting state into deterministic readiness and next-action projections. They keep dashboard and runtime decisions consistent without performing side effects.

## Ownership And Contracts

- `core/live_status.py` is the compatibility facade for callers that previously used one status module.
- `core/live_status_core.py` owns connection and live-state summaries.
- `core/live_status_timing.py` owns age calculations, activity-level intervals, and recent real-output lookup.
- `core/live_status_idle.py` and `core/live_status_active.py` calculate idle-hosting and active-engagement eligibility.
- `core/live_status_director.py` combines those projections into the next automatic hosting action.
- `core/live_status_readiness.py` owns solo-test readiness and speech explanations.
- `core/live_host_theme.py` owns the small theme projection used by status consumers.

The helpers accept configuration-like objects and plain dictionaries, then return plain dictionaries with stable summary, reason, eligibility, cooldown, and next-action fields. They do not mutate their inputs.

## Pipeline, Safety, And Data

These modules only describe runtime state. They do not create `InteractionRequest` objects, emit NEKO output, subscribe to EventBus, or write stores. Any action selected from a status projection still enters `core/pipeline.py`, passes `core/safety_guard.py`, and reaches NEKO through `adapters/neko_dispatcher.py`.

Timing helpers read compact recent-result metadata and health-row ages. A `dry_run` is test evidence, not a delivered output, so it must not consume real-output cooldowns. No raw live payloads, credentials, viewer profiles, or persistent data are read or written.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_roast/tests/test_live_status_rules.py -q
uv run pytest plugin/plugins/neko_roast/tests -q --maxfail=1
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
```

Focused tests verify that dry-run results do not count as delivered hosting output and that an idle-hosting streak can hand control to active engagement.

## Limitations And Rollback

- Status dictionaries are snapshots and may become stale immediately after the runtime acts.
- Unknown or malformed timestamps are ignored instead of blocking the live loop.
- Missing activity evidence degrades to conservative waiting or ineligible projections.
- To roll back the split, restore callers to the `core/live_status.py` facade; pipeline, safety, dispatcher, EventBus, and stores remain unchanged.
