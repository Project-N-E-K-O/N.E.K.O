# NEKO Live Independent Mode Product Plan

> Updated: 2026-06-26
>
> This document is the canonical product plan for Independent Mode. It describes product priorities, MVP scope, validation sequence, and non-goals. It does not define internal architecture, runtime observability, or implementation details.

## Product Thesis

NEKO Live is the live-scene capability plugin for the N.E.K.O main persona. The next product question is:

> Can NEKO independently sustain a 30-minute livestream in a 10-50 viewer room without awkward silence?

The current gap is not live ingest or output plumbing. The main gap is **live pacing**, with one required conversation bridge before broader proactive hosting:

- when NEKO should speak;
- what NEKO should say;
- how often NEKO should speak;
- how NEKO avoids awkward self-talk;
- how NEKO invites viewers to reply.
- how NEKO keeps replying to the same viewer after the first appearance roast.

Product success is not measured by the number of supported event types. It is measured by whether a real small streamer can trust NEKO to keep a 30-minute room alive.

## Current Priority

Independent Mode is the current product priority.

Companion Mode remains part of NEKO Live, but it is not the current stage target. Gift, Super Chat, and Guard-specific behavior are enhancements; they are not prerequisites for proving Independent Mode.

The next phase should validate two promises first:

1. The streamer can safely hand the room to NEKO.
2. NEKO does not let a low-danmaku room fall into dead air.

## Current Implementation Status

Independent Mode is now past the first implementation and acceptance check and should move into controlled live-effect validation.

- Slice 1 base is landed: Live Status, preflight conclusion, and "why not speaking" status are available for streamer trust checks.
- Slice 2 base is landed: live state inference, manual Idle Hosting trigger, and automatic Idle Hosting trigger are available for solo-stream idle moments.
- Slice 4 base is landed: activity level gives the streamer a small quiet / standard / active pacing control instead of many parameters. It now controls both quiet/idle state thresholds and Idle Hosting minimum intervals.
- Danmaku Response transition slice is implemented in the current development branch: first appearance still uses `avatar_roast`; later ordinary danmaku from the same UID uses `danmaku_response` instead of being blocked by the first-appearance once gate.
- Active Engagement v0 is implemented as a solo-stream quiet-moment trigger with both automatic and manual paths. It is meant for controlled live-effect validation only: one small replyable topic, standard pacing around a 90-second minimum interval, active pacing around a 60-second minimum interval, and no Gift / SC / Guard coupling.
- Active Engagement v1 topic material now avoids stale or single-viewer-biased material: when recent useful danmaku material is older than the live-topic freshness window, or all comes from the same UID repeatedly, NEKO should fall back to neutral topics instead of turning old or single-viewer message streams into the whole room's topic.
- Next-test tuning now treats only real danmaku as viewer reply activity for idle detection, so entry / gift / SC / Guard health rows do not block cold-room hosting. Active Engagement fallback topics now bias toward concrete reply handles such as A/B choices, one-word answers, tiny stances, or small playful challenges.
- Active Engagement v1.15 adds lightweight topic profiling for recent danmaku and Bilibili public material. Obvious choice, challenge, tease, or mood titles now carry a preferred shape, fun axis, and reply affordance into the prompt instead of arriving as a raw title only.
- Live Director status is exposed in the dashboard to explain the next automatic speaking action: none, active engagement, or idle hosting, including whether it is eligible and how long it must wait.
- Solo stream readiness is exposed in the dashboard as a streamer-facing checklist. It aggregates preflight, test isolation, warmup, first-viewer roast, follow-up danmaku reply, light active topic, idle hosting, and pacing control into one readiness conclusion; it is not a separate output path or test backend.
- Warmup Hosting is implemented for solo-stream opening moments before any recent room activity exists. It gives NEKO an opening host beat so the first autonomous line does not sound like cold-room filler.
- The current validation target is not another event type. It is a controlled solo-stream validation with low danmaku, occasional danmaku, and no-danmaku moments.
- The next product decision should be based on controlled validation:
  - if NEKO is too quiet or too noisy, tune the quiet / standard / active pacing thresholds next;
  - if NEKO sounds generic or awkward, tune Idle Hosting wording first;
  - if the streamer cannot tell why NEKO is silent, refine Live Status before adding more behavior.
  - if Active Engagement feels too pushy, raise its minimum interval or turn it back into manual-only validation.

## Current UI Direction

The UI should keep the existing plugin-panel visual language: light gray page background, white cards, blue capsule tabs, status badges, and compact dashboard cards. Do not introduce a separate product shell, OBS dock layout, or a new visual system until the Independent Mode behavior is stable.

Live-time assumption: during a real stream, the streamer will not keep watching the plugin panel. The panel is a preflight, remote-control, emergency, and after-action review surface. It should not become the primary live experience or a dense operator dashboard that expects constant attention.

Inside the plugin-center hosted panel, the first viewport is limited. The console should prioritize streamer decisions over module inventory:

1. whether NEKO can stream now;
2. why NEKO is quiet;
3. what NEKO is likely to do next;
4. the smallest set of live actions: refresh, manual test, pause/resume, and pacing controls.

Module details, account setup, health rows, readiness checklist, and advanced diagnostics may remain in the same panel format, but they should sit below the first decision area or in the existing secondary tabs.

Future UI simplification should therefore remove live-time noise before adding more diagnostics. Keep the first screen focused on "can stream", "why quiet", "what NEKO will do next", and "safe controls"; move route traces, module inventories, and detailed review evidence into secondary or developer surfaces.

## Current Development Split

This section describes the product-stage split for Independent Mode work. General role ownership and Protected Module review rules remain in `development.md`.

### Live Director Track

The Live Director Track owns the main livestream experience:

- Independent Mode product direction;
- preflight and "why not speaking" clarity;
- Idle Hosting behavior;
- Pacing Control;
- NEKO live-scene persona consistency;
- real-stream validation and beta readiness.

This track decides why NEKO speaks, when NEKO speaks, how often NEKO speaks, and whether the result still feels like NEKO. It should stay tightly owned while Independent Mode is being validated.

### Event Module Track

The Event Module Track owns future extension modules:

- Gift signal slices;
- Super Chat signal slices;
- Guard signal slices;
- private-message slices;
- viewer profile extensions;
- contribution / watch-time signals;
- dashboard sub-status cards;
- fixtures, samples, module docs, and focused tests.

Event modules may contribute signals, context, display state, and priority hints. They must not directly control NEKO's main speaking rhythm.

### Contribution Boundary

New modules should answer:

- what happened;
- why the event may matter;
- what context the Live Director Track can use;
- what the streamer should be able to see.

New modules must not:

- bypass the main selection and pacing flow;
- directly force NEKO to speak;
- bypass pause, test mode, or safety behavior;
- redefine NEKO's fixed persona;
- introduce complex streamer-management or live-ops surfaces.

Recommended onboarding order for a new contributor is:

1. docs, fixtures, and samples;
2. read-only dashboard status;
3. Gift Signal Slice;
4. Super Chat Signal Slice;
5. Guard Signal Slice.

## Slice Order

### Slice 1: Live Status / Preflight / Why Not Speaking

Goal: make the streamer confident enough to hand the room to NEKO.

This slice must answer, in streamer language:

- can NEKO go live now;
- is NEKO in test mode;
- can NEKO see danmaku;
- can NEKO output;
- why NEKO is temporarily not speaking.

This is not the slice with the strongest show effect, but it should land first because it builds the trust foundation for Independent Mode.

### Slice 2: Idle Hosting

Goal: validate whether NEKO starts to feel like a livestream host.

This is the fastest slice for proving Independent Mode product value. In low-danmaku or no-danmaku moments, NEKO should make short, light hosting moves that prevent silence without becoming repetitive, pushy, or awkward.

Opening moments are handled separately by Warmup Hosting: when solo stream has just started and there is no recent room activity, NEKO should open the room instead of treating the room as already idle.

Idle Hosting should avoid:

- long monologues;
- repeated template sentences;
- calling attention to the lack of viewers;
- forcing viewers to interact;
- breaking NEKO's fixed persona.

Idle Hosting wording principles:

- say one short line, not a paragraph;
- throw a light topic, do not beg for comments;
- do not explain internal system state;
- do not pretend a viewer said something;
- do not say "why is nobody talking";
- keep the line in NEKO's main persona;
- leave room for viewers to answer.

Idle Hosting and Danmaku Response should use recent lightweight interaction context to avoid repeating the same opening, punchline shape, or host beat. This is not a long-term memory system; it is only a short live-room continuity aid for the current session.

### Transition Slice: Danmaku Response

Goal: let NEKO keep a normal conversation after the first appearance roast.

This slice exists because Independent Mode cannot rely on `avatar_roast` as a generic reply template. `avatar_roast` should remain the viewer's first-appearance moment: avatar, ID, and the first message. Later ordinary danmaku from the same UID should be answered by `danmaku_response`.

Danmaku Response should:

- answer the current danmaku, not re-roast the viewer's avatar or ID;
- keep one short line suitable for live TTS;
- preserve NEKO's fixed persona;
- work in both `solo_stream` and `co_stream`, with different interruption posture;
- keep test mode, pause, safety, pacing, and dispatcher behavior intact.

Danmaku Response should avoid:

- repeating first-appearance templates;
- treating every message like a new viewer entrance;
- generic customer-service replies;
- engagement bait before the viewer has actually offered a topic.
- reusing the same response shape from the immediately previous interaction.

This slice should be validated before Active Engagement. NEKO should first prove she can receive and continue audience conversation before she tries to proactively create topics.

### Slice 4: Pacing Control

Goal: prevent Idle Hosting from becoming spam or awkward chatter.

The streamer should not tune many parameters. Use a small number of simple live states first:

- quiet;
- standard;
- active.

Pacing Control is the safety valve for Independent Mode. It should keep NEKO from speaking too often, speaking too rarely, or interrupting useful audience interaction.

Current behavior: quiet waits longer before classifying the room as idle, uses a longer Idle Hosting interval, and biases Idle Hosting toward soft observations instead of direct questions. Active enters idle sooner, allows shorter Idle Hosting intervals, and may ask one specific low-pressure question. Standard keeps the middle baseline.

### Slice 3: Active Engagement

Goal: let NEKO proactively create moments viewers want to answer.

This slice has high value, but it is the most likely to fail. It should come after Idle Hosting and Pacing Control have been validated.

Current v0 scope: conservative auto trigger plus manual trigger, solo-stream quiet moments only. It should create one short, specific, low-pressure topic and still use the same test mode, pause, safety, pacing, and dispatcher behavior as every other speaking path. It must remain easy to tune down if live tests show generic or pushy wording.

Current v1 topic-material rule: meaningful recent danmaku can seed one small topic only while it is still fresh, successfully reached output or dry-run, and is not just the same event that already powered a first-appearance roast. Repeated useful messages from the same UID should not monopolize Active Engagement. If the only room material is stale, skipped / failed, already consumed by `avatar_roast`, or one viewer is the only recent source repeatedly, use Bilibili public material or the built-in neutral topic pool instead.

Failure shapes to avoid:

- sounding like customer support;
- sounding like a template host;
- asking generic low-energy questions;
- trying too hard;
- damaging the sense that NEKO is a consistent character.

## Independent Mode MVP

MVP must include:

- a clear Independent Mode entry;
- preflight check;
- "why not speaking" status;
- normal follow-up danmaku response after first appearance;
- Idle Hosting;
- basic pacing control;
- NEKO fixed-persona live-scene behavior;
- one-click pause / resume;
- clear test-mode indication.

MVP should include:

- activity level: quiet / standard / active;
- roast intensity: gentle / light tease / sharp roast;
- simple idle frequency levels;
- short status conclusion: ready to stream / test only / temporarily not speaking / cannot stream.

## Out of Scope for MVP

The MVP should not include:

- Gift / Super Chat / Guard-specific complex behavior;
- multiple persona presets;
- complex parameter panels;
- streamer management backend;
- live-ops SaaS features;
- long-term fan operation systems;
- advanced analytics.

## Verification Plan

### 1. Internal 30-Minute Simulation

Goal: verify whether NEKO falls into dead air or awkward chatter.

Scenarios:

- low danmaku;
- no danmaku;
- occasional danmaku.

Passing standard:

- no long dead silence;
- no obvious spam;
- idle lines do not become painfully repetitive.

Suggested observation sheet:

| Time | Room state | What NEKO did | Too quiet? | Too noisy? | Host-like? | Generic / awkward? | Viewer reply point |
|---|---|---|---|---|---|---|---|
| 00:00-05:00 | warm start |  |  |  |  |  |  |
| 05:00-10:00 | low danmaku |  |  |  |  |  |  |
| 10:00-15:00 | no danmaku |  |  |  |  |  |  |
| 15:00-20:00 | occasional danmaku |  |  |  |  |  |  |
| 20:00-25:00 | low danmaku |  |  |  |  |  |  |
| 25:00-30:00 | no danmaku |  |  |  |  |  |  |

Record only what affects the live feel. Do not turn this into an engineering trace; the question is whether the stream feels alive.

## Solo Stream Validation Checklist

Use this checklist before a controlled solo-stream test. It is for product validation, not debugging internals.

### Streamer trust

- The dashboard gives one clear conclusion: ready for dry-run test, ready for live test, switch to solo stream first, or live room not ready.
- The streamer can see whether `dry_run` is on before NEKO speaks.
- If NEKO is silent, the dashboard explains the visible reason without requiring module knowledge.
- Pause and resume are visible and easy to reach.

### Dead-air control

- NEKO can start with a warmup host line when solo stream has just begun.
- In no-danmaku moments, NEKO can fill silence with one short line instead of a long monologue.
- Idle Hosting avoids saying the room is empty or begging viewers to comment.
- The streamer can tell whether the next automatic action is warmup, active engagement, idle hosting, or none.

### Danmaku continuity

- First viewer appearance still feels like an entrance moment.
- Later danmaku from the same viewer gets a normal follow-up reply, not another avatar / ID roast.
- The panel copy makes it clear that "once per viewer" means first-appearance roast only.
- Recent results make it possible to see whether the route was `avatar_roast`, `danmaku_response`, `warmup_hosting`, `idle_hosting`, or `active_engagement`.

### Pacing safety

- Quiet / standard / active is understandable without explaining thresholds.
- NEKO does not speak again immediately after a recent output.
- Active Engagement stays conservative and does not feel like template hosting.
- If NEKO feels too noisy, the next tuning action is to lower activity level or raise intervals, not add more event types.

### Persona fit

- NEKO sounds like the same N.E.K.O persona in opening, replies, idle lines, and light active topics.
- Lines are short enough for live TTS.
- NEKO leaves viewers a natural reply point.
- Failed lines should be judged by live feel first: generic, pushy, repetitive, too quiet, or too noisy.

## Live Validation Record - 2026-06-24

Scope: one real Bilibili solo-stream validation run with `solo_stream`, `dry_run=false`, cleared viewer profiles, live danmaku, Active Engagement, and Idle Hosting enabled.

Validated:

- NEKO Live connected to a real Bilibili live room and stayed in `live=receiving`.
- `solo_stream` could run with real output after `dry_run` was turned off.
- Clearing `viewer_profiles.json` gave a clean test baseline; new viewers were persisted again during the run.
- Real danmaku was ingested and pushed to NEKO.
- Later danmaku from the same UID continued to produce output instead of being blocked by the first-appearance once gate.
- Active Engagement produced real output during quiet moments.
- Idle Hosting produced real output once (`idle_hosting -> pushed`), proving the cold-room path can speak in a real room.
- `cooldown`, `recently_spoke`, `quiet`, and `manual_paused` states were observable during the run.

Gift / fan-club / guard signal note:

- A fan-club medal event was observed as text similar to "sent 1 fan-club medal" and was pushed through the current `live_danmaku` path.
- Gift, fan-club, and guard events should be observable as signal labels before full Gift / SC / Guard behavior exists.
- This proves the ingest side can see the signal, but it is not yet a Gift module. Gift / SC / Guard should remain future event modules and should not be treated as Independent Mode prerequisites.

Product findings:

- Some replies were too long for live pacing. Danmaku replies, Active Engagement, and Idle Hosting should prefer short TTS-friendly lines.
- Active Engagement fired too soon after some danmaku replies, which made the experience feel like NEKO was repeating or continuing the same reply. Increase the minimum interval after recent danmaku output or make Active Engagement more conservative.
- Dashboard / monitoring still exposes ordinary live input as `live_danmaku`; it does not clearly distinguish `avatar_roast` from `danmaku_response`. This made validation harder even though the user-visible reply path worked.
- Natural long-idle observation was limited because viewers kept entering or sending danmaku. The idle path was validated, but longer no-danmaku live feel still needs a quieter test window.
- Observed logs showed low response latency, but streamer-perceived delay still needs separate timing because the live feel depends on the full viewer-message-to-NEKO-speech path.

Implemented before the next live test (offline verified; live feel still needs the next run):

1. Prompt Context Isolation: recent context should only prevent repetition. It must not make NEKO continue the previous reply, inherit the previous topic, reuse the previous joke shape, or treat Active Engagement as the current danmaku context. The current danmaku is always the primary target.
2. Live Mode Prompt Polish: split prompt behavior by `live_mode`.
   - `solo_stream`: NEKO is the only on-stage host. She receives viewers, replies to danmaku, controls pacing, and fills dead air.
   - `co_stream`: the human streamer is the main host. NEKO is a low-interrupt partner who catches jokes, supports the streamer, and avoids taking over the room.
   - Streamer relationship labels must come from the current user/profile memory. Do not hard-code labels such as "older brother" or "owner"; if no label is available, use a neutral label or avoid naming the streamer.
3. Reply Length Contract: the prompt contract is implemented offline for first-appearance roast, follow-up danmaku, warmup hosting, idle hosting, and active engagement. All speaking paths share the hard short-TTS limit: one sentence, no paragraph, at most 14 Chinese characters or 8 English words, with no explanation, setup, comma-chained clauses, or second sentence. `avatar_roast` and `danmaku_response` use reply rules: short danmaku should get even shorter replies and should not add a follow-up question unless the current danmaku asks one. `warmup_hosting`, `idle_hosting`, and `active_engagement` use host rules: they may include one concrete low-pressure reply hook, but must not expand into a host script or audience survey. Live feel still needs the next run.
4. Active Engagement Pacing: make automatic Active Engagement more conservative after recent danmaku replies. It should not fire in `engaged` state and should wait longer after successful live danmaku output.
5. Result Labels: validation and dashboard output should distinguish `avatar_roast`, `danmaku_response`, `warmup_hosting`, `idle_hosting`, `active_engagement`, and gift/fan-club/guard signal capture instead of showing all ordinary live input as `live_danmaku`.
6. Warmup Hosting Testability: the next live test should make the opening moment observable so the team can tell whether `warmup_hosting` fired, whether it spoke only one natural opening line, and whether it was not mistaken for idle hosting.
7. Gift Signal v0: if a gift, fan-club medal, or guard event appears again, capture it as a gift/fan-club/guard signal. Do not build full Gift / SC / Guard behavior before the live pacing issues are fixed.

## Second Long Live Validation - 2026-06-25

Scope: roughly one hour of real Bilibili solo-stream validation after the offline prompt and pacing fixes above. The run used `solo_stream`, `dry_run=false`, real live danmaku, follow-up danmaku response, Idle Hosting, Active Engagement, and live monitoring.

Validated:

- The main live chain still works in real output mode: live connection stayed receiving, `solo_stream` was active, and normal danmaku produced pushed results.
- `danmaku_response` was used for later ordinary danmaku from already-seen UIDs; the once-per-UID gate did not block follow-up conversation.
- Plugin-side latency was usually low (`0-1000ms` in recent results), so the main perceived delay is not primarily live ingest or module routing.
- Reply length improved compared with the first long live run; most observed `send_lanlan_response` lengths stayed around 17-22, though outliers still occurred.

New findings:

- `voice playback gate watchdog` repeated several times. This is now the top stability blocker because it can make NEKO feel delayed or stuck even when plugin-side routing is fast.
- Warthunder proactive messages appeared during the run and polluted the solo-stream validation. Next controlled live tests should isolate NEKO Live from unrelated proactive plugins.
- Follow-up danmaku sometimes still felt like repeated avatar roasting. The cause is not `roast_once_per_uid`: routing had moved to `danmaku_response`, but dispatcher still attached avatar image parts whenever `identity.avatar_bytes` existed. This lets the model keep looking at the same avatar during ordinary follow-up chat.
- Clearing viewer profiles is useful for first-appearance baseline tests, but it can make many new UIDs trigger `avatar_roast` in a short window. Product judgment: in solo stream, first appearance should prioritize replying to the first danmaku, with avatar / ID as a small accent instead of the main topic.

Next implementation focus before another long live run:

1. Voice Playback Gate: current development package makes the browser open the backend playback gate as soon as audio has drained, even if the later turn-completion bookkeeping is delayed or missing. The next live run must verify that repeated `voice playback gate watchdog` stalls disappear.
2. Avatar Image Scope: current development package makes dispatcher attach avatar image parts only for explicit visual opt-in requests. `avatar_roast` owns the first-appearance visual input; `danmaku_response`, `idle_hosting`, `active_engagement`, and `warmup_hosting` are text-only by default.
3. Test Isolation: current development package treats `live_enabled=false` as a runtime preflight blocker even when a stale live connection snapshot still looks connected. Automatic `warmup_hosting`, `active_engagement`, and `idle_hosting` should not enter the pipeline or write recent results while NEKO Live is disabled. Controlled solo-stream validations should still keep unrelated proactive plugins, especially Warthunder, out of the test window.

Do not redo already-landed prompt work as if it were missing. Prompt context isolation, live-mode prompt split, shorter reply contract, conservative Active Engagement pacing, result labels, warmup testability, Gift Signal v0, Avatar Image Scope, the Playback Gate source-level fix, and the `live_enabled` runtime preflight blocker are already implemented in the current development line. The next unresolved blocker is live verification that playback watchdog stalls no longer recur under a clean controlled test window.

## Third Long Live Validation - 2026-06-25

Scope: another real-output Bilibili solo-stream validation after the playback-gate, avatar-image-scope, short-reply, and entrance-pacing fixes. The run used `solo_stream`, `dry_run=false`, real live danmaku, follow-up danmaku response, Active Engagement, and live monitoring.

Validated:

- The main live chain stayed usable after restart: live connection was receiving, `ready_to_stream` stayed true, and normal danmaku continued to produce pushed results.
- `danmaku_response` handled follow-up danmaku reliably. Recent results showed mostly `danmaku_response -> pushed` with plugin-side latency around `0-1000ms`.
- Reply length was much more stable than the earlier long run. Observed `send_lanlan_response` lengths were mostly short (`7-8` in backend log markers).
- Active Engagement did reach the pipeline and appeared in recent results as `active_engagement -> pushed`, so the route is wired.

New findings:

- Active Engagement is not yet strong enough as a live product behavior. It triggered, but it felt too infrequent and the topic quality was weak: not enough concrete choice, stance, joke, or hook for viewers to answer.
- Idle Hosting did not get a meaningful validation in this run. The room stayed mostly `engaged` or `quiet`; automatic idle hosting requires the `idle` state. The current development package now lowers `standard` pacing to about 120 seconds of no recent viewer activity before entering `idle`, so the next test can observe cold-room hosting without waiting three minutes.
- Active Engagement can also prevent Idle Hosting from being observed: it runs in the `quiet` window before `idle_hosting`, and NEKO's own output may refresh runtime activity enough that the room never reaches the `idle` threshold during a normal test.
- The next problem is therefore no longer basic chain correctness. It is Independent Mode pacing: separating viewer activity from NEKO output, making Active Engagement more replyable, and making true no-danmaku windows reach Idle Hosting predictably.

Next implementation focus before another long live run:

1. Independent Pacing v1: implemented. `live_state` now distinguishes recent viewer activity from recent NEKO output, so NEKO's own proactive speech does not permanently prevent idle detection.
2. Active Engagement v1: implemented as a first pass. Active topics now carry lightweight topic material, rotate topic shape / title, and prefer meaningful recent live-room danmaku before falling back to filtered public Bilibili trending material or built-in topics. The prompt also expands each topic shape into a concrete example pattern and viewer reply affordance, so NEKO gets a replyable structure without hard-coded lines. Active pacing now exposes separate minimum-interval and post-danmaku waits; `standard` pacing uses an approximately 90-second minimum interval, `active` uses approximately 60 seconds, and both wait after a danmaku reply to avoid talking over it while staying observable in the next live test. The fallback topic pool now uses at least ten concrete low-pressure hooks, natural per-topic shapes, and a longer recent-topic de-dup window, so low-danmaku tests are less likely to hear the same generic prompt loop.
3. Idle Hosting Validation: implemented at the state / prompt contract level; still requires the next controlled no-danmaku live window to validate actual feel.
4. Keep Gift / SC / Guard, private messages, automation, and major UI redesign out of this package.

## Fourth Long Live Validation - 2026-06-26

Scope: real-output Bilibili solo-stream validation after Independent Pacing v1 and Active Engagement v1. The run used `solo_stream`, `dry_run=false`, cleared viewer profiles, live danmaku, automatic Active Engagement, automatic Idle Hosting, hosted-ui context checks, and backend log tail monitoring.

Validated:

- The main live chain stayed usable: live connection was receiving, `ready_to_stream` stayed true, `safety` stayed running, and no repeated TTS / playback watchdog / Traceback failure stopped the run.
- `avatar_roast` still handled first appearances, while same-UID follow-up danmaku routed through `danmaku_response`.
- Viewer profiles persisted again after clearing. New viewer records were written and first-appearance state was observable.
- `danmaku_response`, `idle_hosting`, and `active_engagement` all produced `pushed` results in the same long run.
- Active Engagement became observable in quiet windows, and Idle Hosting also appeared in no-danmaku / low-danmaku windows.
- Reply length was mostly stable after the short-reply contract. Most backend `send_lanlan_response` markers were short, though earlier outliers around `43` and `94` chars remain worth monitoring.
- One transient `prompt_ephemeral` API connection error recovered on retry and did not stop the live run.

Product findings:

- Active Engagement is now wired and observable, but topic variety is still not good enough. It can overuse `recent_danmaku`, especially expression / emote material, so viewers may feel NEKO is circling the same recent joke.
- Some viewers perceived repetition even when routing was technically correct. This is a live-feel issue: topic source, intent, and phrase-shape diversity matter more than adding new event types.
- `idle_hosting` and `active_engagement` can coexist, but the handoff still needs review. Active topics should not consume the same quiet window so often that Idle Hosting loses its distinct dead-air coverage role.
- Danmaku can mention other viewers. NEKO should distinguish NEKO-directed mentions from viewer-to-viewer mentions, avoid treating every `@` as addressed to herself, and avoid interrupting viewer-to-viewer chat unless there is a clear host-worthy hook.
- Result snapshots can still mislead review: a just-pushed `avatar_roast` may show the pre-update profile snapshot even though the persisted viewer profile has already recorded the first roast.
- The next priority is not Gift / SC / Guard. It is live-feel polish: Active Engagement topic diversity, mention parsing, anti-repetition signals, and clearer result/profile snapshot semantics.

Live Feel Pack v1.5 implemented before the next long live run:

1. Active Engagement Diversity: recent danmaku topics no longer dominate after a recent-danmaku source streak. NEKO should rotate back to Bilibili trend material or fallback NEKO-owned hooks instead of circling the same recent emote / phrase.
2. Mention Parsing v1: viewer-to-viewer `@` danmaku is filtered out as Active Engagement topic material. NEKO-directed mentions, viewer-to-viewer mentions, and ordinary danmaku can now be distinguished during review.
3. Repetition Guard v1: Active Engagement avoids repeating the same topic shape / intent streak, so consecutive proactive beats should vary between quick vote, tiny answer, tease-back, and small stance shapes. When the guard changes a topic shape, the prompt hint is also realigned to the new shape so NEKO does not receive contradictory instructions such as a non-A/B shape with an old A/B hint.
4. Result Snapshot Clarity: successful first-appearance `avatar_roast` updates the public result profile snapshot after `viewer_profile.mark_roasted`, so `roast_count` does not look missing immediately after first roast.
5. Monitor visibility: `monitor_live.ps1` now reports viewer-to-viewer mention filtering, recent-danmaku source streak filtering, and shape-guard hits in the same snapshot as the existing topic-source / intent / repeat signals.

Live Feel Pack v1.15 implemented before the next long live run:

1. Active Topic Profile: recent danmaku and Bilibili public topic titles are now profiled before prompt construction. Choice-like titles become A/B choices, challenge-like titles become tiny challenges, tease-like titles become playful teases, and mood-like titles become small NEKO stances.
2. Replyable External Material: public topic material now carries `preferred_shape`, `fun_axis`, `reply_affordance`, and hint when the title has a clear interaction pattern. This keeps Bilibili trend material from becoming a generic "what should we talk about" prompt.
3. Rotation Fallback: unclear titles still use the existing shape rotation, so the profiler does not force every external topic into the same format.

Live Feel Pack v1.16 implemented before the next long live run:

1. NEKO-owned Mini Columns: idle host beats and active engagement topics now carry `live_column`, such as `NEKO micro poll`, `NEKO tiny verdict`, `NEKO tiny radio`, `NEKO room thermometer`, and `NEKO one-word callback`.
2. Format, Not Script: `live_column` tells the prompt which tiny live format to use, but NEKO should not formally announce a segment or turn it into a long host script.
3. Review Visibility: `live_column` is exposed in recent results and recent interaction context so the next live review can judge whether NEKO is varying the hosting format instead of only checking route names.

Live Feel Pack v1.17 implemented before the next long live run:

1. Larger Content Pool: Idle Hosting now has 32 maintained JSON host beats and Active Engagement has at least 36 fallback topics, so a long quiet room has more material before it loops.
2. More NEKO-shaped Hooks: new material adds tiny court, tail-state poll, desk guardian poll, two-character password, room filter, and small weather-vote formats. These are meant to feel like NEKO's own live-room habits, not generic host scripts.
3. Still Low-pressure: every new item keeps one concrete reply path and must avoid "please interact / send danmaku / what should we talk about" bait.

Live Feel Pack v1.18 implemented before the next long live run:

1. Idle Stage Progression: repeated idle beats now prefer `settle -> column -> callback`, so a quiet room should move from light observation to a tiny show format and then to a low-pressure viewer callback instead of feeling like random material draws.
2. Active Topic Packs: Active Engagement topics now expose `topic_pack`, such as `micro_poll`, `neko_verdict`, `room_mood`, `room_observation`, `viewer_callback`, or `micro_challenge`.
3. Reviewable Program Rhythm: `idle_stage` and `topic_pack` are prompt/review metadata, not new streamer controls. They let the next live review judge whether NEKO has a small program rhythm before changing frequency again.

Live Feel Pack v1.19 implemented before handoff:

1. Maintainer-owned Meme Knowledge: `data/meme_knowledge.json` is the first-version hot-meme knowledge base, currently 36 entries. It is loaded by `core/meme_knowledge.py` and can provide optional prompt hints for `danmaku_response` and idle `meme_query` seasoning.
2. Maintainer-owned Idle Beats: `data/idle_hosting_beats.json` is the maintained idle-hosting catalog, currently 32 entries. Legacy Python host catalog groups remain fallback only.
3. Plugin Boundary: meme hints and idle beats are plugin-owned prompt material. They do not fetch online trend data, do not require host/core hooks, and must not override the current danmaku, safety, dry_run, pacing, or dispatcher path.
4. Handoff Focus: the next maintainer should review and tune the JSON material from real live runs, then continue UI/profile governance. Do not treat v1.19 as a reason to change Douyin transport, trigger frequency, or host/core prompt plumbing.

Danmaku Response Quality v2 implemented before the next long live run:

1. Current-message Profile: follow-up danmaku prompts now expose `danmaku_profile` so NEKO can tell whether the current line is a viewer-to-viewer mention, a question, a tiny reaction, a short line, or ordinary chat.
2. Better Reply Shape: short reactions should stay tiny, questions should be answered directly first, and ordinary chat should answer the current meaning instead of summarizing same-viewer history.
3. Mention Boundary: `@` messages aimed at other viewers are not treated as calls to NEKO; `@NEKO` / `@猫猫` remains a normal current-message target.
4. No New Route: this is a response-quality contract for `danmaku_response`, not a new event type or a bypass around safety, dry_run, dispatcher, cooldown, or pacing.

### Live Validation - 2026-06-28

Scope: real-output Bilibili solo-stream validation after Live Feel Pack v1.6. The run used `solo_stream`, `dry_run=false`, real live danmaku, automatic Idle Hosting, automatic Active Engagement, hosted-ui context checks, and `monitor_live.ps1` with backend log inspection.

Validated:

- The solo-stream live chain stayed stable: the final snapshot was `ready_to_stream`, `live=receiving`, `safety=running`, and recent results contained only `pushed` output.
- The recent 30-result window included `avatar_roast`, `danmaku_response`, `idle_hosting`, and `active_engagement`, so the room was no longer dominated by first-appearance roast.
- `avatar_roast` was about 14% of the recent window, which is a healthier ratio for Independent Mode than earlier tests where first-appearance roast could swallow the live feel.
- Gift / SC / Guard did not appear as ordinary danmaku output in the final monitor window.

Product findings:

- The next weakness is not routing correctness. It is no-danmaku entertainment quality.
- When nobody sends danmaku, NEKO can now speak, but some idle / active lines still feel bland: too close to "what should we talk about" or generic host prompting, and not enough like a cat主播 naturally finding a fun tiny beat.
- Active topics need a stronger reply affordance. A viewer should be able to answer with one word, one side of an A/B choice, a quick tease-back, or a tiny stance.
- Idle Hosting should cover dead air without sounding like she is begging for comments. It should feel like a short atmosphere beat, small observation, light tease, or NEKO mood beat.
- Long-reply risk remains visible. The final monitor window reported long-reply alerts across `danmaku_response`, `idle_hosting`, and `active_engagement`, even though the latest backend `send_lanlan_response` marker was short.

Next implementation focus before another long live run:

1. Idle / Active Content Quality: improve the topic and host-beat material so no-danmaku windows feel less boring without increasing speaking frequency.
2. Strong Reply Hook Contract: every proactive line should expose exactly one easy reply path, such as A/B, one-word answer, light stand-taking, or a tease-back.
3. No Generic Host Prompt: continue rejecting "everyone interact", "send danmaku", "anyone here", "what should we talk about", and similar customer-service-like prompts.
4. Short Line Enforcement Review: keep tracking per-route long replies and prioritize whichever route still creates multi-clause or paragraph-like output.
5. Idle-to-Active Balance: if `idle_hosting` covers several dead-air beats and no viewer replies, `active_engagement` may take over once, but it must introduce a concrete playful hook rather than another generic question.

### Previous Live Prep Pack - 2026-06-25

This package prepared the 2026-06-26 long solo-stream validation and remains useful as context for what was under test:

1. Avatar Image Scope Fix: `avatar_roast` and explicit visual demos may carry avatar image input; `danmaku_response`, `idle_hosting`, `active_engagement`, and `warmup_hosting` stay text-only by default.
   In `solo_stream`, first-appearance `avatar_roast` should answer the viewer's current danmaku first, then use avatar / nickname only as a small entrance accent.
2. Follow-up Danmaku Stability: later danmaku should answer the current message in a short line and must not become another avatar / ID roast.
3. Entrance Pacing: in `solo_stream`, true first-appearance `avatar_roast` should not fire for every new UID in a burst. If a new viewer sends danmaku during the entrance pacing window, answer the current danmaku through `danmaku_response` instead of doing another avatar / ID roast.
4. Idle Hosting Polish: no-danmaku coverage should stay short, specific, and non-template, with no customer-service-style interaction begging. The current development line now rotates lightweight `host_beat` material for idle hosting so consecutive cold-room lines can vary between soft observation, tiny choice, light tease, and small NEKO mood shapes; the next live run should judge whether this feels natural rather than scheduled.
5. Playback Gate Fix: browser playback now releases the backend gate once audio drains, even if turn completion is late. The next live test should watch for repeated playback watchdog logs or missing `voice_play_end`.
6. Test Isolation: controlled NEKO Live tests should disable unrelated proactive output sources so the live feel can be judged cleanly.
7. Active Engagement v1: active topics should create an easy reply point. Prefer concrete choices, light stand-taking, or a tiny NEKO-flavored observation over generic "what should we talk about" prompts. Current implementation records `topic_source` / `topic_shape` / `topic_key` / `topic_hook` / `topic_pattern` / `topic_intent` / `topic_reply_affordance` in recent results for review; low-information trending titles, generic English and Chinese host-prompt topics, recommendation-request topics, open-ended topic-survey material, and room-silence descriptions such as "nobody is talking / suddenly quiet / 突然安静 / 弹幕少 / 冷场" are filtered out, fallback topics can provide their natural shape, and the prompt includes an `example pattern` and explicit viewer reply path for the selected shape so validation can focus on whether the resulting line is actually replyable. The monitor also reports `latest_topic_repeat` so repeated material is visible during live review.
8. Active Topic Fun Axis: recent results and monitor output should expose `topic_fun_axis` alongside `topic_shape`, `topic_intent`, and `topic_reply_affordance`; use it to judge whether Active Engagement is overusing one kind of hook rather than simply increasing frequency.
9. Independent Pacing v1: idle detection should not be blocked forever by NEKO's own proactive output. Current implementation reports `last_viewer_activity_age_sec` and `last_output_age_sec` separately so controlled no-danmaku windows can reach `idle_hosting`; the panel surfaces these values so the next live test can tell whether the room is waiting on viewers or only seeing NEKO's own recent output.
10. Backend Log Watch: run `monitor_live.ps1` with `-BackendLogPath` during controlled tests so watchdog stalls, unrelated proactive-plugin contamination, and long reply outliers are visible as `log_watchdog`, `log_contamination`, `log_reply_len`, and `log_reply_length_status` in the same snapshot as pacing and routing.
11. Active-to-Idle Handoff: automatic Active Engagement now yields during the final few seconds before `idle_hosting` becomes eligible, so a true no-danmaku validation window can reach Idle Hosting instead of being preempted by one more proactive topic.

These slices were validated together in the 2026-06-26 solo-stream run. Do not add Gift / SC / Guard behavior, multi-persona settings, or a major UI redesign while addressing the remaining live-feel findings.

## External Test Readiness Checklist

This is the release gate for handing NEKO Live to more testers. It is stricter than a single developer smoke test and should be used before inviting 3-5 external or semi-external streamers.

Goal: a streamer who is not reading the code can deploy NEKO Live, enter `solo_stream`, and trust NEKO to hold a 30-minute low-danmaku room without obvious silence, spam, repeated first-appearance roast, long-reply drift, cross-plugin contamination, or confusing recovery steps.

### Required before external testing

- Deployment path is documented well enough that a tester can start the plugin without live help from the maintainer.
- Dashboard first screen answers four streamer questions: can NEKO stream, is this dry_run or real output, why is NEKO quiet, and what should the streamer do if something is blocked.
- `solo_stream` is the default validation target for this gate. `co_stream` may remain available, but it does not prove Independent Mode readiness.
- A 30-minute real-output `solo_stream` run passes at least twice after the latest behavior changes.
- One validation run includes a low-danmaku or no-danmaku window of at least 5 minutes.
- One validation run includes at least 5-20 real viewer danmaku, or an explicitly documented substitute if real viewers are unavailable.
- Plugin disabled or `live_enabled=false` never allows NEKO Live to push proactive speech or steal another plugin's output window.
- Gift / SC / Guard use `live_support_events` for short thanks-style replies; they must not fall through to `avatar_roast` or ordinary `danmaku_response`.
- `dry_run=true` and `dry_run=false` are visually and operationally obvious to the streamer.
- Pause / resume / stop listening / refresh status remain available from the panel and do not require code or terminal access.
- Viewer profile clearing works for controlled first-appearance validation and does not clear unrelated sandbox, safety, or live-summary data.

### Live-behavior acceptance

- First useful danmaku from a new viewer creates one first-appearance moment through `avatar_roast`.
- The same UID's later danmaku routes through `danmaku_response`, not another avatar / ID / entrance roast.
- Short reactions such as laughter, emoji, `6`, or one-word replies get short responses, not a new segment or long explanation.
- Question danmaku is answered directly before NEKO changes topic or adds a host hook.
- Viewer-to-viewer `@` messages are not treated as a call to NEKO; NEKO-directed `@NEKO` / `@猫猫` still works as normal current-message input.
- Follow-up replies target the current danmaku and do not continue the previous NEKO line unless the viewer explicitly continues that thread.
- No-danmaku windows can reach `idle_hosting`; idle lines are short, non-template, and do not beg for interaction.
- Repeated idle windows show some program rhythm, such as `settle -> column -> callback`, instead of random filler.
- `active_engagement` creates one concrete low-pressure reply point, such as a tiny choice, small stance, or small challenge.
- Active Engagement does not overuse "send danmaku / anyone here / what should we talk about" audience-prompt language.
- NEKO output remains one short TTS-friendly line in normal cases; no repeated long monologues appear in the second half of the run.
- Recent output should not drift into paraphrasing a previous NEKO Live line, reward bit, host self-test, one-word callback, tiny radio, or other spent output family.

### Observability acceptance

- Before the monitor-tooling slice lands, perform observability acceptance with the Dashboard, recent results, `live_explain`, and the backend log. Record the latest route, status, source, reason, output length, ordinary-danmaku profile/reply shape, idle/active signals, and any watchdog, contamination, long-reply, or repeat evidence those surfaces expose.
- After the monitor-tooling slice lands, run `monitor_live.ps1 -ExpectRealOutput`; when a backend log is available, add `-BackendLogPath <backend-log>` and use the monitor's `alerts` and `log_*` fields as supplemental acceptance evidence.

### Pass / hold decision

Pass for 3-5 tester rollout if:

- Two 30-minute `solo_stream` runs pass without deathly silence, obvious spam, or repeated route confusion.
- At least one quiet window validates `idle_hosting`.
- At least one active topic validates a concrete reply hook.
- Same-UID follow-up danmaku validates `danmaku_response`.
- Short reactions, questions, and `@` cases behave acceptably.
- The streamer can recover from common states using the panel, without editing config files.

Hold external testing if:

- NEKO Live speaks while disabled or steals output from another plugin.
- The streamer cannot tell whether the run is dry_run or real output.
- Same UID repeatedly gets avatar / ID roast.
- Gift / SC / Guard are mistaken for ordinary roastable danmaku.
- Long replies or previous-reply contamination still dominate the second half of a run.
- Idle windows remain silent or turn into generic interaction begging.
- The maintainer must explain terminal logs live for the tester to know what is happening.

## Next Live Test Checklist

This is the canonical checklist for the next controlled solo-stream validation. Quickstart may link to it, but should not duplicate the full decision criteria.

Goal: verify whether the follow-up fixes after the 2026-06-26 run improve live feel. The test should answer whether NEKO can run a 30-minute `solo_stream` without awkward silence, noisy repetition, mistaken `@` replies, over-reliance on recent emotes, avatar re-roast pollution, or playback stalls.

### Preflight

- Use `solo_stream`.
- Decide whether this is a real-output run (`dry_run=false`) or a chain-only run (`dry_run=true`) before the stream starts.
- Clear viewer profiles from the panel only if the test needs a fresh first-appearance baseline.
- If profiles were cleared, confirm through the Dashboard or recent results that no retained viewer profile affects the run. If no count is exposed, record the clear action and verify that the first useful danmaku follows the fresh first-appearance path.
- Disable unrelated proactive-output plugins before the controlled run. The solo-stream validation window should only allow NEKO Live to speak for the live room.
- If another plugin must stay installed, confirm it cannot push proactive speech during the validation window; otherwise mark the run as contaminated and retest.
- The panel should be used for preflight, safe controls, and after-action review. The streamer should not need to watch it constantly during the live room.
- Confirm the first screen answers: can NEKO stream, why she is quiet, what she is likely to do next, and how to pause or recover output.

### Opening and warmup

- `warmup_hosting` should be observable at the start of solo stream.
- NEKO should speak at most one natural opening line.
- The opening line should not sound like idle filler and should not ask viewers to rescue the room.
- Solo readiness should mark warmup as observed after the opening path runs.

### Danmaku continuity

- The first useful viewer danmaku should route as `avatar_roast` and feel like a first-appearance moment, but in `solo_stream` it should answer the current danmaku first instead of becoming a pure avatar / ID roast.
- If multiple new UIDs appear close together in `solo_stream`, only the first one should get true `avatar_roast`; later new-viewer danmaku inside the entrance pacing window should be answered as `danmaku_response`. The current entrance window follows activity level: quiet 75s, standard 45s, active 30s.
- Later ordinary danmaku from the same UID should route as `danmaku_response`.
- In chain-only `dry_run`, the same runtime session may treat a successful dry-run first appearance as a session-local first-roast marker so the next same-UID danmaku can validate `danmaku_response`; this must not persist `roast_count` or write a permanent first-roast result.
- Follow-up danmaku should not reuse avatar / ID roast templates.
- Follow-up danmaku should not carry avatar image input unless a future module explicitly opts into visual analysis.
- The reply should target the current danmaku, not continue the previous NEKO response.
- Short danmaku should get one short TTS-friendly reply.
- Danmaku that mentions another viewer should not automatically be treated as a NEKO-directed message. NEKO-directed mentions, viewer-to-viewer mentions, and ordinary danmaku should be distinguishable during review.
- After first-appearance roast succeeds, recent results should not show a stale pre-roast profile snapshot; `roast_count` should reflect the successful first roast.

### Idle and active pacing

- No-danmaku windows should let `idle_hosting` cover silence with one short line.
- Idle lines should not be repeated, generic, or customer-service-like.
- Active Engagement should wait after recent danmaku output and should not fire in an engaged room.
- Active Engagement should create one easy reply point, not beg for interaction. Good shapes include either/or choices, light stance prompts, and concrete one-line observations that viewers can answer quickly.
- Active Engagement should prefer meaningful recent live-room danmaku as topic material before falling back to public Bilibili trending material, so NEKO sounds like she is hosting this room instead of reading a generic trend list.
- Active Engagement must not ask viewers what they want to hear or ask viewers to choose NEKO's topic; NEKO should bring one small concrete hook herself.
- Active Engagement should not use "room is silent / nobody is talking / suddenly quiet / 突然安静 / 弹幕少 / 冷场" as topic material; silence coverage belongs to Idle Hosting. It should also reject recommendation requests, open-ended topic-survey material such as "any recommendations" or "what are we doing tonight", and template host-bait material such as "get the chat moving" or "keep the chat alive".
- Active Engagement should not permanently prevent Idle Hosting from being observed in a true no-danmaku window.
- When `active_idle_wait` is small, Active Engagement should yield and the next expected automatic action should become `idle_hosting`.
- If `idle_hosting` has already produced three actual host beats after the last viewer danmaku, the next expected automatic action may become `active_engagement` with `director_reason=idle_hosting_streak`. This is the handoff check: cold-room coverage should not become endless idle filler.
- Live-state review should distinguish viewer silence from NEKO's own recent output when judging whether the room is `quiet` or `idle`.
- Active Engagement should not overuse recent danmaku as topic material. If recent-danmaku source repeats too much, the next topic should come from Bilibili trend material or NEKO-owned fallback hooks.
- Active Engagement should not repeat the same topic shape / intent several times in a row; the review should be able to see whether a shape guard changed the next proactive topic.
- Before the monitor-tooling slice lands, capture the Dashboard, recent results, `live_explain`, and backend log at least once during a quiet or idle window; record the available pacing, routing, output-age, topic/host-beat, status, and reply-length evidence. The monitor-specific checks below apply only after that later slice lands.
- `monitor_live.ps1` should be captured at least once during quiet / idle windows; record `director_action`, `director_reason`, `director_eligible`, `director_wait`, `viewer_age`, `output_age`, `entrance_pacing_window`, `active_min_interval`, `active_min_wait`, `active_danmaku_wait`, `active_idle_wait`, `latest_status`, `latest_route`, `latest_uid`, `latest_source`, `latest_text`, `latest_reason`, `latest_age`, `latest_age_status`, `latest_output_len`, `latest_output_length_status`, `recent_long_reply_count`, `recent_long_reply_avatar_roast`, `recent_long_reply_danmaku_response`, `recent_long_reply_live_support_events`, `recent_long_reply_idle_hosting`, `recent_long_reply_active_engagement`, `recent_long_reply_warmup_hosting`, `recent_total`, `recent_avatar_roast`, `recent_danmaku_response`, `recent_live_support_events`, `recent_warmup_hosting`, `recent_idle_hosting`, `recent_active_engagement`, `recent_actual_avatar_roast`, `recent_actual_danmaku_response`, `recent_actual_live_support_events`, `recent_actual_warmup_hosting`, `recent_actual_idle_hosting`, `recent_actual_active_engagement`, `recent_signal_danmaku_signal`, `recent_signal_gift_signal`, `recent_signal_super_chat_signal`, `recent_pushed`, `recent_dry_run`, `recent_skipped`, `recent_failed`, `latest_topic_source`, `latest_topic_shape`, `latest_topic_key`, `latest_topic_hook`, `latest_topic_pattern`, `latest_topic_intent`, `latest_topic_fun_axis`, `latest_topic_reply_affordance`, `recent_topic_reply_affordance_top`, `recent_topic_reply_affordance_bias`, `recent_topic_source_fallback`, `recent_topic_source_bili_trending`, `recent_topic_source_recent_danmaku`, `recent_topic_shape_either_or`, `recent_topic_shape_light_stance`, `recent_topic_shape_tiny_tease`, `recent_topic_shape_small_challenge`, `recent_topic_axis_choice`, `recent_topic_axis_tease`, `recent_topic_axis_mood`, `recent_topic_axis_micro_challenge`, `recent_topic_axis_viewer_callback`, `recent_topic_intent_quick_vote`, `recent_topic_intent_tiny_answer`, `recent_topic_intent_tease_back`, `recent_topic_intent_agree_or_pushback`, `latest_topic_repeat`, `latest_host_beat_key`, `latest_host_beat_shape`, `latest_host_beat_fun_axis`, `latest_host_beat_title`, `latest_host_beat_reply_affordance`, `recent_host_beat_reply_affordance_top`, `recent_host_beat_reply_affordance_bias`, `latest_host_beat_repeat`, `recent_host_beat_axis_choice`, `recent_host_beat_axis_tease`, `recent_host_beat_axis_mood`, `recent_host_beat_axis_micro_challenge`, `recent_host_beat_axis_viewer_callback`, `avatar_repeat_uid`, and `avatar_repeat_count` so the review can tell whether pacing, routing, topic material source, topic shape, topic quality, active topic axis variety, active reply-path variety, idle host beat replyability, host beat axis variety, idle reply-path variety, latest input, repeated topic material, repeated avatar roast, overlong replies, support-event thanks, repeated or missing warmup hosting, proactive output in an engaged room, missing idle hosting, missing active engagement, or a stalled result stream failed. Use the recent status counts, recent actual route counts, recent signal counts, recent topic source counts, recent topic shape counts, recent topic axis counts, recent topic intent counts, topic / host beat reply-affordance fields, host beat fun-axis fields, and per-route long-reply counts to distinguish actual output from skipped / failed attempts, spot overreliance on fallback / Bili / recent danmaku material, spot one-note proactive topics or one-note idle host beats, confirm gift / SC / guard uses `live_support_events` without polluting ordinary danmaku routes, and locate which speaking path is producing overlong replies. `alerts` should flag `live_disabled`, `test_isolation`, `topic_repeat`, `topic_reply_missing`, `host_beat_reply_missing`, `topic_axis_bias`, `host_beat_axis_bias`, `topic_reply_affordance_bias`, `host_beat_reply_affordance_bias`, `topic_source_bias`, `topic_shape_bias`, `topic_intent_bias`, `topic_filter_direct_request`, `topic_filter_reaction`, `topic_filter_runtime_feedback`, `avatar_repeat`, `avatar_bias`, `long_reply`, `generic_host_prompt`, `recent_failed`, `host_beat_repeat`, `proactive_in_engaged`, `active_blocks_idle`, `warmup_repeat`, `warmup_missing`, `idle_missing`, or `active_missing` when those risks appear.
- For Live Feel Pack v1.5, also record `recent_topic_skip_viewer_to_viewer_mention`, `recent_topic_skip_recent_danmaku_source_streak`, and `latest_topic_shape_guard_reason`; `alerts` should flag `topic_viewer_mention`, `topic_source_streak`, and `topic_shape_guard` when mention filtering, recent-danmaku source throttling, or shape / intent guards affect Active Engagement.
- For support-event validation, verify Gift / Guard / SC route to `live_support_events`, expose support-event metadata, stay short, do not ask for more support, and keep host routes (`warmup_hosting`, `idle_hosting`, `active_engagement`) on their own pacing checks.
- For Live Feel Pack v1.6, also verify `profile_count` follows solo readiness even when `recent_profiles` is empty, and host routes (`warmup_hosting`, `idle_hosting`, `active_engagement`) warn on 60+ character outputs even if the global long-reply threshold is higher.
- For Live Feel Pack v1.7, verify that cold-room lines rotate through more than one hosting beat shape and fun axis, and Active Engagement topics expose a clear `fun_axis` plus `reply_affordance` so reviewers can tell whether the topic gives viewers a low-effort way to answer. The prompt should treat the reply path as the only hook, not add a second question, and the fallback material should avoid template-hosting bait such as "everyone interact", "send danmaku", "what should we talk about", or "get the chat moving" even inside negative instructions. If `topic_axis_bias` or `host_beat_axis_bias` appears, treat the issue as content variety first, not as a need to raise frequency.
- For Live Feel Pack v1.9, also verify cross-module content-family variety: after an `idle_hosting` beat uses a family such as `choice_vote`, `short_callback`, `room_mood`, `object_scene`, `host_self_test`, `tease`, or `micro_challenge`, the next `active_engagement` should prefer a different family, and vice versa. This catches the live-feel problem where NEKO does not repeat the exact same key, but still sounds like she is reusing the same hosting trick.
- For Live Feel Pack v1.11, also verify long-run anti-repeat: after 30+ minutes, NEKO should not circle back to the same high-signal hosting families such as reward / snack, room mood / tiny radio, host self-test, one-word callback, either-or choice, or micro-challenge just because the exact wording changed. If fallback Active Engagement topics are constrained by family / axis / title guards, the next candidate should still prefer an unused key before reusing the first fallback topic.
- For Live Feel Pack v1.13, also verify host memory isolation: NEKO Live short replies should still be available to anti-repeat and voice-echo protection, but should not enter the ordinary AI turn text that normal chat memory uses. A later danmaku should answer the current input, not continue or paraphrase the previous live reply.
- For Live Feel Pack v1.14, treat cross-server and hot-swap memory isolation as host-side risk boundaries outside the current plugin scope: `live_reply_contract=short_tts_line` outputs and turn ends may still enter ordinary chat history through `cross_server` or `message_cache_for_new_session`. The plugin provides metadata, recent-output constraints, and monitor clues only; if replies still feel like they continue the previous live line, inspect the live recent-output window first, then check ordinary memory/analyzer logs and new-session cache as host-side follow-up evidence.
- Record `latest_topic_family`, `latest_host_beat_family`, `recent_topic_family_*`, `recent_host_beat_family_*`, `topic_family_bias`, and `host_beat_family_bias` in the next real-output review. These fields are the acceptance signal for "NEKO keeps repeating the same kind of thing" even when exact text, topic key, or host beat key is different.
- `topic_source_bias` means recent Active Engagement topics overuse one material source; use it together with `recent_topic_source_*` to decide whether to adjust fallback topics, Bili trending filtering, or recent-danmaku topic reuse. `topic_shape_bias` means the material may be varied but the interaction shape is still one-note; use it with `recent_topic_shape_*` before changing broader pacing.
- Also record `avatar_roast_share`, `avatar_roast_bias`, `recent_long_reply_count`, and `recent_generic_host_prompt_count` during solo-stream tests. `avatar_bias` means recent ordinary danmaku routes are still dominated by first-appearance roast, `recent_long_reply_count` catches overlong replies even after a later short reply becomes the latest result, and `generic_host_prompt` means NEKO slipped into template-like "please interact / send danmaku / anyone here" hosting.
- Before the monitor-tooling slice lands, use the Dashboard, recent results, `live_explain`, and the backend log together for real-output validation. Record the latest route, output status, watchdog evidence, unrelated proactive output, and reply length so playback stalls or contamination are not mistaken for NEKO Live pacing failures. After the monitor tooling is available, `monitor_live.ps1 -ExpectRealOutput -BackendLogPath <backend-log>` may be used to collect the equivalent `alerts` and `log_*` fields; treat that command as a later-slice workflow, not a prerequisite for this slice.
- If NEKO feels too quiet or too noisy, tune pacing before adding event types.

### Signal observation

- Recent results should distinguish `avatar_roast`, `danmaku_response`, `warmup_hosting`, `idle_hosting`, `active_engagement`, and gift/fan-club/guard signal capture.
- If a gift, fan-club medal, or guard event appears, record whether it is captured as `gift_signal`.
- Do not treat gift/fan-club/guard observation as full Gift / SC / Guard behavior.

### Pass / fail decision

Pass if:

- 30 minutes has no deathly silence and no obvious spam.
- NEKO sounds like the same persona across opening, replies, idle lines, and active topics.
- Follow-up danmaku does not feel polluted by the previous reply.
- The streamer would still trust NEKO to hold the room.

Fail or retest if:

- replies are too long for live TTS;
- playback gate watchdog or missing `voice_play_end` repeatedly delays NEKO;
- follow-up danmaku still feels like another avatar roast;
- unrelated proactive plugin output appears in the live room;
- Active Engagement feels pushy or generic;
- Idle Hosting repeats or sounds awkward;
- current danmaku is ignored in favor of old context;
- the panel cannot explain why NEKO is quiet before the streamer starts guessing.

Out of scope before the next live test:

- full Gift / SC / Guard behavior;
- private messages;
- automation;
- major UI redesign;
- long-term memory;
- multi-persona configuration.

### 2. One Friendly Streamer Shadow Test

Goal: verify whether a streamer dares to hand the room to NEKO.

Observe:

- whether the streamer understands NEKO's status;
- whether viewers respond to NEKO's lines;
- whether NEKO feels like a host instead of a reply bot.

### 3. Three to Five Small Streamer Closed Beta

Prerequisite: Slice 1 + Slice 2 + Slice 4 are complete enough for a controlled test.

Goal: validate whether 30-minute Independent Mode holds in real rooms.

Key signals:

- silence duration;
- repetition feeling;
- viewer reply rate;
- streamer trust;
- persona consistency.

## Fastest Test Cadence

- Now: friendly streamer observation is acceptable, but do not claim Independent Mode is solved.
- After Slice 1: run small "streamer trust" validation.
- After Slice 2 + Slice 4 acceptance: start a 3-5 streamer Independent Mode closed beta.
- Slice 3 should be introduced cautiously after beta feedback.

## Product Principles

- NEKO Live is a live-scene plugin for the N.E.K.O main persona, not a new platform.
- The current stage prioritizes Independent Mode.
- First solve "the streamer can trust NEKO" and "the room does not go silent".
- Gift / Super Chat / Guard are enhancements, not Independent Mode prerequisites.
- Do not make the streamer tune many parameters; expose a few understandable live states.
- The product succeeds when a 30-minute stream is not awkward.

## Decision Rules

- If a feature improves live trust or reduces dead air, consider it for Independent Mode MVP.
- If a feature only adds another event type, defer it until Independent Mode validates.
- If a feature makes NEKO sound generic, pushy, or unlike herself, reject or redesign it.
- If a control requires the streamer to understand internal mechanics, simplify it into a live-state choice.
