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
            # asyncio.Lock 不可重入：在锁内必须用 _aensure_persona_locked
            # 而不是 aensure_persona（后者自己 acquire 同把锁 → 死锁）
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

            consumed: set[str] = set()  # split / merge / discard 的源 id
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
                    if not src or src.get('protected'):
                        continue
                    produce = act_obj.get('produce')
                    if not isinstance(produce, list) or len(produce) < 2:
                        continue
                    # 先过滤 valid，再按实际 split_count 分摊 evidence
                    valid_produce = [
                        p for p in produce
                        if isinstance(p, dict) and str(p.get('text', '')).strip()
                    ]
                    if len(valid_produce) < 2:
                        continue
                    split_n = len(valid_produce)
                    # 继承 evidence（Codex P1 #1392）：可分配的 counters 按
                    # 1/N 等分（保留累积 evidence，不让 split 静默清零评分/
                    # 衰减信号）；天数 / 时间戳 / 溯源直接继承 src。
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
                    merged = self._normalize_entry(text)
                    # 唯一 id（同 split），否则 section_by_id 会折叠
                    merged['id'] = self._refine_persona_id(text)
                    history = []
                    # 继承所有 evidence 状态 —— 不能只复制 reinforcement
                    # 和 user_fact_reinforce_count，否则 disputation /
                    # rein_last_signal_at / disp_last_signal_at /
                    # sub_zero_days 会被默认值清零，掩盖反证和归档倒计时
                    # （CodeRabbit Major #1392）。
                    max_rein = 0.0
                    max_disp = 0.0
                    max_user_count = 0
                    max_sub_zero_days = 0
                    latest_rein_signal_at: str | None = None
                    latest_disp_signal_at: str | None = None
                    latest_sub_zero_increment: str | None = None
                    inherited_source = None
                    inherited_source_id = None
                    for sid in valid_ids:
                        src = section_by_id[sid]
                        history.append({
                            'text': src.get('text', ''),
                            'replaced_at': now_iso,
                            'reason': 'refine_merge',
                            'source_fact_id': None,
                        })
                        max_rein = max(max_rein, float(src.get('reinforcement', 0) or 0))
                        max_disp = max(max_disp, float(src.get('disputation', 0) or 0))
                        max_user_count = max(
                            max_user_count,
                            int(src.get('user_fact_reinforce_count', 0) or 0),
                        )
                        max_sub_zero_days = max(
                            max_sub_zero_days,
                            int(src.get('sub_zero_days', 0) or 0),
                        )
                        # ISO timestamp 字符串比较即时间序；取最新
                        for key, current in (
                            ('rein_last_signal_at', latest_rein_signal_at),
                            ('disp_last_signal_at', latest_disp_signal_at),
                            ('sub_zero_last_increment_date', latest_sub_zero_increment),
                        ):
                            v = src.get(key)
                            if v and (current is None or v > current):
                                if key == 'rein_last_signal_at':
                                    latest_rein_signal_at = v
                                elif key == 'disp_last_signal_at':
                                    latest_disp_signal_at = v
                                else:
                                    latest_sub_zero_increment = v
                        if inherited_source is None:
                            inherited_source = src.get('source')
                            inherited_source_id = src.get('source_id')
                    from config import PERSONA_VERSION_HISTORY_MAX as _VH_MAX
                    merged['version_history'] = history[-_VH_MAX:]
                    merged['reinforcement'] = max_rein
                    merged['disputation'] = max_disp
                    merged['user_fact_reinforce_count'] = max_user_count
                    merged['sub_zero_days'] = max_sub_zero_days
                    merged['rein_last_signal_at'] = latest_rein_signal_at
                    merged['disp_last_signal_at'] = latest_disp_signal_at
                    merged['sub_zero_last_increment_date'] = latest_sub_zero_increment
                    merged['source'] = inherited_source or 'unknown'
                    merged['source_id'] = inherited_source_id
                    produced.append(merged)
                    consumed.update(valid_ids)
                    applied += 1

                elif act == 'modify':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_ids or src_id in consumed:
                        continue
                    src = section_by_id.get(src_id)
                    if not src or src.get('protected'):
                        continue
                    produce = act_obj.get('produce')
                    if not isinstance(produce, dict):
                        continue
                    new_text = str(produce.get('text', '')).strip()
                    if not new_text:
                        continue
                    reason = str(act_obj.get('reason') or 'refine_modify')
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
                    applied += 1

                elif act == 'discard':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_ids or src_id in consumed:
                        continue
                    src = section_by_id.get(src_id)
                    if not src or src.get('protected'):
                        continue
                    consumed.add(src_id)
                    applied += 1

            # Stamp 决策（Codex P1 + P2 #1392 合并语义）：
            # - applied > 0：有变化，必 stamp + save
            # - applied == 0 + actions 为空：LLM 明确判定 no-op，stamp
            #   防 cluster_hash skip 失效（Codex P1）
            # - applied == 0 + actions 非空：所有 action 都被 reject =
            #   LLM 输出语义垃圾（unknown action / missing field 等），
            #   不 stamp，等下轮重试（Codex P2，防垃圾输出导致 30 天
            #   静默推迟需要的 refine）
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
                    # 成功 stamp → 清 Site 4 liveness 计数器：之前因毒 cluster
                    # 邻居拖累的累计 attempts 一笔勾销，让本 entry 后续可以
                    # 重新进新 cluster 接受 LLM 判断。
                    if e.get('refine_attempts'):
                        e['refine_attempts'] = 0
                    stamped += 1

            if applied == 0 and stamped == 0:
                # cluster 成员都已不在（被并发删除等罕见情况），无可 stamp，
                # 也无须落盘，直接返回。
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

        # 按 entity 收集需要 bump 的 entry id（fact 不算——fact 是 readonly
        # 信息源，refine 永远不会改它，自然也不应记 refine_attempts）。
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
                    # 戳失败时刻供 dead-letter 时间自愈（cooldown_elapsed）：
                    # 一次性 correction 模型宕机把 entry 顶到 MAX 后，过 5h 冷却
                    # 重新进候选 probe，避免宕机恢复后仍永久冻结。
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
