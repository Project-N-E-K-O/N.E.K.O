# RFC: Memory subsystem event log + view derivation (P2)

Status: **Draft v2** — revised after first design review; awaiting second
review before implementation.

Branch context: `claude/awesome-goldberg-omrDx`, built on top of P0 (persistent
rebuttal cursor) and P1 (reflection id determinism + outbox).

## Revision log

- **v1** (initial draft): 9 event types, single-file compaction with
  intermediate `events.snapshot`, `persona.fact_mentioned` bundled into
  `persona.fact_added`, per-batch sentinel advance proposed.
- **v2** (this revision, after review round 1) — addresses 4 blockers:
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

**Event is appended before the view is written, inside a per-character lock.**

```python
async def _record_and_save(lanlan_name, event_type, payload, save_view):
    async with _character_lock(lanlan_name):  # see §3.4.1
        await event_log.aappend(lanlan_name, event_type, payload)
        await save_view()                      # atomic_write_json_async
        await event_log.aadvance_sentinel(lanlan_name)  # see §3.5
```

Rationale: an event line in the log with no corresponding view update is
"reconcilable on restart" (we know what to apply). A view change with no event
line is "silent data — unrecoverable on audit". We prefer the former.

Failure modes:
- Event append fails → caller raises, no view change, no sentinel advance, no
  partial state.
- Event appended, view save fails (disk full mid-`os.replace`'s rename window
  is impossible since atomic_write_json swaps in one syscall; but the
  pre-rename tempfile write can fail) → reconciler on next startup applies
  the event onto the stale view.
- Event + view both succeed, sentinel advance fails → next startup re-applies
  the tail; apply is idempotent (§3.4.3).

#### 3.4.1 Lock discipline

Every event-emitting write site runs inside a per-character
`threading.Lock` spanning **load → append event → save view → advance
sentinel** (composed via `asyncio.to_thread`, never held across `await` at
the asyncio level, matching the neko-guide rule).

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

Some state transitions emit more than one event. The canonical example: a
reflection going `confirmed → promoted` transitively calls
`PersonaManager.aadd_fact` (persona gains a new fact).

**Rule**: each `_record_and_save` call is independently atomic. Compound
transitions emit events in causal order, each with its own save:

```python
# reflection.py: aauto_promote_stale, promotion branch
async with _reflection_lock(name):
    await _record_and_save(name, "reflection.state_changed",
                           {"reflection_id": rid, "from": "confirmed", "to": "promoted"},
                           save_reflections)
async with _persona_lock(name):
    await _record_and_save(name, "persona.fact_added",
                           {"entity_key": ek, "fact": fact_text, "source_reflection_id": rid},
                           save_persona)
```

Crash between the two blocks: restart sees `reflection.state_changed(promoted)`
in the log and `persona.fact_added` absent. Reconciler's `_apply_state_changed`
checks: "is there a corresponding persona fact with `source_reflection_id=rid`?"
If no, the reconciler invokes `PersonaManager.aadd_fact` directly (which
emits its own event). This makes the state_changed handler structurally
responsible for repair, not just playback. Tradeoff: reconciler handlers are
slightly smarter than raw appliers, but recovery is automatic without
introducing two-phase commit.

The alternative — a single batch event `reflection.promoted_with_persona_fact`
— is rejected because it couples two subsystems' schemas and breaks the
"each event is a minimal state-change record" principle.

#### 3.4.3 Idempotency contract per event type

Reconciler apply must be idempotent. Per-type guarantees:

| Event type | Idempotent by | Notes |
|---|---|---|
| `fact.added` | SHA-256 dedup in `FactStore` | Existing behavior |
| `fact.absorbed` | `absorbed=True` set only if currently False | Already idempotent |
| `fact.archived` | Move only if fact still in active list | |
| `reflection.synthesized` | id dedup (P1.a) | Already idempotent |
| `reflection.state_changed` | Overwrite `status` + `feedback` to target values | Monotonic forward, so re-apply is safe; handler re-checks compound-transaction invariants |
| `reflection.surfaced` | Overwrite next cooldown to payload value | |
| `reflection.rebutted` | Set `status=denied` (if not already), append deduped user excerpt hash | |
| `persona.fact_added` | `source_reflection_id` / `source_correction_id` dedup | Add only if not present |
| `persona.fact_mentioned` | **Payload carries full post-mutation `recent_mentions` list** (set semantics, NOT a delta) — re-apply is overwrite | See blocker §3.3.1 below |
| `persona.suppressed` | Overwrite `suppress` + `suppressed_at` to payload values | |
| `correction.queued` | `correction_id` dedup | |
| `correction.resolved` | `correction_id` + status overwrite | |

### 3.5 Startup reconciliation

On `memory_server` startup, after P1 outbox replay, run:

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
_COMPACT_LINES_THRESHOLD = 10_000
_COMPACT_DAYS_THRESHOLD = 90
```

On every startup, if `events.ndjson` exceeds either threshold:

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

## 4. Implementation plan

Phase 2 is intentionally three sub-phases to keep reviews small:

- **P2.a**: `memory/event_log.py` + unit tests + add per-character lock to
  `ReflectionEngine` and `PersonaManager` (see §3.4.1 — both currently lack
  one and the event-log rules require it). Reconciler skeleton with handler
  registry + unknown-type log-and-skip. Zero production call sites wired.
  Pure infrastructure.
- **P2.b**: Wire ALL 12 event types at their producers in one go. Rationale:
  §3.4.2's compound-transaction rule means partial wiring creates a state
  where `reflection.state_changed(promoted)` is logged but no corresponding
  `persona.fact_added` event exists, and reconciler repair logic has to
  special-case "the missing event happened in the old pre-P2.b world vs
  the new world". Doing all 12 at once avoids two migration states.
- **P2.c** (deferred, out-of-scope follow-up): memory-browser UI "history"
  tab reading `events.ndjson`; optional `events_archive/` retention.

Tests required for P2.a/b merge:
- Unit tests for each apply handler (idempotency contract from §3.4.3).
- Integration test: force-kill between `aappend` and `asave_view` for each
  of the 4 write sites listed in §8 → restart → verify view consistency.
- Compaction test: seed 10K+ events → trigger compaction → verify single
  rename atomicity (no intermediate files remain, no double-apply on
  post-compact boot).

Rationale for 3-way split revision from prior 3-way: the prior P2.b
("wire 3 write sites") ignored the compound-transaction constraint
introduced in §3.4.2. Doing fewer than all writers leaves orphan events
with no matching apply handler — the reconciler's safe-default (skip
unknown) would silently mask real wiring bugs. Either all or nothing.

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
