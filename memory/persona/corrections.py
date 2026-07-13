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

class CorrectionsMixin:
    @staticmethod
    def _build_correction_list(
        corrections: list[dict], old_text: str, new_text: str, entity: str,
    ) -> list[dict] | None:
        """Returns the modified list or None if duplicate (no change needed)."""
        for existing in corrections:
            if (existing.get('old_text') == old_text
                    and existing.get('new_text') == new_text
                    and existing.get('entity') == entity):
                return None
        corrections.append({
            'old_text': old_text,
            'new_text': new_text,
            'entity': entity,
            'created_at': datetime.now().isoformat(),
        })
        return corrections

    def _queue_correction(self, name: str, old_text: str, new_text: str, entity: str) -> None:
        corrections = self.load_pending_corrections(name)
        updated = self._build_correction_list(corrections, old_text, new_text, entity)
        if updated is None:
            return
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/persona_corrections.json",
        )
        atomic_write_json(self._corrections_path(name), updated, indent=2, ensure_ascii=False)
        logger.info(f"[Persona] {name}: 发现潜在矛盾，加入审视队列")

    async def _aqueue_correction(self, name: str, old_text: str, new_text: str, entity: str) -> None:
        """Public async entry — acquires the per-character lock.
        Callers already holding the lock must use _aqueue_correction_locked."""
        async with self._get_alock(name):
            await self._aqueue_correction_locked(name, old_text, new_text, entity)

    async def _aqueue_correction_locked(self, name: str, old_text: str, new_text: str, entity: str) -> None:
        """Inner body. Caller must hold self._get_alock(name).
        Used by aadd_fact which already has the lock."""
        corrections = await self.aload_pending_corrections(name)
        updated = self._build_correction_list(corrections, old_text, new_text, entity)
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
                pass
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
            # 文件损坏或被并发进程替换：按空队列处理，下次 add_pending_correction 会重建
            pass
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
            pairs = []
            for i, item in enumerate(corrections):
                if safe_int_field(item, 'resolve_attempts') >= MEMORY_LIVENESS_MAX_ATTEMPTS:
                    continue
                old_text = item.get('old_text', '')
                new_text = item.get('new_text', '')
                if old_text and new_text:
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

            batch_text = "\n".join(
                f"[{i}] 已有: {item['old_text']} | 新观察: {item['new_text']}"
                for i, item in pairs
            )
            prompt = persona_correction_prompt.format(pairs=batch_text, count=len(pairs))

            # ── LLM (锁外) ──
            try:
                from utils.token_tracker import set_call_type
                from utils.llm_client import create_chat_llm_async
                set_call_type("memory_correction")
                api_config = self._config_manager.get_model_api_config('correction')
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
    ) -> int:
        """The post-LLM apply phase of resolve_corrections. Runs inside the data lock."""
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            return await self._apply_correction_results_locked(
                name, persona, corrections, allowed_indices, results,
            )

    async def _apply_correction_results_locked(
        self,
        name: str,
        persona: dict,
        corrections: list[dict],
        allowed_indices: set,
        results: list,
    ) -> int:
        """Apply implementation for when the data lock is already held."""
        resolved = 0
        for result in results:
            if not isinstance(result, dict):
                continue
            try:
                idx = int(result.get('index', -1))
                if idx < 0 or idx >= len(corrections) or idx not in allowed_indices:
                    continue
                item = corrections[idx]
            except (ValueError, TypeError):
                continue

            action = result.get('action', 'keep_both')
            merged_text = result.get('text', item.get('new_text', ''))
            entity = item.get('entity', 'master')
            old_text = item.get('old_text', '')
            new_text = item.get('new_text', '')
            section_facts = self._get_section_facts(persona, entity)

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
                history_entry = {
                    'text': old_text,
                    'replaced_at': datetime.now().isoformat(),
                    'reason': 'correction',
                    # `source_fact_id` stays None: the pending-correction
                    # record has no upstream fact id today. Follow-up work
                    # can plumb one through _queue_correction without
                    # changing this structure.
                    'source_fact_id': None,
                }
                for j, existing in enumerate(section_facts):
                    et = existing.get('text', '') if isinstance(existing, dict) else str(existing)
                    if et == old_text:
                        if isinstance(existing, dict):
                            from config import PERSONA_VERSION_HISTORY_MAX as _VH_MAX
                            prior_history = existing.get('version_history', []) or []
                            existing['text'] = merged_text
                            existing['version_history'] = (
                                list(prior_history) + [history_entry]
                            )[-_VH_MAX:]
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
                            new_entry = self._normalize_entry(merged_text)
                            new_entry['version_history'] = [history_entry]
                            section_facts[j] = new_entry
                        break
            elif action == 'keep_new':
                section_facts[:] = [
                    e for e in section_facts
                    if (e.get('text', '') if isinstance(e, dict) else str(e)) != old_text
                ]
                section_facts.append(self._normalize_entry(new_text))
            elif action == 'keep_old':
                pass
            else:  # keep_both
                existing_texts = {
                    (e.get('text', '') if isinstance(e, dict) else str(e))
                    for e in section_facts
                }
                if new_text not in existing_texts:
                    section_facts.append(self._normalize_entry(new_text))

            resolved += 1

        if resolved:
            await self.asave_persona(name, persona)
            # 收集已处理条目的 created_at 作为精确匹配键
            processed_keys: set[str] = set()
            for r in results:
                raw_idx = r.get('index')
                if raw_idx is None:
                    continue
                try:
                    idx = int(raw_idx)
                    if 0 <= idx < len(corrections) and idx in allowed_indices:
                        key = corrections[idx].get('created_at', '')
                        if key:
                            processed_keys.add(key)
                except (ValueError, TypeError):
                    continue
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
                        logger.warning(
                            f"[Persona] {name}: correction dead-letter "
                            f"(old={(c.get('old_text', '') or '')[:30]!r} "
                            f"new={(c.get('new_text', '') or '')[:30]!r}) "
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
