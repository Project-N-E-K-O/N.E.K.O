# live_events Module

## Purpose

`live_events` is the live-room selection hub for provider-neutral rich events. Live providers publish `LiveEvent` envelopes to `ctx.event_bus`; this module unwraps the provider event, reads it through `modules/live_events/provider_event.py`, and forwards one selected payload to `ctx.handle_live_payload()`.

It also owns the live-room danmaku context used by prompt builders. While events pass through the same hub, `live_events` delegates this work to its private `room_topic.py` collaborator: it keeps a short runtime-only danmaku window, filters low-information messages, groups related messages into room themes, attaches static reply tactics, and exposes `prompt_block_for_event()` so `avatar_roast` and `danmaku_response` can nudge NEKO to answer the room topic instead of every line one by one.

## Owner And Contracts

- Module owner: `plugin.plugins.neko_roast.modules.live_events.LiveEventsModule`
- Private collaborators:
  - `plugin.plugins.neko_roast.modules.live_events.provider_event`
  - `plugin.plugins.neko_roast.modules.live_events.room_topic.RoomTopicContext`
- Input contract: `LiveEvent.raw` is a provider event exposing safe scalar fields such as `event_type` / `type`, `uid`, `nickname`, `text` / `danmaku_text`, `avatar_url`, `room_ref`, `room_id`, `score`, and optional gift summary fields. It may be an object-style event or an already-sanitized dict event; dict events may use common snake_case or camelCase summary keys such as `gift_name` / `giftName`. Explicit `event_type` / `type` aliases must be strings; object-shaped values are ignored instead of stringified. Common event aliases such as `chat` / `danmu` -> `danmaku` and `sc` / `superchat` -> `super_chat` are normalized by the provider helper. Bilibili `LiveDanmaku` is still accepted through `msg_type` compatibility helpers, but callers should not depend on Bilibili-only types.
- Output contract: selected danmaku, gift, super_chat, and guard events call `ctx.handle_live_payload(payload)`. Low-value danmaku may be intentionally skipped before pipeline, but the room-topic context is still updated first.
- Support-event contract: `gift`, `super_chat`, and `guard` are converted to safe payload summaries; downstream routing sends them to `live_support_events` instead of ordinary danmaku / avatar routes.
- Prompt context contract: `prompt_block_for_event(ViewerEvent) -> str` returns advisory room-topic context for prompt builders.
- Audit: selected events record `live_event_selected` with the selected candidate and redacted dropped candidate summaries; low-value danmaku skips record `live_event_reply_skipped` with a stable `selection.*` reason and no raw text; flush or signal handling failures record warning audit entries.

## Data Flow

```text
live provider
  -> LiveEvent(type, uid, payload, raw=safe provider event)
  -> ctx.event_bus.publish(type)
  -> live_events._on_bus_event()
  -> provider_event helpers
  -> immediate dispatch or cooldown-window selection
  -> ctx.handle_live_payload()
```

`live_events` subscribes in `setup()` and unsubscribes in `teardown()`.

For normal danmaku, if the safety/local cooldown is clear, the first valid event is dispatched immediately. If cooldown remains, the module opens a short window, keeps the highest-scoring candidate, then dispatches that candidate when the window ends.

During live reply pressure, `live_events` also applies the existing `RoastConfig.queue_limit` before the pipeline. The pressure count is computed from recently pushed live danmaku replies plus the current selection buffer. Once the limit is reached, plain low-priority danmaku is dropped at the selection layer instead of being buffered or forwarded to the host callback queue. Explicit questions, active-engagement answers, guard/high-score events, and support signals remain eligible.

For support-event types, the module forwards only the safe summary payload. The downstream pipeline records normal dry-run / pushed results through `live_support_events`.

For normal danmaku, the same submit path also updates a short rolling context window. The prompt context includes only compact representative examples, theme labels, reply tips, static reply tactics, and transient viewer hints such as "often asks questions" or "likes tech/AI".

Low-value danmaku selection happens inside this module, not in host/core. The public pacing knob is `RoastConfig.activity_level`; there is no separate user-facing reply-selection config. Runtime status exposes the derived `reply_selection_policy` only for debugging:

- `selected`: used for `standard` and `active`; skips only low-information danmaku such as bare reactions, repeated digits, or empty short noise.
- `quiet`: used for `quiet`; also skips low-priority plain danmaku below the quiet score threshold, while questions, content requests, greetings, guards, and very high-score events still pass.

Skip reasons are stable observability keys:

- `selection.low_value_danmaku`: low-information danmaku was ignored before pipeline.
- `selection.quiet_low_priority`: quiet activity level suppressed a plain low-priority danmaku.
- `selection.queue_limit`: recent live replies plus the current selection buffer reached `queue_limit`, so a plain low-priority danmaku was dropped before pipeline.

These skips set `last_selected_type="danmaku.skipped"`, `last_skip_reason`, and `reply_selection_policy` in module status. They do not push output, do not write raw danmaku text to audit detail, and do not prevent the room-topic window from learning that the room received a low-value candidate.

## Safety Boundary

This module does not push messages to NEKO directly. All output stays behind `ctx.handle_live_payload()`, so the normal pipeline, safety guard, audit store, signal-only handling, and dispatcher boundaries remain intact.

The room-topic context is advisory prompt text only. It does not bypass `ctx.handle_live_payload()`, `safety_guard`, `pipeline`, or `neko_dispatcher`; prompt consumers only read the advisory block. The room-topic collaborator also reads provider events through the shared provider helpers so public UID, nickname, and compact example text use the same token filtering, credential-fragment redaction, and length bounds as payload construction. Durable viewer preference memory is written later by the normal pipeline through `viewer_store.py`, using only safe tags, counts, and short rule-like summaries from `core/viewer_preferences.py`; `room_topic.py` itself does not write durable storage.

Status and audit output stay privacy-safe: they expose counts, selected types, scores, guard levels, and candidate summary metadata, not raw provider packets. Provider events must already be sanitized before reaching this module; cookie, token, signature params, full HTML, protobuf raw packets, and avatar bytes/base64 are not valid `LiveEvent.raw` data.

Provider `uid` values are public identifiers used in payloads and selection audit summaries. `live_events` only accepts short token-shaped UID values such as Bilibili numeric ids or platform-prefixed ids like `douyin:<stable_id>`; URL, query, path, object-shaped, or credential-shaped UID values are treated as missing and dropped before dispatch.

Provider `room_ref` values are public payload fields. `live_events` only forwards short token-shaped room references and drops URLs, query strings, fragments, slash paths, object-shaped, or credential-shaped text before building pipeline payloads.

Support-event summary text such as `gift_name` is treated as public payload too. The provider layer should sanitize it before publish, and `live_events` still accepts string text only, collapses multi-line text, redacts credential-shaped fragments, and bounds the forwarded text length as a second guardrail. Objects, bytes, containers, bools, and numbers are dropped instead of being stringified into public text.

Normal danmaku text is still forwarded to the pipeline because it is the user-visible message NEKO responds to, but the provider-neutral helper accepts string text only, collapses multi-line text, redacts credential-shaped fragments, and bounds the public payload length before dispatch. Standalone words like "token" remain valid chat content; only credential-like fragments such as `token=...`, `signature=...`, or `Authorization: ...` are redacted.

Provider `avatar_url` is projected as public string metadata only. `live_events` accepts only HTTP(S) string URLs with public hostnames, no username/password, no local/private IP literals, and strips params, query, and fragment before forwarding. Object-shaped URLs are dropped instead of stringified. It does not fetch or resolve avatar URLs.

Public numeric fields such as `room_id`, `guard_level`, `gift_count`, `gift_value`, and score summaries are projected as non-negative finite scalar values. Integers and numeric strings are accepted where ids/counts are expected; scores accept non-boolean int/float values or numeric strings. Invalid, negative, boolean, `NaN`, infinite, container, bytes, or custom numeric-looking object values are dropped or coerced to zero before payload, audit, or selection state output.

## Limitations

- Entry events are out of scope for this module.
- Gift, Super Chat, and guard candidates still participate in the selection window, but once selected they are routed by the pipeline to `live_support_events` for a short thanks-style reply rather than pretending to be ordinary danmaku.
- The selection window stores only the current best candidate plus privacy-safe candidate summaries for the current decision chain.
- The room-topic window keeps a short in-memory danmaku sample for prompt context. It does not create a second output queue and does not write durable viewer preferences itself.
- Real Douyin WebSocket/protobuf/heartbeat transport is not implemented here. This module only defines how already-sanitized provider events are consumed.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_roast/tests/test_live_events.py plugin/plugins/neko_roast/tests/test_douyin_bridge.py -q
```

The tests cover immediate dispatch, cooldown-window selection, rich event routing, support-event routing, reset/cancel cleanup, failure-state cleanup, room-topic prompt context, low-quality filtering, reply tactics, transient viewer hints, room-topic prompt field redaction, public `uid` / `room_ref` filtering, public avatar URL projection, public numeric projection, public danmaku text redaction and length bounds, event-type alias normalization, object and dict provider-event routing, Douyin provider-event routing without Bilibili-only types, and status-only event boundaries.

Selection tests also cover `activity_level`-derived reply policy: `standard` / `active` skip only low-value danmaku, while `quiet` skips additional plain low-priority danmaku without blocking question-like input.

## Rollback

Disable or remove `LiveEventsModule` registration from the plugin module list to return to direct provider-to-runtime handling. EventBus subscriptions are isolated and teardown unregisters handlers, so Bilibili and Douyin provider modules can remain in place.
