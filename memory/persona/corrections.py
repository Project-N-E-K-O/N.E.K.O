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
"""Corrections methods for the memory manager."""

from __future__ import annotations

import asyncio
import hashlib


import json

import os




from datetime import datetime



from memory.facts import safe_int_field


from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable


from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)



from ._shared import (
    logger,
)

_VALID_CORRECTION_ACTIONS = frozenset({
    'merge', 'keep_new', 'keep_old', 'keep_both',
})


class CorrectionsMixin:
    @staticmethod
    def _build_correction_list(
        corrections: list[dict], old_text: str, new_text: str, entity: str,
        subject_fields: dict | None = None,
        old_speaker_provenance: dict | None = None,
        new_speaker_provenance: dict | None = None,
    ) -> list[dict] | None:
        """Returns the modified list or None if duplicate (no change needed)."""
        for existing in corrections:
            if (existing.get('old_text') == old_text
                    and existing.get('new_text') == new_text
                    and existing.get('entity') == entity
                    and existing.get('scope') == (subject_fields or {}).get('scope')):
                from memory.speaker_trust import normalize_trust, stable_speaker_id
                changed = False
                for prefix, provenance in (
                    ('old', old_speaker_provenance),
                    ('new', new_speaker_provenance),
                ):
                    if not isinstance(provenance, dict):
                        continue
                    speaker_id = stable_speaker_id(provenance.get('speaker_id'))
                    if speaker_id is None:
                        continue
                    mixed_key = f'{prefix}_speaker_provenance_mixed'
                    if existing.get(mixed_key) is True:
                        continue
                    raw_trust = provenance.get('speaker_trust')
                    trust = (
                        normalize_trust(raw_trust)
                        if isinstance(raw_trust, (int, float))
                        and not isinstance(raw_trust, bool)
                        else None
                    )
                    current_id = existing.get(f'{prefix}_speaker_id')
                    raw_current_trust = existing.get(f'{prefix}_speaker_trust')
                    current_trust = (
                        normalize_trust(raw_current_trust)
                        if isinstance(raw_current_trust, (int, float))
                        and not isinstance(raw_current_trust, bool)
                        else None
                    )
                    if current_id is None:
                        existing[f'{prefix}_speaker_id'] = speaker_id
                        changed = True
                    elif stable_speaker_id(current_id) != speaker_id:
                        existing.pop(f'{prefix}_speaker_id', None)
                        existing.pop(f'{prefix}_speaker_trust', None)
                        existing[mixed_key] = True
                        changed = True
                        continue
                    if current_id is None and trust is not None:
                        existing[f'{prefix}_speaker_trust'] = trust
                        changed = True
                    elif trust is not None and current_trust is not None:
                        conservative = min(current_trust, trust)
                        if conservative != current_trust:
                            existing[f'{prefix}_speaker_trust'] = conservative
                            changed = True
                return corrections if changed else None
        item = {
            'old_text': old_text,
            'new_text': new_text,
            'entity': entity,
            'created_at': datetime.now().isoformat(),
        }
        if subject_fields:
            # scoped correction 携带完整 subject 戳：section key 不含 scope，
            # resolve 分域与 apply 界定都需要它。
            item.update(subject_fields)
        from memory.speaker_trust import normalize_trust, stable_speaker_id
        for prefix, provenance in (
            ('old', old_speaker_provenance),
            ('new', new_speaker_provenance),
        ):
            if not isinstance(provenance, dict):
                continue
            speaker_id = stable_speaker_id(provenance.get('speaker_id'))
            if speaker_id is not None:
                item[f'{prefix}_speaker_id'] = speaker_id
                raw_trust = provenance.get('speaker_trust')
                if isinstance(raw_trust, (int, float)) and not isinstance(
                    raw_trust, bool
                ):
                    item[f'{prefix}_speaker_trust'] = normalize_trust(raw_trust)
        corrections.append(item)
        return corrections

    def _queue_correction(
        self, name: str, old_text: str, new_text: str, entity: str,
        subject_fields: dict | None = None,
        old_speaker_provenance: dict | None = None,
        new_speaker_provenance: dict | None = None,
    ) -> None:
        corrections = self.load_pending_corrections(name)
        updated = self._build_correction_list(
            corrections, old_text, new_text, entity, subject_fields,
            old_speaker_provenance, new_speaker_provenance,
        )
        if updated is None:
            return
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/persona_corrections.json",
        )
        atomic_write_json(self._corrections_path(name), updated, indent=2, ensure_ascii=False)
        logger.info(f"[Persona] {name}: 发现潜在矛盾，加入审视队列")

    async def _aqueue_correction(
        self, name: str, old_text: str, new_text: str, entity: str,
        subject_fields: dict | None = None,
        old_speaker_provenance: dict | None = None,
        new_speaker_provenance: dict | None = None,
    ) -> None:
        """Public async entry — acquires the per-character lock.
        Callers already holding the lock must use _aqueue_correction_locked."""
        async with self._get_alock(name):
            await self._aqueue_correction_locked(
                name, old_text, new_text, entity, subject_fields,
                old_speaker_provenance, new_speaker_provenance,
            )

    async def _aqueue_correction_locked(
        self, name: str, old_text: str, new_text: str, entity: str,
        subject_fields: dict | None = None,
        old_speaker_provenance: dict | None = None,
        new_speaker_provenance: dict | None = None,
    ) -> None:
        """Inner body. Caller must hold self._get_alock(name).
        Used by aadd_fact which already has the lock."""
        corrections = await self.aload_pending_corrections(name)
        updated = self._build_correction_list(
            corrections, old_text, new_text, entity, subject_fields,
            old_speaker_provenance, new_speaker_provenance,
        )
        if updated is None:
            return
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/persona_corrections.json",
        )
        await atomic_write_json_async(self._corrections_path(name), updated, indent=2, ensure_ascii=False)
        logger.info(f"[Persona] {name}: 发现潜在矛盾，加入审视队列")

    def load_pending_corrections(self, name: str) -> list[dict]:
        path = self._corrections_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, OSError):
                # Corrupt or concurrently replaced files are treated as an empty queue.
                return []
        return []

    async def aload_pending_corrections(self, name: str) -> list[dict]:
        path = self._corrections_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            # Corrupt or concurrently replaced files are treated as an empty queue.
            return []
        return []

    async def resolve_corrections(self, name: str) -> int:
        """Batch-review the contradiction queue with the correction model (single LLM call).

        Merges all pending corrections into one prompt for the correction model;
        returns the number of contradictions processed.

        C4 refactor + thinking: the LLM call runs outside the data lock. The
        data lock is only borrowed briefly before/after the LLM (load
        corrections / load persona + apply + save). The separate _resolve_alock
        serializes same-character resolve_corrections calls, preventing multiple
        entry points (IdleMaint subtask 2 and _run_post_turn_signals) from
        concurrently processing the same batch of corrections twice (especially
        keep_new, which without dedup would append duplicates).

        Why this is safe:
        - During the LLM call, aadd_fact / arecord_mentions / aapply_signal /
          aensure_persona can still take the data lock and make progress; the
          /process path no longer stalls
        - resolves are mutually exclusive (resolve_alock), preventing duplicate
          processing of the same correction batch
        - The apply phase reads a fresh persona, naturally merging with persona
          state written concurrently during the LLM call
        - The final "re-read corrections file → filter processed_keys → save"
          already protects corrections newly added during the LLM call
        """
        from config.prompts.prompts_memory import persona_correction_prompt

        # ── 串行 resolve（独立锁，与 data lock 不互锁） ──
        async with self._get_resolve_alock(name):
            # ── 短临界 1: 拿 corrections 列表 ──
            async with self._get_alock(name):
                corrections = await self.aload_pending_corrections(name)
            if not corrections:
                return 0

            # 合并所有矛盾为单个 prompt。受 PERSONA_CORRECTION_BATCH_LIMIT
            # 限制：corrections 队列可能堆积，单次只处理前 N 条，剩下的下次
            # 触发时再处理。
            #
            # Liveness：过滤已达 ``MEMORY_LIVENESS_MAX_ATTEMPTS`` 的 dead-letter
            # entry（防御性——下面 _abump_correction_attempts_and_dead_letter
            # 命中阈值时会直接从 queue 删除，正常路径不会让 attempts ≥ MAX 的
            # entry 还留在 queue。这里只是 race-condition 防御 + schema 兼容）。
            from config import (
                MEMORY_LIVENESS_MAX_ATTEMPTS,
                PERSONA_CORRECTION_BATCH_LIMIT,
            )
            from memory.scopes import SCOPED_PERSONA_PREFIX

            # 单批只装同一个隔离域：legacy 私聊全体算一个域（与升级前
            # 行为逐字节一致），每个 @subject/ scoped section 各算一个域。
            # 不分域的话，不同群/私聊的记忆文本会拼进同一个 correction
            # prompt 里互相可见，且 keep/merge 的不可逆决策会被相邻域的
            # 上下文影响（merge 重写文本甚至可能串词）。非本域条目留在
            # 队列里，下一轮 resolve 触发时按 FIFO 头部轮到的域继续消化。
            pairs = []
            batch_domain = None
            for i, item in enumerate(corrections):
                if safe_int_field(item, 'resolve_attempts') >= MEMORY_LIVENESS_MAX_ATTEMPTS:
                    continue
                old_text = item.get('old_text', '')
                new_text = item.get('new_text', '')
                if old_text and new_text:
                    entity_raw = item.get('entity')
                    entity = (
                        entity_raw.strip()
                        if isinstance(entity_raw, str) else ''
                    )
                    if not entity:
                        # 实体缺失/空/非字符串的畸形 correction 既不算 legacy
                        # 也不算 scoped：跳过本批、留在队列（fail-closed，
                        # 与 scopes.py 读路径对损坏描述符的处理一致）。
                        continue
                    if entity.startswith(SCOPED_PERSONA_PREFIX):
                        # 域 = (section key, scope)：同 kind/id 的自定义
                        # scope 互为独立隔离域，不得同批。旧队列条目无
                        # scope 字段时按默认 scope（=key）归域。
                        domain = (
                            entity,
                            item.get('scope')
                            or entity[len(SCOPED_PERSONA_PREFIX):],
                        )
                    else:
                        domain = '__legacy__'
                    if batch_domain is None:
                        batch_domain = domain
                    elif domain != batch_domain:
                        continue
                    pairs.append((i, item))
                if len(pairs) >= PERSONA_CORRECTION_BATCH_LIMIT:
                    break
            if not pairs:
                return 0
            # 仅允许"本批送进 prompt"的全局 index 被消费 —— LLM 偶尔会回写
            # 没在这一批 prompt 里的合法全局 index（比如 hallucinate 出未来批
            # 的 idx），不防的话会误改未送审的 corrections，导致队列数据被
            # 错误消费。
            allowed_indices = {i for i, _ in pairs}

            from memory.speaker_trust import trust_band
            batch_text = "\n".join(
                f"[{i}] 已有(trust={trust_band(item.get('old_speaker_trust'))}): "
                f"{item['old_text']} | 新观察(trust="
                f"{trust_band(item.get('new_speaker_trust'))}): {item['new_text']}"
                for i, item in pairs
            )
            prompt = persona_correction_prompt.format(pairs=batch_text, count=len(pairs))

            # ── LLM (锁外) ──
            try:
                from utils.token_tracker import set_call_type
                from utils.llm_client import create_chat_llm_async
                set_call_type("memory_correction")
                api_config = await self._config_manager.aget_model_api_config('correction')
                # timeout: 见 MEMORY_LLM_HARD_TIMEOUT_SECONDS（上游转发
                # 120s hard cap，必须 ≤110）。批量决策（每对 keep_old/
                # keep_new/keep_both/merge + 重写 merged_text）值得吃满
                # thinking——后果不可逆（persona pollution）。LLM 在 data
                # lock 外，不阻塞 /process 路径上的 arecord_mentions /
                # aapply_signal。
                # max_retries=0: 禁 SDK 自动重试（这里没业务 retry，单次即终态）。
                # extra_body=None: 显式开 thinking。
                from config import MEMORY_LLM_HARD_TIMEOUT_SECONDS, LLM_OUTPUT_GUARD_MAX_TOKENS
                llm = await create_chat_llm_async(
                    api_config['model'],
                    api_config['base_url'], api_config['api_key'],
                    timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS, max_retries=0,
                    max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length JSON (incl. thinking) isn't truncated
                    extra_body=None,
                    provider_type=api_config.get('provider_type'),
                )
                try:
                    resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # correction prompt built from PERSONA_MERGE_POOL_MAX_TOKENS-capped entity pool.
                finally:
                    await llm.aclose()
                raw = resp.content
                if raw.startswith("```"):
                    raw = raw.replace("```json", "").replace("```", "").strip()
                results = robust_json_loads(raw)
                if not isinstance(results, list):
                    results = [results]
            except Exception as e:
                logger.warning(f"[Persona] {name}: correction model 调用失败: {e}")
                # Liveness 兜底：给本批 corrections bump resolve_attempts 字段，
                # 达 MEMORY_LIVENESS_MAX_ATTEMPTS 的 entry 从 queue dead-letter
                # 丢弃。否则同样的 (old_text, new_text) 队头 entry 每次 resolve
                # tick 都被送进相同 prompt，LLM 同样失败，永久卡住后续 corrections
                # （safety filter / 长 prompt token 超限 / 永远 parse 不出来 等
                # 毒 payload 场景）。
                await self._abump_correction_attempts_and_dead_letter(
                    name, [item for _, item in pairs],
                )
                return 0

            # ── 短临界 2: load fresh persona + apply + save ──
            resolved = await self._apply_correction_results(
                name, corrections, allowed_indices, results,
                refresh_pending=True,
            )
            # 对偶 fact_dedup：LLM 返了 list 但 ``_apply_correction_results_locked``
            # 没消费任何 correction（全 invalid index / 全 unknown action），
            # corrections queue 原样保留 → 队头同样 N 条下次 tick 重新喂同样
            # prompt → 仍然 0 resolved → 永久卡死。算 attempts 一次。
            if resolved == 0:
                logger.warning(
                    f"[Persona] {name}: correction model 输出 {len(results)} "
                    f"条 action 全部无效（invalid index / unknown action），"
                    f"batch 0 条 correction 消费，按 attempt 失败计"
                )
                await self._abump_correction_attempts_and_dead_letter(
                    name, [item for _, item in pairs],
                )
            return resolved

    async def _apply_correction_results(
        self,
        name: str,
        corrections: list[dict],
        allowed_indices: set,
        results: list,
        *,
        refresh_pending: bool = False,
    ) -> int:
        """The post-LLM apply phase of resolve_corrections. Runs inside the data lock."""
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            fresh_corrections = (
                await self.aload_pending_corrections(name)
                if refresh_pending else None
            )
            return await self._apply_correction_results_locked(
                name, persona, corrections, allowed_indices, results,
                fresh_corrections=fresh_corrections,
            )

    async def _apply_correction_results_locked(
        self,
        name: str,
        persona: dict,
        corrections: list[dict],
        allowed_indices: set,
        results: list,
        *,
        fresh_corrections: list[dict] | None = None,
    ) -> int:
        """Apply implementation for when the data lock is already held."""
        resolved = 0
        resolved_indices: set[int] = set()
        fresh_by_created_at = None
        if fresh_corrections is not None:
            fresh_by_created_at = {
                item.get('created_at'): item
                for item in fresh_corrections
                if isinstance(item, dict) and item.get('created_at')
            }
        for result in results:
            if not isinstance(result, dict):
                continue
            try:
                idx = int(result.get('index', -1))
                if idx < 0 or idx >= len(corrections) or idx not in allowed_indices:
                    continue
                item = corrections[idx]
                if fresh_by_created_at is not None:
                    item = fresh_by_created_at.get(item.get('created_at'))
                    if item is None:
                        # Removed while the model was running: its stale
                        # snapshot must not be applied or trusted.
                        continue
            except (ValueError, TypeError):
                continue

            raw_action = result.get('action')
            action = (
                raw_action.strip().lower()
                if isinstance(raw_action, str) else None
            )
            # Trust may choose between valid model alternatives, but it must
            # never turn malformed model output into a destructive decision.
            # Invalid/missing actions remain queued for the existing liveness
            # retry/dead-letter path.
            if action not in _VALID_CORRECTION_ACTIONS:
                continue
            merged_text = result.get('text', item.get('new_text', ''))
            entity_raw = item.get('entity')
            entity = entity_raw.strip() if isinstance(entity_raw, str) else ''
            if not entity:
                # 对偶批次构建的畸形实体守卫：绝不能默认写进 master 段——
                # 一条丢了 entity 的 scoped correction 会因此跨进 legacy
                # 私聊 persona。跳过即留队，不计 resolved。
                continue
            old_text = item.get('old_text', '')
            new_text = item.get('new_text', '')
            from memory.speaker_trust import (
                deterministic_relation,
                preferred_by_trust,
            )
            old_speaker_id = item.get('old_speaker_id')
            new_speaker_id = item.get('new_speaker_id')
            preference = None
            old_trust = item.get('old_speaker_trust')
            new_trust = item.get('new_speaker_trust')
            if (
                old_speaker_id and new_speaker_id
                and old_speaker_id != new_speaker_id
                and isinstance(old_trust, (int, float))
                and not isinstance(old_trust, bool)
                and isinstance(new_trust, (int, float))
                and not isinstance(new_trust, bool)
                and deterministic_relation(old_text, new_text) == 'correction'
            ):
                preference = preferred_by_trust(
                    old_trust, new_trust,
                )
            if preference is not None and action != 'keep_both':
                forced = 'keep_old' if preference == 'old' else 'keep_new'
                if action != forced:
                    logger.info(
                        f"[Persona] {name}: trust 仲裁覆盖模型动作 "
                        f"entity={entity} old_speaker={old_speaker_id} "
                        f"new_speaker={new_speaker_id} action={action!r}->{forced}"
                    )
                action = forced
            section_facts = self._get_section_facts(persona, entity)

            # scoped correction 的一切匹配/删除/新建都限定在 item 自己的
            # subject 域内：section key 不含 scope，同 kind/id 异 scope 的
            # 条目共存于同一 section，按裸文本跨域改写即泄漏。item 无戳
            # （升级前入队）按默认 scope（=key）重建；重建失败 fail-closed。
            from memory.scopes import (
                SCOPED_PERSONA_PREFIX,
                MemoryScopeError,
                MemorySubject,
                entry_matches_subject,
                subject_from_entry,
            )

            item_subject = None
            if entity.startswith(SCOPED_PERSONA_PREFIX):
                item_subject = subject_from_entry(item)
                if item_subject is None:
                    section_key_body = entity[len(SCOPED_PERSONA_PREFIX):]
                    kind, _, subject_id = section_key_body.partition(':')
                    try:
                        item_subject = MemorySubject.create(
                            kind, subject_id,
                            scope=item.get('scope') or section_key_body,
                        )
                    except MemoryScopeError:
                        continue

            def _entry_in_scope(entry, _subj=item_subject) -> bool:
                if _subj is None:
                    return True
                return isinstance(entry, dict) and entry_matches_subject(entry, _subj)

            def _stamped_new_entry(text_value, _subj=item_subject) -> dict:
                new_entry = self._normalize_entry_for_section(
                    persona, entity, text_value,
                )
                if _subj is not None:
                    new_entry.update(_subj.as_entry_fields())
                if not new_entry.get('id'):
                    # correction 新建条目必须有 ID（掺域盐，对齐
                    # _build_fact_entry）：空 ID 会被 ID 索引的 refine/
                    # signal/archive 全部跳过，且多条互相撞空串。
                    salt = (
                        f"{_subj.key}|{_subj.scope}|" if _subj is not None else ""
                    )
                    digest = hashlib.sha256(
                        (salt + str(text_value)).encode()
                    ).hexdigest()[:8]
                    new_entry['id'] = (
                        f"corr_{datetime.now().strftime('%Y%m%d%H%M%S')}_{digest}"
                    )
                if item.get('new_speaker_id'):
                    new_entry['speaker_id'] = item['new_speaker_id']
                    trust = item.get('new_speaker_trust')
                    if (
                        isinstance(trust, (int, float))
                        and not isinstance(trust, bool)
                        and 0.0 <= float(trust) <= 1.0
                    ):
                        new_entry['speaker_trust'] = float(trust)
                return new_entry

            def _history_snapshot(text_value: str, prefix: str, reason: str) -> dict:
                snapshot = {
                    'text': text_value,
                    'replaced_at': datetime.now().isoformat(),
                    'reason': reason,
                    'source_fact_id': None,
                }
                speaker_id = item.get(f'{prefix}_speaker_id')
                if speaker_id:
                    snapshot['speaker_id'] = speaker_id
                    trust = item.get(f'{prefix}_speaker_trust')
                    if (
                        isinstance(trust, (int, float))
                        and not isinstance(trust, bool)
                        and 0.0 <= float(trust) <= 1.0
                    ):
                        snapshot['speaker_trust'] = float(trust)
                return snapshot

            if action == 'merge':
                # `replace` means "new observation is an update/correction to
                # the old memory" — semantically an in-place edit, not a
                # fresh insertion. We update `text` + extend the version
                # chain but **preserve** id / source / source_id / evidence
                # counters (reinforcement, disputation, sub_zero_days) /
                # recent_mentions / merged_from_ids so confirm/dispute state
                # and provenance survive the rewrite. Rebuilding via
                # `_normalize_entry(merged_text)` would wipe all of that,
                # reducing a confirmed persona entry to a blank slate.
                history_entry = _history_snapshot(
                    old_text, 'old', 'correction',
                )
                for j, existing in enumerate(section_facts):
                    et = existing.get('text', '') if isinstance(existing, dict) else str(existing)
                    if et == old_text and _entry_in_scope(existing):
                        if isinstance(existing, dict):
                            from config import PERSONA_VERSION_HISTORY_MAX as _VH_MAX
                            prior_history = existing.get('version_history', []) or []
                            from memory.speaker_trust import provenance_of_entries
                            new_source = {
                                'speaker_id': new_speaker_id,
                                'speaker_trust': item.get('new_speaker_trust'),
                            }
                            folded_provenance = provenance_of_entries([
                                existing, new_source,
                            ])
                            mixed_speakers = not folded_provenance.get(
                                'speaker_id'
                            )
                            merged_history = list(prior_history) + [history_entry]
                            if mixed_speakers and new_speaker_id:
                                merged_history.append(_history_snapshot(
                                    new_text, 'new', 'correction_merge_source',
                                ))
                            existing['text'] = merged_text
                            existing['version_history'] = merged_history[-_VH_MAX:]
                            # Replace provenance even for the same speaker:
                            # the merged text must carry the conservative
                            # minimum of both authored snapshots, never borrow
                            # the older stronger score. Mixed/unknown sources
                            # yield an empty fold and clear single-speaker
                            # provenance entirely.
                            for key in (
                                'speaker_id', 'speaker_trust', 'speaker_label',
                            ):
                                existing.pop(key, None)
                            existing.update(folded_provenance)
                            # Text changed → invalidate the derived
                            # caches so the next render recomputes
                            # against the new text instead of serving
                            # stale counts/vectors tied to old_text.
                            self._invalidate_token_count_cache(existing)
                            self._invalidate_embedding_cache(existing)
                            section_facts[j] = self._normalize_entry(existing)
                        else:
                            # Legacy str entry — no metadata to preserve;
                            # migrate to dict form and seed the chain.
                            new_entry = _stamped_new_entry(merged_text)
                            new_entry['version_history'] = [history_entry]
                            section_facts[j] = new_entry
                        break
            elif action == 'keep_new':
                old_entries = [
                    e for e in section_facts
                    if (e.get('text', '') if isinstance(e, dict) else str(e)) == old_text
                    and _entry_in_scope(e)
                ]
                section_facts[:] = [
                    e for e in section_facts
                    if (e.get('text', '') if isinstance(e, dict) else str(e)) != old_text
                    or not _entry_in_scope(e)
                ]
                replacement = _stamped_new_entry(new_text)
                from config import PERSONA_VERSION_HISTORY_MAX as _VH_MAX
                if preference == 'new':
                    prior_history = (
                        list(old_entries[0].get('version_history') or [])
                        if old_entries and isinstance(old_entries[0], dict)
                        else []
                    )
                    replacement['version_history'] = (
                        prior_history
                        + list(replacement.get('version_history') or [])
                        + [
                            _history_snapshot(
                                old_text, 'old', 'trust_superseded',
                            )
                            for _ in old_entries[:1]
                        ]
                    )[-_VH_MAX:]
                section_facts.append(replacement)
            elif action == 'keep_old':
                # trust 仲裁选中旧观察时，新观察不能静默蒸发：嵌入赢家的
                # version_history，保留全文、speaker 与 trust，可人工回滚。
                if preference == 'old':
                    from config import PERSONA_VERSION_HISTORY_MAX as _VH_MAX
                    for existing in section_facts:
                        et = existing.get('text', '') if isinstance(existing, dict) else str(existing)
                        if et == old_text and _entry_in_scope(existing) and isinstance(existing, dict):
                            existing['version_history'] = (
                                list(existing.get('version_history') or []) + [
                                    _history_snapshot(
                                        new_text, 'new',
                                        'trust_rejected_observation',
                                    )
                                ]
                            )[-_VH_MAX:]
                            break
            else:  # keep_both
                existing_texts = {
                    (e.get('text', '') if isinstance(e, dict) else str(e))
                    for e in section_facts
                    if _entry_in_scope(e)
                }
                if new_text not in existing_texts:
                    section_facts.append(_stamped_new_entry(new_text))

            resolved += 1
            resolved_indices.add(idx)

        if resolved:
            await self.asave_persona(name, persona)
            # 收集已处理条目的 created_at 作为精确匹配键
            processed_keys = {
                corrections[idx].get('created_at', '')
                for idx in resolved_indices
                if corrections[idx].get('created_at', '')
            }
            # 重新读取文件，仅删除已处理的条目，保留 LLM 期间新增的
            # （防止并发 _aqueue_correction 新追加的矛盾被覆盖丢失）
            current = await self.aload_pending_corrections(name)
            remaining = [c for c in current if c.get('created_at', '') not in processed_keys]
            assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{name}/persona_corrections.json",
            )
            await atomic_write_json_async(self._corrections_path(name), remaining,
                                          indent=2, ensure_ascii=False)
            logger.info(f"[Persona] {name}: 批量审视完成 {resolved} 条矛盾，剩余 {len(remaining)} 条")
        return resolved

    async def _abump_correction_attempts_and_dead_letter(
        self, name: str, batch_items: list[dict],
    ) -> None:
        """Liveness fallback when the resolve_corrections LLM fails.

        Bumps the ``resolve_attempts`` field on every entry of this batch's
        corrections; entries reaching ``MEMORY_LIVENESS_MAX_ATTEMPTS`` are
        removed from the queue with a WARN.

        Why: if the queue head is a poison payload (safety filter / oversized
        prompt / never parsable), resolve_corrections takes the same first N
        FIFO entries into the prompt every tick and the LLM fails the same way →
        the whole corrections pipeline deadlocks forever. Same root cause as the
        poison window in signal extraction — stuck cursor + no counter.
        """
        from config import MEMORY_LIVENESS_MAX_ATTEMPTS
        if not batch_items:
            return
        bumped_keys = {
            it.get('created_at', '') for it in batch_items if it.get('created_at')
        }
        if not bumped_keys:
            return
        async with self._get_alock(name):
            current = await self.aload_pending_corrections(name)
            kept: list[dict] = []
            dropped = 0
            for c in current:
                key = c.get('created_at', '')
                if key in bumped_keys:
                    new_attempts = safe_int_field(c, 'resolve_attempts') + 1
                    if new_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
                        dropped += 1
                        if (
                            c.get('subject_kind')
                            or c.get('subject_id')
                            or c.get('scope')
                        ):
                            # scoped（成员/群衍生）内容不进日志：只打域标识
                            # 与长度，对齐 scoped 反思原文不进 stdout 的口径。
                            detail = (
                                f"(scoped {c.get('subject_kind', '')!s}"
                                f"/{c.get('subject_id', '')!s}"
                                f" old_len={len(c.get('old_text', '') or '')}"
                                f" new_len={len(c.get('new_text', '') or '')})"
                            )
                        else:
                            detail = (
                                f"(old={(c.get('old_text', '') or '')[:30]!r} "
                                f"new={(c.get('new_text', '') or '')[:30]!r})"
                            )
                        logger.warning(
                            f"[Persona] {name}: correction dead-letter "
                            f"{detail} "
                            f"resolve {new_attempts} 次失败 ≥ "
                            f"{MEMORY_LIVENESS_MAX_ATTEMPTS}，丢弃"
                        )
                        continue
                    c['resolve_attempts'] = new_attempts
                kept.append(c)
            try:
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{name}/persona_corrections.json",
                )
                await atomic_write_json_async(
                    self._corrections_path(name), kept,
                    indent=2, ensure_ascii=False,
                )
            except MaintenanceModeError as e:
                logger.debug(
                    f"[Persona] {name}: 维护态跳过 correction attempts 写盘: {e}"
                )
            except OSError as e:
                logger.warning(
                    f"[Persona] {name}: correction attempts 写盘失败: {e}"
                )
            if dropped:
                logger.info(
                    f"[Persona] {name}: dead-letter 丢弃 {dropped} 条 correction，"
                    f"剩余 {len(kept)} 条"
                )
