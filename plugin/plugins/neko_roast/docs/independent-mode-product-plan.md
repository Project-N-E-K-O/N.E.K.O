# NEKO Live Independent Mode Product Plan

> Updated: 2026-06-24
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
- Active Engagement v0 is implemented as a conservative solo-stream quiet-moment trigger with both automatic and manual paths. It is meant for controlled live-effect validation only: one small replyable topic, long minimum intervals, and no Gift / SC / Guard coupling.
- Live Director status is exposed in the dashboard to explain the next automatic speaking action: none, active engagement, or idle hosting, including whether it is eligible and how long it must wait.
- Solo stream readiness is exposed in the dashboard as a streamer-facing checklist. It aggregates preflight, warmup, first-viewer roast, follow-up danmaku reply, light active topic, idle hosting, and pacing control into one readiness conclusion; it is not a separate output path or test backend.
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

Gift / fan-club signal note:

- A fan-club medal event was observed as text similar to "sent 1 fan-club medal" and was pushed through the current `live_danmaku` path.
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
3. Reply Length Contract: first-appearance roast is at most one or two short lines; follow-up danmaku, warmup hosting, idle hosting, and active engagement should each be one short TTS-friendly line. Short danmaku should get short replies.
4. Active Engagement Pacing: make automatic Active Engagement more conservative after recent danmaku replies. It should not fire in `engaged` state and should wait longer after successful live danmaku output.
5. Result Labels: validation and dashboard output should distinguish `avatar_roast`, `danmaku_response`, `warmup_hosting`, `idle_hosting`, `active_engagement`, and gift/fan-club signal capture instead of showing all ordinary live input as `live_danmaku`.
6. Warmup Hosting Testability: the next live test should make the opening moment observable so the team can tell whether `warmup_hosting` fired, whether it spoke only one natural opening line, and whether it was not mistaken for idle hosting.
7. Gift Signal v0: if a gift or fan-club medal appears again, capture it as a gift/fan-club signal. Do not build full Gift / SC / Guard behavior before the live pacing issues are fixed.

## Next Live Test Checklist

This is the canonical checklist for the next controlled solo-stream validation. Quickstart may link to it, but should not duplicate the full decision criteria.

Goal: verify whether the offline fixes after the 2026-06-24 run improved the live feel. The test should answer whether NEKO can run a 30-minute `solo_stream` without awkward silence, noisy repetition, or context pollution.

### Preflight

- Use `solo_stream`.
- Decide whether this is a real-output run (`dry_run=false`) or a chain-only run (`dry_run=true`) before the stream starts.
- Clear viewer profiles only if the test needs a fresh first-appearance baseline.
- The panel should be used for preflight, safe controls, and after-action review. The streamer should not need to watch it constantly during the live room.
- Confirm the first screen answers: can NEKO stream, why she is quiet, what she is likely to do next, and how to pause or recover output.

### Opening and warmup

- `warmup_hosting` should be observable at the start of solo stream.
- NEKO should speak at most one natural opening line.
- The opening line should not sound like idle filler and should not ask viewers to rescue the room.
- Solo readiness should mark warmup as observed after the opening path runs.

### Danmaku continuity

- The first useful viewer danmaku should route as `avatar_roast` and feel like a first-appearance moment.
- Later ordinary danmaku from the same UID should route as `danmaku_response`.
- Follow-up danmaku should not reuse avatar / ID roast templates.
- The reply should target the current danmaku, not continue the previous NEKO response.
- Short danmaku should get one short TTS-friendly reply.

### Idle and active pacing

- No-danmaku windows should let `idle_hosting` cover silence with one short line.
- Idle lines should not be repeated, generic, or customer-service-like.
- Active Engagement should wait after recent danmaku output and should not fire in an engaged room.
- Active Engagement should create one easy reply point, not beg for interaction.
- If NEKO feels too quiet or too noisy, tune pacing before adding event types.

### Signal observation

- Recent results should distinguish `avatar_roast`, `danmaku_response`, `warmup_hosting`, `idle_hosting`, `active_engagement`, and gift/fan-club signal capture.
- If a gift or fan-club medal appears, record whether it is captured as `gift_signal`.
- Do not treat gift/fan-club observation as full Gift / SC / Guard behavior.

### Pass / fail decision

Pass if:

- 30 minutes has no deathly silence and no obvious spam.
- NEKO sounds like the same persona across opening, replies, idle lines, and active topics.
- Follow-up danmaku does not feel polluted by the previous reply.
- The streamer would still trust NEKO to hold the room.

Fail or retest if:

- replies are too long for live TTS;
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
