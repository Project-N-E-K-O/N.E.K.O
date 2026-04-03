# -*- coding: utf-8 -*-
"""
PersonaManager — Tier 3 of the three-tier memory hierarchy.

Manages long-term persona data with dynamic entity support.
Core entities: master (human), neko (AI character), relationship (dynamics).
Storage is entity-agnostic: any entity key can be added at runtime
(e.g. QQ group IDs, other users, other nekos).

Key features:
- Dynamic entity sections: each entity stores a list of facts
- Pending reflections injected with "(还不太确定)" annotation
- Suppress mechanism: 5h window, >2 mentions → suppress (completely hidden from
  all rendering sections; suppress has highest priority)
- Contradiction detection → queued for batch correction via LLM
- Auto-migration from legacy settings files and v1 entity names
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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

# ── 负面话题回避 ─────────────────────────────────────────────────
TOPIC_HARD_AVOID_THRESHOLD = 2       # 同一话题命中 2 次后升级为长期不提

_NEGATIVE_PATTERNS = (
    (re.compile(r'(?:别提|不要提|别再提|不要再提|不想聊|别聊|不要聊|不想提|别说|不要说)(?P<topic>.+?)(?:了|啦|呀|啊|吧|。|！|!|？|\?|,|，|$)'), True),
    (re.compile(r'(?P<topic>.+?)(?:这个|这件事|这话题|这个话题)?(?:很烦|好烦|烦死了|烦透了|讨厌|恶心|让我难受|让我不舒服)(?:了|。|！|!|？|\?|,|，|$)'), False),
    (re.compile(r"\b(?:don't mention|dont mention|don't talk about|dont talk about|stop talking about|stop mentioning)\s+(?P<topic>[a-z0-9][\w\s\-]{0,40}?)(?:\s+anymore)?(?:[.!?,]+|$)", re.IGNORECASE), True),
    (re.compile(r"\b(?:i hate|hate)\s+(?P<topic>[a-z0-9][\w\s\-]{0,40}?)(?:[.!?,]+|$)", re.IGNORECASE), False),
    (re.compile(r"\b(?P<topic>[a-z0-9][\w\s\-]{0,40}?)\s+(?:is|feels|seems|gets)?\s*(?:so\s+)?(?:annoying|upsetting|frustrating|awful|terrible)\b", re.IGNORECASE), False),
)
_NEGATIVE_KEYWORDS = (
    "烦", "烦死", "烦透", "讨厌", "恶心", "难受", "不舒服", "不爽", "生气",
    "焦虑", "崩溃", "郁闷", "失望", "无语", "受不了", "痛苦", "别提",
    "不要提", "不想聊", "别聊", "不要聊", "不想提", "别说", "不要说",
    "hate", "annoying", "upset", "frustrated", "anxious", "depressed",
    "don't mention", "dont mention", "don't talk about", "dont talk about",
    "stop talking about", "stop mentioning",
)
_EXPLICIT_AVOID_TOKENS = (
    "别提", "不要提", "别再提", "不要再提", "不想聊", "别聊", "不要聊",
    "don't mention", "dont mention", "don't talk about", "dont talk about",
    "stop talking about", "stop mentioning",
)
_TOPIC_PREFIX_CLEANUPS = (
    "这个", "这件事", "这话题", "这个话题", "关于", "聊", "提", "说", "讲",
    "我", "你", "你们", "咱们", "我们", "一下", "一下子", "一直",
    "the ", "about ", "our ", "my ", "your ",
)
_TOPIC_SUFFIX_CLEANUPS = (
    "了", "啦", "呀", "啊", "吧", "嘛", "呢", "哦", "噢", "这个", "这件事",
    "这话题", "这个话题", "的话题", "这件事情",
    " anymore", " any more",
)
_GENERIC_TOPIC_WORDS = {
    "这个", "那个", "这件事", "这话题", "这个话题", "事情", "话题", "内容",
}
_REFERENCE_TOPIC_PATTERNS = (
    re.compile(r'(?:关于|聊聊|继续聊|说说|提到|提起|讲到)(?P<topic>[^，。！？!?]{1,24})'),
    re.compile(r'(?P<topic>[^，。！？!?]{1,24})(?:这个话题|这件事|这个事情)'),
)

# Split on any CJK/Latin punctuation, symbols, whitespace
_SPLIT_RE = re.compile(r'[，。、！？；：\u201c\u201d\u2018\u2019（）()\[\]{}<>《》【】\s,.!?;:\-\u2014\u2026\xb7\u3000]+')

def _extract_keywords(text: str) -> set[str]:
    """从文本提取关键词/n-gram，支持 CJK 和拉丁文。

    - 拉丁文按空格分词，保留 len>=2 的 token
    - CJK 文本生成 2-gram 和 3-gram 滑动窗口
    """
    segments = _SPLIT_RE.split(text)
    keywords: set[str] = set()

    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        cjk_count = sum(
            1 for ch in seg
            if '\u4e00' <= ch <= '\u9fff' or '\u3040' <= ch <= '\u30ff' or '\uac00' <= ch <= '\ud7af'
        )
        if cjk_count > len(seg) / 2:
            for n in (2, 3):
                for i in range(len(seg) - n + 1):
                    keywords.add(seg[i:i + n])
        else:
            if len(seg) >= 2:
                keywords.add(seg)

    return keywords


def _is_mentioned(fact_text: str, response_text: str) -> bool:
    """判断 response 中是否"提及"了某条 persona 事实。"""
    if not fact_text or not response_text:
        return False
    keywords = _extract_keywords(fact_text)
    if not keywords:
        return False
    return any(kw in response_text for kw in keywords)


def _contains_negative_signal(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    return any(keyword in lowered for keyword in _NEGATIVE_KEYWORDS)


def _clean_topic_candidate(text: str) -> str:
    topic = (text or "").strip()
    if not topic:
        return ""
    topic = re.sub(r'^[\s:：,，.\-]+|[\s:：,，.。!！?？]+$', '', topic)
    topic = topic.replace("这个话题", " ").replace("这话题", " ").replace("这件事", " ")
    topic = re.sub(r'\b(?:really|actually|just|now)\b', ' ', topic, flags=re.IGNORECASE)
    topic = topic.replace("真的", " ").replace("现在", " ").replace("最近", " ")
    topic = re.sub(r'\s+', ' ', topic).strip()
    changed = True
    while changed and topic:
        changed = False
        for prefix in _TOPIC_PREFIX_CLEANUPS:
            if topic.startswith(prefix) and len(topic) > len(prefix):
                topic = topic[len(prefix):].strip()
                changed = True
        for suffix in _TOPIC_SUFFIX_CLEANUPS:
            if topic.endswith(suffix) and len(topic) > len(suffix):
                topic = topic[:-len(suffix)].strip()
                changed = True
    topic = re.sub(r'^(?:关于|聊|提|说|讲)+', '', topic).strip()
    topic = re.sub(r'(?:一下|一下子)$', '', topic).strip()
    topic = re.sub(r'(?:真的|现在|最近)$', '', topic).strip()
    if topic in _GENERIC_TOPIC_WORDS:
        return ""
    return topic[:40]


def _is_specific_topic(topic: str) -> bool:
    topic = (topic or "").strip()
    if len(topic) < 2:
        return False
    if topic in _GENERIC_TOPIC_WORDS:
        return False
    if re.fullmatch(r'[了啊呀吧嘛呢哦噢很太真就都还又也好]+', topic):
        return False
    return True


def _extract_topic_from_reference_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    for pattern in _REFERENCE_TOPIC_PATTERNS:
        match = pattern.search(raw)
        if match:
            topic = _clean_topic_candidate(match.group("topic"))
            if _is_specific_topic(topic):
                return topic
    sentences = [seg.strip() for seg in re.split(r'[，。！？!?]', raw) if seg.strip()]
    if sentences:
        topic = _clean_topic_candidate(sentences[0])
        if _is_specific_topic(topic):
            return topic
    return ""


def _extract_negative_topic(text: str, referenced_topic: str = "") -> tuple[str, bool]:
    """从负面表达中提取被触发的话题，并判断是否属于明确“不要再提”类信号。"""
    raw = (text or "").strip()
    if not raw:
        return "", False
    raw_lower = raw.lower()

    for pattern, explicit in _NEGATIVE_PATTERNS:
        match = pattern.search(raw)
        if match:
            topic = _clean_topic_candidate(match.group("topic"))
            if _is_specific_topic(topic):
                return topic, explicit

    explicit = any(token in raw_lower for token in _EXPLICIT_AVOID_TOKENS)
    referenced = _extract_topic_from_reference_text(referenced_topic)
    if explicit and referenced:
        return referenced, True
    # 回退：尝试从句子中剥离负面词，保留剩余较具体的主题描述。
    if explicit:
        fallback = raw
        for token in _NEGATIVE_KEYWORDS:
            fallback = fallback.replace(token, " ")
        fallback = re.sub(r'[，。、！？；：,.!?]', ' ', fallback)
        segments = [seg.strip() for seg in fallback.split() if seg.strip()]
        if segments:
            longest = max(segments, key=len)
            topic = _clean_topic_candidate(longest)
            if _is_specific_topic(topic):
                return topic, explicit
    return "", explicit


class PersonaManager:
    """Manages per-character persona files with dynamic entity sections.

    Core entities: 'master', 'neko', 'relationship'.
    Storage is entity-agnostic — any string key is accepted as an entity.
    Each entity section is ``{entity: {'facts': [...]}}``.
    """

    def __init__(self):
        self._config_manager = get_config_manager()
        self._personas: dict[str, dict] = {}

    # ── file paths ───────────────────────────────────────────────────

    def _persona_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'persona.json')

    def _corrections_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'persona_corrections.json')

    # ── CRUD ─────────────────────────────────────────────────────────

    def _empty_persona(self) -> dict:
        return {}

    def _is_persona_empty(self, persona: dict) -> bool:
        """Check if all fact/dynamics lists are empty."""
        for section in persona.values():
            if isinstance(section, dict):
                for lst in section.values():
                    if isinstance(lst, list) and lst:
                        return False
        return True

    def ensure_persona(self, name: str) -> dict:
        """Load or create persona. Auto-migrate from legacy settings if needed.

        每次调用时自动与 characters.json 同步 character_card 条目。
        """
        if name in self._personas:
            # 每次读取时同步 character card
            if self._sync_character_card(name, self._personas[name]):
                self.save_persona(name, self._personas[name])
            return self._personas[name]

        path = self._persona_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    migrated = self._migrate_v1_entity_keys(data)
                    if self._is_persona_empty(data):
                        self._migrate_from_settings(name, data)
                        migrated = True
                    # 同步 character card
                    if self._sync_character_card(name, data):
                        migrated = True
                    if migrated:
                        self.save_persona(name, data)
                    self._personas[name] = data
                    return data
                logger.warning(f"[Persona] {name}: persona 文件不是 dict，忽略")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Persona] 加载失败: {e}")

        # Auto-migrate from legacy settings
        persona = self._empty_persona()
        self._migrate_from_settings(name, persona)
        self._sync_character_card(name, persona)
        self._personas[name] = persona
        self.save_persona(name, persona)
        return persona

    @staticmethod
    def _migrate_v1_entity_keys(persona: dict) -> bool:
        """One-time migration: rename v1 entity keys and unify inner key to 'facts'.

        - 'user'  → 'master'
        - 'ai'    → 'neko'
        - relationship.dynamics → relationship.facts

        Returns True if any migration was performed.
        """
        changed = False

        # Rename top-level keys
        for old_key, new_key in [('user', 'master'), ('ai', 'neko')]:
            if old_key in persona and new_key not in persona:
                persona[new_key] = persona.pop(old_key)
                changed = True

        # Unify 'dynamics' → 'facts' for any section that still uses it
        for section in persona.values():
            if isinstance(section, dict) and 'dynamics' in section:
                section['facts'] = section.pop('dynamics')
                changed = True

        if changed:
            logger.info("[Persona] v1→v2 entity key 迁移完成 (user→master, ai→neko, dynamics→facts)")
        return changed

    @staticmethod
    def _card_entry_id(entity: str, field_name: str) -> str:
        """为 character card 条目生成确定性 ID（基于 entity + field_name 哈希）。"""
        raw = f"{entity}:{field_name}"
        return f"card_{hashlib.sha256(raw.encode()).hexdigest()[:8]}"

    def _migrate_from_settings(self, name: str, persona: dict) -> None:
        """One-time migration from legacy settings.json to persona format.

        仅迁移 settings.json（LLM 从对话中提取的设定）。
        角色卡数据（characters.json）的同步统一由 _sync_character_card() 负责，
        此方法不再处理角色卡，避免两处写入导致重复条目。
        """
        from memory import ensure_character_dir

        _, _, _, _, name_mapping, _, _, _, _ = (
            self._config_manager.get_character_data()
        )
        master_name = name_mapping.get('human', '主人')
        migrated_count = 0

        # 确保 persona 有核心 entity 结构，防止 KeyError
        for section_key in ('neko', 'master', 'relationship'):
            persona.setdefault(section_key, {}).setdefault('facts', [])

        def _is_migratable(val) -> bool:
            """仅排除 None、空白字符串、空容器；保留 0/False 等标量假值。"""
            if val is None:
                return False
            if isinstance(val, str):
                return bool(val.strip())
            if isinstance(val, (list, dict, set, tuple)):
                return len(val) > 0
            return True  # int 0, bool False 等合法标量

        # ── 从 settings.json 迁移（LLM 提取的设定）──
        char_dir = ensure_character_dir(self._config_manager.memory_dir, name)
        settings_path = os.path.join(char_dir, 'settings.json')
        if not os.path.exists(settings_path):
            old_path = os.path.join(str(self._config_manager.memory_dir), f'settings_{name}.json')
            if os.path.exists(old_path):
                settings_path = old_path
        if os.path.exists(settings_path):
            try:
                with open(settings_path, encoding='utf-8') as f:
                    old_settings = json.load(f)
                if isinstance(old_settings, dict):
                    # 按 target section 分别去重，避免跨 section 误去重
                    def _existing_texts_for(facts_list):
                        return {e.get('text', '') for e in facts_list if isinstance(e, dict)}

                    for section_key, facts_dict in old_settings.items():
                        if not isinstance(facts_dict, dict):
                            continue
                        if section_key == master_name or section_key == name_mapping.get('human', ''):
                            target = persona['master']['facts']
                        elif section_key == name:
                            target = persona['neko']['facts']
                        else:
                            target = persona['relationship']['facts']

                        seen = _existing_texts_for(target)
                        for k, v in facts_dict.items():
                            if _is_migratable(v):
                                text = f"{k}: {v}"
                                if text not in seen:
                                    entry = self._normalize_entry(text)
                                    content_hash = hashlib.sha256(text.encode()).hexdigest()[:8]
                                    entry['id'] = f"legacy_{content_hash}"
                                    entry['source'] = 'settings'
                                    target.append(entry)
                                    seen.add(text)
                                    migrated_count += 1
            except Exception as e:
                logger.warning(f"[Persona] {name}: settings.json 读取失败: {e}")

        if migrated_count:
            logger.info(f"[Persona] {name}: 迁移了 {migrated_count} 条 persona 数据（settings）")

    # ── character card 同步 ───────────────────────────────────────────

    def _sync_character_card(self, name: str, persona: dict) -> bool:
        """同步 character card 条目到 persona 头部，保持顺序与 characters.json 一致。

        规则：
        1. 读取当前 characters.json 的 neko/master 字段
        2. 为每个字段生成确定性 ID (card_{entity}_{hash})
        3. 与 persona 中 source=='character_card' 的条目对比
        4. 更新文本变化的、新增缺少的、删除 card 中已移除的
        5. card 条目始终排在 facts 列表头部，顺序与 card 一致

        Returns True if any change was made.
        """
        from config import CHARACTER_RESERVED_FIELDS

        try:
            _, _, master_basic_config, lanlan_basic_config, name_mapping, _, _, _, _ = (
                self._config_manager.get_character_data()
            )
        except Exception:
            return False

        excluded_fields = set(CHARACTER_RESERVED_FIELDS)
        changed = False

        def _is_syncable(val) -> bool:
            if val is None:
                return False
            if isinstance(val, str):
                return bool(val.strip())
            if isinstance(val, (list, dict, set, tuple)):
                return len(val) > 0
            return True

        def _build_expected(card_data: dict, entity: str) -> list[tuple[str, dict]]:
            """从 card 字段构建期望的 (id, entry) 列表，保持 card 字段顺序。"""
            expected = []
            for k, v in card_data.items():
                if k in excluded_fields or not _is_syncable(v):
                    continue
                if isinstance(v, (dict, set, tuple)):
                    continue
                if isinstance(v, list):
                    v = '、'.join(str(item) for item in v)
                entry_id = self._card_entry_id(entity, k)
                text = f"{k}: {v}"
                expected.append((entry_id, text))
            return expected

        def _sync_entity(entity: str, card_data: dict) -> bool:
            """同步单个 entity section。返回是否有变更。"""
            section = persona.setdefault(entity, {})
            facts = section.setdefault('facts', [])
            expected = _build_expected(card_data, entity)
            expected_ids = {eid for eid, _ in expected}

            # 分离 card 条目和非 card 条目
            existing_card = {}  # id → entry
            other_entries = []
            for entry in facts:
                if isinstance(entry, dict) and entry.get('source') == 'character_card':
                    existing_card[entry.get('id', '')] = entry
                else:
                    other_entries.append(entry)

            # 按 card 顺序构建新的 card 条目列表
            modified = False
            new_card_entries = []
            for eid, text in expected:
                if eid in existing_card:
                    entry = existing_card[eid]
                    if entry.get('text') != text:
                        # 文本变化 → 更新
                        old_text = entry.get('text', '')
                        entry['text'] = text
                        modified = True
                        logger.info(f"[Persona] {name}: card 同步更新 [{entity}] \"{old_text[:30]}\" → \"{text[:30]}\"")
                    new_card_entries.append(entry)
                else:
                    # 新字段 → 创建
                    entry = self._normalize_entry(text)
                    entry['id'] = eid
                    entry['source'] = 'character_card'
                    entry['protected'] = True
                    new_card_entries.append(entry)
                    modified = True
                    logger.info(f"[Persona] {name}: card 同步新增 [{entity}] \"{text[:40]}\"")

            # 检查是否有 card 中已删除的条目
            removed_ids = set(existing_card.keys()) - expected_ids
            if removed_ids:
                modified = True
                for rid in removed_ids:
                    removed_text = existing_card[rid].get('text', '')
                    logger.info(f"[Persona] {name}: card 同步移除 [{entity}] \"{removed_text[:40]}\"")


            if modified:
                # card 条目在前，其他条目在后
                section['facts'] = new_card_entries + other_entries

            return modified

        # 同步 neko entity
        if name in (lanlan_basic_config or {}):
            if _sync_entity('neko', lanlan_basic_config[name]):
                changed = True

        # 同步 master entity
        if master_basic_config and isinstance(master_basic_config, dict):
            if _sync_entity('master', master_basic_config):
                changed = True

        return changed

    def save_persona(self, name: str, persona: dict | None = None) -> None:
        if persona is None:
            persona = self._personas.get(name, self._empty_persona())
        self._personas[name] = persona
        atomic_write_json(self._persona_path(name), persona, indent=2, ensure_ascii=False)

    def get_persona(self, name: str) -> dict:
        return self.ensure_persona(name)

    # ── topic guidance ───────────────────────────────────────────────

    @staticmethod
    def _normalize_topic_entry(entry, *, default_state: str = 'soft') -> dict:
        defaults = {
            'topic': '',
            'state': default_state,      # soft | hard
            'trigger_count': 1,
            'created_at': None,
            'last_triggered_at': None,
            'source': 'negative_signal',
        }
        if isinstance(entry, dict):
            for key, value in defaults.items():
                entry.setdefault(key, value)
            return entry
        normalized = dict(defaults)
        normalized['topic'] = str(entry)
        return normalized

    def _get_topic_guidance(self, persona: dict) -> dict:
        guidance = persona.setdefault('_topic_guidance', {})
        soft = guidance.setdefault('soft_avoid', [])
        hard = guidance.setdefault('hard_avoid', [])
        normalized_soft = []
        for item in soft:
            entry = self._normalize_topic_entry(item, default_state='soft')
            if entry.get('topic'):
                normalized_soft.append(entry)
        normalized_hard = []
        for item in hard:
            entry = self._normalize_topic_entry(item, default_state='hard')
            if entry.get('topic'):
                normalized_hard.append(entry)
        guidance['soft_avoid'] = normalized_soft
        guidance['hard_avoid'] = normalized_hard
        return guidance

    @staticmethod
    def _find_topic_entry(entries: list[dict], topic: str) -> dict | None:
        for entry in entries:
            if isinstance(entry, dict) and entry.get('topic') == topic:
                return entry
        return None

    def register_negative_signal(self, name: str, user_text: str, referenced_topic: str = "") -> dict:
        """记录负面信号，并返回当前轮应该采用的回复指令。"""
        text = (user_text or "").strip()
        if not text or not _contains_negative_signal(text):
            return {
                'matched': False,
                'topic': '',
                'policy': 'none',
                'response_instruction': '',
            }

        topic, explicit_avoid = _extract_negative_topic(text, referenced_topic=referenced_topic)
        policy = 'tone_only'
        persona = self.ensure_persona(name)
        now_str = datetime.now().isoformat()
        changed = False

        if topic:
            guidance = self._get_topic_guidance(persona)
            soft_entries = guidance.get('soft_avoid', [])
            hard_entries = guidance.get('hard_avoid', [])
            soft_entry = self._find_topic_entry(soft_entries, topic)
            hard_entry = self._find_topic_entry(hard_entries, topic)

            if hard_entry is not None:
                hard_entry['trigger_count'] = int(hard_entry.get('trigger_count', 1)) + 1
                hard_entry['last_triggered_at'] = now_str
                policy = 'avoid'
                changed = True
            elif explicit_avoid:
                if soft_entry is not None:
                    soft_entries[:] = [entry for entry in soft_entries if entry.get('topic') != topic]
                hard_entries.append({
                    'topic': topic,
                    'state': 'hard',
                    'trigger_count': max(2, int(soft_entry.get('trigger_count', 1)) + 1 if soft_entry else 2),
                    'created_at': soft_entry.get('created_at', now_str) if soft_entry else now_str,
                    'last_triggered_at': now_str,
                    'source': 'negative_signal',
                })
                policy = 'avoid'
                changed = True
            elif soft_entry is not None:
                soft_entry['trigger_count'] = int(soft_entry.get('trigger_count', 1)) + 1
                soft_entry['last_triggered_at'] = now_str
                if soft_entry['trigger_count'] >= TOPIC_HARD_AVOID_THRESHOLD:
                    soft_entries[:] = [entry for entry in soft_entries if entry.get('topic') != topic]
                    hard_entries.append({
                        'topic': topic,
                        'state': 'hard',
                        'trigger_count': soft_entry['trigger_count'],
                        'created_at': soft_entry.get('created_at', now_str),
                        'last_triggered_at': now_str,
                        'source': 'negative_signal',
                    })
                    policy = 'avoid'
                else:
                    policy = 'de_emphasize'
                changed = True
            else:
                soft_entries.append({
                    'topic': topic,
                    'state': 'soft',
                    'trigger_count': 1,
                    'created_at': now_str,
                    'last_triggered_at': now_str,
                    'source': 'negative_signal',
                })
                policy = 'de_emphasize'
                changed = True

        if changed:
            self.save_persona(name, persona)

        if policy == 'avoid' and topic:
            instruction = (
                f"用户刚刚对“{topic}”表现出明显负面情绪。当前回复先共情安抚，"
                f"不要继续展开这个话题，也不要主动再次提起它；如需回应，只做简短承接并温和换题。"
            )
        elif policy == 'de_emphasize' and topic:
            instruction = (
                f"用户刚刚对“{topic}”表现出负面情绪。当前回复先共情安抚，"
                f"尽量减少对这个话题的提及，不要继续深挖或主动延展，优先缓和情绪。"
            )
        else:
            instruction = "用户刚刚表现出负面情绪。当前回复请先共情安抚，语气更柔和，避免追问和说教。"

        return {
            'matched': True,
            'topic': topic,
            'policy': policy,
            'response_instruction': instruction,
        }

    # ── entry normalization ──────────────────────────────────────────

    @staticmethod
    def _normalize_entry(entry) -> dict:
        """将纯字符串条目迁移为 dict 格式。

        每个条目包含以下溯源字段：
        - id: 唯一标识。card_xxx / legacy_xxx / prom_xxx / manual_xxx
        - source: 来源类型。character_card / settings / reflection / manual
        - source_id: 上游 ID（如 reflection_id），用于追溯来源链
        """
        defaults = {
            'id': '',                   # 唯一标识
            'text': '',
            'source': 'unknown',        # character_card | settings | reflection | manual
            'source_id': None,          # 上游 ID（reflection_id 等）
            'recent_mentions': [],      # 窗口内提及时间戳列表
            'suppress': False,          # 是否被抑制
            'suppressed_at': None,      # suppress 开始时间
            'protected': False,         # character_card 来源条目，不可 suppress
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

    # add_fact return codes
    FACT_ADDED = 'added'
    FACT_REJECTED_CARD = 'rejected_card'      # contradicts character_card → permanent
    FACT_QUEUED_CORRECTION = 'queued'         # contradicts non-card → correction queue

    def add_fact(self, name: str, text: str, entity: str = 'master',
                 source: str = 'manual', source_id: str | None = None) -> str:
        """Add a confirmed fact to persona. Checks for contradictions first.

        Args:
            source: 来源类型 (reflection / manual / ...)
            source_id: 上游 ID，如 reflection_id (ref_xxx)

        Returns:
            FACT_ADDED            — successfully appended
            FACT_REJECTED_CARD    — contradicts character_card, permanently blocked
            FACT_QUEUED_CORRECTION — contradicts existing non-card fact, queued for LLM review
        """
        persona = self.ensure_persona(name)
        section_facts = self._get_section_facts(persona, entity)
        stop_names = self._get_entity_stop_names()

        for existing in section_facts:
            if isinstance(existing, dict):
                old_text = existing.get('text', '')
                is_card = existing.get('source') == 'character_card'
            else:
                old_text = str(existing)
                is_card = False
            if self._texts_may_contradict(old_text, text, stop_names=stop_names):
                if is_card:
                    logger.info(
                        f"[Persona] {name}: 新条目与角色卡矛盾，无条件拒绝: "
                        f"card=\"{old_text[:40]}\" vs new=\"{text[:40]}\""
                    )
                    return self.FACT_REJECTED_CARD
                self._queue_correction(name, old_text, text, entity)
                return self.FACT_QUEUED_CORRECTION

        entry = self._normalize_entry(text)
        if source == 'reflection' and source_id:
            entry['id'] = f"prom_{source_id}"
        else:
            entry['id'] = f"manual_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hashlib.sha256(text.encode()).hexdigest()[:8]}"
        entry['source'] = source
        entry['source_id'] = source_id
        section_facts.append(entry)
        self.save_persona(name, persona)
        return self.FACT_ADDED

    def _get_section_facts(self, persona: dict, entity: str) -> list:
        return persona.setdefault(entity, {}).setdefault('facts', [])

    def _get_entity_stop_names(self) -> list[str]:
        """Return master + neko names to strip from contradiction keywords."""
        try:
            master_name, her_name, _, _, _, _, _, _, _ = (
                self._config_manager.get_character_data()
            )
            names = []
            if master_name:
                names.append(master_name)
            if her_name:
                names.append(her_name)
            return names
        except Exception:
            return []

    @staticmethod
    def _texts_may_contradict(old_text: str, new_text: str,
                              stop_names: list[str] | None = None) -> bool:
        """Lightweight keyword-overlap heuristic for contradiction detection.

        Uses the same CJK-aware tokenization as _is_mentioned.
        ``stop_names`` — entity names (master/neko) whose n-grams are
        stripped from both sides so that shared names alone don't inflate
        the overlap ratio.
        """
        if not old_text or not new_text:
            return False
        old_kw = _extract_keywords(old_text)
        new_kw = _extract_keywords(new_text)
        if stop_names:
            stop_kw: set[str] = set()
            for sn in stop_names:
                stop_kw |= _extract_keywords(sn)
            old_kw -= stop_kw
            new_kw -= stop_kw
        if not old_kw or not new_kw:
            return False
        overlap = old_kw & new_kw
        ratio = len(overlap) / min(len(old_kw), len(new_kw))
        return ratio >= 0.4

    # ── contradiction queue ──────────────────────────────────────────

    def _queue_correction(self, name: str, old_text: str, new_text: str, entity: str) -> None:
        corrections = self.load_pending_corrections(name)
        for existing in corrections:
            if (existing.get('old_text') == old_text
                    and existing.get('new_text') == new_text
                    and existing.get('entity') == entity):
                return
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
            if not isinstance(result, dict):
                continue
            try:
                idx = int(result.get('index', -1))
                if idx < 0 or idx >= len(corrections):
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
            # Only remove processed corrections, keep unprocessed ones
            processed_indices: set[int] = set()
            for r in results:
                raw_idx = r.get('index')
                if raw_idx is None:
                    continue
                try:
                    processed_indices.add(int(raw_idx))
                except (ValueError, TypeError):
                    continue
            remaining = [c for i, c in enumerate(corrections) if i not in processed_indices]
            atomic_write_json(self._corrections_path(name), remaining,
                              indent=2, ensure_ascii=False)
            logger.info(f"[Persona] {name}: 批量审视完成 {resolved} 条矛盾，剩余 {len(remaining)} 条")
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
            if entry.get('protected'):
                continue  # 硬编码条目不参与 suppress 计数
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
        """收集 persona 中所有 entity section 的 facts 条目引用。"""
        entries = []
        for section in persona.values():
            if isinstance(section, dict):
                entries.extend(section.get('facts', []))
        return entries

    # ── rendering ────────────────────────────────────────────────────

    def render_persona_markdown(self, name: str, pending_reflections: list[dict] | None = None,
                                   confirmed_reflections: list[dict] | None = None) -> str:
        """Render persona as markdown for LLM context injection.

        Suppressed entries are rendered in a separate "暂不主动提及" section,
        NOT in their original sections. suppress has highest priority.
        """
        # Refresh suppressions before rendering so expired cooldowns are released
        self.update_suppressions(name)
        persona = self.ensure_persona(name)
        _, _, _, _, name_mapping, _, _, _, _ = self._config_manager.get_character_data()
        master_name = name_mapping.get('human', '主人')
        ai_name = name

        # Entity key → section header mapping for known entities
        _headers = {
            'master': f"关于{master_name}",
            'neko': f"关于{ai_name}",
            'relationship': "关系动态",
        }

        sections = []

        # Collect suppressed items across all sections
        suppressed_lines = []
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress'):
                text = entry.get('text', '')
                if text:
                    suppressed_lines.append(f"- {text}")

        # Render each entity section dynamically
        for entity_key, section in persona.items():
            if not isinstance(section, dict):
                continue
            facts = section.get('facts', [])
            lines = self._render_fact_entries(facts)
            if lines:
                header = _headers.get(entity_key, entity_key)
                sections.append(f"### {header}\n" + "\n".join(lines))

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

        # Confirmed reflections (软 persona：比 pending 更稳固，但仍可修正)
        if confirmed_reflections:
            confirmed_lines = []
            for r in confirmed_reflections:
                text = r.get('text', '')
                if text and not self._is_suppressed_text(persona, text):
                    confirmed_lines.append(f"- {text}")
            if confirmed_lines:
                sections.append(
                    f"### {ai_name}比较确定的印象\n"
                    + "\n".join(confirmed_lines)
                )

        topic_guidance = self._get_topic_guidance(persona)
        soft_topics = [entry.get('topic', '') for entry in topic_guidance.get('soft_avoid', []) if entry.get('topic')]
        hard_topics = [entry.get('topic', '') for entry in topic_guidance.get('hard_avoid', []) if entry.get('topic')]

        if soft_topics:
            sections.append(
                "### 当前应谨慎提及的话题\n"
                + "\n".join(
                    f"- {topic}（用户最近对此表现出负面情绪；若再次聊到，先安抚，不要主动展开）"
                    for topic in soft_topics
                )
            )

        if hard_topics:
            sections.append(
                "### 不要主动提及的话题\n"
                + "\n".join(
                    f"- {topic}（用户已经明确表现出抗拒，除非用户主动重新提起，否则不要主动提及）"
                    for topic in hard_topics
                )
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
