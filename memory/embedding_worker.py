# -*- coding: utf-8 -*-
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

from memory.embeddings import (
    clear_embedding_fields,
    get_embedding_service,
    is_cached_embedding_valid,
    stamp_embedding_fields,
)

# Type annotations on `__init__` use string forward refs — keeping the
# TYPE_CHECKING imports earned a lint warning for "unused" because the
# `from __future__ import annotations` above already makes the strings
# unresolved at runtime. Static analyzers that need the resolved types
# can import the modules themselves; we don't pay an extra import here.

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
        """
        try:
            names = list(self._get_character_names())
        except Exception as e:  # noqa: BLE001
            logger.warning("[EmbeddingWorker] character lookup failed: %s", e)
            return 0
        if not names:
            return 0

        budget = MAX_ENTRIES_PER_TICK
        total = 0
        for name in names:
            if budget <= 0 or self._stop_event.is_set():
                break
            spent = await self._sweep_persona(name, budget)
            budget -= spent
            total += spent
            if budget <= 0:
                break
            spent = await self._sweep_reflections(name, budget)
            budget -= spent
            total += spent
            if budget <= 0:
                break
            spent = await self._sweep_facts(name, budget)
            budget -= spent
            total += spent
        return total

    async def _sweep_persona(self, name: str, budget: int) -> int:
        """Persona facts live in nested ``{entity: {facts: [...]}}``;
        we walk every section and collect entries that need embedding.

        Holds the per-character asyncio.Lock around the whole
        load → mutate → save sequence so we don't race
        ``aadd_fact`` / ``arecord_mentions`` / ``resolve_corrections``
        and clobber their writes (Codex PR-956 P1). The lock is
        already used by every other write path in PersonaManager so
        the convention is safe here.
        """
        persona_manager = self._get_persona_manager()
        async with persona_manager._get_alock(name):
            try:
                persona = await persona_manager._aensure_persona_locked(name)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[EmbeddingWorker] persona load failed for %s: %s", name, e,
                )
                return 0
            targets = self._collect_stale_persona_entries(persona, budget)
            if not targets:
                return 0
            await self._fill_embeddings(targets)
            try:
                await persona_manager.asave_persona(name, persona)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[EmbeddingWorker] persona save failed for %s: %s", name, e,
                )
                # Returning len(targets) here would mark the batch as
                # progress; combined with the no-sleep saturated-budget
                # branch in _run, a persistent disk error (full / RO /
                # permission) would hot-loop on the same entries. 0
                # routes us through ACTIVE/POLL_INTERVAL backoff.
                return 0
            return len(targets)

    async def _sweep_reflections(self, name: str, budget: int) -> int:
        """Same lock contract as persona — synthesis / auto-promote /
        confirm-promotion all hold ``_get_alock(name)`` around their
        load+save, so the worker must too (Codex PR-956 P1)."""
        reflection_engine = self._get_reflection_engine()
        async with reflection_engine._get_alock(name):
            try:
                reflections = await reflection_engine._aload_reflections_full(name)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[EmbeddingWorker] reflection load failed for %s: %s", name, e,
                )
                return 0
            targets = self._collect_stale_simple_entries(reflections, budget)
            if not targets:
                return 0
            await self._fill_embeddings(targets)
            try:
                await reflection_engine.asave_reflections(name, reflections)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[EmbeddingWorker] reflection save failed for %s: %s", name, e,
                )
                return 0  # see _sweep_persona — same hot-loop guard
            return len(targets)

    async def _sweep_facts(self, name: str, budget: int) -> int:
        """Embed stale fact rows in place, then opportunistically scan
        the freshly-embedded rows for cosine collisions against the
        rest of the fact pool — collisions go into the dedup queue
        for the LLM-arbitrated resolve pass to handle later.

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
        targets = self._collect_stale_simple_entries(facts, budget)
        if not targets:
            return 0
        await self._fill_embeddings(targets)
        try:
            await fact_store.asave_facts(name)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[EmbeddingWorker] fact save failed for %s: %s", name, e,
            )
            # Save failed — skip dedup enqueue (the embeddings the
            # resolver would key on aren't durable yet) and return 0
            # so the outer loop backs off instead of hot-looping on a
            # persistent disk error. See _sweep_persona for context.
            return 0

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
                only_for_ids = {
                    e.get("id") for e in targets if e.get("id")
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

    async def _fill_embeddings(self, entries: list[dict]) -> None:
        """Batch-embed ``entries`` in chunks of ``BATCH_SIZE`` and stamp
        the cache fields in place. Caller is responsible for the save."""
        model_id = self._service.model_id()
        if model_id is None:
            return
        for i in range(0, len(entries), BATCH_SIZE):
            chunk = entries[i : i + BATCH_SIZE]
            texts = [(e.get("text") or "") for e in chunk]
            vectors = await self._service.embed_batch(texts)
            for entry, text, vec in zip(chunk, texts, vectors):
                if vec is None:
                    # One bad row in the batch shouldn't poison the rest;
                    # leave the entry's embedding fields alone (they
                    # stay None) so the next sweep retries.
                    clear_embedding_fields(entry)
                    continue
                stamp_embedding_fields(entry, vec, text, model_id)
