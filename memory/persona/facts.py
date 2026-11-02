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


import asyncio
from copy import deepcopy
import hashlib
import json


import os




from datetime import datetime


from utils.cloudsave_runtime import assert_cloudsave_writable

from utils.file_utils import atomic_write_json_async, read_json_async


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
            'id': '',                   # Unique identifier.
            'text': '',
            'source': 'unknown',        # character_card | settings | reflection | manual
            'source_id': None,          # Upstream ID, e.g. reflection_id.
            'recent_mentions': [],      # Mention timestamps within the window.
            'suppress': False,          # Whether suppressed.
            'suppressed_at': None,      # Suppression start time.
            'protected': False,         # character_card-origin entries cannot be suppressed.
            # Evidence counters (RFC §3.2.3)
            'reinforcement': 0.0,
            'disputation': 0.0,
            'rein_last_signal_at': None,
            'disp_last_signal_at': None,
            'sub_zero_days': 0,
            'sub_zero_last_increment_date': None,
            # user_fact reinforcement-combo counter (RFC section 3.1.8), accumulated for life;
            # decay affects reinforcement values only, never this counter.
            'user_fact_reinforce_count': 0,
            # Provenance: reflection IDs absorbed by merge_into.
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
            # resolve_corrections' replace branch keeps "主人以前住东京，后来搬到
            # 大阪" (the owner moved from Tokyo to Osaka) traceable. Each item: {text, replaced_at, reason,
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
            # MemoryRefineEngine cluster_hash skip state, Phase A-3.
            # cluster_hash = sha1(sorted(member_ids)); after refinement, stamp every
            # surviving member with current cluster hash/time. When the same cluster
            # reforms, all hashes match and age stays below REVISIT_AFTER_DAYS,
            # so skip LLM calls. After any member merges/splits/modifies/discards,
            # new entries lack stamps and changed membership naturally invalidates hashes.
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
            # Legacy-field compatibility.
            entry.pop('mention_count', None)
            entry.pop('consecutive_mentions', None)
            entry.pop('last_mentioned', None)
            return entry
        d = dict(defaults)
        d['text'] = str(entry)
        return d

    def _collect_card_facts(self, persona: dict) -> list[dict]:
        """All protected character-card entries, whatever section holds them.

        Scoped writes only scan their own @subject section, so without this
        a group-derived claim that contradicts the fixed character
        definition (stored under master/neko/relationship) would be added
        instead of rejected."""
        card_facts: list[dict] = []
        if not isinstance(persona, dict):
            return card_facts
        for section in persona.values():
            if not isinstance(section, dict):
                continue
            for fact in section.get('facts') or []:
                if isinstance(fact, dict) and fact.get('source') == 'character_card':
                    card_facts.append(fact)
        return card_facts

    def _evaluate_fact_contradiction(
        self, name: str, text: str, section_facts: list, stop_names: list[str],
        *, redact_text: bool = False,
    ) -> tuple[str | None, str | None]:
        """Returns (rejection_code, conflicting_text) or (None, None) if OK.

        redact_text=True for scoped (group/participant) input: that content
        is deliberately kept out of the ordinary Memory log, so the
        rejection line records lengths instead of excerpts."""
        for existing in section_facts:
            if isinstance(existing, dict):
                old_text = existing.get('text', '')
                is_card = existing.get('source') == 'character_card'
            else:
                old_text = str(existing)
                is_card = False
            if self._texts_may_contradict(old_text, text, stop_names=stop_names):
                if is_card:
                    if redact_text:
                        # Scoped input bodies stay out of ordinary Memory logs, matching scoped
                        # extraction/persistence redaction rules; record lengths only.
                        logger.info(
                            f"[Persona] {name}: scoped 新条目与角色卡矛盾，"
                            f"无条件拒绝: card_len={len(old_text)} "
                            f"new_len={len(text)}"
                        )
                    else:
                        logger.info(
                            f"[Persona] {name}: 新条目与角色卡矛盾，无条件拒绝: "
                            f"card=\"{old_text[:40]}\" vs new=\"{text[:40]}\""
                        )
                    return self.FACT_REJECTED_CARD, old_text
                return self.FACT_QUEUED_CORRECTION, old_text
        return None, None

    def _build_fact_entry(
        self, text: str, source: str, source_id: str | None, *, subject=None,
        speaker_provenance: dict | None = None,
    ) -> dict:
        entry = self._normalize_entry(text)
        # Include isolation domains in scoped entry IDs: identical same-section/same-second text in different scopes otherwise collides,
        # causing ID-based archival/deletion across domains (Codex P2).
        domain_salt = (
            f"{subject.key}|{subject.scope}|" if subject is not None else ""
        )
        if source == 'reflection' and source_id:
            entry['id'] = f"prom_{source_id}"
        else:
            digest = hashlib.sha256((domain_salt + text).encode()).hexdigest()[:8]
            entry['id'] = f"manual_{datetime.now().strftime('%Y%m%d%H%M%S')}_{digest}"
        entry['source'] = source
        entry['source_id'] = source_id
        if subject is not None:
            entry.update(subject.as_entry_fields())
        if speaker_provenance:
            if source.startswith('reflection'):
                from memory.temporal import explicit_event_window
                event_start_at, event_end_at = explicit_event_window(
                    speaker_provenance,
                )
                if event_start_at is not None or event_end_at is not None:
                    entry['event_when_raw'] = deepcopy(
                        speaker_provenance.get('event_when_raw')
                    )
                    entry['event_start_at'] = event_start_at
                    entry['event_end_at'] = event_end_at
            from memory.speaker_trust import (
                finite_trust_score,
                normalize_trust,
                stable_speaker_id,
            )
            if speaker_provenance.get('speaker_provenance_mixed') is True:
                entry['speaker_provenance_mixed'] = True
                return entry
            speaker_id = stable_speaker_id(speaker_provenance.get('speaker_id'))
            if speaker_id is not None:
                entry['speaker_id'] = speaker_id
                trust = speaker_provenance.get('speaker_trust')
                trust_score = finite_trust_score(trust)
                if trust_score is not None:
                    entry['speaker_trust'] = normalize_trust(trust_score)
            label = str(speaker_provenance.get('speaker_label') or '').strip()
            if label:
                entry['speaker_label'] = label[:64]
        return entry

    def add_fact(self, name: str, text: str, entity: str = 'master',
                 source: str = 'manual', source_id: str | None = None,
                 subject=None, speaker_provenance: dict | None = None) -> str:
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

        # Sections may mix custom scopes because section keys omit scope;
        # contradiction scans consider only this subject, never letting cross-scope text
        # veto entries or trigger corrections.
        scan_facts = section_facts
        if memory_subject is not None:
            from memory.scopes import entry_matches_subject
            scan_facts = [
                e for e in section_facts
                if isinstance(e, dict) and entry_matches_subject(e, memory_subject)
            ]
        if memory_subject is not None:
            # Symmetric with aadd_fact: scoped scans cannot see character-card sections,
            # so reject group-derived assertions conflicting with fixed personas here.
            card_code, _card_text = self._evaluate_fact_contradiction(
                name, text, self._collect_card_facts(persona), stop_names,
                redact_text=True,
            )
            if card_code == self.FACT_REJECTED_CARD:
                return self.FACT_REJECTED_CARD
        code, old_text = self._evaluate_fact_contradiction(
            name, text, scan_facts, stop_names,
            redact_text=memory_subject is not None,
        )
        if code == self.FACT_REJECTED_CARD:
            return self.FACT_REJECTED_CARD
        if code == self.FACT_QUEUED_CORRECTION:
            correction_entity = (
                memory_subject.persona_section_key
                if memory_subject is not None else entity
            )
            self._queue_correction(
                name, old_text, text, correction_entity,
                subject_fields=(
                    memory_subject.as_entry_fields()
                    if memory_subject is not None else None
                ),
                old_speaker_provenance=next((
                    e for e in scan_facts
                    if isinstance(e, dict) and e.get('text') == old_text
                ), None),
                new_speaker_provenance=speaker_provenance,
            )
            return self.FACT_QUEUED_CORRECTION

        section_facts.append(self._build_fact_entry(
            text, source, source_id, subject=memory_subject,
            speaker_provenance=speaker_provenance,
        ))
        self.save_persona(name, persona)
        return self.FACT_ADDED

    async def aadd_fact(self, name: str, text: str, entity: str = 'master',
                        source: str = 'manual', source_id: str | None = None,
                        subject=None,
                        speaker_provenance: dict | None = None) -> str:
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

            # Match synchronous add_fact by filtering contradiction scans per subject.
            scan_facts = section_facts
            if memory_subject is not None:
                from memory.scopes import entry_matches_subject
                scan_facts = [
                    e for e in section_facts
                    if isinstance(e, dict) and entry_matches_subject(e, memory_subject)
                ]
            if memory_subject is not None:
                # Scoped writes scan only their isolation domains, missing master /
                # neko / relationship character-card entries and treating conflicting group-derived
                # assertions as normal additions; add a character-card check here.
                card_code, card_text = self._evaluate_fact_contradiction(
                    name, text, self._collect_card_facts(persona), stop_names,
                    redact_text=True,
                )
                if card_code == self.FACT_REJECTED_CARD:
                    return self.FACT_REJECTED_CARD
            code, old_text = self._evaluate_fact_contradiction(
                name, text, scan_facts, stop_names,
                redact_text=memory_subject is not None,
            )
            if code == self.FACT_REJECTED_CARD:
                return self.FACT_REJECTED_CARD
            if code == self.FACT_QUEUED_CORRECTION:
                correction_entity = (
                    memory_subject.persona_section_key
                    if memory_subject is not None else entity
                )
                await self._aqueue_correction_locked(
                    name, old_text, text, correction_entity,
                    subject_fields=(
                        memory_subject.as_entry_fields()
                        if memory_subject is not None else None
                    ),
                    old_speaker_provenance=next((
                        e for e in scan_facts
                        if isinstance(e, dict) and e.get('text') == old_text
                    ), None),
                    new_speaker_provenance=speaker_provenance,
                )
                return self.FACT_QUEUED_CORRECTION

            section_facts.append(self._build_fact_entry(
                text, source, source_id, subject=memory_subject,
                speaker_provenance=speaker_provenance,
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
                # Async lock is already held and memory cache is current; reuse it directly.
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
        source_provenance: dict | None = None,
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

            merged_provenance = None
            if source_provenance is not None:
                from memory.speaker_trust import provenance_of_entries
                merged_provenance = provenance_of_entries([
                    target_entry, source_provenance,
                ])

            merged_temporal = None
            if source_provenance is not None:
                from memory.temporal import explicit_event_window, to_naive_local

                explicit_windows = [
                    window for window in (
                        explicit_event_window(target_entry),
                        explicit_event_window(source_provenance),
                    )
                    if any(boundary is not None for boundary in window)
                ]
                if explicit_windows:
                    def _boundary_key(value: str) -> datetime:
                        parsed = datetime.fromisoformat(
                            value.replace('Z', '+00:00')
                        )
                        return to_naive_local(parsed)

                    starts = [
                        start for start, _end in explicit_windows if start
                    ]
                    ends = [end for _start, end in explicit_windows]
                    merged_temporal = {
                        'event_when_raw': deepcopy(
                            source_provenance.get('event_when_raw')
                            if any(explicit_event_window(source_provenance))
                            else target_entry.get('event_when_raw')
                        ),
                        'event_start_at': min(
                            starts, key=_boundary_key,
                        ) if starts else None,
                        'event_end_at': (
                            None
                            if any(end is None for end in ends)
                            else max(ends, key=_boundary_key)
                        ),
                    }

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
                # confirmations and disp likewise). RFC section 3.1.1 resets only the touched
                # side for ordinary aapply_signal, but merge is a
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
            if merged_provenance is not None:
                # Explicit nested snapshot distinguishes "legacy caller did
                # not reconcile provenance" from "this merge deliberately
                # cleared mixed/unknown provenance" during event replay.
                entry_payload['speaker_provenance'] = merged_provenance
            if merged_temporal is not None:
                entry_payload.update(merged_temporal)

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
                if merged_provenance is not None:
                    for key in (
                        'speaker_id', 'speaker_trust', 'speaker_label',
                    ):
                        target_entry.pop(key, None)
                    target_entry.update(merged_provenance)
                if merged_temporal is not None:
                    for key in (
                        'event_when_raw', 'event_start_at', 'event_end_at',
                    ):
                        target_entry.pop(key, None)
                    target_entry.update(deepcopy(merged_temporal))
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
            from memory.scopes import persona_subject_from_section

            previous_subject = persona_subject_from_section(
                subject.persona_section_key, section,
            )
            if previous_subject != subject:
                # The section key omits scope. When promotion hands the
                # section to another scope of the same subject, its old
                # human-readable name is isolation metadata and must not
                # follow the ownership change.
                section.pop('display_name', None)
            section.update(subject.as_entry_fields())
            section.setdefault('entity', subject.kind)
            return section.setdefault('facts', [])
        return persona.setdefault(entity, {}).setdefault('facts', [])

    async def aupdate_subject_display_name(
        self, name: str, subject, display_name, *, strict: bool = False,
    ) -> bool:
        """Stamp a human-readable display name onto an EXISTING scoped section.

        Scoped fact/history writes call this after success to refresh section
        metadata; renderers replace bare subject_id headings with name plus ID. Three
        deliberate boundaries:

        1. Never create sections: promotion creates scoped persona sections. Creating
           empty ones for names leaves shells in persona.json for every speaking
           group member, wasting rendering/promotion/refinement work. If absent,
           discard the name; the next write naturally self-heals it.
        2. Require exact scope matches: section keys omit scope and may contain
           another isolation domain. Stamping one's own name on another section
           crosses metadata boundaries, matching _normalize_entry_for_section's fail-closed rule.
        3. Sanitize display_name with sanitize_speaker_label because it enters prompt
           headings. User-editable group names/cards share speaker_label's attack
           surface (#2605), so reuse its neutralizer. Empty results mean no name;
           retain old values rather than clear them when temporarily unavailable.

        ``strict``: a persona file that cannot be read (or is not an object)
        raises instead of returning False, so a caller that must retry can
        tell it apart from the legitimate no-ops (no section yet, unchanged).
        """  # noqa: DOCSTRING_CJK
        from memory.facts import FactStore
        from memory.scopes import coerce_subject, persona_subject_from_section
        try:
            memory_subject = coerce_subject(subject)
        except Exception:
            return False
        if memory_subject is None:
            return False
        cleaned = FactStore.sanitize_speaker_label(display_name)
        if not cleaned:
            return False
        section_key = memory_subject.persona_section_key
        async with self._get_alock(name):
            # Cosmetic metadata must never invoke the recovery loader: a
            # malformed persona file makes _aensure_persona_locked() create and
            # save an empty replacement, destroying unrelated memory sections.
            # Strict-read an existing file and fail closed instead.
            path = self._persona_path(name)
            if await asyncio.to_thread(os.path.exists, path):
                try:
                    persona = await read_json_async(path)
                except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                    if strict:
                        raise
                    logger.warning(
                        f"[Persona] {name}: display_name skipped; strict load "
                        f"failed: {exc}"
                    )
                    return False
                if not isinstance(persona, dict):
                    if strict:
                        raise ValueError(f"persona of {name!r} is not an object")
                    logger.warning(
                        f"[Persona] {name}: display_name skipped; persona is not a dict"
                    )
                    return False
                self._personas[name] = persona
            else:
                persona = self._personas.get(name)
                if not isinstance(persona, dict):
                    return False
            section = persona.get(section_key)
            if not isinstance(section, dict):
                return False
            section_subject = persona_subject_from_section(section_key, section)
            if (
                section_subject is None
                or section_subject.key != memory_subject.key
                or section_subject.scope != memory_subject.scope
            ):
                return False
            if section.get('display_name') == cleaned:
                return False
            section['display_name'] = cleaned
            await self.asave_persona(name, persona)
            return True

    async def aforget_subject(self, name: str, subject) -> dict:
        """Delete one exact (subject, scope) domain from the persona view.

        Revocation entry, symmetric with FactStore/ReflectionEngine aforget_subject:
        1. Delete all entry_matches_subject matches in scoped sections.
        2. If emptied and section metadata belongs to this domain, remove the section
           including display_name. Section keys omit scope, so retain sections
           containing other scopes' entries.
        3. Also clear pending corrections stamped for this subject; otherwise
           resolving them writes deleted text back into persona.
        Leave archive shards untouched; they are outside rendering/recall, preserving event history.
        """  # noqa: DOCSTRING_CJK
        from memory.scopes import (
            SCOPED_PERSONA_PREFIX,
            MemoryScopeError,
            MemorySubject,
            coerce_subject,
            entry_matches_subject,
            persona_subject_from_section,
            subject_from_entry,
        )
        memory_subject = coerce_subject(subject)
        if memory_subject is None:
            raise ValueError("aforget_subject requires an explicit subject")
        section_key = memory_subject.persona_section_key
        removed_entries = 0
        section_dropped = False
        section_metadata_changed = False
        corrections_removed = 0

        def _correction_matches(correction: object) -> bool:
            if not isinstance(correction, dict):
                return False
            if entry_matches_subject(correction, memory_subject):
                return True
            # Older scoped queue rows can be unstamped and carry their owner
            # only in entity="@subject/<kind>:<id>". Mirror the resolver's
            # normalization exactly so forget cannot leave a reflow source.
            entity_raw = correction.get('entity')
            entity = entity_raw.strip() if isinstance(entity_raw, str) else ''
            if not entity.startswith(SCOPED_PERSONA_PREFIX):
                return False
            correction_subject = subject_from_entry(correction)
            if correction_subject is None:
                section_key_body = entity[len(SCOPED_PERSONA_PREFIX):]
                kind, _, subject_id = section_key_body.partition(':')
                try:
                    correction_subject = MemorySubject.create(
                        kind,
                        subject_id,
                        scope=correction.get('scope') or section_key_body,
                    )
                except MemoryScopeError:
                    return False
            return (
                correction_subject.key == memory_subject.key
                and correction_subject.scope == memory_subject.scope
            )

        # Same lock order as resolve_corrections (resolve → data): a forget
        # waits for any already-copied LLM batch to finish applying, then removes
        # both its result and queue source before reporting success.
        async with self._get_resolve_alock(name):
            async with self._get_alock(name):
                # Erasure must inspect the queue strictly *before* mutating
                # persona. The normal reader intentionally maps corruption to
                # [], which would allow a retained correction to recreate the
                # forgotten entry later.
                corrections_path = self._corrections_path(name)
                corrections: list = []
                if await asyncio.to_thread(os.path.exists, corrections_path):
                    try:
                        corrections_data = await read_json_async(corrections_path)
                    except (json.JSONDecodeError, OSError) as exc:
                        raise RuntimeError(
                            f"persona corrections unreadable during forget: {exc}"
                        ) from exc
                    if not isinstance(corrections_data, list):
                        raise RuntimeError(
                            "persona corrections are not a list during forget"
                        )
                    corrections = corrections_data

                # Keep correction cleanup under the same character lock:
                # _aqueue_correction_locked writes the same file under it; unlocked
                # read-modify-write would overwrite concurrent enqueueing. Write the queue first;
                # if persona persistence then fails, retry can still identify targets from persona,
                # whereas reverse ordering can leave rebound entries without source mappings.
                kept_corrections = [
                    c for c in corrections if not _correction_matches(c)
                ]
                corrections_removed = len(corrections) - len(kept_corrections)
                if corrections_removed:
                    assert_cloudsave_writable(
                        self._config_manager,
                        operation="save",
                        target=f"memory/{name}/persona_corrections.json",
                    )
                    await atomic_write_json_async(
                        corrections_path, kept_corrections,
                        indent=2, ensure_ascii=False,
                    )

                # The normal ensure path is allowed to recover a corrupt read
                # by constructing and saving an empty persona. During erasure
                # that would overwrite every unrelated section, so inspect the
                # on-disk view strictly and never repair it here.
                persona_path = self._persona_path(name)
                cached_persona = self._personas.get(name)
                persona: dict = (
                    cached_persona if isinstance(cached_persona, dict) else {}
                )
                if await asyncio.to_thread(os.path.exists, persona_path):
                    try:
                        persona_data = await read_json_async(persona_path)
                    except (
                        json.JSONDecodeError,
                        UnicodeDecodeError,
                        OSError,
                    ) as exc:
                        raise RuntimeError(
                            f"persona state unreadable during forget: {exc}"
                        ) from exc
                    if not isinstance(persona_data, dict):
                        raise RuntimeError(
                            "persona state is not an object during forget"
                        )
                    persona = persona_data
                self._personas[name] = persona
                section = persona.get(section_key)
                if isinstance(section, dict):
                    entries = section.get('facts')
                    if not isinstance(entries, list):
                        # This section key already identifies the requested
                        # subject id. A malformed collection cannot be
                        # inspected for the exact scope, so reporting success
                        # would leave potentially recoverable target entries.
                        raise RuntimeError(
                            "persona section facts are not a list during forget"
                        )
                    kept = [
                        e for e in entries
                        if not (
                            isinstance(e, dict)
                            and entry_matches_subject(e, memory_subject)
                        )
                    ]
                    removed_entries = len(entries) - len(kept)
                    entries[:] = kept
                    section_subject = persona_subject_from_section(
                        section_key, section,
                    )
                    if not section.get('facts'):
                        if (
                            section_subject is not None
                            and section_subject.key == memory_subject.key
                            and section_subject.scope == memory_subject.scope
                        ):
                            persona.pop(section_key, None)
                            section_dropped = True
                    elif (
                        section_subject is not None
                        and section_subject.key == memory_subject.key
                        and section_subject.scope == memory_subject.scope
                    ):
                        # This key deliberately omits scope. If another scope
                        # survives, retaining the forgotten scope's metadata
                        # leaks its display_name into the surviving section and
                        # prevents that scope from refreshing the name.
                        replacement_subject = None
                        for entry in section.get('facts') or []:
                            if not isinstance(entry, dict):
                                continue
                            candidate = persona_subject_from_section(
                                section_key, entry,
                            )
                            if candidate is not None:
                                replacement_subject = candidate
                                break
                        if replacement_subject is not None:
                            section.update(replacement_subject.as_entry_fields())
                        else:
                            for field in memory_subject.as_entry_fields():
                                section.pop(field, None)
                        section.pop('display_name', None)
                        section_metadata_changed = True
                if removed_entries or section_dropped or section_metadata_changed:
                    await self.asave_persona(name, persona)
        if (
            removed_entries
            or section_dropped
            or section_metadata_changed
            or corrections_removed
        ):
            logger.info(
                f"[Persona] {name}: forget "
                f"{memory_subject.key}/{memory_subject.scope}: "
                f"entries={removed_entries} section_dropped={section_dropped} "
                f"corrections={corrections_removed}"
            )
        return {
            "persona_entries": removed_entries,
            "persona_section_dropped": section_dropped,
            "corrections": corrections_removed,
        }

    def _normalize_entry_for_section(
        self, persona: dict, section_key: str, value,
    ) -> dict:
        """Normalize a persona fact and inherit its scoped section metadata.

        The section key carries kind:subject_id but not the scope, so one
        section can hold entries from several isolation domains and its
        metadata is whoever wrote last. Inherit only when the entry has no
        stamp of its own AND every stamped entry already there agrees with
        that metadata; otherwise leave it unstamped, which reads as
        fail-closed at render time rather than filing the fact under
        someone else's domain."""
        entry = self._normalize_entry(value)
        from memory.scopes import persona_subject_from_section, subject_from_entry
        section = persona.get(section_key, {})
        subject = persona_subject_from_section(section_key, section)
        if subject is None or subject_from_entry(entry) is not None:
            return entry
        stamped = {
            (e.get('subject_kind'), e.get('subject_id'), e.get('scope'))
            for e in (section.get('facts') or [])
            if isinstance(e, dict) and subject_from_entry(e) is not None
        }
        section_triple = (subject.kind, subject.subject_id, subject.scope)
        if stamped - {section_triple}:
            logger.info(
                f"[Persona] section {section_key} 含多个隔离域，"
                f"新条目不继承 section 元数据（按无戳处理）"
            )
            return entry
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
