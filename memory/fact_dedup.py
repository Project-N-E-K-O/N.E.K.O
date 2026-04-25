# -*- coding: utf-8 -*-
"""
FactDedupResolver — vector-aware deduplication of newly-written facts.

The flow is intentionally LLM-arbitrated, NOT auto-merge on cosine
threshold:

  1. The embedding-worker sweep computes a vector for each fact and,
     while it has both old and new vectors in hand, scans for
     cosine > FACT_DEDUP_COSINE_THRESHOLD against existing facts of
     the same entity.  Hits go into ``facts_pending_dedup.json``.
  2. The idle-maintenance loop periodically calls ``aresolve(name)``,
     which batches the queue into one LLM call asking the model to
     classify each (candidate, existing) pair as ``merge`` / ``replace``
     / ``keep_both``.
  3. Decisions are applied to facts.json under the FactStore's
     existing per-character file lock, then processed queue items
     are removed.

Why an LLM is in the loop:

  * Cosine alone can't distinguish "主人喜欢猫" from "主人讨厌猫".
    Both surface forms vary by 1 token but ride opposite poles.
  * Hash-based dedup remains the first line of defence (catches exact
    repeats, no LLM cost) and the FTS5 lightweight near-dup check
    handles strong textual overlap.  This module addresses the
    *paraphrase* class — "对猫咪很感兴趣" / "最近养了只猫" — that
    legacy dedup misses entirely.

When the EmbeddingService is disabled, no candidates are ever
enqueued, so ``aresolve`` always sees an empty queue and the legacy
hash + FTS5 dedup path is the entire dedup pipeline — exactly the
behaviour pre-P2.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING

from memory.embeddings import cosine_similarity
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.file_utils import (
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)

if TYPE_CHECKING:
    from memory.facts import FactStore

logger = logging.getLogger(__name__)


# Cosine cutoff for "candidate is *probably* a paraphrase". 0.85 is
# the design number from the P2 plan — empirically what Jina-v5 nano
# emits for "主人喜欢猫" vs "对猫咪很感兴趣" (≈0.88) without false-
# positives between "主人喜欢猫" / "主人讨厌猫" (≈0.78). Tunable per
# deploy via the constant; lower values flood the LLM, higher misses
# real paraphrases.
FACT_DEDUP_COSINE_THRESHOLD = 0.85

# Cap how many candidate pairs go into a single LLM call. The prompt
# scales linearly with batch size, and the LLM's reliability degrades
# past ~20 simultaneous classifications. Excess items wait for the
# next aresolve tick.
FACT_DEDUP_BATCH_LIMIT = 20

# Cap how many pairs we enqueue from a single sweep. A pathological
# new fact that's near-duplicate of 50 existing rows would otherwise
# stuff the queue with N pairs, all about the same row. Bounded so
# the queue stays interpretable.
FACT_DEDUP_PAIRS_PER_NEW = 3


class FactDedupResolver:
    """Co-resident with FactStore. Owns the pending_dedup queue file
    and the LLM-arbitrated resolve path.

    Concurrency model: per-character asyncio.Lock guards the queue
    file (multiple writers — embedding-worker enqueue + resolve-loop
    consume).  FactStore's own threading.Lock guards facts.json, so
    apply_decision delegates to FactStore's save path rather than
    writing the file directly."""

    def __init__(self, fact_store: "FactStore") -> None:
        self._fact_store = fact_store
        self._config_manager = fact_store._config_manager
        self._alocks: dict[str, asyncio.Lock] = {}
        self._alocks_guard = threading.Lock()

    # ── lock helper ──────────────────────────────────────────────────

    def _get_alock(self, name: str) -> asyncio.Lock:
        """Per-character asyncio.Lock; lazy + DCL-guarded.

        Same shape as PersonaManager._get_alock. asyncio.Lock binds to
        the running loop on first acquire (CPython 3.10+), so the
        threading.Lock here only protects the dict-mutation race —
        not loop binding.
        """
        if name not in self._alocks:
            with self._alocks_guard:
                if name not in self._alocks:
                    self._alocks[name] = asyncio.Lock()
        return self._alocks[name]

    # ── file paths ───────────────────────────────────────────────────

    def _pending_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'facts_pending_dedup.json',
        )

    # ── queue I/O ────────────────────────────────────────────────────

    async def aload_pending(self, name: str) -> list[dict]:
        path = self._pending_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            # Corrupt queue file — treat as empty. The next enqueue
            # rebuilds it; we'd rather lose pending dedup work than
            # crash the resolver.
            pass
        return []

    async def _asave_pending(self, name: str, items: list[dict]) -> None:
        try:
            assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{name}/facts_pending_dedup.json",
            )
        except MaintenanceModeError as exc:
            logger.debug(
                "[FactDedup] %s: 维护态跳过 facts_pending_dedup.json 写入: %s",
                name, exc,
            )
            return
        await atomic_write_json_async(
            self._pending_path(name), items, indent=2, ensure_ascii=False,
        )

    async def aenqueue_candidates(
        self, name: str, pairs: list[dict],
    ) -> int:
        """Append candidate (candidate_id, existing_id, …) pairs to
        the queue. Returns count actually appended (de-duped against
        existing pending items by (candidate_id, existing_id) pair).

        Each pair dict must contain:
          * candidate_id / existing_id — stable fact ids
          * candidate_text / existing_text — for the LLM prompt
          * cosine — scoring transparency (debugging + threshold tuning)

        The id-pair dedup matters because an oscillating worker (e.g.
        re-embed under a new model_id) would otherwise re-enqueue the
        same pair on every sweep.
        """
        if not pairs:
            return 0
        async with self._get_alock(name):
            existing = await self.aload_pending(name)
            existing_keys = {
                (it.get('candidate_id'), it.get('existing_id'))
                for it in existing
            }
            now_iso = datetime.now().isoformat()
            appended = 0
            for p in pairs:
                key = (p.get('candidate_id'), p.get('existing_id'))
                if key in existing_keys or None in key:
                    continue
                existing.append({
                    'candidate_id': p.get('candidate_id'),
                    'existing_id': p.get('existing_id'),
                    'candidate_text': p.get('candidate_text', ''),
                    'existing_text': p.get('existing_text', ''),
                    'entity': p.get('entity'),
                    'cosine': float(p.get('cosine', 0.0)),
                    'queued_at': now_iso,
                })
                existing_keys.add(key)
                appended += 1
            if appended:
                await self._asave_pending(name, existing)
                logger.info(
                    "[FactDedup] %s: 入队 %d 对候选（队列总长 %d）",
                    name, appended, len(existing),
                )
        return appended

    # ── candidate detection ──────────────────────────────────────────

    @staticmethod
    def detect_candidates(
        facts: list[dict],
        *,
        threshold: float = FACT_DEDUP_COSINE_THRESHOLD,
        per_fact_limit: int = FACT_DEDUP_PAIRS_PER_NEW,
        only_for_ids: set[str] | None = None,
    ) -> list[dict]:
        """Pure function: scan facts for cosine > threshold pairs.

        ``only_for_ids`` constrains the *candidate* (newer) side so
        the worker can pass the ids it just embedded — we don't want
        to repeatedly scan the entire history on every sweep, only
        check the new arrivals against existing rows.

        Pairs are entity-scoped: ``主人喜欢猫`` (entity=master) should
        not collide with ``关系融洽`` (entity=relationship) even if
        the embeddings happen to be close. Cross-entity overlap is
        weird enough that we'd rather defer it to manual review.

        Pairs are absorbed-aware on the existing side: an existing
        fact already absorbed into a reflection is skipped. Re-merging
        a paraphrase into an absorbed fact would resurrect it from the
        archive path, which is worse than the duplicate.
        """
        results: list[dict] = []
        # Pre-bucket by entity so the inner loop only walks relevant rows.
        by_entity: dict[str, list[dict]] = {}
        for f in facts:
            if not isinstance(f, dict):
                continue
            entity = f.get('entity') or 'master'
            by_entity.setdefault(entity, []).append(f)

        for f in facts:
            if not isinstance(f, dict):
                continue
            cid = f.get('id')
            if not cid:
                continue
            if only_for_ids is not None and cid not in only_for_ids:
                continue
            if f.get('absorbed'):
                # Already folded into a reflection — merging or
                # replacing now would create an inconsistency between
                # the absorbed marker and the row's continued
                # existence in active facts.
                continue
            cvec = f.get('embedding')
            if not cvec:
                # Cannot dedup without an embedding — skip; the
                # worker will retry on its next sweep once the vector
                # is filled.
                continue
            entity = f.get('entity') or 'master'
            ctext = f.get('text', '')
            collected = 0
            # Sort siblings by cosine descending so we capture the
            # strongest pair first; the per_fact_limit cap then keeps
            # the queue interpretable when N rows are all near.
            scored: list[tuple[float, dict]] = []
            for sib in by_entity.get(entity, ()):
                sid = sib.get('id')
                if not sid or sid == cid:
                    continue
                if sib.get('absorbed'):
                    continue
                svec = sib.get('embedding')
                if not svec:
                    continue
                cos = cosine_similarity(cvec, svec)
                if cos < threshold:
                    continue
                scored.append((cos, sib))
            scored.sort(key=lambda x: x[0], reverse=True)
            for cos, sib in scored:
                if collected >= per_fact_limit:
                    break
                results.append({
                    'candidate_id': cid,
                    'existing_id': sib.get('id'),
                    'candidate_text': ctext,
                    'existing_text': sib.get('text', ''),
                    'entity': entity,
                    'cosine': cos,
                })
                collected += 1
        return results

    # ── resolve loop ─────────────────────────────────────────────────

    async def aresolve(self, name: str) -> int:
        """Process one batch of pending items via a single LLM call.

        Returns the number of items resolved (i.e. removed from the
        queue this round). On LLM failure, the queue is preserved
        intact so the next tick retries — failures here are transient
        by definition (otherwise the model would never resolve them).

        Concurrency: holds the per-character lock for the whole
        load → LLM → apply → save sequence. The LLM call is the long
        leg; concurrent enqueue calls block on the lock. That's
        intentional — the alternative (release lock during LLM call)
        introduces a TOCTOU between deciding which queue items we're
        about to remove and removing them, which would lose new pairs
        that landed mid-call.
        """
        async with self._get_alock(name):
            return await self._aresolve_locked(name)

    async def _aresolve_locked(self, name: str) -> int:
        from config import SETTING_PROPOSER_MODEL
        from config.prompts_memory import get_fact_dedup_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm
        from utils.token_tracker import set_call_type

        pending = await self.aload_pending(name)
        if not pending:
            return 0

        batch = pending[:FACT_DEDUP_BATCH_LIMIT]
        pairs_text = "\n".join(
            f"[{i}] candidate: {item.get('candidate_text', '')}"
            f" | existing: {item.get('existing_text', '')}"
            f" | cosine={item.get('cosine', 0.0):.3f}"
            for i, item in enumerate(batch)
        )
        prompt = (
            get_fact_dedup_prompt(get_global_language())
            .replace('{PAIRS}', pairs_text)
            .replace('{COUNT}', str(len(batch)))
        )

        try:
            set_call_type("memory_fact_dedup")
            api_config = self._config_manager.get_model_api_config('correction')
            llm = create_chat_llm(
                api_config.get('model', SETTING_PROPOSER_MODEL),
                api_config['base_url'], api_config['api_key'],
                temperature=0.2,
            )
            try:
                resp = await llm.ainvoke(prompt)
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            results = robust_json_loads(raw)
            if not isinstance(results, list):
                logger.warning(
                    "[FactDedup] %s: LLM 返回非数组 (%s)，跳过本轮",
                    name, type(results).__name__,
                )
                return 0
        except Exception as e:
            logger.warning("[FactDedup] %s: LLM 调用失败: %s", name, e)
            return 0

        applied, processed_keys = await self._aapply_decisions(
            name, batch, results,
        )

        # Read-modify-write the queue so concurrent enqueue calls
        # that landed during the LLM call survive — same shape as
        # PersonaManager._resolve_corrections_locked's processed-keys
        # filter at the end.  ``processed_keys`` comes from
        # _aapply_decisions and explicitly excludes pairs whose LLM
        # decision was malformed (unknown action) — those stay queued
        # for retry rather than being silently dropped (CodeRabbit
        # PR-957 Major).
        current = await self.aload_pending(name)
        remaining = [
            it for it in current
            if (it.get('candidate_id'), it.get('existing_id')) not in processed_keys
        ]
        await self._asave_pending(name, remaining)
        if applied:
            logger.info(
                "[FactDedup] %s: 处理 %d 对，剩余队列 %d 条",
                name, applied, len(remaining),
            )
        return applied

    # Whitelist of action vocabulary the LLM may return. Anything
    # outside this set (case mismatch, trailing whitespace, localised
    # synonym) is treated as malformed and the queue entry is
    # preserved for retry — the alternative is silently dropping a
    # paraphrase pair the next batch can no longer surface (CodeRabbit
    # PR-957 Major).
    _VALID_ACTIONS = frozenset({'merge', 'replace', 'keep_both'})

    async def _aapply_decisions(
        self, name: str, batch: list[dict], results: list[dict],
    ) -> tuple[int, set[tuple]]:
        """Translate LLM decisions into facts.json mutations.

        Decision vocabulary:
          * ``merge``    — drop the candidate, bump existing.importance
                           by +1 (capped at 10), append candidate_id
                           to existing.merged_from_ids
          * ``replace``  — drop the existing, keep the candidate
                           (paraphrase but the new wording is better)
          * ``keep_both``— no mutation, just clear from queue (LLM
                           judged they're not actually duplicates)

        Decisions referencing ids that no longer exist (e.g. a
        concurrent /process absorbed them) are silently skipped —
        the next sweep will re-enqueue if the situation recurs.

        Conflict avoidance (Codex PR-957 P1): if the LLM returns
        reciprocal decisions in the same batch — e.g. ``merge`` for
        (c1, e1) (drop c1) and ``replace`` for (e1, c1) (drop e1) —
        a naive "remove all ids in ids_to_remove at the end" would
        delete BOTH facts and leave the user with nothing.  The
        defensive guard is an in-loop check: if either side of the
        current pair is already scheduled for removal by a prior
        decision, skip this decision entirely.  The earlier decision
        wins (LLM ordering matters); the conflicting pair is still
        consumed (so the next round doesn't keep flagging it).

        Returns ``(applied_count, processed_pair_keys)``.  The set
        contains the (candidate_id, existing_id) keys for queue
        entries the caller should *remove* — exactly the entries we
        applied or consumed via the conflict guard, NOT the ones we
        skipped due to malformed LLM output (those stay queued for
        retry).
        """
        if not results:
            return 0, set()
        facts = await self._fact_store.aload_facts(name)
        by_id = {f.get('id'): f for f in facts if isinstance(f, dict) and f.get('id')}
        applied = 0
        ids_to_remove: set[str] = set()
        processed_pairs: set[tuple] = set()
        for r in results:
            if not isinstance(r, dict):
                continue
            try:
                idx = int(r.get('index', -1))
            except (TypeError, ValueError):
                continue
            if not (0 <= idx < len(batch)):
                continue
            item = batch[idx]
            action = r.get('action')
            # Strict whitelist (CodeRabbit PR-957 Major): unknown
            # action ⇒ leave the queue entry alone so the next round
            # gets a fresh chance.  Without this, "MERGE" / "merge "
            # / a localised synonym would silently drop into the
            # else-branch, then get cleared from the queue by the
            # caller's `processed_keys` filter — losing the
            # arbitration entirely.  Defensive normalisation
            # (lowercase + strip) gives the LLM a tiny grace margin
            # without opening the door to genuine garbage.
            if isinstance(action, str):
                action_norm = action.strip().lower()
            else:
                action_norm = None
            if action_norm not in self._VALID_ACTIONS:
                logger.warning(
                    "[FactDedup] %s: LLM 返回未知 action=%r，pair (%s,%s) 保留队列待下轮重试",
                    name, action, item.get('candidate_id'), item.get('existing_id'),
                )
                continue
            action = action_norm
            cand_id = item.get('candidate_id')
            exist_id = item.get('existing_id')
            cand = by_id.get(cand_id)
            existing = by_id.get(exist_id)
            if cand is None or existing is None:
                # One side disappeared between enqueue and resolve —
                # not an error, just stale; consume the queue entry
                # so it doesn't keep blocking subsequent batches.
                processed_pairs.add((cand_id, exist_id))
                continue
            # Reciprocal-pair guard: an earlier decision in this batch
            # already scheduled one side for removal. Honouring this
            # decision too would either delete both facts (merge after
            # replace) or mutate a row about to vanish.  Treat as
            # consumed so the queue entry clears, but skip the apply.
            if cand_id in ids_to_remove or exist_id in ids_to_remove:
                logger.info(
                    "[FactDedup] %s: 跳过冲突决策 cand=%s exist=%s (一方已被前一决策处理)",
                    name, cand_id, exist_id,
                )
                processed_pairs.add((cand_id, exist_id))
                applied += 1
                continue
            if action == 'merge':
                # Bump importance and record provenance on the existing
                # row, then schedule the candidate for removal. The
                # cap-at-10 mirrors _apersist_new_facts' clamp so a
                # parade of paraphrases can't grow importance unbounded.
                merged = list(existing.get('merged_from_ids') or [])
                if cand_id not in merged:
                    merged.append(cand_id)
                existing['merged_from_ids'] = merged
                cur_imp = int(existing.get('importance', 5) or 5)
                existing['importance'] = min(10, cur_imp + 1)
                ids_to_remove.add(cand_id)
                processed_pairs.add((cand_id, exist_id))
                applied += 1
            elif action == 'replace':
                # Mirror image: drop existing, keep candidate. Carry
                # the existing's merged_from chain forward so we don't
                # lose provenance back to its earlier paraphrases.
                merged = list(cand.get('merged_from_ids') or [])
                for mid in (existing.get('merged_from_ids') or []):
                    if mid not in merged:
                        merged.append(mid)
                if exist_id not in merged:
                    merged.append(exist_id)
                cand['merged_from_ids'] = merged
                # Importance: max of the two so a "replace" doesn't
                # silently demote a high-importance row.
                cur = int(cand.get('importance', 5) or 5)
                old = int(existing.get('importance', 5) or 5)
                cand['importance'] = max(cur, old)
                ids_to_remove.add(exist_id)
                processed_pairs.add((cand_id, exist_id))
                applied += 1
            else:  # keep_both
                # No mutation, just count it as resolved so the queue
                # entry is consumed.
                processed_pairs.add((cand_id, exist_id))
                applied += 1

        if ids_to_remove:
            # Use the in-memory list reference and rely on FactStore's
            # asave_facts to persist. Removing in place preserves the
            # FactStore's view-cache identity (same list object).
            facts[:] = [f for f in facts if f.get('id') not in ids_to_remove]
            await self._fact_store.asave_facts(name)
        elif applied:
            # Even pure keep_both rounds may have nudged nothing on
            # facts.json, but we still need a save if importance was
            # bumped on a merge above (handled by the ids_to_remove
            # branch). The else here is no-op for the no-mutation case.
            pass
        return applied, processed_pairs
