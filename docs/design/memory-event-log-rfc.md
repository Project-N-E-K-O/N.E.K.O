# RFC: Memory subsystem event log + view derivation (P2)

Status: **Implemented (P2.a)** in PR #905 — infrastructure for event log,
reconciler scaffolding, and per-character manager locks has landed. P2.b
(producer wiring for the 12 event types) tracked separately per
`docs/design/p2-continuation-task.md`.

Historical branch context and revision history are preserved in the
Revision log below.

## Revision log

- **v1** (initial draft): 9 event types, single-file compaction with
  intermediate `events.snapshot`, `persona.fact_mentioned` bundled into
  `persona.fact_added`, per-batch sentinel advance proposed.
- **v4** (this revision, after review round 3) — addresses the two
  remaining blockers from v3:
  1. §3.4 pseudocode rewritten again: load NOW happens inside the lock
     via `sync_load_view` callable (was outside in v3, reintroducing the
     RMW race the lock was supposed to close). `_record_and_save` now
     takes three callbacks: `sync_load_view`, `sync_mutate_view`,
     `sync_save_view`. The actual mutation of the view is described as
     a function, not pre-applied data captured from enclosing scope.
  2. §3.4.2.1 added: specs a **new idempotency code** —
     `FACT_ALREADY_PRESENT` — on `PersonaManager.(a)add_fact`, with a
     pre-append dedup check on `id = "prom_<source_id>"` for the
     reflection-promotion call site. v3's claim that `FACT_DUP` dedup
     already existed was false (reviewer confirmed by reading
     persona.py:567-610). The dedup is now explicit P2.b.2 code work.
  Plus: §3.4.2.2 cites the `_build_correction_list` dedup
  (persona.py:677-681) as the convergence mechanism for the
  `FACT_QUEUED_CORRECTION` branch. §3.6 `_scan_head_and_count` edge cases
  (empty / corrupt first line). P2.b.1 persona.fact_added nuance (wire
  manual/correction-resolve call sites only; reflection-promotion wires
  in P2.b.2). §3.4 explicit note about sync-vs-async twin requirement.
- **v3** (review round 2) — addressed two remaining issues from v2:
  1. §3.4 pseudocode rewritten: the critical section is now wrapped in a
     single `asyncio.to_thread` hop, with the `threading.Lock` acquired
     entirely inside the worker thread. Prior v2 code erroneously used
     `async with threading.Lock()` which is not a valid async context
     manager and contradicted §3.4.1's own prose.
  2. §3.4.2 pseudocode rewritten to match the real control flow of
     `aauto_promote_stale` (reflection.py:731-783). The new model is
     "persona side first, then reflection side"; crash recovery converges
     through the producer's retry loop, not a reconciler self-heal path.
     This removes the sentinel-advance-during-self-heal ambiguity flagged
     in review round 2.
  Plus: compaction ordering relative to outbox replay spelled out (§3.5);
  implementation plan split into 5 smaller commits (P2.a.1 / P2.a.2 /
  P2.b.1 / P2.b.2 / P2.c); `_COMPACT_DAYS_THRESHOLD` wired to actual
  check, not just declared.
- **v2** (review round 1) — addressed 4 blockers:
  1. Compaction atomicity: eliminated intermediate `events.snapshot`; new
     body is written into `events.ndjson` via a single `os.replace` swap
     (§3.6). No dual-file reconciler ambiguity.
  2. `persona.fact_mentioned` / `persona.suppressed` are now their own
     event types with set-semantics payloads (§3.3, §3.4.3). Re-apply
     over the same event is overwrite, not delta.
  3. Lock discipline codified in §3.4.1. `ReflectionEngine` and
     `PersonaManager` grow per-character locks in P2.a (both currently
     lack one — concurrent `/reflect` + auto-promote race today).
  4. Compound-transaction semantics for `reflection.state_changed(promoted)
     + persona.fact_added` spelled out in §3.4.2.
  Plus: sentinel safe-defaults, per-event advance adopted, naive ISO8601
  `ts` convention, hash-only user content (privacy), forward-compat
  log-and-skip for unknown types, fsync budget quantified.

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

```text
memory_dir/<character>/events.ndjson       # append-only event log
memory_dir/<character>/events_applied.json # reconciler sentinel (last applied event_id)
```

One file per character. Lives beside `facts.json`, `reflections.json`, etc.
Compaction in §3.6 is a single-swap on `events.ndjson` — no intermediate
`events.snapshot` file is ever written.

### 3.2 Record schema

Every line is a JSON object:

```json
{
  "event_id": "<uuid4>",
  "type": "<event_type>",
  "ts": "<naive ISO8601>",
  "payload": { ... }
}
```

`event_id` is uuid4 (not sequential). Ordering is implicit in file position.
`ts` uses `datetime.now().isoformat()` (naive local time) to match the
convention used throughout the codebase (`facts.py`, `reflection.py`,
`persona.py` all store naive ISO8601). Event ordering relies on **file
position**, not `ts`; `ts` is for human audit only. Clock-rollback does NOT
cause event-log misordering (position is monotonic by construction).

Payload rules:
- No raw user content (see §3.3.1). Anything derived from user input is
  hashed with `hashlib.sha256(s.encode("utf-8")).hexdigest()`.
- All timestamps inside payloads use the same naive ISO8601 convention.

### 3.3 Event types (initial set: 12)

| # | Type | Payload | Written by |
|---|---|---|---|
| 1 | `fact.added` | `{fact_id, text_sha256, entity, importance}` | `FactStore.extract_facts` |
| 2 | `fact.absorbed` | `{fact_id, reflection_id}` | `FactStore.mark_absorbed` |
| 3 | `fact.archived` | `{fact_id, moved_to: "facts_archive.json"}` | `FactStore._archive_absorbed` |
| 4 | `reflection.synthesized` | `{reflection_id, text_sha256, entity, source_fact_ids}` | `ReflectionEngine.synthesize_reflections` |
| 5 | `reflection.state_changed` | `{reflection_id, from, to, reason}` | `ReflectionEngine.aconfirm_promotion / areject_promotion / aauto_promote_stale` |
| 6 | `reflection.surfaced` | `{reflection_id, next_eligible_at}` | `ReflectionEngine.arecord_surfaced` |
| 7 | `reflection.rebutted` | `{reflection_id, user_msg_sha256}` | `_periodic_rebuttal_loop` |
| 8 | `persona.fact_added` | `{entity_key, entry_id, text_sha256, source_reflection_id?, source_correction_id?}` | `PersonaManager.add_fact` |
| 9 | `persona.fact_mentioned` | `{entity_key, entry_id, recent_mentions_snapshot: [ISO8601, ...]}` — full list after mutation, set semantics | `PersonaManager.record_mentions` |
| 10 | `persona.suppressed` | `{entity_key, entry_id, suppress: bool, suppressed_at: ISO8601?}` | `PersonaManager._apply_record_mentions` (side effect of 9) |
| 11 | `correction.queued` | `{correction_id, conflict_summary_sha256}` | `PersonaManager.queue_correction` |
| 12 | `correction.resolved` | `{correction_id, action}` | `PersonaManager.resolve_corrections` |

#### 3.3.1 Idempotency-critical design notes

- **No raw user content in payloads.** Per `.agent/rules/neko-guide.md` rule 3
  (raw conversation only via `print`, never `logger`), the event log — which
  lives on disk and is searchable — MUST hash anything that came from user
  input. `text_sha256`, `user_msg_sha256`, `conflict_summary_sha256` encode
  identity for dedup without storing plaintext. If a debug dump of excerpts
  is ever needed, it goes via `print` in a separate diagnostic path, not in
  the event log.
- **`persona.fact_mentioned` carries the full post-mutation `recent_mentions`
  list**, not a delta. `recent_mentions` in persona is a bounded FIFO (filtered
  to a 5h window in `_apply_record_mentions` at `persona.py:864`); replaying a
  `+1` delta twice would double-count within the window and prematurely trip
  `suppress`. A full-snapshot payload means re-apply is an overwrite, which is
  idempotent by construction. Cost: payload size grows with `SUPPRESS_MENTION_LIMIT`
  (currently ~10 ISO timestamps, <500 bytes).
- **`persona.suppressed` is a separate event** so a re-apply that overwrites
  `suppress=True/False` is straightforward. Keeping it bundled inside
  `fact_mentioned` would blur the semantic boundary.
- **`reflection.state_changed` is emitted once per transition**, not once per
  batch. `aauto_promote_stale` iterates reflections; each transition it
  produces is its own event + its own save. See compound-transaction rules
  in §3.4.2 for the promotion→`persona.fact_added` chain.
- **`_mark_surfaced_handled` / `_batch_mark_surfaced_handled`** (reflection.py
  657-668, 801-822) write `surfaced.json` to record user feedback on surfaced
  reflections. No dedicated event; these writes are causally derived from
  `reflection.state_changed` and reconciler handlers re-derive `surfaced.json`
  from the reflection status transition.

### 3.4 Write order rule

**Load, event append, mutate, save, and sentinel advance all run in one
critical section — the entire block wrapped in a single `asyncio.to_thread`
worker, with a per-character `threading.Lock` acquired inside.**

The load **MUST** happen inside the lock. If the call site loads the view
upfront and hands a pre-loaded object to the critical section, two
coroutines can each `aload`, each mutate their own copy, and the second
save clobbers the first — reintroducing the exact RMW race the lock is
there to eliminate.

The mutation is described as a callable, not pre-applied data:

```python
# Async entry point — called from memory_server handlers / periodic loops.
# Single to_thread hop hands the whole critical section to a worker thread;
# the synchronous per-character lock covers load → mutate → append → save →
# sentinel advance.
async def _record_and_save(
    lanlan_name: str,
    event_type: str,
    payload: dict,
    *,
    sync_load_view,        # callable: (name) -> view_obj
    sync_mutate_view,      # callable: (view_obj) -> None (mutate in-place)
    sync_save_view,        # callable: (name, view_obj) -> None
) -> str:
    return await asyncio.to_thread(
        _sync_record_and_save,
        lanlan_name, event_type, payload,
        sync_load_view, sync_mutate_view, sync_save_view,
    )

def _sync_record_and_save(
    name, event_type, payload,
    sync_load_view, sync_mutate_view, sync_save_view,
) -> str:
    with _character_lock(name):   # threading.Lock — never held across await
        view = sync_load_view(name)                         # fresh read under lock
        event_id = event_log.append(name, event_type, payload)  # ← append FIRST
        sync_mutate_view(view)                              # in-memory mutation
        sync_save_view(name, view)                          # atomic_write_json
        event_log.advance_sentinel(name, event_id)
        return event_id
```

**Append-first ordering rationale:** `sync_load_view` routinely returns the
manager's shared cache object (e.g. `self._personas[name]`). If `mutate` ran
before `append`, an `append` failure (fsync OSError, disk full) would leave
the shared cache dirty while no event is on disk; any subsequent normal
save would flush those "eventless" changes, breaking the event↔view
correspondence and leaving the reconciler nothing to compensate against.
`append → mutate → save` keeps the invariant: cache is only ever dirtied
after an event is durably persisted.

**On sync-vs-async twins**: `sync_load_view` / `sync_save_view` must be the
**synchronous** twins (`FactStore.load_facts` / `save_facts`, not
`aload_facts` / `asave_facts`). Using them inside an `asyncio.to_thread`
worker is safe — we're on a worker thread, not the event loop — and the
sync twins avoid a pointless `asyncio.to_thread` re-hop. Every memory
manager already exposes both twins (neko-guide.md rule on 对偶性); the
sync twins currently exist but are used only in migration paths.

Rationale for the whole-block-in-one-to_thread approach:
- The `threading.Lock` is acquired and released entirely inside the worker
  thread, never held across an asyncio `await` boundary.
- Sibling coroutines on the same event loop are never blocked directly;
  they go through the thread pool like all other I/O.
- The lock holds for one load + one append + one mutate + one
  `atomic_write_json` + one sentinel write. Worst case on slow disks: a
  few tens of ms, dominated by 4 sequential fsyncs (§3.5 budget).

Failure modes:
- Event append fails → raises, no view change (save hasn't run), no
  sentinel advance. Lock released by `with` exit.
- Event appended, view save fails (pre-rename tempfile IO error) →
  reconciler on next startup replays the event onto the old view.
- Event + view both succeed, sentinel advance fails → next startup
  re-applies the tail; apply is idempotent (§3.4.3).
- Event + view + sentinel all succeed, process killed right after the lock
  releases but before the caller returns → consistent; next startup reads
  the already-applied state.

#### 3.4.1 Lock discipline

Every event-emitting write site runs inside a per-character
`threading.Lock` spanning **load → append event → save view → advance
sentinel**, held inside the single `asyncio.to_thread` worker described
above. Never held across `await` at the asyncio level.

Current lock holders:
- `FactStore._locks` — already exists (`memory/facts.py:49`).
- `CursorStore._locks` — already exists (P0).
- `Outbox._locks` — already exists (P1.b).

New locks to add in P2.a:
- `ReflectionEngine._locks` — currently missing; `/reflect` and the periodic
  auto-promote loop race today. P2.a adds per-character lock as part of
  event-log wiring.
- `PersonaManager._locks` — currently missing; `add_fact`, `resolve_corrections`
  and `record_mentions` all race. Added in P2.a for the same reason.

All locks are per-character `threading.Lock`, acquired inside the
`asyncio.to_thread` worker that does load/append/save. The asyncio caller
never holds the lock across `await`.

#### 3.4.2 Compound transactions

Some state transitions emit more than one event. The canonical example:
`ReflectionEngine.aauto_promote_stale` (reflection.py:731-783) walks reflections
and for each `confirmed → promoted` transition calls
`PersonaManager.aadd_fact`. The real code today interleaves:

```text
for r in reflections:                       # in-memory iteration
    if eligible_for_promote(r):
        result = await persona.aadd_fact(...)  # persona-side mutation + save
        if result == FACT_ADDED:
            r.status = 'promoted'
            ...
# single asave_reflections at the END
```

So: persona is saved N times (once per promotion) while reflections are
saved once at the end. P2.b.2 must restructure this so each mutation's
save is in the same critical section as its event append. Additionally,
**P2.b.2 must add a new idempotency code to `PersonaManager.add_fact` /
`aadd_fact`**: see §3.4.2.1.

Proposed restructured loop. Note each transition only carries the
`(reflection_id, target_state)` tuple out of the computation phase —
the actual load + mutate happens inside `_record_and_save` (§3.4):

```python
async def aauto_promote_stale(self, name):
    # Phase 1: decide what transitions should happen, pure (no I/O after aload).
    initial = await self.aload_reflections(name)
    transitions = _compute_transitions(initial, now=datetime.now())
    # -> list[Transition(kind='confirm'|'promote', rid=..., entity=..., text=...)]

    applied = 0
    for tr in transitions:
        if tr.kind == 'confirm':
            # single _record_and_save: load/append/mutate/save/sentinel under lock
            await _record_and_save(
                name,
                event_type="reflection.state_changed",
                payload={"reflection_id": tr.rid, "from": "pending", "to": "confirmed"},
                sync_load_view=self.load_reflections,
                sync_mutate_view=lambda view, rid=tr.rid: _mark_status(view, rid, "confirmed"),
                sync_save_view=self.save_reflections,
            )
            applied += 1

        elif tr.kind == 'promote':
            # Persona side FIRST. aadd_fact is itself a _record_and_save that
            # emits persona.fact_added under the persona lock. It returns an
            # idempotency-aware status code (see §3.4.2.1).
            result = await self._persona_manager.aadd_fact(
                name, tr.text, entity=tr.entity,
                source='reflection', source_id=tr.rid,
            )
            if result in (PersonaManager.FACT_ADDED,
                          PersonaManager.FACT_ALREADY_PRESENT):  # new code
                # Persona side is now reconciled with the promotion intent
                # (either we just added or it was already there from a
                # previously-crashed attempt). Emit the reflection side.
                await _record_and_save(
                    name,
                    event_type="reflection.state_changed",
                    payload={"reflection_id": tr.rid, "from": "confirmed", "to": "promoted"},
                    sync_load_view=self.load_reflections,
                    sync_mutate_view=lambda view, rid=tr.rid: _mark_status(view, rid, "promoted"),
                    sync_save_view=self.save_reflections,
                )
                applied += 1
            elif result == PersonaManager.FACT_REJECTED_CARD:
                await _record_and_save(
                    name,
                    event_type="reflection.state_changed",
                    payload={"reflection_id": tr.rid, "from": "confirmed", "to": "denied",
                             "reason": "contradicts_character_card"},
                    sync_load_view=self.load_reflections,
                    sync_mutate_view=lambda view, rid=tr.rid: _mark_denied_card(view, rid),
                    sync_save_view=self.save_reflections,
                )
                applied += 1
            # result == FACT_QUEUED_CORRECTION: aadd_fact already emitted
            # correction.queued under its own lock; reflection stays in
            # 'confirmed' until the correction is resolved. Convergence is
            # via the correction-queue's own dedup (§3.4.2.2).
    return applied
```

Crash recovery, explicit:
- Crash after `aadd_fact` success but before reflection state_changed
  emits. Restart reads event log tail — there is a
  `persona.fact_added(source_reflection_id=R)` with no matching
  `reflection.state_changed(R, promoted)`. Reconciler applies the
  `persona.fact_added` event (idempotent by `source_id` dedup in the
  apply handler). Reflection still shows `confirmed`. Next
  `aauto_promote_stale` cycle finds `R` in `confirmed` state,
  time-eligible, calls `aadd_fact(..., source_id=R)` — which now returns
  `FACT_ALREADY_PRESENT` (§3.4.2.1) instead of inserting a duplicate.
  Loop then emits the reflection `state_changed`. Converges.
- Crash inside `aadd_fact` mid-save → standard `_record_and_save`
  recovery applies: event logged OR nothing logged; never half-logged.

**Reconciler self-heal is not needed.** The apply handlers for
`reflection.state_changed` and `persona.fact_added` are pure state-setters;
they do NOT invoke `aadd_fact` from inside the reconciler. Convergence is
the `aauto_promote_stale` retry loop's responsibility.

Chain depth: compound transitions fan out at most 2 levels
(state_changed → fact_added; correction.queued → correction.resolved on a
later user action). Unbounded recursion is impossible because apply
handlers don't re-emit.

#### 3.4.2.1 New idempotency code on `PersonaManager.add_fact`

P2.b.2 must add a new return code and a pre-append dedup check in
`memory/persona.py:add_fact` / `aadd_fact`:

```python
FACT_ALREADY_PRESENT = 'already_present'  # new, alongside FACT_ADDED etc.

# inside (a)add_fact, before appending:
if source == 'reflection' and source_id:
    expected_id = f"prom_{source_id}"
    if any(entry.get('id') == expected_id for entry in section_facts):
        return self.FACT_ALREADY_PRESENT
```

This makes `add_fact` idempotent under `(source, source_id)` — a repeated
call with the same `source_id` returns `FACT_ALREADY_PRESENT` without
creating a duplicate persona entry. The field `id = "prom_<source_id>"`
is already produced by `_build_fact_entry` (persona.py:559-560), so the
dedup key already exists on disk.

Callers (currently only `aauto_promote_stale`) must treat
`FACT_ALREADY_PRESENT` as equivalent to `FACT_ADDED` for the purpose of
"should I emit the reflection state_changed event?" (both mean persona
is in the desired state).

No behavior change for the manual-add / correction-resolve call sites:
they pass `source='manual'` or `source='correction'`, neither of which
has a stable source_id for dedup; the check is skipped and the old
append-always path is preserved.

#### 3.4.2.2 Correction-queue convergence

For the `FACT_QUEUED_CORRECTION` branch: `aauto_promote_stale` keeps the
reflection in `confirmed` state. `aadd_fact` called `_aqueue_correction`
internally, which dedups by `(old_text, new_text, entity)` tuple
(persona.py:677-681 — `_build_correction_list` filters duplicates).
Repeated calls from the `aauto_promote_stale` retry loop will not double-
queue the same correction. The reflection only leaves `confirmed` when
the user resolves (or the LLM auto-resolves) the correction via
`resolve_corrections`, which emits `correction.resolved` and then
either re-promotes (via another `aadd_fact` that now succeeds since
the contradicting fact is gone) or sticks with the older fact.

#### 3.4.3 Idempotency contract per event type

Reconciler apply must be idempotent. Per-type guarantees:

| Event type | Idempotent by | Notes |
|---|---|---|
| `fact.added` | SHA-256 dedup in `FactStore` | Existing behavior |
| `fact.absorbed` | `absorbed=True` set only if currently False | Already idempotent |
| `fact.archived` | Move only if fact still in active list | |
| `reflection.synthesized` | id dedup (P1.a) | Already idempotent |
| `reflection.state_changed` | Overwrite `status` + `feedback` to target values | Monotonic forward, so re-apply is safe; handler is a pure state-setter and does NOT invoke compound-transaction side effects (those converge via the producer's retry loop — §3.4.2) |
| `reflection.surfaced` | Overwrite next cooldown to payload value | |
| `reflection.rebutted` | Set `status=denied` (if not already), append deduped user excerpt hash | |
| `persona.fact_added` | `source_reflection_id` / `source_correction_id` dedup | Add only if not present |
| `persona.fact_mentioned` | **Payload carries full post-mutation `recent_mentions` list** (set semantics, NOT a delta) — re-apply is overwrite | See blocker §3.3.1 below |
| `persona.suppressed` | Overwrite `suppress` + `suppressed_at` to payload values | |
| `correction.queued` | `correction_id` dedup | |
| `correction.resolved` | `correction_id` + status overwrite | |

### 3.5 Startup reconciliation

Startup order:
1. Per-character `event_log.acompact_if_needed(name)` (§3.6) — runs FIRST.
   This matters because post-compact the old body is gone; if we replayed
   outbox ops before compaction, a replay handler's `aappend` would land
   in the old body that is about to be swapped. Run compaction first so
   subsequent appends land in the fresh body.
2. Per-character `event_log.areconcile_views(name)` (this section).
3. Per-character `outbox.apending_ops(name)` replay (P1.c behavior).

The compaction → reconciliation → replay chain is all per-character and
can be parallelized via `asyncio.gather` across characters, but within a
character the three steps are strictly sequential.

On `memory_server` startup, step 2 runs:

```python
async def _reconcile_views(lanlan_name):
    last_applied = await _read_last_applied_event_id(lanlan_name)
    tail = await event_log.aread_since(lanlan_name, last_applied)
    for event in tail:
        await _apply_event_to_view(lanlan_name, event)
        await _advance_sentinel(lanlan_name, event['event_id'])  # per-event advance
```

**Sentinel**: `memory_dir/<character>/events_applied.json` stores
`{last_applied_event_id, ts}`. Written via `atomic_write_json_async` **after**
each event's apply handler returns successfully.

**Sentinel safe defaults**:
- File missing → `last_applied_event_id = null`. Reconciler replays the full
  current body (post-compaction, at most the snapshot-start seed set, all
  idempotent).
- File corrupt / unparsable → log warning, treat as missing. Never crash
  startup on sentinel.
- `last_applied_event_id` not found in current body → same as missing (the
  event was compacted away). Replay full current body.

**Per-event advance vs per-batch advance**: the RFC picks per-event
advance despite the write-amplification cost. Rationale: on a cold-boot
with, say, 50 tail events, per-event advance is 50 fsyncs of a small
(~100 byte) JSON file = ~500ms total on a commodity SSD. Per-batch would
be 1 fsync, but a crash mid-batch would re-apply up to N events. Some of
our apply paths call handlers that have their own external side effects
(e.g., `reflection.state_changed(promoted)` → compound transaction to
persona in §3.4.2) — the reconciler handler itself is idempotent, but
fan-out reduction matters. Per-event advance wins.

**Hot-path fsync budget**: on a normal state transition (e.g., a
reflection promoting),
1. outbox `append_done` — 1 fsync
2. `event_log.aappend` — 1 fsync
3. view save (`atomic_write_json_async`) — 1 fsync via tmpfile + rename
4. sentinel `aadvance_sentinel` — 1 fsync

Total: 4 fsyncs per state transition. On a commodity SSD (~200μs each)
this is ≤1ms; on rotational disk or encrypted FUSE it may spike to
10-50ms. Acceptable for the user-facing latency of the two hot paths
(`/process` and `/reflect`): both already await LLM calls measured in
seconds, so an extra ≤50ms is invisible.

**Apply semantics**: each `_apply_<type>` is defined in §3.4.3's
idempotency contract. Unknown event types (future schema additions loaded
on an older binary) are **logged and skipped**, never crash reconciliation
— this keeps forward-compatibility degradation graceful.

### 3.6 Compaction

Thresholds (module-level constants in `memory/event_log.py`):

```python
_COMPACT_LINES_THRESHOLD = 10_000   # file line count
_COMPACT_DAYS_THRESHOLD = 90        # age of oldest line (ts field)
```

Wiring both checks explicitly:

```python
def _should_compact(self, name: str) -> bool:
    path = self._events_path(name)
    if not os.path.exists(path):
        return False
    line_count, oldest_ts = self._scan_head_and_count(path)
    if line_count >= _COMPACT_LINES_THRESHOLD:
        return True
    if oldest_ts is not None:
        age_days = (datetime.now() - oldest_ts).total_seconds() / 86400
        if age_days >= _COMPACT_DAYS_THRESHOLD:
            return True
    return False
```

`_scan_head_and_count` reads the first line (oldest — file is append-only) for
its `ts`, then counts remaining lines without parsing payloads. O(n) on line
count, O(1) on parse work.

Edge cases:
- Empty file → `(0, None)`; neither threshold triggers; compaction skipped.
- First line missing or unparseable → return `(line_count, None)`; only the
  line-count threshold applies (age check is unavailable). Log a warning so
  the corruption is visible.
- Unreadable file → return `(0, None)` + log warning; compaction skipped
  (best to not touch an already-unreadable file).

On every startup, if `_should_compact(name)` returns True:

1. Read current views (facts.json / reflections.json / persona.json).
2. Derive a starting-point event list: one `.*.snapshot_start` event per live
   entity (facts by `fact_id`, reflections by `reflection_id`, persona entries
   by `(entity_key, entry_id)`). See §6 Q3 for the keys.
3. Serialize the full starting list as the **new body of events.ndjson**.
4. `atomic_write_text(events.ndjson, new_body)` — single `os.replace` swap.
5. After swap succeeds, reset `events_applied.json` to `{last_applied_event_id: null, ts: now}`.

Crash safety: there is **no intermediate `events.snapshot` file**. The swap
is one atomic rename at the filesystem level (`atomic_write_text` is built
on `tempfile + os.replace`). Before the rename: old events.ndjson is the
truth. After: new compacted body is the truth. No window where the
reconciler sees both.

Sentinel reset is ordered AFTER the swap so a crash between swap and
sentinel reset leaves: new compacted body + stale sentinel pointing at an
event id that no longer exists. Reconciler handles missing
`last_applied_event_id` in the current body by replaying the full compacted
body (the snapshot-start events, which are all idempotent). Cost: at most
one extra replay of the compacted seed set on the post-crash boot.

Optional keep-historical mode (deferred to P2.c): if users want a
detailed history trail, the pre-compaction body can be archived to
`events_archive/<timestamp>.ndjson` before swap. Default is "drop history
on compact" to match the non-goal "not full event sourcing".

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

Cost of splitting: two append paths instead of one. The outbox append
(`append_pending` / `append_done`) and the event-log append are sequential
on the same event loop task inside `_run_outbox_op` — they do NOT overlap
without explicit `asyncio.gather`, and we do not gather them (ordering
matters for crash recovery — the event log records the state AFTER the
outbox op succeeded). Total fsync budget accounting is in §3.5.

### 3.8 API surface

```python
# memory/event_log.py (new)

class EventLog:
    # sync path — call from startup / migration / tests ONLY.
    # MUST NOT be called from async def (per neko-guide zero-blocking rule).
    def append(self, name: str, event_type: str, payload: dict) -> str: ...
    def read_since(self, name: str, after_event_id: str | None) -> list[dict]: ...
    def snapshot_and_compact(self, name: str, seed_events: list[dict]) -> int: ...
    def read_sentinel(self, name: str) -> str | None: ...
    def advance_sentinel(self, name: str, event_id: str) -> None: ...

    # async duals (asyncio.to_thread wrappers) — call from async def code paths.
    async def aappend(self, name, event_type, payload) -> str: ...
    async def aread_since(self, name, after_event_id) -> list[dict]: ...
    async def asnapshot_and_compact(self, name, seed_events) -> int: ...
    async def aread_sentinel(self, name) -> str | None: ...
    async def aadvance_sentinel(self, name, event_id) -> None: ...
```

Module-level event-type constants (`EVT_FACT_ADDED`, etc.) mirror `OP_*` in
`outbox.py`. The sync/async pairing mirrors `CursorStore` and `Outbox`;
both the pattern and the zero-blocking constraint are load-bearing.

Note: the final P2.a.1 implementation renames `snapshot_and_compact` to
`compact_if_needed` (plus async twin `acompact_if_needed`). The new name
reflects that the method does threshold check + compact in one call; the
standalone "compact without threshold check" entry point is the private
`_should_compact_unlocked` + body swap, not exposed. RFC API signatures
above are kept for historical continuity of the design review.

## 4. Implementation plan

Phase 2 is split into five commits/PRs to keep each blast radius small:

- **P2.a.1**: `memory/event_log.py` module + unit tests. Pure new code, no
  call sites wired. Tests cover: append/read_since/sentinel round-trip,
  compaction atomicity (crash simulations), corrupt-file tolerance,
  sync/async duality, unknown-event-type log-and-skip on reconciler.
- **P2.a.2**: Add per-character `threading.Lock` to `ReflectionEngine` and
  `PersonaManager` (they currently lack one — concurrent `/reflect` +
  auto-promote race today even without event log). Zero event log wiring;
  this is a standalone resilience improvement. Regression test:
  concurrent `/reflect` + `/process` + `_periodic_auto_promote_loop` on
  same character over 60s, no deadlock, no corrupted JSON.
- **P2.b.1**: Wire the **8 non-compound event types** at their producers
  (facts 1-3, reflection 4/6/7, persona 9/10, correction 11/12).
  Reconciler handlers for these 8 types registered. Unknown-type log-
  and-skip protects against the partial-wiring migration window for the
  remaining 4 (reflection 5 `state_changed`, persona 8 `fact_added`).
  This is safe precisely because an unwired producer simply never emits,
  and an unwired apply handler silently ignores events it doesn't
  understand.

  Nuance: `persona.fact_added` has two call-site categories —
  (a) manual-add / correction-resolve (non-compound), and
  (b) reflection-promotion (compound, depends on §3.4.2 restructure).
  P2.b.1 wires (a) only. Call site (b) is wired as part of P2.b.2 so the
  compound pair lands together. Concretely: `PersonaManager.add_fact`
  called with `source='manual'` or `source='correction'` emits the event
  in P2.b.1; the `source='reflection'` path emits only in P2.b.2.
- **P2.b.2**: Wire the **compound pair** (reflection 5 `state_changed` +
  persona 8 `fact_added`). Restructure `ReflectionEngine.aauto_promote_stale`
  per §3.4.2 pseudocode. Restructure `PersonaManager.resolve_corrections`
  similarly. This commit is the only one that changes control flow of
  existing production code; isolated for reviewability.
- **P2.c** (deferred follow-up): memory-browser UI "history" tab reading
  `events.ndjson`; optional `events_archive/` retention on compact.

Tests required across P2.a.1 + P2.a.2 + P2.b.1 + P2.b.2 before merge to
main:
- Unit tests for each apply handler (idempotency contract from §3.4.3).
- Integration test: force-kill between `aappend` and `save_view` for each
  of the 4 write sites listed in §8 → restart → verify view consistency.
- Compaction test: seed 10K+ events → trigger compaction → verify single-
  rename atomicity (no intermediate files remain, no double-apply on
  post-compact boot).
- Deadlock regression for the new locks (P2.a.2).
- Compound-transaction convergence test for P2.b.2: start a promotion,
  kill after `aadd_fact` succeeds but before `reflection.state_changed`
  emits, restart, run one more `aauto_promote_stale` cycle, verify
  convergence (reflection ends up `promoted`, persona has exactly one
  fact with `source_reflection_id`).

The "all-or-nothing" argument in v2 was wrong. Non-compound types can
land safely before the compound pair, because reconciler unknown-type
log-and-skip makes partial wiring forward-compatible at the schema level.
The only real constraint is that P2.b.2 MUST land before P2.c (history
UI) because the UI would otherwise show an incomplete timeline.

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

## 6. Open questions — resolved after first review round

The original RFC opened 7 questions; the design review resolved them as
follows:

1. **Sentinel granularity** → per-event advance, adopted into §3.5. Write
   amplification quantified (4 fsyncs per hot-path transition); cost deemed
   acceptable versus the LLM latency already on the path.

2. **Event ordering vs file position** → file position only, no secondary
   time index. At 10K-line budget linear scan is <50ms. Deferred until a
   P2.c consumer actually needs range queries.

3. **Compaction seed events** → one seed event per stable entity id:
   `fact_id`, `reflection_id`, `(entity_key, entry_id)` for persona. Using
   `entry['id']` (not list index) because `persona.py` entry ids are
   stable strings (`prom_<source_id>` / `manual_<ts>_<hash>`); list order
   is not.

4. **Multi-character location** → per-character (`ensure_character_dir`
   model retained). Cross-character aggregate views can be built at query
   time if ever needed.

5. **`event_id` form** → UUID4. File offsets shift on compaction and
   cannot be used as a stable external reference.

6. **P3 dependency** → the event type set is **structurally plausible**
   for the 5 LifecycleDriver handlers listed in the project brief, but
   final confirmation belongs in the P3 RFC, not here. Do NOT block P2
   implementation on P3's eventual schema requirements.

7. **P4 dependency** → same as 6. The evidence counter design is not
   written. The event set likely supports it (hash-based dedup +
   `reflection.rebutted` + `persona.fact_mentioned` are the natural
   aggregation inputs), but this is speculation. P4 RFC may need schema
   additions; if so, P4.a will add them as new event types — forward
   compat works because of the unknown-type log-and-skip rule in §3.5.

New questions opened by the design review round (still open — intentionally
deferred, not blockers):

8. **Archive-on-compact**: whether to keep compacted bodies under
   `events_archive/<ts>.ndjson`. Default is drop (§3.6). Memory browser
   "history" tab (P2.c) is the user for an archive; revisit then.

9. **Privacy of `user_msg_sha256`**: hashing is chosen over plaintext
   excerpts (§3.3.1). The hash is still not pseudo-anonymous (short
   user messages can be brute-forced); but this is a local-only log in
   a single-user context, so the privacy model is "the user controls
   the file". No further action.

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

After P2.a + P2.b both land:

1. Force-kill `memory_server` between `event_log.aappend(type=X)` and the
   corresponding view save → new process boots → reconciler re-applies
   event → view converges. Verify integration tests for all four
   "interesting" X: `fact.added`, `reflection.synthesized`,
   `reflection.state_changed(promoted)`, `persona.fact_added`.
2. Re-apply idempotency: run reconciler twice on the same tail → view
   identical (no duplicated facts / no double-suppressed entries / no
   inflated `recent_mentions`).
3. Compaction atomicity: simulate crash between snapshot body write and
   sentinel reset → boot → no double-apply, no dangling `events.snapshot`
   or `.tmp` files.
4. No regression on existing unit tests (currently 261).
5. `events.ndjson` growth under a 24h synthetic workload stays below the
   10K-line compact threshold at default heuristic (roughly: ≤6 state
   transitions per minute per character).
6. Per-character lock addition in `ReflectionEngine` / `PersonaManager` does
   not introduce new deadlocks under concurrent `/reflect` +
   `_periodic_auto_promote_loop` + `/process` workload. Integration test
   with 3 concurrent async producers.

## 9. Out-of-scope follow-ups

- Hot-path observability: streaming events to an in-memory ring buffer for
  `/health` to expose "events/minute" rate.
- UI: memory browser "event history" tab.
- Analytics: per-event-type LLM-cost attribution (needs token_tracker
  integration).
