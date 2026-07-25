# twitch_live_ingest

`twitch_live_ingest` is NEKO Live's read-only Twitch provider. It uses
`twitchio==3.2.2` for Helix and EventSub WebSocket delivery, while NEKO owns the
client-secret-free Device Code Flow and encrypted token persistence.

## Supported flow

1. Create a Twitch Developer application and copy its Client ID. A Client Secret
   is not requested or stored.
2. Start device authorization, open `https://www.twitch.tv/activate`, enter the
   displayed user code, and wait while the panel checks authorization at the
   interval returned by Twitch. The pending flow can be cancelled from the panel.
3. Enter any target channel login or a canonical
   `https://www.twitch.tv/<login>` URL. The authorized account and target channel
   are independent.
4. Query the channel and start listening. Helix supplies online/offline metadata;
   EventSub supplies read-only chat and visible support events.

Only the `user:read:chat` scope is requested. Access and refresh tokens are
stored in the `twitch` namespace of `CredentialStore`, encrypted at rest. Device
codes remain in memory. TwitchIO is always started with
`load_tokens=False`, `save_tokens=False`, and `with_adapter=False`, so it never
creates `.tio.tokens.json` or starts its OAuth web adapter. A TwitchIO token
refresh callback must replace both rotated tokens through the encrypted store;
if that save fails, the listener stops in `auth_required` state.

## Public event boundary

The provider subscribes to `channel.chat.message` and
`channel.chat.notification` on the same EventSub WebSocket connection.

- Ordinary chat becomes `LiveEvent(type="danmaku")`.
- A chat message with typed Cheer metadata becomes a verified
  `LiveEvent(type="gift")`; Bits are projected as the public gift value.
- Visible `sub`, `resub`, standalone `sub_gift`, and `community_sub_gift`
  notices become verified gift events. A community gift's aggregate notice is
  retained and its child `sub_gift` notices are ignored to prevent duplicate
  thanks. Anonymous gifts use the stable public identity `twitch:anonymous`.
- Raid, announcement, redemption, and other notification types are ignored.

Every support event requires a bounded provider event ID and carries
`support_evidence=twitch_eventsub_typed_event`. Only bounded public fields enter
the shared EventBus and support scheduler; the TwitchIO object and raw EventSub
payload are never retained. Selected events continue through the normal
pipeline, safety guard, and dispatcher before NEKO speaks.

## Runtime observability

The provider records only bounded, in-memory Timeline facts; it does not copy a
TwitchIO object or raw EventSub payload into Timeline, audit, recent results, or
the dashboard.

| Input path | Runtime Timeline | Event outcome / Skip Reason |
|---|---|---|
| Valid ordinary chat | `ingest: received` -> `event_bus: published` -> `live_events.select` | The selected payload enters the shared pipeline. Policy skips use the existing `selection.*` reasons. |
| Verified Cheer or visible subscription | `ingest: received` -> `event_bus: published` -> `live_support_events.receive` | The bounded support scheduler emits the existing `support.<type>` outcome or its stable scheduler skip reason. |
| Malformed chat or supported notice that cannot be safely normalized | `ingest: dropped` | `ingest.invalid_twitch_projection`; no `LiveEvent` is published. |
| Unsupported notice, or a child gift notice suppressed after its community aggregate | `ingest: dropped` | `ingest.ignored_twitch_notification`; no `LiveEvent` is published. |

Once a payload reaches `ctx.handle_live_payload(...)`, `safety_guard.before_event`,
`safety_guard.before_output`, `dispatcher.push`, and `result.record` remain the
visible shared stages. Their final outcome is one of the normal `ok`, `dry_run`,
`skipped`, `blocked`, or `failed` results. Pre-projection drops stop at `ingest`
and therefore do not manufacture a Dispatcher outcome.

The dashboard derives connection state, last accepted event time, Timeline, and
recent outcomes only from runtime/audit facts. It never projects provider raw
payload. The two Twitch ingest skip records contain a new trace ID, stage,
status, stable reason, and route only; they contain no UID, message text,
nickname, token, URL, or notification object.

### Decision points

| Decision | Cost and budget | Alternatives and tradeoff | Selected option | Rollout, rollback, and checks |
|---|---|---|---|---|
| Explain provider-boundary drops | One bounded in-memory Timeline append per rejected projection; no new timer, queue, request, dependency, storage, audit write, or raw-data retention. Valid events add the existing `ingest` and `event_bus` boundary facts. | Keeping drops invisible has zero append cost but cannot explain expected non-output. Per-event audit or persistent logging improves history but adds I/O and privacy exposure. | Reuse the existing bounded runtime Timeline with two allowlisted reasons and empty UID. | Enabled with the Twitch listener. Roll back by removing the provider record calls and reason codes. Projection/lifecycle tests assert stage, reason, and empty UID; the plugin test suite covers bounded Timeline behavior. |

## Permission and cost decision

The approved low-permission design keeps the existing `user:read:chat` scope.
It adds one EventSub subscription but no OAuth scope, reauthorization,
dependency, or persistent gift ledger. This reports support activity visible in
chat; it is not an authoritative broadcaster revenue feed. Adding authoritative
Bits or subscription accounting later would require a separate owner review and
the broader broadcaster scopes such as `bits:read` or
`channel:read:subscriptions`. Rollback only requires removing the chat
notification subscription and Cheer projection.

Device authorization polling is an explicit, bounded external-request cost. It
starts only after the user selects **Authorize Twitch**, permits one request in
flight, waits at least Twitch's returned interval (five seconds when omitted),
adds five seconds after `slow_down`, and backs off transient network failures up
to 60 seconds. It stops on success, denial, expiry, cancellation, or panel
unmount. With Twitch's typical 1,800-second device-code lifetime and five-second
interval, the upper-bound baseline is about 360 token checks for one user-started
authorization attempt. See Twitch's [Device Code Grant
Flow](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#device-code-grant-flow)
and [RFC 8628 section 3.5](https://www.rfc-editor.org/rfc/rfc8628#section-3.5).

## Deliberately out of scope

- Twitch homepage, discovery, recommendations, or followed-channel feeds
- authoritative broadcaster revenue/accounting feeds
- raids, channel-point redemptions, announcements, or other notification types
- sending chat messages or any other Twitch write operation
- always-on Device Flow polling outside an active user-started authorization
  session
- bundled Client ID or Client Secret

Rollback is provider-local: stop/unregister `twitch_live_ingest` and
`twitch_identity`; the Bilibili and Douyin providers and the shared pipeline do
not need to change. Encrypted `twitch_credential.*` files can be removed through
the Twitch logout action.
