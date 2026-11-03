# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Background warmup + batch-fill loop for vector embeddings.

Two responsibilities, one loop so we share the lifecycle and the
"vectors disabled → nothing to do, exit silently" logic:

1. **Warmup gating.** The ONNX model file is ~150 MB and the first
   decompress can hold the GIL for several seconds. We refuse to load
   it during cold boot (which would inflate first-paint of the
   frontend). Instead we wait for whichever comes first:

     * the warmup delay elapses (configurable, default 30 s)
     * the user hits ``/process`` for the first time (signal that the
       greeting / prominent drain has finished and there's a real
       conversation underway)

   Then we call ``EmbeddingService.request_load()`` once. After that
   the service is either READY (carry on) or DISABLED (exit loop —
   nothing else for this worker to do for the rest of the process).

2. **Batch backfill.** Once READY, scan persona / reflection / facts
   for entries whose ``embedding`` is None (or whose stored model_id
   doesn't match the current service id, indicating a config/dim flip
   since the last process). Embed them in batches of N, write back to
   disk through the existing save APIs. Throttled so we don't peg the
   CPU during normal use.

Failure semantics: any exception inside the loop is logged but doesn't
propagate — this is a best-effort backfill, not a critical path.
A retry happens on the next poll interval. Sticky service-level
disable propagates correctly because ``EmbeddingService`` itself
flips to DISABLED on the first inference failure, and our
``is_available()`` short-circuit catches it on the next iteration.
"""
from __future__ import annotations

import asyncio
import logging
import sys

try:
    from memory.embeddings import (
        clear_embedding_fields,
        get_embedding_service,
        is_cached_embedding_valid,
        stamp_embedding_fields,
    )
except ImportError:
    # ``memory/embeddings.py`` is missing (typically because an antivirus
    # quarantined it — see memory.embeddings for the false-positive history).
    # Fall back to disabled stubs so the warmup/backfill loop still
    # imports; the loop's own ``is_available()`` short-circuit then
    # turns the whole worker into a one-shot no-op.
    from memory.embeddings_fallback import (
        clear_embedding_fields,
        get_embedding_service,
        is_cached_embedding_valid,
        stamp_embedding_fields,
        _warn_once,
    )
    _warn_once(__name__)

# Type annotations on `__init__` use string forward refs — keeping the
# TYPE_CHECKING imports earned a lint warning for "unused" because the
# `from __future__ import annotations` above already makes the strings
# unresolved at runtime. Static analyzers that need the resolved types
# can import the modules themselves; we don't pay an extra import here.

# As in embeddings.py, use N.E.K.O.Memory.* so these messages reach the Memory log file; otherwise
# "[EmbeddingWorker] service disabled at construction (reason); worker exiting"
# these exit reasons go to root (which has no handler) and are invisible in production.
try:
    from utils.logger_config import get_module_logger
    logger = get_module_logger(__name__, "Memory")
except Exception:  # noqa: BLE001 — fall back to the bare logger to keep the worker importable.
    logger = logging.getLogger(__name__)


# Tuning knobs — kept as module-level so the loop is easy to monkeypatch
# in tests and easy to tweak per-deploy without a code change.
BATCH_SIZE = 16          # entries per ONNX inference call
POLL_INTERVAL_SECONDS = 20   # idle interval between full sweeps —
                             # full sweep walks in-memory lists, costs
                             # a few ms, so the interval is bounded by
                             # acceptable backfill latency, not CPU.
ACTIVE_INTERVAL_SECONDS = 5  # interval while there's still backlog
                             # but the per-tick budget wasn't hit
                             # (i.e. partial work — small breather
                             # before the next sweep)
MAX_ENTRIES_PER_TICK = 64    # bound per-tick work to keep memory_server
                             # responsive during a large backfill
_UNBOUNDED = sys.maxsize     # write-back rescans a whole store; the cap is the collected texts


class EmbeddingWarmupWorker:
    """Owns the warmup-then-backfill loop for one process.

    Wired in memory_server's startup hook alongside the other
    ``_periodic_*`` loops. Lifetime ends with the process; ``stop()``
    is called from the shutdown hook so we don't leak the task.
    """

    def __init__(
        self,
        *,
        get_persona_manager,         # callable returning current PersonaManager
        get_reflection_engine,       # callable returning current ReflectionEngine
        get_fact_store,              # callable returning current FactStore
        get_character_names,         # callable returning list[str]
        warmup_delay_seconds: float,
        get_dedup_resolver=None,     # callable returning current FactDedupResolver, or None to disable
        get_sweep_barrier=None,      # callable returning the reload/forget lock
        is_character_writable=None,  # callable(name) -> bool; False while a release/rename holds the name
    ) -> None:
        # All store/manager accesses go through getters so /reload (which
        # rebinds the memory_server module globals) is observed on the next
        # sweep. Capturing concrete instances here would let the worker
        # write through stale managers and clobber the new ones' updates.
        self._get_persona_manager = get_persona_manager
        self._get_reflection_engine = get_reflection_engine
        self._get_fact_store = get_fact_store
        self._get_character_names = get_character_names
        self._warmup_delay = warmup_delay_seconds
        # Same /reload-staleness shape as the managers above: reload
        # rebuilds fact_dedup_resolver against the new FactStore, so the
        # worker resolves the current instance per sweep instead of
        # capturing the startup one. Without this, post-reload enqueue
        # would write through the OLD resolver while the idle-maintenance
        # loop's resolve runs through the NEW one — two instances racing
        # on the same facts_pending_dedup.json without a shared lock.
        # Pass None to disable dedup entirely (e.g. tests, or
        # installations that prefer pure hash + FTS5 dedup until they
        # have evaluated LLM cost).
        self._get_dedup_resolver = get_dedup_resolver
        # A getter keeps this worker independent of memory_server while letting
        # production drain a sweep that captured pre-reload managers before a
        # reload + scoped forget can erase through their replacements.
        self._get_sweep_barrier = get_sweep_barrier
        # Inference no longer runs under the barrier, so a release + rename /
        # delete can complete between collect and write-back; a retired name
        # must not be loaded or saved (that would recreate its directory).
        self._is_character_writable = is_character_writable
        # Stores whose last save failed go to the back of the next collect,
        # so a persistently unwritable store cannot spend every tick's budget.
        self._failed_stores: set[tuple[str, str]] = set()

        self._service = get_embedding_service()
        self._first_process_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self) -> asyncio.Task:
        """Spawn the worker task. Idempotent — repeated calls return
        the existing task rather than starting a duplicate."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return self._task

    def notify_first_process(self) -> None:
        """Signal from /process / /renew handlers that user activity
        has begun. Wakes the warmup wait early — we don't want to make
        the user wait a full ``warmup_delay`` after their first
        interaction just to start backfilling."""
        if not self._first_process_event.is_set():
            self._first_process_event.set()

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    # ── main loop ────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Warmup → backfill → idle, repeating until ``stop_event``."""
        if self._service.is_disabled():
            logger.info(
                "[EmbeddingWorker] service disabled at construction (%s); "
                "worker exiting", self._service.disable_reason(),
            )
            return

        if not await self._wait_for_warmup_trigger():
            return  # stop requested during the wait

        ready = await self._service.request_load()
        if not ready:
            logger.info(
                "[EmbeddingWorker] model load did not produce a ready service "
                "(%s); worker exiting", self._service.disable_reason(),
            )
            return

        # Backfill loop. Three pacing tiers, all respecting stop_event:
        #   - budget saturated (processed == MAX_ENTRIES_PER_TICK):
        #     almost certainly more work, no sleep — ONNX inference
        #     inside embed_batch already yields to the event loop, so
        #     this doesn't peg CPU at the expense of other handlers.
        #   - partial work (0 < processed < budget): ACTIVE_INTERVAL
        #     small breather before next sweep.
        #   - idle (processed == 0): POLL_INTERVAL, the bound on how
        #     long a freshly written entry waits for its first vector.
        while not self._stop_event.is_set():
            try:
                processed = await self._sweep_once()
            except Exception as e:  # noqa: BLE001 — best effort, log + retry
                logger.warning(
                    "[EmbeddingWorker] sweep raised (%s: %s); retrying next tick",
                    type(e).__name__, e,
                )
                processed = 0

            if self._service.is_disabled():
                # Inference failed during the sweep and flipped sticky-off;
                # no more work is possible from this process.
                logger.info(
                    "[EmbeddingWorker] service flipped disabled mid-run (%s); "
                    "worker exiting", self._service.disable_reason(),
                )
                return

            if processed >= MAX_ENTRIES_PER_TICK:
                # Budget hit — drain backlog without sleeping. Still
                # checks stop_event at top of loop, so shutdown remains
                # prompt.
                continue
            interval = ACTIVE_INTERVAL_SECONDS if processed > 0 else POLL_INTERVAL_SECONDS
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                # Expected: interval elapsed without stop_event firing —
                # fall through to the next sweep iteration. Distinct
                # from a real cancellation, which propagates as
                # CancelledError and exits the loop.
                pass

    async def _wait_for_warmup_trigger(self) -> bool:
        """Wait for ``warmup_delay`` OR the first /process call. Returns
        False if ``stop_event`` fired first (shutdown during warmup —
        skip the load entirely).

        We race ``stop_event`` against (delay-or-first-process) so
        shutdown doesn't get blocked by the warmup wait. asyncio.wait
        with FIRST_COMPLETED returns the moment any of them resolve.
        """
        delay_task = asyncio.create_task(asyncio.sleep(self._warmup_delay))
        first_task = asyncio.create_task(self._first_process_event.wait())
        stop_task = asyncio.create_task(self._stop_event.wait())
        done, pending = await asyncio.wait(
            {delay_task, first_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        return not self._stop_event.is_set()

    # ── sweep implementation ─────────────────────────────────────────

    async def _sweep_once(self) -> int:
        """Embed up to ``MAX_ENTRIES_PER_TICK`` stale rows across all
        characters and stores. Returns count processed.

        Round-robin per-character, persona → reflection → facts. Stops
        once the per-tick budget is exhausted so a backlog doesn't
        starve the rest of memory_server. Remaining work picks up on
        the next sweep — eventual consistency is fine here.

        Three phases, so inference never runs under a lock (#3601):

          1. collect: under the sweep barrier and each store's lock,
             copy out the texts of stale entries;
          2. embed: no lock held — one tick of ONNX inference takes
             seconds to tens of seconds on a single intra-op thread,
             and /new_dialog waits on the persona / reflection locks
             inside the settle lock with a 5 s caller timeout;
          3. apply: under the barrier again, through the *current*
             managers, reload each store under its lock and stamp only
             entries that are still stale and still carry a text that
             was embedded. A vector is a function of (text, model_id),
             so an entry edited or removed meanwhile is simply not
             written; it is picked up (or gone) on the next sweep.
        """
        plan = await self._under_sweep_barrier(self._collect_sweep_plan)
        if not plan:
            return 0
        model_id = self._service.model_id()
        if model_id is None:
            return 0
        texts = list(dict.fromkeys(
            text for _name, _kind, wanted in plan for text in wanted
        ))
        vectors = await self._embed_texts(texts)
        if self._service.model_id() != model_id:
            # The service flipped (disabled / reloaded) while embedding;
            # these vectors belong to a model id that no longer applies.
            return 0
        return await self._under_sweep_barrier(
            lambda: self._apply_sweep_plan(plan, vectors, model_id),
        )

    async def _under_sweep_barrier(self, step):
        """Run ``step`` while the caller keeps component generation stable."""
        barrier = (
            self._get_sweep_barrier()
            if self._get_sweep_barrier is not None else None
        )
        if barrier is None:
            return await step()
        async with barrier:
            return await step()

    async def _collect_sweep_plan(self) -> list[tuple[str, str, list[str]]]:
        """Phase 1: ``[(name, store_kind, texts)]`` within the tick budget."""
        try:
            names = list(self._get_character_names())
        except Exception as e:  # noqa: BLE001
            logger.warning("[EmbeddingWorker] character lookup failed: %s", e)
            return []

        stores = [
            (name, kind)
            for name in names if self._writable(name)
            for kind in ("persona", "reflection", "fact")
        ]
        stores = (
            [s for s in stores if s not in self._failed_stores]
            + [s for s in stores if s in self._failed_stores]
        )
        budget = MAX_ENTRIES_PER_TICK
        plan: list[tuple[str, str, list[str]]] = []
        for name, kind in stores:
            if budget <= 0 or self._stop_event.is_set():
                break
            texts = await self._load_stale_texts(name, kind, budget)
            if texts:
                plan.append((name, kind, texts))
                budget -= len(texts)
        return plan

    def _writable(self, name: str) -> bool:
        if self._is_character_writable is None:
            return True
        try:
            return bool(self._is_character_writable(name))
        except Exception as e:  # noqa: BLE001
            logger.warning("[EmbeddingWorker] admission check failed for %s: %s", name, e)
            return False

    async def _load_stale_texts(self, name: str, kind: str, budget: int) -> list[str]:
        """Texts of one store's stale entries, read under that store's lock."""
        if kind == "fact":
            try:
                facts = await self._get_fact_store().aload_facts(name)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[EmbeddingWorker] fact load failed for %s: %s", name, e,
                )
                return []
            return self._texts(self._collect_stale_simple_entries(facts, budget))

        owner = (
            self._get_persona_manager() if kind == "persona"
            else self._get_reflection_engine()
        )
        async with owner._get_alock(name):
            try:
                if kind == "persona":
                    persona = await owner._aensure_persona_locked(name)
                    return self._texts(self._collect_stale_persona_entries(persona, budget))
                reflections = await owner._aload_reflections_full(name)
                return self._texts(self._collect_stale_simple_entries(reflections, budget))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[EmbeddingWorker] %s load failed for %s: %s", kind, name, e,
                )
                return []

    async def _apply_sweep_plan(self, plan, vectors: dict, model_id: str) -> int:
        """Phase 3: stamp the computed vectors into the current stores."""
        try:
            current_names = set(self._get_character_names())
        except Exception as e:  # noqa: BLE001
            logger.warning("[EmbeddingWorker] character lookup failed: %s", e)
            return 0
        total = 0
        for name, kind, texts in plan:
            if name not in current_names or not self._writable(name):
                # Renamed / deleted / released while embedding: skip without
                # loading, so the old identity's files are not recreated.
                continue
            if kind == "fact":
                total += await self._apply_facts(name, texts, vectors, model_id)
            else:
                total += await self._apply_locked_store(name, kind, texts, vectors, model_id)
        return total

    async def _apply_locked_store(self, name, kind, texts, vectors, model_id) -> int:
        """Persona / reflection write-back.

        Holds the per-character asyncio.Lock around the whole
        reload → mutate → save sequence so we don't race
        ``aadd_fact`` / ``arecord_mentions`` / ``resolve_corrections``
        / synthesis / auto-promote and clobber their writes (Codex
        PR-956 P1). The lock is already used by every other write path
        in PersonaManager / ReflectionEngine so the convention is safe
        here; only the inference moved out from under it.
        """
        owner = (
            self._get_persona_manager() if kind == "persona"
            else self._get_reflection_engine()
        )
        async with owner._get_alock(name):
            try:
                if kind == "persona":
                    container = await owner._aensure_persona_locked(name)
                    stale = self._collect_stale_persona_entries(container, _UNBOUNDED)
                else:
                    container = await owner._aload_reflections_full(name)
                    stale = self._collect_stale_simple_entries(container, _UNBOUNDED)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[EmbeddingWorker] %s load failed for %s: %s", kind, name, e,
                )
                return 0
            touched = self._stamp_matching(stale, texts, vectors, model_id)
            if not touched:
                return 0
            try:
                if kind == "persona":
                    await owner.asave_persona(name, container)
                else:
                    await owner.asave_reflections(name, container)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[EmbeddingWorker] %s save failed for %s: %s", kind, name, e,
                )
                # Returning len(touched) here would mark the batch as
                # progress; combined with the no-sleep saturated-budget
                # branch in _run, a persistent disk error (full / RO /
                # permission) would hot-loop on the same entries. 0
                # routes us through ACTIVE/POLL_INTERVAL backoff.
                self._failed_stores.add((name, kind))
                return 0
            self._failed_stores.discard((name, kind))
            return len(touched)

    async def _apply_facts(self, name, texts, vectors, model_id) -> int:
        """Stamp fact rows in place, then opportunistically scan the
        freshly-embedded rows for cosine collisions against the rest of
        the fact pool — collisions go into the dedup queue for the
        LLM-arbitrated resolve pass to handle later.

        FactStore uses a threading.Lock around `asave_facts` (not an
        asyncio.Lock like persona/reflection), and the embedding triple
        is the only field this worker touches, so a per-character
        asyncio lock isn't needed here — the field-level race is empty.
        """
        fact_store = self._get_fact_store()
        try:
            facts = await fact_store.aload_facts(name)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[EmbeddingWorker] fact load failed for %s: %s", name, e,
            )
            return 0
        targets = self._stamp_matching(
            self._collect_stale_simple_entries(facts, _UNBOUNDED), texts, vectors, model_id,
        )
        if not targets:
            return 0
        try:
            await fact_store.asave_facts(name)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[EmbeddingWorker] fact save failed for %s: %s", name, e,
            )
            # Save failed — skip dedup enqueue (the embeddings the
            # resolver would key on aren't durable yet) and return 0
            # so the outer loop backs off instead of hot-looping on a
            # persistent disk error. See _apply_locked_store for context.
            self._failed_stores.add((name, "fact"))
            return 0
        self._failed_stores.discard((name, "fact"))

        # Dedup pass: only the rows we just embedded are candidates;
        # we don't want to repeatedly scan the entire fact history on
        # every sweep. detect_candidates is entity-scoped + absorbed-
        # aware, so its output is already a clean set of (candidate,
        # existing) pairs ready for the LLM arbitration queue.
        dedup_resolver = (
            self._get_dedup_resolver()
            if self._get_dedup_resolver is not None else None
        )
        if dedup_resolver is not None:
            try:
                from memory.fact_dedup import FactDedupResolver  # local import keeps the worker importable when dedup module isn't loaded
                from memory.facts import _fact_scoped_identity
                only_for_ids = {
                    identity for e in targets
                    if (identity := _fact_scoped_identity(e)) is not None
                }
                pairs = FactDedupResolver.detect_candidates(
                    facts, only_for_ids=only_for_ids,
                )
                if pairs:
                    await dedup_resolver.aenqueue_candidates(name, pairs)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[EmbeddingWorker] dedup enqueue failed for %s: %s",
                    name, e,
                )
        return len(targets)

    # ── helpers ──────────────────────────────────────────────────────

    def _collect_stale_persona_entries(self, persona: dict, budget: int) -> list[dict]:
        """Return persona entry dicts (live references, not copies)
        that need a fresh embedding. Live refs let us mutate in place
        and have the change survive the subsequent ``asave_persona``."""
        stale: list[dict] = []
        for section in persona.values():
            if not isinstance(section, dict):
                continue
            for entry in section.get("facts", []) or []:
                if len(stale) >= budget:
                    return stale
                if self._needs_embedding(entry):
                    stale.append(entry)
        return stale

    def _collect_stale_simple_entries(self, items, budget: int) -> list[dict]:
        if not isinstance(items, list):
            return []
        stale: list[dict] = []
        for entry in items:
            if len(stale) >= budget:
                return stale
            if self._needs_embedding(entry):
                stale.append(entry)
        return stale

    def _needs_embedding(self, entry) -> bool:
        if not isinstance(entry, dict):
            return False
        text = entry.get("text") or ""
        if not text.strip():
            return False
        model_id = self._service.model_id()
        if model_id is None:
            return False
        return not is_cached_embedding_valid(entry, text, model_id)

    @staticmethod
    def _entry_text(entry: dict) -> str:
        return entry.get("text") or ""

    @classmethod
    def _texts(cls, entries: list[dict]) -> list[str]:
        return [cls._entry_text(e) for e in entries]

    def _stamp_matching(self, stale, texts, vectors, model_id) -> list[dict]:
        """Stamp stale entries whose current text was embedded, at most
        ``len(texts)`` of them (the collect phase's share of the budget).
        Returns the entries touched; the caller saves."""
        wanted = set(texts)
        touched: list[dict] = []
        for entry in stale:
            if len(touched) >= len(texts):
                break
            text = self._entry_text(entry)
            if text not in wanted or text not in vectors:
                continue
            vec = vectors[text]
            if vec is None:
                # One bad row in the batch shouldn't poison the rest;
                # leave the entry's embedding fields empty so the next
                # sweep retries.
                clear_embedding_fields(entry)
            else:
                stamp_embedding_fields(entry, vec, text, model_id)
            touched.append(entry)
        return touched

    async def _embed_texts(self, texts: list[str]) -> dict:
        """Batch-embed ``texts`` in chunks of ``BATCH_SIZE``; no lock may be held here."""
        vectors: dict = {}
        for i in range(0, len(texts), BATCH_SIZE):
            chunk = texts[i : i + BATCH_SIZE]
            vectors.update(zip(chunk, await self._service.embed_batch(chunk)))
        return vectors
