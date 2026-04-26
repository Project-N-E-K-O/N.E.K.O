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
import hashlib
import json
import os
import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from config import (
    EVIDENCE_CONFIRMED_THRESHOLD,
    EVIDENCE_PROMOTE_MAX_RETRIES,
    EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES,
    EVIDENCE_PROMOTED_THRESHOLD,
    EVIDENCE_PROMOTION_MERGE_MODEL_TIER,
    SETTING_PROPOSER_MODEL,
)
from memory.evidence import evidence_score, initial_reinforcement_from_importance
from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.config_manager import get_config_manager
from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from memory.persona import (
    PersonaManager,
    SUPPRESS_COOLDOWN_HOURS,
    SUPPRESS_MENTION_LIMIT,
    SUPPRESS_WINDOW_HOURS,
    _is_mentioned,
)

if TYPE_CHECKING:
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager

logger = get_module_logger(__name__, "Memory")

# Minimum unabsorbed facts to trigger reflection synthesis
MIN_FACTS_FOR_REFLECTION = 5

# memory-evidence-rfc §3.2.2: new/updated reflection status vocabulary.
# pending | confirmed | denied | promoted | merged | archived | promote_blocked
# `merged` = LLM merge_into 吸收到某 persona entry（reflection 保留带 absorbed_into 溯源）
# `promote_blocked` = LLM 连续失败触发的死信状态（需人工或 user signal 重置）
REFLECTION_TERMINAL_STATUSES = frozenset({
    'promoted', 'denied', 'archived', 'merged', 'promote_blocked',
})

# ── Reflection ontology (RFC memory-enhancements §3) ────────────────
# Constrains the semantic category of each synthesized reflection so
# same-(entity, relation_type) entries are naturally mergeable and the
# render path can group by role-of-information instead of flat-listing.
#
# The schema is **kind-based, not name-based**: every entity gets mapped
# to one of three kinds (user / character / relationship) and the
# allowed relation_type set follows from the kind. Today we have one
# user (`master`) and one character (`neko`) — but for future group-chat
# extensions, every additional human plays role-kind `user` and reuses
# the same relation set, every additional AI character plays role-kind
# `character`, and a single shared `relationship` slot covers the
# group-as-a-whole. New `entity` names plug in without touching
# RELATION_TYPES or the prompt enum.
RELATION_TYPES = {
    # `user` kind — facts about a human participant
    'preference':     '偏好/喜好',
    'trait':          '性格/特征',
    'habit':          '习惯/日常',
    'identity':       '身份/背景',
    'emotional':      '情感状态',
    'boundary':       '边界/禁忌',
    # `character` kind — AI self-model
    'self_awareness': '自我认知',
    'learned':        '习得行为',
    'role_note':      '角色备注',
    # `relationship` kind — the group as a whole (1v1 today, NvM later)
    'dynamic':        '互动模式',
    'milestone':      '关系里程碑',
    'tension':        '摩擦/冲突',
    'shared_memory': '共同记忆',
    'agreement':     '约定/共识',
}

# Entity → kind mapping. New entity names default to `user` kind so that
# adding "guest_alice" or "groupmate_bob" to the chat just works without
# a schema migration. Override here only when an entity needs a non-user
# template (i.e. another AI character, or a relationship aggregate).
ENTITY_KINDS: dict[str, str] = {
    'master':       'user',
    'neko':         'character',
    'relationship': 'relationship',
}

# Allowed relation_type set per kind. This is the actual source of truth
# for validation; ENTITY_KINDS is the indirection that lets us add new
# entities without expanding this table.
KIND_RELATION_MAP: dict[str, frozenset[str]] = {
    'user':         frozenset({'preference', 'trait', 'habit', 'identity', 'emotional', 'boundary'}),
    'character':    frozenset({'self_awareness', 'learned', 'role_note'}),
    'relationship': frozenset({'dynamic', 'milestone', 'tension', 'shared_memory', 'agreement'}),
}


def _entity_kind(entity: str) -> str:
    """Resolve an entity name to its ontology kind.

    Unknown entities default to ``user`` — the safest fallback because
    user-kind has the richest relation set, and future group members are
    overwhelmingly likely to be additional human participants. Existing
    callers ignore this default by passing the canonical entity names
    (master / neko / relationship) we already register above.
    """
    return ENTITY_KINDS.get(entity, 'user')


def _allowed_relation_types(entity: str) -> frozenset[str]:
    return KIND_RELATION_MAP.get(_entity_kind(entity), frozenset())


TEMPORAL_SCOPES = frozenset({'current', 'past', 'ongoing'})

# Soft cap on reflection text length. Beyond this, the ontology fields
# are stripped because the text is likely a compound/multi-fact statement.
# The reflection itself still persists unchanged so no information is lost.
MAX_REFLECTION_TEXT_CHARS = 200


def _validate_reflection_ontology(
    entity: str,
    relation_type: str | None,
    temporal_scope: str | None,
    text: str,
) -> tuple[bool, str]:
    """Returns (is_valid, reason). Invalid entries keep their text but
    have relation_type/temporal_scope stripped — see caller."""
    if relation_type is not None:
        if relation_type not in RELATION_TYPES:
            return False, f'unknown relation_type: {relation_type!r}'
        allowed = _allowed_relation_types(entity)
        if relation_type not in allowed:
            return (
                False,
                f'{relation_type!r} not valid for entity {entity!r} '
                f'(kind={_entity_kind(entity)!r})',
            )
    if temporal_scope is not None and temporal_scope not in TEMPORAL_SCOPES:
        return False, f'unknown temporal_scope: {temporal_scope!r}'
    if len(text) > MAX_REFLECTION_TEXT_CHARS:
        return False, f'text too long: {len(text)} chars (compound risk)'
    return True, 'ok'


def _reflection_id_from_facts(source_fact_ids: list[str]) -> str:
    """根据 source fact ids 生成确定性 reflection id（P1 幂等性核心）。

    同一批 facts 的重复合成产生相同 id，save_reflections + mark_absorbed
    这对"半原子"操作在 kill 后重启重跑时可基于 id 去重——消灭致命点 3。

    取 sha256 前 16 字符（64 bit）。单角色 reflection 规模远低于生日碰撞阈值。
    """
    h = hashlib.sha256()
    for fid in sorted(source_fact_ids):
        h.update(fid.encode('utf-8'))
        h.update(b'\x00')  # 分隔符防 "ab" + "c" 与 "a" + "bc" 冲突
    return f"ref_{h.hexdigest()[:16]}"
# memory-evidence-rfc §3.9.1：time-based auto-promotion 删除。
# pending → confirmed / confirmed → promoted 改由 evidence_score 穿阈值
# 触发（§3.1.4）。本 PR (PR-1) 只实现 pending → confirmed 的 score 驱动；
# confirmed → promoted 的 merge-on-promote 路径在 PR-3。
# Cooldown between proactive chat candidacy
REFLECTION_COOLDOWN_MINUTES = 30
# promoted/denied reflections older than this are moved to archive
_REFLECTION_ARCHIVE_DAYS = 30


class ReflectionEngine:
    """Synthesizes facts into reflections and manages the pending → confirmed lifecycle."""

    def __init__(
        self, fact_store: FactStore, persona_manager: PersonaManager,
        event_log: EventLog | None = None,
    ):
        self._config_manager = get_config_manager()
        self._fact_store = fact_store
        self._persona_manager = persona_manager
        # memory-evidence-rfc §3.3.3：evidence 写路径必须走 record_and_save。
        # event_log 注入；None 时 aapply_signal 不可用（冷启动 / 纯单元测试
        # 路径仍可用 synthesize / auto_promote 等不触 evidence 的方法）。
        self._event_log = event_log
        # Per-character asyncio.Lock (P2.a.2). ReflectionEngine's async mutating
        # methods span multiple awaits (e.g. aauto_promote_stale calls
        # persona.aadd_fact across an await boundary) — so asyncio.Lock is the
        # right choice per CLAUDE rule "threading.Lock 持锁跨 await → 改用
        # asyncio.Lock". Lock is lazily created to avoid event-loop binding
        # at module-import time.
        self._alocks: dict[str, asyncio.Lock] = {}
        # threading.Lock guards the dict itself (reads/writes of _alocks are
        # pure Python, no await inside this critical section).
        self._alocks_guard = threading.Lock()

    def _get_alock(self, name: str) -> asyncio.Lock:
        """Get (or lazily create) the per-character asyncio.Lock.

        Thread-safety scope: this method is called from the single
        FastAPI event-loop thread, never from asyncio.to_thread workers.
        The outer `name not in self._alocks` check is therefore single-
        threaded by construction. The inner check inside the guard is
        for multi-loop robustness (e.g. test harnesses that spin up a
        fresh loop per test). Matches the DCL pattern already used in
        facts.py / outbox.py / cursors.py.

        asyncio.Lock binding: on CPython 3.10+ Lock binds to the running
        loop at first `acquire`/`__aenter__`, not at `__init__`. Lazy
        construction here is defensive for 3.9 and cleaner for fresh-
        loop tests; not strictly required on the target 3.11 runtime.
        """
        if name not in self._alocks:
            with self._alocks_guard:
                if name not in self._alocks:
                    self._alocks[name] = asyncio.Lock()
        return self._alocks[name]

    # ── file paths ───────────────────────────────────────────────────

    def _reflections_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'reflections.json')

    def _reflections_archive_dir(self, name: str) -> str:
        """Sharded archive directory (RFC §3.5.4).

        Replaces the legacy flat ``reflections_archive.json`` file. The
        directory is created lazily by `aappend_to_shard` on first
        archive event so an idle character never carries an empty dir.
        """
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'reflection_archive',
        )

    def _reflections_legacy_archive_path(self, name: str) -> str:
        """Legacy flat archive path (pre-RFC §3.5.4). Kept for the
        one-shot migration in `aone_shot_archive_migration` and to keep
        the migration sentinel test-discoverable. NOT referenced by any
        write path."""
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'reflections_archive.json',
        )

    def _surfaced_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'surfaced.json')

    # ── persistence ──────────────────────────────────────────────────

    @staticmethod
    def _normalize_reflection(entry: dict) -> dict:
        """Fill evidence/archive/promote 节流等新字段的默认值 in-place.

        Schema extension from memory-evidence-rfc §3.2.2. Defaults do NOT
        back-fill to "migrated" seed values — that's the migration-seed path
        (§5.2), which goes through aapply_signal so it's also event-sourced.
        Here we only guarantee the fields exist so downstream code
        (`evidence_score`, `derive_status` …) doesn't KeyError on legacy data.

        Also adds the `recent_mentions` / `suppress` mention抑制 fields so
        confirmed reflections can share persona's 5h-window rate-limit
        machinery (AI 自我克制，不在 5h 内反复提同一条)。

        Note on token-count cache: persona entries carry a
        `token_count` / `token_count_text_sha256` / `token_count_tokenizer`
        cache that survives across renders because `PersonaManager._personas`
        is a process-resident view — render-time cache writes land on the
        same dict that the next render reads. Reflections do NOT have an
        equivalent in-memory view; `aload_reflections` /
        `_aload_reflections_full` always read fresh from disk, so any cache
        writeback on a reflection entry would be garbage-collected right
        after the render. We deliberately do NOT add the cache fields to
        this schema — populating them would be misleading (they'd look like
        a working cache but every render still tokenizes from scratch).
        Reflection tokenization stays live; in typical workloads the
        persona-side cache captures the bulk of render time anyway.
        """
        defaults = {
            # Evidence counters
            'reinforcement': 0.0,
            'disputation': 0.0,
            'rein_last_signal_at': None,
            'disp_last_signal_at': None,
            'sub_zero_days': 0,
            'sub_zero_last_increment_date': None,
            # user_fact reinforces combo counter (RFC §3.1.8)
            'user_fact_reinforce_count': 0,
            # Merge / archive 溯源
            'absorbed_into': None,
            # Promote 节流
            'last_promote_attempt_at': None,
            'promote_attempt_count': 0,
            'promote_blocked_reason': None,
            # AI-mention rate-limit，和 persona 同机制
            'recent_mentions': [],
            'suppress': False,
            'suppressed_at': None,
            # Ontology fields (RFC memory-enhancements §3). All optional —
            # legacy entries and validation-stripped entries both read None.
            'relation_type': None,
            'subject': None,
            'temporal_scope': None,
            # Vector-embedding cache (memory-enhancements P2 — see
            # memory/embeddings.py). Reflection text is immutable in the
            # current pipeline (synthesis writes once, callers don't
            # rewrite text), so embeddings here only invalidate when
            # the model_id flips. Legacy entries read None and the
            # warmup worker fills them in on the next pass.
            'embedding': None,
            'embedding_text_sha256': None,
            'embedding_model_id': None,
        }
        for k, v in defaults.items():
            entry.setdefault(k, v)
        return entry

    @classmethod
    def _filter_reflections(cls, data, include_archived: bool, path: str) -> list[dict]:
        if not isinstance(data, list):
            logger.warning(f"[Reflection] reflections 文件不是列表，忽略: {path}")
            return []
        items = [
            cls._normalize_reflection(item)
            for item in data if isinstance(item, dict) and 'id' in item
        ]
        if not include_archived:
            # Hides every terminal status (promoted / denied / merged /
            # archived / promote_blocked) from active reads — reading from
            # the shared constant so PR-3's promote_blocked dead-letter
            # state is excluded without an additional edit here.
            items = [
                r for r in items
                if r.get('status') not in REFLECTION_TERMINAL_STATUSES
            ]
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
        """Pure logic: compute (merged_main, to_archive, keep_in_main).

        RFC §3.11.3: `merged` and `promote_blocked` terminals stay in the
        main file indefinitely (merged carries `absorbed_into` for trace
        chains; promote_blocked is dead-letter awaiting manual / new-signal
        reset). Only `promoted` and `denied` are candidates for the
        `_REFLECTION_ARCHIVE_DAYS` age-based archival split.

        CodeRabbit PR #929 fix: earlier this function only preserved
        `promoted|denied` from `all_on_disk`, so when `_aauto_promote_stale_locked`
        filters its active set through REFLECTION_TERMINAL_STATUSES (which
        includes `merged`), any merged entry on disk would silently vanish
        from the main file on save.
        """
        active_ids = {r['id'] for r in reflections if 'id' in r}
        cutoff = datetime.now() - timedelta(days=_REFLECTION_ARCHIVE_DAYS)
        keep_in_main: list[dict] = []
        to_archive: list[dict] = []
        for r in all_on_disk:
            if r.get('id') in active_ids:
                continue
            status = r.get('status')
            if status in ('promoted', 'denied'):
                ts_key = (r.get('promoted_at') or r.get('denied_at')
                          or r.get('created_at', ''))
                try:
                    if datetime.fromisoformat(ts_key) < cutoff:
                        to_archive.append(r)
                        continue
                except (ValueError, TypeError):
                    # 时间戳缺失/格式异常：不归档，落回 main 保守保留
                    pass
                keep_in_main.append(r)
            elif status in ('merged', 'promote_blocked'):
                # Non-archivable terminal states — must survive save cycles
                keep_in_main.append(r)
            # `archived` should never appear on disk already (that's a
            # post-archive state for the shard file) — drop silently if
            # it sneaks in here.
        merged = reflections + keep_in_main
        return merged, to_archive, keep_in_main

    @staticmethod
    def _make_archive_stamper(now_iso: str):
        """Return a ``stamper(chunk, shard_basename)`` callback for
        ``aappend_to_shard`` / ``append_to_shard_sync``. RFC §3.5.6:
        "归档后字段追加：archived_at（ISO8601）和 archive_shard_path
        （相对路径字符串）"。

        We store the basename only (relative to the kind-archive dir) —
        keeps logs short and survives memory_dir relocation.

        Why a callback (chatgpt-codex / coderabbit review #934): the
        previous "stamp after write" path mutated only the in-memory
        list, so on-disk records lacked both fields; and in the overflow
        case where one batch spilled into multiple shards, the single
        returned ``shard_path`` couldn't describe per-entry locations.
        Per-chunk stamping inside ``aappend_to_shard`` fixes both.

        Coderabbit PR #934 round-3 Minor: ``archived_at`` uses direct
        assignment (not ``setdefault``) so cross-day retries restamp
        with the actual write date. Earlier ``setdefault`` would
        preserve the first failed attempt's timestamp; if shard write
        failed and ``save_reflections`` rolled the entries back into
        main with ``archived_at`` already attached, a next-day retry
        would update ``archive_shard_path`` to the new day's basename
        but leave ``archived_at`` pointing at yesterday — the two
        fields would disagree on the date. Losing the first stamp is
        fine because the entry never landed in any shard at that point.
        """
        def _stamp(chunk: list[dict], shard_basename: str) -> None:
            for e in chunk:
                e['archived_at'] = now_iso
                e['archive_shard_path'] = shard_basename
        return _stamp

    def save_reflections(self, name: str, reflections: list[dict]) -> None:
        """Save reflections, archiving stale promoted/denied entries to shards.

        promoted/denied 超过 _REFLECTION_ARCHIVE_DAYS 的条目自动移入分片归档
        目录 (RFC §3.5.4)。This path covers the EXISTING age-based archival —
        score-driven `sub_zero_days >= EVIDENCE_ARCHIVE_DAYS` archival uses
        the dedicated `aarchive_reflection` event-sourced path instead.
        """
        from memory.archive_shards import append_to_shard_sync
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/reflections.json",
        )
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
            archive_dir = self._reflections_archive_dir(name)
            try:
                stamper = self._make_archive_stamper(datetime.now().isoformat())
                shard_path = append_to_shard_sync(
                    archive_dir, list(to_archive), stamper=stamper,
                )
                logger.info(
                    f"[Reflection] {name}: 归档 {len(to_archive)} 条旧 reflections → {os.path.basename(shard_path)}"
                )
            except OSError as e:
                # Fall-back: keep the entries in main file rather than lose
                # them. Same posture as the old flat-file path.
                logger.warning(
                    f"[Reflection] {name}: 写归档分片失败，回滚到 main 保留: {e}"
                )
                merged = merged + to_archive

        atomic_write_json(path, merged, indent=2, ensure_ascii=False)

    async def asave_reflections(self, name: str, reflections: list[dict]) -> None:
        from memory.archive_shards import aappend_to_shard
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/reflections.json",
        )
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
            archive_dir = self._reflections_archive_dir(name)
            try:
                stamper = self._make_archive_stamper(datetime.now().isoformat())
                shard_path = await aappend_to_shard(
                    archive_dir, list(to_archive), stamper=stamper,
                )
                logger.info(
                    f"[Reflection] {name}: 归档 {len(to_archive)} 条旧 reflections → {os.path.basename(shard_path)}"
                )
            except OSError as e:
                logger.warning(
                    f"[Reflection] {name}: 写归档分片失败，回滚到 main 保留: {e}"
                )
                merged = merged + to_archive

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
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/surfaced.json",
        )
        atomic_write_json(self._surfaced_path(name), surfaced, indent=2, ensure_ascii=False)

    async def asave_surfaced(self, name: str, surfaced: list[dict]) -> None:
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/surfaced.json",
        )
        await atomic_write_json_async(self._surfaced_path(name), surfaced, indent=2, ensure_ascii=False)

    # ── synthesis ────────────────────────────────────────────────────

    async def synthesize_reflections(self, lanlan_name: str) -> list[dict]:
        """Synthesize pending reflections from accumulated unabsorbed facts.

        Called during proactive chat. Returns newly created reflections.

        幂等性（P1 修复致命点 3）：
          1. reflection id 由 source_fact_ids 决定（_reflection_id_from_facts）。
          2. LLM 调用前先查：同一批 unabsorbed facts 对应的 id 若已在
             reflections.json 中存在 → 跳过 LLM、仅补跑 mark_absorbed（幂等）。
          3. save_reflections 亦按 id dedup，防 concurrent synth 双写。
          4. 始终在末尾 amark_absorbed，确保 save 成功但 mark 失败后的
             重启补跑能真正把 facts 的 absorbed 置为 True。

        并发（P2.a.2）：整个方法在角色级 asyncio.Lock 下串行，避免与
        aauto_promote_stale / aconfirm_promotion 竞写 reflections.json。
        """
        async with self._get_alock(lanlan_name):
            return await self._synthesize_reflections_locked(lanlan_name)

    async def _synthesize_reflections_locked(self, lanlan_name: str) -> list[dict]:
        """synthesize_reflections 的内部实现。调用方必须已持有
        self._get_alock(lanlan_name)。"""
        from config.prompts_memory import get_reflection_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm

        unabsorbed = await self._fact_store.aget_unabsorbed_facts(lanlan_name)
        if len(unabsorbed) < MIN_FACTS_FOR_REFLECTION:
            return []

        # 排序一次：on-disk 字段与 _reflection_id_from_facts 内部 sorted 对齐，
        # 消除 "hash 用 sorted，存盘不 sorted" 的隐式非对称
        source_fact_ids = sorted(f['id'] for f in unabsorbed)
        rid = _reflection_id_from_facts(source_fact_ids)

        # 幂等 short-circuit：同一批 facts 的 reflection 已持久化 →
        # 不重复调 LLM，仅补跑 mark_absorbed（致命点 3 的重启补救路径）
        existing_reflections = await self.aload_reflections(lanlan_name)
        existing = next((r for r in existing_reflections if r.get('id') == rid), None)
        if existing is not None:
            await self._fact_store.amark_absorbed(lanlan_name, source_fact_ids)
            logger.info(
                f"[Reflection] {lanlan_name}: 检测到同批 facts 已合成过 reflection "
                f"{rid}，跳过 LLM，补跑 mark_absorbed"
            )
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
            return []

        if not reflection_text:
            return []

        # Create pending reflection — id 已在函数开头由 source_fact_ids 决定
        now = datetime.now()
        now_iso = now.isoformat()

        # Importance-based initial rein seed：让"关键节点"型 reflection 起步
        # 就带一点正分，不必等多轮 user confirms 才穿越 CONFIRMED 阈值。
        # 不走 aapply_signal（synthesis 本身不经 event log），直接写进初始
        # 字典——synth 不是 event-sourced，这些初始值就是 ground truth。
        max_importance = max(
            (int(f.get('importance', 5) or 5) for f in unabsorbed),
            default=5,
        )
        initial_rein = initial_reinforcement_from_importance(max_importance)

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
        })

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

        # 无条件 mark_absorbed：幂等，且覆盖 save 成功后但在此崩溃的补跑情况
        await self._fact_store.amark_absorbed(lanlan_name, source_fact_ids)

        if not created:
            # 并发分支已落盘对方的对象；返回内存副本会让调用方拿到一个
            # 未持久化、可能与磁盘版文本不同的"幽灵反思"，违反"返回值
            # = 本调用真正新建的反思"语义。
            return []
        logger.info(f"[Reflection] {lanlan_name}: 合成了新反思 {rid}: {reflection_text[:50]}...")
        return [reflection]

    # alias for backward compat (system_router calls .reflect())
    async def reflect(self, lanlan_name: str) -> dict | None:
        """Alias for synthesize_reflections. Returns first reflection or None."""
        results = await self.synthesize_reflections(lanlan_name)
        return results[0] if results else None

    # ── evidence signals (RFC §3.4, §3.8.4) ─────────────────────────

    @staticmethod
    def _find_reflection_in_list(reflections: list[dict], rid: str) -> dict | None:
        for r in reflections:
            if isinstance(r, dict) and r.get('id') == rid:
                return r
        return None

    # Delegated to memory.evidence.compute_evidence_snapshot — shared with
    # PersonaManager so rein/disp/combo semantics stay in one place.
    @staticmethod
    def _compute_evidence_after_delta(
        entry: dict, delta: dict, now_iso: str, source: str = 'unknown',
    ) -> dict:
        from memory.evidence import compute_evidence_snapshot
        return compute_evidence_snapshot(entry, delta, now_iso, source)

    async def aapply_signal(
        self, lanlan_name: str, reflection_id: str, delta: dict, source: str,
    ) -> bool:
        """Mutate one reflection's evidence via EVT_REFLECTION_EVIDENCE_UPDATED.

        record_and_save 合约（RFC §3.3.3）：
          load → append event → mutate view → save view → advance sentinel.

        Returns True if applied; False if reflection not found (LLM may point
        at a stale id; signals are best-effort).
        """
        from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection.aapply_signal] event_log 未注入；"
                "ReflectionEngine() 构造时须传入 event_log"
            )

        async with self._get_alock(lanlan_name):
            reflections_full = await self._aload_reflections_full(lanlan_name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                logger.warning(
                    f"[Reflection] {lanlan_name}: aapply_signal 找不到 reflection_id={reflection_id}"
                )
                return False

            now_iso = datetime.now().isoformat()
            snapshot = self._compute_evidence_after_delta(
                entry, delta, now_iso, source,
            )
            payload = {
                'reflection_id': reflection_id,
                'reinforcement': snapshot['reinforcement'],
                'disputation': snapshot['disputation'],
                'rein_last_signal_at': snapshot['rein_last_signal_at'],
                'disp_last_signal_at': snapshot['disp_last_signal_at'],
                'sub_zero_days': snapshot['sub_zero_days'],
                'user_fact_reinforce_count': snapshot['user_fact_reinforce_count'],
                'source': source,
            }

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(_view):
                entry['reinforcement'] = snapshot['reinforcement']
                entry['disputation'] = snapshot['disputation']
                entry['rein_last_signal_at'] = snapshot['rein_last_signal_at']
                entry['disp_last_signal_at'] = snapshot['disp_last_signal_at']
                entry['sub_zero_days'] = snapshot['sub_zero_days']
                entry['user_fact_reinforce_count'] = snapshot['user_fact_reinforce_count']

            def _sync_save(n: str, view):
                # Gate write behind the same cloudsave check as
                # save_reflections/asave_reflections — the evidence mutation
                # path must honour read-only/maintenance mode (CodeRabbit PR #929).
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view, indent=2, ensure_ascii=False,
                )

            await self._event_log.arecord_and_save(
                lanlan_name, EVT_REFLECTION_EVIDENCE_UPDATED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            return True

    # ── score-driven archive (RFC §3.5) ─────────────────────────────

    async def aincrement_sub_zero(
        self, lanlan_name: str, reflection_id: str, now: datetime,
    ) -> int | None:
        """Increment one reflection's `sub_zero_days` via EVT_REFLECTION_EVIDENCE_UPDATED.

        Called by the periodic archive sweep (`memory_server._periodic_archive_sweep_loop`).
        Goes through `arecord_and_save` so the increment is audit-logged
        and replayable — derived state but worth recording.

        Returns the new `sub_zero_days` value if incremented, or None if
        no increment happened (entry not found / protected / already
        incremented today / score >= 0).
        """
        from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
        from memory.evidence import maybe_mark_sub_zero
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection.aincrement_sub_zero] event_log 未注入"
            )

        async with self._get_alock(lanlan_name):
            reflections_full = await self._aload_reflections_full(lanlan_name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                return None
            # Coderabbit PR #934 round-2 Major #2: probe on a staged copy
            # so the in-memory list is NOT mutated until inside the
            # locked record_and_save critical section. If the event
            # append or save raises, the live entry stays clean (no
            # orphan sub_zero_days increment lacking an audit-log row).
            staged_entry = dict(entry)
            if not maybe_mark_sub_zero(staged_entry, now):
                return None

            new_count = int(staged_entry.get('sub_zero_days', 0) or 0)
            new_date = staged_entry.get('sub_zero_last_increment_date')

            payload = {
                'reflection_id': reflection_id,
                'reinforcement': float(entry.get('reinforcement', 0.0) or 0.0),
                'disputation': float(entry.get('disputation', 0.0) or 0.0),
                'rein_last_signal_at': entry.get('rein_last_signal_at'),
                'disp_last_signal_at': entry.get('disp_last_signal_at'),
                'sub_zero_days': new_count,
                'sub_zero_last_increment_date': new_date,
                'user_fact_reinforce_count': int(
                    entry.get('user_fact_reinforce_count', 0) or 0,
                ),
                'source': 'archive_sweep',
            }

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(_view):
                # Apply staged values to the cached entry only after
                # event append succeeded (record_and_save orders
                # append → mutate → save, so a failure earlier never
                # reaches this callback).
                entry['sub_zero_days'] = new_count
                entry['sub_zero_last_increment_date'] = new_date

            def _sync_save(n: str, view):
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view, indent=2, ensure_ascii=False,
                )

            await self._event_log.arecord_and_save(
                lanlan_name, EVT_REFLECTION_EVIDENCE_UPDATED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            return new_count

    async def aarchive_reflection(self, lanlan_name: str, reflection_id: str) -> bool:
        """Move one reflection from main view to a sharded archive file.

        RFC §3.5.6: archive 复用 ``EVT_REFLECTION_STATE_CHANGED`` 事件
        (no new event types). Payload carries `from`/`to`/`archive_shard_path`
        so the reconciler can replay the full transition.

        Flow (coderabbit PR #934 round-1 + round-2):
          1. Pre-pick the shard path (deterministic basename for the
             event payload).
          2. record_and_save — atomically removes the entry from the
             active view + appends the state_changed event whose
             payload carries `entry_snapshot` so a crash later is
             recoverable.
          3. aappend_to_shard — writes the snapshot into the shard
             file. If this raises, the next reconciler boot's archive
             handler self-heals by recreating the shard from
             `entry_snapshot` (idempotent, see
             `make_reflection_archive_handler`).

        Returns True if archived; False if `reflection_id` not found or
        the entry is `protected`.
        """
        from memory.archive_shards import aappend_to_shard
        from memory.event_log import EVT_REFLECTION_STATE_CHANGED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection.aarchive_reflection] event_log 未注入；"
                "ReflectionEngine() 构造时须传入 event_log"
            )

        async with self._get_alock(lanlan_name):
            reflections_full = await self._aload_reflections_full(lanlan_name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                logger.warning(
                    f"[Reflection] {lanlan_name}: aarchive_reflection 找不到 "
                    f"reflection_id={reflection_id}"
                )
                return False
            if entry.get('protected'):
                logger.debug(
                    f"[Reflection] {lanlan_name}: aarchive_reflection 跳过 protected "
                    f"reflection_id={reflection_id}"
                )
                return False

            prev_status = entry.get('status', 'pending')
            now = datetime.now()
            now_iso = now.isoformat()

            # Predict shard path BEFORE writing so we can stamp the
            # entry's own `archive_shard_path` field to match its on-disk
            # location. We deep-copy the entry so the in-memory original
            # remains untouched until the event log gates the mutation;
            # otherwise a crash between shard-write and event-append
            # would leave `archived_at` on a still-active entry.
            from memory.archive_shards import apick_today_shard_path
            archive_dir = self._reflections_archive_dir(lanlan_name)
            shard_path = await apick_today_shard_path(archive_dir, now=now)
            shard_basename = os.path.basename(shard_path)
            archive_entry = dict(entry)
            archive_entry['archived_at'] = now_iso
            archive_entry['status'] = 'archived'
            archive_entry['archive_shard_path'] = shard_basename

            payload = {
                'reflection_id': reflection_id,
                'from': prev_status,
                'to': 'archived',
                'archive_shard_path': shard_basename,
                'archived_at': now_iso,
                # Symmetry with aarchive_persona_entry — the reflection
                # archive handler in evidence_handlers.py reads this on
                # every replay and idempotently recreates the shard if
                # it's missing (coderabbit PR #934 round-2 Major #3).
                # Crash between record_and_save and the shard append
                # below is healed on the next reconciler boot.
                'entry_snapshot': archive_entry,
            }

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(view):
                # Drop the archived entry from active list (in-place
                # mutate, since `view` IS `reflections_full`).
                view[:] = [r for r in view if r.get('id') != reflection_id]

            def _sync_save(n: str, view):
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view, indent=2, ensure_ascii=False,
                )

            # ORDER (coderabbit review #934 round-1 + round-2):
            # 1. record_and_save first (commits event + active-view
            #    removal atomically). Avoids the "shard duplicate +
            #    still-active entry" race where a crash between shard
            #    write and view save would let the next sweep
            #    re-archive into a second shard slot.
            # 2. aappend_to_shard second. If THIS step crashes (or
            #    raises), the active view has already lost the entry
            #    but the shard never got it. Self-heal: the
            #    state_changed handler in evidence_handlers.py reads
            #    `entry_snapshot` from the payload and re-creates the
            #    shard on the next reconciler boot — the event log is
            #    the source of truth (RFC §3.11) and the snapshot
            #    keeps the data recoverable.
            await self._event_log.arecord_and_save(
                lanlan_name, EVT_REFLECTION_STATE_CHANGED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            await aappend_to_shard(archive_dir, [archive_entry], now=now)
            logger.info(
                f"[Reflection] {lanlan_name}: 归档 reflection {reflection_id} "
                f"(score-driven, prev_status={prev_status}) → {shard_basename}"
            )
            return True

    async def aone_shot_archive_migration(self, lanlan_name: str) -> bool:
        """Migrate legacy flat ``reflections_archive.json`` → sharded dir.

        RFC §3.5.5: idempotent one-shot, runs at startup. Returns True if
        a migration actually happened, False if it was a no-op (file
        absent or sentinel already present).

        Failure logs but does NOT raise — boot must not stall on archive
        migration. Worst case: the flat file stays in place and the next
        boot re-tries.
        """
        from memory.archive_shards import amigrate_flat_archive_to_shards
        flat_path = self._reflections_legacy_archive_path(lanlan_name)
        archive_dir = self._reflections_archive_dir(lanlan_name)
        try:
            migrated, n_entries, n_shards = await amigrate_flat_archive_to_shards(
                flat_path, archive_dir,
            )
        except Exception as e:
            logger.warning(
                f"[Reflection] {lanlan_name}: 旧 reflections_archive 迁移失败 "
                f"(保留原文件 fallback): {e}"
            )
            return False
        if migrated:
            logger.info(
                f"[Reflection] {lanlan_name}: 旧 reflections_archive 迁移完成 "
                f"({n_entries} 条 → {n_shards} 分片)"
            )
        return migrated

    async def _aload_reflections_full(self, name: str) -> list[dict]:
        """Like aload_reflections(include_archived=True) but also keeps
        `merged` entries. Needed for aapply_signal + score-driven promote
        paths — we need to reach any non-active reflection by id as well."""
        path = self._reflections_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Reflection] 加载失败: {e}")
            return []
        if not isinstance(data, list):
            return []
        return [
            self._normalize_reflection(item)
            for item in data if isinstance(item, dict) and 'id' in item
        ]

    # ── mention suppress (confirmed only, mirrors persona §2.6) ──────

    @staticmethod
    def _in_window(ts_str: str, cutoff: datetime) -> bool:
        try:
            return datetime.fromisoformat(ts_str) >= cutoff
        except (ValueError, TypeError):
            return False

    @classmethod
    def _apply_record_reflection_mentions(
        cls, reflections: list[dict], response_text: str,
    ) -> bool:
        """AI response 提到任一 **confirmed** reflection 的文本 → recent_mentions 累加。
        Pending reflection 本意就是"AI 主动试探"，抑制会反向破坏机制——
        所以只扫 confirmed。语义和 persona._apply_record_mentions 对齐。
        """
        now = datetime.now()
        now_str = now.isoformat()
        cutoff = now - timedelta(hours=SUPPRESS_WINDOW_HOURS)
        changed = False
        for r in reflections:
            if not isinstance(r, dict):
                continue
            if r.get('status') != 'confirmed':
                continue
            if not _is_mentioned(r.get('text', ''), response_text):
                continue
            mentions = r.get('recent_mentions', [])
            mentions.append(now_str)
            mentions = [t for t in mentions if cls._in_window(t, cutoff)]
            r['recent_mentions'] = mentions
            if not r.get('suppress') and len(mentions) > SUPPRESS_MENTION_LIMIT:
                r['suppress'] = True
                r['suppressed_at'] = now_str
            changed = True
        return changed

    @classmethod
    def _apply_update_reflection_suppressions(
        cls, reflections: list[dict],
    ) -> bool:
        now = datetime.now()
        cutoff = now - timedelta(hours=SUPPRESS_WINDOW_HOURS)
        changed = False
        for r in reflections:
            if not isinstance(r, dict):
                continue
            mentions = r.get('recent_mentions', [])
            cleaned = [t for t in mentions if cls._in_window(t, cutoff)]
            if len(cleaned) != len(mentions):
                r['recent_mentions'] = cleaned
                changed = True
            if r.get('suppress'):
                suppressed_str = r.get('suppressed_at')
                if suppressed_str:
                    try:
                        hours_since = (
                            now - datetime.fromisoformat(suppressed_str)
                        ).total_seconds() / 3600
                        if hours_since >= SUPPRESS_COOLDOWN_HOURS:
                            r['suppress'] = False
                            r['suppressed_at'] = None
                            r['recent_mentions'] = []
                            changed = True
                    except (ValueError, TypeError) as e:
                        # 坏时戳（手编 / 迁移瑕疵）：suppress 冷却本轮跳过、
                        # 下轮再评估；not raising keeps loop non-fatal.
                        logger.debug(
                            f"[Reflection] suppressed_at 解析失败 ({suppressed_str!r}): {e}"
                        )
        return changed

    async def arecord_mentions(self, lanlan_name: str, response_text: str) -> None:
        """AI 发完一轮回复后扫 confirmed reflection，按 5h 窗口累加 mention。
        连续提超过 SUPPRESS_MENTION_LIMIT (=2) 次 → 打上 suppress=True。
        """
        if not response_text:
            return
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            if self._apply_record_reflection_mentions(reflections, response_text):
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(lanlan_name, active)

    async def aupdate_suppressions(self, lanlan_name: str) -> None:
        """Render 前刷新 suppress 状态：冷却期过 → 解除；清理窗口外的 recent_mentions。"""
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            if self._apply_update_reflection_suppressions(reflections):
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(lanlan_name, active)

    # ── feedback lifecycle ───────────────────────────────────────────

    def get_pending_reflections(self, lanlan_name: str) -> list[dict]:
        """Get all pending (unconfirmed) reflections."""
        reflections = self.load_reflections(lanlan_name)
        return [r for r in reflections if r.get('status') == 'pending']

    async def aget_pending_reflections(self, lanlan_name: str) -> list[dict]:
        reflections = await self.aload_reflections(lanlan_name)
        return [r for r in reflections if r.get('status') == 'pending']

    @staticmethod
    def _filter_active_confirmed(
        reflections: list[dict], now: datetime | None = None,
    ) -> list[dict]:
        """Active confirmed = status='confirmed' AND score > 0 AND not suppressed.

        score <= 0：用户否认多次或刚好抵消，既不应进 render "比较确定的印象"
        段（语义漂移），也会随背景循环 tick 归档计数器（§3.5）。
        suppress=True：AI 刚在 5h 窗口内提过太多次这条，由 persona 的同款
        机制静默（§2.6 正交）。
        """
        if now is None:
            now = datetime.now()
        out = []
        for r in reflections:
            if r.get('status') != 'confirmed':
                continue
            if r.get('suppress'):
                continue
            if evidence_score(r, now) <= 0:
                continue
            out.append(r)
        return out

    def get_confirmed_reflections(self, lanlan_name: str) -> list[dict]:
        """Get all confirmed (soft persona) reflections that are still
        active — status='confirmed' AND score > 0 AND not mention-suppressed."""
        return self._filter_active_confirmed(self.load_reflections(lanlan_name))

    async def aget_confirmed_reflections(self, lanlan_name: str) -> list[dict]:
        return self._filter_active_confirmed(
            await self.aload_reflections(lanlan_name),
        )

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
        """
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
            if evidence_score(r, now) < 0:
                continue
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
        """P2.a.2: per-character asyncio.Lock serializes reflections.json /
        surfaced.json 写入，避免与 synth / promote 竞写。"""
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

        P2.a.2: 本方法会写回 surfaced.json（line 572），因此必须在角色锁下
        与 arecord_surfaced / aconfirm_promotion / areject_promotion 串行。
        """
        async with self._get_alock(lanlan_name):
            return await self._check_feedback_locked(lanlan_name, user_messages)

    async def _check_feedback_locked(self, lanlan_name: str, user_messages: list[str]) -> list[dict] | None:
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
        async with self._get_alock(lanlan_name):
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
        async with self._get_alock(lanlan_name):
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
        """Score-driven pending → confirmed (RFC §3.9.1 / §4.1 PR-1 scope).

        Deprecated sync version — retained for backward-compat callers
        (tests / CLI scripts). Production path is the async twin below.

        - 删除时间跳级分支（`AUTO_CONFIRM_DAYS` / `AUTO_PROMOTE_DAYS` 已删）
        - 仅做 pending → confirmed：`evidence_score(r, now) >= EVIDENCE_CONFIRMED_THRESHOLD`
        - confirmed → promoted 由 PR-3 的 `_apromote_with_merge` 接管；
          本函数在 PR-1 不承担 promotion 职责
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
        """P2.a.2: 角色级 asyncio.Lock 串行化。score-driven pending →
        confirmed（§3.9.1） + confirmed → promoted via merge-on-promote
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

    # ── Merge-on-promote (RFC §3.9) ───────────────────────────────────

    @staticmethod
    def _compute_merged_evidence(
        target: dict, reflection: dict,
    ) -> tuple[float, float]:
        """Conservative max-rule for merging two evidence pairs (RFC §3.9.5).

        Why max not sum: sum would inflate evidence whenever the reflection
        and the target persona entry were independently reinforced from the
        same underlying user signals (Stage-2 + check_feedback dual-pickup
        is the canonical case). max represents "the strongest user assertion
        across either witness" — never invents evidence the user never gave.
        """
        merged_rein = max(
            float(target.get('reinforcement', 0.0) or 0.0),
            float(reflection.get('reinforcement', 0.0) or 0.0),
        )
        merged_disp = max(
            float(target.get('disputation', 0.0) or 0.0),
            float(reflection.get('disputation', 0.0) or 0.0),
        )
        return merged_rein, merged_disp

    @staticmethod
    def _within_backoff(reflection: dict, now: datetime | None = None) -> bool:
        """Throttle gate (RFC §3.9.2): True if last attempt was within
        EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES of `now`."""
        last = reflection.get('last_promote_attempt_at')
        if not last:
            return False
        if now is None:
            now = datetime.now()
        try:
            last_ts = datetime.fromisoformat(last)
        except (ValueError, TypeError):
            return False
        elapsed_min = (now - last_ts).total_seconds() / 60.0
        return elapsed_min < EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES

    @staticmethod
    def _exceeds_max_retries(reflection: dict) -> bool:
        return (
            int(reflection.get('promote_attempt_count', 0) or 0)
            >= EVIDENCE_PROMOTE_MAX_RETRIES
        )

    async def _arecord_promote_attempt(
        self, name: str, reflection_id: str, now_iso: str,
    ) -> None:
        """Public wrapper: acquires the per-character lock then delegates
        to `_arecord_promote_attempt_locked`. See that method for details.
        """
        async with self._get_alock(name):
            await self._arecord_promote_attempt_locked(
                name, reflection_id, now_iso,
            )

    async def _arecord_promote_attempt_locked(
        self, name: str, reflection_id: str, now_iso: str,
    ) -> None:
        """Increment `promote_attempt_count` and stamp
        `last_promote_attempt_at` via EVT_REFLECTION_EVIDENCE_UPDATED.

        Caller MUST hold `self._get_alock(name)`. This locked variant
        exists so `_apromote_with_merge` can fuse throttle-check +
        attempt-record into a single critical section (CodeRabbit PR
        #936 round-5 Major #3) — otherwise two concurrent invocations
        could both pass the pre-lock backoff check and both record
        attempts, defeating the throttle.

        Why an evidence event for a counter (not a state change): the
        throttle counters live alongside evidence on each reflection and
        must be event-sourced so reconciler replay restores them after
        crash. Reusing the existing evidence handler avoids inventing a
        third event type — the handler whitelists keys it copies, and
        `promote_attempt_count` / `last_promote_attempt_at` are now on the
        whitelist (see evidence_handlers._EVIDENCE_SNAPSHOT_KEYS).
        """
        from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection._arecord_promote_attempt] event_log 未注入"
            )

        reflections_full = await self._aload_reflections_full(name)
        entry = self._find_reflection_in_list(reflections_full, reflection_id)
        if entry is None:
            logger.warning(
                f"[Reflection] {name}: _arecord_promote_attempt 找不到 "
                f"reflection_id={reflection_id}"
            )
            return

        new_count = int(entry.get('promote_attempt_count', 0) or 0) + 1
        payload = {
            'reflection_id': reflection_id,
            # Full-snapshot evidence values (unchanged by this event,
            # but present so the handler+replay see the consistent view).
            'reinforcement': float(entry.get('reinforcement', 0.0) or 0.0),
            'disputation': float(entry.get('disputation', 0.0) or 0.0),
            'rein_last_signal_at': entry.get('rein_last_signal_at'),
            'disp_last_signal_at': entry.get('disp_last_signal_at'),
            'sub_zero_days': int(entry.get('sub_zero_days', 0) or 0),
            'user_fact_reinforce_count':
                int(entry.get('user_fact_reinforce_count', 0) or 0),
            # New throttle fields — whitelisted in the handler.
            'last_promote_attempt_at': now_iso,
            'promote_attempt_count': new_count,
            'source': 'promote_attempt',
        }

        def _sync_load(_n: str):
            return reflections_full

        def _sync_mutate(_view):
            entry['last_promote_attempt_at'] = now_iso
            entry['promote_attempt_count'] = new_count

        def _sync_save(n: str, view):
            assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{n}/reflections.json",
            )
            atomic_write_json(
                self._reflections_path(n), view,
                indent=2, ensure_ascii=False,
            )

        await self._event_log.arecord_and_save(
            name, EVT_REFLECTION_EVIDENCE_UPDATED, payload,
            sync_load_view=_sync_load,
            sync_mutate_view=_sync_mutate,
            sync_save_view=_sync_save,
        )

    async def _arecord_state_change(
        self, name: str, reflection_id: str, from_status: str, to_status: str,
        *, absorbed_into: str | None = None, reason: str | None = None,
        reject_explanation: str | None = None,
    ) -> None:
        """Mutate a reflection's `status` and emit
        EVT_REFLECTION_STATE_CHANGED for audit + reconciler replay.

        Replay path: `make_reflection_state_changed_composite` in
        `memory/evidence_handlers.py` dispatches on the `to` field —
        `'archived'` routes to the archive handler (PR-2, RFC §3.5),
        any other status routes to `make_reflection_state_changed_handler`
        which re-applies `status`, `<status>_at` timestamp,
        `absorbed_into`, `promote_blocked_reason` (when to='promote_blocked'),
        `denied_reason` (when to='denied'), and `reject_reason` onto the
        reflection entry keyed by `reflection_id`. The persisted view
        updated in this same record_and_save call is therefore
        redundant with the replay path — both paths are kept (view for
        live reads, event for crash recovery). See RFC §3.9.6.
        """
        from memory.event_log import EVT_REFLECTION_STATE_CHANGED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection._arecord_state_change] event_log 未注入"
            )

        async with self._get_alock(name):
            reflections_full = await self._aload_reflections_full(name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                logger.warning(
                    f"[Reflection] {name}: _arecord_state_change 找不到 "
                    f"reflection_id={reflection_id}"
                )
                return

            # Compare-and-swap on `from_status`. _apromote_with_merge runs
            # the LLM call OUTSIDE the per-character lock (LLM is slow); a
            # concurrent rebuttal / archive / parallel promote can flip the
            # status during that window. Without this guard the late writer
            # silently overwrites a newer terminal status (e.g. promote
            # clobbers a freshly-set `denied` from rebuttal), losing the
            # newer signal AND emitting a misleading state-change event
            # whose `from` no longer matches the on-disk view.
            current_status = entry.get('status')
            if current_status != from_status:
                logger.warning(
                    f"[Reflection] {name}/{reflection_id}: "
                    f"_arecord_state_change CAS miss — expected from={from_status!r} "
                    f"but current status is {current_status!r}; dropping "
                    f"transition to {to_status!r} (newer signal wins)"
                )
                return

            now_iso = datetime.now().isoformat()
            payload: dict = {
                'reflection_id': reflection_id,
                'from': from_status,
                'to': to_status,
                'ts': now_iso,
            }
            if absorbed_into is not None:
                payload['absorbed_into'] = absorbed_into
            if reason is not None:
                payload['reason'] = reason
            if reject_explanation is not None:
                payload['reject_explanation'] = reject_explanation

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(_view):
                entry['status'] = to_status
                entry[f'{to_status}_at'] = now_iso
                if absorbed_into is not None:
                    entry['absorbed_into'] = absorbed_into
                if reason is not None:
                    # Route the `reason` audit string into a status-specific
                    # field so the semantics of each terminal-state field
                    # stay clean (RFC §3.9.2 / §3.9.7):
                    #   - promote_blocked → `promote_blocked_reason` (the
                    #     throttle/dead-letter cause; consumed by the dead-
                    #     letter retry path).
                    #   - denied → `denied_reason` (the rejection category;
                    #     audit only, no recovery path).
                    #   - merged → `absorbed_into` already captures
                    #     provenance; no separate reason needed and the
                    #     callers don't pass one.
                    # Without this gate any non-None reason on a denied/
                    # merged transition would pollute promote_blocked_reason,
                    # making dead-letter scans see false-positive blocks.
                    # Mirror of the reconciler handler in
                    # `make_reflection_state_changed_handler`, which already
                    # gates the same way on replay.
                    if to_status == 'promote_blocked':
                        entry['promote_blocked_reason'] = reason
                    elif to_status == 'denied':
                        entry['denied_reason'] = reason
                if reject_explanation is not None:
                    # Keep audit trail off the throttle field — separate key
                    # so promote_blocked vs llm_merge_rejected stays distinct.
                    entry['reject_reason'] = reject_explanation

            def _sync_save(n: str, view):
                # Save via _prepare_save_reflections so terminal entries
                # (merged / promote_blocked) round-trip correctly.
                # asave_reflections handles the merge with on-disk state.
                # We're already inside the per-character lock, so call the
                # raw save path (`asave_reflections` re-acquires no lock).
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view,
                    indent=2, ensure_ascii=False,
                )

            await self._event_log.arecord_and_save(
                name, EVT_REFLECTION_STATE_CHANGED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )

    async def _amark_promote_blocked(
        self, name: str, reflection: dict, reason: str,
    ) -> None:
        """Move reflection to promote_blocked dead-letter (RFC §3.9.2).

        Distinct from `_arecord_state_change` to keep the throttle-vs-merge
        decision points readable in `_apromote_with_merge`.
        """
        await self._arecord_state_change(
            name, reflection['id'], 'confirmed', 'promote_blocked',
            reason=reason,
        )

    async def _allm_call_promotion_merge(
        self, R: dict, persona_pool: list[tuple[str, dict]],
        reflection_pool: list[dict], lanlan_name: str, master_name: str,
    ) -> dict:
        """LLM call producing the merge decision JSON (RFC §3.9.7 prompt).

        Returns the parsed dict on success. Raises on any LLM / parse
        failure — caller (`_apromote_with_merge`) catches and treats as
        skip_retry_pending per §3.9.4.
        """
        from config.prompts_memory import get_promotion_merge_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm

        now = datetime.now()
        # Build the impression pool block with stable ordering — protected
        # persona entries first, then non-protected by score DESC, then
        # confirmed/promoted reflections by score DESC.
        pool_lines: list[str] = []
        for ek, e in persona_pool:
            pool_lines.append(
                f"[persona.{ek}.{e.get('id')}] \"{e.get('text', '')}\""
                f" (evidence_score={evidence_score(e, now):.2f})"
            )
        for r in reflection_pool:
            pool_lines.append(
                f"[reflection.{r.get('id')}] \"{r.get('text', '')}\""
                f" (evidence_score={evidence_score(r, now):.2f})"
            )
        pool_text = "\n".join(pool_lines) if pool_lines else "(印象池为空)"

        prompt = get_promotion_merge_prompt(get_global_language()).format(
            AI_NAME=lanlan_name,
            MASTER_NAME=master_name,
            R_TEXT=R.get('text', ''),
            R_SCORE=f"{evidence_score(R, now):.2f}",
            IMPRESSION_POOL=pool_text,
        )

        set_call_type("memory_promote_merge")
        api_config = self._config_manager.get_model_api_config(
            EVIDENCE_PROMOTION_MERGE_MODEL_TIER,
        )
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
        decision = robust_json_loads(raw)
        if not isinstance(decision, dict):
            raise ValueError(
                f"LLM merge decision is not a dict: {type(decision).__name__}"
            )
        return decision

    async def _apromote_with_merge(
        self, lanlan_name: str, R: dict,
    ) -> str:
        """Score-driven confirmed → promoted via LLM merge decision
        (RFC §3.9.3).

        Returns one of:
          'promote_fresh' | 'merge_into' | 'reject' | 'reject_by_persona'
          | 'queued_correction' | 'skip_retry_pending' | 'blocked'
          | 'invalid_target' | 'noop' | 'no_longer_eligible'

        Throttling: backoff window before retry, max retries → dead-letter.
        LLM failures → skip_retry_pending (NOT promote_fresh — RFC §3.9.4
        explicitly forbids silent fresh-fallback because that breaks
        dedup semantics during outages).
        """
        # Pre-lock advisory throttle check — cheap early-exit when the
        # reflection is obviously inside its backoff window. The
        # authoritative throttle + attempt-record fuse happens under
        # the lock below (CodeRabbit PR #936 round-5 Major #3); this
        # pre-check just saves a lock acquisition in the common case.
        now = datetime.now()
        if self._within_backoff(R, now):
            return 'skip_retry_pending'
        if self._exceeds_max_retries(R):
            await self._amark_promote_blocked(
                lanlan_name, R, 'llm_unavailable',
            )
            return 'blocked'

        # Revalidate snapshot + throttle gate + record attempt — ALL
        # inside the same lock section. CodeRabbit PR #936 round-5
        # Major #3: without this fusion, two concurrent invocations
        # can both pass the pre-lock backoff check, both take the lock
        # serially, and both record attempts — defeating the throttle
        # and double-bumping promote_attempt_count toward the
        # max-retries dead-letter.
        #
        # The caller (`_aauto_promote_score_driven`) iterates a stale
        # snapshot collected outside the per-character lock; by the
        # time we reach this point another coroutine may have demoted,
        # denied, merged or otherwise disqualified `R`. Bumping
        # `promote_attempt_count` for an already-merged reflection
        # both wastes a retry slot and emits a misleading evidence
        # event. Re-read the on-disk view, find the same id, and
        # short-circuit if it's no longer eligible.
        async with self._get_alock(lanlan_name):
            current_list = await self._aload_reflections_full(lanlan_name)
            current = self._find_reflection_in_list(current_list, R['id'])
            if current is None or current.get('status') != 'confirmed':
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: no longer eligible "
                    f"(current status={current.get('status') if current else 'gone'!r}); "
                    f"skip"
                )
                return 'no_longer_eligible'
            if evidence_score(current, now) < EVIDENCE_PROMOTED_THRESHOLD:
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: evidence_score "
                    f"dropped below threshold under lock; skip"
                )
                return 'no_longer_eligible'
            # Re-check throttle against the freshly-read entry — this
            # closes the race where another coroutine recorded an
            # attempt between our pre-lock check and our lock
            # acquisition.
            if self._within_backoff(current, now):
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: another attempt "
                    f"recorded during lock wait; skip_retry_pending"
                )
                return 'skip_retry_pending'
            if self._exceeds_max_retries(current):
                # Must release lock before calling _amark_promote_blocked
                # (it re-acquires the same lock via _arecord_state_change).
                R = current
                break_for_blocked = True
            else:
                break_for_blocked = False
                # Use the freshly-read entry from here on so downstream
                # merge math sees the latest evidence values.
                R = current
                # Record the attempt INSIDE the lock so the throttle
                # stamp lands atomically with the eligibility decision.
                # `_arecord_promote_attempt_locked` expects the caller
                # to hold the lock — we do.
                now_iso = datetime.now().isoformat()
                await self._arecord_promote_attempt_locked(
                    lanlan_name, R['id'], now_iso,
                )

        if break_for_blocked:
            await self._amark_promote_blocked(
                lanlan_name, R, 'llm_unavailable',
            )
            return 'blocked'

        # Build candidate pool (RFC §3.9.3 step 1) — outside the lock
        # because same_entity_persona / same_entity_reflections are
        # advisory inputs to the LLM; stale reads cost at most a
        # suboptimal merge decision, not correctness.
        persona_view = await self._persona_manager.aget_persona(lanlan_name)
        target_entity = R.get('entity')
        same_entity_persona: list[tuple[str, dict]] = []
        if target_entity and isinstance(persona_view, dict):
            section = persona_view.get(target_entity)
            if isinstance(section, dict):
                for e in section.get('facts', []):
                    if isinstance(e, dict) and not e.get('protected'):
                        same_entity_persona.append((target_entity, e))
        all_reflections = await self._aload_reflections_full(lanlan_name)
        same_entity_reflections = [
            r for r in all_reflections
            if r.get('entity') == target_entity
            and r.get('status') in ('confirmed', 'promoted')
            and r.get('id') != R.get('id')
        ]

        try:
            _, _, _, _, name_mapping, _, _, _, _ = (
                await self._config_manager.aget_character_data()
            )
            master_name = name_mapping.get('human', '主人')
            decision = await self._allm_call_promotion_merge(
                R, same_entity_persona, same_entity_reflections,
                lanlan_name, master_name,
            )
        except Exception as e:  # noqa: BLE001
            # RFC §3.9.4: LLM failure does NOT downgrade to promote_fresh.
            # Reflection stays confirmed; throttle state was already bumped
            # above. Next cycle (after backoff) will retry; max-retries
            # tips the reflection into promote_blocked dead-letter.
            logger.warning(
                f"[Promote] {lanlan_name}/{R['id']}: LLM merge call failed: "
                f"{e}; reflection stays confirmed for retry"
            )
            return 'skip_retry_pending'

        action = decision.get('action')

        # Post-LLM revalidation (round-2 review). The pre-LLM check above
        # only fences the snapshot up to the LLM await; the LLM call itself
        # is multi-second. During that window another coroutine (rebuttal,
        # parallel promote of a duplicate signal, archive sweep) can flip
        # status to denied / merged / promote_blocked. Without re-checking
        # here, the post-LLM `aadd_fact` / `amerge_into` will write to
        # persona FIRST, and only then will `_arecord_state_change`'s CAS
        # guard refuse the status flip — leaving persona polluted with a
        # fact whose source reflection is no longer eligible. Re-read the
        # status under the lock; bail out cleanly if the gate has closed.
        async with self._get_alock(lanlan_name):
            current_list2 = await self._aload_reflections_full(lanlan_name)
            current2 = self._find_reflection_in_list(current_list2, R['id'])
            if current2 is None or current2.get('status') != 'confirmed':
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: status changed "
                    f"during LLM await (now "
                    f"{current2.get('status') if current2 else 'gone'!r}); "
                    f"discarding LLM decision={action!r} without persona write"
                )
                return 'no_longer_eligible'
            if evidence_score(current2, datetime.now()) < EVIDENCE_PROMOTED_THRESHOLD:
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: evidence_score "
                    f"dropped below threshold during LLM await; "
                    f"discarding LLM decision={action!r}"
                )
                return 'no_longer_eligible'
            # Refresh R from the freshly-read view so any merge-evidence
            # math below sees current values, not the pre-LLM snapshot.
            R = current2

        if action == 'promote_fresh':
            result = await self._persona_manager.aadd_fact(
                lanlan_name, R.get('text', ''),
                entity=target_entity or 'master',
                source='reflection', source_id=R['id'],
            )
            if result == self._persona_manager.FACT_ADDED:
                await self._arecord_state_change(
                    lanlan_name, R['id'], 'confirmed', 'promoted',
                )
                return 'promote_fresh'
            # FACT_REJECTED_CARD: contradicts character_card → reflection
            # is permanently denied (no recovery path; the card is fixed).
            if result == self._persona_manager.FACT_REJECTED_CARD:
                await self._arecord_state_change(
                    lanlan_name, R['id'], 'confirmed', 'denied',
                    reason=f'rejected_by_persona_add:{result}',
                )
                return 'reject_by_persona'
            # FACT_QUEUED_CORRECTION: contradicts an EXISTING non-card fact;
            # PersonaManager has queued an async LLM correction. The user's
            # confirming intent (which got the reflection to confirmed) is
            # NOT denied — the correction queue may resolve in either
            # direction once the LLM weighs in. Keep the reflection in
            # `confirmed` so a future promote cycle can revisit it after
            # backoff (or after the correction queue has resolved); the
            # throttle counter we already bumped this round prevents a
            # tight retry loop.
            logger.info(
                f"[Promote] {lanlan_name}/{R['id']}: aadd_fact returned "
                f"{result} (correction queued); reflection stays confirmed "
                f"for retry after correction resolves"
            )
            return 'queued_correction'

        if action == 'merge_into':
            target_id = decision.get('target_id')
            merged_text = decision.get('merged_text')
            if (not isinstance(target_id, str)
                    or not target_id.startswith('persona.')
                    or not isinstance(merged_text, str)
                    or not merged_text.strip()):
                # LLM returned malformed merge_into — RFC §3.9.7 constrains
                # target_id to persona.* prefix; reject silently as parse fail.
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: invalid merge_into "
                    f"target_id={target_id!r} merged_text empty/non-str; "
                    f"treating as skip_retry_pending"
                )
                return 'invalid_target'
            # `target_id` is fully-qualified (persona.<entity>.<entry_id>);
            # PersonaManager keys entries by entry_id alone so strip the
            # prefix here. Format: persona.<entity_key>.<entry_id_with_dots_ok>
            #   "persona.master.card_master_abc" -> "card_master_abc"
            #   "persona.master.prom_ref_xyz" -> "prom_ref_xyz"
            parts = target_id.split('.', 2)
            if len(parts) < 3:
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: target_id "
                    f"{target_id!r} missing entity/entry segments"
                )
                return 'invalid_target'
            target_entry_id = parts[2]

            # Pre-flight existence check (under PersonaManager's read
            # path). Note: the canonical re-lookup AND the conservative
            # max-rule evidence aggregation now both happen INSIDE
            # `amerge_into` under the per-character lock — see CodeRabbit
            # PR #936 round-6 Major #2. We pass the reflection's own
            # evidence values; `amerge_into` does the max() against the
            # locked target entry's current evidence so a concurrent
            # `aapply_signal` (or another merge) cannot be rolled back
            # by a stale snapshot computed up here.
            persona_view2 = await self._persona_manager.aget_persona(lanlan_name)
            target_entity_key, target_entry = (
                self._persona_manager._find_entry_with_section(
                    persona_view2, target_entry_id,
                )
            )
            if target_entry is None:
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: merge target "
                    f"{target_entry_id} not found in persona; skip"
                )
                return 'invalid_target'

            merge_outcome = await self._persona_manager.amerge_into(
                lanlan_name, target_entry_id, merged_text,
                reflection_evidence={
                    'reinforcement': float(R.get('reinforcement', 0.0) or 0.0),
                    'disputation': float(R.get('disputation', 0.0) or 0.0),
                },
                source_reflection_id=R['id'],
                merged_from_ids=[R['id']],
            )
            if merge_outcome == 'not_found':
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: amerge_into reported "
                    f"not_found mid-flight; treating as invalid_target"
                )
                return 'invalid_target'
            # 'merged' or 'noop' → both leave the persona in the desired
            # post-merge state, so reflection should flip to merged either way.
            await self._arecord_state_change(
                lanlan_name, R['id'], 'confirmed', 'merged',
                absorbed_into=target_entry_id,
            )
            # Surface tracking: treat merge as a confirm-equivalent terminal
            await self._abatch_mark_surfaced_handled(
                lanlan_name, [R['id']], 'confirmed',
            )
            return 'merge_into'

        if action == 'reject':
            await self._arecord_state_change(
                lanlan_name, R['id'], 'confirmed', 'denied',
                reason='llm_merge_rejected',
                reject_explanation=decision.get('reason'),
            )
            return 'reject'

        # Unknown action — treat as parse failure per §3.9.4 (do NOT
        # downgrade to promote_fresh). Throttle counter already bumped.
        logger.warning(
            f"[Promote] {lanlan_name}/{R['id']}: LLM returned unknown action "
            f"{action!r}; skip_retry_pending"
        )
        return 'skip_retry_pending'

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
