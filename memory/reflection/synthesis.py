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

class SynthesisMixin:
    async def synthesize_reflections(self, lanlan_name: str) -> list[dict]:
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
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm_async

        unabsorbed = await self._fact_store.aget_unabsorbed_facts(lanlan_name)
        if len(unabsorbed) < MIN_FACTS_FOR_REFLECTION:
            return []

        # 失败退避 key 必须覆盖**全部** unabsorbed facts，所以在 cap 之前先取。
        # 不能用 rid（下面那个 capped top-N 子集的 hash）当退避 key：否则一个
        # poison 的 top-N 批次 dead-letter 后，新到的低 importance facts 进不了
        # top-N → rid 不变 → 合成被永久跳过，把整个 unabsorbed 池饿死（Codex P2）。
        # 用全集 key 时任何新 fact 都会改 key → 预算复位 → 重试一次。
        all_unabsorbed_ids = sorted(f['id'] for f in unabsorbed)
        backoff_key = _reflection_id_from_facts(all_unabsorbed_ids)

        # Cap unabsorbed facts entering this synthesis call. 上游
        # aget_unabsorbed_facts 没有 limit 参数，长期不上线时可能堆几百
        # 条；按 importance(desc) → 创建时间(asc) 排序后取前 N 条，避免
        # 一次性塞超长 prompt。
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

        # 排序一次：on-disk 字段与 _reflection_id_from_facts 内部 sorted 对齐，
        # 消除 "hash 用 sorted，存盘不 sorted" 的隐式非对称
        source_fact_ids = sorted(f['id'] for f in unabsorbed)
        rid = _reflection_id_from_facts(source_fact_ids)

        # 幂等 short-circuit：同一批 facts 的 reflection 已持久化 →
        # 不重复调 LLM，仅补跑 mark_absorbed（致命点 3 的重启补救路径）
        # 注：这里是 lock-外的 advisory check，提早避免无谓 LLM；最终
        # dedup 在 lock-内重做（防 LLM 期间并发写入）。
        existing_reflections = await self.aload_reflections(lanlan_name)
        existing = next((r for r in existing_reflections if r.get('id') == rid), None)
        if existing is not None:
            await self._fact_store.amark_absorbed(lanlan_name, source_fact_ids)
            logger.info(
                f"[Reflection] {lanlan_name}: 检测到同批 facts 已合成过 reflection "
                f"{rid}，跳过 LLM，补跑 mark_absorbed"
            )
            return []

        # 失败退避（dead-letter）：同一批 unabsorbed facts(backoff_key) 合成已连续
        # 失败 ≥ MEMORY_LIVENESS_MAX_ATTEMPTS 次 → 不再每 180s 原样重抽空跑 LLM。
        # 任一 unabsorbed fact 增减 → backoff_key 变、计数天然复位（用户审计 #2）。
        # 时间自愈：池子不变（挂机）时，每过 MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS
        # 放行一次 probe，让一次性模型宕机恢复后自愈，poison 批仍被压到每 5h 一次。
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
        master_name = name_mapping.get('human', '主人')

        facts_text = "\n".join(f"- {f['text']} (importance: {f.get('importance', 5)})" for f in unabsorbed)
        related_block = await self._build_related_context_block(lanlan_name, unabsorbed)
        reflection_prompt = get_reflection_prompt(get_global_language())
        prompt = reflection_prompt.replace('{RELATED_CONTEXT_BLOCK}', related_block)
        prompt = prompt.replace('{FACTS}', facts_text)
        prompt = prompt.replace('{LANLAN_NAME}', lanlan_name)
        prompt = prompt.replace('{MASTER_NAME}', master_name)

        try:
            set_call_type("memory_reflection")
            api_config = self._config_manager.get_model_api_config('summary')
            # timeout: 见 MEMORY_LLM_HARD_TIMEOUT_SECONDS 注释（上游转发
            # 120s hard cap，client 必须 ≤110s）。开 thinking 后输出多字段
            # JSON ontology 比简单分类长，吃满 110 也算合理。LLM 在锁外
            # 不阻塞同角色其他 reflection 写。
            # max_retries=0: 禁 SDK 自动重试（无业务 retry，单次即终态，外层
            # try/except 兜底返回 []）。
            # extra_body=None: 显式开 thinking——synth 是创意+结构化合成，
            # 思考能改善 ontology 字段的一致性和 reflection text 的质量。
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
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # prompt assembled from token-capped memory components (REFLECTION_*/RECALL_* budgets in the prompt builder).
            finally:
                await llm.aclose()
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
            reflection_entity = result.get('entity', 'relationship')
            if reflection_entity not in ('master', 'neko', 'relationship'):
                reflection_entity = 'relationship'

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
            # Schema v2: event_when_raw 由 LLM 输出（相对时间，offset+unit），
            # 系统按 created_at 解算。LLM 可能省略整段或单边 (start/end)，
            # normalize_event_when 把破损值兜底成 None。
            from memory.temporal import normalize_event_when as _norm_when
            event_when_raw = _norm_when(result.get('event_when'))

            ok, reason = _validate_reflection_ontology(
                reflection_entity, rel_type, temporal, reflection_text,
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

        # Create pending reflection — id 已在函数开头由 source_fact_ids 决定
        now = datetime.now()
        now_iso = now.isoformat()

        # Importance-based initial rein seed：让"关键节点"型 reflection 起步
        # 就带一点正分，不必等多轮 user confirms 才穿越 CONFIRMED 阈值。
        # 不走 aapply_signal（synthesis 本身不经 event log），直接写进初始
        # 字典——synth 不是 event-sourced，这些初始值就是 ground truth。
        max_importance = max(
            (safe_importance(f) for f in unabsorbed),
            default=5,
        )
        initial_rein = initial_reinforcement_from_importance(max_importance)

        # Schema v2：event_start/end 是 fact 和 reflection 共用的 ISO 锚点。
        # fallback 策略按 temporal_scope 分流：
        #   - state / episode 需要 end（TTL 判定锚点），end 缺失时和 start 同值
        #   - pattern 允许 end=None（持续模式没有"何时结束"）
        #   - 兜底（temporal=None/legacy）按 pattern 处理（保守不淡出）
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

        # ── LOCK 仅护住 re-load + dedup append + save ──
        async with self._get_alock(lanlan_name):
            # 再次 load：LLM 调用期间可能有并发 synth；用最新 list 做 id dedup 追加
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

        # reflection 已落盘（本次 append 或并发对方先写），清掉本次 unabsorbed
        # 全集的失败退避记录——锁外调用（_aclear_synth_backoff 自取锁，避免
        # reentrant 死锁）。
        await self._aclear_synth_backoff(lanlan_name, backoff_key)

        # 无条件 mark_absorbed：幂等，且覆盖 save 成功后但在此崩溃的补跑情况
        # （fact_store 自己有锁，不需要在 reflection 锁内）
        await self._fact_store.amark_absorbed(lanlan_name, source_fact_ids)

        if not created:
            # 并发分支已落盘对方的对象；返回内存副本会让调用方拿到一个
            # 未持久化、可能与磁盘版文本不同的"幽灵反思"，违反"返回值
            # = 本调用真正新建的反思"语义。
            return []
        # reflection 原文不写 logger（隐私）；本地 print 兜底
        logger.info(f"[Reflection] {lanlan_name}: 合成了新反思 {rid} (len={len(reflection_text)} chars)")
        print(f"[Reflection] {lanlan_name}: 新反思 {rid}: {reflection_text[:50]}...")
        return [reflection]

    async def _build_related_context_block(
        self, lanlan_name: str, unabsorbed: list[dict]
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
            # 同时检查 is_disabled (sticky 关闭) 和 is_available (READY) ——
            # INIT/LOADING 状态下 is_disabled=False 但 is_available=False，
            # 若只看前者，reranker 会降级到 evidence-only 排序，把无关历史
            # fact 当 "相关背景" 塞进 prompt（Codex P2 #1392）。
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
            # Phase C-2: 用 full 池含归档 fact，让远期 absorbed 也能被召回
            all_facts = await self._fact_store.aload_facts_full(lanlan_name)
        except Exception as e:
            logger.warning(f"[Reflection] related context load_facts 失败: {e}")
            return ""

        # Codex P2 #1392：必须 pre-filter 出有 valid embedding 的 fact 才能
        # 进 reranker。fact 没 evidence `score` 字段，若放进 rerank=False 的
        # coarse_rank 而 embedding 又是 stale/missing（model-id 切换后 / backfill
        # 前），fallback 路径会按 evidence_score=0 排序 → 顺序近似随机 →
        # 把任意 fact 当 "相关背景" 注入 prompt。这里先 filter，pool 为空就
        # early return，宁可没 RELATED_CONTEXT 也不要塞无关历史。
        absorbed_pool = [
            f for f in all_facts
            if f.get('absorbed')
            and f.get('id') not in unabsorbed_ids
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
            # Per-query top-K（每条 unabsorbed fact 单独享 ``PER_QUERY_K`` 配额，
            # union dedup 后截到 ``TOTAL_CAP``），不走 max-pool 全局预算。详见
            # ``aretrieve_per_query_topk`` docstring 和 PR #1401 thread：max-pool
            # 在 unabsorbed 主题分散时会让冷门主题挤不进 anchor，per-query 配
            # 额保证每条 unabsorbed 至少能拿自己的近邻。
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
        return (
            "======以下为相关历史背景======\n"
            f"{related_text}\n"
            "（仅供参考，本轮不要为它们单独产出 reflection）\n"
            "======以上为相关历史背景======\n\n"
        )

    async def reflect(self, lanlan_name: str) -> dict | None:
        """Alias for synthesize_reflections. Returns first reflection or None."""
        results = await self.synthesize_reflections(lanlan_name)
        return results[0] if results else None
