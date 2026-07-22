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
"""Promotion methods for the memory manager."""

from __future__ import annotations





from datetime import datetime


from config import (
    EVIDENCE_CONFIRMED_THRESHOLD,
    EVIDENCE_PROMOTED_THRESHOLD,
)

from memory.evidence import evidence_score



from utils.file_utils import (
    robust_json_loads,
)


from utils.token_tracker import set_call_type








from ._shared import (
    logger,
    REFLECTION_TERMINAL_STATUSES,
)

class PromotionMixin:
    def auto_promote_stale(self, lanlan_name: str) -> int:
        """Score-driven pending → confirmed (RFC §3.9.1 / §4.1 PR-1 scope).

        Deprecated sync version — retained for backward-compat callers
        (tests / CLI scripts). Production path is the async twin below.

        - The time-skip branches are deleted (`AUTO_CONFIRM_DAYS` / `AUTO_PROMOTE_DAYS` removed)
        - Only does pending → confirmed: `evidence_score(r, now) >= EVIDENCE_CONFIRMED_THRESHOLD`
        - confirmed → promoted is taken over by PR-3's `_apromote_with_merge`;
          in PR-1 this function carries no promotion responsibility
        Returns number of transitions.
        """
        reflections = self.load_reflections(lanlan_name)
        now = datetime.now()
        transitions = 0
        confirmed_ids: list[str] = []

        for r in reflections:
            if r.get('status') != 'pending':
                continue
            if evidence_score(r, now) < EVIDENCE_CONFIRMED_THRESHOLD:
                continue
            r['status'] = 'confirmed'
            r['confirmed_at'] = now.isoformat()
            confirmed_ids.append(r['id'])
            transitions += 1
            logger.info(
                f"[Reflection] {lanlan_name}: pending→confirmed"
                f" (score driven): {r['text'][:50]}..."
            )

        if transitions:
            self.save_reflections(lanlan_name, reflections)
            if confirmed_ids:
                self._batch_mark_surfaced_handled(
                    lanlan_name, confirmed_ids, 'confirmed',
                )
        return transitions

    async def aauto_promote_stale(self, lanlan_name: str) -> int:
        """P2.a.2: serialized by the character-level asyncio.Lock. Score-driven
        pending → confirmed (§3.9.1) + confirmed → promoted via merge-on-promote
        (RFC §3.9.3, PR-3). The promote pass uses `_apromote_with_merge`,
        which dispatches LLM merge decisions and updates throttle state.

        Returns the number of pending→confirmed transitions (the legacy
        contract). Promote attempts are logged but not folded into the
        return count — they're a separate signal kept in
        `last_promote_attempt_at` / `promote_attempt_count` per reflection.
        """
        async with self._get_alock(lanlan_name):
            transitions = await self._aauto_promote_stale_locked(lanlan_name)
        # Promote pass runs OUTSIDE the per-character lock because each
        # `_apromote_with_merge` re-acquires the lock internally and also
        # acquires the persona lock. Keeping the outer lock here would
        # serialize the LLM call within the lock and block other reflection
        # writes (e.g. arecord_mentions) for the duration of the network
        # round-trip — RFC §3.3.3 outer-async-inner-sync chain breaks down
        # if we hold across the LLM await.
        await self._aauto_promote_score_driven(lanlan_name)
        return transitions

    async def _aauto_promote_score_driven(self, lanlan_name: str) -> int:
        """Score-driven confirmed → promoted (RFC §3.9.1).

        Iterates `confirmed` reflections; for each whose
        `evidence_score >= EVIDENCE_PROMOTED_THRESHOLD`, dispatches to
        `_apromote_with_merge`. Returns the count of attempts (NOT the
        count of successful promotions — the LLM may merge / reject /
        skip per-throttle).

        Skips reflections in `promote_blocked` dead-letter status. Caller
        does NOT need to hold the per-character lock — `_apromote_with_merge`
        re-acquires both reflection and persona locks internally.
        """
        # Read snapshot OUTSIDE the lock — the promote pass tolerates a
        # slightly stale view (any concurrent state change will just mean
        # we attempt promote on a no-longer-confirmed reflection, which
        # the inner lock + status recheck will catch).
        reflections = await self._aload_reflections_full(lanlan_name)
        now = datetime.now()
        attempts = 0
        for r in reflections:
            if r.get('status') != 'confirmed':
                continue
            if evidence_score(r, now) < EVIDENCE_PROMOTED_THRESHOLD:
                continue
            try:
                outcome = await self._apromote_with_merge(lanlan_name, r)
                attempts += 1
                logger.info(
                    f"[Promote] {lanlan_name}/{r.get('id')}: "
                    f"outcome={outcome}"
                )
            except Exception as e:  # noqa: BLE001
                # Per-reflection failures don't sink the loop. The throttle
                # state in the reflection itself gates retries.
                logger.warning(
                    f"[Promote] {lanlan_name}/{r.get('id')}: "
                    f"unhandled error: {e}"
                )
        return attempts

    async def _aauto_promote_stale_locked(self, lanlan_name: str) -> int:
        """Score-driven pending → confirmed only.

        Must run **after** all evidence signals for this tick have been
        applied (signal dispatch emits EVT_REFLECTION_EVIDENCE_UPDATED then
        this loop reads the updated view to decide promotions). The caller
        is responsible for that ordering — see memory_server background loops.
        """
        reflections = await self._aload_reflections_full(lanlan_name)
        now = datetime.now()
        transitions = 0
        confirmed_ids: list[str] = []

        for r in reflections:
            if r.get('status') != 'pending':
                continue
            if evidence_score(r, now) < EVIDENCE_CONFIRMED_THRESHOLD:
                continue
            r['status'] = 'confirmed'
            r['confirmed_at'] = now.isoformat()
            confirmed_ids.append(r['id'])
            transitions += 1
            logger.info(
                f"[Reflection] {lanlan_name}: pending→confirmed"
                f" (score driven): {r['text'][:50]}..."
            )

        if transitions:
            # 写回的集合用 filter 出 active（non-terminal）reflections，匹配
            # aload_reflections 的行为；terminal (archived/merged 等) 条目
            # 由 _prepare_save_reflections 的 merge 逻辑处理。
            active = [
                r for r in reflections
                if r.get('status') not in REFLECTION_TERMINAL_STATUSES
            ]
            await self.asave_reflections(lanlan_name, active)
            if confirmed_ids:
                await self._abatch_mark_surfaced_handled(
                    lanlan_name, confirmed_ids, 'confirmed',
                )
        return transitions

    async def aauto_promote_time_driven(self, lanlan_name: str) -> int:
        """Time-driven fallback for the "strong memory OFF" mode.

        Zero LLM cost. Mimics pre-RFC behavior, advancing the lifecycle purely
        by reflection age:
          - pending for ``WEAK_MEMORY_AUTO_CONFIRM_DAYS`` days (by created_at)
            → status='confirmed', confirmed_at=now, auto_confirmed=True
          - confirmed for ``WEAK_MEMORY_AUTO_PROMOTE_DAYS`` days (by
            confirmed_at) → call ``persona.aadd_fact`` directly via the simple
            merge-in path, **not** the merge LLM. aadd_fact's internal heuristic
            dedup + character-card contradiction check are the safety net.

        Returns: total number of pending→confirmed + confirmed→promoted transitions.

        Mutually exclusive with ``aauto_promote_stale`` — the caller picks one
        based on the strong-memory switch. This method never reads
        evidence_score; missing/zero evidence fields have no effect.
        """
        from config import (
            WEAK_MEMORY_AUTO_CONFIRM_DAYS,
            WEAK_MEMORY_AUTO_PROMOTE_DAYS,
        )

        from memory.persona import PersonaManager
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            now = datetime.now()
            transitions = 0
            confirmed_ids: list[str] = []
            promoted_ids: list[str] = []
            denied_ids: list[str] = []

            # Pass 1: pending → confirmed by created_at age
            for r in reflections:
                if r.get('status') != 'pending':
                    continue
                created_iso = r.get('created_at')
                if not created_iso:
                    continue
                try:
                    created = datetime.fromisoformat(created_iso)
                except (ValueError, TypeError):
                    continue
                age_days = (now - created).total_seconds() / 86400
                if age_days < WEAK_MEMORY_AUTO_CONFIRM_DAYS:
                    continue
                r['status'] = 'confirmed'
                r['confirmed_at'] = now.isoformat()
                r['auto_confirmed'] = True
                rid = r.get('id')
                if rid:
                    confirmed_ids.append(rid)
                transitions += 1
                logger.info(
                    f"[Reflection] {lanlan_name}: pending→confirmed "
                    f"(time-driven, {int(age_days)}d): {r.get('text', '')[:50]}..."
                )

            # Pass 2: confirmed → promoted/denied by confirmed_at age
            # 注：刚被 Pass 1 翻成 confirmed 的 confirmed_at = now，age = 0 不会
            # 命中 14 天阈值——所以同一条 reflection 不会一轮内 pending→promoted。
            for r in reflections:
                if r.get('status') != 'confirmed':
                    continue
                confirmed_iso = r.get('confirmed_at')
                if not confirmed_iso:
                    continue
                try:
                    confirmed_at = datetime.fromisoformat(confirmed_iso)
                except (ValueError, TypeError):
                    continue
                age_days = (now - confirmed_at).total_seconds() / 86400
                if age_days < WEAK_MEMORY_AUTO_PROMOTE_DAYS:
                    continue

                # 直接走 persona.aadd_fact——pre-RFC 简单合入。三种返回：
                #   FACT_ADDED → 把 reflection status 翻到 promoted
                #   FACT_REJECTED_CARD → 翻到 denied (角色卡矛盾)
                #   FACT_QUEUED_CORRECTION → 在强力记忆关时 corrections queue
                #     不会被消化（resolve_corrections gate off），所以这里
                #     reflection 留在 confirmed，下轮再试。属于已知的轻度
                #     "记忆失活" case；rare（启发式 ratio ≥0.4 才命中）。
                rid = r.get('id')
                try:
                    from memory.scopes import subject_from_entry
                    promote_subject = subject_from_entry(r)
                    promote_kwargs = (
                        {'subject': promote_subject}
                        if promote_subject is not None else {}
                    )
                    code = await self._persona_manager.aadd_fact(
                        lanlan_name, r.get('text', ''),
                        entity=r.get('entity', 'relationship'),
                        source='reflection_time_driven',
                        source_id=rid,
                        **promote_kwargs,
                    )
                except Exception as e:
                    logger.warning(
                        f"[Promote] {lanlan_name}/{rid}: time-driven aadd_fact 失败: {e}"
                    )
                    continue

                if code == PersonaManager.FACT_ADDED:
                    r['status'] = 'promoted'
                    r['promoted_at'] = now.isoformat()
                    if rid:
                        promoted_ids.append(rid)
                    transitions += 1
                    logger.info(
                        f"[Reflection] {lanlan_name}: confirmed→promoted "
                        f"(time-driven, {int(age_days)}d, no LLM): "
                        f"{r.get('text', '')[:50]}..."
                    )
                elif code == PersonaManager.FACT_REJECTED_CARD:
                    r['status'] = 'denied'
                    r['denied_reason'] = 'rejected_by_persona_card_time_driven'
                    if rid:
                        denied_ids.append(rid)
                    transitions += 1
                    logger.info(
                        f"[Reflection] {lanlan_name}: confirmed→denied "
                        f"(time-driven, 角色卡矛盾): {rid}"
                    )
                # FACT_QUEUED_CORRECTION → 留在 confirmed，下轮再试

            # 单次落盘：按完整 reflections 列表（含刚被翻成 promoted/denied 的
            # 终态条目）保存。**不过滤 REFLECTION_TERMINAL_STATUSES**——上一
            # 版 bug 是过滤后终态条目变成 inactive，被
            # _prepare_save_reflections 当成 stale 不写主文件，导致 promoted/
            # denied 翻转丢失。asave_reflections 内部 _prepare_save_reflections
            # 会按 _REFLECTION_ARCHIVE_DAYS 自然把陈旧 terminal 移到 archive
            # 分片，不需要这里再做过滤。
            # 任何 transition（confirmed/promoted/denied）都触发 save——只 gate
            # 在 promoted_ids 上会丢掉纯 FACT_REJECTED_CARD 批次的 denied 翻转。
            if transitions:
                await self.asave_reflections(lanlan_name, reflections)
                handled_ids = confirmed_ids + promoted_ids + denied_ids
                if handled_ids:
                    await self._abatch_mark_surfaced_handled(
                        lanlan_name, handled_ids, 'confirmed',
                    )

        return transitions

    async def _abump_reflection_recheck_attempts(
        self, lanlan_name: str, rid: str, reason: str,
    ) -> None:
        """Increment the given reflection's ``recheck_attempts`` counter.

        Once failures reach the ``MEMORY_RECHECK_MAX_ATTEMPTS`` cap, the
        candidates filter excludes the entry so the loop gives its slot to
        other v1 entries. The counter is persisted to the main file and
        survives restarts (so the same bad entry can't keep starving others by
        burning LLM quota).
        Best-effort — save failures don't raise; the next run tries again.
        """
        try:
            async with self._get_alock(lanlan_name):
                current = await self._aload_reflections_full(lanlan_name)
                for r in current:
                    if r.get('id') == rid:
                        r['recheck_attempts'] = (r.get('recheck_attempts') or 0) + 1
                        # 戳失败时刻供 dead-letter 时间自愈（cooldown_elapsed）
                        r['last_recheck_attempt_at'] = datetime.now().isoformat()
                        # 传 active-only：对齐 _abump_refine_attempts / arecord_mentions
                        # 的 save 约定，让 promoted/denied 等 terminal 条目正常归档，
                        # 不被多留一个周期（CodeRabbit 一致性 nitpick）。
                        active = [
                            x for x in current
                            if x.get('status') not in REFLECTION_TERMINAL_STATUSES
                        ]
                        await self.asave_reflections(lanlan_name, active)
                        logger.debug(
                            f"[Recheck-Reflection] {lanlan_name} {rid}: "
                            f"recheck_attempts → {r['recheck_attempts']} ({reason})"
                        )
                        return
        except Exception as e:
            logger.debug(
                f"[Recheck-Reflection] {lanlan_name} {rid}: bump attempts 失败: {e}"
            )

    async def arecheck_one_legacy_reflection(self, lanlan_name: str) -> bool:
        """Schema v1 → v2 slow recheck (processes only 1 entry per call).

        Finds the character's oldest non-archived reflection with
        schema_version < CURRENT, asks the LLM to re-label temporal_scope
        (pattern/state/episode) + event_when (relative offsets); the system
        resolves them into ISO against reflection.created_at and writes them
        back.

        Returns: True when one entry was processed successfully; False when no
        candidate was found or processing failed.
        The caller (memory_server._periodic_slow_memory_recheck_loop) may
        proceed to the next item without sleeping on True (in practice it still
        paces at one per 30s).

        Skip conditions:
        - schema_version >= CURRENT (already upgraded)
        - status in REFLECTION_TERMINAL_STATUSES (archived/merged etc., terminal)
        - already outside the main reflections.json (archive shards aren't
          loaded by load_reflections)
        """
        from config import (
            MEMORY_SCHEMA_VERSION_CURRENT as _SCHEMA_V,
            MEMORY_RECHECK_MAX_ATTEMPTS as _MAX_ATTEMPTS,
            MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS as _SELF_HEAL,
        )
        from config.prompts.prompts_memory import (
            MEMORY_RECHECK_REFLECTION_PROMPT,
        )
        from memory.temporal import (
            normalize_event_when as _norm_when,
            compute_event_timestamps as _compute_ts,
            cooldown_elapsed,
            ACTIVE_TEMPORAL_SCOPES,
        )

        # ── 锁外：选候选 + LLM 调用 ─────────────────────────────────
        reflections = await self.aload_reflections(lanlan_name)
        candidates = [
            r for r in reflections
            if (r.get('schema_version') or 1) < _SCHEMA_V
            and r.get('status') not in REFLECTION_TERMINAL_STATUSES
            # 重试预算：LLM 持续给出无效 temporal_scope 或抛异常的 entry
            # 累计达上限后不再阻塞队列（Codex review on PR #1316 P2 catch）。
            # 时间自愈：达上限的 entry 过 _SELF_HEAL 后放行一次 probe。
            and (
                (r.get('recheck_attempts') or 0) < _MAX_ATTEMPTS
                or cooldown_elapsed(r.get('last_recheck_attempt_at'), _SELF_HEAL)
            )
        ]
        if not candidates:
            return False
        # 最老优先（FIFO 迁移），id 兜底排序保稳定
        candidates.sort(key=lambda r: (r.get('created_at', ''), r.get('id', '')))
        # Skip malformed candidates (missing id / created_at) instead of
        # aborting the whole call — otherwise a single bad legacy entry at
        # head of FIFO order would starve every later v1 reflection forever
        # (Codex review on PR #1316 P2 catch).
        target: dict | None = None
        rid = ''
        created_at_iso = ''
        for c in candidates:
            cid = c.get('id')
            cts = c.get('created_at', '')
            if not cid or not cts:
                logger.debug(
                    f"[Recheck-Reflection] {lanlan_name}: skip malformed legacy "
                    f"reflection (id={cid!r} created_at={cts!r})"
                )
                continue
            target = c
            rid = cid
            created_at_iso = cts
            break
        if target is None:
            return False

        # 拉 source facts 上下文（最多 5 条文本，太多 prompt 太长）
        source_fact_ids = target.get('source_fact_ids') or []
        source_facts_text = ""
        if source_fact_ids:
            try:
                all_facts = await self._fact_store.aload_facts(lanlan_name)
                fact_by_id = {f.get('id'): f for f in all_facts}
                lines = []
                for fid in source_fact_ids[:5]:
                    f = fact_by_id.get(fid)
                    if f and f.get('text'):
                        lines.append(f"- {f['text']}")
                source_facts_text = "\n".join(lines) if lines else "（无）"
            except Exception:
                source_facts_text = "（无）"
        else:
            source_facts_text = "（无）"

        prompt = MEMORY_RECHECK_REFLECTION_PROMPT.format(
            REFLECTION_TEXT=target.get('text', ''),
            CREATED_AT=created_at_iso,
            SOURCE_FACTS=source_facts_text,
        )

        failure_reason: str | None = None
        new_scope: str | None = None
        event_when_raw: dict | None = None
        try:
            from utils.llm_client import create_chat_llm_async
            set_call_type("memory_recheck_reflection")
            api_config = self._config_manager.get_model_api_config('summary')
            from config import LLM_OUTPUT_GUARD_MAX_TOKENS
            llm = await create_chat_llm_async(
                api_config['model'],
                api_config['base_url'], api_config['api_key'],
                timeout=60, max_retries=0,
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
                failure_reason = "non-dict response"
            else:
                new_scope = result.get('temporal_scope')
                if new_scope not in ACTIVE_TEMPORAL_SCOPES:
                    failure_reason = f"invalid temporal_scope={new_scope!r}"
                else:
                    event_when_raw = _norm_when(result.get('event_when'))
        except Exception as e:
            failure_reason = f"LLM call failed: {e}"

        # 失败路径统一收口：bump recheck_attempts 计数器，让连续失败的 entry
        # 在达到 MAX 后被 candidates filter 排除（Codex review on PR #1316 P2）
        if failure_reason is not None:
            logger.debug(
                f"[Recheck-Reflection] {lanlan_name} {rid}: 跳过本轮 ({failure_reason})"
            )
            await self._abump_reflection_recheck_attempts(lanlan_name, rid, failure_reason)
            return False

        # 用 created_at 当锚点解算 ISO
        _needs_end = new_scope in ('state', 'episode')
        event_start_at, event_end_at = _compute_ts(
            event_when_raw,
            created_at_iso,
            fallback_start=True,
            fallback_end=_needs_end,
        )

        # ── 锁内：reload + 找同 id + 更新字段 + save ──────────────
        save_failed_reason: str | None = None
        async with self._get_alock(lanlan_name):
            current = await self._aload_reflections_full(lanlan_name)
            found = None
            for r in current:
                if r.get('id') == rid:
                    found = r
                    break
            if found is None:
                logger.debug(f"[Recheck-Reflection] {lanlan_name} {rid}: 锁内已不存在")
                return False
            # 再次检查 schema_version（防并发重复处理）
            if (found.get('schema_version') or 1) >= _SCHEMA_V:
                return False
            found['temporal_scope'] = new_scope
            found['event_when_raw'] = event_when_raw
            found['event_start_at'] = event_start_at
            found['event_end_at'] = event_end_at
            found['schema_version'] = _SCHEMA_V
            try:
                await self.asave_reflections(lanlan_name, current)
            except Exception as e:
                # 落盘失败也要计入 recheck_attempts：否则 cloudsave 维护态 /
                # 只读 FS / 权限导致的持续写盘失败会让同一条 reflection 每 30s
                # 原样重判、熔断永不触发（对齐上面 LLM 失败路径的 bump）。
                # bump helper 自取锁，asyncio.Lock 不可重入，必须退出本锁后再调。
                save_failed_reason = f"save failed: {e}"

        if save_failed_reason is not None:
            logger.warning(
                f"[Recheck-Reflection] {lanlan_name} {rid}: {save_failed_reason}"
            )
            await self._abump_reflection_recheck_attempts(
                lanlan_name, rid, save_failed_reason,
            )
            return False

        logger.info(
            f"[Recheck-Reflection] {lanlan_name} {rid}: v1→v{_SCHEMA_V} "
            f"scope={new_scope} when={event_when_raw}"
        )
        return True

    async def areset_confirmed_at_to_now(self, lanlan_name: str) -> int:
        """On→off migration: reset every confirmed reflection's confirmed_at to now,
        making the time-driven fallback run the full 14-day clock.

        Avoids the jarring experience of "user enabled it for a while, turned
        it off, and old confirmed entries immediately hit the 14-day mark and
        promote in bulk". Returns the number of affected entries.
        """
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            now_iso = datetime.now().isoformat()
            count = 0
            for r in reflections:
                if r.get('status') == 'confirmed':
                    r['confirmed_at'] = now_iso
                    count += 1
            if count:
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(lanlan_name, active)
        return count
