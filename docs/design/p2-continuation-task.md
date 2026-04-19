# P2.b+ Continuation Task — Spawn Prompt

> Paste the prompt block at the end of this file into a fresh Claude Code
> session on branch `claude/awesome-goldberg-omrDx` when you're ready to
> continue. The first section is background for you (the human reviewer);
> the second section is the agent-facing prompt.

---

## Background — where the branch stands

**Branch:** `claude/awesome-goldberg-omrDx` (14 commits ahead of main, 300
unit tests passing, ruff + `scripts/check_async_blocking.py` clean).

**Done:**

- **P0** — `CursorStore` persists rebuttal cursor; clock-rollback self-heal;
  cache-after-disk write order. (`f51e943`, `7b09b1e`)
- **P1** — `Outbox` append-only ndjson queue; startup replay with
  `Semaphore(4)`; `extract_facts` wired through outbox; deterministic
  reflection id via `sha256(sorted(source_fact_ids))` for idempotent retry.
  (`81dcd3a`, `cead490`, `4fd343f`, `dda5d9b`)
- **P2 RFC** — `docs/design/memory-event-log-rfc.md` v4 accepted. Read it
  before touching any wiring code. (`f1b5fab` → `feb0b58`)
- **P2.a.1** — `memory/event_log.py` with `EventLog` + `Reconciler`.
  `record_and_save` takes `(sync_load_view, sync_mutate_view, sync_save_view)`
  callbacks so load happens inside the per-character lock.
  (`c73ad64`, `5793b55`)
- **P2.a.2** — Per-character `asyncio.Lock` added to `ReflectionEngine`
  and `PersonaManager`; all mutating async paths are serialized.
  `_aqueue_correction` split into public wrapper + `_aqueue_correction_locked`
  because `asyncio.Lock` is not reentrant. (`ea81281`, `f9b1446`)

**Deferred (this task):** P2.b.1, P2.b.2, then P3, then P4.

---

## Key implementation constraints (carry these into the new session)

1. **No SQLite.** Atomic JSON via `atomic_write_json` / `atomic_write_text`
   (tempfile + `os.replace`) only. JSON view files keep their existing
   on-disk schemas — the event log sits alongside them, not in place of
   them.
2. **Sync/async duality.** Every new API ships with both a sync method
   and its `a*` twin (see `CursorStore`, `Outbox`, `EventLog`).
3. **Lock discipline** (RFC §3.4.1):
   - Sync critical section → `threading.Lock` (already held by
     `EventLog._get_lock`).
   - Async critical section spanning awaits →
     per-character `asyncio.Lock` (already on
     `ReflectionEngine._alocks` / `PersonaManager._alocks`).
   - `asyncio.Lock` is **not reentrant** — if a public async method is
     already locked, call the `_locked` helper, not the wrapper.
4. **Write order** (RFC §3.4): event append happens **inside** the
   per-character lock, **between** `mutate_view` and `save_view`. The
   sentinel (`events_applied.json`) is written **after** view save.
5. **Reconciler replay** is idempotent: handlers must tolerate replaying
   an event whose effect is already present in the view.
6. **Hash-only user content** (RFC §3.3.1, neko-guide.md rule 3): event
   payloads must never contain raw user text — only `text_sha256`.
7. **character_card veto is sacred** (from the original task brief): if
   a character card forbids a fact, the fact is dropped before it reaches
   the event log. Don't emit events for vetoed facts.
8. **Stop at each phase boundary** and spin up a review subagent before
   committing the next phase. Commit messages use the `feat(memory):`,
   `fix(memory):`, `docs(memory):` prefixes matching existing history.

---

## Phase breakdown for the new session

### P2.b.1 — Wire 10 non-compound event types at producers

Event types (from `memory/event_log.py` `ALL_EVENT_TYPES`):

| # | Event type | Producer | File | Notes |
|---|---|---|---|---|
| 1 | `fact.added` | `extract_facts` | `memory/facts.py` | One event per fact kept after character_card veto |
| 2 | `fact.absorbed` | `mark_absorbed` | `memory/facts.py` | Payload = list of absorbed fact ids |
| 3 | `fact.archived` | `_archive_absorbed` | `memory/facts.py` | Archive path in payload |
| 4 | `reflection.synthesized` | `_synthesize_reflections_locked` | `memory/reflection.py` | Payload includes `rid`, `source_fact_ids` (sorted) |
| 5 | `reflection.surfaced` | `arecord_surfaced` | `memory/reflection.py` | |
| 6 | `reflection.rebutted` | `areject_promotion` (rebuttal path) | `memory/reflection.py` | Distinguish rebuttal from explicit reject |
| 7 | `persona.fact_mentioned` | `_apply_record_mentions` | `memory/persona.py` | **Set-semantics payload** — include full `recent_mentions_snapshot` dict so replay is idempotent (RFC §3.3) |
| 8 | `persona.suppressed` | `aupdate_suppressions` | `memory/persona.py` | Separate event, not folded into `fact_mentioned` |
| 9 | `correction.queued` | `_aqueue_correction_locked` | `memory/persona.py` | |
| 10 | `correction.resolved` | `_resolve_corrections_locked` | `memory/persona.py` | |

**Pattern for each producer** (async example):

```python
async def aadd_fact(self, name, fact):
    async with self._get_alock(name):
        await asyncio.to_thread(
            event_log.record_and_save,
            name,
            FACT_ADDED,
            {'id': fact['id'], 'text_sha256': sha256(fact['text']), ...},
            sync_load_view=self._sync_load_facts,
            sync_mutate_view=lambda view: view.append(fact),
            sync_save_view=self._sync_save_facts,
        )
```

**Reconciler handlers** — register one `apply_<event_type>(view, event)`
per type on `Reconciler`. Each must be a pure function over the view +
event payload. Tests must show: apply twice → same view.

**Startup integration** in `memory_server.py`: for each character, in
order: `event_log.compact_if_needed` → `reconciler.areconcile` →
`_replay_pending_outbox` (already exists). Gather across characters with
`asyncio.gather`.

**Tests to add** (`tests/unit/`):

- `test_event_log_wiring_<producer>.py` — one per producer, asserting
  event is emitted with correct payload and view + sentinel advance
  atomically.
- `test_reconciler_handlers.py` — idempotency per handler (apply N times
  = apply once).
- `test_startup_reconcile_integration.py` — simulate crash-with-sentinel-
  behind-view, verify replay no-ops; crash-with-view-behind-event, verify
  replay catches up.

### P2.b.2 — Compound transactions + `FACT_ALREADY_PRESENT`

RFC §3.4.2 and §3.4.2.1 specify the exact shape. Summary:

1. Add constant `FACT_ALREADY_PRESENT = 'already_present'` on
   `PersonaManager` alongside the existing `FACT_ADDED` / `FACT_VETOED`
   return values.
2. In `add_fact` / `aadd_fact`, **before** appending, check:

   ```python
   if source == 'reflection' and source_id:
       if any(e.get('id') == f"prom_{source_id}" for e in section_facts):
           return FACT_ALREADY_PRESENT
   ```
3. Restructure `_aauto_promote_stale_locked` (RFC §3.4.2): for each
   reflection that needs promotion, execute **persona-first,
   reflection-second**:
   - Emit `persona.fact_added` (type 8 — reuse `FACT_ADDED`
     wiring from P2.b.1) or detect `FACT_ALREADY_PRESENT`.
   - Then emit `reflection.state_changed` transitioning the reflection
     to `promoted`.
   - If crash happens between step a and step b, retry converges:
     step a returns `FACT_ALREADY_PRESENT` (no duplicate), step b runs
     normally.
4. Treat `FACT_ALREADY_PRESENT` as equivalent to `FACT_ADDED` for the
   purposes of "should I emit the reflection state_changed event" —
   both mean persona is consistent, proceed.

**Integration test:**
`tests/integration/test_compound_transaction_convergence.py` — harness
that kills the process between persona save and reflection save (monkey-
patch `atomic_write_json` to raise once), restarts, and asserts final
state: persona has one copy of the fact, reflection is `promoted`.

### P3 — LifecycleDriver

Only start after P2.b has soaked for ~2 weeks in actual usage. Task
brief §3 has the spec: pure editorial layer over the event log, no
state of its own. Separate session, separate RFC round.

### P4 — Evidence counter

Optional. Needs real-usage data to decide whether the state machine
stays or gets replaced. Do not start without explicit user direction.

---

## Agent-facing prompt (copy everything below into the fresh session)

```
Branch: claude/awesome-goldberg-omrDx (do NOT rebase or merge main)

Read these in order before touching any code:

1. docs/design/memory-event-log-rfc.md (v4, whole document)
2. docs/design/p2-continuation-task.md (this file — the breakdown and constraints)
3. memory/event_log.py (especially record_and_save + Reconciler)
4. memory/reflection.py + memory/persona.py (the per-character asyncio.Lock scaffolding from P2.a.2)
5. tests/unit/test_manager_locks.py (torture test for lock discipline)

Scope for THIS session: P2.b.1 only. Do NOT start P2.b.2 or P3 in the same session.

Workflow:

1. Plan with TodoWrite — one item per event type in the P2.b.1 table + one for
   reconciler handler registration + one for startup integration + one per test
   file.
2. Implement producer-by-producer. After each producer, run
   `pytest tests/unit -x -q` and ensure no regression.
3. Every producer change must also register the matching reconciler handler and
   ship an idempotency unit test.
4. Do NOT touch LifecycleDriver, evidence counter, or P2.b.2 compound
   transactions. Those are separate sessions.
5. When all 10 event types wired + reconciler handlers registered + startup
   integration done + integration test green: spawn a review subagent with
   subagent_type=general-purpose and prompt "review the P2.b.1 implementation
   on the current branch against docs/design/memory-event-log-rfc.md v4 and
   docs/design/p2-continuation-task.md — flag any RMW gaps, idempotency holes,
   missed event payloads, or lock-discipline violations". Address all blockers
   before committing.
6. Commit per producer (10 commits) with messages like
   `feat(memory): P2.b.1 wire fact.added event at extract_facts`.
7. Do NOT create a pull request unless the user explicitly asks.

Invariants to preserve (audit after each commit):

- JSON view files keep their existing on-disk schemas.
- Every mutating async path holds the per-character asyncio.Lock across its
  load+mutate+save.
- Event payloads never contain raw user text — only text_sha256.
- character_card veto still runs BEFORE event emission.
- scripts/check_async_blocking.py passes.
- ruff clean.
- All existing 300 unit tests still pass.

When you finish P2.b.1 and the review subagent signs off, push with
`git push -u origin claude/awesome-goldberg-omrDx` and stop. The user will
review before starting P2.b.2.
```

---

*Generated 2026-04-19 as handoff for the deferred P2.b work.*
