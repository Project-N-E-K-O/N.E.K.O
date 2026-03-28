# -*- coding: utf-8 -*-
"""
PersonaManager — Tier 3 of the three-tier memory hierarchy.

Manages long-term persona data (user profile, AI profile, relationship dynamics).
Integrates with the legacy ImportantSettings system and provides markdown rendering
for LLM context injection.

Key features:
- Three-section persona: user facts, AI facts, relationship dynamics
- Pending reflections injected with "(还不太确定)" annotation
- Suppress mechanism: 5h window, >2 mentions → suppress (completely hidden from
  all rendering sections; suppress has highest priority)
- Contradiction detection → queued for batch correction via LLM
- Auto-migration from legacy settings files
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from config import SETTING_PROPOSER_MODEL
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")

# ── 疲劳常量 ──────────────────────────────────────────────────────
# 5小时内提及2次以上 → suppress，5小时后刷新
SUPPRESS_MENTION_LIMIT = 2           # 窗口内提及次数上限
SUPPRESS_WINDOW_HOURS = 5            # 统计窗口（小时）
SUPPRESS_COOLDOWN_HOURS = 5          # suppress 后冷却（=窗口，5小时后刷新）

# ── 矛盾检测阈值 ─────────────────────────────────────────────────
SIMILARITY_THRESHOLD = 0.6           # 余弦相似度(如有embedding)或关键词重叠

# ── 自动晋升冷却 ─────────────────────────────────────────────────
AUTO_CONFIRM_DAYS = 3                # pending reflection N 天无反对 → 自动晋升


def _is_mentioned(fact_text: str, response_text: str) -> bool:
    """判断 response 中是否"提及"了某条 persona 事实。

    使用关键词重叠: 从 fact 中取长度 >= 2 的词，检查 response 中是否包含。
    """
    if not fact_text or not response_text:
        return False
    # 简单策略：fact 中每个长度>=2的连续子串，在 response 中出现即视为提及
    keywords = set()
    for segment in fact_text.replace('，', ' ').replace('。', ' ').replace('、', ' ').split():
        seg = segment.strip()
        if len(seg) >= 2:
            keywords.add(seg)
    if not keywords:
        return False
    return any(kw in response_text for kw in keywords)


class PersonaManager:
    """Manages per-character persona files with three sections:
    user, ai, relationship. Each section contains a list of fact entries.
    """

    def __init__(self):
        self._config_manager = get_config_manager()
        self._personas: dict[str, dict] = {}

    # ── file paths ───────────────────────────────────────────────────

    def _persona_path(self, name: str) -> str:
        self._config_manager.ensure_memory_directory()
        return os.path.join(str(self._config_manager.memory_dir), f'persona_{name}.json')

    def _corrections_path(self, name: str) -> str:
        self._config_manager.ensure_memory_directory()
        return os.path.join(str(self._config_manager.memory_dir), f'persona_corrections_{name}.json')

    # ── CRUD ─────────────────────────────────────────────────────────

    def _empty_persona(self) -> dict:
        return {
            'user': {'facts': []},
            'ai': {'facts': []},
            'relationship': {'dynamics': []},
        }

    def ensure_persona(self, name: str) -> dict:
        """Load or create persona. Auto-migrate from legacy settings if needed."""
        if name in self._personas:
            return self._personas[name]

        path = self._persona_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                self._personas[name] = data
                return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Persona] 加载失败: {e}")

        # Auto-migrate from legacy settings
        persona = self._empty_persona()
        self._migrate_from_settings(name, persona)
        self._personas[name] = persona
        self.save_persona(name, persona)
        return persona

    def _migrate_from_settings(self, name: str, persona: dict) -> None:
        """One-time migration from legacy settings_{name}.json to persona format."""
        self._config_manager.ensure_memory_directory()
        settings_path = os.path.join(str(self._config_manager.memory_dir), f'settings_{name}.json')
        if not os.path.exists(settings_path):
            return
        try:
            with open(settings_path, encoding='utf-8') as f:
                old_settings = json.load(f)
            if not isinstance(old_settings, dict):
                return

            _, _, _, _, name_mapping, _, _, _, _ = self._config_manager.get_character_data()
            master_name = name_mapping.get('human', '主人')

            for section_key, facts_list in old_settings.items():
                if not isinstance(facts_list, dict):
                    continue
                if section_key == master_name or section_key == name_mapping.get('human', ''):
                    target = persona['user']['facts']
                elif section_key == name:
                    target = persona['ai']['facts']
                else:
                    target = persona['relationship']['dynamics']

                for k, v in facts_list.items():
                    if v and str(v).strip():
                        target.append(self._normalize_entry(f"{k}: {v}"))

            logger.info(f"[Persona] {name}: 从 settings 迁移了 persona 数据")
        except Exception as e:
            logger.warning(f"[Persona] {name}: settings 迁移失败: {e}")

    def save_persona(self, name: str, persona: dict | None = None) -> None:
        if persona is None:
            persona = self._personas.get(name, self._empty_persona())
        self._personas[name] = persona
        atomic_write_json(self._persona_path(name), persona, indent=2, ensure_ascii=False)

    def get_persona(self, name: str) -> dict:
        return self.ensure_persona(name)

    # ── entry normalization ──────────────────────────────────────────

    @staticmethod
    def _normalize_entry(entry) -> dict:
        """将纯字符串条目迁移为 dict 格式。"""
        defaults = {
            'text': '',
            'recent_mentions': [],      # 窗口内提及时间戳列表
            'suppress': False,          # 是否被抑制
            'suppressed_at': None,      # suppress 开始时间
        }
        if isinstance(entry, str):
            d = dict(defaults)
            d['text'] = entry
            return d
        if isinstance(entry, dict):
            for k, v in defaults.items():
                entry.setdefault(k, v)
            # 兼容旧字段
            entry.pop('mention_count', None)
            entry.pop('consecutive_mentions', None)
            entry.pop('last_mentioned', None)
            return entry
        d = dict(defaults)
        d['text'] = str(entry)
        return d

    # ── add facts to persona ─────────────────────────────────────────

    def add_fact(self, name: str, text: str, entity: str = 'user') -> None:
        """Add a confirmed fact to persona. Checks for contradictions first."""
        persona = self.ensure_persona(name)
        section_facts = self._get_section_facts(persona, entity)

        # Check for potential contradictions
        for existing in section_facts:
            if isinstance(existing, dict):
                old_text = existing.get('text', '')
            else:
                old_text = str(existing)
            if self._texts_may_contradict(old_text, text):
                self._queue_correction(name, old_text, text, entity)
                return

        section_facts.append(self._normalize_entry(text))
        self.save_persona(name, persona)

    def _get_section_facts(self, persona: dict, entity: str) -> list:
        if entity == 'user':
            return persona.setdefault('user', {}).setdefault('facts', [])
        elif entity == 'ai':
            return persona.setdefault('ai', {}).setdefault('facts', [])
        else:
            return persona.setdefault('relationship', {}).setdefault('dynamics', [])

    @staticmethod
    def _texts_may_contradict(old_text: str, new_text: str) -> bool:
        """Lightweight keyword-overlap heuristic for contradiction detection."""
        if not old_text or not new_text:
            return False
        old_words = set(old_text.replace('，', ' ').replace('。', ' ').split())
        new_words = set(new_text.replace('，', ' ').replace('。', ' ').split())
        old_words = {w for w in old_words if len(w) >= 2}
        new_words = {w for w in new_words if len(w) >= 2}
        if not old_words or not new_words:
            return False
        overlap = old_words & new_words
        # If more than 40% keywords overlap, they might be about the same topic
        ratio = len(overlap) / min(len(old_words), len(new_words))
        return ratio >= 0.4

    # ── contradiction queue ──────────────────────────────────────────

    def _queue_correction(self, name: str, old_text: str, new_text: str, entity: str) -> None:
        corrections = self.load_pending_corrections(name)
        corrections.append({
            'old_text': old_text,
            'new_text': new_text,
            'entity': entity,
            'created_at': datetime.now().isoformat(),
        })
        atomic_write_json(self._corrections_path(name), corrections, indent=2, ensure_ascii=False)
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

    async def resolve_corrections(self, name: str) -> int:
        """用 correction model 批量审视矛盾队列（单次 LLM 调用）。

        将所有 pending corrections 合并为一个 prompt 发给 correction model，
        返回处理的矛盾数量。
        """
        from config.prompts_memory import persona_correction_prompt

        corrections = self.load_pending_corrections(name)
        if not corrections:
            return 0

        # 合并所有矛盾为单个 prompt
        pairs = []
        for i, item in enumerate(corrections):
            old_text = item.get('old_text', '')
            new_text = item.get('new_text', '')
            if old_text and new_text:
                pairs.append((i, item))
        if not pairs:
            return 0

        batch_text = "\n".join(
            f"[{i}] 已有: {item['old_text']} | 新观察: {item['new_text']}"
            for i, item in pairs
        )
        prompt = persona_correction_prompt.format(pairs=batch_text, count=len(pairs))

        try:
            from utils.token_tracker import set_call_type
            from utils.llm_client import create_chat_llm
            set_call_type("memory_correction")
            api_config = self._config_manager.get_model_api_config('correction')
            llm = create_chat_llm(
                api_config.get('model', SETTING_PROPOSER_MODEL),
                api_config['base_url'], api_config['api_key'],
                temperature=0.3,
            )
            try:
                resp = await llm.ainvoke(prompt)
            finally:
                await llm.aclose()
            raw = resp.content
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            results = json.loads(raw)
            if not isinstance(results, list):
                results = [results]
        except Exception as e:
            logger.warning(f"[Persona] {name}: correction model 调用失败: {e}")
            return 0

        # 应用结果
        persona = self.ensure_persona(name)
        resolved = 0
        for result in results:
            try:
                idx = int(result.get('index', -1))
                if idx < 0 or idx >= len(corrections):
                    continue
                item = corrections[idx]
            except (ValueError, TypeError):
                continue

            action = result.get('action', 'keep_both')
            merged_text = result.get('text', item.get('new_text', ''))
            entity = item.get('entity', 'user')
            old_text = item.get('old_text', '')
            new_text = item.get('new_text', '')
            section_facts = self._get_section_facts(persona, entity)

            if action == 'replace':
                for j, existing in enumerate(section_facts):
                    et = existing.get('text', '') if isinstance(existing, dict) else str(existing)
                    if et == old_text:
                        section_facts[j] = self._normalize_entry(merged_text)
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
            self.save_persona(name, persona)
            atomic_write_json(self._corrections_path(name), [],
                              indent=2, ensure_ascii=False)
            logger.info(f"[Persona] {name}: 批量审视完成 {resolved} 条矛盾")
        return resolved

    # ── 提及疲劳：记录 + 更新 suppress ───────────────────────────

    def record_mentions(self, name: str, response_text: str) -> None:
        """主动搭话投递后，扫描 response 中哪些 persona 条目被提及。

        核心逻辑：5小时内提及 > SUPPRESS_MENTION_LIMIT 次 → suppress。
        """
        persona = self.ensure_persona(name)
        now_str = datetime.now().isoformat()
        now = datetime.now()
        cutoff = now - timedelta(hours=SUPPRESS_WINDOW_HOURS)
        changed = False

        for entry in self._collect_all_entries(persona):
            if not isinstance(entry, dict):
                continue
            if not _is_mentioned(entry.get('text', ''), response_text):
                continue

            # 记录本次提及时间
            mentions = entry.get('recent_mentions', [])
            mentions.append(now_str)
            # 清理窗口外的旧记录
            mentions = [t for t in mentions if self._in_window(t, cutoff)]
            entry['recent_mentions'] = mentions

            # 窗口内超限 → suppress
            if not entry.get('suppress') and len(mentions) > SUPPRESS_MENTION_LIMIT:
                entry['suppress'] = True
                entry['suppressed_at'] = now_str
            changed = True

        if changed:
            self.save_persona(name, persona)

    def update_suppressions(self, name: str) -> None:
        """刷新 suppress 状态：冷却期过 → 解除；清理窗口外的 recent_mentions。"""
        persona = self.ensure_persona(name)
        now = datetime.now()
        cutoff = now - timedelta(hours=SUPPRESS_WINDOW_HOURS)
        changed = False

        for entry in self._collect_all_entries(persona):
            if not isinstance(entry, dict):
                continue

            # 清理窗口外的旧记录
            mentions = entry.get('recent_mentions', [])
            cleaned = [t for t in mentions if self._in_window(t, cutoff)]
            if len(cleaned) != len(mentions):
                entry['recent_mentions'] = cleaned
                changed = True

            # suppress 冷却检查
            if entry.get('suppress'):
                suppressed_str = entry.get('suppressed_at')
                if suppressed_str:
                    try:
                        hours_since = (now - datetime.fromisoformat(suppressed_str)).total_seconds() / 3600
                        if hours_since >= SUPPRESS_COOLDOWN_HOURS:
                            entry['suppress'] = False
                            entry['suppressed_at'] = None
                            entry['recent_mentions'] = []
                            changed = True
                    except (ValueError, TypeError):
                        pass

        if changed:
            self.save_persona(name, persona)

    @staticmethod
    def _in_window(ts_str: str, cutoff: datetime) -> bool:
        try:
            return datetime.fromisoformat(ts_str) >= cutoff
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _collect_all_entries(persona: dict) -> list[dict]:
        """收集 persona 中所有 facts/dynamics 条目的引用。"""
        entries = []
        for section_key in ('user', 'ai'):
            entries.extend(persona.get(section_key, {}).get('facts', []))
        entries.extend(persona.get('relationship', {}).get('dynamics', []))
        return entries

    # ── rendering ────────────────────────────────────────────────────

    def render_persona_markdown(self, name: str, pending_reflections: list[dict] | None = None) -> str:
        """Render persona as markdown for LLM context injection.

        Suppressed entries are rendered in a separate "暂不主动提及" section,
        NOT in their original sections. suppress has highest priority.
        """
        persona = self.ensure_persona(name)
        _, _, _, _, name_mapping, _, _, _, _ = self._config_manager.get_character_data()
        master_name = name_mapping.get('human', '主人')
        ai_name = name

        sections = []

        # Collect suppressed items across all sections
        suppressed_lines = []
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress'):
                text = entry.get('text', '')
                if text:
                    suppressed_lines.append(f"- {text}")

        # User section (exclude suppressed)
        user_facts = persona.get('user', {}).get('facts', [])
        user_lines = self._render_fact_entries(user_facts)
        if user_lines:
            sections.append(f"### 关于{master_name}\n" + "\n".join(user_lines))

        # AI section (exclude suppressed)
        ai_facts = persona.get('ai', {}).get('facts', [])
        ai_lines = self._render_fact_entries(ai_facts)
        if ai_lines:
            sections.append(f"### 关于{ai_name}\n" + "\n".join(ai_lines))

        # Relationship section (exclude suppressed)
        dynamics = persona.get('relationship', {}).get('dynamics', [])
        dynamics_lines = self._render_fact_entries(dynamics)
        if dynamics_lines:
            sections.append(f"### 关系动态\n" + "\n".join(dynamics_lines))

        # Pending reflections (also exclude suppressed)
        if pending_reflections:
            pending_lines = []
            for r in pending_reflections:
                text = r.get('text', '')
                if text and not self._is_suppressed_text(persona, text):
                    pending_lines.append(f"- {text}")
            if pending_lines:
                sections.append(
                    f"### {ai_name}最近的印象（还不太确定）\n"
                    + "\n".join(pending_lines)
                )

        # Suppressed section (記得但别主动提)
        if suppressed_lines:
            sections.append(
                f"### 暂不主动提及的内容（{ai_name}记得，但最近提到太多次了，不要再主动提起）\n"
                + "\n".join(suppressed_lines)
            )

        return "\n\n".join(sections) if sections else ""

    def _is_suppressed_text(self, persona: dict, text: str) -> bool:
        """Check if a given text matches any suppressed entry."""
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress') and entry.get('text') == text:
                return True
        return False

    @staticmethod
    def _render_fact_entries(entries: list) -> list[str]:
        """渲染 fact 条目列表。suppress 的条目不在此渲染（移至专用区域）。"""
        lines = []
        for entry in entries:
            if isinstance(entry, dict):
                if entry.get('suppress'):
                    continue  # suppress 的条目在专用区域渲染
                text = entry.get('text', '')
                if text:
                    lines.append(f"- {text}")
            elif entry:
                lines.append(f"- {entry}")
        return lines
