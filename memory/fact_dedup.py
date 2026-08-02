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
FactDedupResolver — vector-aware deduplication of newly-written facts.

The flow is intentionally LLM-arbitrated, NOT auto-merge on cosine
threshold:

  1. The embedding-worker sweep computes a vector for each fact and,
     while it has both old and new vectors in hand, scans for
     cosine > FACT_DEDUP_COSINE_THRESHOLD against existing facts of
     the same entity.  Hits go into ``facts_pending_dedup.json``.
     The queue is **ids-only**: fact text never lands in the sidecar
     file. Scoped (group/member-derived) fact text must not exist in
     any store outside facts.json itself — the queue used to carry
     denormalized ``candidate_text`` / ``existing_text`` copies, which
     both leaked member content into a second plaintext file and went
     stale when the authoritative row was edited between enqueue and
     resolve.
  2. The idle-maintenance loop periodically calls ``aresolve(name)``,
     which re-reads the current text for each queued id pair from
     facts.json (a row that disappeared meanwhile is consumed via the
     stale-pair path) and batches the queue into one LLM call asking
     the model to classify each (candidate, existing) pair as
     ``merge`` / ``replace`` / ``keep_both``.
  3. Decisions are applied to facts.json under the FactStore's
     existing per-character file lock, then processed queue items
     are removed.

Why an LLM is in the loop:

  * Cosine alone can't distinguish "主人喜欢猫" (the user likes cats) from
    "主人讨厌猫" (the user hates cats).
    Both surface forms vary by 1 token but ride opposite poles.
  * Hash-based dedup remains the first line of defence (catches exact
    repeats, no LLM cost) and the FTS5 lightweight near-dup check
    handles strong textual overlap.  This module addresses the
    *paraphrase* class — "对猫咪很感兴趣" / "最近养了只猫" ("very
    interested in cats" / "recently got a cat") — that legacy dedup
    misses entirely.

When the EmbeddingService is disabled, no candidates are ever
enqueued, so ``aresolve`` always sees an empty queue and the legacy
hash + FTS5 dedup path is the entire dedup pipeline — exactly the
behaviour pre-P2.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from memory.facts import _fact_scoped_identity, safe_int_field
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.file_utils import (
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)

if TYPE_CHECKING:
    from memory.facts import FactStore


def _created_at_instant(value: object) -> datetime | None:
    """Parse an ISO timestamp as a comparable UTC instant."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError):
        return None


def _has_distinct_event_windows(first: dict, second: dict) -> bool:
    """Return True when facts describe different, at least partly explicit windows."""
    first_window = (
        _created_at_instant(first.get('event_start_at')),
        _created_at_instant(first.get('event_end_at')),
    )
    second_window = (
        _created_at_instant(second.get('event_start_at')),
        _created_at_instant(second.get('event_end_at')),
    )
    return (
        any(
            boundary is not None
            for boundary in (*first_window, *second_window)
        )
        and first_window != second_window
    )


def _queue_identity(item: dict) -> tuple:
    """Identify one queued pair inside its arbitration domain."""
    return (
        item.get('candidate_id'),
        item.get('existing_id'),
        item.get('subject_key'),
        item.get('scope'),
        item.get('candidate_subject_kind'),
        item.get('candidate_subject_id'),
        item.get('candidate_scope'),
        item.get('existing_subject_kind'),
        item.get('existing_subject_id'),
        item.get('existing_scope'),
    )


def _fact_dedup_domain(entry: dict) -> tuple | None:
    """Return the queue domain for a live fact row."""
    from memory.scopes import is_legacy_private_entry, subject_from_entry

    subject = subject_from_entry(entry)
    if subject is not None:
        if (
            subject.kind == 'group_participant'
            and subject.scope == f"{subject.kind}:{subject.subject_id}"
            and ':' in subject.subject_id
        ):
            group_prefix = subject.subject_id.rsplit(':', 1)[0]
            arbitration_key = f"@group_participant_arbitration:{group_prefix}"
            return arbitration_key, arbitration_key
        return subject.key, subject.scope
    if is_legacy_private_entry(entry):
        return None, None
    return None


def _pair_can_share_dedup(first: dict, second: dict) -> bool:
    """Allow cross-participant pairing only for deterministic corrections."""
    from memory.scopes import subject_from_entry

    first_subject = subject_from_entry(first)
    second_subject = subject_from_entry(second)
    if (
        first_subject is None
        or second_subject is None
        or first_subject.kind != 'group_participant'
        or second_subject.kind != 'group_participant'
    ):
        return True
    if (
        first_subject.kind,
        first_subject.subject_id,
        first_subject.scope,
    ) == (
        second_subject.kind,
        second_subject.subject_id,
        second_subject.scope,
    ):
        return True
    if _fact_dedup_domain(first) != _fact_dedup_domain(second):
        return False
    from memory.speaker_trust import deterministic_relation
    return deterministic_relation(
        str(first.get('text') or ''),
        str(second.get('text') or ''),
    ) == 'correction'


def _find_queued_fact(
    rows_by_id: dict[object, list[dict]], item: dict, side: str,
) -> dict | None:
    """Resolve a queued id to exactly one row in the queued scope."""
    rows = rows_by_id.get(item.get(f'{side}_id'), [])
    identity_fields = (
        item.get(f'{side}_subject_kind'),
        item.get(f'{side}_subject_id'),
        item.get(f'{side}_scope'),
    )
    if all(value is not None for value in identity_fields):
        expected = (str(item.get(f'{side}_id')), *identity_fields)
        rows = [row for row in rows if _fact_scoped_identity(row) == expected]
    elif 'subject_key' in item:
        domain = item.get('subject_key'), item.get('scope')
        rows = [row for row in rows if _fact_dedup_domain(row) == domain]
    return rows[0] if len(rows) == 1 else None

logger = logging.getLogger(__name__)


def cosine_similarity(left, right) -> float:
    """Load the optional vector implementation only when detection runs."""
    try:
        from memory.embeddings import cosine_similarity as implementation
    except ImportError:
        from memory.embeddings_fallback import (
            _warn_once,
            cosine_similarity as implementation,
        )
        _warn_once(__name__)
    return implementation(left, right)


# Cosine cutoff for "candidate is *probably* a paraphrase". 0.85 is
# the design number from the P2 plan — empirically what the default
# local profile emits for "主人喜欢猫" vs "对猫咪很感兴趣" (≈0.88)
# without false-positives between "主人喜欢猫" / "主人讨厌猫" (≈0.78). Tunable per
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

    def rebind_fact_store(self, fact_store: "FactStore") -> None:
        """Swap the FactStore reference *in place*, keeping ``_alocks``.

        /reload rebuilds FactStore for the new core_config but the
        pending_dedup queue is on disk per-character — both old and new
        FactStores resolve to the same file path through
        ``ensure_character_dir``. If reload also rebuilt the resolver,
        the old resolver's per-character locks would be orphaned and a
        mid-reload ``aresolve`` running under the old instance could
        race a fresh ``aenqueue_candidates`` on the new instance,
        corrupting the queue file. Rebinding instead preserves the
        single lock dict so the entire reload window remains
        serialised on the same asyncio.Locks (CodeRabbit PR-956 Major).
        """
        self._fact_store = fact_store
        self._config_manager = fact_store._config_manager

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

    async def _asave_pending(self, name: str, items: list[dict]) -> bool:
        """Persist the pending queue. Returns True on success, False if
        cloudsave is in maintenance mode (write skipped). Callers MUST
        propagate the False — reporting an enqueue/resolve as
        successful when the on-disk queue isn't actually updated would
        silently drop work across the maintenance window
        (CodeRabbit PR-956 Major)."""
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
            return False
        await atomic_write_json_async(
            self._pending_path(name), items, indent=2, ensure_ascii=False,
        )
        return True

    async def aenqueue_candidates(
        self, name: str, pairs: list[dict],
    ) -> int:
        """Append candidate (candidate_id, existing_id, …) pairs to
        the queue. Returns count actually appended (de-duped against
        existing pending items by id pair plus arbitration domain).

        Each pair dict must contain:
          * candidate_id / existing_id — stable fact ids
          * cosine — scoring transparency (debugging + threshold tuning)

        The queue is ids-only by design: fact TEXT is deliberately not
        persisted here. The authoritative copy lives in facts.json;
        ``_aresolve_locked`` re-reads it by id when assembling the LLM
        prompt. A text copy in this sidecar would put scoped (group /
        member-derived) content in a second plaintext file — the same
        content whose extraction / dead-letter logs only ever print
        domain markers and lengths.

        The id-pair dedup matters because an oscillating worker (e.g.
        re-embed under a new model_id) would otherwise re-enqueue the
        same pair on every sweep.
        """
        if not pairs:
            return 0
        from memory.scopes import subject_from_entry

        async with self._get_alock(name):
            # Candidate detection runs outside this lock. A scoped forget may
            # therefore delete its source facts after detection but before
            # enqueue. Revalidate both ids against the live store so a stale
            # worker cannot reintroduce forgotten text after the queue purge.
            has_scoped_pairs = any(
                p.get('subject_key') is not None or p.get('scope') is not None
                for p in pairs
            )
            live_facts_by_id: dict[object, list[dict]] = {}
            if has_scoped_pairs:
                live_facts = await self._fact_store.aload_facts(name)
                for row in live_facts:
                    if isinstance(row, dict) and row.get('id'):
                        live_facts_by_id.setdefault(row.get('id'), []).append(row)
            existing = await self.aload_pending(name)
            # scrub 老 schema 条目的明文残留。即使本次没有新 pair 可追加
            # （全部撞去重），只要发生了 scrub 就必须重写队列文件——否则
            # 磁盘上的明文要等下一次真正的写入才消失。
            scrubbed = False
            for it in existing:
                if 'candidate_text' in it or 'existing_text' in it:
                    it.pop('candidate_text', None)
                    it.pop('existing_text', None)
                    scrubbed = True
            existing_keys = {
                _queue_identity(it) for it in existing
            }
            now_iso = datetime.now().isoformat()
            appended = 0
            for p in pairs:
                key = _queue_identity(p)
                pair_rows = [
                    _find_queued_fact(live_facts_by_id, p, side)
                    for side in ('candidate', 'existing')
                ]
                real_subject_forget_active = any(
                    (
                        subject := subject_from_entry(row or {})
                    ) is not None
                    and self._fact_store._subject_forget_is_active(
                        name, subject,
                    )
                    for row in pair_rows
                )
                if (
                    key in existing_keys
                    or key[0] is None
                    or key[1] is None
                    or self._fact_store._subject_forget_fields_are_active(
                        name, p.get('subject_key'), p.get('scope'),
                    )
                    or real_subject_forget_active
                    or (
                        pair_rows[0] is not None
                        and pair_rows[1] is not None
                        and not _pair_can_share_dedup(
                            pair_rows[0], pair_rows[1],
                        )
                    )
                    or (
                        (
                            p.get('subject_key') is not None
                            or p.get('scope') is not None
                        )
                        and (
                            pair_rows[0] is None
                            or pair_rows[1] is None
                        )
                    )
                ):
                    continue
                queued = {
                    'candidate_id': p.get('candidate_id'),
                    'existing_id': p.get('existing_id'),
                    'entity': p.get('entity'),
                    'subject_key': p.get('subject_key'),
                    'scope': p.get('scope'),
                    'cosine': float(p.get('cosine', 0.0)),
                    'queued_at': now_iso,
                }
                for field in (
                    'candidate_subject_kind', 'candidate_subject_id',
                    'candidate_scope', 'existing_subject_kind',
                    'existing_subject_id', 'existing_scope',
                ):
                    if p.get(field) is not None:
                        queued[field] = p[field]
                existing.append(queued)
                existing_keys.add(key)
                appended += 1
            if scrubbed and not appended:
                await self._asave_pending(name, existing)
            if appended:
                if not await self._asave_pending(name, existing):
                    # Maintenance-mode skip: the queue file was NOT
                    # written, so we have to tell the caller the
                    # appended pairs aren't durable. The worker treats
                    # the return as "progress" — a stale True here
                    # would mark the next sweep as "queue advanced"
                    # and leave the candidates only in this process's
                    # memory until restart drops them.
                    return 0
                logger.info(
                    "[FactDedup] %s: 入队 %d 对候选（队列总长 %d）",
                    name, appended, len(existing),
                )
        return appended

    async def aforget_subject(self, name: str, subject) -> dict:
        """Purge queued text for one exact subject under the resolver lock.

        Holding the same lock as ``aresolve`` waits for any in-flight LLM
        decision before returning. Old queue rows without explicit subject
        fields are matched through their still-live fact ids, before the route
        deletes those facts.
        """
        from memory.scopes import coerce_subject, entry_matches_subject

        memory_subject = coerce_subject(subject)
        if memory_subject is None:
            raise ValueError("aforget_subject requires an explicit subject")
        async with self._get_alock(name):
            path = self._pending_path(name)
            pending: list = []
            if await asyncio.to_thread(os.path.exists, path):
                try:
                    data = await read_json_async(path)
                except (json.JSONDecodeError, OSError) as exc:
                    raise RuntimeError(
                        f"facts_pending_dedup unreadable during forget: {exc}"
                    ) from exc
                if not isinstance(data, list):
                    raise RuntimeError(
                        "facts_pending_dedup is not a list during forget"
                    )
                pending = data

            facts_path = self._fact_store._facts_path(name)
            facts: list = []
            if await asyncio.to_thread(os.path.exists, facts_path):
                try:
                    facts_data = await read_json_async(facts_path)
                except (json.JSONDecodeError, OSError) as exc:
                    raise RuntimeError(
                        f"facts state unreadable during dedup forget: {exc}"
                    ) from exc
                if not isinstance(facts_data, list):
                    raise RuntimeError(
                        "facts state is not a list during dedup forget"
                    )
                facts = facts_data
            subject_fact_ids = {
                row.get('id')
                for row in facts
                if (
                    isinstance(row, dict)
                    and row.get('id')
                    and entry_matches_subject(row, memory_subject)
                )
            }
            archive_path = self._fact_store._facts_archive_path(name)
            if await asyncio.to_thread(os.path.exists, archive_path):
                try:
                    archive_data = await read_json_async(archive_path)
                except (json.JSONDecodeError, OSError) as exc:
                    raise RuntimeError(
                        f"facts_archive unreadable during dedup forget: {exc}"
                    ) from exc
                if not isinstance(archive_data, list):
                    raise RuntimeError(
                        "facts_archive is not a list during dedup forget"
                    )
                subject_fact_ids.update(
                    row.get('id')
                    for row in archive_data
                    if (
                        isinstance(row, dict)
                        and row.get('id')
                        and entry_matches_subject(row, memory_subject)
                    )
                )

            def _matches(item: object) -> bool:
                if not isinstance(item, dict):
                    return False
                if (
                    item.get('subject_key') == memory_subject.key
                    and item.get('scope') == memory_subject.scope
                ):
                    return True
                return bool(subject_fact_ids.intersection({
                    item.get('candidate_id'), item.get('existing_id'),
                }))

            kept: list = []
            scrubbed = False
            for item in pending:
                if _matches(item):
                    continue
                if isinstance(item, dict):
                    item = dict(item)
                    if 'candidate_text' in item or 'existing_text' in item:
                        item.pop('candidate_text', None)
                        item.pop('existing_text', None)
                        scrubbed = True
                kept.append(item)
            removed = len(pending) - len(kept)
            if (
                (removed or scrubbed)
                and not await self._asave_pending(name, kept)
            ):
                raise RuntimeError(
                    "facts_pending_dedup not writable during forget"
                )
        return {'pending_dedup': removed}

    # ── candidate detection ──────────────────────────────────────────

    @staticmethod
    def detect_candidates(
        facts: list[dict],
        *,
        threshold: float = FACT_DEDUP_COSINE_THRESHOLD,
        per_fact_limit: int = FACT_DEDUP_PAIRS_PER_NEW,
        only_for_ids: set[str | tuple[str, str, str, str]] | None = None,
    ) -> list[dict]:
        """Pure function: scan facts for cosine > threshold pairs.

        ``only_for_ids`` constrains the *candidate* (newer) side so
        the worker can pass the scoped identities it just embedded — we don't want
        to repeatedly scan the entire history on every sweep, only
        check the new arrivals against existing rows.

        Pairs are entity-scoped: ``主人喜欢猫`` ("the user likes cats",
        entity=master) should not collide with ``关系融洽`` ("harmonious
        relationship", entity=relationship) even if the embeddings happen
        to be close. Cross-entity overlap is weird enough that we'd rather
        defer it to manual review.

        Pairs are absorbed-aware on the existing side: an existing
        fact already absorbed into a reflection is skipped. Re-merging
        a paraphrase into an absorbed fact would resurrect it from the
        archive path, which is worse than the duplicate.
        """  # noqa: DOCSTRING_CJK
        def _bucket_key(f: dict) -> tuple | None:
            """Entity + subject boundary; None → excluded from dedup.

            Scoped facts all share entity == subject.kind (e.g. every
            group's facts have entity='group_chat'), so entity alone
            would merge/replace rows ACROSS groups/members. The subject
            (key, scope) pair keeps dedup inside one boundary; legacy
            rows keep their pre-scope behaviour. Corrupt subject rows
            are excluded from every read path, so pairing against them
            would resurrect invisible data — skip them entirely.
            """
            entity = f.get('entity') or 'master'
            domain = _fact_dedup_domain(f)
            return (entity, *domain) if domain is not None else None

        results: list[dict] = []
        scoped_only_for_ids = {
            item for item in (only_for_ids or set())
            if isinstance(item, tuple) and len(item) == 4
        }
        bare_only_for_ids = {
            item for item in (only_for_ids or set()) if isinstance(item, str)
        }

        def _is_fresh(fact: dict) -> bool:
            identity = _fact_scoped_identity(fact)
            return (
                identity in scoped_only_for_ids
                or str(fact.get('id')) in bare_only_for_ids
            )

        # Pre-bucket by entity + subject so the inner loop only walks
        # rows inside the same dedup boundary.
        by_entity: dict[tuple, list[dict]] = {}
        fact_order: dict[tuple[str, str, str, str], int] = {}
        for index, f in enumerate(facts):
            if not isinstance(f, dict):
                continue
            identity = _fact_scoped_identity(f)
            if identity is not None:
                fact_order[identity] = index
            bucket = _bucket_key(f)
            if bucket is None:
                continue
            by_entity.setdefault(bucket, []).append(f)

        for f in facts:
            if not isinstance(f, dict):
                continue
            cid = f.get('id')
            if not cid:
                continue
            candidate_identity = _fact_scoped_identity(f)
            if only_for_ids is not None and not _is_fresh(f):
                continue
            if f.get('absorbed'):
                # Already folded into a reflection — merging or
                # replacing now would create an inconsistency between
                # the absorbed marker and the row's continued
                # existence in active facts.
                continue
            cvec = f.get('embedding')
            cmodel = f.get('embedding_model_id')
            if not cvec or not cmodel:
                # Cannot dedup without an embedding or its model_id —
                # skip; the worker will retry on its next sweep once
                # the vector triple is filled.
                continue
            bucket = _bucket_key(f)
            if bucket is None:
                continue
            entity = f.get('entity') or 'master'
            collected = 0
            # Sort siblings by cosine descending so we capture the
            # strongest pair first; the per_fact_limit cap then keeps
            # the queue interpretable when N rows are all near.
            scored: list[tuple[float, dict]] = []
            for sib in by_entity.get(bucket, ()):
                sid = sib.get('id')
                sibling_identity = _fact_scoped_identity(sib)
                if not sid or sibling_identity == candidate_identity:
                    continue
                # Same-batch deduplication (CodeRabbit PR-956 Major):
                # when both rows are in the fresh ``only_for_ids`` batch,
                # the outer loop visits this pair from BOTH sides
                # (cid=a/sid=b and cid=b/sid=a). Without a guard, the
                # queue gets (a,b) AND (b,a), wasting
                # FACT_DEDUP_PAIRS_PER_NEW / FACT_DEDUP_BATCH_LIMIT
                # budget and letting traversal order decide which row
                # plays "candidate" for the LLM's replace semantics.
                # Keep one direction, but preserve the candidate/newer
                # contract: created_at is authoritative and authored list
                # order breaks same-timestamp ties.  ID text is hash-random
                # within one timestamp and must not decide chronology.
                if (only_for_ids is not None
                        and _is_fresh(sib)
                        and _is_fresh(f)):
                    candidate_instant = _created_at_instant(f.get('created_at'))
                    sibling_instant = _created_at_instant(sib.get('created_at'))
                    if (
                        candidate_instant is not None
                        and sibling_instant is not None
                        and candidate_instant != sibling_instant
                    ):
                        candidate_is_newer = candidate_instant > sibling_instant
                    else:
                        candidate_is_newer = (
                            fact_order.get(candidate_identity, -1)
                            > fact_order.get(sibling_identity, -1)
                        )
                    if not candidate_is_newer:
                        continue
                if sib.get('absorbed'):
                    continue
                if not _pair_can_share_dedup(f, sib):
                    continue
                svec = sib.get('embedding')
                if not svec:
                    continue
                # Cross-model_id comparison is meaningless: a 64d INT8
                # vector and a 128d FP32 vector live in different
                # embedding spaces even when the dim happens to match
                # (different quantisation schemes ⇒ different scale +
                # axes). cosine_similarity already returns 0.0 on
                # length mismatch, but same-dim/different-quant pairs
                # would otherwise produce numerically valid cosines
                # against semantically incomparable vectors. Skip
                # until the next sweep so backfill catches up
                # (CodeRabbit PR-956 Major).
                if sib.get('embedding_model_id') != cmodel:
                    continue
                cos = cosine_similarity(cvec, svec)
                if cos < threshold:
                    continue
                scored.append((cos, sib))
            scored.sort(key=lambda x: x[0], reverse=True)
            for cos, sib in scored:
                if collected >= per_fact_limit:
                    break
                # ids-only（隐私收口）：pair 不携带 text，resolve 侧按 id
                # 从 facts.json 现取——队列文件因此不落任何成员衍生原文。
                results.append({
                    'candidate_id': cid,
                    'existing_id': sib.get('id'),
                    'candidate_subject_kind': f.get('subject_kind'),
                    'candidate_subject_id': f.get('subject_id'),
                    'candidate_scope': f.get('scope'),
                    'existing_subject_kind': sib.get('subject_kind'),
                    'existing_subject_id': sib.get('subject_id'),
                    'existing_scope': sib.get('scope'),
                    'entity': entity,
                    # 隔离域随 pair 入队（legacy 为 None/None）：resolve 侧
                    # 按域锁批，跨隔离域的 fact 文本不得共现在同一个 prompt。
                    'subject_key': bucket[1],
                    'scope': bucket[2],
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
        from config import MEMORY_LIVENESS_MAX_ATTEMPTS
        from config.prompts.prompts_memory import get_fact_dedup_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm_async
        from utils.token_tracker import set_call_type

        pending = await self.aload_pending(name)
        if not pending:
            return 0

        # 队列 ids-only 迁移 scrub：老 schema 条目落盘携带 candidate_text/
        # existing_text 明文副本（成员衍生内容），一经发现就地剥掉并立即
        # 重写队列文件——不能等到批次消费时才顺带清，否则轮不上的长尾条
        # 目会让明文在磁盘上一直躺到 dead-letter。维护态写失败无妨：内存
        # 里已 scrub，本轮 prompt 不受影响，下轮重试重写。
        scrubbed = False
        for it in pending:
            if 'candidate_text' in it or 'existing_text' in it:
                it.pop('candidate_text', None)
                it.pop('existing_text', None)
                scrubbed = True
        if scrubbed:
            await self._asave_pending(name, pending)
            logger.info(
                "[FactDedup] %s: 队列明文字段 scrub 完成（ids-only 迁移）", name,
            )

        # Liveness：过滤已达 MEMORY_LIVENESS_MAX_ATTEMPTS 的 dead-letter pair
        # （防御性——_abump_dedup_attempts_and_dead_letter_locked 命中阈值时直接
        # 从 queue 删除，正常路径不会让 attempts ≥ MAX 的 entry 还留着）。
        #
        # 单批锁定单一隔离域（对偶 corrections 的 batch_domain 锁）：legacy
        # 私聊为一域、每个 subject (key, scope) 各一域；跨域 pair 留队等
        # 下一轮 FIFO 轮到。新条目带 subject_key/scope 直接分类；升级前的
        # 老队列条目查活体 fact 行兜底分类。
        #
        # prompt 文本按 id 从 facts.json 现取（队列 ids-only）：任一侧行已
        # 消失（被 absorb 归档 / 上一轮 merge 掉 / subject 归档）的 pair 按
        # 既有 disappeared-row 语义直接出队，不进任何 prompt（fail-closed）。
        from memory.scopes import subject_from_entry

        rows = await self._fact_store.aload_facts(name)
        facts_by_id: dict[object, list[dict]] = {}
        for row in rows:
            if isinstance(row, dict) and row.get('id'):
                facts_by_id.setdefault(row.get('id'), []).append(row)

        def _classify_domain(it: dict) -> tuple | None:
            if 'subject_key' in it:
                return (it.get('subject_key'), it.get('scope'))
            for fid in (it.get('candidate_id'), it.get('existing_id')):
                matches = facts_by_id.get(fid, [])
                row = matches[0] if len(matches) == 1 else None
                if row is None:
                    continue
                domain = _fact_dedup_domain(row)
                if domain is not None:
                    return domain
            return None

        batch: list[dict] = []
        # 与 batch 平行的 (candidate_text, existing_text)。独立结构而不是
        # 临时挂在 item 上：batch 条目在失败路径会带着 resolve_attempts 原样
        # 写回队列文件，挂上去的文本会跟着落盘，把 ids-only 改回去。
        batch_texts: list[tuple[str, str]] = []
        stale_keys: set[tuple] = set()
        batch_domain: tuple | None = None
        for it in pending:
            if safe_int_field(it, 'resolve_attempts') >= MEMORY_LIVENESS_MAX_ATTEMPTS:
                continue
            cand_row = _find_queued_fact(facts_by_id, it, 'candidate')
            exist_row = _find_queued_fact(facts_by_id, it, 'existing')
            if cand_row is None or exist_row is None:
                stale_keys.add(_queue_identity(it))
                continue
            if any(
                (subject := subject_from_entry(row)) is not None
                and self._fact_store._subject_forget_is_active(name, subject)
                for row in (cand_row, exist_row)
            ):
                stale_keys.add(_queue_identity(it))
                continue
            if not _pair_can_share_dedup(cand_row, exist_row):
                stale_keys.add(_queue_identity(it))
                continue
            domain = _classify_domain(it)
            if domain is None:
                stale_keys.add(_queue_identity(it))
                continue
            if batch_domain is None:
                batch_domain = domain
            elif domain != batch_domain:
                continue
            batch.append(it)
            batch_texts.append(
                (cand_row.get('text', '') or '', exist_row.get('text', '') or '')
            )
            if len(batch) >= FACT_DEDUP_BATCH_LIMIT:
                break
        if stale_keys:
            kept = [
                it for it in pending
                if _queue_identity(it) not in stale_keys
            ]
            # 落盘失败（维护态）无妨：下一轮重新识别重新丢。
            await self._asave_pending(name, kept)
            logger.info(
                "[FactDedup] %s: 出队 %d 对行已消失/无法归域的陈旧候选",
                name, len(stale_keys),
            )
        if not batch:
            return 0
        pairs_text = "\n".join(
            f"[{i}] candidate: {cand_text}"
            f" | existing: {exist_text}"
            f" | cosine={item.get('cosine', 0.0):.3f}"
            for i, (item, (cand_text, exist_text)) in enumerate(
                zip(batch, batch_texts)
            )
        )
        prompt = (
            get_fact_dedup_prompt(get_global_language())
            .replace('{PAIRS}', pairs_text)
            .replace('{COUNT}', str(len(batch)))
        )

        try:
            set_call_type("memory_fact_dedup")
            api_config = await self._config_manager.aget_model_api_config('summary')
            # timeout=60: 持 FactDedup 锁但只阻 embedding worker enqueue
            # （background→background），用户路径无感。
            # max_retries=0: 禁 SDK 自动重试（这里没业务 retry，单次即终态）。
            from config import LLM_OUTPUT_GUARD_MAX_TOKENS
            llm = await create_chat_llm_async(
                api_config['model'],
                api_config['base_url'], api_config['api_key'],
                timeout=60, max_retries=0,
                max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so the dedup-decisions JSON isn't truncated
                provider_type=api_config.get('provider_type'),
            )
            try:
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # dedup prompt assembled from FACT_DEDUP_BATCH_LIMIT-capped fact pairs.
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
                # Parse 失败也算 attempt（same input → same parse failure）；
                # 跟 Exception 分支同治。
                await self._abump_dedup_attempts_and_dead_letter_locked(name, batch)
                return 0
        except Exception as e:
            logger.warning("[FactDedup] %s: LLM 调用失败: %s", name, e)
            # Liveness 兜底：给本批 pair bump resolve_attempts；达
            # MEMORY_LIVENESS_MAX_ATTEMPTS 的 entry 从 queue dead-letter
            # 丢弃。否则毒 pair（safety filter / prompt 过长 / 永远 parse
            # 不出来）一直占队头让 dedup 永久卡死。caller (aresolve) 已持
            # 着 _get_alock，这里走 _locked 变体不再重复获取。
            await self._abump_dedup_attempts_and_dead_letter_locked(name, batch)
            return 0

        applied, processed_keys = await self._aapply_decisions(
            name, batch, results,
        )

        # CodeRabbit: LLM 返了 list 但 ``_aapply_decisions`` 没消费任何 pair
        # （所有 action 都被 reject = unknown action / missing index / invalid
        # format 等），processed_keys 为空 → 下面的 ``remaining`` filter 不会
        # 删任何东西 → 队头同一批 pair 下次 tick 重新喂 LLM 同样输出垃圾 →
        # 永久卡死。算 attempts 一次（跟 LLM Exception / 非 list 同治）。
        if not processed_keys:
            logger.warning(
                "[FactDedup] %s: LLM 输出 %d 条 action 全部无效（unknown action / "
                "invalid index / conflict）, batch 无任何 pair 消费，按 attempt 失败计",
                name, len(results),
            )
            await self._abump_dedup_attempts_and_dead_letter_locked(name, batch)
            return 0

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
            if _queue_identity(it) not in processed_keys
        ]
        if not await self._asave_pending(name, remaining):
            # Maintenance-mode skip: queue cleanup didn't land on disk
            # so reporting `applied` as progress would mislead the
            # caller into thinking the queue shrunk. facts.json was
            # already saved by _aapply_decisions, so the next resolve
            # tick will see the (now-stale) queue entries hit the
            # disappeared-row branch and consume them harmlessly —
            # data loss is bounded to "queue file lags facts.json by
            # one tick". Returning 0 makes the worker back off to
            # POLL_INTERVAL_SECONDS rather than ACTIVE_INTERVAL,
            # which is the right cadence for a maintenance window
            # (CodeRabbit PR-956 Major).
            return 0
        if applied:
            logger.info(
                "[FactDedup] %s: 处理 %d 对，剩余队列 %d 条",
                name, applied, len(remaining),
            )
        return applied

    async def _abump_dedup_attempts_and_dead_letter_locked(
        self, name: str, batch_items: list[dict],
    ) -> None:
        """Liveness fallback when the aresolve LLM fails (caller MUST hold _get_alock).

        Bumps ``resolve_attempts`` for this batch's pending pairs; pairs whose
        total reaches ``MEMORY_LIVENESS_MAX_ATTEMPTS`` are removed from the queue
        with a WARN.

        Why: a poison pair (LLM can never parse it / safety filter / oversized
        prompt) sends the queue head into the same prompt with the same failure
        every tick → the whole dedup pipeline deadlocks for that character
        forever. The caller already holds _get_alock, so no `async with` here;
        this matches ``_aapply_decisions`` / ``aload_pending`` /
        ``_asave_pending`` all running inside the lock in ``_aresolve_locked``.
        """
        from config import MEMORY_LIVENESS_MAX_ATTEMPTS
        if not batch_items:
            return
        bumped_keys = {
            _queue_identity(it) for it in batch_items
        }
        bumped_keys = {
            key for key in bumped_keys if key[0] is not None and key[1] is not None
        }
        if not bumped_keys:
            return
        current = await self.aload_pending(name)
        kept: list[dict] = []
        dropped = 0
        for it in current:
            key = _queue_identity(it)
            if key in bumped_keys:
                new_attempts = safe_int_field(it, 'resolve_attempts') + 1
                if new_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
                    dropped += 1
                    logger.warning(
                        "[FactDedup] %s: dead-letter pair (%s, %s) resolve %d 次失败 ≥ %d，丢弃",
                        name, key[0], key[1], new_attempts, MEMORY_LIVENESS_MAX_ATTEMPTS,
                    )
                    continue
                it['resolve_attempts'] = new_attempts
            kept.append(it)
        if not await self._asave_pending(name, kept):
            logger.debug(
                "[FactDedup] %s: 维护态跳过 dedup attempts 写盘", name,
            )
        elif dropped:
            logger.info(
                "[FactDedup] %s: dead-letter 丢弃 %d 对 dedup pair，剩余队列 %d 条",
                name, dropped, len(kept),
            )

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
        contains the full scoped pair keys for queue entries the caller should
        *remove* — exactly the entries we
        applied or consumed via the conflict guard, NOT the ones we
        skipped due to malformed LLM output (those stay queued for
        retry).
        """
        if not results:
            return 0, set()
        live_facts = await self._fact_store.aload_facts(name)
        # Decisions are staged away from FactStore's shared cache.  The
        # archive transaction validates the original survivor snapshots and
        # publishes these copies only after it owns the persistence lock.
        facts = [dict(f) if isinstance(f, dict) else f for f in live_facts]
        rows_by_id: dict[object, list[dict]] = {}
        for fact in facts:
            if isinstance(fact, dict) and fact.get('id'):
                rows_by_id.setdefault(fact.get('id'), []).append(fact)
        originals_by_identity = {
            identity: dict(fact)
            for fact in live_facts
            if (identity := _fact_scoped_identity(fact)) is not None
        }
        applied = 0
        identities_to_remove: set[tuple[str, str, str, str]] = set()
        archive_specs: dict[tuple[str, str, str, str], dict] = {}
        mutated_survivor_identities: set[tuple[str, str, str, str]] = set()
        processed_pairs: set[tuple] = set()
        seen_pairs: set[tuple] = set()
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
            # Defend against the LLM returning the same pair twice with
            # different actions (small-model output instability).
            # Without this guard, a `keep_both` first then `merge`
            # second would still apply the merge — the merge branch's
            # `cand_id in ids_to_remove` check only catches conflicts
            # *between different pairs*, not against an earlier
            # decision on the SAME pair (CodeRabbit PR-956 Major).
            cand_id_dedup = item.get('candidate_id')
            exist_id_dedup = item.get('existing_id')
            pair_key = _queue_identity(item)
            if pair_key in seen_pairs:
                logger.info(
                    "[FactDedup] %s: 跳过重复决策 cand=%s exist=%s (LLM 在同一批次返回多次)",
                    name, cand_id_dedup, exist_id_dedup,
                )
                continue
            seen_pairs.add(pair_key)
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
            cand = _find_queued_fact(rows_by_id, item, 'candidate')
            existing = _find_queued_fact(rows_by_id, item, 'existing')
            if cand is None or existing is None:
                # One side disappeared between enqueue and resolve —
                # not an error, just stale; consume the queue entry
                # so it doesn't keep blocking subsequent batches.
                processed_pairs.add(pair_key)
                continue
            cand_identity = _fact_scoped_identity(cand)
            exist_identity = _fact_scoped_identity(existing)
            if cand_identity is None or exist_identity is None:
                processed_pairs.add(pair_key)
                continue
            from memory.speaker_trust import (
                deterministic_relation,
                preferred_by_trust,
                provenance_of_entries,
                stable_speaker_id,
            )
            cand_speaker = cand.get('speaker_id')
            exist_speaker = existing.get('speaker_id')
            cand_speaker_id = stable_speaker_id(cand_speaker)
            exist_speaker_id = stable_speaker_id(exist_speaker)
            cand_trust = cand.get('speaker_trust')
            exist_trust = existing.get('speaker_trust')
            preference = None
            if (
                cand.get('speaker_provenance_mixed') is not True
                and existing.get('speaker_provenance_mixed') is not True
                and cand_speaker_id is not None
                and exist_speaker_id is not None
                and cand_speaker_id != exist_speaker_id
                and isinstance(cand_trust, (int, float))
                and not isinstance(cand_trust, bool)
                and isinstance(exist_trust, (int, float))
                and not isinstance(exist_trust, bool)
                and deterministic_relation(
                    str(existing.get('text') or ''),
                    str(cand.get('text') or ''),
                ) == 'correction'
                and not _has_distinct_event_windows(existing, cand)
            ):
                preference = preferred_by_trust(
                    exist_trust, cand_trust,
                )
            if preference == 'old' and action == 'replace':
                logger.info(
                    "[FactDedup] %s: trust 仲裁保留 existing=%s(%s)，覆盖模型 replace",
                    name, exist_id, exist_speaker,
                )
                action = 'merge'
            elif preference == 'new' and action == 'merge':
                logger.info(
                    "[FactDedup] %s: trust 仲裁保留 candidate=%s(%s)，覆盖模型 merge",
                    name, cand_id, cand_speaker,
                )
                action = 'replace'

            def _fold_survivor_provenance(
                survivor: dict, absorbed: dict,
            ) -> None:
                provenance_keys = (
                    'speaker_id', 'speaker_label', 'speaker_trust',
                    'speaker_provenance_mixed',
                )
                folded = provenance_of_entries((survivor, absorbed))
                known_ids = [
                    stable_speaker_id(row.get('speaker_id'))
                    for row in (survivor, absorbed)
                ]
                attributed_ids = {value for value in known_ids if value}
                mixed = (
                    survivor.get('speaker_provenance_mixed') is True
                    or absorbed.get('speaker_provenance_mixed') is True
                    or (bool(attributed_ids) and None in known_ids)
                    or len(attributed_ids) > 1
                )
                for key in provenance_keys:
                    survivor.pop(key, None)
                if mixed:
                    survivor['speaker_provenance_mixed'] = True
                else:
                    survivor.update(folded)
            # Reciprocal-pair guard: an earlier decision in this batch
            # already scheduled one side for removal. Honouring this
            # decision too would either delete both facts (merge after
            # replace) or mutate a row about to vanish.  Treat as
            # consumed so the queue entry clears, but skip the apply.
            if (
                cand_identity in identities_to_remove
                or exist_identity in identities_to_remove
            ):
                logger.info(
                    "[FactDedup] %s: 跳过冲突决策 cand=%s exist=%s (一方已被前一决策处理)",
                    name, cand_id, exist_id,
                )
                processed_pairs.add(pair_key)
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
                if preference != 'old':
                    cur_imp = int(existing.get('importance', 5) or 5)
                    existing['importance'] = min(10, cur_imp + 1)
                # A trust-arbitrated correction is replacement semantics even
                # when the surviving side is represented by ``merge``.  The
                # rejected contradiction is not corroborating provenance;
                # keep the selected winner attributable for later disputes.
                if preference != 'old':
                    _fold_survivor_provenance(existing, cand)
                mutated_survivor_identities.add(exist_identity)
                identities_to_remove.add(cand_identity)
                archive_specs[cand_identity] = {
                    'reason': 'fact_dedup_merge',
                    'superseded_by': exist_id,
                }
                processed_pairs.add(pair_key)
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
                if preference != 'new':
                    cur = int(cand.get('importance', 5) or 5)
                    old = int(existing.get('importance', 5) or 5)
                    cand['importance'] = max(cur, old)
                # ``replace`` selects the candidate assertion rather than
                # corroborating it with the rejected row. Keep the selected
                # author's provenance; the loser remains traceable through
                # merged_from_ids and the archive record below.
                mutated_survivor_identities.add(cand_identity)
                identities_to_remove.add(exist_identity)
                archive_specs[exist_identity] = {
                    'reason': 'fact_dedup_replace',
                    'superseded_by': cand_id,
                }
                processed_pairs.add(pair_key)
                applied += 1
            else:  # keep_both
                # No mutation, just count it as resolved so the queue
                # entry is consumed.
                processed_pairs.add(pair_key)
                applied += 1

        if identities_to_remove:
            survivor_identities = (
                mutated_survivor_identities - identities_to_remove
            )
            facts_by_identity = {
                identity: fact
                for fact in facts
                if (identity := _fact_scoped_identity(fact)) is not None
            }
            await self._fact_store.aarchive_arbitrated_facts(
                name,
                archive_specs,
                survivor_updates={
                    identity: facts_by_identity[identity]
                    for identity in survivor_identities
                },
                expected_survivors={
                    identity: originals_by_identity[identity]
                    for identity in survivor_identities
                },
                expected_losers={
                    identity: originals_by_identity[identity]
                    for identity in identities_to_remove
                },
            )
        elif applied:
            # Even pure keep_both rounds may have nudged nothing on
            # facts.json, but we still need a save if importance was
            # bumped on a merge above (handled by the ids_to_remove
            # branch). The else here is no-op for the no-mutation case.
            pass
        return applied, processed_pairs
