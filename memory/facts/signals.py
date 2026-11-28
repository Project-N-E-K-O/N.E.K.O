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
"""Stage-2 evidence signal target selection and signal detection methods."""

from __future__ import annotations

from datetime import datetime
from config import (
    EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS,
    EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS,
    EVIDENCE_DETECT_SIGNALS_MODEL_TIER,
)
from memory.evidence import evidence_score
from memory.scopes import MemorySubject, coerce_subject
from utils.language_utils import detect_prompt_language_with_ascii_fallback

from memory import facts as _facts_facade
from ._shared import (
    FactExtractionFailed,
    safe_importance,
)

class SignalsMixin:
    async def _aload_signal_targets(
        self, lanlan_name: str,
        reflection_engine=None, persona_manager=None,
        new_facts: list[dict] | None = None,
    ) -> list[dict]:
        """Assemble the Stage-2 `existing_observations` set.

        Per RFC §3.4.2 coverage rule:
          - all confirmed + promoted reflections (most recent first)
          - all non-protected persona entries
          (entries introspection retired are left out of both)

        Scale control (§3.4.2 end):
          - When ``new_facts`` is provided, the pool is routed through
            ``MemoryRecallReranker.aretrieve_candidates``, which owns
            the full pipeline regardless of vector service state:
            hard_filter (drops suppress / terminal / score<0 /
            protected) → coarse rank (cosine top-K when vectors are
            ready, evidence_score order otherwise) → optional LLM
            rerank.  Vectors save Stage-2 prompt tokens when ready;
            when not ready, behaviour collapses to filtered top-N by
            evidence_score, matching the legacy contract but with
            the suppression filter still applied.
          - When ``new_facts`` is empty, falls through to the legacy
            local top-N by evidence_score (no hard_filter — that
            shape predates P2 and is what idle-maintenance entry
            points expect).

        Injection pattern: memory_server wires `reflection_engine` / `persona_manager`
        references at call time. Without them we return empty, which simply
        makes Stage-2 skip (fail-open for unit tests).

        Returns list of {id, text, entity, evidence_score} — id is already
        in the `{target_type}.{entity}.{suffix}` shape the prompt expects.
        """
        now = datetime.now()
        pool: list[dict] = []

        # CodeRabbit follow-up: reflection/persona load failures were previously caught,
        # debug-logged, then partial pools returned. Downstream callers saw nonempty pools
        # and marked batches processed, permanently losing potential reinforce/negate signals
        # from failed pool parts. Let errors raise instead, so drain callers
        # catch and skip marking, retrying next idle round.
        # NegKW caller (memory_server.py) already catches errors independently, unaffected.
        from memory.introspection import is_permanently_suppressed

        if reflection_engine is not None:
            all_refl = await reflection_engine._aload_reflections_full(lanlan_name)
            for r in all_refl:
                if r.get('status') not in ('confirmed', 'promoted'):
                    continue
                if is_permanently_suppressed(r):
                    # Retired by introspection: no longer worth a slot in
                    # the evidence prompt. Excluded here, at the source,
                    # because the rebuilt pool rows do not carry the flag
                    # and some callers skip the hard filter.
                    continue
                pool.append({
                    'id': f"reflection.{r.get('id', '')}",
                    'raw_id': r.get('id', ''),
                    'target_type': 'reflection',
                    'text': r.get('text', ''),
                    'entity': r.get('entity', 'relationship'),
                    'score': evidence_score(r, now),
                    'embedding': r.get('embedding'),
                    'embedding_text_sha256': r.get('embedding_text_sha256'),
                    'embedding_model_id': r.get('embedding_model_id'),
                    'status': r.get('status'),
                    # Carry the AI-mention rate-limit suppress flag
                    # so MemoryRecallReranker._hard_filter can drop
                    # suppressed reflections from the rerank pool —
                    # reflections share persona's 5h-window mention
                    # gating (see ReflectionEngine._normalize_reflection).
                    # Codex PR-958 P2: without this, a vector-recall
                    # path with a suppressed reflection would slip
                    # past the filter and re-enter Stage-2 signal
                    # detection, defeating the suppression contract.
                    'suppress': r.get('suppress'),
                    'subject_kind': r.get('subject_kind'),
                    'subject_id': r.get('subject_id'),
                    'scope': r.get('scope'),
                })

        if persona_manager is not None:
            persona = await persona_manager.aensure_persona(lanlan_name)
            for entity_key, section in persona.items():
                if not isinstance(section, dict):
                    continue
                for entry in section.get('facts', []):
                    if not isinstance(entry, dict):
                        continue
                    if entry.get('protected'):
                        # protected = character_card; evidence always
                        # inf, so applying signals has no meaning. Skip.
                        continue
                    if is_permanently_suppressed(entry):
                        continue
                    pool.append({
                        'id': f"persona.{entity_key}.{entry.get('id', '')}",
                        'raw_id': entry.get('id', ''),
                        'target_type': 'persona',
                        'entity_key': entity_key,
                        'text': entry.get('text', ''),
                        'entity': entity_key,
                        'score': evidence_score(entry, now),
                        'embedding': entry.get('embedding'),
                        'embedding_text_sha256': entry.get('embedding_text_sha256'),
                        'embedding_model_id': entry.get('embedding_model_id'),
                        'suppress': entry.get('suppress'),
                        'subject_kind': entry.get('subject_kind'),
                        'subject_id': entry.get('subject_id'),
                        'scope': entry.get('scope'),
                    })

        # Keep Stage-2 evidence inside the same subject boundary as the facts
        # that triggered it. Legacy facts can only see legacy observations;
        # scoped facts can only see explicitly matching scoped observations.
        from memory.scopes import (
            filter_entries_for_subjects,
            subject_from_entry,
        )
        trigger_subjects = []
        include_legacy_private = not new_facts
        for fact in new_facts or []:
            trigger_subject = subject_from_entry(fact)
            if trigger_subject is None:
                include_legacy_private = True
            else:
                trigger_subjects.append(trigger_subject)
        pool = filter_entries_for_subjects(
            pool,
            trigger_subjects or None,
            include_legacy_private=include_legacy_private,
        )

        # P2 step 3: route through MemoryRecallReranker whenever we have
        # a query, regardless of vector service state.  The reranker
        # owns the unified pipeline:
        #
        #   _hard_filter (drops suppressed / terminal / score<0 /
        #     protected) → coarse rank (cosine top-K when vectors are
        #     ready, evidence_score order otherwise) → optional LLM
        #     rerank (skipped automatically when vectors aren't
        #     available).
        #
        # An earlier version gated the call on
        # `reranker._service.is_available()` and fell through to a
        # bare `pool.sort(score)` when the service was INIT / LOADING /
        # DISABLED.  That meant `suppress=True` rows leaked into
        # Stage-2 whenever vectors weren't ready, since the bare sort
        # path didn't apply `_hard_filter` (CodeRabbit PR-956 Major).
        # Behaviour now stays stable across the warmup window.
        if new_facts:
            try:
                from memory.recall import MemoryRecallReranker
                reranker = MemoryRecallReranker()
                query_texts = [
                    f.get('text', '') for f in new_facts if f.get('text')
                ]
                return await reranker.aretrieve_candidates(
                    pool, query_texts,
                    budget=EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS,
                    config_manager=self._config_manager,
                )
            except Exception as e:
                _facts_facade.logger.warning(
                    "[FactStore] vector+LLM rerank failed (%s: %s); "
                    "falling back to evidence_score order",
                    type(e).__name__, e,
                )

        # Fallback / legacy path: top-N by score DESC (most relevant
        # first). Reached when (a) ``new_facts`` is empty (no recall
        # query to drive the reranker), or (b) the reranker raised
        # mid-call.  This matches the pre-P2 behaviour exactly —
        # `_hard_filter` is intentionally NOT applied here because
        # the upstream consumers in the no-new_facts shape (some
        # idle-maintenance entry points) already operate on the
        # unfiltered pool.  CodeRabbit PR-956's Major was specifically
        # about the new_facts branch above silently bypassing the
        # filter when vectors weren't ready.
        pool.sort(key=lambda o: o.get('score', 0.0), reverse=True)
        return pool[:EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS]

    async def _allm_detect_signals(
        self, lanlan_name: str, new_facts: list[dict],
        existing_observations: list[dict],
    ) -> list[dict] | None:
        """Stage-2: map new facts onto existing observations with
        reinforces/negates signals. Returns validated signals (target_ids
        already filtered against existing_observations), or None on
        terminal failure.

        The cap on new_facts is enforced by the caller in
        ``aextract_facts_and_detect_signals`` via
        ``EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS`` (drain mode: the overflow
        stays signal_processed=False, handled on the next idle)."""
        if not new_facts or not existing_observations:
            return []

        # Build prompt sections.
        # Crucial: build budgeted_observations by cumulative budget first; prompt and later
        # valid_ids / id_to_obs must derive from that same subset. Otherwise tail truncation
        # removes observations while valid_ids still includes all; LLM may hallucinate
        # a truncated ID that passes validation, applying to the wrong entry (CodeRabbit fingerprint
        # e625b666 caught this race).
        from config import (
            EVIDENCE_PER_OBSERVATION_MAX_TOKENS,
            EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS,
        )
        from utils.tokenize import truncate_to_tokens, count_tokens
        new_facts_text = "\n".join(
            f"[{f.get('id', '')}] {truncate_to_tokens(f.get('text', '') or '', EVIDENCE_PER_OBSERVATION_MAX_TOKENS)}"
            for f in new_facts
        )
        # Accumulate tokens until total cap; omit excess tail observations from this prompt.
        budgeted_observations: list[tuple[dict, str]] = []  # (obs, formatted_line)
        running = 0
        for o in existing_observations:
            line = (
                f"[{o['id']}] "
                f"{truncate_to_tokens(o.get('text', '') or '', EVIDENCE_PER_OBSERVATION_MAX_TOKENS)}"
            )
            line_tokens = count_tokens(line) + 1  # +1 ≈ one newline.
            if budgeted_observations and running + line_tokens > EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS:
                # Keep at least one; drop tail once over total.
                break
            budgeted_observations.append((o, line))
            running += line_tokens
        if not budgeted_observations:
            return []
        obs_text = "\n".join(line for _, line in budgeted_observations)
        locale_text = "\n".join(
            truncate_to_tokens(
                f.get('text', '') or '',
                EVIDENCE_PER_OBSERVATION_MAX_TOKENS,
            )
            for f in new_facts
        )
        prompt_lang = detect_prompt_language_with_ascii_fallback(
            locale_text,
            ui_language=_facts_facade.get_global_language_full(),
        )
        prompt = _facts_facade.get_signal_detection_prompt(prompt_lang) \
            .replace('{NEW_FACTS}', new_facts_text) \
            .replace('{EXISTING_OBSERVATIONS}', obs_text) \
            .replace('{LANLAN_NAME}', lanlan_name)

        # Phase D: enable thinking for Stage-2 signal detection:
        # judge new_fact × existing_observation relations and select target_id.
        # Existing defenses [memory/facts.py:670-708](memory/facts.py:670) already compensate
        # for LLM hallucinations; thinking reduces target_id mismatch. Entirely background (signal extraction
        # loop), nobody waits. Raise timeout to 90s for thinking headroom.
        # Prompt-leading reasoning limits bound thinking (#3319: unconstrained qwen3.8-flash
        # thought for 8k–10k tokens, exceeding 90s); quota uses MEMORY_THINKING_OUTPUT_MAX_TOKENS.
        parsed = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_DETECT_SIGNALS_MODEL_TIER,
            call_type="memory_signal_detection",
            timeout=90,
            thinking=True,
        )
        if parsed is None:
            return None
        if not isinstance(parsed, dict):
            _facts_facade.logger.warning(
                f"[FactStore] {lanlan_name}: Stage-2 返回非 dict "
                f"{type(parsed).__name__}，丢弃"
            )
            return []
        raw_signals = parsed.get('signals', [])
        if not isinstance(raw_signals, list):
            return []

        # Defensive: drop hallucinated target_ids (§3.4.8). Validation pool must match
        # the prompt subset, or budget-truncated observation IDs still count as valid.
        valid_ids = {o['id'] for o, _ in budgeted_observations}
        id_to_obs = {o['id']: o for o, _ in budgeted_observations}
        # Validate source_fact_id against this batch's new_facts too (CodeRabbit 1f follow-up),
        # or hallucinated fact IDs absent from this prompt become valid sources
        # for evidence-counter updates.
        new_fact_ids = {f['id'] for f in new_facts if f.get('id')}
        validated: list[dict] = []
        # One Stage-2 call may return N signals for the same reflection with inconsistent
        # target_type: model guesses naming conventions ("persona.relationship"
        # versus "persona" versus "persona.relationship.prom"), copying visible prefixes.
        # Fallback works as designed, but per-signal logs flood output; deduplicate/count (LLM value→actual value)
        # and summarize once after loop, revealing repeated mistaken guesses.
        target_type_fixes: dict[tuple[str | None, str], int] = {}
        for s in raw_signals:
            if not isinstance(s, dict):
                continue
            tid = s.get('target_id')
            ttype = s.get('target_type')
            signal = s.get('signal')
            if signal not in ('reinforces', 'negates'):
                continue
            sid = s.get('source_fact_id')
            if sid is not None and sid not in new_fact_ids:
                _facts_facade.logger.warning(
                    f"[FactStore] {lanlan_name}: Stage-2 返回 source_fact_id="
                    f"{sid!r} 不在本批 new_facts 里，丢弃"
                )
                continue
            # Reconstruct full prompt-space id if LLM returned just the raw id
            candidate_full = tid
            if tid not in valid_ids:
                # Try prefixing (LLM sometimes returns just "r_xxx" instead of
                # "reflection.r_xxx"). Match by endswith on prompt ids.
                candidates = [vid for vid in valid_ids if vid.endswith(f".{tid}")]
                if len(candidates) == 1:
                    candidate_full = candidates[0]
                else:
                    _facts_facade.logger.warning(
                        f"[FactStore] {lanlan_name}: Stage-2 返回未知 "
                        f"target_id={tid}，丢弃"
                    )
                    continue
            obs = id_to_obs[candidate_full]
            if obs['target_type'] != ttype:
                # LLM target_type differs from actual → correct to actual.
                # Do not log inside loop; summarize after completion.
                # ttype comes from LLM JSON, theoretically str/None; hallucinated
                # list/dict keys raise TypeError and crash all Stage-2
                # (Codex review #1414). repr converts unhashable values to strings.
                key_ttype = ttype if ttype is None or isinstance(ttype, str) else repr(ttype)
                target_type_fixes[(key_ttype, obs['target_type'])] = (
                    target_type_fixes.get((key_ttype, obs['target_type']), 0) + 1
                )
            validated.append({
                'source_fact_id': s.get('source_fact_id'),
                'target_type': obs['target_type'],
                'target_id': obs['raw_id'],
                'target_full_id': candidate_full,
                'entity_key': obs.get('entity_key'),   # Persona only.
                'signal': signal,
                'reason': s.get('reason', ''),
            })
        if target_type_fixes:
            summary = ", ".join(
                f"{src!r}→{dst}×{n}" if n > 1 else f"{src!r}→{dst}"
                for (src, dst), n in sorted(
                    target_type_fixes.items(), key=lambda kv: -kv[1]
                )
            )
            _facts_facade.logger.info(
                f"[FactStore] {lanlan_name}: Stage-2 target_type 修正 {summary}"
            )
        return validated

    async def aextract_facts_and_detect_signals(
        self, lanlan_name: str, messages: list,
        reflection_engine=None, persona_manager=None,
        *, conversation_at: datetime | None = None, judge: bool = True,
    ) -> tuple[list[dict], list[dict], list[str]]:
        """Two-stage extraction (RFC §3.4.2) with drain semantics.

        Stage-1: pure fact extraction from user messages — no existing
        observations in prompt to avoid self-cycling.
        Stage-2: new_facts × existing_observations → reinforces/negates
        signals (with defensive target_id validation).

        Drain (PR #976):
        - Facts extracted in Stage-1 are persisted with ``signal_processed=False``
        - Stage-2 pulls **all** facts with signal_processed=False (not just this
          round's new ones, also the unfinished tail of previous rounds), takes
          the top N (=EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS) by importance DESC
          into Stage-2, and leaves the rest untouched for the next idle tick to
          drain

        Returns ``(new_facts_this_round, signals, batch_fact_ids)``:
        - ``new_facts_this_round``: facts newly extracted + persisted by this
          round's Stage-1 (for outbox and other audit purposes)
        - ``signals``: evidence signals awaiting dispatch
        - ``batch_fact_ids``: fact ids successfully processed by this Stage-2
          round. The drain queue only ever carries legacy-private facts —
          scoped (group/member) facts take the simplified pipeline and are
          written with ``signal_processed=True``; any stray non-legacy row is
          defensively dequeued before batching. The caller
          must call ``amark_signal_processed(lanlan_name, batch_fact_ids)`` only
          after every returned signal has been applied successfully via
          aapply_signal. If dispatch fails, the next idle sees those facts still
          at signal_processed=False and retries them (CodeRabbit fingerprint
          c755101c).

        Failure semantics (§3.4.2, last paragraph):
        - Stage-1 failure → abort, no fact written; caller retries later
        - Stage-2 LLM failure → batch_fact_ids still returns []; the caller
          won't mark, and the next idle retries the same batch
        - dispatch failure (caller side) → the caller doesn't call amark;
          retried next round
        """
        extracted = await self._allm_extract_facts(lanlan_name, messages)
        if extracted is None:
            # Stage-1 terminal failure — caller MUST NOT advance cursor
            # (§3.4.3: Stage-1 failure → abort whole operation; retry next idle trigger.)
            raise FactExtractionFailed(
                f"Stage-1 LLM call exhausted retries for {lanlan_name!r}"
            )
        if not extracted:
            extracted = []

        # Persist newly extracted facts with signal_processed=False.
        # Even zero new facts still drains previous unprocessed tail below.
        source_accepted: list[dict] = []
        persisted_this_round = await self._apersist_new_facts(
            lanlan_name, extracted, source_messages=messages, source_accepted=source_accepted,
            progress_pass='a',
        )
        # The pre-dedup result, not the persisted rows: a fact that dedup folded into
        # an existing one still has to renew its pinned entry. Candidates rejected by
        # the source checks (for example a retired assertion) never reach it.
        await self._aadmit_pinned_candidates(
            lanlan_name, source_accepted, conversation_at=conversation_at,
        )

        if not judge:
            # Judging belongs to the device that organizes this character. The facts
            # stay unjudged, travel in the snapshot that way, and are judged there.
            return persisted_this_round, [], []
        signals, batch_fact_ids = await self.adetect_pending_signals(
            lanlan_name, reflection_engine=reflection_engine, persona_manager=persona_manager,
        )
        return persisted_this_round, signals, batch_fact_ids

    async def adetect_pending_signals(
        self, lanlan_name: str, reflection_engine=None, persona_manager=None,
    ) -> tuple[list[dict], list[str]]:
        """Stage-2 alone: judge facts still waiting, wherever they were extracted.

        Returns ``(signals, batch_fact_ids)`` with the contract of
        ``aextract_facts_and_detect_signals``: mark the batch processed only
        after every signal was applied. Facts another device extracted arrive
        unjudged in a snapshot, so the organizing device calls this without
        needing a conversation of its own.
        """
        # Drain all signal_processed=False facts (historical tail + current additions).
        # Old facts lacking this field default True, avoiding months of historical facts
        # reentering Stage-2 after upgrade.
        #
        # ⚠️ Source filter excludes source='ai_disclosure': AI self-disclosures
        # must not enter evidence loops (avoids self-reinforcing cycle: AI says "I like X" → extraction → Stage-2
        # increases "neko likes X" reflection score → AI says "I like X" more often → ...).
        # Path B already persists signal_processed=True as fallback (_apersist_new_facts);
        # this source filter adds protection if new paths forget to set signal_processed.
        # Legacy facts without source default to 'user_observation' for compatibility.
        all_facts = await self.aload_facts(lanlan_name)
        unprocessed = [
            f for f in all_facts
            if not f.get('signal_processed', True)
            and not f.get('_near_dup_pending')
            and f.get('source', self._SOURCE_DEFAULT) != 'ai_disclosure'
        ]

        # Stage-2 evidence belongs only to legacy private-chat pipeline. Scoped group/member facts use
        # simplified pipeline, persisting signal_processed=True, so should never appear here. If they do
        # (corrupt subject metadata / old-code residue), mark and dequeue directly:
        # no corresponding evidence observation pool exists; retaining them permanently consumes batch
        # slots, starving legacy processing (extra LLM calls wasted on every idle tick too).
        from memory.scopes import is_legacy_private_entry

        stray = [f for f in unprocessed if not is_legacy_private_entry(f)]
        if stray:
            # Unconditionally filter nonlegacy rows from batches, including unmarkable rows without IDs:
            # they must never enter legacy-only Stage-2 prompts (cross-boundary leakage).
            unprocessed = [
                f for f in unprocessed if is_legacy_private_entry(f)
            ]
            stray_ids = [f['id'] for f in stray if f.get('id')]
            _facts_facade.logger.info(
                "[FactStore] %s: Stage-2 出队 %d 条非 legacy fact"
                "（scoped 走简化管线 / subject 损坏防御；无 id 仅过滤不标记 %d 条）",
                lanlan_name, len(stray), len(stray) - len(stray_ids),
            )
            if stray_ids:
                await self.amark_signal_processed(lanlan_name, stray_ids)
        if not unprocessed:
            return [], []

        # Sort importance DESC + creation time ASC; take first N as this batch.
        # Leave excess signal_processed=False for next idle tick.
        unprocessed.sort(
            key=lambda f: (
                -safe_importance(f),
                str(f.get('created_at') or ''),
            ),
        )
        batch = unprocessed[:EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS]

        # Batch now contains legacy private facts only (scoped defensively dequeued above),
        # retaining single-batch Stage-2 semantics predating partitions. _aload_signal_targets'
        # scope filter also restricts observation pools to legacy (defense in depth: future scoped
        # observations in reflections/persona still cannot leak here).
        try:
            existing_observations = await self._aload_signal_targets(
                lanlan_name,
                reflection_engine=reflection_engine,
                persona_manager=persona_manager,
                new_facts=batch,
            )
        except Exception as e:
            _facts_facade.logger.warning(
                f"[FactStore] {lanlan_name}: _aload_signal_targets 失败，"
                f"跳过本轮 Stage-2（batch 保持未处理下轮重试）: {e}"
            )
            return [], []
        if not existing_observations:
            # Cold start: no confirmed reflection/persona observation targets yet.
            # Leave unmarked and retry next round (same semantics as before scope).
            return [], []

        signals = await self._allm_detect_signals(
            lanlan_name, batch, existing_observations,
        )
        if signals is None:
            # Stage-2 LLM failure: return no IDs; caller leaves unmarked and retries same batch next round.
            return [], []

        # Caller still marks only after every returned signal dispatch succeeds.
        return signals, [
            fact['id'] for fact in batch if fact.get('id')
        ]

    async def extract_facts(
        self,
        messages: list,
        lanlan_name: str,
        *,
        subject: MemorySubject | dict | None = None,
        fail_closed: bool = False,
        speaker_label: str | None = None,
        speaker_provenance: dict | None = None,
        reconciled_facts: list[dict] | None = None,
        conversation_at: datetime | None = None,
        label_own_words: bool = False,
    ) -> list[dict]:
        """Stage-1-only backward-compat entry.

        Kept for callers that predate the evidence mechanism
        (memory_server's _run_post_turn_signals OFF-mode fallback transitively
        calls this, plus outbox replay). Emits only new facts and skips
        signal detection — downstream `_periodic_signal_extraction_loop`
        runs Stage-1+Stage-2 together.

        Unlike the Stage-1+2 entry, a Stage-1 terminal failure is swallowed
        here by default (returns []): the legacy per-turn call site treats
        extraction as best-effort — the next turn / the background loop will
        retry over durably stored history.

        ``fail_closed=True`` raises :class:`FactExtractionFailed` on terminal
        failure (retries exhausted or malformed payload) instead. The scoped
        history route needs the distinction: its callers checkpoint volatile
        caller-owned buffers on success, so a swallowed failure would
        permanently drop the batch (Codex P1). A genuine empty extraction
        still returns [].

        ``speaker_label`` and ``label_own_words`` are forwarded to the
        extraction prompt — see :meth:`_allm_extract_facts`.

        ``speaker_provenance``: stamped onto newly created user_observation facts
        (speaker_label / speaker_trust / speaker_id); AI
        disclosures retain independent sources. Pass separately from ``speaker_label``: that controls
        prompt rendering; group-digest routes default it to a collective descriptor. Such
        calls without one speaker must not stamp fact provenance; callers decide
        whether to supply this parameter.

        ``reconciled_facts`` receives snapshots of existing rows whose
        provenance this extraction reconciled.

        ``conversation_at``: when the turn took place, for the pinned-fact
        section (a turn replayed after a restart is older than now).
        """  # noqa: DOCSTRING_CJK
        memory_subject = coerce_subject(subject)
        expected_subject_generation = (
            self._subject_forget_generation(lanlan_name, memory_subject)
            if memory_subject is not None else None
        )
        extracted = await self._allm_extract_facts(
            lanlan_name, messages,
            treat_malformed_as_failure=fail_closed,
            speaker_label=speaker_label,
            label_own_words=label_own_words,
        )
        if extracted is None and fail_closed:
            raise FactExtractionFailed(
                f"Stage-1 LLM call failed for {lanlan_name!r} (fail_closed caller)"
            )
        if fail_closed and isinstance(extracted, list) and extracted:
            # Any malformed element makes the whole batch retryably fail: persistence silently skips bad items,
            # permanently losing their content after callers advance cursors. Retry complete extraction;
            # deduplication covers repeated valid items (matching daily import semantics).
            malformed = [
                f for f in extracted
                if not (
                    isinstance(f, dict)
                    and isinstance(f.get('text'), str)
                    and f['text'].strip()
                )
            ]
            if malformed:
                raise FactExtractionFailed(
                    f"Stage-1 returned {len(malformed)}/{len(extracted)} "
                    f"malformed fact entries for {lanlan_name!r} "
                    f"(fail_closed caller)"
                )
        if not extracted:
            if memory_subject is None:
                if isinstance(extracted, list):
                    # Nothing new still drains candidates an earlier extraction held
                    # back, and records progress the way a non-empty one does.
                    drained: list[dict] = []
                    await self._apersist_new_facts(lanlan_name, [], source_messages=messages, progress_pass='a',
                                                   source_accepted=drained)
                    # Brought-back candidates join the pinned section as they would have.
                    await self._aadmit_pinned_candidates(lanlan_name, drained, conversation_at=conversation_at)
                else:
                    await self.arecord_extraction_progress(lanlan_name, 'a', messages)
            return []
        source_accepted: list[dict] = []
        created = await self._apersist_new_facts(
            lanlan_name, extracted, subject=subject,
            speaker_provenance=speaker_provenance,
            expected_subject_generation=expected_subject_generation,
            reconciled_facts=reconciled_facts,
            source_messages=messages,
            source_accepted=source_accepted,
            progress_pass='a',
        )
        if memory_subject is None:
            # Private chat only: the pinned section renders into the private
            # prompt, so scoped (group / member) content must never enter it.
            await self._aadmit_pinned_candidates(
                lanlan_name, source_accepted, conversation_at=conversation_at,
            )
        return created
