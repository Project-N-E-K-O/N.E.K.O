# Task spec: draft `memory-evidence-rfc.md` v1

**Status**: handoff document for a fresh session. Delete after the RFC lands.

**Branch to work on**: `claude/review-pr-634-memory-rSsDR` (already checked out;
do NOT switch branches; do NOT open a PR).

**Single deliverable**: one new file
`docs/design/memory-evidence-rfc.md` v1, committed and pushed.

---

## 1. One-sentence task

Draft the RFC for the user-driven **evidence mechanism** of the memory
subsystem (issue #849, absorbing closed PR #634), building on the
event-sourced infrastructure that landed in PR #905.

## 2. Context chain (the "why" in 30 seconds)

- **#634** (Krabbypattyl): topic-level negative-signal + `soft_avoid` /
  `hard_avoid`. **Closed**, architecturally incompatible with current main.
- **#849**: broader user-driven evidence framework. It **absorbs #634** as
  its topic-projection phase. This is the issue this RFC implements.
- **#905** (merged): P0+P1+P2.a — events.ndjson + Reconciler +
  per-character `threading.Lock` + `asyncio.Lock` + Outbox + CursorStore.
  The RFC MUST build on top of #905's `EventLog.record_and_save` /
  `arecord_and_save` API, not around it.

Review discussion with the #905 owner produced the 5-phase plan below.
User has approved: ①#849 absorbs #634; ②one comprehensive RFC; ③the RFC
author is a temporary owner — successor may freely amend.

## 3. Approved 5-phase plan (land in this order unless noted)

| Phase | Scope | PR grouping |
|---|---|---|
| **P-A** | Signal detection: `extract_facts` gains `reinforces`/`negates` fields; `check_feedback` already returns confirmed/denied/ignored — reuse; **stop using `_texts_may_contradict` as a user-feedback channel** (keep for persona-internal correction queue only) | same PR as P-B |
| **P-B** | Evidence data layer: add `reinforcement` / `disputation` / `last_signal_at` to reflection entries AND persona entries; 2 new event types (`reflection.evidence_updated`, `persona.evidence_updated`) full-snapshot pattern; derive status from `evidence_score`; **delete `AUTO_CONFIRM_DAYS` / `AUTO_PROMOTE_DAYS` / `_aauto_promote_stale_locked` time-walk** | same PR as P-A |
| **P-C** | Time maintenance: half-life decay **read-time** (default 30d); archive items with `evidence_score < 0` | parallel with P-D |
| **P-D** | Render budget: hard token cap on `_compose_persona_markdown` (default 2000); `protected=True` always included; LLM-compression cache invalidated on any `persona.*` event | parallel with P-C |
| **P-E** | Topic projection (absorbs #634): `topic_score(T) = Σ evidence_score` over items with tag T; thresholds `soft_avoid_at=-3`, `hard_avoid_at=-8`; fact tags must propagate fact → reflection → persona during promotion (currently dropped) | last |

## 4. Hard constraints (red lines — do not re-litigate)

1. **No AI-driven signals.** Evidence counters only move on explicit user
   input (fact extractor from user messages, or `check_feedback` on user
   messages). No "still stands after 3 days → +1" style heuristics.
2. **No time-based auto-promotion.** Promotion to persona requires
   crossing a score threshold, and scores only move on user signal.
3. **`protected=True` persona entries are exempt** from decay, archival,
   and render-budget eviction.
4. **No raw user content in event payloads.** Use `text_sha256` /
   `user_msg_sha256` — see §3.3.1 of `memory-event-log-rfc.md`.
5. **Read-time decay, not write-time.** Writing decayed values on a
   daily timer pollutes the event log with N events/day/character.

## 5. Locked design decisions (copy into the RFC)

- **Formula**: `evidence_score = importance + reinforcement - disputation`.
  Half-life (default 30d) applied to `reinforcement` and `disputation` at
  read time.
- **Derived status** (from `evidence_score`):
  - `score < 0` → archive candidate
  - `0 ≤ score < 5` → pending
  - `5 ≤ score < 10` → confirmed
  - `score ≥ 10` → promoted (present in persona markdown)
  - Exact thresholds are engineering-judgment; revisit before coding.
- **Two new events** (full-snapshot pattern — like `persona.fact_mentioned`):
  - `reflection.evidence_updated`: `{reflection_id, reinforcement, disputation, last_signal_at, source}` where `source ∈ {user_fact, user_rebut, user_confirm}`
  - `persona.evidence_updated`: `{entity_key, entry_id, reinforcement, disputation, last_signal_at, source}`
  - Both wired via `EventLog.record_and_save` (sync twin) / `arecord_and_save` (async), with handlers registered on `Reconciler`. Idempotency = overwrite from snapshot.
- **Migration seed** (on first boot with P-B code):
  - reflection currently `confirmed` → `reinforcement=2` (NOT 3 — don't want instant re-promotion)
  - reflection currently `promoted` → `reinforcement=3`
  - reflection currently `denied` → `disputation=3`
  - all persona entries → `reinforcement=0, disputation=0, last_signal_at=null` (already in persona; counters matter only for future signal)
- **`recent_mentions` untouched.** Reverse-direction (suppress over-mention); unrelated to evidence.
- **Topic projection uses fact `tags`** (already exists in fact schema at `memory/facts.py:337`). Tags must propagate through promotion in P-E.

## 6. Required reading (real paths + anchors)

Read each BEFORE writing — do not speculate about APIs.

| File | Why | Key anchors |
|---|---|---|
| `docs/design/memory-event-log-rfc.md` | **The template.** Match its structure (revision log, §1 motivation, §2 non-goals, §3 design w/ subsections, §4 implementation plan, §5 migration, §6 open questions, §7 rejects, §8 success criteria, §9 out-of-scope) and tone (dense, file:line refs, rationale blocks) | §3.3.1 hash-only rule; §3.4 write order; §3.4.2 compound transactions; §3.4.3 idempotency contract |
| `memory/event_log.py` | Event type constants + write/reconcile API | `EVT_*` at 51-62; `EventLog.record_and_save` at 387; `arecord_and_save` at 457; `Reconciler.register` / `areconcile` at 477 |
| `memory/persona.py` | Entry schema + existing flags + locks | entry schema at 516-524; `FACT_ADDED` / `FACT_REJECTED_CARD` / `FACT_QUEUED_CORRECTION` at 577-579; `_texts_may_contradict` at 697; `_alocks` at 110; `SUPPRESS_MENTION_LIMIT=2` at 43 |
| `memory/reflection.py` | Reflection schema + state transitions + feedback | entry schema ~line 408; `AUTO_CONFIRM_DAYS=3` / `AUTO_PROMOTE_DAYS=3` at 66-67; `_aauto_promote_stale_locked` at 820; `check_feedback` at 557 (already returns confirmed/denied/ignored — reuse) |
| `memory/facts.py` | `importance` + `tags` already exist | `importance` at 312/337; filter `< 5` at 315 |
| `config/prompts_memory.py` | Prompts to extend | `FACT_EXTRACTION_PROMPT` at 697 (currently outputs `{text, importance, entity, tags}`; P-A adds `reinforces`/`negates`); `REFLECTION_FEEDBACK_PROMPT` at 897 (reuse as-is) |

## 7. RFC document requirements

### 7.1 Status header (use this verbatim, dates kept as-is)

```
Status: **Draft v1** — authored by Claude during the #634 review session as a
temporary owner. Successor authors may freely amend, restructure, or
override any part of this document. Design decisions here reflect the state
of the discussion at 2026-04-21 and are not frozen — in particular, the
exact score thresholds, half-life days, and render-budget token numbers are
engineering judgment calls that the implementation author should re-verify
against real workload traces before coding.
```

### 7.2 Structure (mirror `memory-event-log-rfc.md` exactly)

- Revision log (v1 only — one short entry)
- §1 Motivation (what's broken without evidence; what #849 + #634 tried to
  solve; why #905 makes this cleaner than standalone)
- §2 Non-goals (no AI-driven signals; no cross-character evidence; no
  ML-ranked persona; no per-entry UI today; no schema versioning)
- §3 Proposed design
  - §3.1 Concepts: evidence_score formula, derived status, red lines
  - §3.2 Data-model changes (fact / reflection / persona schemas)
  - §3.3 New event types (2 events, full-snapshot rationale, payloads)
  - §3.4 Signal detection (P-A detail — extractor schema change, feedback reuse, `_texts_may_contradict` scope narrowing)
  - §3.5 Time decay + archiving (P-C detail — read-time math, archive trigger)
  - §3.6 Render budget (P-D detail — cap, eviction order, compression cache invalidation)
  - §3.7 Topic projection (P-E detail — tag propagation, aggregation formula, thresholds, consumers)
  - §3.8 API surface (new `EVT_*` constants, new methods on `PersonaManager` / `ReflectionEngine` for signal application, reconciler registrations)
- §4 Implementation plan (P-A+P-B / P-C / P-D / P-E, with per-phase test list — concurrency, idempotency, crash recovery, migration seed correctness)
- §5 Migration (seed values; how to handle boots that crash mid-seed; note the migration is a one-shot emitted as events so reconciler can replay)
- §6 Open questions (thresholds, half-life, render budget, compression cache granularity, whether topic consumers are in scope for P-E or deferred)
- §7 What this RFC explicitly rejects (plaintext in payloads; time-based auto-promotion; cross-character evidence; ML ranking; per-entry UI; #634's original standalone topic-counter design — the reasons)
- §8 Success criteria (8a force-kill between signal detect and evidence write → reconciler converges; 8b re-apply idempotency; 8c migration seed correctness; 8d no pre-existing test regressions; 8e deadlock-free under concurrent `/process` + `/reflect` + any periodic loop still alive; 8f topic projection reacts to new facts within one write cycle)
- §9 Out-of-scope follow-ups

### 7.3 Tone

Match `memory-event-log-rfc.md`: precise, dense, code snippets, file:line
references, rationale blocks after each decision. Reference §3.4 and
§3.4.3 of the event-log RFC explicitly when reusing its `record_and_save`
pattern — do not restate the whole pattern, just cite it.

### 7.4 Length

Target ~700-900 lines (template is 840). If significantly shorter, you've
probably skipped rationale blocks. If significantly longer, you've
probably restated things the event-log RFC already covers.

## 8. Delivery

```bash
git -C /home/user/N.E.K.O add docs/design/memory-evidence-rfc.md
git -C /home/user/N.E.K.O commit -m "$(cat <<'EOF'
Draft memory-evidence-rfc.md v1

First draft of the evidence-mechanism RFC (issue #849, absorbing #634).
Five-phase plan building on #905 event-log infrastructure:
P-A signal detection, P-B evidence data, P-C time maintenance,
P-D render budget, P-E topic projection.

Authored as temporary owner — successor may freely amend.
EOF
)"
git -C /home/user/N.E.K.O push -u origin claude/review-pr-634-memory-rSsDR
```

**Do NOT open a PR.** **Do NOT modify any file other than the new RFC.**
Report: line count, commit hash, push OK/fail.

## 9. If blocked

If any anchor line number in §6 has drifted (files edit faster than this
spec), grep for the symbol name instead — the names themselves are stable.
If a design decision in §5 feels wrong on contact with real code, stop and
flag it in §6 "Open questions" rather than silently changing the decision.
