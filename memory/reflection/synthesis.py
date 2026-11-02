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
"""Synthesis methods for the memory manager."""

from __future__ import annotations





from datetime import datetime, timedelta



from memory.evidence import initial_reinforcement_from_importance



from utils.file_utils import (
    robust_json_loads,
)


from utils.token_tracker import set_call_type
from utils.language_utils import (
    detect_prompt_language_with_ascii_fallback,
    get_global_language_full,
)



from memory._reflection.ontology import (
    validate_reflection_ontology as _validate_reflection_ontology,
)

from memory._reflection.schema import (
    reflection_id_from_facts as _reflection_id_from_facts,
)




from ._shared import (
    logger,
    MIN_FACTS_FOR_REFLECTION,
    REFLECTION_COOLDOWN_MINUTES,
)


def _detect_synthesis_prompt_language(text: str) -> str:
    return detect_prompt_language_with_ascii_fallback(
        text,
        ui_language=get_global_language_full(),
    )


class SynthesisMixin:
    @staticmethod
    def _synthesis_locale_text(facts: list[dict]) -> str:
        """Return raw fact text without importance labels or watermarks."""
        return "\n".join(
            str(fact.get('text', ''))
            for fact in facts
            if fact.get('text')
        )

    async def synthesize_scoped_reflections(
        self,
        lanlan_name: str,
        *,
        max_subjects: int = 1,
        subject_locale_resolver=None,
    ) -> list[dict]:
        """Synthesize a bounded number of ready non-legacy subjects.

        Group traffic can create many independent subjects. Processing at most
        one ready subject per maintenance tick keeps the expensive reflection
        path proportional and prevents a busy group fleet from monopolizing the
        summary model.
        """
        if max_subjects <= 0:
            return []
        from memory.facts import safe_importance
        from memory.scopes import subject_from_entry

        facts = await self._fact_store.aload_facts(lanlan_name)
        grouped: dict[tuple[str, str], dict] = {}
        for fact in facts or []:
            if not isinstance(fact, dict):
                # load_facts preserves legacy/hand-edited non-dict rows; one
                # such row must not disable scoped synthesis for the whole
                # character (the maintenance tick retries and re-raises
                # forever). Recall and dedup already skip them.
                continue
            if fact.get('absorbed') or safe_importance(fact, 0) < 5:
                continue
            if not fact.get('id'):
                # Hand-edited rows without a stable id pass the dict guard
                # but synthesize_reflections sorts on f['id'] — one such row
                # would keep the whole character's scoped synthesis failing.
                continue
            subject = subject_from_entry(fact)
            if subject is None:
                continue
            bucket = grouped.setdefault(
                (subject.key, subject.scope),
                {'subject': subject, 'facts': []},
            )
            bucket['facts'].append(fact)

        ready = [
            bucket for bucket in grouped.values()
            if len(bucket['facts']) >= MIN_FACTS_FOR_REFLECTION
        ]
        if not ready:
            return []
        # Round-robin cursor, process-local and reset on restart: begin after the last served subject,
        # advancing regardless of success. Oldest-first lets one dead-letter or failing
        # bucket monopolize the sole slot each tick, starving other subjects;
        # synthesize_reflections backoff saves LLM calls but does not
        # release the slot.
        ready.sort(key=lambda bucket: (
            bucket['subject'].key, bucket['subject'].scope,
        ))
        cursors = getattr(self, '_scoped_synth_cursor', None)
        if cursors is None:
            cursors = {}
            self._scoped_synth_cursor = cursors
        last_served = cursors.get(lanlan_name)
        start = 0
        if last_served is not None:
            for i, bucket in enumerate(ready):
                if (bucket['subject'].key, bucket['subject'].scope) > last_served:
                    start = i
                    break
        created: list[dict] = []
        for offset in range(min(max_subjects, len(ready))):
            bucket = ready[(start + offset) % len(ready)]
            cursors[lanlan_name] = (
                bucket['subject'].key, bucket['subject'].scope,
            )
            selected_locale = None
            if subject_locale_resolver is not None:
                try:
                    selected_locale = await subject_locale_resolver(
                        lanlan_name,
                        bucket['subject'],
                    )
                except Exception as locale_error:  # noqa: BLE001
                    logger.warning(
                        f"[ReflectionSynth] {lanlan_name}/"
                        f"{bucket['subject'].key}: scoped prompt locale "
                        f"解析失败，使用角色 locale: {locale_error}"
                    )
            from utils.language_utils import language_context
            with language_context(selected_locale):
                created.extend(await self.synthesize_reflections(
                    lanlan_name, subject=bucket['subject'],
                ))
        return created

    async def synthesize_reflections(
        self, lanlan_name: str, *, subject=None,
    ) -> list[dict]:
        """Synthesize pending reflections from accumulated unabsorbed facts.

        Called during proactive chat. Returns newly created reflections.

        Idempotency (P1 fix for fatal issue #3):
          1. The reflection id is determined by source_fact_ids
             (_reflection_id_from_facts).
          2. Before the LLM call, check: if the id for this batch of unabsorbed
             facts already exists in reflections.json → skip the LLM and only
             re-run mark_absorbed (idempotent).
          3. save_reflections also dedups by id, guarding against concurrent
             synth double-writes.
          4. Always amark_absorbed at the end, so a restart re-run after "save
             succeeded but mark failed" really flips the facts' absorbed to True.

        Concurrency (C3 refactor + thinking): the LLM call is outside the lock.
        The lock only guards the tens-of-milliseconds critical section of
        "reload → id dedup → append → save". Thus:
          - During the LLM (90-120s with thinking), other reflection writes for
            the same character aren't blocked (aapply_signal /
            aauto_promote_stale's pending→confirmed section / arecord_mentions /
            aget_followup_topics, etc.)
          - Double-write defense: rid is determined by source_fact_ids
            (deterministic); concurrent synths over the same fact batch compute
            the same rid, and the post-LLM in-lock dedup append catches it. The
            losing side returns [] without polluting the caller's view.
        """
        from config.prompts.prompts_memory import get_reflection_prompt

        from memory.scopes import coerce_subject
        memory_subject = coerce_subject(subject)
        forget_epoch = (
            self._subject_forget_epoch(lanlan_name, memory_subject)
            if memory_subject is not None else None
        )
        if memory_subject is None:
            unabsorbed = await self._fact_store.aget_unabsorbed_facts(lanlan_name)
        else:
            unabsorbed = await self._fact_store.aget_unabsorbed_facts(
                lanlan_name, subject=memory_subject,
            )
        if len(unabsorbed) < MIN_FACTS_FOR_REFLECTION:
            return []

        # Backoff keys cover **all** unabsorbed facts, computed before capping.
        # Never use rid, the capped top-N subset hash: poisoned
        # dead-letter top-N batches exclude new low-importance facts,
        # leaving rid unchanged and synthesis permanently skipped, starving the entire pool (Codex P2).
        # Full-set keys change for any new fact, resetting budgets for another attempt.
        all_unabsorbed_ids = sorted(f['id'] for f in unabsorbed)
        backoff_key = _reflection_id_from_facts(all_unabsorbed_ids)

        # Cap unabsorbed facts entering synthesis. Upstream
        # aget_unabsorbed_facts lacks limits and may accumulate hundreds
        # after long absences. Sort importance descending, creation ascending, take top N,
        # preventing huge prompts.
        from config import REFLECTION_SYNTHESIS_FACTS_MAX
        from memory.facts import safe_importance
        if len(unabsorbed) > REFLECTION_SYNTHESIS_FACTS_MAX:
            unabsorbed = sorted(
                unabsorbed,
                key=lambda f: (
                    -safe_importance(f),
                    str(f.get('created_at') or ''),
                ),
            )[:REFLECTION_SYNTHESIS_FACTS_MAX]

        # Sort once, aligning disk fields with _reflection_id_from_facts' sorted input,
        # eliminating sorted-hash/unsorted-storage asymmetry.
        source_fact_ids = sorted(f['id'] for f in unabsorbed)
        rid = _reflection_id_from_facts(source_fact_ids)

        # Idempotent short circuit: reflection already persisted for these facts;
        # skip LLM, complete mark_absorbed only, restart recovery for fatal point 3.
        # Unlocked advisory checks avoid unnecessary inference; final
        # locked dedup repeats against concurrent writes during LLM calls.
        existing_reflections = await self.aload_reflections(lanlan_name)
        existing = next((r for r in existing_reflections if r.get('id') == rid), None)
        if existing is not None:
            await self._fact_store.amark_absorbed(lanlan_name, source_fact_ids)
            logger.info(
                f"[Reflection] {lanlan_name}: 检测到同批 facts 已合成过 reflection "
                f"{rid}，跳过 LLM，补跑 mark_absorbed"
            )
            return []

        # Dead-letter backoff: same unabsorbed set/backoff_key failed synthesis at least
        # MEMORY_LIVENESS_MAX_ATTEMPTS times, so stop identical empty LLM work every 180s.
        # Any fact addition/removal changes keys and naturally resets counts (user audit #2).
        # Time healing: unchanged idle pools allow a probe every MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
        # recovering transient outages while limiting poisoned batches to once per five hours.
        from config import (
            MEMORY_LIVENESS_MAX_ATTEMPTS,
            MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
        )
        from memory.temporal import cooldown_elapsed
        synth_entry = (await self._aload_synth_backoff(lanlan_name)).get(backoff_key, {})
        synth_attempts = synth_entry.get("n", 0)
        if synth_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS and not cooldown_elapsed(
            synth_entry.get("at"), MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
        ):
            logger.debug(
                f"[Reflection] {lanlan_name}: backoff_key {backoff_key} 合成连续失败 "
                f"{synth_attempts} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS}，dead-letter 跳过"
                f"（未到 {MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS}s 自愈窗口）"
            )
            return []

        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        if memory_subject is not None:
            # Scoped synthesis never injects private-chat master names: legacy reflection templates'
            # {MASTER_NAME} asks for insights about the master, rewriting correctly
            # owned member facts as private-owner reflections, mirroring extraction's
            # speaker_label fix. Use subject.key as a mechanical descriptor; fact text
            # already contains readable member labels, preserving model anchoring.
            master_name = memory_subject.key
        else:
            master_name = name_mapping.get('human', '主人')

        prompt_lang = _detect_synthesis_prompt_language(
            self._synthesis_locale_text(unabsorbed),
        )
        facts_text = "\n".join(f"- {f['text']} (importance: {f.get('importance', 5)})" for f in unabsorbed)
        related_block = await self._build_related_context_block(
            lanlan_name,
            unabsorbed,
            subject=memory_subject,
            prompt_lang=prompt_lang,
        )
        reflection_prompt = get_reflection_prompt(prompt_lang)
        prompt = reflection_prompt.replace('{RELATED_CONTEXT_BLOCK}', related_block)
        prompt = prompt.replace('{FACTS}', facts_text)
        prompt = prompt.replace('{LANLAN_NAME}', lanlan_name)
        prompt = prompt.replace('{MASTER_NAME}', master_name)

        try:
            set_call_type("memory_reflection")
            api_config = await self._config_manager.aget_model_api_config('summary')
            # Timeout: see MEMORY_LLM_HARD_TIMEOUT_SECONDS. Upstream forwarding
            # caps at 120s, clients at ≤110s. Thinking-enabled multi-field
            # JSON ontology is longer than classification, justifying 110s. LLM runs unlocked,
            # not blocking same-character reflection writes.
            # No business retries: one call terminal; outer try/except returns [].
            # Explicit thinking benefits creative/structured synthesis, ontology-field
            # consistency, and reflection quality. Budget/fallback: memory.thinking_llm.
            from config import MEMORY_LLM_HARD_TIMEOUT_SECONDS
            from memory.thinking_llm import ainvoke_thinking
            resp, _ = await ainvoke_thinking(  # noqa: LLM_INPUT_BUDGET  # prompt assembled from token-capped memory components (REFLECTION_*/RECALL_* budgets in the prompt builder).
                api_config, prompt,
                timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS,
                call_label=f"{lanlan_name} memory_reflection",
            )
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            result = robust_json_loads(raw)
            if not isinstance(result, dict):
                logger.warning(f"[Reflection] LLM 返回非 dict: {type(result)}")
                await self._abump_synth_backoff(lanlan_name, backoff_key, "non-dict response")
                return []
            reflection_text = result.get('reflection', '')
            if not isinstance(reflection_text, str):
                logger.warning(f"[Reflection] reflection 字段非 str: {type(reflection_text)}")
                await self._abump_synth_backoff(lanlan_name, backoff_key, "reflection field non-str")
                return []
            reflection_text = reflection_text.strip()
            semantic_entity = result.get('entity', 'relationship')
            if semantic_entity not in ('master', 'neko', 'relationship'):
                semantic_entity = 'relationship'
            reflection_entity = (
                memory_subject.kind if memory_subject is not None
                else semantic_entity
            )

            # Ontology fields (RFC §3). Missing fields are tolerated — we
            # only enforce consistency when the LLM does fill them in, so
            # older prompts stay compatible. Validation failure degrades
            # to null (soft fail) rather than dropping the whole reflection.
            rel_type = result.get('relation_type')
            if rel_type is not None and not isinstance(rel_type, str):
                rel_type = None
            temporal = result.get('temporal_scope')
            if temporal is not None and not isinstance(temporal, str):
                temporal = None
            subject = result.get('subject')
            if subject is not None and not isinstance(subject, str):
                subject = None
            # Schema v2: LLM outputs relative event_when_raw (offset+unit),
            # resolved against created_at. Missing whole/start/end sections are possible;
            # normalize_event_when converts malformed values to None.
            from memory.temporal import normalize_event_when as _norm_when
            event_when_raw = _norm_when(result.get('event_when'))

            ok, reason = _validate_reflection_ontology(
                semantic_entity, rel_type, temporal, reflection_text,
            )
            if not ok:
                logger.info(
                    f"[Reflection] ontology 验证不通过({reason})，降级为 null: "
                    f"entity={reflection_entity} rel_type={rel_type}"
                )
                # Strip the entire ontology tuple — keeping `subject` while
                # dropping the rest would leave a half-structured record
                # (no class but still a subject label), which downstream
                # grouping/filtering can't interpret consistently.
                rel_type = None
                temporal = None
                subject = None
        except Exception as e:
            logger.warning(f"[Reflection] 合成失败: {e}")
            await self._abump_synth_backoff(lanlan_name, backoff_key, f"LLM/parse exception: {e}")
            return []

        if not reflection_text:
            await self._abump_synth_backoff(lanlan_name, backoff_key, "empty reflection text")
            return []

        # Create pending reflection, ID determined from source_fact_ids at function entry.
        now = datetime.now()
        now_iso = now.isoformat()

        # Importance-based reinforcement seeds give key-event reflections positive
        # starting scores without many user confirmations before CONFIRMED thresholds.
        # Bypass aapply_signal: synthesis has no event log. Initialize dictionaries
        # directly; synthesis is not event-sourced and initial values are ground truth.
        max_importance = max(
            (safe_importance(f) for f in unabsorbed),
            default=5,
        )
        initial_rein = initial_reinforcement_from_importance(max_importance)

        # Schema v2: event_start/end are ISO anchors shared by facts/reflections.
        # Fallbacks depend on temporal_scope:
        #   - state / episode need end for TTL, defaulting missing end to start
        #   - pattern permits end=None for ongoing patterns without endings
        #   - temporal=None/legacy follows pattern, conservatively avoiding fade-out
        from memory.temporal import (
            compute_event_timestamps as _compute_ts,
        )
        from config import MEMORY_SCHEMA_VERSION_CURRENT as _SCHEMA_V
        _needs_end = temporal in ('state', 'episode')
        event_start_at, event_end_at = _compute_ts(
            event_when_raw,
            now_iso,
            fallback_start=True,
            fallback_end=_needs_end,
        )

        reflection = self._normalize_reflection({
            'id': rid,
            'text': reflection_text,
            'entity': reflection_entity,
            'status': 'pending',  # pending | confirmed | denied | promoted | archived
            'source_fact_ids': source_fact_ids,
            'created_at': now_iso,
            'feedback': None,
            'next_eligible_at': (now + timedelta(minutes=REFLECTION_COOLDOWN_MINUTES)).isoformat(),
            'reinforcement': initial_rein,
            'rein_last_signal_at': now_iso if initial_rein > 0 else None,
            # Ontology (RFC §3). May be None if the model omitted them or
            # validation demoted them. Callers that want to filter or group
            # by relation_type should treat None as "uncategorized".
            'relation_type': rel_type,
            'temporal_scope': temporal,
            'subject': subject,
            # Schema v2 event timing (memory/temporal.py)
            'event_when_raw': event_when_raw,
            'event_start_at': event_start_at,
            'event_end_at': event_end_at,
            'schema_version': _SCHEMA_V,
        })
        from memory.speaker_trust import provenance_of_entries
        reflection.update(provenance_of_entries(unabsorbed))
        if memory_subject is not None:
            reflection.update(memory_subject.as_entry_fields())
            # Simplified group memory: scoped reflections bypass evidence confirmation. Group/member
            # subjects lack user confirmations (surfacing defaults legacy-only) and
            # stage-2 signals (scoped facts persist with signal_processed=True),
            # making pending permanent dead ends. Synthesis aggregates ≥MIN_FACTS_FOR_REFLECTION
            # facts as the quality gate, persisting confirmed directly. Lifecycle uses zero-LLM
            # time-driven aauto_promote_time_driven scoped
            # passes, promoting aged confirmed entries into scoped personas.
            reflection['status'] = 'confirmed'
            reflection['confirmed_at'] = now_iso
            reflection['auto_confirmed'] = True
            # Confirmed rendering (_filter_active_confirmed) requires evidence_score
            # strictly >0, but scoped reflections never receive evidence. Importance≤6
            # batches seed 0.0, hiding reflections from group /scoped_context until
            # time-driven persona promotion. Give minimal positive
            # seeds for immediate visibility; exponential decay never reaches zero.
            if float(reflection.get('reinforcement') or 0.0) <= 0.0:
                reflection['reinforcement'] = 0.1
                reflection['rein_last_signal_at'] = now_iso

        # ── Lock only reload + deduplicated append + save ──
        async with self._get_alock(lanlan_name):
            if (
                memory_subject is not None
                and (
                    self._subject_forget_is_active(
                        lanlan_name, memory_subject,
                    )
                    or self._subject_forget_epoch(
                        lanlan_name, memory_subject,
                    ) != forget_epoch
                )
            ):
                # The source facts were erased while the LLM was in flight.
                # A late result must not recreate an immediately recallable
                # scoped reflection after the forget response.
                logger.info(
                    f"[Reflection] {lanlan_name}: subject forget overlapped "
                    f"synthesis for {memory_subject.key}/"
                    f"{memory_subject.scope}; dropping late result"
                )
                return []
            # Reload against concurrent synthesis during inference, deduplicating IDs/appending current lists.
            reflections = await self.aload_reflections(lanlan_name)
            created = False
            if any(r.get('id') == rid for r in reflections):
                logger.info(
                    f"[Reflection] {lanlan_name}: reflection {rid} 已被并发 synth 写入，跳过重复 append"
                )
            else:
                reflections.append(reflection)
                await self.asave_reflections(lanlan_name, reflections)
                created = True

        # Reflection persisted by us or another writer: clear the full-set synthesis
        # backoff outside locks; _aclear_synth_backoff acquires its own, avoiding
        # reentrant deadlock.
        await self._aclear_synth_backoff(lanlan_name, backoff_key)

        # Always mark_absorbed idempotently, covering crashes after save before this step.
        # fact_store owns locks; reflection locking unnecessary.
        await self._fact_store.amark_absorbed(lanlan_name, source_fact_ids)

        if not created:
            # Concurrent branches persisted another object's version. Returning our memory copy creates
            # unpersisted ghost reflections potentially differing from disk, violating
            # returns-are-reflections-created-by-this-call semantics.
            return []
        # Never logger-log reflection text (privacy); local print fallback only. Scoped
        # group/member text never even enters stdout, often collected as application
        # logs and thus exporting scoped private data.
        logger.info(f"[Reflection] {lanlan_name}: 合成了新反思 {rid} (len={len(reflection_text)} chars)")
        if memory_subject is None:
            print(f"[Reflection] {lanlan_name}: 新反思 {rid}: {reflection_text[:50]}...")
        return [reflection]

    async def _build_related_context_block(
        self,
        lanlan_name: str,
        unabsorbed: list[dict],
        *,
        subject=None,
        prompt_lang: str = "zh",
    ) -> str:
        """When embeddings are available, recall absorbed facts as RELATED_CONTEXT;
        unavailable / empty recall → return an empty string (the
        {RELATED_CONTEXT_BLOCK} render disappears; the prompt is equivalent to
        the pre-change one). Distant facts are reference-only: they don't enter
        source_fact_ids and aren't mark_absorbed — idempotency is guaranteed by
        the rid hash of the unabsorbed set."""
        try:
            from memory.embeddings import (
                get_embedding_service,
                is_cached_embedding_valid,
            )
            # Check both sticky is_disabled and READY is_available:
            # INIT/LOADING has is_disabled=False but is_available=False.
            # Disabled-only checks let rerankers degrade to evidence-only ordering, injecting unrelated historical
            # facts as relevant context (Codex P2 #1392).
            service = get_embedding_service()
            if service.is_disabled() or not service.is_available():
                return ""
            model_id = service.model_id()
            if not model_id:
                return ""
        except Exception:
            return ""

        if not unabsorbed:
            return ""

        unabsorbed_ids = {f['id'] for f in unabsorbed if 'id' in f}
        try:
            # Phase C-2: full pools include archives, recalling old absorbed facts too.
            all_facts = await self._fact_store.aload_facts_full(lanlan_name)
        except Exception as e:
            logger.warning(f"[Reflection] related context load_facts 失败: {e}")
            return ""

        from memory.scopes import filter_entries_for_subjects
        all_facts = filter_entries_for_subjects(
            all_facts,
            [subject] if subject is not None else None,
        )

        # Codex P2 #1392: prefilter valid embeddings before reranking.
        # Facts lack evidence score; rerank=False coarse_rank with stale/missing
        # embeddings after model changes/before backfill falls back to
        # evidence_score=0, near-random ordering,
        # injecting arbitrary background facts. Filter first; empty pools return early,
        # preferring absent RELATED_CONTEXT to irrelevant history.
        absorbed_pool = [
            f for f in all_facts
            if f.get('absorbed')
            and f.get('id') not in unabsorbed_ids
            # Exclude subject_archived_at time-archived rows from synthesis
            # background. Already excluded from recall/rendering, they could otherwise enter revived
            # synthesis as RELATED_CONTEXT and be laundered into active reflections,
            # silently bypassing archival.
            and not f.get('subject_archived_at')
            and not f.get('arbitration_archived_at')
            and is_cached_embedding_valid(f, f.get('text', ''), model_id)
        ]
        if not absorbed_pool:
            return ""

        query_texts = [f.get('text', '') for f in unabsorbed if f.get('text')]
        if not query_texts:
            return ""

        try:
            from memory.recall import MemoryRecallReranker
            from config import (
                REFLECTION_RELATED_PER_QUERY_K,
                REFLECTION_RELATED_TOTAL_CAP,
            )
            reranker = MemoryRecallReranker()
            # Per-query top-K: every unabsorbed fact gets PER_QUERY_K, then union dedup
            # and TOTAL_CAP, never global max-pool budgets. See
            # aretrieve_per_query_topk docs and PR #1401: dispersed topics let max-pool
            # exclude rare themes from anchors; per-query quotas
            # give each fact its own nearest neighbors.
            related = await reranker.aretrieve_per_query_topk(
                absorbed_pool, query_texts,
                per_query_k=REFLECTION_RELATED_PER_QUERY_K,
                total_cap=REFLECTION_RELATED_TOTAL_CAP,
            )
        except Exception as e:
            logger.warning(f"[Reflection] related context 召回失败: {e}")
            return ""

        if not related:
            return ""

        related_text = "\n".join(
            f"- {f.get('text', '')} (importance: {f.get('importance', 5)})"
            for f in related
        )
        from config.prompts.prompts_memory import get_reflection_related_context_note
        return (
            "======以下为相关历史背景======\n"
            f"{related_text}\n"
            f"（{get_reflection_related_context_note(prompt_lang)}）\n"
            "======以上为相关历史背景======\n\n"
        )

    async def reflect(self, lanlan_name: str, *, subject=None) -> dict | None:
        """Alias for synthesize_reflections. Returns first reflection or None."""
        results = await self.synthesize_reflections(lanlan_name, subject=subject)
        return results[0] if results else None
