# -*- coding: utf-8 -*-
"""
FactStore — Tier 1 of the three-tier memory hierarchy.

Extracts atomic facts from conversations using LLM, deduplicates via
SHA-256 hash + FTS5 semantic search, and persists to JSON files.
Facts are indexed in TimeIndexedMemory's FTS5 table for later retrieval.
"""
from __future__ import annotations

import hashlib
import json
import os
import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from config import SETTING_PROPOSER_MODEL
from config.prompts_memory import fact_extraction_prompt
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type

if TYPE_CHECKING:
    from memory.timeindex import TimeIndexedMemory

logger = get_module_logger(__name__, "Memory")


class FactStore:
    """Manages raw fact extraction, deduplication, and persistence."""

    def __init__(self, *, time_indexed_memory: TimeIndexedMemory | None = None):
        self._config_manager = get_config_manager()
        self._time_indexed = time_indexed_memory
        self._facts: dict[str, list[dict]] = {}  # {lanlan_name: [fact, ...]}

    # ── persistence ──────────────────────────────────────────────────

    def _facts_path(self, name: str) -> str:
        self._config_manager.ensure_memory_directory()
        return os.path.join(str(self._config_manager.memory_dir), f'facts_{name}.json')

    def load_facts(self, name: str) -> list[dict]:
        path = self._facts_path(name)
        if name in self._facts:
            return self._facts[name]
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._facts[name] = data
                    return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[FactStore] 加载 facts 文件失败: {e}")
        self._facts[name] = []
        return self._facts[name]

    def save_facts(self, name: str) -> None:
        facts = self._facts.get(name, [])
        atomic_write_json(self._facts_path(name), facts, indent=2, ensure_ascii=False)

    # ── extraction ───────────────────────────────────────────────────

    async def extract_facts(self, messages: list, lanlan_name: str) -> list[dict]:
        """Extract facts from a conversation using LLM.

        Returns list of new (non-duplicate) facts that were stored.
        """
        from openai import APIConnectionError, InternalServerError, RateLimitError
        from utils.llm_client import create_chat_llm

        _, _, _, _, name_mapping, _, _, _, _ = self._config_manager.get_character_data()
        name_mapping['ai'] = lanlan_name

        # Build conversation text
        lines = []
        for msg in messages:
            role = name_mapping.get(getattr(msg, 'type', ''), getattr(msg, 'type', ''))
            content = getattr(msg, 'content', '')
            if isinstance(content, str):
                lines.append(f"{role} | {content}")
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        parts.append(item.get('text', f"|{item.get('type', '')}|"))
                    else:
                        parts.append(str(item))
                lines.append(f"{role} | {''.join(parts)}")
        conversation_text = "\n".join(lines)

        prompt = fact_extraction_prompt.replace('{CONVERSATION}', conversation_text)
        prompt = prompt.replace('{LANLAN_NAME}', lanlan_name)
        prompt = prompt.replace('{MASTER_NAME}', name_mapping.get('human', '主人'))

        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                set_call_type("memory_fact_extraction")
                api_config = self._config_manager.get_model_api_config('summary')
                llm = create_chat_llm(
                    api_config.get('model', SETTING_PROPOSER_MODEL),
                    api_config['base_url'], api_config['api_key'],
                    temperature=0.3,
                )
                try:
                    resp = await llm.ainvoke(prompt)
                finally:
                    await llm.aclose()
                raw = resp.content.strip()
                if raw.startswith("```"):
                    raw = raw.replace("```json", "").replace("```", "").strip()
                extracted = json.loads(raw)
                if not isinstance(extracted, list):
                    extracted = []
                break
            except (APIConnectionError, InternalServerError, RateLimitError):
                retries += 1
                if retries < max_retries:
                    await asyncio.sleep(2 ** (retries - 1))
                continue
            except Exception as e:
                logger.warning(f"[FactStore] 事实提取失败: {e}")
                return []
        else:
            return []

        # Deduplicate and store
        new_facts = []
        existing_facts = self.load_facts(lanlan_name)
        existing_hashes = {f.get('hash') for f in existing_facts if f.get('hash')}

        for fact in extracted:
            text = fact.get('text', '').strip()
            if not text:
                continue
            try:
                importance = int(fact.get('importance', 5))
            except (ValueError, TypeError):
                importance = 5
            if importance < 5:
                continue

            # Stage 1: SHA-256 exact dedup
            content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            if content_hash in existing_hashes:
                continue

            # Stage 2: FTS5 semantic dedup (lightweight, no LLM)
            if self._time_indexed is not None:
                similar = self._time_indexed.search_facts(lanlan_name, text, limit=3)
                is_dup = False
                for fid, score in similar:
                    if score < -5:
                        is_dup = True
                        break
                if is_dup:
                    continue

            fact_entry = {
                'id': f"fact_{datetime.now().strftime('%Y%m%d%H%M%S')}_{content_hash[:8]}",
                'text': text,
                'importance': importance,
                'entity': fact.get('entity', 'user'),
                'tags': fact.get('tags', []),
                'hash': content_hash,
                'created_at': datetime.now().isoformat(),
                'absorbed': False,  # True when consumed by a reflection
            }
            existing_facts.append(fact_entry)
            existing_hashes.add(content_hash)
            new_facts.append(fact_entry)

            # Index in FTS5
            if self._time_indexed is not None:
                self._time_indexed.index_fact(lanlan_name, fact_entry['id'], text)

        if new_facts:
            self.save_facts(lanlan_name)
            logger.info(f"[FactStore] {lanlan_name}: 提取了 {len(new_facts)} 条新事实")

        return new_facts

    # ── query helpers ────────────────────────────────────────────────

    def get_unabsorbed_facts(self, name: str, min_importance: int = 5) -> list[dict]:
        """Get facts that haven't been consumed by a reflection yet."""
        facts = self.load_facts(name)
        return [
            f for f in facts
            if not f.get('absorbed') and f.get('importance', 0) >= min_importance
        ]

    def get_facts_by_entity(self, name: str, entity: str) -> list[dict]:
        facts = self.load_facts(name)
        return [f for f in facts if f.get('entity') == entity]

    def mark_absorbed(self, name: str, fact_ids: list[str]) -> None:
        """Mark facts as absorbed by a reflection."""
        facts = self.load_facts(name)
        id_set = set(fact_ids)
        changed = False
        for f in facts:
            if f.get('id') in id_set and not f.get('absorbed'):
                f['absorbed'] = True
                changed = True
        if changed:
            self.save_facts(name)
