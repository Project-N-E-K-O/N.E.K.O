# -*- coding: utf-8 -*-
"""
ReflectionEngine — Tier 2 of the three-tier memory hierarchy.

Synthesizes multiple Tier-1 facts into higher-level reflections (insights).
Reflections start as "pending" and require feedback confirmation before
being promoted to persona (Tier 3).

Cognitive flow:
  Facts(passive) → Reflection(active thinking) → Persona(confirmed & solidified)

Trigger: called during proactive chat (主动搭话), NOT during every conversation.
This allows reflection to double as a "callback" mechanism where the AI naturally
mentions its observations and gauges the user's response.

Auto-promotion: pending reflections that remain 3 days without denial are
automatically promoted to persona.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from config import SETTING_PROPOSER_MODEL
from utils.config_manager import get_config_manager
from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from memory.persona import PersonaManager

if TYPE_CHECKING:
    from memory.facts import FactStore
    from memory.persona import PersonaManager

logger = get_module_logger(__name__, "Memory")

# Minimum unabsorbed facts to trigger reflection synthesis
MIN_FACTS_FOR_REFLECTION = 5
# Days without denial → auto state transition
AUTO_CONFIRM_DAYS = 3       # pending → confirmed
AUTO_PROMOTE_DAYS = 3       # confirmed → promoted (persona)
# Cooldown between proactive chat candidacy
REFLECTION_COOLDOWN_MINUTES = 30
# promoted/denied reflections older than this are moved to archive
_REFLECTION_ARCHIVE_DAYS = 30


class ReflectionEngine:
    """Synthesizes facts into reflections and manages the pending → confirmed lifecycle."""

    def __init__(self, fact_store: FactStore, persona_manager: PersonaManager):
        self._config_manager = get_config_manager()
        self._fact_store = fact_store
        self._persona_manager = persona_manager

    # ── file paths ───────────────────────────────────────────────────

    def _reflections_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'reflections.json')

    def _reflections_archive_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'reflections_archive.json')

    def _surfaced_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'surfaced.json')

    # ── persistence ──────────────────────────────────────────────────

    @staticmethod
    def _filter_reflections(data, include_archived: bool, path: str) -> list[dict]:
        if not isinstance(data, list):
            logger.warning(f"[Reflection] reflections 文件不是列表，忽略: {path}")
            return []
        items = [item for item in data if isinstance(item, dict) and 'id' in item]
        if not include_archived:
            items = [r for r in items if r.get('status') not in ('promoted', 'denied')]
        return items

    def load_reflections(self, name: str, include_archived: bool = False) -> list[dict]:
        path = self._reflections_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                return self._filter_reflections(data, include_archived, path)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] 加载失败: {e}")
        return []

    async def aload_reflections(self, name: str, include_archived: bool = False) -> list[dict]:
        path = self._reflections_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Reflection] 加载失败: {e}")
            return []
        return self._filter_reflections(data, include_archived, path)

    def _prepare_save_reflections(
        self, name: str, reflections: list[dict], all_on_disk: list[dict],
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Pure logic: compute (merged_main, to_archive, keep_in_main)."""
        active_ids = {r['id'] for r in reflections if 'id' in r}
        finished = [r for r in all_on_disk if r.get('id') not in active_ids
                    and r.get('status') in ('promoted', 'denied')]
        cutoff = datetime.now() - timedelta(days=_REFLECTION_ARCHIVE_DAYS)
        keep_in_main, to_archive = [], []
        for r in finished:
            ts_key = r.get('promoted_at') or r.get('denied_at') or r.get('created_at', '')
            try:
                if datetime.fromisoformat(ts_key) < cutoff:
                    to_archive.append(r)
                    continue
            except (ValueError, TypeError):
                # 时间戳缺失/格式异常：不归档，落回 main 保守保留
                pass
            keep_in_main.append(r)
        merged = reflections + keep_in_main
        return merged, to_archive, keep_in_main

    def save_reflections(self, name: str, reflections: list[dict]) -> None:
        """Save reflections, merging with archived entries on disk.

        promoted/denied 超过 _REFLECTION_ARCHIVE_DAYS 的条目自动移入归档文件。
        """
        path = self._reflections_path(name)
        all_on_disk = []
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    all_on_disk = [r for r in data if isinstance(r, dict)]
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] {name}: 读取现有 reflections 失败，中止保存以保护归档数据: {e}")
                return

        merged, to_archive, _ = self._prepare_save_reflections(name, reflections, all_on_disk)

        if to_archive:
            archive_path = self._reflections_archive_path(name)
            existing: list[dict] = []
            if os.path.exists(archive_path):
                try:
                    with open(archive_path, encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        existing = data
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"[Reflection] {name}: 读取归档文件失败，跳过本次归档: {e}")
                    merged = merged + to_archive
                    to_archive = []
            if to_archive:
                existing.extend(to_archive)
                atomic_write_json(archive_path, existing, indent=2, ensure_ascii=False)
                logger.info(f"[Reflection] {name}: 归档 {len(to_archive)} 条旧 reflections")

        atomic_write_json(path, merged, indent=2, ensure_ascii=False)

    async def asave_reflections(self, name: str, reflections: list[dict]) -> None:
        path = self._reflections_path(name)
        all_on_disk = []
        if await asyncio.to_thread(os.path.exists, path):
            try:
                data = await read_json_async(path)
                if isinstance(data, list):
                    all_on_disk = [r for r in data if isinstance(r, dict)]
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] {name}: 读取现有 reflections 失败，中止保存以保护归档数据: {e}")
                return

        merged, to_archive, _ = self._prepare_save_reflections(name, reflections, all_on_disk)

        if to_archive:
            archive_path = self._reflections_archive_path(name)
            existing: list[dict] = []
            if await asyncio.to_thread(os.path.exists, archive_path):
                try:
                    data = await read_json_async(archive_path)
                    if isinstance(data, list):
                        existing = data
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"[Reflection] {name}: 读取归档文件失败，跳过本次归档: {e}")
                    merged = merged + to_archive
                    to_archive = []
            if to_archive:
                existing.extend(to_archive)
                await atomic_write_json_async(archive_path, existing, indent=2, ensure_ascii=False)
                logger.info(f"[Reflection] {name}: 归档 {len(to_archive)} 条旧 reflections")

        await atomic_write_json_async(path, merged, indent=2, ensure_ascii=False)

    def load_surfaced(self, name: str) -> list[dict]:
        """Load the list of reflections that were surfaced in proactive chat."""
        path = self._surfaced_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
                logger.warning(f"[Reflection] surfaced 文件不是列表，忽略: {path}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] 加载 surfaced 失败: {e}")
        return []

    async def aload_surfaced(self, name: str) -> list[dict]:
        path = self._surfaced_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Reflection] 加载 surfaced 失败: {e}")
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        logger.warning(f"[Reflection] surfaced 文件不是列表，忽略: {path}")
        return []

    def save_surfaced(self, name: str, surfaced: list[dict]) -> None:
        atomic_write_json(self._surfaced_path(name), surfaced, indent=2, ensure_ascii=False)

    async def asave_surfaced(self, name: str, surfaced: list[dict]) -> None:
        await atomic_write_json_async(self._surfaced_path(name), surfaced, indent=2, ensure_ascii=False)

    # ── synthesis ────────────────────────────────────────────────────

    async def synthesize_reflections(self, lanlan_name: str) -> list[dict]:
        """Synthesize pending reflections from accumulated unabsorbed facts.

        Called during proactive chat. Returns newly created reflections.
        """
        from config.prompts_memory import get_reflection_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm

        unabsorbed = await self._fact_store.aget_unabsorbed_facts(lanlan_name)
        if len(unabsorbed) < MIN_FACTS_FOR_REFLECTION:
            return []

        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        master_name = name_mapping.get('human', '主人')

        facts_text = "\n".join(f"- {f['text']} (importance: {f.get('importance', 5)})" for f in unabsorbed)
        reflection_prompt = get_reflection_prompt(get_global_language())
        prompt = reflection_prompt.replace('{FACTS}', facts_text)
        prompt = prompt.replace('{LANLAN_NAME}', lanlan_name)
        prompt = prompt.replace('{MASTER_NAME}', master_name)

        try:
            set_call_type("memory_reflection")
            api_config = self._config_manager.get_model_api_config('summary')
            llm = create_chat_llm(
                api_config.get('model', SETTING_PROPOSER_MODEL),
                api_config['base_url'], api_config['api_key'],
                temperature=0.5,
            )
            try:
                resp = await llm.ainvoke(prompt)
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            result = robust_json_loads(raw)
            if not isinstance(result, dict):
                logger.warning(f"[Reflection] LLM 返回非 dict: {type(result)}")
                return []
            reflection_text = result.get('reflection', '')
            if not isinstance(reflection_text, str):
                logger.warning(f"[Reflection] reflection 字段非 str: {type(reflection_text)}")
                return []
            reflection_text = reflection_text.strip()
            reflection_entity = result.get('entity', 'relationship')
            if reflection_entity not in ('master', 'neko', 'relationship'):
                reflection_entity = 'relationship'
        except Exception as e:
            logger.warning(f"[Reflection] 合成失败: {e}")
            return []

        if not reflection_text:
            return []

        # Create pending reflection
        now = datetime.now()
        reflection = {
            'id': f"ref_{now.strftime('%Y%m%d%H%M%S')}",
            'text': reflection_text,
            'entity': reflection_entity,
            'status': 'pending',  # pending | confirmed | denied | promoted | archived
            'source_fact_ids': [f['id'] for f in unabsorbed],
            'created_at': now.isoformat(),
            'feedback': None,
            'next_eligible_at': (now + timedelta(minutes=REFLECTION_COOLDOWN_MINUTES)).isoformat(),
        }

        reflections = await self.aload_reflections(lanlan_name)
        reflections.append(reflection)
        await self.asave_reflections(lanlan_name, reflections)

        # Mark source facts as absorbed
        await self._fact_store.amark_absorbed(lanlan_name, reflection['source_fact_ids'])

        logger.info(f"[Reflection] {lanlan_name}: 合成了新反思: {reflection_text[:50]}...")
        return [reflection]

    # alias for backward compat (system_router calls .reflect())
    async def reflect(self, lanlan_name: str) -> dict | None:
        """Alias for synthesize_reflections. Returns first reflection or None."""
        results = await self.synthesize_reflections(lanlan_name)
        return results[0] if results else None

    # ── feedback lifecycle ───────────────────────────────────────────

    def get_pending_reflections(self, lanlan_name: str) -> list[dict]:
        """Get all pending (unconfirmed) reflections."""
        reflections = self.load_reflections(lanlan_name)
        return [r for r in reflections if r.get('status') == 'pending']

    async def aget_pending_reflections(self, lanlan_name: str) -> list[dict]:
        reflections = await self.aload_reflections(lanlan_name)
        return [r for r in reflections if r.get('status') == 'pending']

    def get_confirmed_reflections(self, lanlan_name: str) -> list[dict]:
        """Get all confirmed (soft persona) reflections."""
        reflections = self.load_reflections(lanlan_name)
        return [r for r in reflections if r.get('status') == 'confirmed']

    async def aget_confirmed_reflections(self, lanlan_name: str) -> list[dict]:
        reflections = await self.aload_reflections(lanlan_name)
        return [r for r in reflections if r.get('status') == 'confirmed']

    @staticmethod
    def _filter_followup_candidates(pending: list[dict]) -> list[dict]:
        if not pending:
            return []
        now = datetime.now()
        eligible = []
        for r in pending:
            next_eligible = r.get('next_eligible_at')
            if next_eligible:
                try:
                    if datetime.fromisoformat(next_eligible) > now:
                        continue
                except (ValueError, TypeError):
                    pass
            eligible.append(r)
        return eligible[:2]

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
        if not reflection_ids:
            return
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
        """
        from config.prompts_memory import get_reflection_feedback_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm

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
            llm = create_chat_llm(
                api_config.get('model', SETTING_PROPOSER_MODEL),
                api_config['base_url'], api_config['api_key'],
                temperature=0.1,
            )
            try:
                resp = await llm.ainvoke(prompt)
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
        from config.prompts_memory import get_reflection_feedback_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm

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
            llm = create_chat_llm(
                api_config.get('model', SETTING_PROPOSER_MODEL),
                api_config['base_url'], api_config['api_key'],
                temperature=0.1,
            )
            try:
                resp = await llm.ainvoke(prompt)
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
        now_str = datetime.now().isoformat()
        for r in reflections:
            if r.get('id') == reflection_id:
                r['status'] = status
                r[f'{status}_at'] = now_str
                return r.get('text', '')
        return None

    def confirm_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        """Mark reflection as confirmed (soft persona). Does NOT write to persona yet.

        Confirmed reflections exist independently for AUTO_PROMOTE_DAYS days,
        during which they can still be rebutted. After that, auto_promote_stale()
        upgrades them to real persona entries.
        """
        reflections = self.load_reflections(lanlan_name)
        text = self._apply_promotion_status(reflections, reflection_id, 'confirmed')
        if text is not None:
            logger.info(f"[Reflection] {lanlan_name}: 反思已确认(软persona): {text[:50]}...")
        self.save_reflections(lanlan_name, reflections)
        self._mark_surfaced_handled(lanlan_name, reflection_id, 'confirmed')

    async def aconfirm_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        reflections = await self.aload_reflections(lanlan_name)
        text = self._apply_promotion_status(reflections, reflection_id, 'confirmed')
        if text is not None:
            logger.info(f"[Reflection] {lanlan_name}: 反思已确认(软persona): {text[:50]}...")
        await self.asave_reflections(lanlan_name, reflections)
        await self._amark_surfaced_handled(lanlan_name, reflection_id, 'confirmed')

    def reject_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        """Mark a reflection as denied — won't be promoted."""
        reflections = self.load_reflections(lanlan_name)
        text = self._apply_promotion_status(reflections, reflection_id, 'denied')
        if text is not None:
            logger.info(f"[Reflection] {lanlan_name}: 反思被否定: {text[:50]}...")
        self.save_reflections(lanlan_name, reflections)
        self._mark_surfaced_handled(lanlan_name, reflection_id, 'denied')

    async def areject_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        reflections = await self.aload_reflections(lanlan_name)
        text = self._apply_promotion_status(reflections, reflection_id, 'denied')
        if text is not None:
            logger.info(f"[Reflection] {lanlan_name}: 反思被否定: {text[:50]}...")
        await self.asave_reflections(lanlan_name, reflections)
        await self._amark_surfaced_handled(lanlan_name, reflection_id, 'denied')

    @staticmethod
    def _apply_mark_surfaced_handled(
        surfaced: list[dict], reflection_id: str, feedback: str,
    ) -> bool:
        now_str = datetime.now().isoformat()
        changed = False
        for s in surfaced:
            if s.get('reflection_id') == reflection_id and s.get('feedback') is None:
                s['feedback'] = feedback
                s['feedback_at'] = now_str
                changed = True
        return changed

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

    def auto_promote_stale(self, lanlan_name: str) -> int:
        """Process two automatic state transitions:

        1. pending → confirmed: after AUTO_CONFIRM_DAYS (3) days without denial
        2. confirmed → promoted: after AUTO_PROMOTE_DAYS (3) more days → write to persona, archive

        Returns total number of transitions.
        """
        reflections = self.load_reflections(lanlan_name)
        now = datetime.now()
        transitions = 0
        confirmed_ids: list[str] = []
        promoted_ids: list[str] = []

        for r in reflections:
            status = r.get('status')
            try:
                if status == 'pending':
                    created = datetime.fromisoformat(r.get('created_at', ''))
                    if (now - created).total_seconds() / 86400 >= AUTO_CONFIRM_DAYS:
                        r['status'] = 'confirmed'
                        r['confirmed_at'] = now.isoformat()
                        r['auto_confirmed'] = True
                        confirmed_ids.append(r['id'])
                        transitions += 1
                        logger.info(f"[Reflection] {lanlan_name}: pending→confirmed({AUTO_CONFIRM_DAYS}天): {r['text'][:50]}...")

                elif status == 'confirmed':
                    confirmed_at = datetime.fromisoformat(r.get('confirmed_at', ''))
                    if (now - confirmed_at).total_seconds() / 86400 >= AUTO_PROMOTE_DAYS:
                        result = self._persona_manager.add_fact(
                            lanlan_name, r['text'],
                            entity=r.get('entity', 'relationship'),
                            source='reflection',
                            source_id=r['id'],
                        )
                        if result == PersonaManager.FACT_ADDED:
                            r['status'] = 'promoted'
                            r['promoted_at'] = now.isoformat()
                            promoted_ids.append(r['id'])
                            transitions += 1
                            logger.info(f"[Reflection] {lanlan_name}: confirmed→persona({AUTO_PROMOTE_DAYS}天): {r['text'][:50]}...")
                        elif result == PersonaManager.FACT_REJECTED_CARD:
                            r['status'] = 'denied'
                            r['denied_at'] = now.isoformat()
                            r['denied_reason'] = 'contradicts_character_card'
                            transitions += 1
                            logger.info(f"[Reflection] {lanlan_name}: confirmed→denied(与角色卡矛盾): {r['text'][:50]}...")
                        else:
                            logger.info(f"[Reflection] {lanlan_name}: confirmed→persona 暂缓(进入矛盾审视队列): {r['text'][:50]}...")
            except (ValueError, TypeError):
                continue

        if transitions:
            self.save_reflections(lanlan_name, reflections)
            if confirmed_ids:
                self._batch_mark_surfaced_handled(lanlan_name, confirmed_ids, 'auto_confirmed')
            if promoted_ids:
                self._batch_mark_surfaced_handled(lanlan_name, promoted_ids, 'promoted')
        return transitions

    async def aauto_promote_stale(self, lanlan_name: str) -> int:
        reflections = await self.aload_reflections(lanlan_name)
        now = datetime.now()
        transitions = 0
        confirmed_ids: list[str] = []
        promoted_ids: list[str] = []

        for r in reflections:
            status = r.get('status')
            try:
                if status == 'pending':
                    created = datetime.fromisoformat(r.get('created_at', ''))
                    if (now - created).total_seconds() / 86400 >= AUTO_CONFIRM_DAYS:
                        r['status'] = 'confirmed'
                        r['confirmed_at'] = now.isoformat()
                        r['auto_confirmed'] = True
                        confirmed_ids.append(r['id'])
                        transitions += 1
                        logger.info(f"[Reflection] {lanlan_name}: pending→confirmed({AUTO_CONFIRM_DAYS}天): {r['text'][:50]}...")

                elif status == 'confirmed':
                    confirmed_at = datetime.fromisoformat(r.get('confirmed_at', ''))
                    if (now - confirmed_at).total_seconds() / 86400 >= AUTO_PROMOTE_DAYS:
                        result = await self._persona_manager.aadd_fact(
                            lanlan_name, r['text'],
                            entity=r.get('entity', 'relationship'),
                            source='reflection',
                            source_id=r['id'],
                        )
                        if result == PersonaManager.FACT_ADDED:
                            r['status'] = 'promoted'
                            r['promoted_at'] = now.isoformat()
                            promoted_ids.append(r['id'])
                            transitions += 1
                            logger.info(f"[Reflection] {lanlan_name}: confirmed→persona({AUTO_PROMOTE_DAYS}天): {r['text'][:50]}...")
                        elif result == PersonaManager.FACT_REJECTED_CARD:
                            r['status'] = 'denied'
                            r['denied_at'] = now.isoformat()
                            r['denied_reason'] = 'contradicts_character_card'
                            transitions += 1
                            logger.info(f"[Reflection] {lanlan_name}: confirmed→denied(与角色卡矛盾): {r['text'][:50]}...")
                        else:
                            logger.info(f"[Reflection] {lanlan_name}: confirmed→persona 暂缓(进入矛盾审视队列): {r['text'][:50]}...")
            except (ValueError, TypeError):
                continue

        if transitions:
            await self.asave_reflections(lanlan_name, reflections)
            if confirmed_ids:
                await self._abatch_mark_surfaced_handled(lanlan_name, confirmed_ids, 'auto_confirmed')
            if promoted_ids:
                await self._abatch_mark_surfaced_handled(lanlan_name, promoted_ids, 'promoted')
        return transitions

    # 允许从这些 feedback 状态转换到新状态（用于 promoted 覆盖 confirmed/auto_confirmed）
    _UPGRADABLE_FEEDBACK = {None, 'confirmed', 'auto_confirmed'}

    def _apply_batch_mark(
        self, surfaced: list[dict], reflection_ids: list[str], feedback: str,
    ) -> bool:
        id_set = set(reflection_ids)
        changed = False
        now = datetime.now().isoformat()
        for s in surfaced:
            if s.get('reflection_id') in id_set and s.get('feedback') in self._UPGRADABLE_FEEDBACK:
                s['feedback'] = feedback
                s['feedback_at'] = now
                changed = True
        return changed

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
