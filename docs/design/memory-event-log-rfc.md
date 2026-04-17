# RFC: Memory subsystem event log + view derivation (P2)

Status: **Draft** — awaiting design review before implementation.

Branch context: `claude/awesome-goldberg-omrDx`, built on top of P0 (persistent
rebuttal cursor) and P1 (reflection id determinism + outbox).

## 1. Motivation

P0 and P1 together close two concrete resilience holes:

- P0 persists the rebuttal loop cursor, so "3-day shutdown loses all rebuttals"
  is no longer possible.
- P1.a makes `synthesize_reflections` idempotent via deterministic ids; P1.b/c
  add an outbox so `extract_facts` etc. can be replayed after process kill.

These are point fixes. The **remaining structural problem** is that the three
view files (`facts.json`, `reflections.json`, `persona.json`) are still the
only record of "what happened to the data". There is no ordered history, which
means:

1. **Debugging is guesswork.** When a persona entry is wrong, there's no trail
   of "which reflection promoted it, from which facts, at which LLM call".
2. **Partial view writes are invisible.** If a crash happens mid-`save_facts`,
   the file has some entries but no signal that some were dropped. There's no
   independent source we can reconcile against.
3. **Cross-file invariants are hard to assert.** "Every confirmed reflection
   has an absorbed fact set" is only checkable by scanning every file; there's
   no audit trail that, e.g., `absorbed=True` was set because of
   `reflection.synthesized(rid)`.
4. **Future counters (P4 evidence score) have nowhere to live.** support/
   contradict counters are aggregations over "what happened". Without an event
   stream, they have to be recomputed from scratch every time — or duplicated
   in each view file.

P2 adds a per-character `events.ndjson` append-only log that records every
state transition **before** the view file changes, and uses it to reconcile
views after abnormal shutdown.

## 2. Non-goals

- **Not full event sourcing.** Views remain the readable source-of-truth. The
  event log is a resilience substrate, not a replacement. `persona.json` is
  still hand-editable; UI keeps reading it directly.
- **No SQLite / Redis / LMDB.** Constraint inherited from CLAUDE.md: single
  local user, single-writer per character, atomic JSON writes are sufficient.
- **No event schema evolution framework.** If an event type ever changes
  shape, a hand-written migration function in-tree is fine.
- **No rule engine / state machine DSL.** Business rules (when pending →
  confirmed, what counts as rebuttal) stay in `reflection.py` and `persona.py`.
- **No multi-process concurrency.** One writer per character per process is
  assumed, same as P1.

## 3. Proposed design

### 3.1 File layout

```
memory_dir/<character>/events.ndjson     # append-only
memory_dir/<character>/events.snapshot   # periodic compaction target (optional)
```

One file per character. Lives beside `facts.json`, `reflections.json`, etc.

### 3.2 Record schema

Every line is a JSON object:

```json
{
  "event_id": "<uuid4>",
  "type": "<event_type>",
  "ts": "<ISO8601>",
  "payload": { ... }
}
```

`event_id` is uuid4 (not sequential). Ordering is implicit in file position and
confirmed by `ts`; we do NOT assume strictly monotonic `ts` across machines
(the clock-rollback protection from P0 applies here too).

### 3.3 Event types (initial set: 9)

| Type | Payload | Written by |
|---|---|---|
| `fact.added` | `{fact_id, text, entity, importance, source_message_hash}` | `FactStore.extract_facts` |
| `fact.absorbed` | `{fact_id, reflection_id}` | `FactStore.mark_absorbed` |
| `fact.archived` | `{fact_id}` | `FactStore._archive_absorbed` |
| `reflection.synthesized` | `{reflection_id, text, entity, source_fact_ids}` | `ReflectionEngine.synthesize_reflections` |
| `reflection.state_changed` | `{reflection_id, from, to, reason}` | `ReflectionEngine.aconfirm/areject/aauto_promote` |
| `reflection.surfaced` | `{reflection_id}` | `ReflectionEngine.arecord_surfaced` |
| `reflection.rebutted` | `{reflection_id, user_message_excerpt}` | `_periodic_rebuttal_loop` |
| `persona.fact_added` | `{entity_key, fact_text, source_reflection_id or source_correction_id}` | `PersonaManager.add_fact` |
| `correction.queued` / `correction.resolved` | `{correction_id, conflict_summary, action}` | `PersonaManager.queue_correction` / `resolve_corrections` |

(Deliberately not exhaustive; `persona.fact_mentioned` and `persona.suppressed`
are absorbed into one `persona.fact_added` with a `mention_delta` subfield to
keep the type count low.)

### 3.4 Write order rule

**Event is appended before the view is written.**

```python
async def _record_and_save(lanlan_name, event_type, payload, save_view):
    await event_log.aappend(lanlan_name, event_type, payload)
    await save_view()  # atomic_write_json for the corresponding view file
```

Rationale: an event line in the log with no corresponding view update is
"reconcilable on restart" (we know what to apply). A view change with no event
line is "silent data — unrecoverable on audit". We prefer the former.

Failure modes:
- Event append fails → caller raises, no view change, no partial state.
- Event appended, view save fails (e.g., disk full mid-write) → reconciler on
  next startup replays the event onto the stale view.

### 3.5 Startup reconciliation

On `memory_server` startup, after P1 outbox replay, run:

```python
async def _reconcile_views(lanlan_name):
    last_applied = await _read_last_applied_event_id(lanlan_name)  # sentinel file
    tail = await event_log.aread_since(lanlan_name, last_applied)
    if not tail:
        return
    for event in tail:
        await _apply_event_to_view(lanlan_name, event)
    await _write_last_applied_event_id(lanlan_name, tail[-1]['event_id'])
```

Sentinel file: `memory_dir/<character>/events_applied.json` stores
`{last_applied_event_id, ts}`. Written by `atomic_write_json` **after** the
corresponding view save succeeds.

**Apply semantics** per event type are hand-coded — each `_apply_X` is idempotent
(e.g., `fact.absorbed` sets `absorbed=True` only if still False; re-applying
`reflection.synthesized` dedups by reflection_id, matching P1.a logic).

### 3.6 Compaction

Every startup, if `events.ndjson` exceeds **10_000 lines or 90 days**, run:

1. Read current views (facts.json / reflections.json / persona.json).
2. Derive a "starting point" event list: each entity's current state as
   a synthesized `.*.snapshot_start` event.
3. Write `events.snapshot` atomically with the derived starting events.
4. `atomic_write_text(events.ndjson, "")` — truncate.
5. Next appends go to the fresh events.ndjson; reconciler reads snapshot +
   events.ndjson in that order.

The snapshot is a full materialization of current views as events, so no
history is lost semantically; only the detailed step-by-step trail older than
the compaction threshold is collapsed.

### 3.7 Relationship to P1 outbox: **split, do not merge**

**Decision**: keep `outbox.ndjson` and `events.ndjson` as two separate files.

Reasons:

1. **Different retention**: outbox records are ephemeral — a pending op has
   interesting life between `append_pending` and `append_done` (~seconds to
   minutes on the hot path). After `done`, compact within hours. Event log
   records are permanent audit trail — compact every 90 days, snapshot
   first.
2. **Different reader**: outbox has one consumer (startup replay, crash-safety
   concern). Event log has multiple (reconciler + future memory-browser
   "history view" + future P4 evidence counters).
3. **Different schema**: outbox payloads are opaque to the log (e.g., full
   serialized messages for `OP_EXTRACT_FACTS`). Event log payloads are
   normalized summaries of state changes, designed for human readability.
4. **Merge risk**: if outbox and events share a file, compacting one forces
   compacting both, and a bug in either schema corrupts both. Splitting
   localizes blast radius.

Cost of splitting: two append paths instead of one. Minor — both are
`open+write+fsync` costing a few ms each. On the hot path the spawn site
does exactly one additional fsync (`outbox` append_pending). On a state
transition (e.g., reflection promotion) there's one fsync each (outbox done
+ event append) but they're independent orderings so can overlap.

### 3.8 API surface

```python
# memory/event_log.py (new)

class EventLog:
    # sync path
    def append(self, name: str, event_type: str, payload: dict) -> str: ...
    def read_since(self, name: str, after_event_id: str | None) -> list[dict]: ...
    def snapshot_and_compact(self, name: str, seed_events: list[dict]) -> int: ...

    # async duals (asyncio.to_thread wrappers)
    async def aappend(self, name, event_type, payload) -> str: ...
    async def aread_since(self, name, after_event_id) -> list[dict]: ...
    async def asnapshot_and_compact(self, name, seed_events) -> int: ...
```

Module-level event-type constants (`EVT_FACT_ADDED`, etc.) mirror `OP_*` in
`outbox.py`.

## 4. Implementation plan

Phase 2 is intentionally two sub-phases to keep reviews small:

- **P2.a**: `memory/event_log.py` + unit tests. Zero call sites wired. Pure
  infrastructure.
- **P2.b**: Wire into the **first three write sites** (`FactStore.extract_facts`,
  `ReflectionEngine.synthesize_reflections`, `ReflectionEngine.aauto_promote*`).
  Add startup reconciler skeleton + `events_applied.json` sentinel. Leave
  remaining write sites (persona, correction) for a P2.c follow-up.
- **P2.c** (deferred): Wire remaining sites; add the memory-browser "history"
  tab reading `events.ndjson`.

Rationale for staging: we want to see real event log growth / compaction
behavior under one subsystem before spreading to all.

## 5. Migration

On first P2 startup with an existing deployment:

1. If `events.ndjson` does not exist → create empty. `events_applied.json`
   written with `{last_applied_event_id: null, ts: now}`.
2. No backfill of prior history. The event log starts from the first new
   event post-upgrade. View files remain authoritative for any pre-upgrade
   state.
3. Reconciler is a no-op on first startup (tail is empty).

This means: **we lose audit trail for pre-upgrade data** but gain it from
upgrade onwards. Acceptable because the audit trail is a debugging tool,
not a correctness requirement.

## 6. Open questions for review

1. **Sentinel granularity**: `events_applied.json` tracks a single
   `last_applied_event_id`. If reconciler applies events incrementally and
   crashes mid-batch, the sentinel is not yet advanced; on next restart we
   replay from the original sentinel → some events re-applied. Since apply
   functions are idempotent this is safe, but is the double-apply a concern
   for `persona.fact_added` (could increment a counter twice)? Proposed:
   advance sentinel after every event, not every batch. Cost: N writes
   instead of 1. Alternative: per-event idempotency keys to short-circuit.

2. **Event ordering vs file position**: I'm assuming file position is the
   only truthful order. If a future P2.c consumer wants "all events since
   yesterday" it must scan and match `ts` — O(n). Is that OK, or do we need
   a secondary time index? For 10K-line budget, linear scan is fine
   (<50ms). Flag if disagreeing.

3. **Compaction seed events**: materializing current view as events is
   straightforward for facts / reflections (each entity becomes one
   `.*.snapshot_start` event). Persona is more complex because of nested
   entity_key → facts structure. Proposal: one snapshot event per
   (entity_key, fact_index) pair. Confirm.

4. **Multi-character event log location**: per-character is the current
   proposal. Alternative: single `memory_dir/events.ndjson` for all
   characters. Pro of aggregation: one global debug view. Con: breaks the
   ensure_character_dir model, bigger compaction, cross-character writes
   contend on one fsync. Recommendation: stay per-character.

5. **Is it worth writing `event_id` at all?** We could use `file_offset` as
   the id. Pros: free to generate. Cons: shifts on compaction. UUID is
   stable. Recommendation: keep UUID.

6. **P3 dependency**: P2 creates the foundation for P3's `LifecycleDriver`.
   LifecycleDriver listeners subscribe to event types. Is the event type
   set sufficient for the 5 existing lifecycle handlers listed in the
   project brief (fact archive, reflection state machine, rebuttal scan,
   persona suppress refresh, correction resolve)? Mapping:
   - `fact_archive` → listens `fact.absorbed` + wall time
   - `reflection.state_changed` → event
   - `rebuttal_scan` → listens `reflection.state_changed(to=confirmed)` for
     activation + cursor-based for sweep
   - `persona_suppress_refresh` → listens `persona.fact_added` (refresh on
     mention) + wall time
   - `correction_resolve` → listens `correction.queued`
   Looks sufficient.

7. **P4 dependency**: P4 evidence counters aggregate `reflection.rebutted`,
   feedback events, and mention deltas. The current event set covers
   these. P4 implementation should not need to extend the event schema —
   only add a read-side aggregator. Confirm before committing to the
   schema.

## 7. What this RFC explicitly rejects

- A separate `snapshots/` directory with numbered snapshots — over-kill
  for single-user data.
- A binary log format (SQLite WAL, LMDB) — violates the "JSON-readable
  views" constraint.
- Event-bus / pub-sub abstraction — single process, direct call is fine.
- Schema versioning in each record — if the schema changes, a one-off
  migration pass is simpler than runtime version dispatch.
- Cross-character events — no known use case.

## 8. Success criteria

After P2.b lands:

1. Force-kill `memory_server` between `event_log.aappend(reflection.synthesized)`
   and `asave_reflections` → new process boots → reconciler applies the event
   → `reflections.json` contains the reflection. Verify via integration test.
2. Repeat for `extract_facts` / `persona.fact_added`.
3. No regression on the 261 existing unit tests.
4. `events.ndjson` growth under a 24h synthetic workload stays below the
   10K-line compact threshold (roughly: ≤6 state transitions per minute per
   character).

## 9. Out-of-scope follow-ups

- Hot-path observability: streaming events to an in-memory ring buffer for
  `/health` to expose "events/minute" rate.
- UI: memory browser "event history" tab.
- Analytics: per-event-type LLM-cost attribution (needs token_tracker
  integration).
