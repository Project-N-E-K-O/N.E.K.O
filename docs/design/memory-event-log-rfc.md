# Memory event journal: implemented design record

This page records the event-journal design that is implemented in the current
memory subsystem. It is intentionally an architectural record, not a line-by-line
construction plan. For the user-facing lifecycle, start with
[Memory System](/architecture/memory-system).

## Status and scope

Each character can have an append-only `events.ndjson` journal and an
`events_applied.json` replay sentinel beside its JSON views. The journal gives
selected evidence and lifecycle mutations an ordered audit trail and a recovery
path if the process stops after the event is durable but before the view or an
archive shard is complete.

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
3. scans pending outbox work and spawns replay tasks;
4. begins reconciliation for each configured character without awaiting those
   spawned tasks;
5. runs evidence and archive migrations, then starts the staggered background
   loops.

Steps 3 through 5 are not a strict completion order. Outbox handlers may overlap
reconciliation, migrations, and early loop activity, so code must not depend on
an outbox side effect being visible first. `_replay_pending_outbox()` returns the
spawned task list, but the current startup caller does not await it. If serialized
recovery becomes a requirement, startup must explicitly await that list before
reconciliation; the current implementation provides no such guarantee.

The reconciler reads events after `last_applied_event_id` and applies them in file
order. A handler must load, idempotently apply, and persist its view before it
returns. The sentinel advances only after successful return.

If a handler raises, or the journal contains a type without a registered handler,
replay stops for that character and leaves the sentinel at the last successful
event. It does not skip ahead, because later transitions may depend on the failed
one. Restarting retries the same tail after the underlying problem is fixed.

A missing, malformed, or no-longer-present sentinel falls back to replaying the
current journal body. Handlers therefore have to tolerate duplicate application.

## Cross-file archive recovery

Reflection and persona archival intentionally commit in this order:

```text
journal event + active-view removal
  -> append full entry to the selected archive shard
```

The event carries the chosen shard basename and an entry snapshot. If the process
stops between the two steps, the replay handler can recreate the missing shard
entry. Archive append and replay are idempotent, so retry does not intentionally
create duplicate logical entries.

## Compaction

`EventLog` contains a compaction helper that can replace a journal with a bounded
set of snapshot seed events after 10,000 lines or when the oldest readable event
is at least 90 days old. The replacement uses a temporary file plus
`os.replace`, then resets the sentinel so the seeds replay.

This helper is **not currently called by the memory-server startup or background
loops**. The thresholds describe available infrastructure, not an active retention
policy. Operators should not assume deployed journals are automatically compacted.

## Concurrency and consistency boundaries

- Locking is per character and in process. There is no cross-process distributed
  lock or multi-writer protocol.
- View files remain authoritative for normal reads and may still be repaired or
  migrated directly by dedicated code.
- Outbox recovery and event replay solve different failure windows: the outbox
  retries background operations; the journal repairs covered mutations after the
  operation has chosen a concrete state transition.
- Events outside the five registered production handlers are not currently a
  general audit/rebuild mechanism.

## Current code entry points

- `memory/event_log.py` — journal format, write contract, sentinel, replay, and
  compaction helper
- `memory/evidence_handlers.py` — registered idempotent replay handlers
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
