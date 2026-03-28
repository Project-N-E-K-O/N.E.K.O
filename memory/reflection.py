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
# Days without denial → auto-promote
AUTO_CONFIRM_DAYS = 3


class ReflectionEngine:
    """Synthesizes facts into reflections and manages the pending → confirmed lifecycle."""

    def __init__(self, fact_store: FactStore, persona_manager: PersonaManager):
        self._config_manager = get_config_manager()
        self._fact_store = fact_store
        self._persona_manager = persona_manager

    # ── file paths ───────────────────────────────────────────────────

    def _reflections_path(self, name: str) -> str:
        self._config_manager.ensure_memory_directory()
        return os.path.join(str(self._config_manager.memory_dir), f'reflections_{name}.json')

    def _surfaced_path(self, name: str) -> str:
        self._config_manager.ensure_memory_directory()
        return os.path.join(str(self._config_manager.memory_dir), f'surfaced_{name}.json')

    # ── persistence ──────────────────────────────────────────────────

    def load_reflections(self, name: str) -> list[dict]:
        path = self._reflections_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] 加载失败: {e}")
        return []

    def save_reflections(self, name: str, reflections: list[dict]) -> None:
        atomic_write_json(self._reflections_path(name), reflections, indent=2, ensure_ascii=False)

    def load_surfaced(self, name: str) -> list[dict]:
        """Load the list of reflections that were surfaced in proactive chat."""
        path = self._surfaced_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def save_surfaced(self, name: str, surfaced: list[dict]) -> None:
        atomic_write_json(self._surfaced_path(name), surfaced, indent=2, ensure_ascii=False)

    # ── synthesis ────────────────────────────────────────────────────

    async def synthesize_reflections(self, lanlan_name: str) -> list[dict]:
        """Synthesize pending reflections from accumulated unabsorbed facts.

        Called during proactive chat. Returns newly created reflections.
        """
        from config.prompts_memory import reflection_prompt
        from utils.llm_client import create_chat_llm

        unabsorbed = self._fact_store.get_unabsorbed_facts(lanlan_name)
        if len(unabsorbed) < MIN_FACTS_FOR_REFLECTION:
            return []

        _, _, _, _, name_mapping, _, _, _, _ = self._config_manager.get_character_data()
        master_name = name_mapping.get('human', '主人')

        facts_text = "\n".join(f"- {f['text']} (importance: {f.get('importance', 5)})" for f in unabsorbed)
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
            reflection_text = result.get('reflection', '')
        except Exception as e:
            logger.warning(f"[Reflection] 合成失败: {e}")
            return []

        if not reflection_text:
            return []

        # Create pending reflection
        reflection = {
            'id': f"ref_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'text': reflection_text,
            'status': 'pending',  # pending | confirmed | denied
            'source_fact_ids': [f['id'] for f in unabsorbed],
            'created_at': datetime.now().isoformat(),
            'feedback': None,  # confirmed | denied | None
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

    def get_followup_topics(self, lanlan_name: str) -> list[dict]:
        """Get pending reflections suitable for natural mention in proactive chat.

        Returns candidates only — does NOT persist anything.
        Call record_surfaced() after the reply is actually sent.
        """
        pending = self.get_pending_reflections(lanlan_name)
        if not pending:
            return []
        return pending[:2]

    def record_surfaced(self, lanlan_name: str, reflection_ids: list[str]) -> None:
        """Record which reflections were actually mentioned in proactive chat.

        Called AFTER the reply is sent, not during candidate selection.
        """
        if not reflection_ids:
            return
        surfaced = self.load_surfaced(lanlan_name)
        now = datetime.now().isoformat()

        # Load reflection texts for reference
        reflections = self.load_reflections(lanlan_name)
        id_to_text = {r['id']: r.get('text', '') for r in reflections}

        for rid in reflection_ids:
            already = any(s.get('reflection_id') == rid for s in surfaced)
            if not already:
                surfaced.append({
                    'reflection_id': rid,
                    'text': id_to_text.get(rid, ''),
                    'surfaced_at': now,
                    'feedback': None,
                })
        self.save_surfaced(lanlan_name, surfaced)

    async def check_feedback(self, lanlan_name: str, user_messages: list[str]) -> list[dict]:
        """Check if user's recent messages confirm/deny surfaced reflections.

        Returns list of {reflection_id, feedback} dicts.
        """
        from config.prompts_memory import reflection_feedback_prompt
        from utils.llm_client import create_chat_llm

        surfaced = self.load_surfaced(lanlan_name)
        pending_surfaced = [s for s in surfaced if s.get('feedback') is None]
        if not pending_surfaced:
            return []

        reflections_text = "\n".join(
            f"- [{s['reflection_id']}] {s['text']}" for s in pending_surfaced
        )
        messages_text = "\n".join(user_messages)

        prompt = reflection_feedback_prompt.format(
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
            return []

        # Update surfaced records
        for fb in feedbacks:
            rid = fb.get('reflection_id')
            feedback = fb.get('feedback')
            if rid and feedback:
                for s in surfaced:
                    if s.get('reflection_id') == rid:
                        s['feedback'] = feedback
        self.save_surfaced(lanlan_name, surfaced)

        return feedbacks

    def confirm_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        """Promote a confirmed reflection to persona (Tier 3)."""
        reflections = self.load_reflections(lanlan_name)
        for r in reflections:
            if r.get('id') == reflection_id:
                r['status'] = 'confirmed'
                r['confirmed_at'] = datetime.now().isoformat()
                # Add to persona
                self._persona_manager.add_fact(
                    lanlan_name, r['text'], entity='relationship'
                )
                logger.info(f"[Reflection] {lanlan_name}: 反思已晋升 persona: {r['text'][:50]}...")
                break
        self.save_reflections(lanlan_name, reflections)

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

    def auto_promote_stale(self, lanlan_name: str) -> int:
        """Auto-promote pending reflections that have been pending for AUTO_CONFIRM_DAYS
        without denial.

        Returns the number of auto-promoted reflections.
        """
        reflections = self.load_reflections(lanlan_name)
        now = datetime.now()
        promoted = 0

        for r in reflections:
            if r.get('status') != 'pending':
                continue
            created_str = r.get('created_at')
            if not created_str:
                continue
            try:
                created = datetime.fromisoformat(created_str)
                days_since = (now - created).total_seconds() / 86400
                if days_since >= AUTO_CONFIRM_DAYS:
                    r['status'] = 'confirmed'
                    r['confirmed_at'] = now.isoformat()
                    r['auto_confirmed'] = True
                    self._persona_manager.add_fact(
                        lanlan_name, r['text'], entity='relationship'
                    )
                    promoted += 1
                    logger.info(f"[Reflection] {lanlan_name}: 反思自动晋升({AUTO_CONFIRM_DAYS}天无反对): {r['text'][:50]}...")
            except (ValueError, TypeError):
                continue

        if promoted:
            self.save_reflections(lanlan_name, reflections)
        return promoted
