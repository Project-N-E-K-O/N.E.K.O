# NEKO Live Independent Mode Product Plan

> Updated: 2026-06-23
>
> This document is the canonical product plan for Independent Mode. It describes product priorities, MVP scope, validation sequence, and non-goals. It does not define internal architecture, runtime observability, or implementation details.

## Product Thesis

NEKO Live is the live-scene capability plugin for the N.E.K.O main persona. The next product question is:

> Can NEKO independently sustain a 30-minute livestream in a 10-50 viewer room without awkward silence?

The current gap is not live ingest, danmaku response, or output plumbing. The current gap is **live pacing**:

- when NEKO should speak;
- what NEKO should say;
- how often NEKO should speak;
- how NEKO avoids awkward self-talk;
- how NEKO invites viewers to reply.

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
- Slice 4 base is landed: activity level gives the streamer a small quiet / standard / active pacing control instead of many parameters.
- The current validation target is not another event type. It is a controlled solo-stream validation with low danmaku, occasional danmaku, and no-danmaku moments.
- The next product decision should be based on controlled validation:
  - if NEKO is too quiet or too noisy, do Pacing Control next;
  - if NEKO sounds generic or awkward, tune Idle Hosting wording first;
  - if the streamer cannot tell why NEKO is silent, refine Live Status before adding more behavior.
  - if the baseline feels stable, prepare a 3-5 streamer closed beta before adding Active Engagement.

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

### Slice 4: Pacing Control

Goal: prevent Idle Hosting from becoming spam or awkward chatter.

The streamer should not tune many parameters. Use a small number of simple live states first:

- quiet;
- standard;
- active.

Pacing Control is the safety valve for Independent Mode. It should keep NEKO from speaking too often, speaking too rarely, or interrupting useful audience interaction.

### Slice 3: Active Engagement

Goal: let NEKO proactively create moments viewers want to answer.

This slice has high value, but it is the most likely to fail. It should come after Idle Hosting and Pacing Control have been validated.

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
