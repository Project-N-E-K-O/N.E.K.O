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
"""Refinement methods for the memory manager."""

from __future__ import annotations








from datetime import datetime



from memory.facts import safe_int_field
from memory.introspection import is_permanently_suppressed







from ._shared import (
    logger,
)

class RefinementMixin:
    @staticmethod
    def _refine_persona_id(text: str) -> str:
        """Stable salted id for split/merge produced persona entries.

        Mirrors `ReflectionEngine._refine_reflection_id` — salted so
        split-into-N pieces with shared underlying source get distinct
        ids. Without this, `_normalize_entry(text)` returns `id=''`,
        making `section_by_id` collapse all newly produced entries
        and cluster_hash skip / archive paths break (CodeRabbit
        Major #1392).
        """
        import hashlib
        salt = datetime.now().isoformat()
        return f"per_{hashlib.sha1(f'{text}|{salt}'.encode('utf-8')).hexdigest()[:16]}"

    def _apply_refine_modify(
        self, src: dict, new_text: str, reason: str, now_iso: str,
    ) -> None:
        """Rewrite ``src`` in place; the old text goes to ``version_history``.

        Shared by refine ``modify`` and introspection ``rewrite``.
        """
        from config import PERSONA_VERSION_HISTORY_MAX as _VH_MAX
        old_text = src.get('text', '')
        prior_history = src.get('version_history') or []
        src['text'] = new_text
        src['version_history'] = (
            list(prior_history) + [{
                'text': old_text,
                'replaced_at': now_iso,
                'reason': reason,
                'source_fact_id': None,
            }]
        )[-_VH_MAX:]
        self._invalidate_token_count_cache(src)
        self._invalidate_embedding_cache(src)

    def _build_refine_merge_entry(
        self, srcs: list[dict], text: str, now_iso: str,
        *, reason: str = 'refine_merge',
    ) -> dict:
        """One new entry replacing ``srcs``; their texts go to its
        ``version_history`` and their evidence is inherited.

        Shared by refine ``merge`` and introspection ``merge``.
        """
        from config import PERSONA_VERSION_HISTORY_MAX as _VH_MAX
        from memory.evidence import merged_evidence_state

        merged = self._normalize_entry(text)
        # Unique ID (as split), or section_by_id collapses entries.
        merged['id'] = self._refine_persona_id(text)
        merged['version_history'] = [
            {
                'text': src.get('text', ''),
                'replaced_at': now_iso,
                'reason': reason,
                'source_fact_id': None,
            }
            for src in srcs
        ][-_VH_MAX:]
        merged.update(merged_evidence_state(srcs))
        inherited_source = None
        inherited_source_id = None
        for src in srcs:
            if inherited_source is None:
                inherited_source = src.get('source')
                inherited_source_id = src.get('source_id')
        merged['source'] = inherited_source or 'unknown'
        merged['source_id'] = inherited_source_id
        return merged

    async def aapply_introspection_actions(
        self, name: str, actions: list[dict],
    ) -> dict:
        """Apply introspection actions already checked against the number map.

        Each action names its targets as ``(entry_id, text_at_render)``
        inside ``action['entity']``. Under the lock every target must still
        exist with that exact text, be neither protected nor already
        suppressed, and not be claimed by an earlier action of this batch;
        otherwise the whole action is skipped as stale — the model judged
        text that is no longer there.

        ``suppress`` is event-sourced (``EVT_PERSONA_SUPPRESSED``, replayed
        by the reconciler); ``rewrite`` / ``merge`` reuse the refine
        helpers and are saved together at the end. A merge deletes nothing:
        its sources stay, permanently suppressed with ``merged_into``.

        Returns ``{'applied': n, 'stale': m}``.
        """
        from memory.event_log import EVT_PERSONA_SUPPRESSED
        from memory.introspection import (
            ACTION_MERGE,
            ACTION_REWRITE,
            ACTION_SUPPRESS,
            PERMANENT_SUPPRESSION_KEY,
            build_permanent_suppression,
        )

        applied = 0
        stale = 0
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            now_iso = datetime.now().isoformat()
            consumed: set[tuple[str, str]] = set()
            dirty = False
            for act in actions:
                entity = act.get('entity')
                section = persona.get(entity)
                facts = section.get('facts') if isinstance(section, dict) else None
                srcs: list[dict] = []
                for entry_id, text in act.get('targets') or ():
                    src = (
                        self._find_entry_in_section(facts, entry_id)
                        if isinstance(facts, list) else None
                    )
                    if (
                        src is None
                        or (entity, entry_id) in consumed
                        or src.get('protected')
                        or is_permanently_suppressed(src)
                        or src.get('text') != text
                    ):
                        srcs = []
                        break
                    srcs.append(src)
                if not srcs:
                    stale += 1
                    continue
                verb = act.get('action')
                reason = f"introspection: {act.get('reason') or verb}"
                if verb == ACTION_SUPPRESS:
                    src = srcs[0]
                    marker = build_permanent_suppression(
                        act.get('reason') or verb, now_iso, issue=act.get('issue'),
                    )
                    if self._event_log is None:
                        src[PERMANENT_SUPPRESSION_KEY] = marker
                        dirty = True
                    else:
                        def _sync_load(_n, _persona=persona):
                            # Lock held, cache current: same pattern as aapply_signal.
                            return _persona

                        def _sync_mutate(_view, _src=src, _marker=marker):
                            _src[PERMANENT_SUPPRESSION_KEY] = _marker

                        await self._event_log.arecord_and_save(
                            name, EVT_PERSONA_SUPPRESSED, {
                                'entity_key': entity,
                                'entry_id': src.get('id'),
                                PERMANENT_SUPPRESSION_KEY: marker,
                            },
                            sync_load_view=_sync_load,
                            sync_mutate_view=_sync_mutate,
                            sync_save_view=self._sync_save_persona_view,
                        )
                elif verb == ACTION_REWRITE:
                    self._apply_refine_modify(srcs[0], act['text'], reason, now_iso)
                    dirty = True
                elif verb == ACTION_MERGE:
                    if any(src.get('suppress') for src in srcs):
                        # A source in mention cooldown would leave the
                        # do-not-mention section early as a fresh entry.
                        stale += 1
                        continue
                    merged = self._build_refine_merge_entry(
                        srcs, act['text'], now_iso, reason=reason,
                    )
                    # Sources stay on disk, retired, rather than deleted: the
                    # merged entry's version_history keeps only the last few
                    # texts, so a large merge would otherwise lose the rest.
                    # Written with the merged entry in one save, not as
                    # separate suppression events, so the two cannot diverge.
                    for src in srcs:
                        src[PERMANENT_SUPPRESSION_KEY] = build_permanent_suppression(
                            act.get('reason') or verb, now_iso,
                            merged_into=merged['id'],
                        )
                    facts.append(merged)
                    dirty = True
                else:
                    stale += 1
                    continue
                consumed.update((entity, src.get('id')) for src in srcs)
                applied += 1
            if dirty:
                await self.asave_persona(name, persona)
        return {'applied': applied, 'stale': stale}

    async def apply_refine_actions(
        self,
        name: str,
        entity: str,
        cluster: list[dict],
        actions: list[dict],
        cluster_hash: str,
    ) -> int:
        """Apply the four MemoryRefineEngine action types to the persona.

        Inside the lock: reload → validate → apply → stamp survivors → save.
        Clusters contain only persona entries (the refine engine never mixes
        facts into the persona pool); protected entries were already filtered at
        gather time; an extra defensive `protected` check here blocks
        split/discard/modify actions mistakenly aimed at protected entries
        (merge likewise excludes protected as a source).

        Newly produced entries are deliberately not stamped — they get
        re-examined when the next cron forms new clusters, naturally triggering
        hash invalidation.

        Returns: number of successfully applied actions."""
        from memory.refine import VALID_REFINE_ACTIONS

        async with self._get_alock(name):
            # asyncio.Lock is non-reentrant; under lock use _aensure_persona_locked,
            # not aensure_persona, which reacquires the same lock and deadlocks.
            persona = await self._aensure_persona_locked(name)
            section = self._get_section_facts(persona, entity)
            section_by_id = {
                e.get('id'): e for e in section
                if isinstance(e, dict) and e.get('id')
            }
            cluster_ids = {
                e.get('id') for e in cluster
                if isinstance(e, dict) and e.get('id')
            }

            consumed: set[str] = set()  # Source IDs for split / merge / discard.
            produced: list[dict] = []
            applied = 0
            now_iso = datetime.now().isoformat()

            for act_obj in actions:
                if not isinstance(act_obj, dict):
                    continue
                act = act_obj.get('action')
                if act not in VALID_REFINE_ACTIONS:
                    logger.warning(f"[Refine apply] persona: 非法 action {act!r}")
                    continue

                if act == 'split':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_ids or src_id in consumed:
                        continue
                    src = section_by_id.get(src_id)
                    # A source suppressed since the cluster was gathered must
                    # not come back as fresh, unflagged pieces.
                    if not src or src.get('protected') or is_permanently_suppressed(src):
                        continue
                    produce = act_obj.get('produce')
                    if not isinstance(produce, list) or len(produce) < 2:
                        continue
                    # Filter valid outputs first, then distribute evidence by actual split_count.
                    valid_produce = [
                        p for p in produce
                        if isinstance(p, dict) and str(p.get('text', '')).strip()
                    ]
                    if len(valid_produce) < 2:
                        continue
                    split_n = len(valid_produce)
                    # Inherit evidence (Codex P1 #1392): divide distributable counters
                    # equally 1/N, retaining cumulative evidence rather than silently zeroing scores/
                    # decay signals on split; inherit days / timestamps / provenance directly from source.
                    src_rein = float(src.get('reinforcement', 0) or 0)
                    src_disp = float(src.get('disputation', 0) or 0)
                    src_user_cnt = int(src.get('user_fact_reinforce_count', 0) or 0)
                    new_entries = []
                    for p in valid_produce:
                        text = str(p.get('text', '')).strip()
                        ne = self._normalize_entry(text)
                        ne['id'] = self._refine_persona_id(text)
                        ne['source'] = src.get('source', 'unknown')
                        ne['source_id'] = src.get('source_id')
                        ne['reinforcement'] = src_rein / split_n
                        ne['disputation'] = src_disp / split_n
                        ne['user_fact_reinforce_count'] = src_user_cnt // split_n
                        ne['rein_last_signal_at'] = src.get('rein_last_signal_at')
                        ne['disp_last_signal_at'] = src.get('disp_last_signal_at')
                        ne['sub_zero_days'] = int(src.get('sub_zero_days', 0) or 0)
                        ne['sub_zero_last_increment_date'] = src.get('sub_zero_last_increment_date')
                        new_entries.append(ne)
                    produced.extend(new_entries)
                    consumed.add(src_id)
                    applied += 1

                elif act == 'merge':
                    src_ids_raw = act_obj.get('source_ids') or []
                    if not isinstance(src_ids_raw, list):
                        continue
                    if any(
                        is_permanently_suppressed(section_by_id.get(sid))
                        for sid in src_ids_raw
                    ):
                        # The merged text was written from every source,
                        # retired one included: dropping that source and
                        # keeping the text would bring its content back.
                        continue
                    valid_ids = [
                        sid for sid in src_ids_raw
                        if sid in cluster_ids
                        and sid in section_by_id
                        and sid not in consumed
                        and not section_by_id[sid].get('protected')
                    ]
                    if len(valid_ids) < 2:
                        continue
                    produce = act_obj.get('produce')
                    if not isinstance(produce, dict):
                        continue
                    text = str(produce.get('text', '')).strip()
                    if not text:
                        continue
                    produced.append(self._build_refine_merge_entry(
                        [section_by_id[sid] for sid in valid_ids], text, now_iso,
                    ))
                    consumed.update(valid_ids)
                    applied += 1

                elif act == 'modify':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_ids or src_id in consumed:
                        continue
                    src = section_by_id.get(src_id)
                    if not src or src.get('protected') or is_permanently_suppressed(src):
                        continue
                    produce = act_obj.get('produce')
                    if not isinstance(produce, dict):
                        continue
                    new_text = str(produce.get('text', '')).strip()
                    if not new_text:
                        continue
                    reason = str(act_obj.get('reason') or 'refine_modify')
                    self._apply_refine_modify(src, new_text, reason, now_iso)
                    applied += 1

                elif act == 'discard':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_ids or src_id in consumed:
                        continue
                    src = section_by_id.get(src_id)
                    if not src or src.get('protected') or is_permanently_suppressed(src):
                        continue
                    consumed.add(src_id)
                    applied += 1

            # Stamp decisions (combined Codex P1 + P2 #1392 semantics):
            # - applied > 0: changed, always stamp + save.
            # - applied == 0, empty actions: model explicitly chose no-op; stamp
            #   to preserve cluster_hash skipping (Codex P1).
            # - applied == 0, nonempty actions: every action rejected =
            #   semantically invalid LLM output (unknown action / missing field, etc.);
            #   do not stamp; retry next round (Codex P2, preventing garbage from silently
            #   delaying needed refinement by 30 days).
            if applied == 0 and actions:
                return 0

            new_section = [
                e for e in section
                if not (isinstance(e, dict) and e.get('id') in consumed)
            ]
            new_section.extend(produced)

            stamped = 0
            for e in new_section:
                if not isinstance(e, dict):
                    continue
                eid = e.get('id')
                if eid in cluster_ids and eid not in consumed:
                    e['last_refine_cluster_hash'] = cluster_hash
                    e['last_refine_at'] = now_iso
                    # Successful stamp clears Site 4 liveness counters: discard attempts accumulated
                    # due to toxic cluster neighbors, allowing this entry to reenter
                    # new clusters for LLM judgment later.
                    if e.get('refine_attempts'):
                        e['refine_attempts'] = 0
                    stamped += 1

            if applied == 0 and stamped == 0:
                # Cluster members all gone (rare concurrent deletion, etc.): nothing to stamp
                # or persist; return directly.
                return 0

            section[:] = new_section
            await self.asave_persona(name, persona)
            logger.info(
                f"[Persona] {name} entity={entity}: refine 应用 {applied} action "
                f"(cluster_hash={cluster_hash}, stamped={stamped}, "
                f"+{len(produced)} produced, -{len(consumed)} consumed)"
            )
        return applied

    async def _abump_refine_attempts(
        self, name: str, cluster: list[dict], cluster_hash: str,
    ) -> None:
        """Site 4 liveness fallback: bump ``refine_attempts`` on non-fact members
        when a refine-cluster LLM call fails. Entries reaching
        ``MEMORY_LIVENESS_MAX_ATTEMPTS`` are filtered out of the next
        ``_run_persona_refine_for_character`` candidate gather, so a poison
        cluster stops hogging the starvation-first ordering slots with futile
        LLM calls.

        Recovery: a successful refine (apply_refine_actions reaching the stamp
        branch) resets ``refine_attempts`` to 0; or manually edit persona.json.

        Why not in-memory: the refine stamp `last_refine_at` is itself
        persisted, so the counter must be too — otherwise a restart zeroes it
        and defeats the dead-letter (see the "persisted-or-not dual" section of
        issue #1409).
        """
        from config import MEMORY_LIVENESS_MAX_ATTEMPTS
        from memory.refine import REFINE_ENTITY_KEY, REFINE_TYPE_KEY

        # Collect entry IDs needing bumps by entity (exclude facts: read-only information
        # sources never modified by refine, so never accrue refine_attempts).
        member_ids_by_entity: dict[str, set[str]] = {}
        for e in cluster:
            if not isinstance(e, dict):
                continue
            if e.get(REFINE_TYPE_KEY) == 'fact':
                continue
            ent = e.get(REFINE_ENTITY_KEY)
            eid = e.get('id')
            if not ent or not eid:
                continue
            member_ids_by_entity.setdefault(ent, set()).add(eid)
        if not member_ids_by_entity:
            return

        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            modified = False
            for entity, ids in member_ids_by_entity.items():
                section = self._get_section_facts(persona, entity)
                for e in section:
                    if not isinstance(e, dict) or e.get('id') not in ids:
                        continue
                    new_attempts = safe_int_field(e, 'refine_attempts') + 1
                    e['refine_attempts'] = new_attempts
                    # Stamp failure time for dead-letter cooldown_elapsed self-healing:
                    # transient correction-model outage reaching MAX can probe candidates again
                    # after five-hour cooldown, avoiding permanent freeze after recovery.
                    e['last_refine_attempt_at'] = datetime.now().isoformat()
                    modified = True
                    if new_attempts == MEMORY_LIVENESS_MAX_ATTEMPTS:
                        logger.warning(
                            f"[PersonaRefine] {name}: persona entry "
                            f"id={e.get('id')} entity={entity} "
                            f"refine_attempts={new_attempts} ≥ "
                            f"{MEMORY_LIVENESS_MAX_ATTEMPTS}（dead-letter，"
                            f"cluster_hash={cluster_hash}）。下次 refine "
                            f"gather 不再选入，避免毒 cluster 占用名额。"
                        )
            if modified:
                await self.asave_persona(name, persona)
