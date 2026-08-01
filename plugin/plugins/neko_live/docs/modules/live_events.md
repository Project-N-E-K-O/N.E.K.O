# live_events Module

## Purpose

`live_events` is the live-room selection hub for provider-neutral rich events. Live providers publish `LiveEvent` envelopes to `ctx.event_bus`; this module unwraps the provider event, reads it through `modules/live_events/provider_event.py`, and forwards one selected payload to `ctx.handle_live_payload()`.

It also owns two deliberately separate stores of short-lived room context. `room_topic.py` keeps advisory prompt context for theme grouping. `recent_chat.py` keeps a bounded factual window for internal selection, local relevance, and the three-row co-stream projection; the legacy `get_recent_live_chat` handler remains available for compatibility. Its registration helper resolves the current NEKO role before registering, while `runtime_live_listener` keeps it unregistered before, during, and after a live listener run. An unselected remark may become one low-pressure `active_engagement` topic candidate. In `co_stream`, the newest three session rows may additionally be projected as one replaceable `ai_behavior="read"` snapshot. A selected row keeps its true position but is marked replied and limited to explicit positional fact questions. Separately, an eligible selected danmaku may enter the normal bounded active response path when `co_stream_output_policy=auto_low_interrupt`. The complete buffer is never injected.

`RoomPulse v0`, its compact prompt projection, solo-only `SceneState v0`, and two-mode direction work are scoped in [Live Room Context And Direction Plan](live_room_context.md). RoomPulse adds privacy-safe aggregate status and may provide one evidence-gated block of at most 240 characters to an already scheduled response. SceneState adds at most 160 characters of deterministic beat guidance to an already selected solo viewer response. Those two sub-block budgets total at most 400 characters; the final combined RoomVerdict + SceneState + RoomPulse + RitualMemory context has a separate 520-character ceiling. None changes selection, scheduling, output frequency, or speaking authority; the distinct co-stream passive slice is documented separately below.

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
  - `plugin.plugins.neko_live.modules.live_events.ambient_hook`
- Input contract: `LiveEvent.raw` is a provider event exposing safe scalar fields such as `event_type` / `type`, `uid`, `nickname`, `text` / `danmaku_text`, `avatar_url`, `room_ref`, `room_id`, `score`, and optional gift summary fields. It may be an object-style event or an already-sanitized dict event; dict events may use common snake_case or camelCase summary keys such as `gift_name` / `giftName`. Explicit `event_type` / `type` aliases must be strings; object-shaped values are ignored instead of stringified. Common event aliases such as `chat` / `danmu` -> `danmaku` and `sc` / `superchat` -> `super_chat` are normalized by the provider helper. Bilibili `LiveDanmaku` is still accepted through `msg_type` compatibility helpers, but callers should not depend on Bilibili-only types.
- Output contract: selected danmaku calls `ctx.handle_live_payload(payload)`. Solo stream keeps the immediate response path. In co-stream, an eligible selection uses the normal `respond` dispatcher path only when `co_stream_output_policy=auto_low_interrupt`; other policy values fail closed. All observed co-stream danmaku may still refresh the independent bounded passive snapshot. Low-value danmaku can be skipped before active dispatch, but room context is updated first.
- Support-event boundary: `gift`, `super_chat`, and `guard` remain owned by `live_support_events`. In solo stream they keep the existing active scheduler and immediate acknowledgement. In co-stream, light/medium support is passive context only, while verified high/milestone support keeps the priority/aggregation scheduler and requests one bounded active acknowledgement. The same event is retained as a passive shadow.
- Prompt context contract: `prompt_block_for_event(ViewerEvent) -> str` returns one consolidated advisory block capped at 520 characters. RoomVerdict, SceneState, RoomPulse, and RitualMemory contribute whole bounded blocks in that order; a block that does not fit is omitted rather than truncated. A RitualMemory offer is counted as used only when its complete block survives this shared budget. The method returns an empty string when no source is eligible and suppresses all prompt collaborators for inactive state, non-running Safety Guard, or queue pressure.
- Scene-result contract: the module subscribes to the existing privacy-safe `result` event and advances SceneState only for `status=pushed` solo results. Here `pushed` means handed to the host, not audibly completed; SceneState is therefore routing continuity rather than proof of playback. It never reads result output text and does not own result recording.
- Recent-chat contract: `recent_chat_snapshot(limit=3) -> list[dict]` returns the three newest public, sanitized facts from a fixed session tail, ordered newest first. The tail is size-bounded rather than time-expired and labels whether each row is still inside the 30-second fresh window. `relevant_chat_snapshot(query, limit=1) -> list[dict]` locally ranks the separate time-bounded candidate view and returns at most one unselected, unused remark under the ambient pressure gate. These APIs feed local plugin behavior and the co-stream projection; the listener deliberately unregisters the legacy LLM tool to avoid tool-call turns racing with voice turns.
- Ambient-chat contract: `ambient_chat_snapshot(limit=3) -> list[dict]` returns only unselected, unused, duplicate-collapsed remarks while solo-stream hosting is healthy and low pressure. It is consumed by the existing active-topic selector and does not trigger a model call itself.
- Co-stream passive contract: at most three recent session-tail danmaku, one deterministic callback candidate, and two verified support facts form one hidden `read` message. Every danmaku row is authoritative for its fixed position; selected rows are marked `已回复` and remain available only for explicit positional fact questions, so active selection never shifts “上一条” into “最新” or causes ordinary conversational repetition. The callback candidate is selected only from the newest five current-session records by `ambient_hook.py`; transport redelivery, same-viewer or anonymous repeats, one-viewer floods, pure reactions, contextless fragments, and selected rows cannot win. A substantive exact line repeated by distinct identified viewers may win once as room chorus. A fixed per-role/per-live-session `coalesce_key` lets the host keep only the newest pending snapshot. The snapshot never requests a response and never triggers or advances a realtime hot swap; it is consumed only inside the existing natural-user-turn/passive delivery contract or an already-occurring safe session swap. There is no freshness timer or mid-session instruction overlay. Stable positional labels keep delayed snapshots truthful, and session reset clears the old session key. Viewer text is explicitly marked as untrusted data and is capped before dispatch.
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
       -> ambient_hook deterministic scan (newest five, at most one winner)
       -> NekoDispatcher.push_ambient_room_context(ai_behavior=read)
       -> host passive-context bridge
       -> queue latest same-key snapshot for a natural host delivery point
       -> no response request and no mid-session hot swap
     + eligible low-interrupt selection
       -> ctx.handle_live_payload(selected co-stream danmaku)
       -> danmaku_response request
       -> NekoDispatcher.push_roast(ai_behavior=respond)
       -> normal safety/cooldown/dispatcher output boundary
       -> claim on next natural turn; clear after that reply
```

`live_events` subscribes in `setup()` and unsubscribes in `teardown()`.

The module also subscribes to the plugin-owned `result` event for SceneState only. This subscription consumes already-public scalar metadata after a successful dispatcher result; it does not receive provider raw packets, create a second result store, or alter the result path.

For normal danmaku, if the local selection cooldown is clear, the first valid event is selected immediately. If cooldown remains, the module opens a short window, keeps the highest-scoring candidate, then dispatches that candidate when the window ends. Solo stream turns the selection into an immediate response. Co-stream permits the same bounded active response only when `co_stream_output_policy=auto_low_interrupt`; other policy values fail closed while passive room context continues to refresh.

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

`NekoLivePlugin` still exposes the `get_recent_live_chat(query="")` compatibility handler for older callers and tests. A controlled caller may explicitly enable registration only after the current role is resolved; `runtime_live_listener` unregisters it whenever live configuration or runtime policy reconciles the listener and keeps it disabled throughout the listener lifecycle. The handler independently checks `developer_tools_enabled` on every invocation and returns `developer_mode_disabled` with no entries when developer mode is off. With no `query`, an otherwise permitted call reads the three-entry session tail; with one sanitized `query`, it performs deterministic local token/substring relevance ranking over the 120-second candidate view. Query text is never stored or exposed in status.

Live-scene instructions consume only passive room facts already available to the current host prompt after a natural delivery point. Positional questions use the fixed labels in that projection, ordinary co-stream conversation may use at most one directly relevant viewer line, and neither path starts a tool round-trip.

For co-stream natural pickup, `ambient_hook.py` performs one O(5) local scan at
snapshot render time. It never reads assistant output, conversation summaries,
long-term memory, viewer profiles, or old-session state. It excludes selected
rows, same-viewer or anonymous exact repeats, three-or-more-message
single-viewer floods, emoji/punctuation-only reactions, repeated-character
spam, and fragments below the completeness floor. Stable provider message IDs
already suppress transport redelivery before this scan. An exact substantive
line repeated by at least two identified viewers is therefore treated as one
room chorus rather than discarded merely because it repeats. Remaining rows
receive an explainable bounded score from completeness, recency, question
shape, mood/joke markers, room chorus, and shared topic tokens from distinct
viewers. At most one winner is rendered with an allowlisted type (`多人接梗`,
`连续话题`, `完整问题`, `情绪/笑点`, or `完整内容`). If it still appears in the
authoritative three-row tail the snapshot references its position instead of
repeating its text; an older retained winner is explicitly marked
`非位置答案`. The same line maps the reason to one deterministic expression
intent: answer a question first, advance a shared topic/joke one beat,
acknowledge emotion or extend a punchline, answer a chorus once as room
resonance, or add one fresh angle to complete content. A separate compact rule
keeps author and body distinct, forbids mechanical “nickname said/asked”
announcements, quoting/light paraphrase, and reuse of a previous complete
answer. The candidate is ignored when it does not connect to the current
speaker. No winner means no ordinary callback hint; factual position rows
remain available for explicit questions.

For natural pickup, `active_topic_recent_source.py` asks for at most three unselected candidates and the normal active-topic selector chooses at most one compact 40-character title for an already scheduled hosting turn. The view is unavailable outside solo stream, while safety is paused/tripped/degraded/disconnected, near the output queue limit, during a new-viewer burst, or when more than four unselected danmaku arrived within ten seconds. The selected sequence is marked ambient-used when its topic is recorded; duplicate collapse, ambient-used state, existing topic-key rotation, and source-streak rotation prevent the same retained remark from being repeatedly selected.

## Safety Boundary

This module does not call `plugin.push_message()` directly. Active output stays behind `ctx.handle_live_payload()`, so the normal pipeline, safety guard, audit store, signal-only handling, and dispatcher boundaries remain intact. Co-stream passive room context uses the dispatcher-owned `read` boundary and cannot trigger a model turn by itself; selected active replies use the normal `respond` path and spend output cooldown.

Content value and speech collision are separate decisions. A complete
question, active-hook answer, high-score viewer, or worthwhile room chorus is
not discarded merely because speaking could interrupt: the plugin keeps using
the configured active/passive entry, and the host Core owns user-speech
detection and hard-collision protection. The plugin reads no VAD, playback, or
`audio_done` state. Stable provider-event redelivery, same-viewer flooding, and
already-replied material are content/identity-level duplicates and remain
suppressed locally. No playback callback, public message lifecycle, second
model turn, or compensating queue is introduced.

The room-topic and scene contexts are advisory prompt text only. They do not bypass `ctx.handle_live_payload()`, `safety_guard`, `pipeline`, or `neko_dispatcher`; prompt consumers only read the advisory block. Rendering is disabled while Safety Guard is not running or when the configured live queue is near its limit. SceneState is additionally disabled for co-stream and support events. The room-topic collaborator also reads provider events through the shared provider helpers so public UID, nickname, and compact example text use the same token filtering, credential-fragment redaction, and length bounds as payload construction. SceneState stores no such text at all. Durable viewer preference memory is written later by the normal pipeline through `viewer_store.py`, using only safe tags, counts, and short rule-like summaries from `core/viewer_preferences.py`; `room_topic.py` itself does not write durable storage.

The exact recent-chat read and solo ambient candidate view do not create output by themselves. A local relevance read and an accepted active-topic candidate only mutate transient `ambient_used` flags to prevent repeats. The co-stream snapshot adds no response turn: it uses one debounce task, makes no external network request or model call, and writes no disk or long-term viewer memory. The host queues the latest same-key passive callback for an existing natural delivery point; it does not rebuild or update a live Realtime session merely because context changed.

Status exposes only bounded counters and stable reasons: exact/relevant query requests and hits, remembered delivery-ID count, duplicate-delivery suppressions, ambient candidate reads and hits, ambient-used count, callback-candidate reads/hits, allowlisted callback reason, integer score and candidate count, suppression count, and the latest suppression reason. It never exposes provider IDs, query text, callback text, nickname, UID, or raw danmaku.

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
- In co-stream, the fixed three-entry session tail enters one passive snapshot with stable `latest / previous / the one before that` labels; the full 12-record relevance buffer never enters the prompt. Selected rows retain their positions with a replied state instead of being filtered and relabeling older facts. Support facts use the same stable positional style. The snapshot has no timer because natural realtime swaps may take minutes; same-key coalescing retains only the newest pending snapshot, and the whole snapshot is retired at the live-session boundary. Already-consumed host history remains append-only.
- The callback selector scans only the newest five retained rows and uses lexical/shape rules. It can miss sarcasm, synonyms, and jokes with no shared surface signal. It fails closed with no hook rather than asking a model or filling from memory. Pure voice sessions still depend on the host's existing passive delivery point; the plugin neither raises nor lowers content delivery frequency to disguise delayed context, hard collisions, or realtime disconnects.
- Local relevance is deliberately lexical and conservative. It can miss synonyms, jokes, and indirect references; the model is instructed to ignore a no-match result rather than inventing a connection.
- Co-stream ordinary-turn awareness depends on the current passive projection reaching the host session. Active participation depends on selection, output policy, Safety Guard, cooldown, and dispatcher admission; the passive projection itself never starts a model/tool turn.
- Real Douyin WebSocket/protobuf/heartbeat transport is not implemented here. This module only defines how already-sanitized provider events are consumed.

## Decision Points

The maintainer approved this verification slice after reviewing current and projected performance costs:

| Decision | Approved verification option | Budget / tradeoff |
|---|---|---|
| Factual retention | Three-entry session tail with a 30-second freshness label; separate 12-record, 120-second ambient/relevance candidates; runtime-only | Fixed O(1) capacity. A busy room overwrites tail positions by count; an idle room keeps only those three until the live session ends. |
| Prompt exposure | One coalesced passive three-position tail plus at most one callback hint in co-stream; the legacy read tool can be registered only after resolving the current role and remains disabled by the live listener | No extra model/tool round-trip. Every handler invocation also enforces `developer_tools_enabled`; a positional row is capped at 36 characters and an older callback excerpt at 32, with truncation marked. |
| Role scope | Current resolved NEKO role only | If the role cannot be resolved, passive delivery safely degrades to unavailable instead of becoming global. |
| Selection identity | Session-local sequence carried through `live_events` | Exact duplicate messages remain distinguishable; small protected-selection-module change required. |
| Ambient awareness | Co-stream passive tail plus one deterministic callback candidate and existing solo active-engagement pickup | Co-stream carries three positional facts and at most one bounded non-positional fact, never starts a turn; solo active hosting still gets at most one 40-character candidate under its existing pressure gates. |
| Co-stream callback selection | One O(5) deterministic scan, at most one bounded hint in the existing same-key passive snapshot | Up to about 100 additional characters; no new turn, model/tool call, queue, timer, storage, network request, or delivery attempt. Distinct-viewer substantive chorus remains eligible; provider redelivery and same-viewer flood do not. Local rules may conservatively miss indirect jokes. |
| Storage / network / dependencies | None | No persistence, polling, external request, or new dependency. |

Performance evidence belongs in the implementing PR or release record. The stable contract here is fixed-capacity retention, bounded local scans, and no per-message model or network call.

The query-form relevance path can be rolled back independently by removing the optional `query` schema/handler branch and its ordinary-conversation instruction; exact latest-fact lookup and active-engagement pickup remain independent. Full rollback is to unregister/remove the one tool, remove `RecentChatBuffer` ownership from `LiveEventsModule`, and remove the live-scene fact rules. Existing selection, room-topic, provider, pipeline, and dispatcher behavior remains otherwise independent.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_live/tests/test_live_events.py plugin/plugins/neko_live/tests/test_douyin_bridge.py -q
```

The tests cover immediate dispatch, cooldown-window selection, rich danmaku routing, reset/cancel cleanup, failure-state cleanup, compact RoomPulse prompt context, low-quality filtering, shared-evidence gating, hard character bounds, dominant-theme example alignment, support-label exclusion, prompt field redaction, privacy-safe prompt observability, successful-result-only SceneState transitions, active-hook callbacks, turn/TTL bounds, co-stream passive-only danmaku, bounded hidden read snapshots, same-key burst replacement, selected-row positional stability, deterministic callback selection, exact-repeat and single-viewer-flood rejection, emoji/reaction/fragment rejection, continuous-topic pickup, no-hook silence, expiry/session-reset replacement, support-tier active/passive routing, combined 520-character final-context bounds, public `uid` / `room_ref` filtering, public avatar URL projection, public numeric projection, public danmaku text redaction and length bounds, event-type alias normalization, object and dict provider-event routing, Douyin provider-event routing without Bilibili-only types, recent-chat capacity/expiry/exact selection, backend positional selection, stable delivery-ID dedupe without content dedupe, separate ambient retention, unselected-only ambient pickup, invalid clocks/limits/object inputs, duplicate collapse and consumption, pressure suppression, local relevance matching and sensitive-query rejection, active-topic pickup and consumption, anonymous observed facts, live-session reset, role-scoped exact/relevant tool lifecycle, live-scene instructions, and status-only event boundaries.

Selection tests also cover `activity_level`-derived reply policy: `standard` / `active` skip only low-value danmaku, while `quiet` skips additional plain low-priority danmaku without blocking question-like input.

## Rollback

To roll back ordinary-chat relevance only, remove the local query branch used by the selector; the disabled compatibility tool does not participate in live delivery. To roll back active-engagement pickup only, remove `_ambient_danmaku_items()` from `active_topic_recent_source.py`. To remove the whole recent-chat slice, remove the passive projection and `RecentChatBuffer` collaborator; no provider or pipeline rollback is needed.
