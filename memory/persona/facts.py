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
"""Facts methods for the memory manager."""

from __future__ import annotations


import hashlib


import os




from datetime import datetime




from memory.stop_names import (
    acollect_stop_names,
    collect_stop_names,
)






from ._shared import (
    logger,
    _extract_keywords,
)

class FactsMixin:
    @staticmethod
    def _normalize_entry(entry) -> dict:
        """Migrate plain-string entries into dict format.

        Each entry carries these provenance fields:
        - id: unique identifier. card_xxx / legacy_xxx / prom_xxx / manual_xxx
        - source: origin type. character_card / settings / reflection / manual
        - source_id: upstream ID (e.g. reflection_id), for tracing the provenance chain

        Evidence fields (RFC §3.2.3 user-driven evidence mechanism):
        - reinforcement / disputation: float accumulators, driven only by user signals
        - rein_last_signal_at / disp_last_signal_at: independent decay clocks
        - sub_zero_days + sub_zero_last_increment_date: archive countdown
        - merged_from_ids: reflection ids absorbed by LLM merge_into decisions

        Token-count cache fields (derived, cache-only — not event-sourced):
        - token_count: int | None — cached acount_tokens(text)
        - token_count_text_sha256: str | None — fingerprint of the text that
          was tokenized; a mismatch triggers recompute on the next render.
        - token_count_tokenizer: str | None — fingerprint of the counter
          used when `token_count` was written (e.g. `tiktoken:o200k_base`
          or `heuristic:v1`). A mismatch with the current tokenizer
          identity also triggers recompute, so a cache warmed under
          tiktoken doesn't get served to a heuristic-fallback render.

        Zero-migration schema addition: existing on-disk entries without
        these fields naturally read as None via `.get()`, which counts as a
        cache miss and triggers a clean recompute on first render. No
        explicit migration event is needed.
        """
        defaults = {
            'id': '',                   # 唯一标识
            'text': '',
            'source': 'unknown',        # character_card | settings | reflection | manual
            'source_id': None,          # 上游 ID（reflection_id 等）
            'recent_mentions': [],      # 窗口内提及时间戳列表
            'suppress': False,          # 是否被抑制
            'suppressed_at': None,      # suppress 开始时间
            'protected': False,         # character_card 来源条目，不可 suppress
            # Evidence counters (RFC §3.2.3)
            'reinforcement': 0.0,
            'disputation': 0.0,
            'rein_last_signal_at': None,
            'disp_last_signal_at': None,
            'sub_zero_days': 0,
            'sub_zero_last_increment_date': None,
            # user_fact reinforces combo 计数（RFC §3.1.8）。终生累计，
            # decay 只作用于 reinforcement 数值本身不影响这个计数器。
            'user_fact_reinforce_count': 0,
            # 溯源：merge_into 吸收的 reflection id 列表
            'merged_from_ids': [],
            # Derived token-count cache — populated by the render path
            # (`_get_cached_token_count` / `_aget_cached_token_count`)
            # on first render and ride-alongs with normal persona saves.
            # Both text-sha and tokenizer-identity must match for a hit,
            # so a cache warmed under tiktoken can't be served to a
            # heuristic-fallback render (e.g. packaging without encoding
            # data file).
            'token_count': None,
            'token_count_text_sha256': None,
            'token_count_tokenizer': None,
            # Fact version chain (RFC memory-enhancements §2). Populated in
            # resolve_corrections' replace branch so "主人以前住东京，后来搬到
            # 大阪" stays traceable. Each item: {text, replaced_at, reason,
            # source_fact_id}. None/empty list means no version history.
            'version_history': [],
            # Vector-embedding cache (memory-enhancements P2 — see
            # memory/embeddings.py). Populated by the background warmup
            # worker after the EmbeddingService becomes ready; consumed
            # by retrieval candidate generation. Same invalidation
            # contract as token_count: text-sha mismatch OR model_id
            # mismatch ⇒ re-embed on next worker pass. Legacy entries
            # naturally read None.
            'embedding': None,
            'embedding_text_sha256': None,
            'embedding_model_id': None,
            # MemoryRefineEngine cluster_hash skip 状态（Phase A-3）。
            # cluster_hash = sha1(sorted(member_ids))；refine 跑完后所有
            # 存活成员都 stamp 上当前 cluster 的 hash + timestamp。下次
            # 同 cluster 再形成时，全员 hash 命中 + 未超 REVISIT_AFTER_DAYS
            # → 直接 skip（不送 LLM）。任一成员被 merge/split/modify/discard
            # 后新条目无 stamp，cluster member set 变化 → hash 自然 invalidate。
            'last_refine_cluster_hash': None,
            'last_refine_at': None,
        }
        if isinstance(entry, str):
            d = dict(defaults)
            d['text'] = entry
            return d
        if isinstance(entry, dict):
            for k, v in defaults.items():
                entry.setdefault(k, v)
            # 兼容旧字段
            entry.pop('mention_count', None)
            entry.pop('consecutive_mentions', None)
            entry.pop('last_mentioned', None)
            return entry
        d = dict(defaults)
        d['text'] = str(entry)
        return d

    def _evaluate_fact_contradiction(
        self, name: str, text: str, section_facts: list, stop_names: list[str],
    ) -> tuple[str | None, str | None]:
        """Returns (rejection_code, conflicting_text) or (None, None) if OK."""
        for existing in section_facts:
            if isinstance(existing, dict):
                old_text = existing.get('text', '')
                is_card = existing.get('source') == 'character_card'
            else:
                old_text = str(existing)
                is_card = False
            if self._texts_may_contradict(old_text, text, stop_names=stop_names):
                if is_card:
                    logger.info(
                        f"[Persona] {name}: 新条目与角色卡矛盾，无条件拒绝: "
                        f"card=\"{old_text[:40]}\" vs new=\"{text[:40]}\""
                    )
                    return self.FACT_REJECTED_CARD, old_text
                return self.FACT_QUEUED_CORRECTION, old_text
        return None, None

    def _build_fact_entry(
        self, text: str, source: str, source_id: str | None, *, subject=None,
    ) -> dict:
        entry = self._normalize_entry(text)
        if source == 'reflection' and source_id:
            entry['id'] = f"prom_{source_id}"
        else:
            entry['id'] = f"manual_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hashlib.sha256(text.encode()).hexdigest()[:8]}"
        entry['source'] = source
        entry['source_id'] = source_id
        if subject is not None:
            entry.update(subject.as_entry_fields())
        return entry

    def add_fact(self, name: str, text: str, entity: str = 'master',
                 source: str = 'manual', source_id: str | None = None,
                 subject=None) -> str:
        """Add a confirmed fact to persona. Checks for contradictions first.

        Args:
            source: origin type (reflection / manual / ...)
            source_id: upstream ID, e.g. reflection_id (ref_xxx)

        Returns:
            FACT_ADDED            — successfully appended
            FACT_REJECTED_CARD    — contradicts character_card, permanently blocked
            FACT_QUEUED_CORRECTION — contradicts existing non-card fact, queued for LLM review
        """
        from memory.scopes import coerce_subject
        memory_subject = coerce_subject(subject)
        if memory_subject is not None:
            entity = memory_subject.kind
        persona = self.ensure_persona(name)
        section_facts = self._get_section_facts(
            persona, entity, subject=memory_subject,
        )
        stop_names = self._get_entity_stop_names(name)

        code, old_text = self._evaluate_fact_contradiction(name, text, section_facts, stop_names)
        if code == self.FACT_REJECTED_CARD:
            return self.FACT_REJECTED_CARD
        if code == self.FACT_QUEUED_CORRECTION:
            correction_entity = (
                memory_subject.persona_section_key
                if memory_subject is not None else entity
            )
            self._queue_correction(name, old_text, text, correction_entity)
            return self.FACT_QUEUED_CORRECTION

        section_facts.append(self._build_fact_entry(
            text, source, source_id, subject=memory_subject,
        ))
        self.save_persona(name, persona)
        return self.FACT_ADDED

    async def aadd_fact(self, name: str, text: str, entity: str = 'master',
                        source: str = 'manual', source_id: str | None = None,
                        subject=None) -> str:
        """P2.a.2: character-level asyncio.Lock serializes add_fact /
        resolve_corrections / record_mentions, preventing persona.json write races.

        Note: _aqueue_correction is invoked while already inside this lock, and
        its standalone lock is an asyncio.Lock (reentrant? no — asyncio.Lock is
        not reentrant) → so inside the lock we call the **unlocked** version of
        _aqueue_correction."""
        from memory.scopes import coerce_subject
        memory_subject = coerce_subject(subject)
        if memory_subject is not None:
            entity = memory_subject.kind
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            section_facts = self._get_section_facts(
                persona, entity, subject=memory_subject,
            )
            stop_names = await self._aget_entity_stop_names(name)

            code, old_text = self._evaluate_fact_contradiction(name, text, section_facts, stop_names)
            if code == self.FACT_REJECTED_CARD:
                return self.FACT_REJECTED_CARD
            if code == self.FACT_QUEUED_CORRECTION:
                correction_entity = (
                    memory_subject.persona_section_key
                    if memory_subject is not None else entity
                )
                await self._aqueue_correction_locked(
                    name, old_text, text, correction_entity,
                )
                return self.FACT_QUEUED_CORRECTION

            section_facts.append(self._build_fact_entry(
                text, source, source_id, subject=memory_subject,
            ))
            await self.asave_persona(name, persona)
            return self.FACT_ADDED

    @staticmethod
    def _find_entry_in_section(section_facts: list, entry_id: str) -> dict | None:
        for entry in section_facts:
            if isinstance(entry, dict) and entry.get('id') == entry_id:
                return entry
        return None

    @staticmethod
    def _compute_evidence_after_delta(
        entry: dict, delta: dict, now_iso: str, source: str = 'unknown',
    ) -> dict:
        from memory.evidence import compute_evidence_snapshot
        return compute_evidence_snapshot(entry, delta, now_iso, source)

    async def aapply_signal(
        self, name: str, entity_key: str, entry_id: str,
        delta: dict, source: str,
    ) -> bool:
        """Mutate an entry's evidence counters via EVT_PERSONA_EVIDENCE_UPDATED.

        Full-snapshot payload, record_and_save contract (RFC §3.3.3). Lock
        nesting: take the PersonaManager async lock first, then the event_log
        threading.Lock inside record_and_save — per the §3.3.3 "async outside,
        sync inside" rule.

        Returns True if the entry existed and was updated; False otherwise
        (unknown entry — migration marker case handled by caller).
        """
        from memory.event_log import EVT_PERSONA_EVIDENCE_UPDATED
        if self._event_log is None:
            raise RuntimeError(
                "[Persona.aapply_signal] event_log 未注入；PersonaManager() 构造时须传入 event_log"
            )

        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            section = persona.get(entity_key)
            if not isinstance(section, dict):
                logger.warning(
                    f"[Persona] {name}: aapply_signal 找不到 entity_key={entity_key}"
                )
                return False
            section_facts = section.get('facts', [])
            entry = self._find_entry_in_section(section_facts, entry_id)
            if entry is None:
                logger.warning(
                    f"[Persona] {name}: aapply_signal 找不到 entry_id={entry_id}"
                )
                return False

            now_iso = datetime.now().isoformat()
            snapshot = self._compute_evidence_after_delta(
                entry, delta, now_iso, source,
            )
            payload = {
                'entity_key': entity_key,
                'entry_id': entry_id,
                'reinforcement': snapshot['reinforcement'],
                'disputation': snapshot['disputation'],
                'rein_last_signal_at': snapshot['rein_last_signal_at'],
                'disp_last_signal_at': snapshot['disp_last_signal_at'],
                'sub_zero_days': snapshot['sub_zero_days'],
                'user_fact_reinforce_count': snapshot['user_fact_reinforce_count'],
                'source': source,
            }

            def _sync_load(_n: str):
                # 我们已持 async 锁 + 内存 cache 就是当前 view，直接复用。
                return persona

            def _sync_mutate(_view):
                entry['reinforcement'] = snapshot['reinforcement']
                entry['disputation'] = snapshot['disputation']
                entry['rein_last_signal_at'] = snapshot['rein_last_signal_at']
                entry['disp_last_signal_at'] = snapshot['disp_last_signal_at']
                entry['sub_zero_days'] = snapshot['sub_zero_days']
                entry['user_fact_reinforce_count'] = snapshot['user_fact_reinforce_count']

            # _sync_save: cloudsave gate + write + cache-evict-on-failure
            # (CodeRabbit PR #929 for the gate, PR #936 round-5 for the
            # evict). See `_sync_save_persona_view` docstring.
            _sync_save = self._sync_save_persona_view

            await self._event_log.arecord_and_save(
                name, EVT_PERSONA_EVIDENCE_UPDATED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            return True

    @staticmethod
    def _find_entry_with_section(
        persona: dict, entry_id: str,
    ) -> tuple[str | None, dict | None]:
        """Locate an entry by id across all entity sections.

        Returns `(entity_key, entry_dict)` or `(None, None)` if absent.
        Used by `amerge_into` where the caller (LLM) supplies a fully-qualified
        target_id but we still need to know which entity section to address
        the event payload against.

        Accepts both bare ids ("p_001") and the fully-qualified
        prompt form ("persona.<entity>.p_001"). The reflection promote
        path strips the prefix before calling, but we accept both forms
        defensively so any callsite (tests, future plugins, manual
        replay) works without re-implementing the parser.
        """
        # Defensive parse of the qualified form. Anything that doesn't
        # match `persona.<entity>.<id>` falls through to direct equality.
        qualified_entity: str | None = None
        bare_id = entry_id
        if isinstance(entry_id, str) and entry_id.startswith('persona.'):
            parts = entry_id.split('.', 2)
            if len(parts) == 3 and parts[2]:
                qualified_entity = parts[1]
                bare_id = parts[2]

        for ek, section in persona.items():
            if not isinstance(section, dict):
                continue
            if qualified_entity is not None and ek != qualified_entity:
                continue
            for entry in section.get('facts', []):
                if isinstance(entry, dict) and entry.get('id') == bare_id:
                    return ek, entry
        return None, None

    async def amerge_into(
        self, name: str, target_entry_id: str, merged_text: str,
        *,
        reflection_evidence: dict,
        source_reflection_id: str,
        merged_from_ids: list[str] | None = None,
    ) -> str:
        """Merge a reflection's content into an existing persona entry.

        Atomically rewrites the target entry's `text`, evidence values, and
        appends `source_reflection_id` to its `merged_from_ids` audit list.
        Emits two events (RFC §3.9.6), in this deliberate order:

          1. EVT_PERSONA_EVIDENCE_UPDATED — evidence-only snapshot so the
             funnel API (§3.10) can scan for evidence changes without
             joining the entry-update stream. Emitted FIRST so that a crash
             between the two writes does not permanently orphan this
             signal.
          2. EVT_PERSONA_ENTRY_UPDATED — text rewrite + evidence + audit;
             carries `rewrite_text_sha256` so the reconciler can detect view
             drift on replay. This is also the event that actually writes
             `merged_from_ids` (the idempotency sentinel) onto the view.

        Order rationale (CodeRabbit PR #936 round-4 Major): the old order
        (entry_updated first, evidence_updated second) created a crash
        window where the sentinel `merged_from_ids` landed on disk but the
        evidence_updated event never did. On retry the idempotency gate at
        line ~911 (`source_reflection_id in existing_merged_from`) returned
        'noop' and the evidence event was permanently lost — funnel
        observability silently missed that merge. By emitting
        evidence_updated FIRST (it has no idempotency side-state), a crash
        between the two writes leaves a retry in the "still not merged"
        state, so on retry BOTH events re-emit and entry_updated finalizes.
        The trade-off is that a crash-retry may append an extra
        evidence_updated to the log (new event_id); the funnel then
        slightly over-counts this merge (rare, human-facing metric) —
        strictly better than the alternative of permanently missing it.

        Idempotency (RFC §3.9.6 "crash halfway through"): if `source_reflection_id` is
        already in the target's `merged_from_ids`, both events are skipped
        and the call returns 'noop'. Replaying persisted events by
        event_id is idempotent on the reconciler side (sha256 matches →
        no-op).

        Evidence aggregation (CodeRabbit PR #936 round-6 Major #2):
        callers MUST pass `reflection_evidence={'reinforcement': ...,
        'disputation': ...}` carrying the source reflection's own
        evidence values; the conservative max-rule against the target's
        CURRENT evidence is computed HERE under the per-character lock.
        The previous signature took pre-computed `merged_reinforcement`
        / `merged_disputation` from the caller, which forced the caller
        to snapshot the target outside the lock. A concurrent
        `aapply_signal` (or another merge) on the same entry between
        the snapshot and `amerge_into` would produce stale "max"
        values, and writing them here effectively rolled the newer
        signal back. Computing under the lock guarantees the merge
        consumes the freshest target state.

        Returns: 'merged' on success, 'noop' if already merged, 'not_found'
        if `target_entry_id` is missing from the persona.
        """
        from memory.event_log import (
            EVT_PERSONA_ENTRY_UPDATED,
            EVT_PERSONA_EVIDENCE_UPDATED,
            EVIDENCE_SOURCE_PROMOTE_MERGE,
        )
        from memory.reflection import ReflectionEngine
        if self._event_log is None:
            raise RuntimeError(
                "[Persona.amerge_into] event_log 未注入；"
                "PersonaManager() 构造时须传入 event_log"
            )

        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            entity_key, target_entry = self._find_entry_with_section(
                persona, target_entry_id,
            )
            if target_entry is None or entity_key is None:
                logger.warning(
                    f"[Persona] {name}: amerge_into 找不到 target_entry_id="
                    f"{target_entry_id}"
                )
                return 'not_found'

            # Compute merged evidence UNDER THE LOCK against the
            # currently-locked target entry — see "Evidence aggregation"
            # block in docstring for the rollback hazard this prevents.
            merged_reinforcement, merged_disputation = (
                ReflectionEngine._compute_merged_evidence(
                    target_entry, reflection_evidence or {},
                )
            )

            # Normalize the id we put in event payloads + log lines to the
            # canonical bare form stored on disk. `_find_entry_with_section`
            # accepts both bare and fully-qualified (`persona.<entity>.<id>`)
            # forms; if a future caller passes the qualified form, the
            # downstream reconciler handlers (`make_persona_entry_handler`,
            # `make_persona_evidence_handler`) match strictly on the bare id
            # via `e.get('id') == entry_id`. Writing the qualified form into
            # the payload would make crash-replay miss the entry. RFC §3.9.6:
            # event payloads must reference the canonical on-disk id.
            canonical_entry_id = target_entry.get('id') or target_entry_id

            existing_merged_from = list(target_entry.get('merged_from_ids') or [])
            if source_reflection_id in existing_merged_from:
                logger.info(
                    f"[Persona] {name}: amerge_into idempotent skip "
                    f"target={canonical_entry_id} src={source_reflection_id}"
                )
                return 'noop'

            # Compute new audit list — dedup by id, preserve insertion order.
            # source_reflection_id MUST be in the final list because it is the
            # idempotency sentinel used at line ~911 (`if source_reflection_id
            # in existing_merged_from: return 'noop'`). If a caller passes a
            # non-empty `merged_from_ids` that omits `source_reflection_id`,
            # the previous fallback `(merged_from_ids or [source_reflection_id])`
            # would skip adding the sentinel and a retry of the same merge
            # would re-apply instead of no-op'ing — audit completeness /
            # idempotency bug (CodeRabbit PR #936 round-4 Minor).
            new_merged_from = list(existing_merged_from)
            for rid in list(merged_from_ids or []) + [source_reflection_id]:
                if rid not in new_merged_from:
                    new_merged_from.append(rid)

            now_iso = datetime.now().isoformat()
            new_text_sha = hashlib.sha256(
                (merged_text or '').encode('utf-8'),
            ).hexdigest()

            entry_payload = {
                'entity_key': entity_key,
                'entry_id': canonical_entry_id,
                'rewrite_text_sha256': new_text_sha,
                'reinforcement': float(merged_reinforcement),
                'disputation': float(merged_disputation),
                # Both clocks bumped — the merge IS a fresh signal on this
                # entry from both sides (rein from the absorbed reflection's
                # confirmations, disp likewise). RFC §3.1.1 says "只重置被
                # 触动的一侧" for normal aapply_signal, but merge is a
                # special case: target evidence values are RECOMPUTED from
                # both contributors via _compute_merged_evidence (max), so
                # both timestamps reflect the moment that recomputation
                # happened — semantic-clean, no half-stale clock.
                'rein_last_signal_at': now_iso,
                'disp_last_signal_at': now_iso,
                # sub_zero_days reset to 0 — the merge brought new positive
                # signal; archive countdown should restart.
                'sub_zero_days': 0,
                'merged_from_ids': new_merged_from,
                'source': EVIDENCE_SOURCE_PROMOTE_MERGE,
            }

            evidence_payload = {
                'entity_key': entity_key,
                'entry_id': canonical_entry_id,
                'reinforcement': float(merged_reinforcement),
                'disputation': float(merged_disputation),
                'rein_last_signal_at': now_iso,
                'disp_last_signal_at': now_iso,
                'sub_zero_days': 0,
                'user_fact_reinforce_count':
                    int(target_entry.get('user_fact_reinforce_count', 0) or 0),
                'source': EVIDENCE_SOURCE_PROMOTE_MERGE,
            }

            def _sync_load(_n: str):
                return persona

            def _sync_mutate_evidence(_view):
                # Evidence_updated emits FIRST and intentionally does NOT
                # write `merged_from_ids` — that sentinel is the idempotency
                # signal for the whole 2-event sequence (line ~911). If we
                # set it here, a crash between the two emits would make the
                # retry think the merge is already done and skip
                # entry_updated forever. Keeping this as a no-op means the
                # view on disk after event 1 still looks "un-merged" from
                # the idempotency gate's perspective, so retries fire both
                # events in order. The evidence_updated event payload
                # itself already carries the post-merge reinforcement /
                # disputation snapshot — replay handler will apply it.
                return None

            def _sync_mutate_entry(_view):
                # Entry_updated (event 2) writes the full final state,
                # including `merged_from_ids` (the idempotency sentinel).
                # By the time this runs, event 1 has already been recorded
                # to the log, so any crash from here onward is
                # replay-recoverable.
                target_entry['text'] = merged_text
                target_entry['reinforcement'] = float(merged_reinforcement)
                target_entry['disputation'] = float(merged_disputation)
                target_entry['rein_last_signal_at'] = now_iso
                target_entry['disp_last_signal_at'] = now_iso
                target_entry['sub_zero_days'] = 0
                target_entry['merged_from_ids'] = new_merged_from
                # Token-count cache is derived from `text`; rewriting text
                # must drop the cache so the next render recomputes. The
                # fingerprint check would catch the drift anyway, but
                # explicit invalidation avoids the tiny window where a
                # concurrent reader might see new text + stale count and
                # saves one sha256 compute on the next render.
                self._invalidate_token_count_cache(target_entry)
                # Same reason for the embedding cache — a stale vector
                # would silently match the old wording in cosine
                # candidate generation.
                self._invalidate_embedding_cache(target_entry)

            # _sync_save: cloudsave gate + write + cache-evict-on-failure
            # (CodeRabbit PR #936 round-5 Major #1). See
            # `_sync_save_persona_view` docstring.
            _sync_save = self._sync_save_persona_view

            # Event 1: evidence_updated — emitted FIRST so a crash between
            # the two writes does NOT permanently orphan this signal. The
            # mutate is a no-op (see _sync_mutate_evidence above); the view
            # on disk is unchanged after this call, which keeps the
            # idempotency gate "still not merged" so a retry re-emits
            # both events. Slight funnel over-count on retry is
            # acceptable vs. permanent signal loss (RFC §3.10 is a
            # human-facing metric).
            await self._event_log.arecord_and_save(
                name, EVT_PERSONA_EVIDENCE_UPDATED, evidence_payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate_evidence,
                sync_save_view=_sync_save,
            )
            # Event 2: entry_updated — canonical merge event. Writes the
            # text rewrite + evidence + audit list (`merged_from_ids`).
            # After this returns, persona.json is on disk with the full
            # merged state and the idempotency sentinel is in place.
            await self._event_log.arecord_and_save(
                name, EVT_PERSONA_ENTRY_UPDATED, entry_payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate_entry,
                sync_save_view=_sync_save,
            )
            logger.info(
                f"[Persona] {name}: amerge_into target={canonical_entry_id} "
                f"src={source_reflection_id} rein={merged_reinforcement} "
                f"disp={merged_disputation}"
            )
            return 'merged'

    async def aincrement_sub_zero(
        self, name: str, entity_key: str, entry_id: str, now: datetime,
    ) -> int | None:
        """Increment one persona entry's `sub_zero_days` via EVT_PERSONA_EVIDENCE_UPDATED.

        Symmetric to `ReflectionEngine.aincrement_sub_zero`. Called by
        the periodic archive sweep loop. Returns the new count or None
        if no increment happened.
        """
        from memory.event_log import EVT_PERSONA_EVIDENCE_UPDATED
        from memory.evidence import maybe_mark_sub_zero
        if self._event_log is None:
            raise RuntimeError(
                "[Persona.aincrement_sub_zero] event_log 未注入"
            )

        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            section = persona.get(entity_key)
            if not isinstance(section, dict):
                return None
            section_facts = section.get('facts', [])
            entry = self._find_entry_in_section(section_facts, entry_id)
            if entry is None:
                return None
            # Coderabbit PR #934 round-2 Major #2: probe on a staged copy
            # so the cached entry is NOT mutated until inside the locked
            # record_and_save critical section. If event append or save
            # raises, the cache stays clean (no orphan sub_zero_days
            # increment that never made it to the event log).
            staged_entry = dict(entry)
            if not maybe_mark_sub_zero(staged_entry, now):
                return None

            new_count = int(staged_entry.get('sub_zero_days', 0) or 0)
            new_date = staged_entry.get('sub_zero_last_increment_date')

            payload = {
                'entity_key': entity_key,
                'entry_id': entry_id,
                'reinforcement': float(entry.get('reinforcement', 0.0) or 0.0),
                'disputation': float(entry.get('disputation', 0.0) or 0.0),
                'rein_last_signal_at': entry.get('rein_last_signal_at'),
                'disp_last_signal_at': entry.get('disp_last_signal_at'),
                'sub_zero_days': new_count,
                'sub_zero_last_increment_date': new_date,
                'user_fact_reinforce_count': int(
                    entry.get('user_fact_reinforce_count', 0) or 0,
                ),
                'source': 'archive_sweep',
            }

            def _sync_load(_n: str):
                return persona

            def _sync_mutate(_view):
                # Apply the staged values to the cached entry only after
                # event append has already succeeded (record_and_save
                # guarantees this ordering).
                entry['sub_zero_days'] = new_count
                entry['sub_zero_last_increment_date'] = new_date

            # _sync_save: cloudsave gate + write + cache-evict-on-failure
            # (CodeRabbit PR #936 round-5 Major #1). See
            # `_sync_save_persona_view` docstring.
            _sync_save = self._sync_save_persona_view

            await self._event_log.arecord_and_save(
                name, EVT_PERSONA_EVIDENCE_UPDATED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            return new_count

    async def aarchive_persona_entry(
        self, name: str, entity_key: str, entry_id: str,
    ) -> bool:
        """Move one persona entry from main view to a sharded archive file.

        RFC §3.5.6: archiving reuses the ``EVT_PERSONA_FACT_ADDED`` event — the payload
        carries an `archive_shard_path` field so consumers can distinguish
        the archive flow from a regular fact_added (regular adds have no
        such field). Mirrors `ReflectionEngine.aarchive_reflection`.

        Returns True if archived; False if not found / protected.
        """
        from memory.archive_shards import aappend_to_shard, apick_today_shard_path
        from memory.event_log import EVT_PERSONA_FACT_ADDED
        if self._event_log is None:
            raise RuntimeError(
                "[Persona.aarchive_persona_entry] event_log 未注入；"
                "PersonaManager() 构造时须传入 event_log"
            )

        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            section = persona.get(entity_key)
            if not isinstance(section, dict):
                logger.warning(
                    f"[Persona] {name}: aarchive_persona_entry 找不到 "
                    f"entity_key={entity_key}"
                )
                return False
            section_facts = section.get('facts', [])
            entry = self._find_entry_in_section(section_facts, entry_id)
            if entry is None:
                logger.warning(
                    f"[Persona] {name}: aarchive_persona_entry 找不到 "
                    f"entry_id={entry_id}"
                )
                return False
            if entry.get('protected'):
                logger.debug(
                    f"[Persona] {name}: aarchive_persona_entry 跳过 protected "
                    f"entry_id={entry_id}"
                )
                return False

            now = datetime.now()
            now_iso = now.isoformat()
            archive_dir = self._persona_archive_dir(name)
            # Pre-pick the shard path BEFORE record_and_save so we can
            # stamp it into the event payload (and into the archive_entry
            # we'll write afterward). `apick_today_shard_path` materializes
            # the file on disk so the choice is stable across the
            # subsequent shard append.
            shard_path = await apick_today_shard_path(archive_dir, now=now)
            shard_basename = os.path.basename(shard_path)
            archive_entry = dict(entry)
            archive_entry['archived_at'] = now_iso
            archive_entry['archive_shard_path'] = shard_basename

            payload = {
                'entity_key': entity_key,
                'entry_id': entry_id,
                'archive_shard_path': shard_basename,
                'archived_at': now_iso,
                # Snapshot the text/source for replayability without
                # reading the shard back from disk.
                'text': entry.get('text', ''),
                'source': entry.get('source', 'unknown'),
                # Full entry snapshot — the persona archive handler in
                # evidence_handlers.py reads this on every replay and
                # idempotently recreates the shard if it's missing
                # (coderabbit PR #934 round-2 Major #3). Recoverable
                # crash window: any failure between record_and_save
                # and the shard append below is healed on the next
                # reconciler boot.
                'entry_snapshot': archive_entry,
            }

            def _sync_load(_n: str):
                return persona

            def _sync_mutate(_view):
                # Drop the archived entry from the entity section.
                section_facts[:] = [
                    e for e in section_facts
                    if not (isinstance(e, dict) and e.get('id') == entry_id)
                ]

            # _sync_save: cloudsave gate + write + cache-evict-on-failure
            # (CodeRabbit PR #936 round-5 Major #1). See
            # `_sync_save_persona_view` docstring.
            _sync_save = self._sync_save_persona_view

            # ORDER (coderabbit review #934 round-1 + round-2):
            # 1. record_and_save first — commits event + view mutation
            #    atomically. Avoids "duplicated shard entry + still
            #    active in view" (next sweep would re-archive into a
            #    second shard slot).
            # 2. aappend_to_shard second. If this raises, the active
            #    view has already lost the entry but the shard never
            #    got it. Self-heal: the persona archive handler in
            #    evidence_handlers.py reads `entry_snapshot` from the
            #    event payload and re-creates the shard on the next
            #    reconciler boot — event log is the source of truth
            #    (RFC §3.11), snapshot makes recovery automatic.
            await self._event_log.arecord_and_save(
                name, EVT_PERSONA_FACT_ADDED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            await aappend_to_shard(archive_dir, [archive_entry], now=now)
            logger.info(
                f"[Persona] {name}: 归档 entry {entity_key}/{entry_id} "
                f"→ {shard_basename}"
            )
            return True

    def _get_section_facts(self, persona: dict, entity: str, *, subject=None) -> list:
        if subject is not None:
            section = persona.setdefault(subject.persona_section_key, {})
            section.update(subject.as_entry_fields())
            section.setdefault('entity', subject.kind)
            return section.setdefault('facts', [])
        return persona.setdefault(entity, {}).setdefault('facts', [])

    def _normalize_entry_for_section(
        self, persona: dict, section_key: str, value,
    ) -> dict:
        """Normalize a persona fact and inherit its scoped section metadata."""
        entry = self._normalize_entry(value)
        from memory.scopes import persona_subject_from_section
        section = persona.get(section_key, {})
        subject = persona_subject_from_section(section_key, section)
        if subject is not None:
            entry.update(subject.as_entry_fields())
        return entry

    def _get_entity_stop_names(self, lanlan_name: str | None = None) -> list[str]:
        """Return master + lanlan names + their nicknames (``昵称``) — used to strip
        stop-names before any keyword/BM25/extraction step in the memory pipeline.

        ``lanlan_name`` defaults to the currently active catgirl. When given,
        use that character's own ``昵称`` — on this path ``aadd_fact`` etc.
        explicitly know the target character, avoiding misuse of the active
        character's nicknames in multi-character setups.
        """  # noqa: DOCSTRING_CJK
        return collect_stop_names(self._config_manager, lanlan_name)

    async def _aget_entity_stop_names(self, lanlan_name: str | None = None) -> list[str]:
        return await acollect_stop_names(self._config_manager, lanlan_name)

    @staticmethod
    def _texts_may_contradict(old_text: str, new_text: str,
                              stop_names: list[str] | None = None) -> bool:
        """Lightweight keyword-overlap heuristic for contradiction detection.

        Uses the same CJK-aware tokenization as ``_is_mentioned``.
        ``stop_names`` — master/lanlan + their nicknames — are substring-replaced
        out of the texts first, before cutting n-grams, so shared entity names
        can't single-handedly inflate the overlap ratio into false positives.
        """
        if not old_text or not new_text:
            return False
        old_kw = _extract_keywords(old_text, stop_names=stop_names)
        new_kw = _extract_keywords(new_text, stop_names=stop_names)
        if not old_kw or not new_kw:
            return False
        overlap = old_kw & new_kw
        ratio = len(overlap) / min(len(old_kw), len(new_kw))
        return ratio >= 0.4
