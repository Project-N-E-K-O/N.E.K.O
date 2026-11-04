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












from memory._reflection.schema import (
    refine_reflection_id,
)

from memory._reflection.refine import (
    build_merge_reflection,
    build_split_reflection,
)
from memory.evidence import merged_evidence_state
from memory.introspection import is_permanently_suppressed



from ._shared import (
    logger,
    REFLECTION_TERMINAL_STATUSES,
)

class RefinementMixin:
    @staticmethod
    def _refine_reflection_id(text: str) -> str:
        """Salted hash so split-into-N pieces get distinct ids even when
        the underlying fact set is shared."""
        return refine_reflection_id(text, now=datetime.now())

    def _build_split_reflection(
        self,
        src: dict,
        produce_item: dict,
        entity: str,
        now: datetime,
        *,
        split_count: int,
    ) -> dict:
        """Build a reflection produced by split; inherits src's source_fact_ids,
        ontology and event_when. reinforcement is divided evenly by the actual
        split_count (Codex P2 #1392: the old hardcoded /2 underestimated each
        item's strength when N>2). split_count is at least 2 (the caller
        already skips when len(produce)<2)."""
        return build_split_reflection(
            src,
            produce_item,
            entity,
            now,
            split_count=split_count,
            id_factory=self._refine_reflection_id,
            normalizer=self._normalize_reflection,
        )

    def _build_merge_reflection(
        self,
        srcs: list[dict],
        fact_source_ids: list[str],
        produce: dict,
        entity: str,
        now: datetime,
    ) -> dict:
        """Build a reflection produced by merge; merges source_fact_ids (including
        absorbed_from_fact_ids), status takes the highest (promoted > confirmed >
        pending), reinforcement takes the max."""
        return build_merge_reflection(
            srcs,
            fact_source_ids,
            produce,
            entity,
            now,
            id_factory=self._refine_reflection_id,
            normalizer=self._normalize_reflection,
        )

    @staticmethod
    def _apply_refine_modify(
        src: dict, new_text: str, reason: str, now_iso: str,
        *, absorbed_fact_ids: list[str] | tuple = (),
    ) -> None:
        """Rewrite ``src`` in place; the old text goes to ``modification_history``.

        Shared by refine ``modify`` and introspection ``rewrite`` — the
        reflection twin of ``PersonaManager._apply_refine_modify``.
        """
        absorbed = list(absorbed_fact_ids)
        old_text = src.get('text', '')
        src['text'] = new_text
        existing_sources = list(src.get('source_fact_ids') or [])
        src['source_fact_ids'] = sorted(set(existing_sources + absorbed))
        if absorbed:
            # Facts of unknown age joined: the line is no longer known to
            # rest only on facts newer than a retirement (see the render).
            src['source_facts_oldest_at'] = None
        mod_history = list(src.get('modification_history') or [])
        mod_history.append({
            'old_text': old_text,
            'modified_at': now_iso,
            'reason': reason,
            'absorbed_fact_ids': absorbed,
        })
        src['modification_history'] = mod_history
        # Changed text invalidates embeddings; re-embed on the next worker scan
        src['embedding'] = None
        src['embedding_text_sha256'] = None
        src['embedding_model_id'] = None

    async def aapply_introspection_actions(
        self, name: str, actions: list[dict],
    ) -> dict:
        """Apply introspection actions already checked against the number map.

        Twin of ``PersonaManager.aapply_introspection_actions``: every target
        ``(reflection_id, text_at_render)`` must still be active with that
        exact text, not already suppressed and not claimed by an earlier
        action of this batch, or the whole action is skipped as stale.
        ``rewrite`` / ``merge`` reuse the refine helpers; a merge also keeps
        each source text in the new reflection's ``modification_history``
        and deletes nothing: its sources stay, permanently suppressed with
        ``merged_into``.
        Reflections have no event type for suppression, so all three write
        straight to the view, as refine does.

        Returns ``{'applied': n, 'stale': m}``.
        """
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
            reflections = await self.aload_reflections(name)
            refl_by_id = {
                r.get('id'): r for r in reflections
                if isinstance(r, dict) and r.get('id')
            }
            now = datetime.now()
            now_iso = now.isoformat()
            consumed: set[str] = set()
            produced: list[dict] = []
            for act in actions:
                srcs: list[dict] = []
                for rid, text in act.get('targets') or ():
                    src = refl_by_id.get(rid)
                    if (
                        src is None
                        or rid in consumed
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
                    srcs[0][PERMANENT_SUPPRESSION_KEY] = build_permanent_suppression(
                        act.get('reason') or verb, now_iso, issue=act.get('issue'),
                    )
                elif verb == ACTION_REWRITE:
                    self._apply_refine_modify(srcs[0], act['text'], reason, now_iso)
                elif verb == ACTION_MERGE:
                    if any(src.get('suppress') for src in srcs):
                        # Same rule as persona: a source in mention cooldown
                        # (it can enter one after the render) would come out
                        # of the merge as a fresh, uncooled reflection.
                        stale += 1
                        continue
                    merged = self._build_merge_reflection(
                        srcs, [], {'text': act['text']},
                        srcs[0].get('entity') or 'master', now,
                    )
                    # The refine builder keeps only the top reinforcement;
                    # dropping a source's disputation or decay clock here
                    # could lift a disputed line to promotion.
                    merged.update(merged_evidence_state(srcs))
                    merged['modification_history'] = [
                        {
                            'old_text': src.get('text', ''),
                            'modified_at': now_iso,
                            'reason': reason,
                            'absorbed_fact_ids': [],
                            'merged_from_id': src.get('id'),
                        }
                        for src in srcs
                    ]
                    produced.append(merged)
                    # Sources stay, retired, as on the persona side.
                    for src in srcs:
                        src[PERMANENT_SUPPRESSION_KEY] = build_permanent_suppression(
                            act.get('reason') or verb, now_iso,
                            merged_into=merged['id'],
                        )
                else:
                    stale += 1
                    continue
                consumed.update(src.get('id') for src in srcs)
                applied += 1
            if applied:
                await self.asave_reflections(name, reflections + produced)
        return {'applied': applied, 'stale': stale}

    async def apply_refine_actions(
        self,
        name: str,
        entity: str,
        cluster: list[dict],
        actions: list[dict],
        cluster_hash: str,
    ) -> int:
        """Apply the four MemoryRefineEngine action types to reflections.

        Fact entries within a cluster are read-only information sources — any
        split / discard / modify aimed at a fact id is rejected outright; facts
        may only serve as absorbed_from_fact_ids of merge / modify. Enforced in
        code, not left to prompt goodwill.

        Inside the lock: reload → validate → apply → stamp → save.
        Newly produced reflections aren't stamped — the next cron re-examines them.

        Returns: number of successfully applied actions."""
        from memory.refine import REFINE_TYPE_KEY, VALID_REFINE_ACTIONS

        cluster_refl_ids = {
            e.get('id') for e in cluster
            if isinstance(e, dict)
            and e.get(REFINE_TYPE_KEY) == 'reflection'
            and e.get('id')
        }
        cluster_fact_ids = {
            e.get('id') for e in cluster
            if isinstance(e, dict)
            and e.get(REFINE_TYPE_KEY) == 'fact'
            and e.get('id')
        }

        async with self._get_alock(name):
            reflections = await self.aload_reflections(name)
            refl_by_id = {
                r.get('id'): r for r in reflections
                if isinstance(r, dict) and r.get('id')
            }

            consumed: set[str] = set()
            produced: list[dict] = []
            applied = 0
            now = datetime.now()
            now_iso = now.isoformat()

            for act_obj in actions:
                if not isinstance(act_obj, dict):
                    continue
                act = act_obj.get('action')
                if act not in VALID_REFINE_ACTIONS:
                    logger.warning(f"[Refine apply] reflection: 非法 action {act!r}")
                    continue

                # Facts cannot be source_id for split / discard / modify; enforce this in code
                if act in ('split', 'discard', 'modify'):
                    sid = act_obj.get('source_id')
                    if sid in cluster_fact_ids:
                        logger.warning(
                            f"[Refine apply] reflection: 拒绝对 fact id={sid} 做 {act}"
                        )
                        continue

                if act == 'split':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_refl_ids or src_id in consumed:
                        continue
                    src = refl_by_id.get(src_id)
                    # A source suppressed since the cluster was gathered must
                    # not come back as fresh, unflagged pieces.
                    if not src or is_permanently_suppressed(src):
                        continue
                    produce = act_obj.get('produce')
                    if not isinstance(produce, list) or len(produce) < 2:
                        continue
                    # Filter valid produced items first, then use their actual count as split_count
                    valid_produce = [
                        p for p in produce
                        if isinstance(p, dict) and str(p.get('text', '')).strip()
                    ]
                    if len(valid_produce) < 2:
                        continue
                    new_entries = [
                        self._build_split_reflection(
                            src, p, entity, now, split_count=len(valid_produce),
                        )
                        for p in valid_produce
                    ]
                    produced.extend(new_entries)
                    consumed.add(src_id)
                    applied += 1

                elif act == 'merge':
                    src_ids_raw = act_obj.get('source_ids') or []
                    if not isinstance(src_ids_raw, list):
                        continue
                    # Every source_id must identify a reflection; a fact ID rejects the entire action
                    if any(sid in cluster_fact_ids for sid in src_ids_raw):
                        logger.warning(
                            "[Refine apply] reflection: merge source_ids 含 fact，拒绝"
                        )
                        continue
                    if any(
                        is_permanently_suppressed(refl_by_id.get(sid))
                        for sid in src_ids_raw
                    ):
                        # Same rule as persona: the merged text carries the
                        # retired source's content, so the whole merge goes.
                        continue
                    valid_refl_ids = [
                        sid for sid in src_ids_raw
                        if sid in cluster_refl_ids
                        and sid in refl_by_id
                        and sid not in consumed
                    ]
                    if len(valid_refl_ids) < 2:
                        continue
                    absorbed_fact_ids = act_obj.get('absorbed_from_fact_ids') or []
                    if not isinstance(absorbed_fact_ids, list):
                        absorbed_fact_ids = []
                    valid_fact_sources = [
                        fid for fid in absorbed_fact_ids if fid in cluster_fact_ids
                    ]
                    produce = act_obj.get('produce')
                    if not isinstance(produce, dict):
                        continue
                    text = str(produce.get('text', '')).strip()
                    if not text:
                        continue
                    new_refl = self._build_merge_reflection(
                        [refl_by_id[sid] for sid in valid_refl_ids],
                        valid_fact_sources, produce, entity, now,
                    )
                    produced.append(new_refl)
                    consumed.update(valid_refl_ids)
                    applied += 1

                elif act == 'modify':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_refl_ids or src_id in consumed:
                        continue
                    src = refl_by_id.get(src_id)
                    if not src or is_permanently_suppressed(src):
                        continue
                    produce = act_obj.get('produce')
                    if not isinstance(produce, dict):
                        continue
                    new_text = str(produce.get('text', '')).strip()
                    if not new_text:
                        continue
                    absorbed_fact_ids = act_obj.get('absorbed_from_fact_ids') or []
                    if not isinstance(absorbed_fact_ids, list):
                        absorbed_fact_ids = []
                    valid_fact_sources = [
                        fid for fid in absorbed_fact_ids if fid in cluster_fact_ids
                    ]
                    reason = str(act_obj.get('reason') or 'refine_modify')
                    self._apply_refine_modify(
                        src, new_text, reason, now_iso,
                        absorbed_fact_ids=valid_fact_sources,
                    )
                    applied += 1

                elif act == 'discard':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_refl_ids or src_id in consumed:
                        continue
                    if is_permanently_suppressed(refl_by_id.get(src_id)):
                        continue
                    consumed.add(src_id)
                    applied += 1

            # Stamp decisions (combined Codex P1 + P2 #1392 semantics):
            # - applied > 0: changes occurred; always stamp + save
            # - applied == 0 with empty actions: explicit LLM no-op; stamp to prevent
            #   cluster_hash skip failure (Codex P1)
            # - applied == 0 with nonempty actions: every action rejected as LLM
            #   semantic garbage; do not stamp, allowing retry next round (Codex P2)
            if applied == 0 and actions:
                return 0

            new_reflections = [
                r for r in reflections
                if not (isinstance(r, dict) and r.get('id') in consumed)
            ]
            new_reflections.extend(produced)

            stamped = 0
            for r in new_reflections:
                if not isinstance(r, dict):
                    continue
                rid = r.get('id')
                if rid in cluster_refl_ids and rid not in consumed:
                    r['last_refine_cluster_hash'] = cluster_hash
                    r['last_refine_at'] = now_iso
                    # Successful stamping clears Site 4 liveness counters (symmetric with
                    # PersonaManager.apply_refine_actions）
                    if r.get('refine_attempts'):
                        r['refine_attempts'] = 0
                    stamped += 1

            if applied == 0 and stamped == 0:
                return 0

            await self.asave_reflections(name, new_reflections)
            logger.info(
                f"[Reflection] {name} entity={entity}: refine 应用 {applied} action "
                f"(cluster_hash={cluster_hash}, stamped={stamped}, "
                f"+{len(produced)} produced, -{len(consumed)} consumed)"
            )
        return applied

    async def _abump_refine_attempts(
        self, name: str, cluster: list[dict], cluster_hash: str,
    ) -> None:
        """Site 4 liveness fallback: bump ``refine_attempts`` on non-fact reflection
        members when a refine-cluster LLM call fails. Twin of
        ``PersonaManager._abump_refine_attempts`` — same cure, different storage
        (reflections.json vs persona.json).

        Recovery: a successful refine (apply_refine_actions reaching the stamp
        branch) resets ``refine_attempts`` to 0; or manually edit reflections.json.
        """
        from config import MEMORY_LIVENESS_MAX_ATTEMPTS
        from memory.facts import safe_int_field
        from memory.refine import REFINE_TYPE_KEY

        # Facts do not count attempts: they are read-only information sources that refine never changes;
        # bump only reflection members. Reflections are not stored in separate entity sections, but together in one
        # list distinguished by entity fields, so collecting set[rid] suffices.
        member_rids: set[str] = set()
        for e in cluster:
            if not isinstance(e, dict):
                continue
            if e.get(REFINE_TYPE_KEY) == 'fact':
                continue
            rid = e.get('id')
            if rid:
                member_rids.add(rid)
        if not member_rids:
            return

        async with self._get_alock(name):
            reflections = await self._aload_reflections_full(name)
            modified = False
            for r in reflections:
                if not isinstance(r, dict) or r.get('id') not in member_rids:
                    continue
                new_attempts = safe_int_field(r, 'refine_attempts') + 1
                r['refine_attempts'] = new_attempts
                # Stamp failure time for dead-letter time-based recovery (cooldown_elapsed), symmetric with
                # persona: after a one-off outage reaches MAX, probe again after a 5h cooldown.
                r['last_refine_attempt_at'] = datetime.now().isoformat()
                modified = True
                if new_attempts == MEMORY_LIVENESS_MAX_ATTEMPTS:
                    logger.warning(
                        f"[ReflectionRefine] {name}: reflection id={r.get('id')} "
                        f"refine_attempts={new_attempts} ≥ "
                        f"{MEMORY_LIVENESS_MAX_ATTEMPTS}（dead-letter，"
                        f"cluster_hash={cluster_hash}）。下次 refine gather "
                        f"不再选入，避免毒 cluster 占用名额。"
                    )
            if modified:
                # CodeRabbit: pass active-only entries to ``asave_reflections``.
                # ``_prepare_save_reflections`` treats input as the desired surviving active set;
                # all_on_disk IDs present in input skip archival checks. Passing
                # the full list (including terminal entries) leaves ``to_archive`` permanently empty, so old promoted/
                # denied entries never archive. Follow the same convention as arecord_mentions / aupdate_suppressions
                # to let archival proceed normally.
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(name, active)
