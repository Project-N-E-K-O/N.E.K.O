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

                # fact 不可作 split / discard / modify 的 source_id —— 代码层硬拦
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
                    if not src:
                        continue
                    produce = act_obj.get('produce')
                    if not isinstance(produce, list) or len(produce) < 2:
                        continue
                    # 先过滤出有效 produce items，再以实际数量作 split_count
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
                    # source_ids 必须全是 reflection；含 fact id → 拒绝整个 action
                    if any(sid in cluster_fact_ids for sid in src_ids_raw):
                        logger.warning(
                            "[Refine apply] reflection: merge source_ids 含 fact，拒绝"
                        )
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
                    if not src:
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
                    old_text = src.get('text', '')
                    src['text'] = new_text
                    existing_sources = list(src.get('source_fact_ids') or [])
                    src['source_fact_ids'] = sorted(
                        set(existing_sources + valid_fact_sources)
                    )
                    mod_history = list(src.get('modification_history') or [])
                    mod_history.append({
                        'old_text': old_text,
                        'modified_at': now_iso,
                        'reason': reason,
                        'absorbed_fact_ids': valid_fact_sources,
                    })
                    src['modification_history'] = mod_history
                    # 文本变 → embedding 失效（下次 worker 扫描重 embed）
                    src['embedding'] = None
                    src['embedding_text_sha256'] = None
                    src['embedding_model_id'] = None
                    applied += 1

                elif act == 'discard':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_refl_ids or src_id in consumed:
                        continue
                    consumed.add(src_id)
                    applied += 1

            # Stamp 决策（Codex P1 + P2 #1392 合并语义）：
            # - applied > 0：有变化，必 stamp + save
            # - applied == 0 + actions 为空：LLM 明确 no-op，stamp 防
            #   cluster_hash skip 失效（Codex P1）
            # - applied == 0 + actions 非空：所有 action 全 reject = LLM
            #   语义垃圾，不 stamp 等下轮重试（Codex P2）
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
                    # 成功 stamp → 清 Site 4 liveness 计数器（对偶
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

        # fact 不计 attempts（fact 是 readonly 信息源，refine 永远不改它）；
        # 只 bump reflection 成员。reflection 不分 entity section 单独存（全
        # 在一个 list 里按 entity 字段区分），所以收集 set[rid] 即可。
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
                # 戳失败时刻供 dead-letter 时间自愈（cooldown_elapsed），对偶
                # persona 侧——一次性宕机顶到 MAX 后过 5h 冷却重新 probe。
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
                # CodeRabbit: 传 active-only 给 ``asave_reflections``。
                # ``_prepare_save_reflections`` 把 input 当 "想要存活的 active 集",
                # all_on_disk 里 id 在 input set 中的会跳过归档判断。如果传
                # full list（含 terminal），``to_archive`` 永远是空，老 promoted/
                # denied 永远 archive 不掉，跟 arecord_mentions / aupdate_suppressions
                # 走同一约定保证归档流程正常推进。
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(name, active)
