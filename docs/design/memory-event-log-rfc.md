# Memory event journal: implemented design record

This page records the event-journal design that is implemented in the current
memory subsystem. It is intentionally an architectural record, not a line-by-line
construction plan. For the user-facing lifecycle, start with
[Memory System](/architecture/memory-system).

## Status and scope

Each character can have an `events.ndjson` journal and an
`events_applied.json` replay sentinel beside its JSON views. Writers only ever
append. The unapplied part of the journal (the sentinel's line and everything
after it) is always kept in full; the already-applied history in front of it is
pruned by a background task and kept only within a bounded window (see
[Compaction](#compaction)). The journal gives selected evidence and lifecycle
mutations an ordered, recent audit trail and a recovery path if the process
stops after the event is durable but before the view or an archive shard is
complete.

The JSON views remain the live read models. This is **not full event sourcing**,
and the current journal does **not** cover every mutation in facts, reflections,
persona, recent history, or time-indexed storage.

## Problem it solves

Atomic JSON replacement protects one file, but a memory transition may span a
view, an archive shard, and related lifecycle state. Without an independent
ordered record, a crash between those writes can leave no reliable way to tell
which mutation was intended.

For the covered paths, the journal provides three guarantees:

1. The intended mutation reaches disk before the live view changes.
2. Startup can replay an unapplied event through an idempotent handler.
3. Causal ordering is preserved: replay stops at the first event it cannot apply.

## On-disk layout

```text
memory/<character>/events.ndjson
memory/<character>/events_applied.json
```

Each non-empty journal line is a JSON object:

```json
{
  "event_id": "uuid4",
  "type": "reflection.evidence_updated",
  "ts": "2026-07-16T12:34:56.123456",
  "payload": {}
}
```

File position defines event order. `ts` is local naive ISO 8601 metadata and is
not used as the ordering key. Payloads are event-specific. Evidence updates are
mostly identifier and counter snapshots; archive recovery events can contain a
full derived entry snapshot, including text, so the character memory directory
must be treated as user-sensitive data.

## Current event coverage

`memory/event_log.py` declares 15 event names, but production startup currently
registers replay handlers for only these five:

| Event | Current use |
|---|---|
| `reflection.evidence_updated` | Evidence counters, decay bookkeeping, and promotion retry state |
| `persona.evidence_updated` | Persona evidence counters and archive countdown state |
| `persona.entry_updated` | Merge-on-promote text rewrite, evidence snapshot, and `merged_from_ids` |
| `reflection.state_changed` | Reflection lifecycle changes and reflection archive recovery |
| `persona.fact_added` | Persona archive recovery when the payload contains an archive snapshot |

The remaining declared names are reserved or legacy vocabulary:
`fact.added`, `fact.absorbed`, `fact.archived`, `reflection.synthesized`,
`reflection.surfaced`, `reflection.rebutted`, `persona.fact_mentioned`,
`persona.suppressed`, `correction.queued`, and `correction.resolved`.
They do not currently have production replay handlers. New code must not emit one
of these names merely because it appears in `ALL_EVENT_TYPES`; a recoverable
writer requires both a producer and a registered, idempotent handler.

## Write contract

Covered mutations use `EventLog.record_and_save()` or its async twin. One
per-character `threading.Lock` contains the complete synchronous critical section:

```text
load current view
  -> append event, flush, fsync
  -> mutate the loaded view
  -> atomically save the view
  -> atomically advance the sentinel
```

The async twin moves that entire block to one worker thread. Callers may use an
outer per-character `asyncio.Lock` for manager-level serialization, but they must
not hold the journal's threading lock across an `await`.

Appending first is deliberate. If append or `fsync` fails, the shared in-memory
view has not changed. If mutation or view save fails after append, the durable
event remains available for startup replay.

Unknown event names are rejected at append time. This prevents a new writer from
creating an event that the current binary cannot replay.

## Startup recovery

During memory-server startup, the runtime:

1. creates the managers, `EventLog`, and `Reconciler`;
2. registers the current evidence/lifecycle handlers;
3. reconciles every configured character, awaited to completion;
4. scans pending outbox work and spawns replay tasks;
5. runs evidence and archive migrations, then starts the staggered background
   loops.

Reconciliation before outbox replay is deliberate and load-bearing. Both write the
same view files, but their read points are asymmetric: a replay handler loads,
mutates, and saves inside the `EventLog` lock, while the reflection and persona
live writers load the whole snapshot *outside* that lock and only then hand it to
`record_and_save`. Any overlap therefore leaves a window where a live writer saves
a pre-replay snapshot back over a just-completed repair, with the sentinel already
past the event — silent, permanent loss. Resumed outbox operations are background
tasks, and reconciliation is fully awaited, so running reconciliation first removes
the overlap entirely.

The reverse dependency does not exist: a resumed operation emits its own events
through `record_and_save`, which appends, applies, and saves as one step, so
reconciliation never has to apply them afterwards. Code still must not depend on an
outbox side effect being visible to any later startup step: `_replay_pending_outbox()`
returns the spawned task list, and the startup caller does not await it, so those
operations may overlap migrations and early loop activity.

The reconciler reads events after `last_applied_event_id` and applies them in file
order. A handler must load, idempotently apply, and persist its view before it
returns. The sentinel advances only after successful return.

If a handler raises, or the journal contains a type without a registered handler,
replay stops for that character and leaves the sentinel at the last successful
event. It does not skip ahead, because later transitions may depend on the failed
one. Restarting retries the same tail after the underlying problem is fixed.

A missing, malformed, or no-longer-present sentinel falls back to replaying the
current journal body. Handlers therefore have to tolerate duplicate application.
That fallback is a last resort, **not** a safe operation: replaying old events
over newer views is only as safe as every handler's idempotence, and two are
not. `persona.entry_updated` raises when the view's text hash no longer matches
the event (so replay stops there, after the events before it have already been
re-applied), and the `reflection.state_changed` status branch ignores `from`
and re-applies an old `to`. Evidence snapshots from weeks ago are re-applied
too. `read_sentinel` currently folds a transient read error (`OSError`) into
"no sentinel", so a single failed read at startup can trigger it. Compaction
never removes the sentinel's line, so compaction itself never causes this
fallback; it only shortens the history that such a replay would push back over
the views.

## Cross-file archive recovery

Reflection and persona archival intentionally commit in this order:

```text
journal event + active-view removal + sentinel advance (one record_and_save)
  -> append full entry to the selected archive shard
```

The event carries the chosen shard basename and an entry snapshot. The replay
handler can recreate the missing shard entry, but only for an event that is
still **after** the sentinel. The sentinel advances inside `record_and_save`,
before the shard append, and callers only log a failed append; a crash or an
append failure at that point therefore leaves no replay to self-heal from, and
the event's `entry_snapshot` is the only remaining copy of that archival.

Compaction accounts for this: an archive event is dropped only when some shard
of its side (reflection or persona) holds a record with the same entry id **and**
the same `archived_at` (or, without `archived_at`, the same whole entry). Same id
alone is not enough, because an entry can be archived more than once — a subject
restore keeps the shard copy and brings the entry back, a later sweep archives it
again with a new `archived_at` — and only the newest copy may be missing. Every
other archive event that carries an entry snapshot is pinned in the journal
regardless of age or window. An unreadable shard pins every archive event of
that side. Compaction only reads
shards; it never repairs them (that would race the live archive path, which
appends after releasing the journal lock).

The handler's self-heal itself deduplicates by entry id only, so it cannot put a
second archival of the same id into a shard that already holds an older copy.
Moving the shard append in front of the sentinel advance, and repairing pinned
events, are separate changes.

## Compaction

`EventLog.compact_applied_history` prunes already-applied history from one
character's journal. The memory server runs it from a background loop
(`app/memory_server/evidence_loops.py`, `_periodic_event_log_compact_loop`): the
first round runs 300 s after startup, then every 6 h
(`EVENT_LOG_COMPACT_INTERVAL_SECONDS`), one character after another. Nothing on
the startup path waits for it.

**Trigger.** A journal is examined only when it is at least 512 KiB, or when its
first non-empty line is at least 30 days old (an unparseable first line counts as
old). Otherwise the call costs one `stat` and one line read. The age trigger is
what reaches small journals: size alone would never prune them, and archived
entry text would stay on disk indefinitely.

**What is kept.** The anchor is the first record whose `event_id` equals the
sentinel — the same match `read_since` makes. The anchor line and every line
after it, corrupt and blank lines included, are kept verbatim and in order (only
the line terminator is normalized, and a missing final newline is added). In
front of the anchor:

- lines replay would skip (blank, not JSON, not an object, no `event_id`) are
  dropped;
- `memory.long_term_replaced` events are dropped and never retained: reference
  events are no-ops once their inbox is consumed, and old head-style events
  carried a whole long-term snapshot;
- archive events that may be the only copy are pinned (see above);
- other events are kept while their `ts` is within the last 7 days, capped at
  256 KiB, evicting the oldest first; a missing or unparseable `ts` counts as
  old, and timezone-aware values are compared in local time;
- everything else is dropped.

The 7-day / 256 KiB window (`_RETAIN_APPLIED_DAYS`,
`_RETAIN_APPLIED_MAX_BYTES` in `memory/event_log.py`) is the only retention knob;
setting both to 0 keeps only the anchor and pinned lines. With the defaults a busy
journal settles at roughly a month of growth or 512 KiB, whichever comes first.

**Skipped, journal left byte-for-byte unchanged**, when: the journal is not due;
a reconciliation round for the character is in flight (`replay_in_progress`);
a round paused earlier in this process (`replay_paused`); the sentinel is
missing, `null`, unreadable or malformed (read strictly — unlike
`read_sentinel`, an unreadable file is not treated as "no sentinel"); the
sentinel's id is not in the journal (`sentinel_not_found`: the next start replays
the whole body anyway, and compaction must not change that input); nothing would
be dropped; the cloud-save write fence is up (`write_blocked`); the character
directory is gone; the file changed between the scan and the write
(`journal_changed`); or the in-memory self-check fails. On Windows, a journal
held open by another process makes the final replace fail after the usual short
busy retry (`busy`); the original stays and the temp file is removed.

**How it writes.** One `atomic_write_text` of `events.ndjson` (temp file, fsync,
replace). `events_applied.json` is never opened for writing and no event id is
created, so a crash at any point leaves either the old body or the new one, and
both give `read_since(sentinel)` the same answer. Before writing, the new body is
parsed in memory with the same line iteration replay uses, and must contain the
anchor followed by exactly the old tail's ids. Compaction never creates a
directory: paths are built without `ensure_character_dir`, and the final write
refuses to recreate a vanished parent. The pre-write checks and the replace run
inside `cloudsave_writable_transaction`, taken before the per-character lock,
which keeps a cloud import or restore from interleaving.

**Why not seeds.** An earlier helper replaced the body with "seed" events derived
from the views and reset the sentinel to `null`. That dropped any unapplied tail,
fell back to a full replay if the process stopped between its two writes, and the
seed replay pushed values back over later writes that never go through the
journal. It was never wired up and has been removed.

**Funnel endpoint.** `GET /api/memory/funnel/{lanlan_name}` counts events that are
still in the journal, so after a compaction it only sees the retained window
(7 days by default) plus pinned archive events and the unapplied tail.

**Forgetting.** Forgetting a subject does not rewrite the journal. Text of an
archived or forgotten entry stays in `events.ndjson` until compaction drops the
event (at most the trigger age plus the window), unless the event is pinned as
the only archival copy; the archive shards keep that text by design either way.

**Orphaned events.** An event left unapplied by a paused round and then orphaned
by a later live write (see the known limitation below) sits in front of the
anchor. Compaction leaves the character alone for the rest of the process in
which the pause happened; after a restart such an event is pruned like any other
applied history once it falls out of the window.

## Concurrency and consistency boundaries

- Locking is per character and in process. There is no cross-process distributed
  lock or multi-writer protocol.
- View files remain authoritative for normal reads and may still be repaired or
  migrated directly by dedicated code.
- The sentinel write is an unconditional overwrite. Event ids are UUIDs, so no
  writer can tell from two ids which is newer; the only order that exists is the
  position in the journal. `record_and_save` can never rewind it, since it writes
  the id it just appended. Replay cannot make that assumption, so it compares the
  on-disk sentinel against the value its round started from — before it runs the
  handler, not after — and neither applies the event nor writes the sentinel when
  they differ.
- A replay round that loses that comparison does not stop, and does not keep
  applying the list it was holding. Either would lose data. The list is now
  *behind* another writer's sentinel, so applying it as-is pushes older payloads
  over that writer's values, and the newer event sits ahead of the frozen sentinel
  where no later boot replays it. Stopping instead abandons repairs that the same
  sentinel write has already made unreplayable. So the round re-reads the journal
  from its own last-applied position through to the end of the file and replays
  that range in journal order: the queued repairs land, the other writer's events
  land after them, and the newest payload for any given entry wins. The sentinel
  stays frozen — rewinding it would return the other writer's events to the tail,
  and one without a registered handler wedges every later boot — and needs no
  final write, because that writer already parked it at the journal end. Replaying
  an already-applied event is harmless for the same reason a missing sentinel
  triggers a full replay: handlers carry full snapshots and are idempotent.
- That "end of the file" is a snapshot, and writers keep appending past it. An
  event landing after the snapshot is absent from the range being replayed, while
  the stale payloads queued ahead of it are not: replaying those pushes older
  values over the view that writer just saved, and the sentinel now parked on its
  id means no later boot replays it back. So the frozen pass re-probes from its
  own last-applied position each time it drains, and only returns once a probe
  comes back empty. The probe count is bounded (`_MAX_FROZEN_RESCANS`) so that
  unbroken write traffic ends the round instead of stalling startup; hitting the
  bound is logged, because whatever is left unreplayed sits behind the sentinel
  and no later boot picks it up.
- **Known limitation, not fixed here.** Advancing the sentinel to the journal end
  asserts that every earlier event was applied, and `record_and_save` never checks
  that. Whenever an unapplied tail is on disk — replay paused on an unregistered
  event type or a raising handler — the next live write silently orphans it. This
  is unrelated to reconciliation running concurrently, and startup ordering does
  not help. Closing it requires the sentinel to stop being "an event id": either a
  full journal scan on every write, or a position/applied-set with an on-disk
  format migration. Until then, do not read "the sentinel only moves forward" as
  "no event can be skipped".
- Compaction and replay. A replay round releases the per-character lock between
  events, and after a sentinel conflict it re-reads the journal from its *own*
  last-applied event, which is in front of the on-disk sentinel. Had compaction
  dropped that line in between, the re-read would fall back to a full replay of
  what is left and the events the round still owed would never be applied (this
  was reproduced). So every round runs inside `EventLog.replay_round`, a
  per-character in-flight counter, and compaction skips while it is non-zero. It
  covers startup reconciliation and the run-time `areconcile` call in long-term
  sync alike. A round that stops before draining its tail (unregistered type,
  raising handler, sentinel write failure, exhausted rescans, an exception or
  cancellation) is recorded on the `EventLog`, and compaction skips that
  character for the rest of the process (`replay_paused`): a later round that
  drains cannot tell whether a live write orphaned the paused tail in between.
  Lock order is "cloud-save write transaction → per-character lock → the
  `EventLog` bookkeeping lock"; nothing takes them in the other order.
- **Known limitations of compaction.** The per-character lock is keyed by the
  character name string and lives in one process. Compaction is a rewrite, which
  — unlike an append — cannot tolerate a concurrent writer that does not take the
  same lock:
  - another process using the same data root (for example a development checkout
    and the packaged app pointed at one documents folder): the file identity
    (inode, size, mtime) is compared between the scan and the write, which
    catches an append that lands before the final check, but an append between
    that check and the replace is lost, and the sentinel then names an event that
    is no longer in the body (next start: full replay);
  - alias keys: `characters.json` keys that normalize to a different name (a
    trailing space, for instance) map to the same directory under a different
    lock key. The loop skips any key whose `validate_lanlan_name` result differs
    from the raw key; names that differ only by case on a case-insensitive file
    system are not detected.
  - The character lifecycle lease the loop takes only excludes rename / delete
    callers that take a publication hold. Cloud downloads and workshop
    unsubscribe take none and do not drain. Unsubscribe can remove the directory
    at any moment; compaction therefore never creates a directory and returns
    `no_character_dir` instead of writing into a vanished one. Cloud import and
    restore run under the cloud-apply fence, which the compaction write
    transaction excludes.
- Outbox recovery and event replay solve different failure windows: the outbox
  retries background operations; the journal repairs covered mutations after the
  operation has chosen a concrete state transition.
- Events outside the five registered production handlers are not currently a
  general audit/rebuild mechanism.

## Current code entry points

- `memory/event_log.py` — journal format, write contract, sentinel, replay, the
  replay-round bookkeeping, and `compact_applied_history`
- `memory/evidence_handlers.py` — registered idempotent replay handlers, and
  `make_archive_copy_check` (which archive events compaction must keep)
- `app/memory_server/evidence_loops.py` — `_periodic_event_log_compact_loop`, the
  background compaction loop
- `memory/archive_shards.py` — `shard_entry_index_sync`, the read-only shard
  lookup behind the archive-copy check
- `app/memory_server/runtime.py` — startup ordering and per-character reconciliation
- `memory/reflection/evidence_flow.py` — reflection evidence and archive producers
- `memory/reflection/promotion_merge.py` — promotion state-change producers
- `memory/persona/facts.py` — persona evidence, merge, and archive producers
- `memory/archive_shards.py` — sharded archive append behavior
- `memory/outbox.py` — operation retry layer, separate from the event journal

## Non-goals

- Rebuilding every memory view solely from the journal
- Replacing the JSON/SQLite storage layout with a database-backed event store
- Cross-character ordering or cross-process transactions
- A public event API for plugins
- Automatic schema-version negotiation for arbitrary future event payloads
