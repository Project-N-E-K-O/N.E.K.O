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
"""Surfacing methods for the memory manager."""

from __future__ import annotations





from datetime import datetime, timedelta



from memory.evidence import evidence_score



from utils.file_utils import (
    robust_json_loads,
)


from utils.token_tracker import set_call_type

from memory.persona import (
    SUPPRESS_COOLDOWN_HOURS,
    SUPPRESS_MENTION_LIMIT,
    SUPPRESS_WINDOW_HOURS,
    _is_mentioned,
)

from memory.stop_names import acollect_stop_names




from memory._reflection.selection import (
    filter_active_confirmed,
    filter_followup_candidates,
    followup_render_key,
    in_window,
    record_mentions,
    update_suppressions,
)

from memory._reflection.transitions import (
    apply_batch_mark,
    apply_mark_surfaced_handled,
    apply_promotion_status,
)

from ._shared import (
    logger,
    REFLECTION_TERMINAL_STATUSES,
    REFLECTION_COOLDOWN_MINUTES,
)

class SurfacingMixin:
    @staticmethod
    def _in_window(ts_str: str, cutoff: datetime) -> bool:
        return in_window(ts_str, cutoff)

    @classmethod
    def _apply_record_reflection_mentions(
        cls,
        reflections: list[dict],
        response_text: str,
        stop_names: list[str] | None = None,
    ) -> bool:
        """When the AI response mentions any **confirmed** reflection's text →
        increment recent_mentions. Pending reflections are by design "the AI
        probing proactively"; suppressing them would break the mechanism — so
        only confirmed ones are scanned. Semantics aligned with
        persona._apply_record_mentions.

        ``stop_names`` are stripped before ``_is_mentioned``, so the
        ever-present master/lanlan + nicknames don't get unrelated reflections
        judged as mentioned.
        """
        return record_mentions(
            reflections,
            response_text,
            stop_names=stop_names,
            now=datetime.now(),
            window_hours=SUPPRESS_WINDOW_HOURS,
            mention_limit=SUPPRESS_MENTION_LIMIT,
            is_mentioned=_is_mentioned,
            is_in_window=cls._in_window,
        )

    @classmethod
    def _apply_update_reflection_suppressions(
        cls, reflections: list[dict],
    ) -> bool:
        def _log_bad_timestamp(value: str, error: Exception) -> None:
            logger.debug(
                f"[Reflection] suppressed_at 解析失败 ({value!r}): {error}"
            )

        return update_suppressions(
            reflections,
            now=datetime.now(),
            window_hours=SUPPRESS_WINDOW_HOURS,
            cooldown_hours=SUPPRESS_COOLDOWN_HOURS,
            on_bad_timestamp=_log_bad_timestamp,
            is_in_window=cls._in_window,
        )

    async def arecord_mentions(
        self, lanlan_name: str, response_text: str, *, subjects=None,
        include_legacy_private: bool | None = None,
    ) -> None:
        """After the AI finishes a reply, scan confirmed reflections and accumulate
        mentions within the 5h window. Mentioned more than
        SUPPRESS_MENTION_LIMIT (=2) times in a row → stamp suppress=True.
        """
        if not response_text:
            return
        stop_names = await acollect_stop_names(self._config_manager, lanlan_name)
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            from memory.scopes import filter_entries_for_subjects
            mention_candidates = filter_entries_for_subjects(
                reflections,
                subjects,
                include_legacy_private=include_legacy_private,
            )
            if self._apply_record_reflection_mentions(
                mention_candidates, response_text, stop_names=stop_names,
            ):
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(lanlan_name, active)

    async def aupdate_suppressions(self, lanlan_name: str) -> None:
        """Refresh suppress states before render: cooldown elapsed → lift; prune recent_mentions outside the window."""
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            if self._apply_update_reflection_suppressions(reflections):
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(lanlan_name, active)

    def get_pending_reflections(
        self, lanlan_name: str, *, subjects=None,
        include_legacy_private: bool | None = None,
    ) -> list[dict]:
        """Get all pending (unconfirmed) reflections."""
        reflections = self.load_reflections(lanlan_name)
        from memory.scopes import filter_entries_for_subjects
        return filter_entries_for_subjects(
            [r for r in reflections if r.get('status') == 'pending'],
            subjects,
            include_legacy_private=include_legacy_private,
        )

    async def aget_pending_reflections(
        self, lanlan_name: str, *, subjects=None,
        include_legacy_private: bool | None = None,
    ) -> list[dict]:
        reflections = await self.aload_reflections(lanlan_name)
        from memory.scopes import filter_entries_for_subjects
        return filter_entries_for_subjects(
            [r for r in reflections if r.get('status') == 'pending'],
            subjects,
            include_legacy_private=include_legacy_private,
        )

    @staticmethod
    def _filter_active_confirmed(
        reflections: list[dict], now: datetime | None = None,
    ) -> list[dict]:
        """Active confirmed = status='confirmed' AND score > 0 AND not suppressed.

        score <= 0: the user denied it repeatedly or it nets out to zero; it
        should neither enter the render's "fairly certain impressions" section
        (semantic drift), and it also ticks the background loop's archive
        counter (§3.5).
        suppress=True: the AI just mentioned this too often within the 5h
        window; silenced by persona's same mechanism (§2.6, orthogonal).
        """
        return filter_active_confirmed(
            reflections,
            now=now or datetime.now(),
            score=evidence_score,
        )

    def get_confirmed_reflections(
        self, lanlan_name: str, *, subjects=None,
        include_legacy_private: bool | None = None,
    ) -> list[dict]:
        """Get all confirmed (soft persona) reflections that are still
        active — status='confirmed' AND score > 0 AND not mention-suppressed."""
        from memory.scopes import filter_entries_for_subjects
        return filter_entries_for_subjects(
            self._filter_active_confirmed(self.load_reflections(lanlan_name)),
            subjects,
            include_legacy_private=include_legacy_private,
        )

    async def aget_confirmed_reflections(
        self, lanlan_name: str, *, subjects=None,
        include_legacy_private: bool | None = None,
    ) -> list[dict]:
        from memory.scopes import filter_entries_for_subjects
        return filter_entries_for_subjects(
            self._filter_active_confirmed(
                await self.aload_reflections(lanlan_name),
            ),
            subjects,
            include_legacy_private=include_legacy_private,
        )

    @staticmethod
    def _followup_render_key(value) -> str:
        """Return the text key used when deciding whether a followup can render.

        Keep this local instead of importing main_logic.topic.common.clean_text:
        memory is a lower layer and should not depend on prompt-rendering code.
        """
        return followup_render_key(value)

    @staticmethod
    def _filter_followup_candidates(pending: list[dict]) -> list[dict]:
        """Filter pending reflections for proactive chat candidacy.

        RFC §3.8.6 adds an `evidence_score >= 0` gate on top of the existing
        `next_eligible_at` cooldown. A reflection with score < 0 is
        "coldshouldered" by user signals but not yet archived — it stays in
        `reflections.json` but is skipped from active selection.

        Note: we intentionally DO NOT gate on the CONFIRMED_THRESHOLD upper
        bound — a pending reflection whose score has crossed into the
        derived-confirmed range is still a valid followup candidate. AI
        picking it up gives user a natural chance to re-affirm (or push
        back) before the periodic loop finally flips the stored status.

        Sampling: when the candidate pool > TOP_K, draw a weighted random sample
        without replacement by `evidence_score + WEIGHT_BASE`
        (Efraimidis-Spirakis), avoiding always picking the same top-K batch and
        making proactive chats repetitive. WEIGHT_BASE gives the "fresh,
        unsignaled" score=0 entries a minimum weight; otherwise an
        all-zero-score pool would degenerate into an empty set.
        `REFLECTION_FOLLOWUP_WEIGHTED=False` reverts to the old "first K in
        list order" behavior (for tests / debugging).
        """
        from config import (
            REFLECTION_SURFACE_TOP_K,
            REFLECTION_FOLLOWUP_WEIGHTED,
            REFLECTION_FOLLOWUP_WEIGHT_BASE,
        )
        from memory.temporal import weighted_sample_no_replace

        return filter_followup_candidates(
            pending,
            now=datetime.now(),
            score=evidence_score,
            render_key=SurfacingMixin._followup_render_key,
            weighted=REFLECTION_FOLLOWUP_WEIGHTED,
            top_k=REFLECTION_SURFACE_TOP_K,
            weight_base=REFLECTION_FOLLOWUP_WEIGHT_BASE,
            sample=weighted_sample_no_replace,
        )

    def get_followup_topics(self, lanlan_name: str) -> list[dict]:
        """Get pending reflections suitable for natural mention in proactive chat.

        Returns candidates that have passed their cooldown period.
        Does NOT persist anything — call record_surfaced() after reply is sent.
        """
        return self._filter_followup_candidates(self.get_pending_reflections(lanlan_name))

    async def aget_followup_topics(self, lanlan_name: str) -> list[dict]:
        pending = await self.aget_pending_reflections(lanlan_name)
        return self._filter_followup_candidates(pending)

    def _apply_record_surfaced(
        self, reflection_ids: list[str], reflections: list[dict], surfaced: list[dict],
    ) -> tuple[bool, list[dict]]:
        now = datetime.now()
        now_str = now.isoformat()
        next_eligible = (now + timedelta(minutes=REFLECTION_COOLDOWN_MINUTES)).isoformat()

        id_to_text = {r['id']: r.get('text', '') for r in reflections}
        cooldown_changed = False
        for r in reflections:
            if r.get('id') in reflection_ids:
                r['next_eligible_at'] = next_eligible
                cooldown_changed = True

        for rid in reflection_ids:
            found = False
            for s in surfaced:
                if s.get('reflection_id') == rid:
                    s['surfaced_at'] = now_str
                    s['text'] = id_to_text.get(rid, s.get('text', ''))
                    s['feedback'] = None
                    found = True
                    break
            if not found:
                surfaced.append({
                    'reflection_id': rid,
                    'text': id_to_text.get(rid, ''),
                    'surfaced_at': now_str,
                    'feedback': None,
                })
        return cooldown_changed, surfaced

    def record_surfaced(self, lanlan_name: str, reflection_ids: list[str]) -> None:
        """Record which reflections were actually mentioned in proactive chat.

        Called AFTER the reply is sent, not during candidate selection.
        Also refreshes the cooldown on surfaced reflections.
        """
        if not reflection_ids:
            return
        surfaced = self.load_surfaced(lanlan_name)
        reflections = self.load_reflections(lanlan_name)
        cooldown_changed, surfaced = self._apply_record_surfaced(
            reflection_ids, reflections, surfaced,
        )
        if cooldown_changed:
            self.save_reflections(lanlan_name, reflections)
        self.save_surfaced(lanlan_name, surfaced)

    async def arecord_surfaced(self, lanlan_name: str, reflection_ids: list[str]) -> None:
        """P2.a.2: per-character asyncio.Lock serializes reflections.json /
        surfaced.json writes, avoiding races with synth / promote."""
        if not reflection_ids:
            return
        async with self._get_alock(lanlan_name):
            surfaced = await self.aload_surfaced(lanlan_name)
            reflections = await self.aload_reflections(lanlan_name)
            cooldown_changed, surfaced = self._apply_record_surfaced(
                reflection_ids, reflections, surfaced,
            )
            if cooldown_changed:
                await self.asave_reflections(lanlan_name, reflections)
            await self.asave_surfaced(lanlan_name, surfaced)

    async def check_feedback(self, lanlan_name: str, user_messages: list[str]) -> list[dict] | None:
        """Check if user's recent messages confirm/deny surfaced reflections.

        Returns list of {reflection_id, feedback} dicts, or None on LLM/processing failure.

        P2.a.2: this method writes back surfaced.json (line 572), so it must be
        serialized under the character lock with arecord_surfaced /
        aconfirm_promotion / areject_promotion.
        """
        async with self._get_alock(lanlan_name):
            return await self._check_feedback_locked(lanlan_name, user_messages)

    async def _check_feedback_locked(self, lanlan_name: str, user_messages: list[str]) -> list[dict] | None:
        from config.prompts.prompts_memory import get_reflection_feedback_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm_async

        surfaced = await self.aload_surfaced(lanlan_name)
        pending_surfaced = [s for s in surfaced if s.get('feedback') is None]
        if not pending_surfaced:
            return []

        reflections_text = "\n".join(
            f"- [{s['reflection_id']}] {s['text']}" for s in pending_surfaced
        )
        messages_text = "\n".join(user_messages)

        prompt = get_reflection_feedback_prompt(get_global_language()).format(
            reflections=reflections_text,
            messages=messages_text,
        )

        try:
            set_call_type("memory_feedback_check")
            api_config = self._config_manager.get_model_api_config('summary')
            # timeout=60: 后台 task 内调用，二分类任务 prompt + 输出都不大。
            # max_retries=0: 禁 SDK 自动重试。
            from config import LLM_OUTPUT_GUARD_MAX_TOKENS
            llm = await create_chat_llm_async(
                api_config['model'],
                api_config['base_url'], api_config['api_key'],
                timeout=60, max_retries=0,
                max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length JSON isn't truncated
                provider_type=api_config.get('provider_type'),
            )
            try:
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # prompt assembled from token-capped memory components (REFLECTION_*/RECALL_* budgets in the prompt builder).
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            feedbacks = robust_json_loads(raw)
            if not isinstance(feedbacks, list):
                feedbacks = [feedbacks]
        except Exception as e:
            logger.warning(f"[Reflection] 反馈检查失败: {e}")
            return None  # 区别于 []（无反馈），None 表示调用失败

        # Update surfaced records (whitelist valid feedback values)
        _VALID_FEEDBACK = {'confirmed', 'denied', 'ignored'}
        for fb in feedbacks:
            if not isinstance(fb, dict):
                continue
            rid = fb.get('reflection_id')
            feedback = fb.get('feedback')
            if rid and feedback in _VALID_FEEDBACK:
                for s in surfaced:
                    if s.get('reflection_id') == rid:
                        s['feedback'] = feedback
        await self.asave_surfaced(lanlan_name, surfaced)

        return feedbacks

    async def check_feedback_for_confirmed(
        self, lanlan_name: str, confirmed: list[dict], user_messages: list[str],
    ) -> list[dict] | None:
        """Check if recent user messages rebut any confirmed reflections.

        Used by periodic rebuttal check (every 5 min). Only returns 'denied' or 'ignored'.
        Returns None on LLM/processing failure (same convention as check_feedback).
        """
        from config.prompts.prompts_memory import get_reflection_feedback_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm_async

        if not confirmed or not user_messages:
            return []

        reflections_text = "\n".join(
            f"- [{r['id']}] {r['text']}" for r in confirmed
        )
        messages_text = "\n".join(user_messages)

        prompt = get_reflection_feedback_prompt(get_global_language()).format(
            reflections=reflections_text,
            messages=messages_text,
        )

        try:
            set_call_type("memory_rebuttal_check")
            api_config = self._config_manager.get_model_api_config('summary')
            # timeout=90: 开 thinking 后判断"用户最近的话否定了哪条 confirmed
            # reflection"——drain 模式下每批最多 20 条 user msg × 多条
            # confirmed reflection，思考能改善误判（防止把 user 的反讽 / 情景
            # 转换误标为否定）。完全后台无锁，没人等结果，安全开 thinking。
            # max_retries=0: 禁 SDK 自动重试，失败 cursor 不推进自然下轮重试。
            # extra_body=None: 显式开 thinking。
            from config import LLM_OUTPUT_GUARD_MAX_TOKENS
            llm = await create_chat_llm_async(
                api_config['model'],
                api_config['base_url'], api_config['api_key'],
                timeout=90, max_retries=0,
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
            feedbacks = robust_json_loads(raw)
            if not isinstance(feedbacks, list):
                feedbacks = [feedbacks]
            return feedbacks
        except Exception as e:
            logger.warning(f"[Reflection] 反驳检查失败: {e}")
            return None

    @staticmethod
    def _apply_promotion_status(
        reflections: list[dict], reflection_id: str, status: str,
    ) -> str | None:
        return apply_promotion_status(
            reflections, reflection_id, status, now=datetime.now(),
        )

    def confirm_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        """Mark reflection as confirmed (soft persona). Does NOT write to persona yet.

        Confirmed reflections exist independently for AUTO_PROMOTE_DAYS days,
        during which they can still be rebutted. After that, auto_promote_stale()
        upgrades them to real persona entries.
        """
        reflections = self.load_reflections(lanlan_name)
        text = self._apply_promotion_status(reflections, reflection_id, 'confirmed')
        if text is not None:
            logger.info(f"[Reflection] {lanlan_name}: 反思已确认(软persona) id={reflection_id} len={len(text)}")
            print(f"[Reflection] {lanlan_name}: 反思已确认(软persona): {text[:50]}...")
        self.save_reflections(lanlan_name, reflections)
        self._mark_surfaced_handled(lanlan_name, reflection_id, 'confirmed')

    async def aconfirm_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        async with self._get_alock(lanlan_name):
            reflections = await self.aload_reflections(lanlan_name)
            text = self._apply_promotion_status(reflections, reflection_id, 'confirmed')
            if text is not None:
                logger.info(f"[Reflection] {lanlan_name}: 反思已确认(软persona) id={reflection_id} len={len(text)}")
                print(f"[Reflection] {lanlan_name}: 反思已确认(软persona): {text[:50]}...")
            await self.asave_reflections(lanlan_name, reflections)
            await self._amark_surfaced_handled(lanlan_name, reflection_id, 'confirmed')

    def reject_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        """Mark a reflection as denied — won't be promoted."""
        reflections = self.load_reflections(lanlan_name)
        text = self._apply_promotion_status(reflections, reflection_id, 'denied')
        if text is not None:
            logger.info(f"[Reflection] {lanlan_name}: 反思被否定 id={reflection_id} len={len(text)}")
            print(f"[Reflection] {lanlan_name}: 反思被否定: {text[:50]}...")
        self.save_reflections(lanlan_name, reflections)
        self._mark_surfaced_handled(lanlan_name, reflection_id, 'denied')

    async def areject_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        async with self._get_alock(lanlan_name):
            reflections = await self.aload_reflections(lanlan_name)
            text = self._apply_promotion_status(reflections, reflection_id, 'denied')
            if text is not None:
                logger.info(f"[Reflection] {lanlan_name}: 反思被否定 id={reflection_id} len={len(text)}")
                print(f"[Reflection] {lanlan_name}: 反思被否定: {text[:50]}...")
            await self.asave_reflections(lanlan_name, reflections)
            await self._amark_surfaced_handled(lanlan_name, reflection_id, 'denied')

    @staticmethod
    def _apply_mark_surfaced_handled(
        surfaced: list[dict], reflection_id: str, feedback: str,
    ) -> bool:
        return apply_mark_surfaced_handled(
            surfaced, reflection_id, feedback, now=datetime.now(),
        )

    def _mark_surfaced_handled(self, lanlan_name: str, reflection_id: str, feedback: str) -> None:
        """Mark surfaced record as handled so check_feedback won't reprocess it."""
        surfaced = self.load_surfaced(lanlan_name)
        if self._apply_mark_surfaced_handled(surfaced, reflection_id, feedback):
            self.save_surfaced(lanlan_name, surfaced)

    async def _amark_surfaced_handled(
        self, lanlan_name: str, reflection_id: str, feedback: str,
    ) -> None:
        surfaced = await self.aload_surfaced(lanlan_name)
        if self._apply_mark_surfaced_handled(surfaced, reflection_id, feedback):
            await self.asave_surfaced(lanlan_name, surfaced)

    def _apply_batch_mark(
        self, surfaced: list[dict], reflection_ids: list[str], feedback: str,
    ) -> bool:
        return apply_batch_mark(
            surfaced,
            reflection_ids,
            feedback,
            upgradable_feedback=self._UPGRADABLE_FEEDBACK,
            now=datetime.now(),
        )

    def _batch_mark_surfaced_handled(
        self, lanlan_name: str, reflection_ids: list[str], feedback: str,
    ) -> None:
        """Mark multiple surfaced records as handled in a single I/O round-trip.

        Allows transitions from None/confirmed/auto_confirmed to the new feedback value,
        so that promoted can overwrite confirmed/auto_confirmed.
        """
        if not reflection_ids:
            return
        surfaced = self.load_surfaced(lanlan_name)
        if self._apply_batch_mark(surfaced, reflection_ids, feedback):
            self.save_surfaced(lanlan_name, surfaced)

    async def _abatch_mark_surfaced_handled(
        self, lanlan_name: str, reflection_ids: list[str], feedback: str,
    ) -> None:
        if not reflection_ids:
            return
        surfaced = await self.aload_surfaced(lanlan_name)
        if self._apply_batch_mark(surfaced, reflection_ids, feedback):
            await self.asave_surfaced(lanlan_name, surfaced)
