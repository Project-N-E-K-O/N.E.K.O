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
"""Fact persistence pipeline, index backfill, and near-duplicate enqueueing methods."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from config import MEMORY_SCHEMA_VERSION_CURRENT
from memory.temporal import compute_event_timestamps, normalize_event_when
from memory import cold_store
from memory.timeindex import FACT_NEAR_DUP_ARBITRATE_OVERLAP
from memory.scopes import MemorySubject, coerce_subject, entry_matches_subject
from typing import Any

from memory import facts as _facts_facade
from ._shared import (
    SHARED_SOURCE_OVERLAP,
    _fact_scoped_identity,
    _readable_fact_id,
)

class WritePipelineMixin:
    # Source-tier whitelist. 'user_observation' = ground truth from path A user messages;
    # 'ai_disclosure' = path B AI disclosures/screen context (lower trust tier).
    # Legacy facts without source fall back to 'user_observation' (backward-compatible: all facts
    # before this PR came from user messages).
    _SOURCE_VALUES = frozenset({'user_observation', 'ai_disclosure'})

    _SOURCE_DEFAULT = 'user_observation'

    # In-memory note attached by source upgrade, removed by save_facts to permit one
    # monotonic signal_processed writeback exception. Underscore convention matches '_external_import':
    # survives one persistence flow only; always strip before save_facts disk writes.
    #
    # Rationale: save_facts read-merge treats signal_processed as False→True only
    # (#976, preventing stale-cache False overriding disk True and drain loops
    # consuming facts twice), but upgrade must reset it False (#1408,
    # user-confirmed AI disclosures must reenter Stage-2). Rules conflict; later persistence
    # always won, so upgraded Stage-2 reentry never actually persisted. Explicit upgrade notes
    # are less error-prone than making save_facts infer whether a fact was just unblocked.
    _SIGNAL_RESET_PENDING = '_signal_reset_pending'

    @staticmethod
    def _apply_external_import_provenance(entry: dict, external_import: dict) -> None:
        """Stamp external-import provenance onto a fact entry: metadata, tags, the
        event_start_at derived from event_date, and signal_processed=True
        (external_import facts skip the Stage-2 evidence loop)."""
        entry['external_import'] = dict(external_import)
        entry['tags'] = ['external_import', str(external_import.get('format') or 'unknown')]
        entry['signal_processed'] = True
        event_date = external_import.get('event_date')
        if isinstance(event_date, str) and event_date:
            entry['event_start_at'] = f"{event_date}T00:00:00"

    @staticmethod
    def _apply_visit_origin(entry: dict, visit_origin: dict) -> None:
        """Stamp visit provenance onto a NEW fact entry: ``origin='neko_visit'``, ``visit_id``, ``absorbed=True``."""
        entry['origin'] = 'neko_visit'
        entry['visit_id'] = str(visit_origin['visit_id'])
        entry['absorbed'] = True

    async def _apersist_new_facts(
        self, lanlan_name: str, extracted: list[dict],
        *,
        default_source: str = 'user_observation',
        semantic_dedup: bool = True,
        subject: MemorySubject | dict | None = None,
        speaker_provenance: dict | None = None,
        expected_subject_generation: int | None = None,
        reconciled_facts: list[dict] | None = None,
        effect_keys: list[str | None] | None = None,
        visit_origin: dict | None = None,
        source_messages: list | None = None,
        source_accepted: list[dict] | None = None,
        extraction_path: str | None = None,
        progress_pass: str | None = None,
    ) -> list[dict]:
        # Collect near-duplicate pairs under lock, submit only afterward: submission needs
        # FactDedupResolver's per-character lock, while aresolve reverses the order,
        # calling aarchive_arbitrated_facts under resolver lock, which needs
        # this _persist_alock. Mutual waits on one loop deadlock, permanently blocking
        # this character's writes and deduplication decisions.
        near_dup_pairs: list[tuple[dict, dict, float]] = []
        await self.aretry_pending_near_dups(lanlan_name)
        memory_subject = coerce_subject(subject)
        from memory.long_term_sync import (
            RETIRED_ASSERTION_REVIEW_MAX_PAIRS, retirement_evidence, retirement_review_pairs, verify_retired_assertions,
            resolve_retirement_cutoffs, trusted_message_source,
        )
        directory = os.path.join(self._config_manager.memory_dir, lanlan_name)
        retirements = await _facts_facade.asyncio.to_thread(retirement_evidence, directory) if memory_subject is None else []
        expected_retirements = retirements
        if default_source not in self._SOURCE_VALUES:
            default_source = self._SOURCE_DEFAULT
        # Validate the whole batch before changing any fact or signal marker.
        if memory_subject is None and (retirements or source_messages):
            extracted = [{**candidate,
                'entity': candidate.get('entity') if candidate.get('entity') in ('master', 'neko', 'relationship') else 'master',
                'source': candidate.get('source') if isinstance(candidate.get('source'), str) and candidate.get('source') in self._SOURCE_VALUES else default_source}
                if isinstance(candidate, dict) else candidate for candidate in extracted]
        # The held-back proof and queue id are this device's own records. A model
        # output carrying the same keys must not pass for one: drop them first.
        from memory.long_term_sync import STORED_PROOF
        from memory.retirement_review_queue import ANCHOR, QUEUE_ID, RETRY_LATER
        internal = (STORED_PROOF, QUEUE_ID, RETRY_LATER, ANCHOR)
        extracted = [{key: value for key, value in candidate.items() if key not in internal}
                     if isinstance(candidate, dict) and any(key in candidate for key in internal) else candidate
                     for candidate in extracted]
        # Candidates an earlier extraction held back come first, so the queue drains.
        review_queue = None
        if memory_subject is None and source_messages is not None:
            from memory import retirement_review_queue
            queue_key = retirement_review_queue.key(default_source, extraction_path, progress_pass)
            review_queue = await _facts_facade.asyncio.to_thread(retirement_review_queue.read, directory)
            brought_back = retirement_review_queue.candidates(review_queue, queue_key)
            extracted = brought_back + list(extracted)
        matches = {}
        held_back: list = []
        lookup = ((lambda ids: self._time_indexed.aget_original_source_evidence(lanlan_name, ids))
                  if getattr(self._time_indexed, 'aget_original_source_evidence', None) is not None else None)
        if retirements:
            if review_queue is not None and sum(len(pairs) for _, pairs in retirement_review_pairs(
                    retirements, extracted, source_messages, default_source=default_source)) > RETIRED_ASSERTION_REVIEW_MAX_PAIRS:
                # Over budget: known correction times narrow the pairs, since a record
                # whose time is still unknown pairs with every new fact on its subject.
                # Only a help here; a batch that fits never waits on this lookup.
                try:
                    retirements = await resolve_retirement_cutoffs(retirements, source_messages, lookup=lookup)
                except Exception as error:  # noqa: BLE001 - the strict resolution below still runs
                    _facts_facade.logger.info(f"[FactStore] {lanlan_name}: 预先解析纠正时间失败，按原样配对: {error}")
            matches = await verify_retired_assertions(retirements, extracted, source_messages,
                lambda prompt, **kwargs: self._allm_call_with_retries(prompt, lanlan_name, **kwargs), default_source=default_source,
                deferred=held_back if review_queue is not None else None)
            reviewed = [candidate for candidate in extracted if id(candidate) not in {id(item) for item in held_back}]
            relevant = [row for row in retirements if any(isinstance(candidate, dict)
                and candidate.get('entity') == row['subject'] and (row['id'] in matches.get(id(candidate), ())
                    or isinstance(candidate.get('text'), str) and candidate['text'].strip() == row.get('text', '').strip())
                for candidate in reviewed)]
            resolved = await resolve_retirement_cutoffs(relevant, source_messages, lookup=lookup)
            repaired = {(row['subject'], row['id']): row for row in resolved}
            retirements = [repaired.get((row['subject'], row['id']), row) for row in retirements]
        if held_back:
            _facts_facade.logger.info(f"[FactStore] {lanlan_name}: 退役断言审查本轮已达上限，{len(held_back)} 条候选留到下一轮")
            held = {id(candidate) for candidate in held_back}
            extracted = [candidate for candidate in extracted if id(candidate) not in held]
        retirement_review = (expected_retirements, retirements, matches)
        try:
            async with self._get_persist_alock(lanlan_name):
                memory_subject = coerce_subject(subject)
                if (
                    memory_subject is not None
                    and (
                        self._subject_forget_is_active(
                            lanlan_name, memory_subject,
                        )
                        or (
                            expected_subject_generation is not None
                            and self._subject_forget_generation(
                                lanlan_name, memory_subject,
                            ) != expected_subject_generation
                        )
                    )
                ):
                    _facts_facade.logger.info(
                        f"[FactStore] {lanlan_name}: 丢弃撤回期间完成的 scoped "
                        f"fact extraction ({memory_subject.key}/"
                        f"{memory_subject.scope})"
                    )
                    return []
                created = await self._apersist_new_facts_locked(
                    lanlan_name,
                    extracted,
                    default_source=default_source,
                    semantic_dedup=semantic_dedup,
                    subject=subject,
                    speaker_provenance=speaker_provenance,
                    reconciled_facts=reconciled_facts,
                    near_dup_pairs_out=near_dup_pairs,
                    retirement_review=retirement_review,
                    **({'source_messages': source_messages} if source_messages is not None else {}),
                    **({'source_accepted': source_accepted} if source_accepted is not None else {}),
                    **(
                        {"effect_keys": effect_keys}
                        if effect_keys is not None else {}
                    ),
                    **(
                        {"visit_origin": visit_origin}
                        if visit_origin is not None else {}
                    ),
                    **(
                        {"extraction_path": extraction_path}
                        if extraction_path is not None else {}
                    ),
                    **({'held_back_out': held_back} if review_queue is not None else {}),
                )
                progressed = source_messages
                if review_queue is not None:
                    kept, settled = await self._asave_retirement_review_queue_locked(
                        lanlan_name, queue_key, [candidate[retirement_review_queue.QUEUE_ID] for candidate in brought_back],
                        held_back, source_messages)
                    # A message whose fact still waits is not read yet, on any device.
                    progressed = [message for message in source_messages or []
                                  if trusted_message_source(message)[0] not in kept]
                    if progress_pass is not None and settled:
                        await self._arecord_extraction_progress_locked(
                            lanlan_name, progress_pass, None, identities=settled)
                if progress_pass is not None and memory_subject is None:
                    # Same lock as the facts above: a snapshot never carries this
                    # progress without the facts extracted from those messages.
                    await self._arecord_extraction_progress_locked(lanlan_name, progress_pass, progressed)
        finally:
            if near_dup_pairs:
                # Only committed pairs reach this basket. Release persist before
                # taking the resolver lock, including when cancellation is pending.
                from utils.asyncio_retirement import await_retirement
                await await_retirement(self._aenqueue_near_dup_pairs(lanlan_name, near_dup_pairs, durable=True))
        return created

    async def aextraction_progress(self, lanlan_name: str) -> dict:
        """Message identities each extraction pass has already read, on any device."""
        from memory import extraction_progress
        directory = os.path.join(self._config_manager.memory_dir, lanlan_name)
        return await _facts_facade.asyncio.to_thread(extraction_progress.read, directory)

    async def arecord_extraction_progress(self, lanlan_name: str, passage: str, messages: list | None) -> None:
        """Record a pass over messages that produced no fact to persist."""
        async with self._get_persist_alock(lanlan_name):
            await self._arecord_extraction_progress_locked(lanlan_name, passage, messages)

    async def _asave_retirement_review_queue_locked(self, lanlan_name, queue_key, brought_ids, held_back, source_messages):
        """Keep candidates still waiting for review; returns (identities waiting, identities now settled)."""
        from memory import retirement_review_queue
        from memory.long_term_sync import STORED_PROOF, fact_source_evidence
        added = []
        for candidate in held_back:
            if candidate.get(retirement_review_queue.QUEUE_ID):
                continue
            # Proven now, while its messages are at hand. A candidate the source
            # checks reject would be rejected on its return too.
            proof = fact_source_evidence([], candidate, candidate.get('entity', 'master'), source_messages,
                                         source=candidate.get('source', 'user_observation'))
            if proof:
                added.append({**{name: value for name, value in candidate.items() if name != 'source_indices'},
                              STORED_PROOF: proof, retirement_review_queue.ANCHOR: datetime.now().isoformat()})
        if not brought_ids and not added:
            return set(), []
        directory = os.path.join(self._config_manager.memory_dir, lanlan_name)

        def save():
            from utils.cloudsave_runtime import cloudsave_writable_transaction
            target = Path(directory) / retirement_review_queue.FILE
            with cloudsave_writable_transaction(self._config_manager, operation='save', target=target):
                return retirement_review_queue.commit(directory, queue_key, set(brought_ids), held_back, added, STORED_PROOF)
        from memory.recent import _await_recent_mutation_to_completion
        return await _await_recent_mutation_to_completion(save)

    async def _arecord_extraction_progress_locked(self, lanlan_name: str, passage: str, messages: list | None,
                                                  *, identities: list | None = None) -> None:
        from memory import extraction_progress
        if identities is None and messages:
            # Whatever path records it, a message whose fact still waits for review is not read yet.
            from memory import retirement_review_queue
            from memory.long_term_sync import STORED_PROOF, trusted_message_source
            waiting = await _facts_facade.asyncio.to_thread(
                retirement_review_queue.waiting_identities,
                os.path.join(self._config_manager.memory_dir, lanlan_name), STORED_PROOF)
            if waiting:
                messages = [message for message in messages if trusted_message_source(message)[0] not in waiting]
        processed = extraction_progress.identities(messages) if identities is None else identities
        if not processed:
            return
        directory = os.path.join(self._config_manager.memory_dir, lanlan_name)

        def save():
            from utils.cloudsave_runtime import cloudsave_writable_transaction
            target = Path(directory) / extraction_progress.STATE_FILE
            with cloudsave_writable_transaction(self._config_manager, operation='save', target=target):
                extraction_progress.record_processed(directory, passage, processed)
        from memory.recent import _await_recent_mutation_to_completion
        await _await_recent_mutation_to_completion(save)

    async def apersist_scoped_facts(
        self,
        lanlan_name: str,
        extracted: list[dict],
        *,
        subject: MemorySubject | dict,
    ) -> list[dict]:
        """Persist already extracted facts for an explicitly scoped adapter request."""
        memory_subject = coerce_subject(subject)
        if memory_subject is None:
            raise ValueError("apersist_scoped_facts requires an explicit subject")
        # Capture before waiting for the per-character persistence lock. If
        # a queued tombstone close wins that lock first, its generation bump
        # must still invalidate this request even though the active marker is
        # gone by the time persistence enters its critical section.
        expected_subject_generation = self._subject_forget_generation(
            lanlan_name, memory_subject,
        )
        return await self._apersist_new_facts(
            lanlan_name,
            extracted,
            subject=memory_subject,
            expected_subject_generation=expected_subject_generation,
        )

    async def _apersist_new_facts_locked(
        self, lanlan_name: str, extracted: list[dict],
        *,
        default_source: str = 'user_observation',
        semantic_dedup: bool = True,
        subject: MemorySubject | dict | None = None,
        speaker_provenance: dict | None = None,
        reconciled_facts: list[dict] | None = None,
        near_dup_pairs_out: list | None = None,
        retirement_review=None,
        effect_keys: list[str | None] | None = None,
        visit_origin: dict | None = None,
        source_messages: list | None = None,
        source_accepted: list[dict] | None = None,
        extraction_path: str | None = None,
        held_back_out: list | None = None,
    ) -> list[dict]:
        """Dedup (SHA-256 + FTS5) + persist. importance < 5 facts are KEPT
        (RFC §3.1.3)—downstream `get_unabsorbed_facts(min_importance=5)`
        filters at read time.

        ``default_source``: the fallback when an LLM-emitted fact dict has no
        ``source`` field. Path A callers pass ``'user_observation'`` (also the
        default), path B callers pass ``'ai_disclosure'`` — a source field
        explicitly emitted by the LLM wins over the default.

        External migration batches may set ``semantic_dedup=False`` after
        preview to avoid one FTS5 search per candidate while holding the
        persistence lock. Exact SHA-256 deduplication still applies.

        Exact matches keep their identity while retaining new trusted source
        evidence. If the existing source is 'ai_disclosure' and the incoming
        source is 'user_observation', upgrade
        existing.source in place + reset signal_processed=False so Stage-2
        re-evaluates. The reverse (user→ai) never downgrades — user
        corroboration is irreversible.

        ``speaker_provenance``: speaker provenance identifiers (speaker_label / speaker_trust /
        speaker_id whitelist), stamped only on this batch's newly created user_observation facts; AI
        disclosures never impersonate participant provenance. Supplied by callers (scoped_history
        request segments), never read from extracted
        elements' same-name keys: LLM output cannot forge it.

        ``reconciled_facts`` receives snapshots of existing rows whose
        provenance this call reconciled, so callers can distinguish their own
        write from a concurrent provenance change.

        ``source_accepted`` receives the candidates that passed source and
        retirement checks, before dedup folds any of them into existing rows,
        so the pinned section never admits a candidate persistence rejected.

        ``effect_keys`` (keyed scoped_history only) is aligned index-for-index
        with ``extracted``. A non-empty entry is stamped onto the created row
        as ``effect_key`` in the same save, and a candidate whose effect key
        already exists on any active or archived row is skipped. That closes
        the \"row written, journal not yet updated\" crash window independently
        of content dedup. It is a separate argument rather than a field on the
        extracted dict on purpose: extracted dicts are model output, and a
        model must never be able to mint or collide an effect key. ``None``
        (every legacy caller) leaves this method's behaviour unchanged.
        """  # noqa: DOCSTRING_CJK
        if default_source not in self._SOURCE_VALUES:
            default_source = self._SOURCE_DEFAULT
        memory_subject = coerce_subject(subject)

        from memory.long_term_sync import retirement_evidence, fact_source_evidence, FactEvidenceUnavailable, STORED_PROOF
        from memory.retirement_review_queue import RETRY_LATER
        expected, retirements, matches = retirement_review
        if memory_subject is None:
            directory = os.path.join(self._config_manager.memory_dir, lanlan_name)
            if await _facts_facade.asyncio.to_thread(retirement_evidence, directory) != expected:
                raise FactEvidenceUnavailable('retirement_evidence_changed')
            if retirements != expected:
                def save_repairs():
                    from utils.file_utils import atomic_write_json
                    from utils.cloudsave_runtime import cloudsave_writable_transaction
                    state_path = Path(directory) / 'long_term_portable.json'
                    with cloudsave_writable_transaction(self._config_manager, operation='save', target=state_path):
                        with open(state_path, encoding='utf-8') as handle:
                            state = json.load(handle)
                        repaired = {(row['subject'], row['id']): row for row in retirements}
                        for row in state['evidence']['retirements']:
                            update = repaired.get((row['subject'], row['id']), {})
                            for field in ('retiredThroughAt', 'retiredThroughAtFromCorrection'):
                                if field in update:
                                    row[field] = update[field]
                        atomic_write_json(state_path, state, ensure_ascii=False, indent=2)
                from memory.recent import _await_recent_mutation_to_completion
                await _await_recent_mutation_to_completion(save_repairs)
        source_provenance = {}
        # A candidate brought back from the review queue carries its own proof; it is
        # checked (and keeps its provenance) even with no retirements or messages left.
        brought_proof = any(isinstance(candidate, dict) and STORED_PROOF in candidate for candidate in extracted)
        if memory_subject is None and (retirements or source_messages or brought_proof):
            accepted = []
            accepted_effects = [] if effect_keys is not None else None
            for index, candidate in enumerate(extracted):
                if not isinstance(candidate, dict):
                    continue
                candidate_entity = candidate.get('entity', 'master')
                if candidate_entity not in ('master', 'neko', 'relationship'):
                    candidate_entity = 'master'
                candidate_source = candidate.get('source', default_source)
                try:
                    proof = fact_source_evidence(retirements, candidate, candidate_entity, source_messages, source=candidate_source, matched_ids=matches.get(id(candidate), ()))
                except FactEvidenceUnavailable:
                    if held_back_out is None or STORED_PROOF not in candidate:
                        raise
                    # A candidate brought back from the queue waits again instead of
                    # failing the window it happened to join.
                    held_back_out.append(candidate)
                    # Reviewed and still waiting: it goes behind those not reached yet.
                    candidate[RETRY_LATER] = True
                    continue
                if proof is not None:
                    accepted.append(candidate)
                    source_provenance[id(candidate)] = proof
                    if accepted_effects is not None:
                        accepted_effects.append(effect_keys[index] if index < len(effect_keys) else None)
            rejected = sum(isinstance(candidate, dict) for candidate in extracted) - len(accepted)
            unproven = sum(1 for candidate in accepted if not source_provenance[id(candidate)])
            if rejected or unproven:
                # Counts only: fact texts never go to the logger.
                _facts_facade.logger.info(
                    f"[FactStore] {lanlan_name}: 来源校验拒绝 {rejected} 条候选，"
                    f"{unproven} 条没有可用来源编号、按无来源保存"
                )
            extracted = accepted
            effect_keys = accepted_effects
        if source_accepted is not None:
            source_accepted.extend(candidate for candidate in extracted if isinstance(candidate, dict))

        new_facts: list[dict] = []
        # Stage-2 candidates (new fact, existing fact, text overlap). Only collect into caller's
        # basket here, never submit: submission acquires resolver lock, while this entire method
        # runs under _persist_alock, opposite aresolve's lock order (see caller comments).
        # Actually enqueue only after successful persistence and lock release; IDs-only queues
        # referencing facts not yet in facts.json would hold dangling references.
        near_dup_pairs: list[tuple[dict, dict, float]] = []
        upgraded_count = 0
        provenance_updated_count = 0
        # (entry, original source, original signal_processed): restore in-place upgrades on persistence/index
        # failure, otherwise retries hit guard and skip saving.
        upgraded_snapshots: list[tuple[dict, Any, Any]] = []
        provenance_snapshots: list[tuple[dict, dict[str, Any]]] = []
        source_snapshots: list[tuple[dict, dict[str, Any]]] = []
        request_provenance: dict[str, Any] = {}
        if isinstance(speaker_provenance, dict):
            from memory.speaker_trust import stable_speaker_id
            request_speaker_id = stable_speaker_id(
                speaker_provenance.get('speaker_id')
            )
            if request_speaker_id is not None:
                request_provenance['speaker_id'] = request_speaker_id
                # Persisted alongside — NOT instead of — ``speaker_id``, whose
                # bytes never change. It lets a same-person comparison succeed
                # even when the pool is unavailable later; when it goes stale
                # after a merge, the live pool lookup in
                # ``same_provenance_source`` backs it up.
                #
                # Comes FROM THE CALLER, never from a fresh pool read here. The
                # route resolves subject routing, trust and this id from ONE
                # snapshot taken at request start (§4.4). Re-reading the live
                # pool at persistence time would let an unbind landing
                # mid-request write a row under the OLD canonical subject while
                # stamping the account's NEW entity — and those stranded rows
                # would then miss the persisted-entity equality that keeps the
                # mixed pump closed.
                entity_id = str(
                    speaker_provenance.get('speaker_entity_id') or ''
                ).strip()
                if entity_id:
                    request_provenance['speaker_entity_id'] = entity_id
            # label/trust predate stable speaker_id and remain valid request-
            # derived provenance on legacy callers.  Keep them independent:
            # model output still never enters this mapping.
            label = str(
                speaker_provenance.get('speaker_label') or ''
            ).strip()
            if label:
                request_provenance['speaker_label'] = label[:64]
            trust = speaker_provenance.get('speaker_trust')
            if (
                isinstance(trust, (int, float))
                and not isinstance(trust, bool)
                and 0.0 <= float(trust) <= 1.0
            ):
                request_provenance['speaker_trust'] = float(trust)

        def _reconcile_existing_provenance(
            existing: dict | None, *, dedup_stage: str,
        ) -> None:
            nonlocal provenance_updated_count
            proof = source_provenance.get(id(fact), {})
            if existing is not None and proof.get('source_message_ids') and (
                source == 'user_observation' or existing.get('source', self._SOURCE_DEFAULT) == 'ai_disclosure'
            ):
                from memory.long_term_sync import merge_fact_source_evidence
                previous = {key: existing[key] for key in ('source_message_ids', 'source_created_at') if key in existing}
                replacing = source == 'user_observation' and existing.get('source') == 'ai_disclosure'
                desired = merge_fact_source_evidence({} if replacing else existing, proof)
                if previous != desired:
                    source_snapshots.append((existing, previous))
                    existing.pop('source_created_at', None)
                    existing.update(desired)
                    provenance_updated_count += 1
            if (
                existing is None
                or source != 'user_observation'
                or not request_provenance.get('speaker_id')
            ):
                return
            from memory.speaker_trust import (
                provenance_of_entries,
                same_provenance_source,
                stable_speaker_id,
            )
            provenance_keys = (
                'speaker_id', 'speaker_label', 'speaker_trust',
                'speaker_entity_id', 'speaker_provenance_mixed',
            )
            existing_speaker_id = stable_speaker_id(existing.get('speaker_id'))
            if existing.get('speaker_provenance_mixed') is True:
                desired_provenance = {'speaker_provenance_mixed': True}
            elif existing_speaker_id is None:
                desired_provenance = dict(request_provenance)
            elif existing_speaker_id == request_provenance['speaker_id']:
                desired_provenance = provenance_of_entries((
                    existing, request_provenance,
                ))
            else:
                # Three-state. Canonical write routing makes two accounts of
                # one person share a subject, and the fact hash is salted with
                # the subject, so the same sentence now collides here for the
                # first time. The old unconditional ``mixed`` would make that
                # collision destroy the row's provenance permanently.
                verdict = same_provenance_source(
                    existing, request_provenance,
                )
                if verdict is False:
                    # Genuinely two different people — today's behaviour, and
                    # this regression must be preserved (I-P-6): the relaxation
                    # applies to one person's accounts, NEVER across people.
                    desired_provenance = {'speaker_provenance_mixed': True}
                else:
                    # ``True``  → same person, different account: keep the
                    #             existing provenance verbatim. Abstain rather
                    #             than merge, so ``min(trusts)`` can never
                    #             ratchet an owner row down (see
                    #             ``provenance_of_entries``).
                    # ``None`` → unknown (pool unloaded / account unregistered).
                    #             "I don't know" must never be written down as
                    #             "I know it's mixed". Fail-closed: leave the
                    #             row exactly as it is and count it.
                    desired_provenance = {
                        key: existing[key]
                        for key in provenance_keys if key in existing
                    }
                    if verdict is None:
                        _facts_facade.logger.info(
                            f"[FactStore] {lanlan_name}: {dedup_stage} "
                            f"provenance_deferred fact_id={existing.get('id')} "
                            f"(身份未知，保留原样、不写 mixed)"
                        )
            current_provenance = {
                key: existing[key]
                for key in provenance_keys if key in existing
            }
            if current_provenance == desired_provenance:
                return
            provenance_snapshots.append((existing, current_provenance))
            for key in provenance_keys:
                existing.pop(key, None)
            existing.update(desired_provenance)
            provenance_updated_count += 1
            _facts_facade.logger.info(
                f"[FactStore] {lanlan_name}: {dedup_stage} fact provenance "
                f"reconciled fact_id={existing.get('id')} "
                f"existing_speaker={existing_speaker_id or '-'} "
                f"incoming_speaker={request_provenance['speaker_id']}"
            )
        if effect_keys is not None:
            # Keyed writes strictly verify disk first, then start from disk. Permissive loaders once cached unreadable files
            # as empty lists; after repair cache remained empty, overwriting all repaired facts on save.
            on_disk = await _facts_facade.asyncio.to_thread(self._assert_active_facts_readable, lanlan_name)
            if on_disk and not self._facts.get(lanlan_name):
                self._facts.pop(lanlan_name, None)
        existing_facts = await self.aload_facts(lanlan_name)
        existing_hashes = {f.get('hash') for f in existing_facts if f.get('hash')}
        # hash → fact lookup (upgrade path only). aload_facts already caches the list in place;
        # read without copying here.
        hash_to_existing = {
            f.get('hash'): f for f in existing_facts if f.get('hash')
        }
        # id → fact lookup: after Stage-2 semantic match, find existing fact by ID and compare daily
        # event_date for exemptions (repeated events on different dates are not duplicates, CodeRabbit).
        facts_by_id = {f.get('id'): f for f in existing_facts if f.get('id')}

        if semantic_dedup and self._time_indexed is not None:
            # Check once per batch, not per fact; no process cache needed outside loop.
            await self._aensure_fact_index_backfilled(
                lanlan_name, existing_facts,
            )

        # Effect-key set (only keyed scoped_history provides effect_keys): existing effect_key values
        # across active + archive pools. Without them, read no archives and construct no set.
        existing_effect_keys: set[str] | None = None
        # Return existing rows matching replayed effects too, or retries after facts persist but journal fails
        # report created: 0 and cannot return fact identities to callers.
        replayed_effect_rows: list[dict] = []
        active_effect_rows: dict[str, dict] = {}
        if effect_keys is not None:
            active_effect_rows = {
                f['effect_key']: f for f in existing_facts
                if isinstance(f, dict) and isinstance(f.get('effect_key'), str)
            }
            existing_effect_keys = set(active_effect_rows)
            existing_effect_keys |= await _facts_facade.asyncio.to_thread(
                self._read_archived_effect_keys, lanlan_name,
            )

        committed = False
        try:
            for fact_index, fact in enumerate(extracted):
                if not isinstance(fact, dict):
                    continue
                effect_key = None
                if existing_effect_keys is not None and fact_index < len(effect_keys):
                    candidate_effect_key = effect_keys[fact_index]
                    if isinstance(candidate_effect_key, str) and candidate_effect_key:
                        effect_key = candidate_effect_key
                if effect_key is not None and effect_key in existing_effect_keys:
                    # This effect already persisted last time (crash between fact write and journal record).
                    if effect_key in active_effect_rows:
                        replayed_effect_rows.append(dict(active_effect_rows[effect_key]))
                    continue
                text = fact.get('text', '').strip()
                if not text:
                    continue
                try:
                    importance = int(fact.get('importance', 5))
                except (ValueError, TypeError):
                    importance = 5
                # Clamp to the documented 1..10 range so downstream consumers
                # can assume a well-formed value; dirty LLM output (-3, 999)
                # would otherwise leak straight into reflection synthesis
                # weighting and audit dashboards (CodeRabbit PR #929).
                if importance < 1:
                    importance = 1
                elif importance > 10:
                    importance = 10
                # RFC §3.1.3: no longer drop importance < 5 at extraction entry. All facts
                # Persist uniformly; consumers filter via scenario-specific min_importance=, retaining complete audit.

                # Scoped writes own their entity: untrusted LLM output must not
                # redirect a group/participant fact into the legacy master bucket.
                # Legacy writes keep the original three-value whitelist unchanged.
                if memory_subject is not None:
                    entity = memory_subject.kind
                else:
                    raw_entity = fact.get('entity', 'master')
                    if raw_entity in ('master', 'neko', 'relationship'):
                        entity = raw_entity
                    else:
                        _facts_facade.logger.debug(
                            f"[FactStore] {lanlan_name}: LLM 返回非法 entity={raw_entity!r}，回退到 master"
                        )
                        entity = 'master'

                # Source resolution: explicit LLM source first + whitelist + default fallback.
                raw_source = fact.get('source')
                # Type first: a list/dict source is unhashable and would abort the
                # whole batch; it falls back to the default like any other junk.
                if isinstance(raw_source, str) and raw_source in self._SOURCE_VALUES:
                    source = raw_source
                else:
                    source = default_source

                # Stage 1: SHA-256 exact dedup（+ source monotonic upgrade）。
                # Daily import uses event_date + text as exact key: same-day retries remain idempotent,
                # while repeated events on different dates (e.g. gym visits on consecutive days) persist separately with provenance
                # (CodeRabbit). Salt persisted 'hash'; retries compare equally salted values.
                external_import = fact.get('_external_import')
                if not isinstance(external_import, dict):
                    external_import = None
                daily_event_date = (
                    str(external_import.get('event_date'))
                    if external_import
                    and external_import.get('section') == 'daily'
                    and external_import.get('event_date')
                    else None
                )
                hash_input = f"{daily_event_date}\n{text}" if daily_event_date else text
                if memory_subject is not None:
                    hash_input = f"{memory_subject.key}\n{memory_subject.scope}\n{hash_input}"
                content_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                if content_hash in existing_hashes:
                    existing = hash_to_existing.get(content_hash)
                    _reconcile_existing_provenance(existing, dedup_stage='exact')
                    if (
                        existing is not None
                        and existing.get('source', self._SOURCE_DEFAULT) == 'ai_disclosure'
                        and source == 'user_observation'
                    ):
                        # Path A user messages confirm ai_disclosure facts previously written by path B:
                        # upgrade source and reenter Stage-2 evidence loop.
                        # Scoped facts are exempt: simplified pipeline skips Stage-2; upgrade source but
                        # retain signal_processed=True.
                        upgraded_snapshots.append((
                            existing,
                            existing.get('source', self._SOURCE_DEFAULT),
                            existing.get('signal_processed'),
                        ))
                        existing['source'] = 'user_observation'
                        existing['signal_processed'] = memory_subject is not None
                        if not source_provenance.get(id(fact), {}).get('source_message_ids'):
                            # Without its own citation the claim keeps no proof at all:
                            # the character lines must not read as the user's source.
                            stale_proof = {key: existing.pop(key) for key in ('source_message_ids', 'source_created_at') if key in existing}
                            if stale_proof:
                                source_snapshots.append((existing, stale_proof))
                        # If confirmation comes from external import, attach external_import provenance too;
                        # SHA hit followed by immediate continue would miss the label (external_import semantics reset
                        # signal_processed=True and skip Stage-2) (Codex P2).
                        if external_import is not None:
                            self._apply_external_import_provenance(existing, external_import)
                        # Leave a note for save_facts monotonic read-merge: this False reset is deliberate;
                        # do not force it back to True via False→True-only rules. After provenance,
                        # recheck actual value: external imports reseal it True, in which case
                        # leave no note inconsistent with actual entry state.
                        if existing.get('signal_processed') is False:
                            existing[self._SIGNAL_RESET_PENDING] = True
                        upgraded_count += 1
                    continue

                # Stage 2: FTS5 near-duplicate retrieval (lightweight, no LLM).
                #
                # This gate no longer decides duplication itself (#2703): high text overlap does not mean
                # identical meaning. "养了一只猫" and "养了一只狗" have 2/3-gram
                # overlap 0.87, highest of all, yet both must remain. Thus except identical normalized
                # token sets (overlap 1.0, Stage-1 hash variants from Traditional/Simplified folding
                # or stop-name removal), persist all matches normally, submitting (new, old) pairs
                # to fact_dedup LLM arbitration for merge / replace / keep_both.
                arbitration_hit: tuple[dict, float] | None = None
                if semantic_dedup and self._time_indexed is not None:
                    # Fanout (same event stored for N subjects, tied BM25 ranks) may fill
                    # all first-window ten slots, putting this subject's candidates below rank 10;
                    # expand once to 200 and rescan. If still insufficient, permit writes (>200 matches imply
                    # degenerate generic text where deduplication is pointless). Skip cross-subject /
                    # cross-entity / absorbed matches without stopping scanning.
                    is_dup = False
                    duplicate_hit: dict | None = None
                    raw_limit = 10
                    archived_by_id: dict | None = None

                    async def _aarchived_by_id() -> dict:
                        # Lazily read facts_archive.json only for FTS hits outside the active set:
                        # archive rows remain FTS-indexed, while facts_by_id contains active rows only.
                        # Without archive metadata, archived identical text bypasses deduplication
                        # (legacy blocked this on main, so regression; scoped needs
                        # subject stamps for boundaries).
                        nonlocal archived_by_id
                        if archived_by_id is None:
                            def _read() -> dict:
                                path = self._facts_archive_path(lanlan_name)
                                if os.path.exists(path):
                                    try:
                                        cold = self._cold_identities(lanlan_name)
                                    except cold_store.ColdStoreUnreadable:
                                        cold = set()
                                    try:
                                        with open(path, encoding='utf-8') as fh:
                                            data = json.load(fh)
                                        if isinstance(data, list):
                                            # A copy also held cold is cold: it never
                                            # blocks a new statement of the same fact.
                                            return {
                                                r.get('id'): r for r in data
                                                if isinstance(r, dict)
                                                and _fact_scoped_identity(r) not in cold
                                            }
                                    except (
                                        json.JSONDecodeError,
                                        UnicodeDecodeError,
                                        OSError,
                                    ):
                                        # Corrupt archives degrade to active-only data (matching
                                        # load_facts_full), never blocking this write.
                                        pass
                                return {}
                            archived_by_id = await _facts_facade.asyncio.to_thread(_read)
                        return archived_by_id

                    while True:
                        similar = await self._time_indexed.asearch_similar_facts(
                            lanlan_name, text, raw_limit,
                        )
                        # Rescan and select across the full window each pass: dice is calculated after SQL LIMIT,
                        # so highest-BM25 first-window rows need not have highest overlap;
                        # expansion requires reconsidering the larger window.
                        arbitration_hit = None
                        for fid, overlap in similar:
                            hit = facts_by_id.get(fid)
                            if hit is None:
                                hit = (await _aarchived_by_id()).get(fid)
                            # Time-archived subject rows (subject_archived_at) do not block
                            # deduplication: resurrected subjects must be able to persist restated old facts as
                            # new active facts; archived rows no longer recall/render, so blocking restatements
                            # makes information permanently invisible. Absorbed archives without this marker
                            # continue blocking duplicates normally.
                            if (
                                hit is None
                                or hit.get('subject_archived_at')
                                or hit.get('arbitration_archived_at')
                                or not entry_matches_subject(hit, memory_subject)
                            ):
                                continue
                            if overlap < FACT_NEAR_DUP_ARBITRATE_OVERLAP:
                                continue
                            if daily_event_date:
                                # Daily candidates matching existing daily facts on different dates represent
                                # repeated events, not semantic duplicates (same-date near matches still block,
                                # preserving retry idempotency despite variable LLM re-extraction).
                                hit_meta = (hit or {}).get('external_import')
                                hit_date = (
                                    str(hit_meta.get('event_date'))
                                    if isinstance(hit_meta, dict) and hit_meta.get('event_date')
                                    else None
                                )
                                if hit_date and hit_date != daily_event_date:
                                    continue
                            if (hit.get('text') or '') == text:
                                # Only criterion permitting immediate new-fact discard: text identical
                                # character for character to the matched row.
                                #
                                # Former normalizations included token sets / Traditional-Simplified folding / stop-name
                                # removal / punctuation / case / whitespace; every one had counterexamples:
                                # n-gram sets lose order ("喜欢猫，不喜欢狗"
                                # versus "喜欢狗，不喜欢猫"); Traditional many-to-one (`鍾`/`鐘`
                                # both become `钟`); stop-name lists contain both names (`主人喜欢
                                # 猫`/`兰兰喜欢猫`); punctuation carries meaning (`-10`/`10`);
                                # case carries meaning (`/Foo`/`/foo`); whitespace carries meaning
                                # (`echo 'a  b'`/`echo 'a b'`). No normalization is safe here:
                                # irreversible discard requires character-for-character equality.
                                #
                                # Still necessary: Stage-1 hash sets contain active rows only,
                                # so only this gate blocks text identical to archived rows
                                # (archives blocking duplicates is existing main behavior).
                                is_dup = True
                                duplicate_hit = hit
                                break
                            if (
                                (hit.get('entity') or 'master') != entity
                                or hit.get('absorbed')
                            ):
                                # Cross-entity: arbitration queues bucket by entity (same boundary as vector
                                # detect_candidates); master / neko /
                                # relationship facts are incomparable; sending them to
                                # LLM invites erroneous merges. Absorbed facts already entered reflections;
                                # merging new text resurrects them from archives, worse than retaining duplicates.
                                # Hard blockers above are unaffected: Stage-1 hashes
                                # also omit entity, so identical sentences across entities already
                                # retain only one row; absorbed rows blocking duplicates is existing behavior.
                                continue
                            # No candidate budget anymore: take only first (descending overlap,
                            # hence strongest), making quota counting pointless. Its break could
                            # stop scanning before an exactly identical row:
                            # several rows with equal token sets but differing order (all overlap
                            # 1.0) ahead of it could permanently hide the true text match.
                            if arbitration_hit is None:
                                arbitration_hit = (hit, overlap)
                        if (
                            is_dup
                            or len(similar) < raw_limit
                            or raw_limit >= 200
                        ):
                            # Only reason to stop: initial window not full (nothing outside it).
                            #
                            # Deliberately do not stop because a candidate was selected or budget exhausted:
                            # dice is computed after SQL LIMIT, so three 0.26 matches in first window
                            # must not block the true 0.9 duplicate outside it,
                            # not yet scored. Three-candidate budgets govern candidates examined
                            # per pass, never whether to expand the window.
                            break
                        raw_limit = 200
                    if is_dup:
                        # Archived absorbed rows are outside this active-facts
                        # commit; only an active survivor can be reconciled here.
                        if (
                            duplicate_hit is not None
                            and duplicate_hit.get('id') in facts_by_id
                        ):
                            _reconcile_existing_provenance(
                                duplicate_hit, dedup_stage='semantic',
                            )
                        continue

                if arbitration_hit is None and memory_subject is None and self._dedup_resolver is not None:
                    # The same chat lines can reach extraction twice: on two devices, or
                    # after their progress entry aged out. Sharing a source proves nothing
                    # about meaning, so the pair only goes to the arbitration a text
                    # near-duplicate gets; nothing is folded together without its verdict.
                    cited = set(source_provenance.get(id(fact), {}).get('source_message_ids') or ())
                    if cited:
                        from memory.scopes import is_legacy_private_entry as _private
                        # One message often yields several facts; those of this batch are
                        # siblings, not repeats, so only rows from an earlier pass qualify.
                        siblings = {id(row) for row in new_facts}
                        arbitration_hit = next((
                            (row, SHARED_SOURCE_OVERLAP) for row in existing_facts
                            if isinstance(row, dict) and row.get('id') and not row.get('absorbed')
                            and id(row) not in siblings
                            and (row.get('entity') or 'master') == entity and _private(row)
                            and cited & set(row.get('source_message_ids') or ())), None)

                created_at_iso = datetime.now().isoformat()
                # Event timing (schema v2): LLM outputs relative offsets + units; system
                # resolves ISO timestamps anchored at created_at. Facts lack temporal_scope, but event
                # start is needed for outdated-block rendering/future rechecks; fallback_start=True
                # guarantees event_start_at. end_at is optional (facts mostly describe immediate
                # observations without explicit end).
                event_when_raw = normalize_event_when(fact.get('event_when'))
                # A candidate that waited for retired-assertion review keeps the time it was extracted.
                from memory.retirement_review_queue import ANCHOR as _review_anchor
                # Only an explicit relative time uses it; without one the start falls back to
                # created_at as for any fact, so it is not mistaken for an event window.
                anchor = (fact.get(_review_anchor) if event_when_raw and isinstance(fact.get(_review_anchor), str)
                          else created_at_iso)
                event_start_at, event_end_at = compute_event_timestamps(
                    event_when_raw,
                    anchor,
                    fallback_start=True,
                    fallback_end=False,
                )
                fact_entry = {
                    'id': f"fact_{datetime.now().strftime('%Y%m%d%H%M%S')}_{content_hash[:8]}",
                    'text': text,
                    'importance': importance,
                    'entity': entity,
                    # Trust-tier source: path A writes 'user_observation', path B
                    # 'ai_disclosure'. Stage-2 / other evidence-loop consumers filter as needed.
                    # Missing legacy source defaults to 'user_observation' on read for backward compatibility.
                    'source': source,
                    # RFC §2.7: retain tags field, but new facts default empty; LLM no longer fills it.
                    'tags': [],
                    'hash': content_hash,
                    'created_at': created_at_iso,
                    # Schema v2 (memory/temporal.py): event time, output by LLM as relative
                    # offset + unit, resolved against created_at. Retain event_when_raw
                    # for later rechecks / debug inspection.
                    'event_when_raw': event_when_raw,
                    'event_start_at': event_start_at,
                    'event_end_at': event_end_at,
                    'schema_version': MEMORY_SCHEMA_VERSION_CURRENT,
                    'absorbed': False,  # True when consumed by a reflection
                    # Stage-2 signal detection drain marker. False → still in queue
                    # for the next idle-loop tick. amark_signal_processed() flips
                    # to True after Stage-2 LLM returns successfully. Old facts.json
                    # without this key are read with default=True (i.e. treated as
                    # already processed) so an upgrade doesn't replay months of
                    # history through Stage-2.
                    #
                    # ⚠️ source='ai_disclosure' facts persist directly with True, so Stage-2
                    # never selects them. Together with aextract_facts_and_detect_signals'
                    # source filter, this provides double protection against leaks.
                    # ⚠️ Scoped group/member facts likewise use True: group memory takes simplified pipeline
                    # (facts → periodic confirmed reflection synthesis → time-driven promotion),
                    # not Stage-2 evidence. Scoped partitions lack user-confirm channels,
                    # leaving observation pools empty at cold start; queued entries permanently consume batch
                    # slots and starve legacy private-chat processing.
                    'signal_processed': (
                        source == 'ai_disclosure' or memory_subject is not None
                    ),
                    # Vector-embedding cache (memory-enhancements P2 — see
                    # memory/embeddings.py). Written as None so /process
                    # returns immediately without blocking on embedding;
                    # the background warmup worker fills the triple in
                    # batches once the EmbeddingService is ready. Used by
                    # the upcoming fact dedup path (cosine > threshold →
                    # LLM arbitration queue).
                    'embedding': None,
                    'embedding_text_sha256': None,
                    'embedding_model_id': None,
                }
                fact_entry.update(source_provenance.get(id(fact), {}))
                if memory_subject is not None:
                    fact_entry.update(memory_subject.as_entry_fields())
                if request_provenance and source == 'user_observation':
                    # Whitelisted, request-derived fields only. Model-produced
                    # lookalike keys never enter request_provenance.
                    fact_entry.update(request_provenance)

                if external_import is not None:
                    self._apply_external_import_provenance(fact_entry, external_import)
                if visit_origin is not None:
                    # Visit save-as-diary facts (only visit_facts passes this parameter; never read model-provided fields):
                    # stamp new rows only, never upgrade exact-dedup existing facts. absorbed=True prevents reflection synthesis.
                    self._apply_visit_origin(fact_entry, visit_origin)
                if extraction_path is not None:
                    # Which extraction pass wrote the row (EXTRACTION_PATH_B for
                    # the AI-aware pass). New rows only, like visit_origin; an
                    # exact-dedup hit keeps the existing row's marker or none.
                    fact_entry['extraction_path'] = extraction_path
                if effect_key is not None:
                    fact_entry['effect_key'] = effect_key
                    existing_effect_keys.add(effect_key)
                existing_facts.append(fact_entry)
                existing_hashes.add(content_hash)
                facts_by_id[fact_entry['id']] = fact_entry
                # Update hash_to_existing too: if later facts in this batch share text
                # (occasional LLM repetition / same observation across segments), the next
                # `content_hash in existing_hashes` match can obtain this turn's newly inserted
                # fact_entry and take the monotonic upgrade path. Otherwise hash_to_existing.
                # get() returns None, skipping upgrade and silently discarding
                # the new user_observation upgrade (Codex P2 round-10 on PR #1408).
                hash_to_existing[content_hash] = fact_entry
                new_facts.append(fact_entry)
                if arbitration_hit is not None:
                    if self._dedup_resolver is not None:
                        # Persist the ids-only handoff with the fact, before either file can commit alone.
                        fact_entry['_near_dup_pending'] = {
                            'existing_id': arbitration_hit[0]['id'], 'text_overlap': arbitration_hit[1],
                        }
                    near_dup_pairs.append(
                        (fact_entry, arbitration_hit[0], arbitration_hit[1]),
                    )

                if self._time_indexed is not None:
                    await self._time_indexed.aindex_fact(lanlan_name, fact_entry['id'], text)

            # Save additions, source upgrades and reconciled evidence. Without
            # upgrade persistence, B after A hits hash but
            # differing sources lose in-place modifications, and next startup's facts.json reload
            # wipes the upgrade.
            if new_facts or upgraded_count or provenance_updated_count:
                save_task = _facts_facade.asyncio.create_task(self.asave_facts(lanlan_name))
                try:
                    await _facts_facade.asyncio.shield(save_task)
                    committed = True
                except _facts_facade.asyncio.CancelledError:
                    # Do not release the persist lock while a submitted write is running.
                    while not save_task.done():
                        try:
                            await _facts_facade.asyncio.shield(save_task)
                        except _facts_facade.asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    committed = not save_task.cancelled() and save_task.exception() is None
                    raise
        except BaseException:
            if not committed:
                from utils.asyncio_retirement import await_retirement
                await await_retirement(self._rollback_uncommitted_facts(
                    lanlan_name, new_facts, existing_hashes,
                    upgraded_snapshots, provenance_snapshots, source_snapshots,
                ))
            raise
        finally:
            if committed and near_dup_pairs_out is not None:
                near_dup_pairs_out.extend(near_dup_pairs)
        if new_facts:
            _facts_facade.logger.info(
                f"[FactStore] {lanlan_name}: 提取了 {len(new_facts)} 条新事实"
            )
            for nf in new_facts:
                if nf.get('subject_kind') or nf.get('subject_id') or nf.get('scope'):
                    # Never log original scoped group/member-derived fact text: domain identifiers and
                    # length only, matching scoped reflection/correction dead-letter accounting.
                    _facts_facade.logger.debug(
                        f"   - [scoped {nf.get('subject_kind','?')}"
                        f"/{nf.get('subject_id','?')}] "
                        f"len={len(nf.get('text','') or '')}"
                    )
                else:
                    _facts_facade.logger.debug(
                        f"   - [{nf.get('entity','?')}/{nf.get('source','?')}] {nf.get('text','')[:80]}"
                    )
        if upgraded_count:
            _facts_facade.logger.info(
                f"[FactStore] {lanlan_name}: 升级 {upgraded_count} 条 ai_disclosure → user_observation "
                f"(user 印证后重入 Stage-2 evidence loop)"
            )
        if provenance_updated_count:
            _facts_facade.logger.info(
                f"[FactStore] {lanlan_name}: reconciled provenance for "
                f"{provenance_updated_count} exact facts"
            )
            if reconciled_facts is not None:
                reconciled_by_identity = {
                    identity: dict(entry)
                    for entry, _before in [*provenance_snapshots, *source_snapshots]
                    if (identity := _fact_scoped_identity(entry)) is not None
                }
                reconciled_facts.extend(reconciled_by_identity.values())

        return new_facts + replayed_effect_rows

    async def _aensure_fact_index_backfilled(
        self, lanlan_name: str, active_facts: list[dict],
    ) -> None:
        """Populate the near-dup index once per character per process.

        The index was rebuilt from scratch for #2703 (the old one indexed
        raw text under a tokenizer that could not retrieve Chinese), and
        ``index_fact`` only ever runs for a fact being written *now* — so
        without this, every fact that predates the upgrade stays invisible
        to Stage-2 and the check silently covers only new arrivals.

        Archived rows go in too: they still block duplicates (an absorbed
        row that stopped blocking would let its own text back in as a new
        fact), and dropping them from the index would change that.

        Best-effort by construction: a read-only or maintenance-mode
        store just doesn't get the backfill this round, and the marker
        stays unset so the next write retries.

        Deliberately keeps **no** process-local "already done" set: that
        cache would skip ``fts_index_needs_backfill`` entirely, so a
        table dropped or replaced underneath a running process would
        never be noticed (``index_fact`` would then rebuild an empty v2
        while the persistent marker survives, losing the history even
        across a restart). The check is one small query against
        sqlite_master, run once per write batch rather than per fact.
        """
        if self._time_indexed is None:
            return
        try:
            needs = await _facts_facade.asyncio.to_thread(
                self._time_indexed.fts_index_needs_backfill, lanlan_name,
            )
            if needs:
                # Carry IDs unchanged, never force str(): legacy rows permit
                # non-string scalar IDs (see _speaker_trust_fact_id annotation),
                # and FTS5 `WHERE fact_id = :fid` is type-sensitive. Storing integer 1
                # as text "1" defeats integer-ID privacy deletion, and Stage-2 cannot
                # find it in facts_by_id either.
                # _readable_fact_id: facts.json is an ordinary file modified by users/old versions;
                # ID may be list/dict. Treat those as unusable and skip,
                # matching all other paths; one malformed ID failing the whole backfill prevents
                # its marker forever and disables near-duplicate search for this character.
                rows: list[tuple[object, str]] = [
                    (fid, str(f.get('text') or ''))
                    for f in active_facts
                    if isinstance(f, dict)
                    and (fid := _readable_fact_id(f)) is not None
                ]

                # None return = read failure, distinct from missing file (valid zero rows).
                # Treating read failure as empty archives persists backfill completion normally;
                # that durable marker prevents retries after repair/restart, leaving those rows unable
                # to block duplicates, contrary to this method's docstring promise.
                def _read_archive() -> list[tuple[object, str]] | None:
                    path = self._facts_archive_path(lanlan_name)
                    if not os.path.exists(path):
                        return []
                    try:
                        with open(path, encoding='utf-8') as fh:
                            data = json.load(fh)
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                        return None
                    if not isinstance(data, list):
                        return None
                    # Index all archive rows, including arbitration losers.
                    #
                    # Previously excluded them because Stage-2 skips losers and they consume window space;
                    # but `arestore_arbitrated_fact` can restore losers to active,
                    # requiring index repair on resurrection. Every repair step
                    # (index_fact / invalidate backfill marker) swallows its own exceptions,
                    # making success indistinguishable from failure; four consecutive review rounds found
                    # more invisible failures along this chain.
                    #
                    # This exclusion was a tiny candidate-window optimization, introducing an entire
                    # class of silent failures. Retain rows: Stage-2 skips them before consuming candidate
                    # budget, with one expansion to 200 if first window fills;
                    # resurrection then needs no work because rows are already indexed.
                    return [
                        (fid, str(r.get('text') or ''))
                        for r in data
                        if isinstance(r, dict)
                        and (fid := _readable_fact_id(r)) is not None
                    ]

                archived_rows = await _facts_facade.asyncio.to_thread(_read_archive)
                if archived_rows is None:
                    _facts_facade.logger.warning(
                        f"[FactStore] {lanlan_name}: 归档不可读，跳过本轮近重复索引回填"
                    )
                    return
                rows.extend(archived_rows)
                indexed = await self._time_indexed.abackfill_fact_index(
                    lanlan_name, rows,
                )
                # Failed backfill (indexed is None) needs no extra handling: only successful
                # backfill_fact_index persists its marker, naturally retrying next write.
                if indexed:
                    _facts_facade.logger.info(
                        f"[FactStore] {lanlan_name}: 回填 {indexed} 条 fact 到近重复索引"
                    )
        except Exception as e:
            _facts_facade.logger.debug(
                f"[FactStore] {lanlan_name}: 近重复索引回填跳过: {e}"
            )

    @staticmethod
    def _log_background_enqueue_result(task) -> None:
        """Surface a background enqueue's outcome; nothing awaits it."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _facts_facade.logger.warning(f"[FactStore] 后台投递近重复候选失败: {exc}")

    async def _aack_near_dup_pairs(self, name, acknowledgements):
        from utils.asyncio_retirement import await_retirement
        from memory.fact_dedup import _fact_dedup_domain

        async def commit():
            async with self._get_persist_alock(name):
                rows = await self.aload_facts(name)
                changed = []
                for row in rows:
                    pending = row.get('_near_dup_pending') if isinstance(row, dict) else None
                    if pending and (row.get('id'), pending.get('existing_id'), _fact_dedup_domain(row)) in acknowledgements:
                        changed.append((row, row.pop('_near_dup_pending')))
                if changed:
                    try:
                        await self.asave_facts(name)
                    except Exception:
                        for row, pending in changed:
                            row['_near_dup_pending'] = pending
                        raise
        await await_retirement(commit())

    async def aretry_pending_near_dups(self, name) -> bool:
        """Recover durable fact-to-queue handoffs before resolving or publishing them."""
        from memory.fact_dedup import _fact_dedup_domain

        async with self._get_persist_alock(name):
            rows = await self.aload_facts(name)
            pairs, stale = [], set()
            for row in rows:
                pending = row.get('_near_dup_pending') if isinstance(row, dict) else None
                if not pending:
                    continue
                target = next((other for other in rows if isinstance(other, dict)
                               and other.get('id') == pending.get('existing_id')
                               and _fact_dedup_domain(other) == _fact_dedup_domain(row)), None)
                if target is None:
                    stale.add((row.get('id'), pending.get('existing_id'), _fact_dedup_domain(row)))
                else:
                    pairs.append((row, target, pending.get('text_overlap', 0.0)))
        if stale:
            await self._aack_near_dup_pairs(name, stale)
        return await self._aenqueue_near_dup_pairs(name, pairs, durable=True) if pairs else True

    async def _aenqueue_near_dup_pairs(
        self, lanlan_name: str,
        pairs: list[tuple[dict, dict, float]], *, durable=False,
    ) -> bool:
        """Hand Stage-2's near-dup hits to the LLM arbitration queue.

        Stage-2 knows two facts read alike; it does not know whether they
        say the same thing. That question already has an owner —
        ``FactDedupResolver`` batches (candidate, existing) pairs into one
        LLM call classifying merge / replace / keep_both — and the vector
        path feeds the very same queue, so a pair both paths find is
        deduped by id inside ``aenqueue_candidates`` rather than arbitrated
        twice.

        Without a resolver attached (an embedded/legacy FactStore, or a
        bootstrap that failed) the hits are logged and dropped: writing
        both facts and saying so beats dropping one on textual overlap
        alone.
        """
        from memory.fact_dedup import _fact_dedup_domain

        resolver = self._dedup_resolver
        if resolver is None:
            _facts_facade.logger.debug(
                f"[FactStore] {lanlan_name}: {len(pairs)} 对近重复命中无仲裁队列可投递"
            )
            return False
        payload: list[dict] = []
        for candidate, existing, overlap in pairs:
            domain = _fact_dedup_domain(existing)
            if domain is None:
                continue
            payload.append({
                'candidate_id': candidate.get('id'),
                'existing_id': existing.get('id'),
                'candidate_subject_kind': candidate.get('subject_kind'),
                'candidate_subject_id': candidate.get('subject_id'),
                'candidate_scope': candidate.get('scope'),
                'existing_subject_kind': existing.get('subject_kind'),
                'existing_subject_id': existing.get('subject_id'),
                'existing_scope': existing.get('scope'),
                'entity': existing.get('entity') or 'master',
                'subject_key': domain[0],
                'scope': domain[1],
                # Do not stuff overlap into cosine (default 0.0 on enqueue): these differ,
                # and queue cosine enters arbitration prompts unchanged, misrepresenting evidence strength
                # if substituted. Store text overlap separately; detector identifies its origin.
                'text_overlap': overlap,
                'detector': 'shared_source' if overlap == SHARED_SOURCE_OVERLAP else 'fts_near_dup',
            })
        if not payload:
            return True
        acknowledgements = {(candidate.get('id'), existing.get('id'), _fact_dedup_domain(candidate)) for candidate, existing, _ in pairs}
        acknowledged = False

        async def acknowledge():
            nonlocal acknowledged
            await self._aack_near_dup_pairs(lanlan_name, acknowledgements)
            acknowledged = True
        try:
            # Bound waiting: aresolve holds resolver's per-character lock
            # throughout the LLM call (60s timeout). Routes such as scoped-history directly
            # await _apersist_new_facts; facts already committed, so requests should not
            # wait another minute for unrelated background arbitration.
            #
            # ⚠️ shield is essential; never wait_for the coroutine directly: timeout after
            # acquiring queue lock and entering _asave_pending cancels/releases that lock while
            # atomic_write_json_async's thread keeps writing. Another writer can read-modify-write concurrently;
            # whichever atomic replacement finishes last overwrites the other's entire update. With shield,
            # stop waiting only; queue completes its write normally.
            enqueue = _facts_facade.asyncio.ensure_future(
                resolver.aenqueue_candidates(lanlan_name, payload, **({'on_persisted': acknowledge} if durable else {}))
            )
            appended = await _facts_facade.asyncio.wait_for(
                _facts_facade.asyncio.shield(enqueue),
                timeout=_facts_facade.FACT_NEAR_DUP_ENQUEUE_TIMEOUT_SECONDS,
            )
        except _facts_facade.asyncio.TimeoutError:
            _facts_facade.logger.info(
                f"[FactStore] {lanlan_name}: 仲裁队列忙，{len(payload)} 对近重复"
                f"候选改在后台投递"
            )
            enqueue.add_done_callback(self._log_background_enqueue_result)
            return False
        except Exception as e:
            _facts_facade.logger.warning(
                f"[FactStore] {lanlan_name}: 近重复候选入队失败: {e}"
            )
            return False
        if not appended:
            # Zero means either all pairs already queued (normal), or maintenance prevented
            # queue-file writing (candidates lost: retry hits Stage-1 exact deduplication,
            # never rerunning Stage-2). Indistinguishable here, but leave evidence rather than
            # appearing successfully delivered.
            _facts_facade.logger.debug(
                f"[FactStore] {lanlan_name}: {len(payload)} 对近重复候选未入队"
                f"（重复配对或队列不可写）"
            )
        return acknowledged if durable else True
