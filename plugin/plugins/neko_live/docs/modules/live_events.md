# live_events Module

## Purpose

`live_events` is the live-room selection hub for provider-neutral rich events. Live providers publish `LiveEvent` envelopes to `ctx.event_bus`; this module unwraps the provider event, reads it through `modules/live_events/provider_event.py`, and forwards one selected payload to `ctx.handle_live_payload()`.

It also owns two deliberately separate stores of short-lived room context. `room_topic.py` keeps advisory prompt context for theme grouping. `recent_chat.py` keeps a bounded factual window so the current NEKO role can use the `get_recent_live_chat` LLM tool when a user explicitly asks who just said what. The same role-scoped tool may perform one local relevance lookup for a concrete topic during ordinary live conversation, and an unselected remark may become one low-pressure `active_engagement` topic candidate. In `co_stream`, the newest three unselected rows may additionally be projected as one replaceable `ai_behavior="read"` snapshot for the next natural user/host turn; the complete buffer is never injected.

`RoomPulse v0`, its compact prompt projection, solo-only `SceneState v0`, and two-mode direction work are scoped in [Live Room Context And Direction Plan](live_room_context.md). RoomPulse adds privacy-safe aggregate status and may provide one evidence-gated block of at most 240 characters to an already scheduled response. SceneState adds at most 160 characters of deterministic beat guidance to an already selected solo viewer response. Those prompt blocks are capped at 400 characters and do not change selection, scheduling, output frequency, or speaking authority; the distinct co-stream passive slice is documented separately below.

## Owner And Contracts

- Module owner: `plugin.plugins.neko_live.modules.live_events.LiveEventsModule`
- Private collaborators:
  - `plugin.plugins.neko_live.modules.live_events.provider_event`
  - `plugin.plugins.neko_live.modules.live_events.room_topic.RoomTopicContext`
  - `plugin.plugins.neko_live.modules.live_events.room_pulse`
  - `plugin.plugins.neko_live.modules.live_events.room_pulse_prompt`
  - `plugin.plugins.neko_live.modules.live_events.scene_state.SceneState`
  - `plugin.plugins.neko_live.modules.live_events.recent_chat.RecentChatBuffer`
  - `plugin.plugins.neko_live.modules.live_events.ambient_context.AmbientRoomContext`
- Input contract: `LiveEvent.raw` is a provider event exposing safe scalar fields such as `event_type` / `type`, `uid`, `nickname`, `text` / `danmaku_text`, `avatar_url`, `room_ref`, `room_id`, `score`, and optional gift summary fields. It may be an object-style event or an already-sanitized dict event; dict events may use common snake_case or camelCase summary keys such as `gift_name` / `giftName`. Explicit `event_type` / `type` aliases must be strings; object-shaped values are ignored instead of stringified. Common event aliases such as `chat` / `danmu` -> `danmaku` and `sc` / `superchat` -> `super_chat` are normalized by the provider helper. Bilibili `LiveDanmaku` is still accepted through `msg_type` compatibility helpers, but callers should not depend on Bilibili-only types.
- Output contract: selected solo-stream danmaku calls `ctx.handle_live_payload(payload)`. In co-stream, ordinary danmaku does not request an immediate reply; it only refreshes the bounded passive snapshot through `NekoDispatcher.push_ambient_room_context()`. Low-value danmaku may be intentionally skipped before the solo pipeline, but the room-topic context is still updated first.
- Support-event boundary: `gift`, `super_chat`, and `guard` remain owned by `live_support_events`. In solo stream they keep the existing active scheduler. In co-stream, light/medium support is passive context only, while verified high/milestone support may request one active response and is also retained as a passive shadow so an interrupted acknowledgement is not blindly retried.
- Prompt context contract: `prompt_block_for_event(ViewerEvent) -> str` returns one consolidated advisory block capped at 400 characters: RoomPulse contributes at most 240 characters and solo SceneState at most 160. It returns an empty string when neither source is eligible and suppresses both for inactive state, non-running Safety Guard, or queue pressure.
- Scene-result contract: the module subscribes to the existing privacy-safe `result` event and advances SceneState only for `status=pushed` solo results. It never reads result output text and does not own result recording.
- Recent-chat contract: `recent_chat_snapshot(limit=3) -> list[dict]` returns the three newest public, sanitized facts from a fixed session tail, ordered newest first. The tail is size-bounded rather than time-expired and labels whether each row is still inside the 30-second fresh window. The role-scoped tool exposes no numbered `position` or `limit` controls; NEKO treats the result as a small delayed live-room view and chooses naturally from the user's wording and relative times. `relevant_chat_snapshot(query, limit=1) -> list[dict]` locally ranks the separate time-bounded candidate view and returns at most one unselected, unused remark under the ambient pressure gate. The dynamically registered `get_recent_live_chat` tool is visible only to the resolved NEKO role while the listener is connected.
- Ambient-chat contract: `ambient_chat_snapshot(limit=3) -> list[dict]` returns only unselected, unused, duplicate-collapsed remarks while solo-stream hosting is healthy and low pressure. It is consumed by the existing active-topic selector and does not trigger a model call itself.
- Co-stream passive contract: at most three recent unselected danmaku and two verified support facts form one hidden `read` message. A fixed per-role/per-live-session `coalesce_key` replaces pending snapshots, a 45-second timer replaces stale content with a text-free expiry marker, and session reset immediately replaces the previous session key. Viewer text is explicitly marked as untrusted data and is capped before dispatch.
- Audit: selected events record `live_event_selected` with the selected candidate and redacted dropped candidate summaries; low-value danmaku skips record `live_event_reply_skipped` with a stable `selection.*` reason and no raw text; flush or signal handling failures record warning audit entries.

## Data Flow

```text
live provider
  -> LiveEvent(type, uid, payload, raw=safe provider event)
  -> ctx.event_bus.publish(type)
  -> live_events._on_bus_event()
  -> provider_event helpers
  -> recent_chat.remember() (all textual danmaku, including selection skips)
  -> solo_stream: immediate dispatch or cooldown-window selection
       -> recent_chat.mark_selected(seq) for the exact winner
       -> ctx.handle_live_payload()
  -> co_stream: ambient_context projection
       -> NekoDispatcher.push_ambient_room_context(ai_behavior=read)
       -> next natural user/host turn; no immediate response
```

`live_events` subscribes in `setup()` and unsubscribes in `teardown()`.

The module also subscribes to the plugin-owned `result` event for SceneState only. This subscription consumes already-public scalar metadata after a successful dispatcher result; it does not receive provider raw packets, create a second result store, or alter the result path.

For normal solo-stream danmaku, if the safety/local cooldown is clear, the first valid event is dispatched immediately. If cooldown remains, the module opens a short window, keeps the highest-scoring candidate, then dispatches that candidate when the window ends. Co-stream ordinary danmaku does not enter that active selection path; it refreshes only the passive snapshot.

During live reply pressure, `live_events` also applies the existing `LiveConfig.queue_limit` before the pipeline. The pressure count is computed from recently pushed live danmaku replies plus the current selection buffer. Once the limit is reached, plain low-priority danmaku is dropped at the selection layer instead of being buffered or forwarded to the host callback queue. Explicit questions, active-engagement answers, guard/high-score events, and support signals remain eligible.

For normal danmaku, the same submit path also updates a short rolling context window. The prompt projection contains bounded aggregate labels and at most one sanitized representative example. It does not include raw recent-chat history, other-viewer profile hints, or a second tactic section. Current-viewer preference guidance remains independently owned by the existing viewer-profile prompt path.

Low-value danmaku selection happens inside this module, not in host/core. The public pacing knob is `LiveConfig.activity_level`; there is no separate user-facing reply-selection config. Runtime status exposes the derived `reply_selection_policy` only for debugging:

- `selected`: the base selection policy used for `standard` and `active`; it skips low-information danmaku such as bare reactions, repeated digits, or empty short noise. Queue pressure is an additional independent gate and may still produce `selection.queue_limit` before pipeline.
- `quiet`: used for `quiet`; also skips low-priority plain danmaku below the quiet score threshold, while questions, content requests, greetings, guards, and very high-score events still pass.

Skip reasons are stable observability keys:

- `selection.low_value_danmaku`: low-information danmaku was ignored before pipeline.
- `selection.quiet_low_priority`: quiet activity level suppressed a plain low-priority danmaku.
- `selection.queue_limit`: recent live replies plus the current selection buffer reached `queue_limit`, so a plain low-priority danmaku was dropped before pipeline.

These skips set `last_selected_type="danmaku.skipped"`, `last_skip_reason`, and `reply_selection_policy` in module status. They do not push output, do not write raw danmaku text to audit detail, and do not prevent the room-topic window from learning that the room received a low-value candidate.

## Recent Danmaku Facts And Tool

`RecentChatBuffer` keeps at most 12 time-bounded candidate records plus a three-reference session tail. The tail always points to the last three received records, does not expire by seconds, and is replaced only by newer danmaku or cleared on session reset. Each exact result exposes `within_fresh_window`, based on 30 seconds, so an older tail fact is described as “the latest item recorded in this session” rather than “just now.” Separate unselected-only ambient and relevance views retain candidates for at most 120 seconds and never use an old session-tail-only record for natural pickup. Every record receives a session-local monotonic sequence number. `LiveEventsModule` carries that sequence alongside the cooldown-window winner and marks the exact record selected; repeated messages from the same UID are therefore not matched by text. When a provider exposes an explicit safe message ID, a bounded 64-ID session cache suppresses transport redelivery before room-topic and reply selection. No content fingerprint is used: two genuine messages with the same UID and text remain two facts when their provider IDs differ or are unavailable. Once an ambient or relevant candidate is reserved, all same-UID/same-text duplicates are hidden only from later ambient reads so the same joke is not picked up repeatedly; this does not rewrite the factual tail. Missing-UID textual danmaku may remain queryable as observed facts, but the existing reply pipeline still rejects them because it requires a stable identity.

The buffer stores only `uid`, sanitized `nickname`, sanitized public `text` (the existing 512-character provider-neutral limit), arrival time, sequence number, selected state, and transient ambient-used state. Inputs remain string-only and credential-shaped fragments are redacted; custom objects are rejected rather than stringified. Invalid, non-finite, future, or backward-moving clocks are clamped to the session-local monotonic time so expiry cannot be extended accidentally. It never stores raw provider packets, credentials, avatar data, or durable viewer data. `begin_live_session()`, disconnect, room switch, teardown, and `reset()` discard the entire buffer and restart its sequence.

`NekoLivePlugin` dynamically registers `get_recent_live_chat(query="")` after a listener connects. Registration is scoped to the role returned by `resolve_plugin_target_lanlan`; unresolved roles do not fall back to a global tool. Disconnect and failed reconnect unregister the tool. With no `query`, the handler performs a read-only newest-first read of the three-entry session tail. It exposes neither numbered candidates nor `position/limit`; stale callers that still send those removed arguments are ignored and receive the same three-row view. With one concrete, sanitized `query`, the handler performs deterministic local token/substring relevance ranking over only the 120-second candidate view, returns at most one low-pressure unselected remark, and reserves that transient record against repeat pickup. Query text is never stored or exposed in status. The handler returns structured facts and never writes a character line itself.

Live-scene instructions require the model to call the no-query form for factual recent-danmaku questions, treat its newest-first rows as a small delayed live-room view rather than numbered choices, preserve speakers and meaning, and say that no session-tail fact was observed when the tool returns no entry rather than reconstructing text from conversation memory. An entry with `within_fresh_window=false` must be described as a recent retained fact from this session, not as something said just now. During ordinary live conversation the model may call the query form once only when the current turn has one concrete topic and a matching viewer remark would materially improve the reply. It must not poll, call on every turn, override the current speaker, or mention an empty relevance result. Personality may choose, summarize, or frame the returned facts naturally, but must not invent a message outside the returned view.

For natural pickup, `active_topic_recent_source.py` asks for at most three unselected candidates and the normal active-topic selector chooses at most one compact 40-character title for an already scheduled hosting turn. The view is unavailable outside solo stream, while safety is paused/tripped/degraded/disconnected, near the output queue limit, during a new-viewer burst, or when more than four unselected danmaku arrived within ten seconds. The selected sequence is marked ambient-used when its topic is recorded; duplicate collapse, ambient-used state, existing topic-key rotation, and source-streak rotation prevent the same retained remark from being repeatedly selected.

## Safety Boundary

This module does not call `plugin.push_message()` directly. Active output stays behind `ctx.handle_live_payload()`, so the normal pipeline, safety guard, audit store, signal-only handling, and dispatcher boundaries remain intact. Co-stream passive context uses the dispatcher-owned `read` boundary, checks live/session/Safety Guard/output-channel state first, and cannot trigger a model turn by itself.

The room-topic and scene contexts are advisory prompt text only. They do not bypass `ctx.handle_live_payload()`, `safety_guard`, `pipeline`, or `neko_dispatcher`; prompt consumers only read the advisory block. Rendering is disabled while Safety Guard is not running or when the configured live queue is near its limit. SceneState is additionally disabled for co-stream and support events. The room-topic collaborator also reads provider events through the shared provider helpers so public UID, nickname, and compact example text use the same token filtering, credential-fragment redaction, and length bounds as payload construction. SceneState stores no such text at all. Durable viewer preference memory is written later by the normal pipeline through `viewer_store.py`, using only safe tags, counts, and short rule-like summaries from `core/viewer_preferences.py`; `room_topic.py` itself does not write durable storage.

The exact recent-chat read and solo ambient candidate view do not create output by themselves. A relevant tool read and an accepted active-topic candidate only mutate transient `ambient_used` flags to prevent repeats. The co-stream snapshot adds no response turn: it uses one debounce task and one replaceable expiry task, makes no external network request or model call, and writes no disk or long-term viewer memory. A model-chosen query-form tool call can add one ordinary tool round-trip to that conversation turn.

Status exposes only bounded counters and stable reasons: exact/relevant query requests and hits, remembered delivery-ID count, duplicate-delivery suppressions, ambient candidate reads and hits, ambient-used count, suppression count, and the latest suppression reason. It never exposes provider IDs, query text, or raw danmaku.

Status and audit output stay privacy-safe: they expose counts, selected types, scores, guard levels, and candidate summary metadata, not raw provider packets. Provider events must already be sanitized before reaching this module; cookie, token, signature params, full HTML, protobuf raw packets, and avatar bytes/base64 are not valid `LiveEvent.raw` data.

Provider `uid` values are public identifiers used in payloads and selection audit summaries. `live_events` only accepts short token-shaped UID values such as Bilibili numeric ids or platform-prefixed ids like `douyin:<stable_id>`; URL, query, path, object-shaped, or credential-shaped UID values are treated as missing and dropped before dispatch.

Provider `room_ref` values are public payload fields. `live_events` only forwards short token-shaped room references and drops URLs, query strings, fragments, slash paths, object-shaped, or credential-shaped text before building pipeline payloads.

Support-event summary text such as `gift_name` is treated as public payload too. The provider layer should sanitize it before publish, and `live_events` still accepts string text only, collapses multi-line text, redacts credential-shaped fragments, and bounds the forwarded text length as a second guardrail. Objects, bytes, containers, bools, and numbers are dropped instead of being stringified into public text.

Normal danmaku text is still forwarded to the pipeline because it is the user-visible message NEKO responds to, but the provider-neutral helper accepts string text only, collapses multi-line text, redacts credential-shaped fragments, and bounds the public payload length before dispatch. Standalone words like "token" remain valid chat content; only credential-like fragments such as `token=...`, `signature=...`, or `Authorization: ...` are redacted.

Provider `avatar_url` is projected as public string metadata only. `live_events` accepts only HTTP(S) string URLs with public hostnames, no username/password, no local/private IP literals, and strips params, query, and fragment before forwarding. Object-shaped URLs are dropped instead of stringified. It does not fetch or resolve avatar URLs.

Public numeric fields such as `room_id`, `guard_level`, `gift_count`, `gift_value`, and score summaries are projected as non-negative finite scalar values. Integers and numeric strings are accepted where ids/counts are expected; scores accept non-boolean int/float values or numeric strings. Invalid, negative, boolean, `NaN`, infinite, container, bytes, or custom numeric-looking object values are dropped or coerced to zero before payload, audit, or selection state output.

## Limitations

- Entry events are out of scope for this module.
- Gift, Super Chat, and guard do not participate in this selection window; `live_support_events` receives and schedules them independently.
- The selection window stores only the current best candidate plus privacy-safe candidate summaries for the current decision chain.
- The room-topic window keeps a short in-memory danmaku sample for aggregate and compact prompt context. It does not create a second output queue and does not write durable viewer preferences itself.
- SceneState keeps one phase, one allowlisted interaction-shape key, counters, and a timestamp. It follows at most three successful viewer turns, expires after 120 seconds, and clears on session or live-mode boundaries. It cannot recover an interrupted scene or infer semantics beyond the existing active-hook-answer signal.
- The factual tool can answer positions only within the last three messages actually received in the current live session. These three do not time-expire, but older positions are overwritten by new messages and all are cleared on disconnect, room switch, reconnect, teardown, or reset. It does not recover provider history that the plugin never received.
- Transport redelivery suppression works only when the provider bridge exposes a stable explicit message ID. Missing or unsafe IDs deliberately disable this dedupe rather than risking the loss of a legitimate repeated message.
- In co-stream, the fixed three-entry session tail enters one passive snapshot with stable `latest / previous / the one before that` labels; the full 12-record relevance buffer never enters the prompt. The labels contain no moving age value, so the tail can remain valid until a newer danmaku shifts it or the live session resets. After 45 seconds, same-key replacement removes volatile support facts while retaining only this bounded positional tail; a support-only snapshot still expires to an empty marker. Already-consumed context may remain in host conversation history because host `read` has no native per-message TTL.
- Local relevance is deliberately lexical and conservative. It can miss synonyms, jokes, and indirect references; the model is instructed to ignore a no-match result rather than inventing a connection.
- Ordinary-turn awareness depends on the model choosing the role-scoped tool. The plugin does not modify host conversation memory or force a lookup on every turn.
- Real Douyin WebSocket/protobuf/heartbeat transport is not implemented here. This module only defines how already-sanitized provider events are consumed.

## Decision Points

The maintainer approved this verification slice after reviewing current and projected performance costs:

| Decision | Approved verification option | Budget / tradeoff |
|---|---|---|
| Factual retention | Three-entry session tail with a 30-second freshness label; separate 12-record, 120-second ambient/relevance candidates; runtime-only | Fixed O(1) capacity. A busy room overwrites tail positions by count; an idle room keeps only those three until the live session ends. |
| Prompt exposure | One coalesced passive three-position tail in co-stream; the legacy role-scoped read tool remains disabled | No extra model/tool round-trip. A row is capped at 48 characters and marked with an ellipsis when truncated, so exact wording is never reconstructed beyond the visible fact. |
| Role scope | Current resolved NEKO role only | If the role cannot be resolved, registration safely degrades to unavailable instead of becoming global. |
| Selection identity | Session-local sequence carried through `live_events` | Exact duplicate messages remain distinguishable; small protected-selection-module change required. |
| Ambient awareness | Co-stream passive tail plus existing solo active-engagement pickup | Co-stream carries at most three bounded facts and never starts a turn; solo active hosting still gets at most one 40-character candidate under its existing pressure gates. |
| Storage / network / dependencies | None | No persistence, polling, external request, or new dependency. |

On the 2026-07-21 local verification run with twelve near-512-character synthetic records and the three-reference session tail, the retained structure measured about 11.8 KiB. Mean local operation time was about 1.6 microseconds for a full-buffer write, 3.0 microseconds for a three-entry session-tail read, 8.9 microseconds for a three-candidate ambient read, and 6.9 microseconds for a one-topic relevance read. These are microbenchmarks rather than latency guarantees; their purpose is to catch accidental unbounded work or per-message model/network cost.

The query-form relevance path can be rolled back independently by removing the optional `query` schema/handler branch and its ordinary-conversation instruction; exact latest-fact lookup and active-engagement pickup remain independent. Full rollback is to unregister/remove the one tool, remove `RecentChatBuffer` ownership from `LiveEventsModule`, and remove the live-scene fact rules. Existing selection, room-topic, provider, pipeline, and dispatcher behavior remains otherwise independent.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_live/tests/test_live_events.py plugin/plugins/neko_live/tests/test_douyin_bridge.py -q
```

The tests cover immediate dispatch, cooldown-window selection, rich danmaku routing, reset/cancel cleanup, failure-state cleanup, compact RoomPulse prompt context, low-quality filtering, shared-evidence gating, hard character bounds, dominant-theme example alignment, support-label exclusion, prompt field redaction, privacy-safe prompt observability, successful-result-only SceneState transitions, active-hook callbacks, turn/TTL bounds, co-stream passive-only danmaku, bounded hidden read snapshots, expiry/session-reset replacement, support-tier active/passive routing, combined 400-character bounds, public `uid` / `room_ref` filtering, public avatar URL projection, public numeric projection, public danmaku text redaction and length bounds, event-type alias normalization, object and dict provider-event routing, Douyin provider-event routing without Bilibili-only types, recent-chat capacity/expiry/exact selection, backend positional selection, stable delivery-ID dedupe without content dedupe, separate ambient retention, unselected-only projection, invalid clocks/limits/object inputs, duplicate collapse and consumption, pressure suppression, local relevance matching and sensitive-query rejection, active-topic pickup and consumption, anonymous observed facts, live-session reset, role-scoped exact/relevant tool lifecycle, live-scene instructions, and status-only event boundaries.

Selection tests also cover `activity_level`-derived reply policy: `standard` / `active` skip only low-value danmaku, while `quiet` skips additional plain low-priority danmaku without blocking question-like input.

## Rollback

To roll back ordinary-chat relevance only, remove the optional `query` branch from `get_recent_live_chat` and the matching live-scene instruction. To roll back active-engagement pickup only, remove `_ambient_danmaku_items()` from `active_topic_recent_source.py`; the exact-query tool remains independent. To remove the whole recent-chat slice, unregister `get_recent_live_chat` and remove the `RecentChatBuffer` collaborator; no provider or pipeline rollback is needed.
