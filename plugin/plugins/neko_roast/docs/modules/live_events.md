# live_events Module

## Purpose

`live_events` is the live-room selection hub for rich Bilibili events. It subscribes to `danmaku`, `gift`, `super_chat`, and `guard` events published by `bili_live_ingest`, keeps only the highest-scoring candidate during a cooldown window, and forwards exactly one selected payload to the existing roast pipeline.

## Owner And Contracts

- Module owner: `plugin.plugins.neko_roast.modules.live_events.LiveEventsModule`
- Input contract: `LiveEvent.raw` must be a `LiveDanmaku`-compatible object with `uid`, `text`, `msg_type`, `room_id`, and `get_score()`.
- Output contract: selected events call `ctx.handle_live_payload(payload)`.
- Audit: selected events record `live_event_selected`; flush or pipeline failures record warning audit entries.

## Data Flow

`bili_live_ingest` publishes rich live events to `ctx.event_bus`.

`live_events` subscribes in `setup()` and unsubscribes in `teardown()`.

If the safety/local cooldown is clear, the first valid event is dispatched immediately. If cooldown remains, the module opens a short window, keeps the highest `get_score()` candidate, then dispatches that candidate when the window ends.

## Safety Boundary

This module does not build prompts, write viewer profiles, or push messages to NEKO directly. All output stays behind `ctx.handle_live_payload()`, so the normal pipeline, safety guard, audit store, and dispatcher boundaries remain intact.

## Limitations

- Entry events are out of scope for this module.
- Gift, Super Chat, and guard events currently reuse the normal roast pipeline context; dedicated thanks/reading/welcome behavior belongs to later event handlers.
- The window stores only the current best candidate and a count, not a full event history.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_roast/tests/test_live_events.py -q
```

The tests cover immediate dispatch, cooldown-window selection, rich event routing, reset/cancel cleanup, and failure-state cleanup.

## Rollback

Disable or remove `LiveEventsModule` registration from the plugin module list to return to direct pipeline handling. The Bilibili ingest and EventBus contracts can remain in place because subscriptions are isolated and teardown unregisters handlers.
