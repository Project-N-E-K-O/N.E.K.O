# douyin_live_ingest Module

## Purpose

`douyin_live_ingest` is the read-only Douyin live provider. Its v1 responsibility is to normalize Douyin room targets, keep Douyin credentials separate from Bilibili credentials, start the bundled local `douyinLive` bridge when available, connect to that localhost bridge, and publish already-cleaned provider events into the shared live event pipeline.

Direct Douyin WebSocket/protobuf/ack/heartbeat transport is intentionally not kept in the v1 runtime because live testing hit Douyin `DEVICE_BLOCKED` handshakes. The stable path is a bundled MIT `douyinLive` executable supervised by the plugin and consumed only through the generic localhost bridge adapter. Browser automation, automatic login, vendored `Douyin_Spider` code, and plugin-side JS signature execution remain outside the approved v1 boundary; bridge or protocol drift must degrade visibly through `listener_state()` and the connection snapshot.

## Owner And Contracts

- Module owner: `plugin.plugins.neko_roast.modules.douyin_live_ingest.DouyinLiveIngestModule`
- Public projection helper: `plugin.plugins.neko_roast.modules.douyin_live_ingest.public_projection`
- Room target parser: `plugin.plugins.neko_roast.modules.douyin_live_ingest.room_ref`
- Room metadata parser: `plugin.plugins.neko_roast.modules.douyin_live_ingest.webcast`
- Bridge connection plan projection: `plugin.plugins.neko_roast.modules.douyin_live_ingest.bridge_plan`
- Bridge backend spec: `plugin.plugins.neko_roast.modules.douyin_live_ingest.bridge_backend`
- Bundled bridge supervisor: `plugin.plugins.neko_roast.modules.douyin_live_ingest.embedded_bridge`
- External bridge wrapper: `plugin.plugins.neko_roast.modules.douyin_live_ingest.external_bridge`
- Transport event contract: `plugin.plugins.neko_roast.modules.douyin_live_ingest.transport_event`
- Replaceable bridge contract: `plugin.plugins.neko_roast.modules.live_bridge`
- Retry state projection: `plugin.plugins.neko_roast.modules.douyin_live_ingest.retry_policy`
- Event normalization: `plugin.plugins.neko_roast.modules.douyin_live_ingest.event_model`
- Identity resolver: `plugin.plugins.neko_roast.modules.douyin_identity`

Input contracts:

- `room_ref` accepts a string `live.douyin.com` URL, string safe token, or positive integer compatibility value parsed by `parse_douyin_room_ref()`; dict/list/bytes/object inputs are rejected instead of stringified.
- Router-facing configured room references are public projections too; Douyin config values must be parsed before reaching snapshots or status output.
- `DouyinRoomRef.to_dict()` is also a public projection. It must re-parse `room_ref`, accept only exact boolean `ok`, allow only known `source` labels, and redact `message` instead of trusting directly constructed dataclass fields.
- Manual cookie credentials come only from the `douyin` namespace in `CredentialStore`, and the saved cookie value must be a string; objects, bytes, containers, and other non-string values are treated as missing credentials. The bundled bridge can run without a plugin cookie, and non-string plugin cookies must not be stringified into bridge URLs or metadata fetches.
- Fixture and transport event payloads must be dict objects. Non-dict payloads, including iterable pairs or custom objects, must normalize to `unknown` and must not publish to the EventBus.
- Fixture and transport events must be reduced to safe scalar fields before reaching `LiveEvent.raw`.
- `normalize()` must derive `ViewerEvent` fields only from `safe_payload()` output; non-dict payloads and object text fields must not be stringified into uid, nickname, danmaku text, target, avatar URL, or raw.
- `to_live_event()` must re-sanitize direct `DouyinLiveProviderEvent` fields before filling `LiveEvent.payload` or `LiveEvent.raw`; directly constructed provider events are not trusted as already-safe.
- Fixture and transport events must carry a safe `room_ref`, either from the configured module target or from the payload. Events without a safe room target are dropped before EventBus publish.

Output contracts:

- Normal chat events publish provider-neutral `LiveEvent(type="danmaku")` and later become `ViewerEvent(source="live_danmaku")`.
- Douyin viewer identity must prefer stable opaque ids such as `webcastUid`, `webcast_user_id`, `open_id`, or `sec_uid` before legacy numeric ids. Some rooms hide detailed viewer information and emit `id` / `idStr` as the placeholder `111111`; those placeholder ids must not become the viewer-profile key when a stable opaque id is present.
- Gift events publish only safe gift summary fields. The provider remains signal-only in the sense that it never creates a reply itself; after EventBus publish, shared `live_events` Selection may choose the event and `live_support_events` may produce a short thanks line. Flat `giftName` / count / value fields and nested `gift` summary objects are accepted, but only `giftName` / `gift_name` / `name`, `num` / `repeat_count` / `combo_count`, and `total_coin` / `diamond_count` / `price` are projected; nested raw objects are dropped. Bridge contribution events such as `WebcastLightGiftMessage`, `WebcastLinkerContributeMessage`, and `WebcastProfitInteractionScoreMessage` may also normalize to `gift` when the bridge emits them instead of `WebcastGiftMessage`; `WebcastLinkerContributeMessage` must prefer `userContributeList[*].userId` over the top-level receiver/anchor id, and must not project a top-level anchor `avatarThumb` as the gift sender avatar. Empty or invalid flat summary fields may fall back to the nested safe summary fields, and multiple numeric aliases use the first valid positive integer or pure-digit string. Bytes, bools, objects, and other unsafe numeric values are treated as missing instead of being stringified.
- Member, follow, like, and stats events are status-only in v1 and must not publish to the EventBus unless a later event-family design is approved.
- Routing and status-only checks must normalize string event aliases before classification, for example `chat` / `danmu` -> `danmaku` and `sc` / `superchat` -> `super_chat`; objects, bytes, bools, or other non-string event-type values must normalize to `unknown` instead of being stringified into a routable alias.
- Numeric `room_id` / `webcast_room_id` may be projected into provider events only when the value is a pure-digit string or a positive integer. Explicit `room_id` takes precedence; if it is present but unsafe, the room id is treated as missing instead of falling back to another alias. Bytes, bools, objects, and other unsafe values are treated as missing instead of being stringified.
- Unknown event types normalize to `unknown`, are not published to the EventBus, and must not expose the raw event-type text in status.

## Data Flow

```text
UI live_platform/live_room_ref
  -> live_provider_router
  -> douyin_live_ingest.start_listening()
  -> parse_douyin_room_ref()
  -> embedded_bridge.DouyinEmbeddedBridgeSupervisor.start()
  -> bundled douyinLive.exe --port <free-port> --log-level warn
  -> modules/live_bridge.LiveBridgeTransport
  -> DouyinLiveBridgeAdapter.map_message()
  -> publish_transport_event()
```

Room-page metadata fetches must use a bounded scalar timeout. Invalid timeout values, bools, containers, and custom numeric-looking objects fall back to the default, and overly large values are capped before reaching `urlopen`.
Room-page URL construction and metadata parsing must not stringify object inputs. `room_ref` values may only be string targets or positive integer compatibility values, and page HTML must be a string before JSON extraction is attempted.
Manual cookie values may be attached only as a `Cookie` request header after string-only validation and CR/LF rejection; malformed multi-line cookie text and non-string cookie values are dropped instead of being forwarded to `urlopen`.
Room-page HTTP and network failures must degrade to `DouyinWebcastInfo(ok=False)` with a bounded public message. HTTP status codes such as 404 may be exposed, but exception text, headers, request URLs, cookies, and token-looking reason strings must not be copied into public status.
The v1 runtime does not perform plugin-side `webcast/im/fetch`, protobuf decoding, ack, heartbeat, or direct WebSocket signing. If those capabilities are reintroduced later, they must go through a separate cost and maintenance review instead of being added behind the current bridge contract.

For fixture and transport payloads:

```text
raw provider message
  -> DouyinTransportEvent
  -> douyin_live_ingest.publish_transport_event()
  -> event_model.safe_payload()
  -> to_provider_event()
  -> to_live_event()
  -> ctx.event_bus.publish()
  -> live_events
  -> ctx.handle_live_payload()
```

Bridge open failures must also be reduced before reaching module status. Full response headers, URLs, cookies, signatures, and exception messages are never exposed.

The default runtime path is the bundled local bridge:

```text
DouyinEmbeddedBridgeSupervisor.start()
  -> douyinLive.exe --port <free-port> --log-level warn
  -> DouyinExternalBridgeTransport.start()
  -> modules/live_bridge.LiveBridgeTransport
  -> DouyinLiveBridgeAdapter.map_message()
  -> DouyinTransportEvent
  -> publish_transport_event()
```

`modules/live_bridge` is provider-neutral. It only opens localhost `ws://` / `wss://` URLs, strips query/fragment/userinfo, parses JSON messages, and delegates message-shape knowledge to the adapter. The current Douyin adapter targets `jwwsjlm/douyinLive` style local WebSocket JSON, but the contract is deliberately narrow: replacing it with `DouyinBarrageGrab`, `dycast`, or another bridge should require a new adapter/wrapper only. EventBus publish, viewer identity, viewer profiles, safe gift projection, shared support-event routing, and the downstream interaction modules must remain unchanged. Gift bridge payloads may expose flat fields, nested `gift` / `giftInfo` / `giftDetail` objects, or contribution-score shapes such as `userContributeList` + `totalScore`; the adapter must reduce those shapes to the safe `gift_name`, `gift_count`, `gift_value`, `uid`, `nickname`, and `avatar_url` fields. A reduced bridge payload with gift fields but no explicit event type may be inferred as `gift`, but it must still pass through `event_model.safe_payload()` before publish.

The bundled `douyinLive` binary lives under `vendor/douyin_bridge/windows-amd64/` with its MIT license, version metadata, and checksum. `bridge_backend.py` is the only place that should name the selected bundled executable, launch arguments, and same-path stale-process cleanup hook. The plugin starts and stops that process, and on Windows a new supervisor start may first terminate old `douyinLive.exe` processes whose executable path exactly matches the bundled backend path, so hot reloads do not accumulate orphan bridge processes. It still treats the binary as a replaceable bridge: no Go source is imported, no bridge raw payload is persisted, stdout/stderr are discarded, and plugin interaction logic only sees sanitized JSON-derived payloads.

## Real-Machine Validation

2026-07-07 internal pass: manual Cookie import, room lookup, bridge startup, event forwarding, and stop cleanup were verified with the N.E.K.O backend and N.E.K.O.-PC Electron frontend running together. The plugin started the bundled `douyinLive.exe` as a child process, forwarded live-room events through the existing proactive bridge while the room was active, and removed the bridge process after `disconnect_live_room`; only a transient `TIME_WAIT` socket remained on the local bridge port.

This validation does not settle binary distribution. Keep the bundled executable as the current internal bridge backend for now, but leave signing, checksum policy, packaging size, platform matrix, and replacement-source review for a later distribution decision.

## Replacement Boundary

If the current bridge stops working, keep the runtime shape and replace only the disposable bridge layer:

1. Add or update a `DouyinBridgeBackend` entry in `bridge_backend.py` with the new executable path and launch arguments.
2. Add a new adapter beside `bridge_adapter.py` only if the replacement bridge emits a different JSON shape.
3. Wire that adapter in `external_bridge.py` or `embedded_bridge.py`; do not touch EventBus, `live_events`, viewer profiles, pipeline, UI actions, or safety guard.
4. Keep the public payload contract unchanged: `event_type`, `room_ref`, `uid`, `nickname`, `text`, optional `avatar_url`, optional gift summary fields, and optional numeric `room_id`.
5. Update vendor license/version/checksum metadata and run the bridge/douyin boundary tests.

Dropping a broken bridge should therefore be a small backend/adapter change, not a rewrite of the Douyin provider.

## Safety Boundary

`public_projection.py` owns the shared safe projection helpers used by room metadata, bridge connection plan, and retry-state output. Any public status, audit detail, dashboard field, connection snapshot, or lookup result must use these helpers or an equivalent stricter sanitizer before exposing Douyin-derived text.

`status()` and `listener_state()` are public projections. They must sanitize internal `state`, `room_ref`, `last_error`, bridge connection plan, reconnect, retry policy, numeric counters, timestamps, and event-type fields again instead of trusting future transport internals to stay clean. Public `state` must be a string known lifecycle label, otherwise it is projected as `unknown`; derived lifecycle booleans such as `listening` must be computed from that sanitized state, not from raw internal state objects; public event-type fields must be strings before alias normalization, otherwise they are hidden. Numeric public fields must be finite and non-negative, and boolean or non-scalar values are treated as invalid numeric input. Boolean public fields such as module `enabled`, metadata `ok`, bridge-plan `ready`, and reconnect `exhausted` accept only exact booleans; truthy strings, numbers, containers, or custom objects must project as `false`.

Reconnect delay calculation must also use finite non-negative scalar values. Invalid retry policy numbers such as negative delays, `NaN`, infinite values, bools, containers, or custom numeric-looking objects must produce a zero delay instead of scheduling a bad timer. Invalid retry budgets are treated as zero retries and exhaust immediately without throwing. Reconnect state-machine flags must use the same exact-boolean contract as public projections; a truthy string or object must not be treated as an exhausted retry state.

The following data must never enter config, audit, UI, viewer profiles, recent results, `ViewerEvent.raw`, or module status:

- cookie, authorization header, token, signature, `ttwid`, `odin_tt`, `sessionid`, or related credential text
- full HTML, raw JSON blobs, protobuf packets, gzip bytes, unknown binary frames, avatar bytes/base64
- unnormalized room targets, unsafe UID shapes, local/private avatar or WebSocket URLs, avatar/endpoint URL userinfo, avatar/endpoint URL query, avatar/endpoint URL fragments

Allowed public text fields are string-only and still redacted before projection. Objects, bytes, containers, bools, and numeric values are dropped instead of being stringified into nickname, text, gift name, event label, bridge status message, or retry reason. A string value such as `cookie=...`, `token=...`, or `signature=...` inside those fields must become `[redacted]`. Authorization header text such as `Authorization: Bearer ...` must be redacted as a whole, including values separated by spaces.

UIDs must use the `douyin:<stable_id>` shape after sanitization. Stable ids may come only from string values or positive integers; bools, bytes, objects, and other unsafe values are cleared instead of being stringified. For Douyin bridge payloads, `webcastUid` and equivalent opaque ids are the preferred profile key because `id` / `idStr` may be the platform placeholder `111111` when the room hides detailed viewer information. If a stable id cannot be proven safe, or if it contains credential marker text such as `cookie`, `token`, `signature`, `webcast_sign`, `ttwid`, `odin_tt`, or `sessionid`, clear it instead of preserving raw text.

Avatar URLs are string-only metadata in v1. They must be HTTP(S), public-host only, and projected as scheme/host/path without params, query, fragment, username, or password. Objects, bytes, and other non-string values are dropped instead of being stringified. Some Douyin rooms emit only default avatar URLs for viewers; that is a metadata downgrade, not a connection failure, and viewer profiles must tolerate empty/default avatars without fetching user pages.

Bridge connection plans are intentionally minimal public metadata. They may expose `ready`, sanitized `room_ref`, empty `params`, empty `endpoint`, safe missing labels (`bridge_executable` / `bridge_runtime`) and a redacted status `message`; they must not expose localhost bridge ports, cookies, signatures, generated WebSocket params, or raw bridge URLs. A healthy bridge plan keeps `missing` empty, while bridge executable or startup failures must visibly degrade the plan instead of reporting ready.

## Limitations

- Bundled local bridge transport is the default stable path, but protocol drift may still cause reconnects, empty events, or bridge startup failures.
- Local bridge integration exists only as a replaceable adapter boundary. If the selected bridge protocol changes, replace the bundled bridge and/or adapter instead of changing EventBus, viewer profile, or interaction modules.
- The current v1 does not execute Douyin's dynamic JS signature routine. If the platform requires a fresh `signature` for a room, the module must remain visibly degraded until that extra runtime cost is separately approved.
- No automatic login, QR login, browser automation, JS signature execution, or vendored `Douyin_Spider` code is allowed before explicit approval.
- v1 does not fetch user profile pages or download avatars.
- v1 does not send Douyin chat, likes, follows, private messages, or gift thanks.
- Gift, member, follow, like, and stats events must not add LLM turns, TTS, or prompt context without a separate cost review.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_roast/tests/test_douyin_bridge.py plugin/plugins/neko_roast/tests/test_event_bus.py -q
uv run pytest plugin/plugins/neko_roast/tests/test_module_registry.py plugin/plugins/neko_roast/tests/test_output_contract.py -q
uv run pytest plugin/plugins/neko_roast/tests/test_live_events.py plugin/plugins/neko_roast/tests/test_live_status_rules.py plugin/plugins/neko_roast/tests/test_runtime_live_controls.py -q
uv run pytest plugin/plugins/neko_roast/tests/test_smoke.py plugin/plugins/neko_roast/tests/test_config_contracts.py plugin/plugins/neko_roast/tests/test_plugin_lifecycle.py -q
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
```

The tests cover decision-point documentation, forbidden transport imports and artifacts, room target parsing, credential separation, metadata and bridge-plan projection, bundled bridge process supervision, replaceable local bridge startup, retry-state redaction, fixture/bridge event normalization, safe gift publication and shared support-event routing boundaries, status-only event boundaries, identity sanitization, provider-router compatibility, and Bilibili non-regression boundaries.

## Rollback

Set `live_platform` back to `bilibili` and clear `live_room_ref` for Douyin. The router keeps Bilibili behavior separate, and Douyin credentials live in their own `douyin` namespace, so deleting the Douyin credential does not affect Bilibili login state.
