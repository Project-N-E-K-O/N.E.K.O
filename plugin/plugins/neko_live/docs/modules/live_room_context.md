# Live Room Context And Direction Plan

## Status

`RoomPulse v0`, its compact prompt projection, solo-only `SceneState v0`, and the plugin-only co-stream passive-context validation slice are implemented. Reliable host-turn speaking-authority enforcement is not implemented.

The RoomPulse/SceneState prompt slices change only the context of an output that the existing selection and scheduling paths already chose. The separate co-stream passive slice adds bounded replacement/expiry tasks but still adds no automatic turn, model call, network request, or speaking authority.

## Product Goal

NEKO should understand the room beyond the one danmaku selected for reply while preserving exact factual recall and keeping live output bounded.

The context system has four distinct responsibilities:

1. **Exact facts**: answer who said the latest, previous, or third-latest danmaku without guessing.
2. **Room pulse**: summarize what the room is collectively doing over a short window.
3. **Scene state**: remember the current program beat long enough to set up, develop, and close it.
4. **Mode direction**: apply different speaking authority in `solo_stream` and `co_stream`.

These responsibilities must not be collapsed into one large prompt history.

## Current Baseline

The plugin already has two bounded and deliberately separate live-room views:

- `modules/live_events/recent_chat.py` owns exact session-local facts: 12 retained candidates for at most 120 seconds plus a three-entry session tail. It supports positional and local relevance reads and is not injected into every prompt.
- `modules/live_events/room_topic.py` owns advisory room context: at most 80 sanitized candidates in a 45-second window. It filters low-value messages, groups themes, keeps bounded representative examples, and supplies the compact RoomPulse renderer.

The solo path already has warmup, idle-hosting, active-engagement, pacing, material rotation, and output-quality gates. The co-stream path already defines participation levels and a pure `HostTurnSignal` policy, but `runtime_co_stream_policy.py` currently exposes that policy as `read_only=true` and `enforced=false`; no production host-turn signal is wired.

Therefore:

- Do not create a third raw danmaku queue.
- Do not increase the exact fact buffer to solve aggregate room awareness.
- Do not make a new model call for every danmaku.
- Do not enable co-stream automatic speech merely because a room pulse exists.

## Target Context Packet

Only the context needed by the current turn should be projected:

```text
exact_fact: optional one-row tool result
room_pulse: bounded aggregate labels and counts
scene_state: optional short program-beat state
mode_direction: solo host or low-interrupt co-host authority
```

Sources must remain distinguishable:

- `exact_fact`: directly observed public danmaku.
- `room_pulse`: deterministic aggregate of sanitized recent candidates.
- `scene_state`: plugin inference from recent successful live routes and viewer response signals.
- `mode_direction`: configured mode plus a reliable host-turn signal when one exists.

Personality may change wording, but it must not change an exact fact or promote an inference into a fact.

## Slice Plan

### Slice 1: Read-only RoomPulse v0

Implemented in `modules/live_events/room_pulse.py` as a pure collaborator over the existing 45-second candidate window. It creates no timer, queue, model call, network request, or persistent record. The snapshot contains only bounded, deterministic fields:

- candidate count and unique-viewer count;
- low-value ratio;
- question and reaction pressure;
- coarse activity band: `quiet`, `steady`, or `burst`;
- dominant existing room-topic key and its support count;
- optional repeated short signal when repetition is directly observed.

The snapshot is exposed through privacy-safe `live_events.status()` fields and reused by the independent Slice 2 renderer. It does not alter selection, active-engagement scheduling, or output frequency. Dynamic `topic:<chat-derived text>` keys are projected as `other_topic`, repeated-signal status contains only `reaction` / `content` plus unique-viewer support, and no representative text is exposed in status.

Activity uses the newest 10 seconds inside the retained window: zero or one candidate is `quiet`, two through four is `steady`, and five or more is `burst`. Question and reaction pressure use distinct supporting viewers: zero is `none`, one or two is `low`, and three or more is `high`. Repetition requires at least two distinct viewers, so one-viewer spam cannot create a repeated signal.

### Slice 2: Compact Prompt Projection

Implemented in `modules/live_events/room_pulse_prompt.py` and consumed through the existing `live_events_context_block()` hook. One compact RoomPulse block may be added only to an already scheduled viewer or support response. It has a hard 240-character budget, contains at most one sanitized representative example, and adds no extra LLM call.

The renderer requires evidence from at least two distinct viewers plus a supported dominant theme, repeated signal, question cluster, reaction cluster, or sufficiently active window. It returns an empty block when evidence is weak, Safety Guard is not running, or the live queue is near its configured limit. Support-event labels are not treated as danmaku. Repeated content is shown only when it aligns with the dominant theme, preventing unrelated jokes from being mixed into the room summary.

`danmaku_response` consolidates this projection with the previous room-context section rather than appending both. If the pulse is omitted, the old bounded room context remains as a compatibility fallback. Runtime status records only use/omit counts, rendered character count, and a stable reason code; it never records the prompt or representative text.

Mode use differs:

- `solo_stream`: the pulse may help the existing response or active hosting beat acknowledge a shared joke, question cluster, or room reaction.
- `co_stream`: the pulse is advisory only. It cannot grant speaking authority, override the current human speaker, or schedule a new turn.

This verification slice wires only the existing selected viewer/support response paths. Extending the projection to idle hosting or active engagement requires separate live evidence and is intentionally deferred.

### Slice 3: SceneState v0

Implemented in `modules/live_events/scene_state.py` as one session-local deterministic state object. It tracks only `setup`, `develop`, `viewer_choice`, `callback`, `close`, or `transition`, one allowlisted interaction-shape key, bounded counters, and monotonic update time. It never stores transcript text, output text, topic titles, nicknames, or UIDs.

SceneState observes the existing privacy-safe `result` event and advances only for actually `pushed` solo-stream results. A successful warmup, idle beat, or active-engagement beat starts a scene. The existing `active_hook_answer` signal may move the current selected viewer response into `callback`; after that response succeeds, the scene moves to `close`. Ordinary viewer replies develop the beat for at most three successful turns. The state expires lazily after 120 seconds and clears on disconnect, room switch, reconnect, teardown, reset, or live-mode change.

The scene renderer assists only already selected solo-stream viewer responses. Its block is capped at 160 characters and is consolidated with the existing RoomPulse block under a 400-character total. It cannot schedule a turn, change selection, consume a support event, or run in `co_stream`. Failed, skipped, and `dry_run` results do not advance production scene state.

### Slice 4A: Plugin-only Co-stream Passive Context

Implemented without host/core changes. Ordinary co-stream danmaku no longer requests an immediate selected reply. The fixed three-entry session tail and latest two provider-verified support facts are formatted as one hidden `ai_behavior="read"` snapshot. The tail uses stable `latest / previous / the one before that` positions rather than a moving age and is replaced only by newer danmaku or session reset. After 45 seconds, same-key replacement removes volatile support facts but retains the bounded positional tail; a support-only snapshot still expires normally.

Support behavior is mode- and tier-aware:

- `solo_stream`: existing proactive selected danmaku and support behavior is unchanged;
- `co_stream` ordinary danmaku: passive snapshot only;
- `co_stream` light/medium support: passive verified fact only;
- `co_stream` high/milestone support: at most one active scheduler request plus a passive shadow marked as requested, never as audibly completed.

The snapshot marks viewer text as untrusted data, forbids invented quotes and duplicate thanks, and answers latest/previous questions from its explicit position labels without a tool call. Each visible row is capped at 48 characters; truncation receives an ellipsis and cannot be completed from memory. It adds no model call, durable storage, dependency, or main-program change. Because host `read` has no native per-message expiry, replacement is approximated with the same key; already-consumed text may remain in host conversation history.

### Slice 4B: Co-stream Direction

Do not enforce `LiveInteractionPolicy` until a production host-turn provider has a reliability contract, stale-signal handling, fallback behavior, and real-device evidence. Unknown or degraded host-turn state must remain conservative. RoomPulse cannot substitute for host-turn detection.

## Ownership And Contracts

- `modules/live_events/room_topic.py`: existing sanitized 45-second candidate and theme owner.
- `modules/live_events/room_pulse.py`: pure aggregate projection; no timers, network, output, persistence, or raw payload ownership.
- `modules/live_events/room_pulse_prompt.py`: pure bounded renderer; no scheduling, model, queue, or output ownership.
- `modules/live_events/scene_state.py`: solo-only bounded state machine derived from privacy-safe successful result events; no transcript, scheduling, model, persistence, or output ownership.
- `modules/live_events/ambient_context.py`: bounded renderer and verified support tail for co-stream passive context; no provider raw packets, persistence, or direct plugin output.
- `modules/live_events/module.py`: owns collaborator lifecycle and privacy-safe status projection.
- `modules/danmaku_response/module.py`: existing selected-response consumer; consolidates the pulse with its previous room context.
- `core/live_hosting_director.py` and active-engagement helpers: existing scene producers through successful results; they are not modified and do not consume SceneState in Slice 3.
- `core/host_turn.py` and `core/live_interaction_policy.py`: co-stream signal and decision contracts; remain read-only until Slice 4.
- `core/pipeline.py` and `core/safety_guard.py`: unchanged mandatory active-output path.
- `adapters/neko_dispatcher.py`: owns the hidden `read` push, role/session coalescing key, and passive metadata boundary.

`RoomPulse` must consume fields already sanitized by the live-event provider helpers. It must not read provider raw packets directly or write viewer, audit, credential, or long-term memory stores.

## Privacy And Safety

- No raw provider payload, credential, cookie, token, avatar bytes/base64, or query text may enter RoomPulse.
- Runtime status and audit may expose counts, ratios, coarse labels, and stable reason codes only. Do not expose representative danmaku text in status or audit.
- Prompt projection may use only the already-sanitized bounded examples owned by `RoomTopicContext`, at most one per rendered block.
- SceneState status and prompt may expose only an allowlisted phase and interaction-shape key. They must not copy result output, topic title, hook, viewer text, UID, or nickname.
- A pulse or scene inference never bypasses selection, pipeline, Safety Guard, output contracts, or dispatcher.
- Reset must clear all session-local context.

## Decision Points

The following decisions require maintainer approval before their corresponding runtime slice is implemented.

| Decision | Options and cost | Recommended option | Rollout / rollback |
|---|---|---|---|
| Candidate storage | Reuse current 45s/80-candidate room-topic window, or add a separate queue with duplicate memory and lifecycle cost | Reuse the existing window; add no queue and no retention increase | Slice 1 can be removed without changing recent-chat facts or event selection |
| Computation trigger | Compute on every ingest, on a timer, or lazily on status/prompt request | Reuse the current room-topic classification result and derive the extra pulse fields lazily, with no timer or background task | Degrade to an empty snapshot if input is unavailable; target incremental synthetic p95 below 1 ms and measure before prompt rollout |
| Initial signals | Rules-first counts/labels, or semantic clustering/sentiment through an extra model | Rules-first activity, repetition, question/reaction pressure, and existing theme keys | No network/model/dependency cost; uncertain signals are omitted rather than guessed |
| Prompt exposure | No prompt, bounded projection, or inject raw recent chat | Slice 2 uses one block capped at 240 characters on an already scheduled turn | One independent renderer can be disabled/removed without affecting pulse computation or exact recall |
| Token budget | Unbounded theme/examples, current room-topic block plus pulse, or one consolidated compact block | Consolidate rather than append; `danmaku_response` uses one room-context section | Track rendered character count; empty projection is the pressure/failure fallback |
| Solo behavior | Advisory reply context only, or immediately influence automatic scheduling | Start advisory-only; scheduling changes require separate live evidence | Roll back the consumer while retaining read-only metrics |
| Co-stream behavior | Enforce with unknown host turn, or remain read-only until a reliable provider exists | Keep `enforced=false`; RoomPulse never grants L3 speaking authority | Existing conservative downgrade remains the fallback |
| Scene memory | Transcript/RAG, model-authored summaries, or one bounded deterministic state object | Slice 3 uses one runtime-only object, three viewer turns, 120-second lazy expiry, no persistence, and no transcript | Reset on all session/mode boundaries; remove the prompt consumer to revert behavior |
| UI surface | Add controls now, display read-only diagnostics later, or no UI | No new UI in Slice 1; use tests/status evidence first | Avoids eight-locale and panel compatibility cost until product value is proven |

The user approved the recommended Slice 1, Slice 2, and Slice 3 verification options on 2026-07-21. Slice 4 co-stream enforcement remains open.

Expected Slice 1 and Slice 2 cost budget:

- Memory: no additional candidate storage; one small immutable snapshot at most.
- CPU: bounded O(80) local work only when requested; no timer and no per-message model call.
- Network/model: zero.
- Disk/IO: zero.
- Prompt tokens: zero for status reads; at most 240 characters on an eligible, already scheduled response.
- Dependencies: zero.
- Architecture: one pure collaborator plus protected `live_events` wiring and tests.

Additional Slice 3 cost budget:

- Memory: one fixed-size state object; no event list, transcript, or raw payload copy.
- CPU: constant-time result transition and prompt rendering; no timer or background scan.
- Network/model/disk/dependencies: zero.
- Prompt: at most 160 additional characters, with RoomPulse plus SceneState capped at 400 characters total.
- Runtime behavior: content guidance on an already selected solo viewer response only; zero additional turns.

## Local Preparation Evidence

On 2026-07-21, a local synthetic run filled the existing `RoomTopicContext` with 80 candidates from 20 synthetic viewers. Over 1,000 repeated builds, the current `_build_context()` path averaged about 3.62 ms per build on this machine. The rendered advisory block reached 1,959 characters with three themes and ten low-quality candidates.

This is a development microbenchmark, not a latency guarantee. It supports two implementation constraints:

- RoomPulse should reuse the current classification pass rather than scan and classify the same candidates independently.
- Slice 2 must replace/consolidate the current room-topic rendering instead of appending another prompt section. Prompt length is the more material cost than bounded local CPU in this sample.

After Slice 1 implementation, a second synthetic run used 80 candidates, 20 viewers, 20 low-value reactions, and three ordinary themes. Across 10,000 pulse-only projections, mean time was about 0.0049 ms and p95 about 0.0051 ms. Across 1,000 complete `RoomTopicContext.status()` builds, mean time was about 2.96 ms and p95 about 3.37 ms. These development measurements satisfy the incremental-pulse target while confirming that existing theme classification, not the pulse projection, remains the dominant local cost.

After Slice 2 alignment hardening, a third synthetic run used the full 80-candidate window and 20 viewers. Across 2,000 complete prompt projections, mean time was about 0.95 ms, p95 about 1.11 ms, and maximum about 2.42 ms. The rendered block was 229 characters. This is still a development microbenchmark rather than a latency guarantee; it demonstrates bounded local work and no network/model cost.

After Slice 3 implementation, 20,000 repeated SceneState prompt projections averaged about 0.0024 ms with p95 about 0.0027 ms and a maximum of about 0.060 ms. The longest exercised block was 156 characters. This is a development microbenchmark, not a latency guarantee.

After Slice 4A implementation, a worst-normal bounded sample with three chat rows and two support facts rendered 454 characters. Across 100,000 local renders it averaged about 4.55 microseconds per render. A `tracemalloc` sample of 1,000 independent contexts with two unique support facts estimated about 2.1 KiB of incremental Python allocations per context. Debounce caps passive snapshot dispatch attempts at one per second; same-key host coalescing keeps only the latest pending snapshot. These measurements exclude message-plane IPC and model token processing and are development evidence, not latency or memory guarantees.

## Tests And Observability

Slices 1 through 3 must cover:

- empty, quiet, steady, and burst windows;
- repeated reactions versus genuine repeated content;
- unique-viewer counting and one-viewer spam resistance;
- question/reaction pressure boundaries;
- invalid timestamps and session reset;
- deterministic bounded output with 80 candidates;
- no raw text in status/audit projection;
- hard 240-character prompt limit, weak-evidence and safety/queue-pressure omission;
- one representative at most, dominant-theme alignment, support-label exclusion, and redaction preservation;
- no change to selection winner, scheduling, output frequency, or dispatcher calls;
- successful-result-only scene transitions, explicit hook-answer callback, three-turn and 120-second bounds;
- solo-only use, support/co-stream exclusion, mode/session reset, and no transcript/status leakage;
- combined RoomPulse plus SceneState prompt length at or below 400 characters;
- a synthetic performance check recorded as evidence, not a timing-fragile unit assertion.

If runtime status fields are added, update `docs/runtime-observability.md` with stable names and meaning before implementation is considered complete.

Required code gates for any Python implementation:

```powershell
uv run pytest plugin/plugins/neko_live/tests -q
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_live
```

## External References

- [AITuber OnAir](https://github.com/shinshin86/aituber-onair): rules-first comment filtering, ranking, ignored-comment summary, compact agent context, and viewer safety/answer memory.
- [Proact-VL](https://proact-vl.github.io/): proactive response timing for solo and multi-speaker commentary.
- [Streamer.bot action queues](https://docs.streamer.bot/guide/core/actions): trigger, queue, serialization, and execution-history reference for later direction scheduling.
- [Response-worthy live chat selection](https://aclanthology.org/2024.sigdial-1.16.pdf): filter/rate/select workflow with an explicit no-reply outcome.
- [Waterfall of Text](https://cir.nii.ac.jp/crid/1390869987442204544): evidence that high-density live chat contains repeated short reactions and that streamer discourse drives room activity.
- [Twitch interaction rituals](https://www.tandfonline.com/doi/abs/10.1080/1369118X.2021.1913211): repeated jokes, synchronized reactions, and shared conventions as community interaction.
