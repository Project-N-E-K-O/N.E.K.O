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

import json
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from config import SETTING_PROPOSER_MODEL
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type

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

    def _surfaced_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'surfaced.json')

    # ── persistence ──────────────────────────────────────────────────

    def load_reflections(self, name: str, include_archived: bool = False) -> list[dict]:
        path = self._reflections_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    items = [item for item in data if isinstance(item, dict) and 'id' in item]
                    if not include_archived:
                        # Exclude promoted/denied (archived) from active operations
                        items = [r for r in items if r.get('status') not in ('promoted', 'denied')]
                    return items
                logger.warning(f"[Reflection] reflections 文件不是列表，忽略: {path}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] 加载失败: {e}")
        return []

    def save_reflections(self, name: str, reflections: list[dict]) -> None:
        """Save reflections, merging with archived entries on disk."""
        path = self._reflections_path(name)
        # Load all (including archived) to preserve them
        all_on_disk = []
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    all_on_disk = [r for r in data if isinstance(r, dict)]
            except (json.JSONDecodeError, OSError) as e:
                # 读取失败 → 中止保存，避免覆盖磁盘上的 promoted/denied 条目
                logger.warning(f"[Reflection] {name}: 读取现有 reflections 失败，中止保存以保护归档数据: {e}")
                return

        # Build id→entry map from active list
        active_ids = {r['id'] for r in reflections if 'id' in r}
        # Keep archived entries that aren't in the active list
        archived = [r for r in all_on_disk if r.get('id') not in active_ids
                     and r.get('status') in ('promoted', 'denied')]
        merged = reflections + archived
        atomic_write_json(path, merged, indent=2, ensure_ascii=False)

    def load_surfaced(self, name: str) -> list[dict]:
        """Load the list of reflections that were surfaced in proactive chat."""
        path = self._surfaced_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    # Filter to dicts only; drop corrupted entries
                    return [item for item in data if isinstance(item, dict)]
                logger.warning(f"[Reflection] surfaced 文件不是列表，忽略: {path}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] 加载 surfaced 失败: {e}")
        return []

    def save_surfaced(self, name: str, surfaced: list[dict]) -> None:
        atomic_write_json(self._surfaced_path(name), surfaced, indent=2, ensure_ascii=False)

    # ── synthesis ────────────────────────────────────────────────────

    async def synthesize_reflections(self, lanlan_name: str) -> list[dict]:
        """Synthesize pending reflections from accumulated unabsorbed facts.

        Called during proactive chat. Returns newly created reflections.
        """
        from config.prompts_memory import get_reflection_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm

        unabsorbed = self._fact_store.get_unabsorbed_facts(lanlan_name)
        if len(unabsorbed) < MIN_FACTS_FOR_REFLECTION:
            return []

        _, _, _, _, name_mapping, _, _, _, _ = self._config_manager.get_character_data()
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
            result = json.loads(raw)
            if not isinstance(result, dict):
                logger.warning(f"[Reflection] LLM 返回非 dict: {type(result)}")
                return []
            reflection_text = result.get('reflection', '')
            if not isinstance(reflection_text, str):
                logger.warning(f"[Reflection] reflection 字段非 str: {type(reflection_text)}")
                return []
            reflection_text = reflection_text.strip()
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
            'status': 'pending',  # pending | confirmed | denied | promoted | archived
            'source_fact_ids': [f['id'] for f in unabsorbed],
            'created_at': now.isoformat(),
            'feedback': None,
            'next_eligible_at': (now + timedelta(minutes=REFLECTION_COOLDOWN_MINUTES)).isoformat(),
        }

        reflections = self.load_reflections(lanlan_name)
        reflections.append(reflection)
        self.save_reflections(lanlan_name, reflections)

        # Mark source facts as absorbed
        self._fact_store.mark_absorbed(lanlan_name, reflection['source_fact_ids'])

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

    def get_confirmed_reflections(self, lanlan_name: str) -> list[dict]:
        """Get all confirmed (soft persona) reflections."""
        reflections = self.load_reflections(lanlan_name)
        return [r for r in reflections if r.get('status') == 'confirmed']

    def get_followup_topics(self, lanlan_name: str) -> list[dict]:
        """Get pending reflections suitable for natural mention in proactive chat.

        Returns candidates that have passed their cooldown period.
        Does NOT persist anything — call record_surfaced() after reply is sent.
        """
        pending = self.get_pending_reflections(lanlan_name)
        if not pending:
            return []
        now = datetime.now()
        eligible = []
        for r in pending:
            next_eligible = r.get('next_eligible_at')
            if next_eligible:
                try:
                    if datetime.fromisoformat(next_eligible) > now:
                        continue  # still in cooldown
                except (ValueError, TypeError):
                    pass
            eligible.append(r)
        return eligible[:2]

    def record_surfaced(self, lanlan_name: str, reflection_ids: list[str]) -> None:
        """Record which reflections were actually mentioned in proactive chat.

        Called AFTER the reply is sent, not during candidate selection.
        Also refreshes the cooldown on surfaced reflections.
        """
        if not reflection_ids:
            return
        surfaced = self.load_surfaced(lanlan_name)
        now = datetime.now()
        now_str = now.isoformat()
        next_eligible = (now + timedelta(minutes=REFLECTION_COOLDOWN_MINUTES)).isoformat()

        # Load and update reflections (refresh cooldown)
        reflections = self.load_reflections(lanlan_name)
        id_to_text = {r['id']: r.get('text', '') for r in reflections}
        cooldown_changed = False
        for r in reflections:
            if r.get('id') in reflection_ids:
                r['next_eligible_at'] = next_eligible
                cooldown_changed = True
        if cooldown_changed:
            self.save_reflections(lanlan_name, reflections)

        for rid in reflection_ids:
            # If already surfaced, refresh timestamp and clear feedback for re-check
            found = False
            for s in surfaced:
                if s.get('reflection_id') == rid:
                    s['surfaced_at'] = now_str
                    s['text'] = id_to_text.get(rid, s.get('text', ''))
                    s['feedback'] = None  # re-enable feedback collection
                    found = True
                    break
            if not found:
                surfaced.append({
                    'reflection_id': rid,
                    'text': id_to_text.get(rid, ''),
                    'surfaced_at': now_str,
                    'feedback': None,
                })
        self.save_surfaced(lanlan_name, surfaced)

    async def check_feedback(self, lanlan_name: str, user_messages: list[str]) -> list[dict]:
        """Check if user's recent messages confirm/deny surfaced reflections.

        Returns list of {reflection_id, feedback} dicts.
        """
        from config.prompts_memory import get_reflection_feedback_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm

        surfaced = self.load_surfaced(lanlan_name)
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
            feedbacks = json.loads(raw)
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
        self.save_surfaced(lanlan_name, surfaced)

        return feedbacks

    async def check_feedback_for_confirmed(
        self, lanlan_name: str, confirmed: list[dict], user_messages: list[str],
    ) -> list[dict]:
        """Check if recent user messages rebut any confirmed reflections.

        Used by periodic rebuttal check (every 5 min). Only returns 'denied' or 'ignored'.
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
            feedbacks = json.loads(raw)
            if not isinstance(feedbacks, list):
                feedbacks = [feedbacks]
            return feedbacks
        except Exception as e:
            logger.warning(f"[Reflection] 反驳检查失败: {e}")
            return []

    def confirm_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        """Mark reflection as confirmed (soft persona). Does NOT write to persona yet.

        Confirmed reflections exist independently for AUTO_PROMOTE_DAYS days,
        during which they can still be rebutted. After that, auto_promote_stale()
        upgrades them to real persona entries.
        """
        reflections = self.load_reflections(lanlan_name)
        for r in reflections:
            if r.get('id') == reflection_id:
                r['status'] = 'confirmed'
                r['confirmed_at'] = datetime.now().isoformat()
                logger.info(f"[Reflection] {lanlan_name}: 反思已确认(软persona): {r['text'][:50]}...")
                break
        self.save_reflections(lanlan_name, reflections)
        self._mark_surfaced_handled(lanlan_name, reflection_id, 'confirmed')

    def reject_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        """Mark a reflection as denied — won't be promoted."""
        reflections = self.load_reflections(lanlan_name)
        for r in reflections:
            if r.get('id') == reflection_id:
                r['status'] = 'denied'
                r['denied_at'] = datetime.now().isoformat()
                logger.info(f"[Reflection] {lanlan_name}: 反思被否定: {r['text'][:50]}...")
                break
        self.save_reflections(lanlan_name, reflections)
        self._mark_surfaced_handled(lanlan_name, reflection_id, 'denied')

    def _mark_surfaced_handled(self, lanlan_name: str, reflection_id: str, feedback: str) -> None:
        """Mark surfaced record as handled so check_feedback won't reprocess it."""
        surfaced = self.load_surfaced(lanlan_name)
        changed = False
        now = datetime.now().isoformat()
        for s in surfaced:
            if s.get('reflection_id') == reflection_id and s.get('feedback') is None:
                s['feedback'] = feedback
                s['feedback_at'] = now
                changed = True
        if changed:
            self.save_surfaced(lanlan_name, surfaced)

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
                    # pending → confirmed after AUTO_CONFIRM_DAYS
                    created = datetime.fromisoformat(r.get('created_at', ''))
                    if (now - created).total_seconds() / 86400 >= AUTO_CONFIRM_DAYS:
                        r['status'] = 'confirmed'
                        r['confirmed_at'] = now.isoformat()
                        r['auto_confirmed'] = True
                        confirmed_ids.append(r['id'])
                        transitions += 1
                        logger.info(f"[Reflection] {lanlan_name}: pending→confirmed({AUTO_CONFIRM_DAYS}天): {r['text'][:50]}...")

                elif status == 'confirmed':
                    # confirmed → promoted after AUTO_PROMOTE_DAYS
                    confirmed_at = datetime.fromisoformat(r.get('confirmed_at', ''))
                    if (now - confirmed_at).total_seconds() / 86400 >= AUTO_PROMOTE_DAYS:
                        self._persona_manager.add_fact(
                            lanlan_name, r['text'], entity='relationship'
                        )
                        r['status'] = 'promoted'
                        r['promoted_at'] = now.isoformat()
                        promoted_ids.append(r['id'])
                        transitions += 1
                        logger.info(f"[Reflection] {lanlan_name}: confirmed→persona({AUTO_PROMOTE_DAYS}天): {r['text'][:50]}...")
            except (ValueError, TypeError):
                continue

        if transitions:
            self.save_reflections(lanlan_name, reflections)
            if confirmed_ids:
                self._batch_mark_surfaced_handled(lanlan_name, confirmed_ids, 'auto_confirmed')
            if promoted_ids:
                self._batch_mark_surfaced_handled(lanlan_name, promoted_ids, 'promoted')
        return transitions

    def _batch_mark_surfaced_handled(
        self, lanlan_name: str, reflection_ids: list[str], feedback: str,
    ) -> None:
        """Mark multiple surfaced records as handled in a single I/O round-trip."""
        if not reflection_ids:
            return
        surfaced = self.load_surfaced(lanlan_name)
        id_set = set(reflection_ids)
        changed = False
        now = datetime.now().isoformat()
        for s in surfaced:
            if s.get('reflection_id') in id_set and s.get('feedback') is None:
                s['feedback'] = feedback
                s['feedback_at'] = now
                changed = True
        if changed:
            self.save_surfaced(lanlan_name, surfaced)
