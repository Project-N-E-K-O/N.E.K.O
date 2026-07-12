# live_support_events Module

## Purpose

`live_support_events` builds the NEKO reply request for Gift, Super Chat, and guard events after `live_events` selection has chosen one candidate. It exists so high-value support events no longer fall through as ordinary danmaku or signal-only skipped results.

The module asks for one short appreciative line. It must not ask viewers for more gifts, SC, or guards; it must not create a ceremony, ranking, or reward promise.

## Owner And Contracts

- Module owner: `plugin.plugins.neko_roast.modules.live_support_events.LiveSupportEventsModule`
- Input contract: a `ViewerEvent` whose raw payload has `event_type` normalized to `gift`, `super_chat`, or `guard`/`sc`.
- Output contract: returns an `InteractionRequest` for the normal pipeline and dispatcher path.
- Metadata contract: request metadata exposes `support_event_type`, `support_event_tier`, and `support_event_label`.

## Data Flow

`live_events` still selects the best candidate in the cooldown window and calls `ctx.handle_live_payload(payload)`.

`core/pipeline_routing.py` detects support event types before first-appearance or repeat-danmaku routing and selects `response_module_id="live_support_events"`.

`core/pipeline_requests.py` calls `ctx.live_support_events.build_request(event, identity, profile)`. The resulting request reuses recent context, viewer preference prompts, and live-event context, but sets `allow_avatar_image=False`.

## Safety Boundary

This module does not push messages directly. Support-event replies still pass through identity/profile preparation, pipeline steps, `safety_guard`, `neko_dispatcher`, audit records, `dry_run`, and runtime timeline projection.

Raw Bilibili payloads are not exposed. `ViewerEvent.to_dict()` only projects support summary fields such as gift name, gift count, coin totals, and guard level.

## Limitations

- Entry/follow events are still out of scope.
- The module only produces short thanks-style replies; it does not implement contribution rankings, reward logic, or privileged viewer treatment.
- Support-event selection priority comes from the provider-neutral `provider_event.event_score()` helper used by `live_events`, so Bilibili, Douyin, and other providers share the same scoring boundary.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_roast/tests/test_runtime_live_controls.py::test_handle_live_payload_routes_gift_to_support_events plugin/plugins/neko_roast/tests/test_runtime_live_controls.py::test_handle_live_payload_routes_support_events_through_pipeline -q
```

The broader solo-stream simulation covers Gift and SC flowing through `live_support_events` together with ordinary danmaku and hosting routes.
