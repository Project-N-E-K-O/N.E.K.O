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
import re
import asyncio
import secrets
import threading
from datetime import datetime
from typing import TYPE_CHECKING

from config import (
    EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS,
    EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS,
    EVIDENCE_DETECT_SIGNALS_MODEL_TIER,
    EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
    EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS,
    EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY,
    EXTERNAL_IMPORT_DAILY_MAX_FILES,
    MEMORY_SCHEMA_VERSION_CURRENT,
    SCOPED_BATCH_SEGMENT_NONCE_BYTES,
)
from memory.temporal import (
    compute_event_timestamps,
    normalize_event_when,
)
from config.prompts.prompts_memory import (
    get_fact_extraction_batch_prompt,
    get_fact_extraction_prompt,
    get_signal_detection_prompt,
)
from memory.evidence import evidence_score
from memory.scopes import MemorySubject, coerce_subject, entry_matches_subject
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.language_utils import (
    detect_prompt_language,
    get_global_language_full,
)
from utils.config_manager import get_config_manager
from utils.file_utils import (
    atomic_write_json,
    robust_json_loads,
)
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type

if TYPE_CHECKING:
    from memory.timeindex import TimeIndexedMemory

logger = get_module_logger(__name__, "Memory")


_ARCHIVE_AGE_DAYS = 7          # absorbed 且创建超过此天数的 facts 被归档
_ARCHIVE_COOLDOWN_HOURS = 24   # 两次归档尝试之间的最小间隔

# Sentinel：让 _allm_call_with_retries 区分"调用方没指定 extra_body"（默认走
# create_chat_llm 自动解析）和"调用方显式传 None"（关闭 extra_body 自动解析，
# 保留 thinking）。Phase D：Stage-2 signal detection 显式传 None 开 thinking。
_DEFAULT_EXTRA_BODY = object()


def safe_importance(f: dict, default: int = 5) -> int:
    """Defensively coerce ``f['importance']`` to int.

    Normal entries pass through `_apersist_new_facts` where importance is
    clamped to 1..10, so this only matters for hand-edited facts.json or
    legacy data — but a malformed value here would otherwise raise
    ValueError inside a sort key and stall the entire drain loop for that
    character. Falls back to ``default`` on any failure.
    """
    try:
        val = f.get('importance', default)
        return int(val) if val else default
    except (ValueError, TypeError):
        return default


def safe_int_field(d: dict, key: str, default: int = 0) -> int:
    """Defensively coerce ``d[key]`` to int (Codex P2 on PR #1412).

    Liveness attempt counters (``refine_attempts`` / ``resolve_attempts`` /
    ``_attempt_count``) are all read from dict fields deserialized from JSON /
    ndjson; once a manual edit / legacy data / migration noise writes a dirty
    value like ``""`` / ``"unknown"`` / list / dict, the original
    ``int(d.get(key, 0) or 0)`` raises ValueError / TypeError and takes down the
    whole list comprehension (candidate gather) → that pass fails forever → the
    liveness fallback itself becomes a new liveness gap.

    Difference from ``safe_importance``: this helper treats ``0`` / ``"0"`` as
    legitimate and returns 0 (an attempt counter of 0 is a valid count) instead
    of falling back to the default. ``safe_importance`` mapping all falsy values
    to the default is importance-specific semantics.
    """
    try:
        val = d.get(key)
        if val is None:
            return default
        return int(val)
    except (ValueError, TypeError):
        return default


class FactExtractionFailed(RuntimeError):
    """Stage-1 LLM call exhausted retries (RFC §3.4.2, last paragraph).

    Distinct from "Stage-1 returned an empty list" — the latter is a
    successful zero-result run that should advance the signal-extraction
    cursor, while the former must leave the cursor untouched so the next
    idle cycle retries the same message window.
    """


def _readable_fact_id(entry: dict):
    """The fact's id when it can be used as a lookup key, else ``None``.

    Ids are written as strings (``fact_<timestamp>_<hash>``), but facts.json is
    a plain file users and older versions have edited: a row can arrive with no
    id at all, or with a list/dict where the id should be. Indexing those
    directly raises ``KeyError`` / ``TypeError: unhashable``, neither of which
    the read-merge's ``except (json.JSONDecodeError, OSError)`` catches, so one
    malformed row would abort every future save instead of being overwritten.

    Only genuinely unusable ids are dropped. A legacy scalar id such as
    ``12345`` still matches between cache and disk exactly as it did before, so
    excluding it would silently stop preserving that row's ``absorbed`` /
    ``signal_processed`` flags — trading the crash for quiet data loss.
    """
    fact_id = entry.get('id')
    # 只排除「没有 id」这一类：None / 缺失 / 空串（空串会让所有没有真 id 的行
    # 互相撞上）。不能写成 `not fact_id`——那会把老库里 id 为 0 的行也一并丢掉，
    # 它是个完全可用的键，丢了就是又一次「崩溃换静默丢标记」。
    if fact_id is None or fact_id == '':
        return None
    try:
        hash(fact_id)
    except TypeError:  # list / dict 之类，进不了集合
        return None
    return fact_id


def _merge_archive_entries(existing: list, incoming: list) -> list[dict]:
    """Merge archive rows keyed by id: later occurrence wins, first slot kept.

    facts_archive.json is appended by a two-file commit (archive first, then
    facts.json — see ``_archive_absorbed``). An interrupted commit leaves a row
    in BOTH files, so the next archive pass re-appends it. Keying on
    ``_readable_fact_id`` makes that append idempotent, and merging ``existing``
    against itself also heals duplicates an earlier half-commit already wrote to
    a user's disk.

    Later wins because ``incoming`` is the live copy being archived right now —
    it may carry flag updates the older archived copy predates.

    Rows with an unusable id are all kept: there is no key to compare them on,
    and folding them together would trade a duplicate for silent data loss (the
    same call ``_readable_fact_id`` already makes).
    """
    out: list[dict] = []
    pos: dict = {}
    for entry in list(existing) + list(incoming):
        if not isinstance(entry, dict):
            continue
        fid = _readable_fact_id(entry)
        if fid is None:
            out.append(entry)
            continue
        if fid in pos:
            out[pos[fid]] = entry
        else:
            pos[fid] = len(out)
            out.append(entry)
    return out


# 惰性创建 recheck 镜像时的互斥（见 FactStore._recheck_mem）。放模块级而不是
# 实例级，是因为它要保护的正是「实例上还没有那把锁」的那一瞬间。只护创建的
# 那几行，不参与后续读写。
_RECHECK_MEM_BOOTSTRAP = threading.Lock()


class FactStore:
    """Manages raw fact extraction, deduplication, and persistence."""

    # legacy fact 重判的失败计数「进程内镜像」：{name: {fid: {"n", "at"}}}。
    # session 内它是权威工作副本，facts.json 里那两栏只是持久化 + 重启恢复。
    # 对齐 ReflectionEngine._synth_backoff_mem（memory/reflection/manager.py:79）：
    # 计数器如果只活在「那个写不进去的文件」里，只读 FS / 权限 / 维护态下熔断
    # 永远不会触发。
    #
    # class 级默认 None + 惰性创建（不是只在 __init__ 里赋值）：仓库里有多处
    # `FactStore.__new__(FactStore)` 绕过 __init__ 造实例
    # （tests/unit/test_ai_aware_stage1_path_b.py、tests/unit/test_group_memory_scopes.py），
    # 那些实例一碰镜像就会 AttributeError。
    #
    # 刻意用实例级而不是模块级状态：它是 liveness 兜底不是持久状态，热重载
    # （app/memory_server/runtime.py 重建 FactStore）丢掉只是让卡住的 fact 多跑
    # 几轮，而模块单例会在 pytest 用例之间串状态。
    _recheck_attempts_mem: dict | None = None
    _recheck_mem_guard: threading.Lock | None = None

    def __init__(self, *, time_indexed_memory: TimeIndexedMemory | None = None):
        self._config_manager = get_config_manager()
        self._time_indexed = time_indexed_memory
        self._facts: dict[str, list[dict]] = {}  # {lanlan_name: [fact, ...]}
        self._locks: dict[str, threading.Lock] = {}  # per-character 文件锁
        self._locks_guard = threading.Lock()  # 保护 _locks 字典本身
        self._persist_alocks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, name: str) -> threading.Lock:
        """Get the character-specific file lock (lazily created)"""
        if name not in self._locks:
            with self._locks_guard:
                if name not in self._locks:  # double-check
                    self._locks[name] = threading.Lock()
        return self._locks[name]

    def _get_persist_alock(self, name: str) -> asyncio.Lock:
        """Serialize each character's load/dedup/mutate/save fact pipeline."""
        if name not in self._persist_alocks:
            with self._locks_guard:
                if name not in self._persist_alocks:
                    self._persist_alocks[name] = asyncio.Lock()
        return self._persist_alocks[name]

    # ── persistence ──────────────────────────────────────────────────

    def _facts_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'facts.json')

    # v1→v2 entity key renames
    _ENTITY_RENAMES = {'user': 'master', 'ai': 'neko'}

    def load_facts(self, name: str) -> list[dict]:
        path = self._facts_path(name)
        if name in self._facts:
            return self._facts[name]
        with self._get_lock(name):
            # double-check: 另一个线程可能在等锁期间已经加载了
            if name in self._facts:
                return self._facts[name]
            if os.path.exists(path):
                try:
                    with open(path, encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        if self._migrate_v1_entity_values(data):
                            try:
                                assert_cloudsave_writable(
                                    self._config_manager,
                                    operation="migrate",
                                    target=f"memory/{name}/facts.json",
                                )
                                atomic_write_json(path, data, indent=2, ensure_ascii=False)
                                logger.info(f"[FactStore] {name}: v1→v2 entity 值迁移完成")
                            except MaintenanceModeError as exc:
                                logger.debug(f"[FactStore] {name}: 维护态跳过 facts.json 迁移落盘: {exc}")
                        self._facts[name] = data
                        return data
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"[FactStore] 加载 facts 文件失败: {e}")
            self._facts[name] = []
            return self._facts[name]

    async def aload_facts(self, name: str) -> list[dict]:
        if name in self._facts:
            return self._facts[name]
        return await asyncio.to_thread(self.load_facts, name)

    def load_facts_full(self, name: str) -> list[dict]:
        """Full fact pool: active + archived (Phase C-2).

        Archived = old entries already moved into facts_archive.json by
        `_archive_absorbed` (absorbed more than _ARCHIVE_AGE_DAYS = 7 days ago).

        For scenarios where "distant history must be searchable" — currently the
        RELATED_CONTEXT recall of reflection synthesis. Returns a new list; the
        archive never enters the cache.

        A corrupted archive file degrades best-effort to active-only, no raise
        (incl. invalid UTF-8: daily import's fingerprint scan now reads the
        archive via this loader before any per-day isolation, so a non-UTF-8
        archive must degrade here instead of aborting the whole import — Codex
        P2). ``UnicodeDecodeError`` is a ``ValueError`` subclass, distinct from
        ``JSONDecodeError``, so it is listed explicitly.

        Rows whose id also exists among the active facts are dropped, keeping
        the active copy. That is not an optional extra guard — it is the read
        half of ``_archive_absorbed``'s two-file commit protocol, whose write
        order deliberately prefers "the row is in both files" over "the row is
        in neither". Something has to collapse "in both", and a warning makes
        this a backstop with a detector rather than a cover-up."""
        active = self.load_facts(name)
        archive_path = self._facts_archive_path(name)
        if not os.path.exists(archive_path):
            return list(active)
        try:
            with open(archive_path, encoding='utf-8') as f:
                archived = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            logger.warning(f"[FactStore] {name}: 读取 archive 失败，降级仅 active: {e}")
            return list(active)
        if not isinstance(archived, list):
            return list(active)
        # active 与 archive 的 id 重叠 = 归档两文件提交被打断后还没收敛的残留。
        # 收敛到 active 那份：它至少和归档副本一样新，且 absorbed /
        # signal_processed 之类的 monotonic 标记以它为准。
        merged = list(active)
        active_ids = {
            fid for fid in (
                _readable_fact_id(f) for f in merged if isinstance(f, dict)
            ) if fid is not None
        }
        dropped = 0
        for f in archived:
            if not isinstance(f, dict):
                continue
            fid = _readable_fact_id(f)
            if fid is not None and fid in active_ids:
                dropped += 1
                continue
            merged.append(f)
        if dropped:
            logger.warning(
                f"[FactStore] {name}: active/archive 存在 {dropped} 条 id 重叠，"
                f"已按 active 收敛（多半是上一次归档两文件提交被打断）"
            )
        return merged

    async def aload_facts_full(self, name: str) -> list[dict]:
        return await asyncio.to_thread(self.load_facts_full, name)

    @classmethod
    def _migrate_v1_entity_values(cls, facts: list[dict]) -> bool:
        """Rename v1 entity values ('user'→'master', 'ai'→'neko') in-place."""
        changed = False
        for f in facts:
            old = f.get('entity')
            new = cls._ENTITY_RENAMES.get(old)
            if new:
                f['entity'] = new
                changed = True
        return changed

    def save_facts(self, name: str) -> None:
        with self._get_lock(name):
            try:
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{name}/facts.json",
                )
                facts = self._facts.get(name, [])
                path = self._facts_path(name)
                # 先统一摘纸条（见 _SIGNAL_RESET_PENDING）：摘的动作必须无条件
                # 发生，不能挂在下面 read-merge 的任何一层分支里——文件还不存在
                # 或这一批没有 monotonic 标记时那些分支都不进，纸条就会跟着落盘。
                just_unsealed_ids = {
                    _readable_fact_id(f) for f in facts
                    if isinstance(f, dict)
                    and f.pop(self._SIGNAL_RESET_PENDING, False)
                    and _readable_fact_id(f) is not None
                }
                # Read-merge-write: 保护其他进程/路径写入的 monotonic 标记
                # （只能从 False → True 单向翻的字段：absorbed、signal_processed）。
                # 否则旧 cache 的写路径会用 False 覆盖磁盘上的 True，让同一批
                # facts 被 drain loop 重复送进 Stage-2 / 重复合成 reflection。
                if os.path.exists(path):
                    try:
                        with open(path, encoding='utf-8') as f:
                            disk_facts = json.load(f)
                        if isinstance(disk_facts, list):
                            # 索引键统一走 _readable_fact_id：手改/半损坏的 legacy 行
                            # 可能没有 id（f['id'] 抛 KeyError），也可能 id 是 list/dict
                            # （放进 set 抛 TypeError: unhashable）。内层只接 JSON/OS
                            # 错误，这两种异常都会冒到外层把缓存清掉并重抛，此后每次存盘
                            # 都死在同一行、新 fact 再也落不了盘——正好是 read-merge 想
                            # 兜的「坏数据也要能覆盖写掉」的反面。
                            absorbed_ids = {
                                _readable_fact_id(f) for f in disk_facts
                                if isinstance(f, dict) and f.get('absorbed')
                                and _readable_fact_id(f) is not None
                            }
                            signal_processed_ids = {
                                _readable_fact_id(f) for f in disk_facts
                                if isinstance(f, dict) and f.get('signal_processed')
                                and _readable_fact_id(f) is not None
                            }
                            # ⚠残留窗口：这次读盘与下面的 atomic_write_json 之间没有
                            # 跨进程锁（self._get_lock 只挡同进程的线程），所以理论上
                            # 仍能被「读到 ai_disclosure → 别人升级并消费 → 我方写回
                            # False」插进来。这不是本次豁免引入的新race：整段 read-merge
                            # 从 #976 起就是「读盘-合并-覆盖写」的尽力而为，任何并发写
                            # 者都能在同一窗口里丢掉合并结果。真要关掉它得给 facts.json
                            # 加一把覆盖读+写全程的跨进程文件锁（portalocker，本仓库目前
                            # 只在 galgame store 里用过）并让所有写者都走它——那是独立的
                            # 架构改动，不在本次范围。豁免面已经收到「磁盘上那条仍是
                            # ai_disclosure」这一个条件里，窗口比原来的整段合并更窄。
                            #
                            # 纸条只在磁盘上那条**还没被别人升级过**时算数：另一个
                            # 进程可能已经用它自己的缓存升级完、Stage-2 也消费过了
                            # （磁盘上是 user_observation + True）。此时本进程拿着
                            # 旧缓存再升一次并放行回写，等于把一条已消费的 fact 重新
                            # 排回 Stage-2——正是这段 read-merge 要挡的跨进程回归。
                            # 重复 id 按保守口径：只有当磁盘上带这个 id 的行**全部**
                            # 还是 ai_disclosure 时才算「没人升过」。手改或对账后的
                            # 文件里可能同一个 id 有两行——一行还是 ai_disclosure、
                            # 另一行已升级且 signal_processed=True。两个集合各自都会
                            # 包含它，只判「在 disclosure 集合里」就会放行回写，把已
                            # 消费状态盖回 False。
                            disk_disclosure_ids: set = set()
                            disk_other_source_ids: set = set()
                            for f in disk_facts:
                                if not isinstance(f, dict):
                                    continue
                                disk_id = _readable_fact_id(f)
                                if disk_id is None:
                                    continue
                                if f.get('source', self._SOURCE_DEFAULT) == 'ai_disclosure':
                                    disk_disclosure_ids.add(disk_id)
                                else:
                                    disk_other_source_ids.add(disk_id)
                            disk_ai_disclosure_ids = disk_disclosure_ids - disk_other_source_ids
                            if absorbed_ids or signal_processed_ids:
                                for f in facts:
                                    if f.get('id') in absorbed_ids:
                                        f['absorbed'] = True
                                    unsealed = (
                                        f.get('id') in just_unsealed_ids
                                        and f.get('id') in disk_ai_disclosure_ids
                                    )
                                    if f.get('id') in signal_processed_ids and not unsealed:
                                        f['signal_processed'] = True
                    except (json.JSONDecodeError, OSError):
                        # Read-merge is best-effort: if the on-disk
                        # file is corrupt or unreadable, fall through
                        # and write whatever we have. The atomic
                        # write below will overwrite the bad payload.
                        pass
                atomic_write_json(path, facts, indent=2, ensure_ascii=False)
            except Exception:
                # Cache divergence guard (CodeRabbit PR-956 Major,
                # mirroring `PersonaManager.asave_persona`'s round-7
                # fix from PR #936). Callers like
                # `FactDedupResolver._aapply_decisions` mutate the
                # in-memory list directly via `facts[:] = [...]` and
                # then call us; if the disk write raises, the cache
                # still holds the post-mutation state but disk
                # doesn't, so the next `aload_facts` returns
                # divergent data. Evicting forces a fresh disk read.
                self._facts.pop(name, None)
                raise
            # 基于文件修改时间节流归档：距上次归档超过 _ARCHIVE_COOLDOWN_HOURS 才尝试
            try:
                archive_path = self._facts_archive_path(name)
                if os.path.exists(archive_path):
                    mtime = datetime.fromtimestamp(os.path.getmtime(archive_path))
                    if (datetime.now() - mtime).total_seconds() < _ARCHIVE_COOLDOWN_HOURS * 3600:
                        return
                # 用 marker 文件记录上次归档尝试时间（即使归档文件尚不存在）
                marker_path = archive_path + '.last_attempt'
                if os.path.exists(marker_path):
                    mtime = datetime.fromtimestamp(os.path.getmtime(marker_path))
                    if (datetime.now() - mtime).total_seconds() < _ARCHIVE_COOLDOWN_HOURS * 3600:
                        return
                # 只在归档「真跑过」（跑完或真失败）时才 touch marker。维护态是
                # 预期跳过、不该消耗归档窗口：否则维护期间每次 save 都把冷却续
                # 24h，维护结束后要再等一整天才归档。
                archive_ran = True
                try:
                    self._archive_absorbed(name)
                except MaintenanceModeError as exc:
                    # 对齐 load_facts 里的迁移分支：维护态跳过走 debug。
                    #
                    # 窄但不是死代码：save_facts 顶部的 assert_cloudsave_writable
                    # 已经判过一次维护态，稳态维护期根本进不到这里。能走到的只有
                    # 「顶部那次判定放行之后、_archive_absorbed 里那次判定之前
                    # 进入维护态」这一窄窗——两次判定之间没有跨进程锁，root_state
                    # 由别的进程/线程改。所以别按"不可达"把 archive_ran 一并删掉：
                    # 真踩上时它决定了不消耗 24h 归档窗口。
                    archive_ran = False
                    logger.debug(f"[FactStore] {name}: 维护态跳过归档: {exc}")
                except Exception:
                    # 归档失败不能让调用方的 save 失败（facts.json 本身已经落盘
                    # 成功了），但必须留痕：原来是裸 pass，两文件半提交在生产里
                    # 完全不可见。marker 照常 touch —— archive 那次写就失败时
                    # archive 的 mtime 不变、冷却不生效，不 touch 就会每次 save
                    # 都重跑一遍必然失败的归档。
                    logger.warning(
                        f"[FactStore] {name}: 归档失败，facts.json 已落盘，"
                        f"{_ARCHIVE_COOLDOWN_HOURS}h 后重试",
                        exc_info=True,
                    )
                if archive_ran:
                    # 更新 marker（无论归档是否有实际条目都 touch 一次）
                    with open(marker_path, 'w', encoding='utf-8') as f:
                        f.write(datetime.now().isoformat())
            except Exception:
                # 冷却判定 / marker 落盘本身出错：不影响已成功的 facts.json 保存，
                # 但不再整个吞掉。
                logger.debug(
                    f"[FactStore] {name}: 归档冷却判定失败", exc_info=True,
                )

    async def asave_facts(self, name: str) -> None:
        await asyncio.to_thread(self.save_facts, name)

    def _facts_archive_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'facts_archive.json')

    def _archive_absorbed(self, name: str) -> int:
        """Move facts that are absorbed and older than _ARCHIVE_AGE_DAYS into the archive file."""
        from datetime import timedelta
        assert_cloudsave_writable(
            self._config_manager,
            operation="archive",
            target=f"memory/{name}/facts.json",
        )
        facts = self._facts.get(name, [])
        cutoff = datetime.now() - timedelta(days=_ARCHIVE_AGE_DAYS)
        active, to_archive = [], []
        for f in facts:
            try:
                created = datetime.fromisoformat(f.get('created_at', ''))
            except (ValueError, TypeError):
                active.append(f)
                continue
            if f.get('absorbed') and created < cutoff:
                to_archive.append(f)
            else:
                active.append(f)
        if not to_archive:
            return 0
        # 追加到归档文件
        archive_path = self._facts_archive_path(name)
        existing_archive: list[dict] = []
        if os.path.exists(archive_path):
            try:
                with open(archive_path, encoding='utf-8') as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    existing_archive = data
            except (json.JSONDecodeError, OSError) as e:
                # 归档文件损坏 → 放弃本次归档，避免覆盖丢数据
                logger.warning(f"[FactStore] {name}: 读取归档文件失败，跳过本次归档: {e}")
                return 0
        # 追加按 id 幂等合并：半提交（archive 写成功、facts.json 没写成）之后
        # 下一轮归档会把同一批 fact 再送进来一次，extend 会让它在归档里永久存在
        # 两份。顺带修好已经落在用户盘上的历史重复。
        existing_archive = _merge_archive_entries(existing_archive, to_archive)
        atomic_write_json(archive_path, existing_archive, indent=2, ensure_ascii=False)
        # 两文件提交顺序固定为「先 archive 再 facts.json」：中断窗口的状态是
        # 「两边都有」（重复 —— 读侧 load_facts_full 按 id 收敛、下轮归档按 id
        # 幂等追加），而不是「两边都没有」（永久丢已归档 fact，连带丢 daily
        # import 的指纹 → 整天重导）。顺序不要反过来。
        atomic_write_json(self._facts_path(name), active, indent=2, ensure_ascii=False)
        # 缓存只在 facts.json 落盘成功之后才动 —— 对齐 event_log.record_and_save
        # 已经写明的纪律（cache only changes after the disk write）。原来是先改
        # 缓存再写盘，写盘失败就留下「缓存少 k 条 / 磁盘多 k 条」的半提交，而
        # save_facts 的 evict 只挂在前一个 try 上、这里不触发。
        #
        # 原地更新活跃列表（保持对象引用不变，避免外部持有旧引用导致修改丢失）。
        # 按身份剔除已归档的那几条，而不是拿函数开头算的 active 快照整体替换：
        # add_facts 在 aload_facts 之后直接 append，并不持 _get_lock（本仓库既有
        # 约定），快照整体替换会把「快照之后、这里之前」append 进来的 fact 一起
        # 抹掉，而写序重排把这个窗口从一次写盘拉长成了两次。
        # 用 id() 作键是安全的：to_archive 全程持有这些 dict 的强引用，它们不会
        # 在此期间被回收、id 也就不会被别的对象复用。
        archived_identities = {id(f) for f in to_archive}
        facts[:] = [f for f in facts if id(f) not in archived_identities]
        # 剩余数报缓存实际长度而不是 active 快照长度：并发 append 进来的行也还在。
        logger.info(f"[FactStore] {name}: 归档 {len(to_archive)} 条已吸收的旧 facts，剩余 {len(facts)} 条")
        return len(to_archive)

    # ── extraction ───────────────────────────────────────────────────

    @staticmethod
    def _flatten_message_content(content) -> str:
        """Flatten a message content (str or content-part list) to plain text."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get('text', f"|{item.get('type', '')}|"))
                else:
                    parts.append(str(item))
            return ''.join(parts)
        return str(content or '')

    @classmethod
    def _format_conversation(cls, messages: list, name_mapping: dict) -> str:
        """Serialize messages into the 'role | content' shape used by LLM prompts."""
        lines = []
        for msg in messages:
            role = name_mapping.get(getattr(msg, 'type', ''), getattr(msg, 'type', ''))
            content = getattr(msg, 'content', '')
            if isinstance(content, (str, list)):
                lines.append(f"{role} | {cls._flatten_message_content(content)}")
        return "\n".join(lines)

    # 段首标记里不允许出现的结构字符：方括号 / 竖线 / 任何换行与制表。
    # speaker_label 是**用户可改**的原始数据（群名片），不中和的话
    # "X]\n[SEGMENT 2 | speaker: Alice" 这种名片会在渲染结果里造出一个
    # 位于行首、逐字节合法的段首。
    _SEGMENT_LABEL_STRUCTURAL = re.compile(r'[\[\]|]')
    # 正文里的段首字面量：即便有逐行前缀兜底（见 _format_speaker_segments），
    # 也把它折成全角左括号，让"看起来像段首"的注入连形状都不成立。
    _SEGMENT_MARKER_LITERAL = re.compile(r'\[\s*SEGMENT', re.IGNORECASE)

    @classmethod
    def sanitize_speaker_label(cls, label) -> str:
        """Strip a speaker label down to something that cannot forge markup.

        剥掉方括号 / 竖线 / 换行 / 控制字符并压缩空白，最后截到 64 字符
        （与路由的长度契约同口径）。返回空串表示这个 label 整个由结构字符
        组成——调用方（路由）按契约违例 fail loud，不要静默替换成占位符：
        插件侧的 label 恒含 "(sender_id)" 数字，空只可能是调用方 bug。"""  # noqa: DOCSTRING_CJK
        text = cls._SEGMENT_LABEL_STRUCTURAL.sub(' ', str(label or ''))
        # 控制字符（含各种换行/分隔符）一律折成空格再压缩：段首必须是单行。
        text = ''.join(ch if ch.isprintable() else ' ' for ch in text)
        return ' '.join(text.split())[:64].strip()

    @classmethod
    def _format_speaker_segments(cls, segments: list[dict], *, nonce: str) -> str:
        """Render multi-speaker segments for the batch extraction prompt.

        段首标记 ``[SEGMENT n:nonce | speaker: label]`` 是 locale 无关的固定
        形状，批模板（FACT_EXTRACTION_BATCH_PROMPT）按原样向模型解释它。

        三层防伪，缺一不可（模板恰恰告诉模型"段首就是归属依据"，能伪造段首
        的群成员就能把自己的内容写进别人的 subject，并借到别人的
        speaker_trust 信任基线）：

        1. **每一行都带前缀**——正文永远不出现在行首，注入进来的
           "\\n[SEGMENT 2 | speaker: Alice]" 只会渲染成
           "| ［SEGMENT 2 | ...]"，明确落在自己那段里。按 ``splitlines()``
           切，覆盖 \\r / \\x85 / U+2028 这些同样会被渲染成换行的分隔符。

           续行用的是**短标记**而不是重复整条 label：label 可以到 64 字符，
           而消息里的换行数不受任何上游限制（路由只数消息条数、群名片也
           没有长度校验），逐行重复 label 等于给攻击者一个 ~67 倍的放大器
           ——一条几千行的消息就能把 prompt 撑爆或耗光抽取超时，而失败的
           批是保留重试的，同批其他成员会被一起拖住（Codex）。短标记把
           放大压到每行 2 字节，防伪性质不变：行首不是段首形状就够了。
        2. **段首带一次性 nonce**——攻击者的消息在 nonce 生成之前就写死了，
           猜不到本次请求的 token，伪造头与真段首形状对不上。
        3. **label 与正文里的结构字面量中和**——label 剥方括号/竖线/换行，
           正文里的 "[SEGMENT" 折成全角左括号。

        nonce 只用于渲染侧的边界防伪，**不要求模型原样回吐**（归属输出仍是
        段号整数）——让模型复述 token 只会凭空增加它出错的面。"""  # noqa: DOCSTRING_CJK
        blocks = []
        for index, segment in enumerate(segments, start=1):
            label = cls.sanitize_speaker_label(segment.get('speaker_label'))
            lines = [f"[SEGMENT {index}:{nonce} | speaker: {label}]"]
            for msg in segment.get('messages') or []:
                body = cls._SEGMENT_MARKER_LITERAL.sub(
                    '［SEGMENT',
                    cls._flatten_message_content(getattr(msg, 'content', '')),
                )
                body_lines = body.splitlines() or ['']
                lines.append(f"{label} | {body_lines[0]}")
                # 续行只冠短标记：同一条消息的后续行，发言人显然没变。
                lines.extend(f"| {line}" for line in body_lines[1:])
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    @staticmethod
    def _strip_code_fence(raw: str) -> str:
        """Remove ```json ... ``` fences if present."""
        if not raw.startswith("```"):
            return raw
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
        if match:
            return match.group(1).strip()
        return raw.replace("```json", "").replace("```", "").strip()

    async def _allm_call_with_retries(
        self, prompt: str, lanlan_name: str, tier: str, call_type: str,
        max_retries: int = 3,
        timeout: float = 60,
        extra_body=_DEFAULT_EXTRA_BODY,
    ):
        """Shared LLM helper: retry on network errors + JSON errors, same
        policy as the old `extract_facts`. Returns parsed JSON or None on
        terminal failure (caller decides whether to abort / swallow).

        Note: no longer accepts temperature. The project-wide convention is to
        never send that parameter (gatekeeper: scripts/check_no_temperature.py).
        The model comes straight from the api_config of ``tier``; the
        SETTING_PROPOSER_MODEL fallback is gone.

        The 60s default timeout suits background LLM calls (Stage-1 fact extract
        / Stage-2 signal detect / negative keyword check); callers may raise it
        as needed (e.g. pass 90s for Stage-2 with thinking on). SDK
        max_retries=0 avoids double-layer retries (the business layer already
        controls retries via its max_retries parameter).

        extra_body: the default _DEFAULT_EXTRA_BODY lets create_chat_llm resolve
        it per model (for most providers this disables thinking); explicitly
        passing None means "send no extra_body" → the model's default behavior
        (thinking models enter thinking mode).
        Phase D: Stage-2 signal detection explicitly passes None to enable thinking."""
        from openai import APIConnectionError, InternalServerError, RateLimitError
        from utils.llm_client import create_chat_llm_async

        retries = 0
        while retries < max_retries:
            try:
                set_call_type(call_type)
                api_config = await self._config_manager.aget_model_api_config(tier)
                from config import LLM_OUTPUT_GUARD_MAX_TOKENS
                _llm_kwargs = dict(
                    timeout=timeout,
                    max_retries=0,
                    max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,
                    provider_type=api_config.get('provider_type'),
                )
                if extra_body is not _DEFAULT_EXTRA_BODY:
                    _llm_kwargs['extra_body'] = extra_body
                llm = await create_chat_llm_async(  # noqa: LLM_OUTPUT_BUDGET  # budget + timeout live in _llm_kwargs above (splat invisible to the lint); guard is generous for variable-length JSON.
                    api_config['model'],
                    api_config['base_url'], api_config['api_key'],
                    **_llm_kwargs,
                )
                try:
                    resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # extract-facts prompt assembled from token-capped recent history components.
                finally:
                    await llm.aclose()
                raw = resp.content.strip()
                raw = self._strip_code_fence(raw)
                return robust_json_loads(raw)
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                retries += 1
                logger.warning(
                    f"[FactStore] {lanlan_name}: {call_type} 网络错误 {type(e).__name__}, "
                    f"重试 {retries}/{max_retries}"
                )
                if retries < max_retries:
                    await asyncio.sleep(2 ** (retries - 1))
                continue
            except json.JSONDecodeError as e:
                retries += 1
                logger.warning(
                    f"[FactStore] {lanlan_name}: {call_type} JSON 解析失败 "
                    f"(重试 {retries}/{max_retries}): {e}"
                )
                if retries < max_retries:
                    await asyncio.sleep(2 ** (retries - 1))
                continue
            except Exception as e:
                retries += 1
                logger.warning(
                    f"[FactStore] {lanlan_name}: {call_type} 失败 "
                    f"(重试 {retries}/{max_retries}): {type(e).__name__}: {e}"
                )
                if retries < max_retries:
                    await asyncio.sleep(2 ** (retries - 1))
                continue

        logger.warning(
            f"[FactStore] {lanlan_name}: {call_type} 达到最大重试 {max_retries}，放弃"
        )
        return None

    async def _allm_extract_facts(
        self, lanlan_name: str, messages: list,
        *, treat_malformed_as_failure: bool = False,
        speaker_label: str | None = None,
    ) -> list[dict] | None:
        """Stage-1: pure extraction. Prompt carries no existing observations
        to avoid self-cycling (the LLM quoting an existing reflection back as a
        new fact). Returns the raw LLM-extracted list, or None on terminal failure.

        ``treat_malformed_as_failure``: a non-array payload (e.g. ``{"facts":
        [...]}``) is a model-shape failure, not a confirmed empty extraction.
        The conversation paths tolerate it as ``[]`` (advance the cursor; the
        window is lossy but recoverable from live chat). Daily import passes
        ``True`` so a malformed result becomes a failed day (retryable) rather
        than being checkpointed in the sidecar as a fact-less day — a sidecar
        checkpoint would skip the LLM on every later import and silently lose
        that day's facts (Codex P2).

        ``speaker_label``: render 'user' turns and the {MASTER_NAME}
        placeholder as this label instead of the configured master name. The
        legacy prompt assumes the human speaker IS the master — true for
        private admin chats, wrong for group-member batches, whose speaker
        would otherwise have their statements extracted as facts about the
        master (Codex P2)."""
        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        name_mapping['ai'] = lanlan_name
        if speaker_label:
            name_mapping['human'] = speaker_label
        conversation_text = self._format_conversation(messages, name_mapping)

        prompt = (
            get_fact_extraction_prompt(get_global_language_full())
            .replace('{CONVERSATION}', conversation_text)
            .replace('{LANLAN_NAME}', lanlan_name)
            .replace('{MASTER_NAME}', name_mapping.get('human', '主人'))
        )

        extracted = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
            call_type="memory_fact_extraction",
        )
        if extracted is None:
            return None
        if not isinstance(extracted, list):
            if treat_malformed_as_failure:
                logger.warning(
                    f"[FactStore] {lanlan_name}: Stage-1 返回非数组 "
                    f"{type(extracted).__name__}，当作抽取失败（可重试，不 checkpoint）"
                )
                return None
            logger.warning(
                f"[FactStore] {lanlan_name}: Stage-1 返回非数组 "
                f"{type(extracted).__name__}，当作空列表处理"
            )
            return []
        return extracted

    async def _allm_extract_facts_batch(
        self, lanlan_name: str, segments: list[dict],
    ) -> list | None:
        """Stage-1 batch: one LLM call over multiple single-speaker segments.

        输出契约是**每段一个对象**（``{"segment": n, "facts": [...]}``，由
        ``extract_facts_batch`` fail-closed 解析）：归属结构化之后，一条带
        内容的事实不可能"归属不明"，段覆盖也变成显式信号。非数组一律按终止
        失败返回 None——唯一调用方是 fail-closed 的 scoped_history 路由，
        没有"宽容当空"的模式。

        占位符替换顺序刻意 {LANLAN_NAME} → {SEGMENT_NONCE} → {SEGMENTS}：
        消息正文里出现字面 "{LANLAN_NAME}" / "{SEGMENT_NONCE}" 时不得被
        二次替换（后者尤其重要——那等于让攻击者把 nonce 印进自己的正文）。"""  # noqa: DOCSTRING_CJK
        # 一次性段边界 token：攻击者的消息在它生成之前就写死了，猜不到。
        nonce = secrets.token_hex(SCOPED_BATCH_SEGMENT_NONCE_BYTES)
        prompt = (
            get_fact_extraction_batch_prompt(get_global_language_full())
            .replace('{LANLAN_NAME}', lanlan_name)
            .replace('{SEGMENT_NONCE}', nonce)
            .replace(
                '{SEGMENTS}',
                self._format_speaker_segments(segments, nonce=nonce),
            )
        )
        extracted = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
            call_type="memory_fact_extraction_batch",
        )
        if extracted is None:
            return None
        if not isinstance(extracted, list):
            logger.warning(
                f"[FactStore] {lanlan_name}: 批抽取返回非数组 "
                f"{type(extracted).__name__}，当作抽取失败（可重试）"
            )
            return None
        return extracted

    @staticmethod
    def _coerce_segment_index(raw, segment_count: int) -> int | None:
        """The 0-based segment index for a model-emitted段号, or None.

        接受 int 与纯数字字符串（模型输出 "1" 的常见形态），bool 显式排除
        （True 是 int 子类）。越界一律 None——绝不 clamp 到边界段：A 的内容
        挂到 B 头上比整批重试严重得多。"""  # noqa: DOCSTRING_CJK
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            seg = raw
        elif isinstance(raw, str):
            # try/except 而非 isdigit() 预检：isdigit() 对上标数字（"²"）等
            # int() 消化不了的字符也返回 True，预检放行后 int() 抛
            # ValueError 会把整批弄崩。
            try:
                seg = int(raw.strip())
            except ValueError:
                return None
        elif isinstance(raw, float) and raw.is_integer():
            seg = int(raw)
        else:
            return None
        if not (1 <= seg <= segment_count):
            return None
        return seg - 1

    # 一条 LLM 事实里真正会被 :meth:`_apersist_new_facts_locked` 读走的键。
    # 只有这一处用它：段对象被整个收作事实时，判断"还剩什么没人读"。
    #
    # ⚠️ 手写清单会随 fact schema 演进变陈旧，而陈旧的后果是**每一条带新
    # 字段的事实都被误判成 failed、桶被无休止重抽**。所以它由
    # test_persisted_fact_fields_matches_what_persist_actually_reads 用 AST
    # 扫 _apersist_new_facts_locked 反查兜底——加了字段忘了更新这里，那条
    # 测试会红，而不是等着线上打转。
    _PERSISTED_FACT_FIELDS = frozenset({
        'text', 'importance', 'entity', 'source', 'event_when',
        '_external_import',
    })

    @staticmethod
    def _as_fact_entry(entry) -> dict | None:
        """Normalize one ``facts[]`` element into a persistable fact, or None.

        裸字符串 promote 成 ``{'text': ...}``：模型偶尔直接给一句话而不是
        对象，它明确承载内容、归属又由所在段对象给定，收下来是无损的——
        比"当垃圾丢掉"（丢内容）和"整段重试"（多花一次抽取）都好。限定
        ``str``：数字/布尔之类的假值渲染成文本毫无意义。"""  # noqa: DOCSTRING_CJK
        if isinstance(entry, dict):
            text = entry.get('text')
            if isinstance(text, str) and text.strip():
                return entry
            return None
        if isinstance(entry, str) and entry.strip():
            return {'text': entry}
        return None

    @classmethod
    def _carries_unused_text(cls, entry) -> bool:
        """True when a value still holds non-blank text somewhere.

        纯粹看"这个值里有没有文字"，不认字段名——字段名那层由
        :meth:`_has_unconsumed_text` 负责。"""  # noqa: DOCSTRING_CJK
        if isinstance(entry, str):
            return bool(entry.strip())
        if isinstance(entry, dict):
            # 键与值一视同仁地往下递归：map 形态的畸形事实
            # （{"Alice likes cats": 7}）可以裹在任意深度的字段下
            # （{"fact": {"Alice likes cats": 7}}），只查顶层键会漏。
            # 字段名形状的键（confidence / start / unit …）不算内容。
            return any(
                (
                    isinstance(key, str)
                    and key.strip()
                    and not cls._FIELD_NAME_RE.match(key)
                )
                or cls._carries_unused_text(value)
                for key, value in entry.items()
            )
        if isinstance(entry, (list, tuple, set)):
            return any(cls._carries_unused_text(v) for v in entry)
        return False

    # JSON 里像"字段名"的键：ASCII 标识符。模型给 schema 加字段时用的是
    # confidence / reason / evidence 这种；而把事实文本塞进键的畸形形态
    # （{"Alice likes cats": 7}）几乎不可能长成标识符——自然语言带空格或
    # 非 ASCII。用它区分"键是字段名"与"键就是内容"。
    _FIELD_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

    @classmethod
    def _holds_unextracted_text(cls, entry, *, always_consumed: tuple = ()) -> bool:
        """True when a **rejected** entry still holds text nobody extracted.

        只对"这一条什么都没抽出来"的元素问这个问题，理由是**重试能不能救**：
        - 什么都没抽出来 → 重来一次抽取，模型完全可能给出规范形状，那截
          内容就回来了。保留桶重试是有意义的；
        - 已经抽出了事实、只是旁边还挂着别的字段 → 重抽会复现同一个形状，
          那个字段照样没人读。判 failed 换不回任何东西，只会让这个成员的
          记忆**永远结算不掉**：桶一路涨到硬顶后连原始消息一起丢，比丢一个
          附注严重得多。那种情况只记一条 WARNING（见调用方），段照常 ok。

        ⚠️ **键本身也可能就是内容**：``{"Alice likes cats": 7}`` 这种 map
        形态的畸形事实，文本全在键上、值是个数字——只查值会把它当空壳丢掉
        （Codex）。所以非字段名形状的键（见 :attr:`_FIELD_NAME_RE`）只要
        非空白就算内容。

        ``always_consumed``：调用方已经逐条解析过的键（段对象的 ``segment``
        / ``facts``），当作已读走。"""  # noqa: DOCSTRING_CJK
        if not isinstance(entry, dict):
            return cls._carries_unused_text(entry)
        for key, value in entry.items():
            if isinstance(key, str) and key in always_consumed:
                continue
            if (
                isinstance(key, str)
                and key.strip()
                and not cls._FIELD_NAME_RE.match(key)
            ):
                return True
            if cls._carries_unused_text(value):
                return True
        return False

    @classmethod
    def _unread_fields_of_accepted_fact(cls, fact, *, always_consumed=()) -> list:
        """Field names on an accepted fact that nobody downstream reads.

        只用于打日志（见 :meth:`_holds_unextracted_text` 里的理由：判 failed
        换不回内容、只会让这个成员永远结算不掉）。留这条日志是为了让"模型
        开始往事实上挂别的文字"这件事看得见——真发生了就去改 prompt，而不是
        靠一个永远重试的闸门去发现。"""  # noqa: DOCSTRING_CJK
        if not isinstance(fact, dict):
            return []
        return sorted(
            str(key)[:64] for key, value in fact.items()
            if key not in cls._PERSISTED_FACT_FIELDS
            and key not in always_consumed
            # 键与值一视同仁（与 _carries_unused_text 同口径）：
            # {"text": "A", "Bob 的生日是 3 月 5 日": 7} 里文本全在键上，
            # 只查值的话这条内容连一行日志都留不下。
            and (
                (
                    isinstance(key, str)
                    and key.strip()
                    and not cls._FIELD_NAME_RE.match(key)
                )
                or cls._carries_unused_text(value)
            )
        )

    @classmethod
    def _parse_batch_segment_entry(
        cls, item, segment_count: int,
    ) -> tuple[int | None, list[dict], int, int]:
        """Parse one top-level element of the batch payload.

        Returns ``(0-based index | None, facts, dropped, suspect)``。

        ``index is None`` 表示这个元素**放不下去**——它可能承载着某一段的
        内容而我们无从判断是哪段，调用方据此整批 raise（与
        :meth:`extract_facts` 对畸形元素"整批可重试失败"同一条不变式：
        persist 会静默跳过畸形项，调用方推进游标后该元素承载的内容永久丢失）。

        丢弃分两档，判据是"丢了会不会丢内容"：
        - ``dropped``：空壳（``{}`` / ``{"text": ""}`` / ``{"importance": 5}``
          / 空串），丢了不丢内容，本段照常 ``ok``；
        - ``suspect``：我们**没能用上、但里面还有文字**的元素——本段判
          ``failed`` 让调用方保留桶重试。嵌套形状消除了"有内容却归属不明"，
          但消除不了"有内容却看不懂形状"，那一类必须 fail-closed。

        容忍的形状：
        - 规范：``{"segment": n, "facts": [...]}``；
        - 段内无事实：``{"segment": n}``（模型显式点名该段且没给内容——
          这是合法的"本段无事实"结论，不是漏标）；
        - 旧的扁平事实：``{"segment": n, "text": ...}``，**含与 facts 数组
          同时出现的情形**——两种约定混用时归属并无歧义（都挂在这一个段
          对象上），元素自带的 text 必须一起收下，不能被 list 分支吃掉。
          这种形态整个 dict 原样交给 persist（event_when / entity / source
          等字段那边自己读），所以它身上没有"被丢弃的内容"可言；
        - ``facts`` 里的裸字符串（见 :meth:`_as_fact_entry`）。
        ``facts`` 存在、不是数组、又不是 ``null`` = 形状坏了且可能带内容 →
        放不下去。``null`` 例外：它不承载任何内容，语义与"没给这个字段"、
        与空数组都一样是"本段无事实"，按缺席处理（判成畸形会为了一个空值
        把整批 8 段一起打回重抽）。"""  # noqa: DOCSTRING_CJK
        if not isinstance(item, dict):
            return None, [], 0, 0
        index = cls._coerce_segment_index(item.get('segment'), segment_count)
        if index is None:
            return None, [], 0, 0
        raw_facts = item.get('facts')
        if raw_facts is not None and not isinstance(raw_facts, list):
            return None, [], 0, 0

        kept: list[dict] = []
        dropped = 0
        suspect = 0
        unread_fields: list[str] = []
        for entry in (raw_facts or []):
            fact = cls._as_fact_entry(entry)
            if fact is None:
                # 读不成事实：里面还攥着没人抽走的文字就 suspect（重抽有可能
                # 把它变成规范形状救回来），纯空壳才丢。
                if cls._holds_unextracted_text(entry):
                    suspect += 1
                else:
                    dropped += 1
                continue
            kept.append(fact)
            unread_fields.extend(cls._unread_fields_of_accepted_fact(fact))
        own_fact = cls._as_fact_entry(item)
        # 段对象上还剩什么没人读？剩下的键里若攥着文字，说明这个形状我们
        # 没读懂，别把它当成"本段的结论"——那会让调用方 pop 掉桶，那截
        # 文字就此消失。
        #
        # 排除集分两种情形，判据是"这次到底消费掉了什么"：
        # - item 被整个收作事实 → persist 会读走 _PERSISTED_FACT_FIELDS 那
        #   一组（event_when 里的 "day"、entity 的 "master" 都是被消费的，
        #   算成旁挂文字会让每条带时间线索的扁平事实都误判 failed）；但
        #   **它读不到的键仍然没人读**，比如 {"text": "...", "note": "..."}
        #   里的 note——那截内容确实会随着 pop 一起消失，仍要判 suspect。
        # - item 读不成事实 → 连 text 都没被消费（{"text": ["Alice 养猫"]}
        #   这种内容裹在非字符串里），一个都不能排除。
        if own_fact is not None:
            kept.append(own_fact)
        # 段对象"答过了"= 给了自己的事实、或给了 facts 数组（哪怕是空的——
        # 空数组正是"本段无事实"这个合法结论）。答过了就跟被收下的事实同
        # 一档：旁挂字段只记日志。{"segment": 1, "facts": [],
        # "reason": "..."} 判 failed 换不回任何东西，模型只要习惯性带上这个
        # 字段，这个成员就永远结算不掉（Codex）。
        if own_fact is not None or isinstance(raw_facts, list):
            unread_fields.extend(cls._unread_fields_of_accepted_fact(
                item, always_consumed=('segment', 'facts'),
            ))
        elif cls._holds_unextracted_text(
            item, always_consumed=('segment', 'facts'),
        ):
            suspect += 1
        if unread_fields:
            logger.warning(
                f"[FactStore] 批抽取：模型往事实上挂了没人读的字段 "
                f"{sorted(set(unread_fields))}——那部分内容不会入库。判 failed "
                f"换不回它（重抽会复现同一个形状），所以只记一条日志；真频繁"
                f"出现就去改 prompt。"
            )
        return index, kept, dropped, suspect

    @staticmethod
    def _speaker_provenance_of(segment: dict) -> dict | None:
        """The provenance fields to stamp onto this segment's persisted facts.

        只落字段不接消费（发言人信赖度阶段一）：speaker_label = 谁说的，
        speaker_trust = 调用方按权限等级派生的 0..1 初值。永远来自请求段，
        绝不读 LLM 输出——模型在输出里伪造同名键不会被采纳。"""  # noqa: DOCSTRING_CJK
        prov: dict = {}
        label = str(segment.get('speaker_label') or '').strip()
        if label:
            prov['speaker_label'] = label[:64]
        trust = segment.get('speaker_trust')
        if (
            isinstance(trust, (int, float))
            and not isinstance(trust, bool)
            and 0.0 <= float(trust) <= 1.0
        ):
            prov['speaker_trust'] = float(trust)
        return prov or None

    async def extract_facts_batch(
        self, segments: list[dict], lanlan_name: str,
    ) -> list[dict]:
        """Multi-segment scoped extraction: one LLM call, per-segment dispatch.

        ``segments``: ``[{'messages': [...], 'subject': MemorySubject,
        'speaker_label': str, 'speaker_trust': float|None}, ...]``——每段一位
        发言人。成本从 O(发言人数) 次 LLM 调用降到每批一次。

        Returns one result dict per segment, in request order:
        ``{'status': 'ok'|'failed', 'created': list[dict], 'dropped': int}``。

        fail-closed 语义是 **per-段** 的（对比 :meth:`extract_facts` 的整批
        判定）：
        - 整个 LLM 调用终止失败 / 返回非数组 → raise
          :class:`FactExtractionFailed`（路由 502，调用方整批保留重试）；
        - 任一顶层元素**放不下去**（段号缺失/越界、facts 不是数组）→ 整批
          raise：它可能承载着某段的内容而我们无从判断是哪段，静默丢弃
          等于让调用方 pop 掉一份内容已经消失的桶；
        - 某段**没出现在输出里** → 该段 ``failed``（保留重试），绝不当成
          "本段无事实"：模型把八段内容全并进段 1 时，另外七个人的桶
          （成员维度唯一副本）会被调用方一次性弹光；
        - 段对象里的空壳条目（``{}`` / ``{"text": ""}`` / 空串）→ 丢弃并
          计入 ``dropped`` 回报给调用方，本段照常 ``ok``：它们**不承载
          内容**，丢它不丢东西；
        - 段对象里**看不懂形状、但还攥着文字**的条目 → 本段 ``failed``，
          认出来的那些仍照常落盘（重试靠 SHA-256 去重兜住重复，而万一
          重试一直失败，起码认出来的这些不会跟着丢）；
        - 某段 persist 失败 → 只该段 ``failed``，其余段不连累重来。

        整个输出为空数组是合法结论（"整批没有值得记的事实"），此时所有段
        ``ok`` 且零 fact——群聊里这是最常见的一批，把它当成"零段被覆盖"
        会让每一批安静的群消息都进入无尽重试。
        """  # noqa: DOCSTRING_CJK
        if not segments:
            return []
        if len(segments) == 1:
            # 单段无归属风险：走成熟的单发抽取管线（prompt 更完整、
            # malformed 判定也更严）。
            segment = segments[0]
            created = await self.extract_facts(
                segment.get('messages') or [],
                lanlan_name,
                subject=segment.get('subject'),
                fail_closed=True,
                speaker_label=segment.get('speaker_label'),
                speaker_provenance=self._speaker_provenance_of(segment),
            )
            return [{'status': 'ok', 'created': created, 'dropped': 0}]

        extracted = await self._allm_extract_facts_batch(lanlan_name, segments)
        if extracted is None:
            raise FactExtractionFailed(
                f"batch Stage-1 LLM call failed for {lanlan_name!r} "
                f"({len(segments)} segments, fail_closed caller)"
            )
        # None = 该段没出现在输出里（≠ 该段无事实，后者是空 list）。
        per_segment: list[list[dict] | None] = [None] * len(segments)
        dropped_per_segment = [0] * len(segments)
        suspect_per_segment = [0] * len(segments)
        unplaceable = 0
        for item in extracted:
            index, facts, dropped, suspect = self._parse_batch_segment_entry(
                item, len(segments),
            )
            if index is None:
                unplaceable += 1
                continue
            if per_segment[index] is None:
                per_segment[index] = []
            per_segment[index].extend(facts)
            dropped_per_segment[index] += dropped
            suspect_per_segment[index] += suspect
        if unplaceable:
            raise FactExtractionFailed(
                f"batch Stage-1 returned {unplaceable}/{len(extracted)} "
                f"unplaceable entries for {lanlan_name!r} (fail_closed caller)"
            )
        if not extracted:
            # 空数组 = 模型对整批的结论是"没有值得记的事实"，每段都算已答复。
            per_segment = [[] for _ in segments]
        if any(facts is None for facts in per_segment):
            missing = [
                i + 1 for i, facts in enumerate(per_segment) if facts is None
            ]
            logger.warning(
                f"[FactStore] {lanlan_name}: 批抽取输出缺段 {missing}（共 "
                f"{len(segments)} 段）——按失败保留重试，绝不当成「本段无事实」"
            )

        results: list[dict] = []
        for position, (segment, segment_facts) in enumerate(
            zip(segments, per_segment), start=1,
        ):
            dropped = dropped_per_segment[position - 1]
            suspect = suspect_per_segment[position - 1]
            if segment_facts is None:
                results.append(
                    {'status': 'failed', 'created': [], 'dropped': dropped}
                )
                continue
            if dropped:
                logger.warning(
                    f"[FactStore] {lanlan_name}: 批抽取第 {position} 段丢弃 "
                    f"{dropped} 条无内容的垃圾条目（归属由段对象给定，"
                    f"丢弃不损失内容）"
                )
            status = 'ok'
            if suspect:
                # 有看不懂但攥着文字的条目：本段报 failed 让调用方保留桶。
                # 认出来的那些照常落盘——重试会把它们重新抽一遍，SHA-256
                # 去重兜住重复，而万一重试一直失败，起码这些不会跟着丢。
                logger.warning(
                    f"[FactStore] {lanlan_name}: 批抽取第 {position} 段有 "
                    f"{suspect} 条形状看不懂但带文字的条目——按失败保留重试"
                )
                status = 'failed'
            created: list[dict] = []
            if segment_facts:
                try:
                    created = await self._apersist_new_facts(
                        lanlan_name,
                        segment_facts,
                        subject=segment.get('subject'),
                        speaker_provenance=self._speaker_provenance_of(segment),
                    )
                except Exception as exc:
                    logger.error(
                        f"[FactStore] {lanlan_name}: 批抽取第 "
                        f"{position} 段持久化失败（其余段不受连累）: {exc}"
                    )
                    results.append(
                        {'status': 'failed', 'created': [], 'dropped': dropped}
                    )
                    continue
            results.append(
                {'status': status, 'created': created, 'dropped': dropped}
            )
        return results

    # Source-tier 白名单。'user_observation' = path A 抽出的 user msg ground truth；
    # 'ai_disclosure' = path B 抽出的 AI 自我披露/屏幕上下文（trust-tier 较低）。
    # 老 fact 没 source 字段时按 'user_observation' 回退（向后兼容——pre-#PR
    # 时代所有 fact 都源自 user msg）。
    _SOURCE_VALUES = frozenset({'user_observation', 'ai_disclosure'})
    _SOURCE_DEFAULT = 'user_observation'

    # 内存内纸条，由 source 升级分支挂上、由 save_facts 摘下并放行一次
    # signal_processed 的单调回写。下划线前缀与 '_external_import' 同约定：
    # 只在一次 persist 流程内活着，save_facts 写盘前一律剥掉，不进磁盘。
    #
    # 存在的理由：save_facts 的 read-merge 把 signal_processed 当成只能
    # False→True 的字段（#976，防旧缓存用 False 覆盖磁盘上的 True 让同一批
    # fact 被 drain loop 重复消费），而升级分支恰恰要把它翻回 False（#1408，
    # 用户印证过的 AI 披露要重进 Stage-2）。两条规则正面撞车、存盘那条跑在
    # 后面永远赢，于是"升级后重进 Stage-2"在盘上从来没成立过。让解封方留一
    # 张显式纸条，比让 save_facts 去倒推"这条是不是刚被解封"更不容易看走眼。
    _SIGNAL_RESET_PENDING = '_signal_reset_pending'

    @staticmethod
    def _apply_external_import_provenance(entry: dict, external_import: dict) -> None:
        """Stamp external-import provenance onto a fact entry: metadata, tags, the
        event_start_at derived from event_date, and signal_processed=True
        (external_import facts skip the Stage-2 evidence loop)."""
        entry['external_import'] = dict(external_import)
        entry['tags'] = ['external_import', str(external_import.get('format') or 'unknown')]
        entry['signal_processed'] = True
        event_date = external_import.get('event_date')
        if isinstance(event_date, str) and event_date:
            entry['event_start_at'] = f"{event_date}T00:00:00"

    async def _apersist_new_facts(
        self, lanlan_name: str, extracted: list[dict],
        *,
        default_source: str = 'user_observation',
        semantic_dedup: bool = True,
        subject: MemorySubject | dict | None = None,
        speaker_provenance: dict | None = None,
    ) -> list[dict]:
        async with self._get_persist_alock(lanlan_name):
            return await self._apersist_new_facts_locked(
                lanlan_name,
                extracted,
                default_source=default_source,
                semantic_dedup=semantic_dedup,
                subject=subject,
                speaker_provenance=speaker_provenance,
            )

    async def apersist_scoped_facts(
        self,
        lanlan_name: str,
        extracted: list[dict],
        *,
        subject: MemorySubject | dict,
    ) -> list[dict]:
        """Persist already extracted facts for an explicitly scoped adapter request."""
        return await self._apersist_new_facts(
            lanlan_name,
            extracted,
            subject=subject,
        )

    async def _apersist_new_facts_locked(
        self, lanlan_name: str, extracted: list[dict],
        *,
        default_source: str = 'user_observation',
        semantic_dedup: bool = True,
        subject: MemorySubject | dict | None = None,
        speaker_provenance: dict | None = None,
    ) -> list[dict]:
        """Dedup (SHA-256 + FTS5) + persist. importance < 5 facts are KEPT
        (RFC §3.1.3)—downstream `get_unabsorbed_facts(min_importance=5)`
        filters at read time.

        ``default_source``: the fallback when an LLM-emitted fact dict has no
        ``source`` field. Path A callers pass ``'user_observation'`` (also the
        default), path B callers pass ``'ai_disclosure'`` — a source field
        explicitly emitted by the LLM wins over the default.

        External migration batches may set ``semantic_dedup=False`` after
        preview to avoid one FTS5 search per candidate while holding the
        persistence lock. Exact SHA-256 deduplication still applies.

        Monotonic source upgrade: when SHA-256 hits an existing fact, normally
        skip without writing. **Sole exception**: the existing fact's source is
        'ai_disclosure' and the new fact's is 'user_observation' → upgrade
        existing.source in place + reset signal_processed=False so Stage-2
        re-evaluates. The reverse (user→ai) never downgrades — user
        corroboration is irreversible.

        ``speaker_provenance``: 发言人来源标识（{'speaker_label', 'speaker_
        trust'} 子集），盖在本批每条**新建** fact 上。只落字段不接消费
        （信赖度阶段一）；来自调用方（scoped_history 请求段），绝不读
        extracted 元素里的同名键——LLM 输出无法伪造它。
        """  # noqa: DOCSTRING_CJK
        if default_source not in self._SOURCE_VALUES:
            default_source = self._SOURCE_DEFAULT
        memory_subject = coerce_subject(subject)

        new_facts: list[dict] = []
        upgraded_count = 0
        # (entry, 原 source, 原 signal_processed)：落盘/索引失败时还原
        # in-place 升级，否则重试撞守卫直接跳过保存。
        upgraded_snapshots: list[tuple[dict, Any, Any]] = []
        existing_facts = await self.aload_facts(lanlan_name)
        existing_hashes = {f.get('hash') for f in existing_facts if f.get('hash')}
        # hash → fact 的快查表（仅 upgrade 路径用）。aload_facts 已经 in-place
        # 缓存了 list，这里读不复制。
        hash_to_existing = {
            f.get('hash'): f for f in existing_facts if f.get('hash')
        }
        # id → fact 快查表：Stage-2 语义命中后按 id 找到既存 fact，比较 daily
        # event_date 决定是否豁免（跨日期重复事件不算 dup，CodeRabbit）。
        facts_by_id = {f.get('id'): f for f in existing_facts if f.get('id')}

        for fact in extracted:
            if not isinstance(fact, dict):
                continue
            text = fact.get('text', '').strip()
            if not text:
                continue
            try:
                importance = int(fact.get('importance', 5))
            except (ValueError, TypeError):
                importance = 5
            # Clamp to the documented 1..10 range so downstream consumers
            # can assume a well-formed value; dirty LLM output (-3, 999)
            # would otherwise leak straight into reflection synthesis
            # weighting and audit dashboards (CodeRabbit PR #929).
            if importance < 1:
                importance = 1
            elif importance > 10:
                importance = 10
            # RFC §3.1.3: **不再**在抽取入口硬丢 importance < 5。所有 fact
            # 一律落盘，消费侧按场景 min_importance= 过滤；保留完整 audit。

            # Scoped writes own their entity: untrusted LLM output must not
            # redirect a group/participant fact into the legacy master bucket.
            # Legacy writes keep the original three-value whitelist unchanged.
            if memory_subject is not None:
                entity = memory_subject.kind
            else:
                raw_entity = fact.get('entity', 'master')
                if raw_entity in ('master', 'neko', 'relationship'):
                    entity = raw_entity
                else:
                    logger.debug(
                        f"[FactStore] {lanlan_name}: LLM 返回非法 entity={raw_entity!r}，回退到 master"
                    )
                    entity = 'master'

            # Source resolution: LLM 显式 source 优先 + 白名单 + default fallback
            raw_source = fact.get('source')
            if raw_source in self._SOURCE_VALUES:
                source = raw_source
            else:
                source = default_source

            # Stage 1: SHA-256 exact dedup（+ source monotonic upgrade）。
            # daily 导入 fact 以「event_date + 文本」为精确键：同一天重试仍幂等，
            # 不同日期的重复事件（如连着两天"去了健身房"）各自落盘、各留 provenance
            # （CodeRabbit）。盐进 'hash' 持久字段，重试对比的是同样盐化的值。
            external_import = fact.get('_external_import')
            if not isinstance(external_import, dict):
                external_import = None
            daily_event_date = (
                str(external_import.get('event_date'))
                if external_import
                and external_import.get('section') == 'daily'
                and external_import.get('event_date')
                else None
            )
            hash_input = f"{daily_event_date}\n{text}" if daily_event_date else text
            if memory_subject is not None:
                hash_input = f"{memory_subject.key}\n{memory_subject.scope}\n{hash_input}"
            content_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
            if content_hash in existing_hashes:
                existing = hash_to_existing.get(content_hash)
                if (
                    existing is not None
                    and existing.get('source', self._SOURCE_DEFAULT) == 'ai_disclosure'
                    and source == 'user_observation'
                ):
                    # Path A 用 user msg 印证了之前 path B 写过的 ai_disclosure fact
                    # → 升级 source + 重新进 Stage-2 evidence loop。
                    # scoped fact 例外：简化管线不进 Stage-2，升级 source 但
                    # 保持 signal_processed=True。
                    upgraded_snapshots.append((
                        existing,
                        existing.get('source', self._SOURCE_DEFAULT),
                        existing.get('signal_processed'),
                    ))
                    existing['source'] = 'user_observation'
                    existing['signal_processed'] = memory_subject is not None
                    # 若这条印证来自外部导入，补上 external_import provenance——否则
                    # SHA 命中直接 continue 会漏掉标签（external_import 语义会把
                    # signal_processed 置回 True，不进 Stage-2）(Codex P2)。
                    if external_import is not None:
                        self._apply_external_import_provenance(existing, external_import)
                    # 给 save_facts 的单调 read-merge 留纸条：这次的 False 是故意
                    # 翻回来的，别按"只能 False→True"把它顶回 True。放在 provenance
                    # 之后并复查一次实际值——外部导入会把它重新封回 True，那种情况
                    # 下不该留纸条，否则纸条与 entry 的真实状态对不上。
                    if existing.get('signal_processed') is False:
                        existing[self._SIGNAL_RESET_PENDING] = True
                    upgraded_count += 1
                continue

            # Stage 2: FTS5 semantic dedup (lightweight, no LLM)
            if semantic_dedup and self._time_indexed is not None:
                # FTS5 is still character-wide, so fetch a wider window and
                # keep the historical "top-3 candidates" semantics *inside*
                # the subject boundary: cross-subject rows neither count as
                # duplicates nor consume the 3-candidate budget (a busy
                # group would otherwise crowd legacy candidates out of a
                # top-3 fetch and let legacy near-duplicates slip through).
                # 扇出场景（同一事件按 subject 存 N 份、BM25 并列）可能把
                # 首窗 10 条全部占满——此时本 subject 的候选在 rank 11 之后，
                # 一次性扩窗到 200 重扫；仍不足就放行（>200 条命中意味着
                # 文本本身是退化的口水句，去重已无意义）。
                is_dup = False
                raw_limit = 10
                archived_by_id: dict | None = None

                async def _aarchived_by_id() -> dict:
                    # 惰性读 facts_archive.json（仅当 FTS 命中不在活跃集时）：
                    # 归档行仍在 FTS 索引里，但 facts_by_id 只含活跃行——
                    # 不解析归档元数据的话，归档后的同文本会绕过去重
                    # （legacy 在 main 上本来是挡得住的，属回归；scoped
                    # 需要 subject 戳判界）。
                    nonlocal archived_by_id
                    if archived_by_id is None:
                        def _read() -> dict:
                            path = self._facts_archive_path(lanlan_name)
                            if os.path.exists(path):
                                try:
                                    with open(path, encoding='utf-8') as fh:
                                        data = json.load(fh)
                                    if isinstance(data, list):
                                        return {
                                            r.get('id'): r for r in data
                                            if isinstance(r, dict)
                                        }
                                except (
                                    json.JSONDecodeError,
                                    UnicodeDecodeError,
                                    OSError,
                                ):
                                    # 损坏归档降级为"仅活跃数据"（对齐
                                    # load_facts_full），不得阻断本次写入。
                                    pass
                            return {}
                        archived_by_id = await asyncio.to_thread(_read)
                    return archived_by_id

                while True:
                    similar = await self._time_indexed.asearch_facts(
                        lanlan_name, text, raw_limit,
                    )
                    same_subject_seen = 0
                    for fid, score in similar:
                        hit = facts_by_id.get(fid)
                        if hit is None:
                            hit = (await _aarchived_by_id()).get(fid)
                        if hit is None or not entry_matches_subject(hit, memory_subject):
                            continue
                        same_subject_seen += 1
                        if same_subject_seen > 3:
                            break
                        if score >= -5:
                            continue
                        if daily_event_date:
                            # daily 候选：命中的既存 fact 若也是 daily 且日期不同 →
                            # 跨日期重复事件，不算语义重复（同日期近似命中仍挡住，
                            # 兜 LLM 重抽输出不稳定的重试幂等）。
                            hit_meta = (hit or {}).get('external_import')
                            hit_date = (
                                str(hit_meta.get('event_date'))
                                if isinstance(hit_meta, dict) and hit_meta.get('event_date')
                                else None
                            )
                            if hit_date and hit_date != daily_event_date:
                                continue
                        is_dup = True
                        break
                    if (
                        is_dup
                        or same_subject_seen >= 3
                        or len(similar) < raw_limit
                        or raw_limit >= 200
                    ):
                        break
                    raw_limit = 200
                if is_dup:
                    continue

            created_at_iso = datetime.now().isoformat()
            # Event timing (schema v2): LLM 输出相对时间 (offset+unit)，系统
            # 按 created_at 当锚点解算成 ISO。fact 没有 temporal_scope，但事件
            # 起始时间在过时 block 渲染和未来重判时都需要——fallback_start=True
            # 保证一定有 event_start_at。end_at 是 optional（fact 多数是即时
            # 观察，无明确 end）。
            event_when_raw = normalize_event_when(fact.get('event_when'))
            event_start_at, event_end_at = compute_event_timestamps(
                event_when_raw,
                created_at_iso,
                fallback_start=True,
                fallback_end=False,
            )
            fact_entry = {
                'id': f"fact_{datetime.now().strftime('%Y%m%d%H%M%S')}_{content_hash[:8]}",
                'text': text,
                'importance': importance,
                'entity': entity,
                # Trust-tier source（path A 写 'user_observation'，path B 写
                # 'ai_disclosure'）。Stage-2 / 其他 evidence-loop 消费者按需 filter。
                # 老 fact 缺该字段时读侧默认 'user_observation' 向后兼容。
                'source': source,
                # RFC §2.7: tags 字段保留位但新 fact 默认写空，LLM 不再填
                'tags': [],
                'hash': content_hash,
                'created_at': created_at_iso,
                # Schema v2 (memory/temporal.py)：事件发生时间，LLM 用相对偏移
                # 输出（offset+unit），系统按 created_at 解算。event_when_raw
                # 留底供后续重判 / debug 反查。
                'event_when_raw': event_when_raw,
                'event_start_at': event_start_at,
                'event_end_at': event_end_at,
                'schema_version': MEMORY_SCHEMA_VERSION_CURRENT,
                'absorbed': False,  # True when consumed by a reflection
                # Stage-2 signal detection drain marker. False → still in queue
                # for the next idle-loop tick. amark_signal_processed() flips
                # to True after Stage-2 LLM returns successfully. Old facts.json
                # without this key are read with default=True (i.e. treated as
                # already processed) so an upgrade doesn't replay months of
                # history through Stage-2.
                #
                # ⚠️ source='ai_disclosure' fact：写盘时直接置 True，让 Stage-2
                # 永不取它。配合 aextract_facts_and_detect_signals 内部的
                # source filter 做双重防御，防漏。
                # ⚠️ scoped（群/成员）fact 同样置 True：群记忆走简化管线
                # （facts → 定期合成 confirmed reflection → time-driven 晋升），
                # 不参与 Stage-2 evidence。scoped 分区没有 user-confirm 通道，
                # 其观察池冷启动恒空，进队列只会永久占用 batch 名额，把 legacy
                # 私聊主链路饿死。
                'signal_processed': (
                    source == 'ai_disclosure' or memory_subject is not None
                ),
                # Vector-embedding cache (memory-enhancements P2 — see
                # memory/embeddings.py). Written as None so /process
                # returns immediately without blocking on embedding;
                # the background warmup worker fills the triple in
                # batches once the EmbeddingService is ready. Used by
                # the upcoming fact dedup path (cosine > threshold →
                # LLM arbitration queue).
                'embedding': None,
                'embedding_text_sha256': None,
                'embedding_model_id': None,
            }
            if memory_subject is not None:
                fact_entry.update(memory_subject.as_entry_fields())
            if speaker_provenance:
                # 只挑白名单键（防调用方 dict 形状漂移把任意键写进磁盘）。
                label = str(speaker_provenance.get('speaker_label') or '').strip()
                if label:
                    fact_entry['speaker_label'] = label[:64]
                trust = speaker_provenance.get('speaker_trust')
                if (
                    isinstance(trust, (int, float))
                    and not isinstance(trust, bool)
                    and 0.0 <= float(trust) <= 1.0
                ):
                    fact_entry['speaker_trust'] = float(trust)
            if external_import is not None:
                self._apply_external_import_provenance(fact_entry, external_import)
            existing_facts.append(fact_entry)
            existing_hashes.add(content_hash)
            facts_by_id[fact_entry['id']] = fact_entry
            # 同步更新 hash_to_existing：若本 batch 后续还有同 text 的 fact
            # 出现（如 LLM 偶发重复 / 同 batch 跨段抽到同一观察），下一轮命
            # 中 `content_hash in existing_hashes` 时能拿到本轮刚写入的
            # fact_entry 走 monotonic upgrade 路径。否则 hash_to_existing.
            # get() 返 None → 跳过 upgrade，新观察的 user_observation 升级被
            # 静默丢弃 (Codex P2 round-10 on PR #1408)。
            hash_to_existing[content_hash] = fact_entry
            new_facts.append(fact_entry)

            if self._time_indexed is not None:
                try:
                    await self._time_indexed.aindex_fact(
                        lanlan_name, fact_entry['id'], text,
                    )
                except BaseException:
                    # 索引失败也必须回滚（含取消：CancelledError 不经
                    # except Exception，留下的缓存会让重试撞去重"空成功"）：本行已进缓存/hash 集合，留着会让
                    # fail-closed 重试撞去重拿"空成功"、调用方推游标，而
                    # facts.json 从未收到它（维护模式等场景）。
                    await self._rollback_uncommitted_facts(
                        lanlan_name, new_facts, existing_hashes,
                        upgraded_snapshots,
                    )
                    raise

        # Save if we either added new facts OR upgraded existing ones'
        # source field. Without the upgrade path: A 后 B 跑时撞到 hash 但
        # 上下源不同会丢 in-place 改的字段，下次启动 reload facts.json 就
        # 把升级 wipe 了。
        if new_facts or upgraded_count:
            try:
                await self.asave_facts(lanlan_name)
            except BaseException:
                # 落盘失败或被取消：进程内缓存已 append、FTS 已索引——留着的话
                # fail-closed 调用方重试会撞内容 hash 去重、拿到"空成功"
                # 并推进游标，而磁盘上什么都没有，重启即永久丢失。回滚
                # 本批新增（upgrade 的 in-place 字段改动保留：字段级幂等，
                # 重试会重做），让重试重新走完整提取+持久化。
                await self._rollback_uncommitted_facts(
                    lanlan_name, new_facts, existing_hashes,
                    upgraded_snapshots,
                )
                raise
        if new_facts:
            logger.info(
                f"[FactStore] {lanlan_name}: 提取了 {len(new_facts)} 条新事实"
            )
            for nf in new_facts:
                if nf.get('subject_kind') or nf.get('subject_id') or nf.get('scope'):
                    # scoped（群/成员衍生）事实原文不进日志：只打域标识与
                    # 长度，对齐 scoped 反思/correction dead-letter 的口径。
                    logger.debug(
                        f"   - [scoped {nf.get('subject_kind','?')}"
                        f"/{nf.get('subject_id','?')}] "
                        f"len={len(nf.get('text','') or '')}"
                    )
                else:
                    logger.debug(
                        f"   - [{nf.get('entity','?')}/{nf.get('source','?')}] {nf.get('text','')[:80]}"
                    )
        if upgraded_count:
            logger.info(
                f"[FactStore] {lanlan_name}: 升级 {upgraded_count} 条 ai_disclosure → user_observation "
                f"(user 印证后重入 Stage-2 evidence loop)"
            )

        return new_facts

    async def _aload_signal_targets(
        self, lanlan_name: str,
        reflection_engine=None, persona_manager=None,
        new_facts: list[dict] | None = None,
    ) -> list[dict]:
        """Assemble the Stage-2 `existing_observations` set.

        Per RFC §3.4.2 coverage rule:
          - all confirmed + promoted reflections (most recent first)
          - all non-protected persona entries

        Scale control (§3.4.2 end):
          - When ``new_facts`` is provided, the pool is routed through
            ``MemoryRecallReranker.aretrieve_candidates``, which owns
            the full pipeline regardless of vector service state:
            hard_filter (drops suppress / terminal / score<0 /
            protected) → coarse rank (cosine top-K when vectors are
            ready, evidence_score order otherwise) → optional LLM
            rerank.  Vectors save Stage-2 prompt tokens when ready;
            when not ready, behaviour collapses to filtered top-N by
            evidence_score, matching the legacy contract but with
            the suppression filter still applied.
          - When ``new_facts`` is empty, falls through to the legacy
            local top-N by evidence_score (no hard_filter — that
            shape predates P2 and is what idle-maintenance entry
            points expect).

        Injection pattern: memory_server wires `reflection_engine` / `persona_manager`
        references at call time. Without them we return empty, which simply
        makes Stage-2 skip (fail-open for unit tests).

        Returns list of {id, text, entity, evidence_score} — id is already
        in the `{target_type}.{entity}.{suffix}` shape the prompt expects.
        """
        now = datetime.now()
        pool: list[dict] = []

        # CodeRabbit follow-up：之前 reflection / persona 加载失败时被 try-except
        # 吞掉，只 debug log 后继续返回 partial pool。下游 caller 看到非空 pool
        # 会正常 mark batch processed，那部分失败的池里可能 reinforce/negate 的
        # signal 永久丢失。改成不 catch、直接 raise，让 caller (drain 路径) 用
        # try-except 捕获并跳过 mark，保证下轮 idle 重试。
        # NegKW caller (memory_server.py) 已有自己的 try-except，不受影响。
        if reflection_engine is not None:
            all_refl = await reflection_engine._aload_reflections_full(lanlan_name)
            for r in all_refl:
                if r.get('status') not in ('confirmed', 'promoted'):
                    continue
                pool.append({
                    'id': f"reflection.{r.get('id', '')}",
                    'raw_id': r.get('id', ''),
                    'target_type': 'reflection',
                    'text': r.get('text', ''),
                    'entity': r.get('entity', 'relationship'),
                    'score': evidence_score(r, now),
                    'embedding': r.get('embedding'),
                    'embedding_text_sha256': r.get('embedding_text_sha256'),
                    'embedding_model_id': r.get('embedding_model_id'),
                    'status': r.get('status'),
                    # Carry the AI-mention rate-limit suppress flag
                    # so MemoryRecallReranker._hard_filter can drop
                    # suppressed reflections from the rerank pool —
                    # reflections share persona's 5h-window mention
                    # gating (see ReflectionEngine._normalize_reflection).
                    # Codex PR-958 P2: without this, a vector-recall
                    # path with a suppressed reflection would slip
                    # past the filter and re-enter Stage-2 signal
                    # detection, defeating the suppression contract.
                    'suppress': r.get('suppress'),
                    'subject_kind': r.get('subject_kind'),
                    'subject_id': r.get('subject_id'),
                    'scope': r.get('scope'),
                })

        if persona_manager is not None:
            persona = await persona_manager.aensure_persona(lanlan_name)
            for entity_key, section in persona.items():
                if not isinstance(section, dict):
                    continue
                for entry in section.get('facts', []):
                    if not isinstance(entry, dict):
                        continue
                    if entry.get('protected'):
                        # protected = character_card；evidence 对它永远
                        # inf，signal 施加它也没语义。跳过。
                        continue
                    pool.append({
                        'id': f"persona.{entity_key}.{entry.get('id', '')}",
                        'raw_id': entry.get('id', ''),
                        'target_type': 'persona',
                        'entity_key': entity_key,
                        'text': entry.get('text', ''),
                        'entity': entity_key,
                        'score': evidence_score(entry, now),
                        'embedding': entry.get('embedding'),
                        'embedding_text_sha256': entry.get('embedding_text_sha256'),
                        'embedding_model_id': entry.get('embedding_model_id'),
                        'suppress': entry.get('suppress'),
                        'subject_kind': entry.get('subject_kind'),
                        'subject_id': entry.get('subject_id'),
                        'scope': entry.get('scope'),
                    })

        # Keep Stage-2 evidence inside the same subject boundary as the facts
        # that triggered it. Legacy facts can only see legacy observations;
        # scoped facts can only see explicitly matching scoped observations.
        from memory.scopes import (
            filter_entries_for_subjects,
            subject_from_entry,
        )
        trigger_subjects = []
        include_legacy_private = not new_facts
        for fact in new_facts or []:
            trigger_subject = subject_from_entry(fact)
            if trigger_subject is None:
                include_legacy_private = True
            else:
                trigger_subjects.append(trigger_subject)
        pool = filter_entries_for_subjects(
            pool,
            trigger_subjects or None,
            include_legacy_private=include_legacy_private,
        )

        # P2 step 3: route through MemoryRecallReranker whenever we have
        # a query, regardless of vector service state.  The reranker
        # owns the unified pipeline:
        #
        #   _hard_filter (drops suppressed / terminal / score<0 /
        #     protected) → coarse rank (cosine top-K when vectors are
        #     ready, evidence_score order otherwise) → optional LLM
        #     rerank (skipped automatically when vectors aren't
        #     available).
        #
        # An earlier version gated the call on
        # `reranker._service.is_available()` and fell through to a
        # bare `pool.sort(score)` when the service was INIT / LOADING /
        # DISABLED.  That meant `suppress=True` rows leaked into
        # Stage-2 whenever vectors weren't ready, since the bare sort
        # path didn't apply `_hard_filter` (CodeRabbit PR-956 Major).
        # Behaviour now stays stable across the warmup window.
        if new_facts:
            try:
                from memory.recall import MemoryRecallReranker
                reranker = MemoryRecallReranker()
                query_texts = [
                    f.get('text', '') for f in new_facts if f.get('text')
                ]
                return await reranker.aretrieve_candidates(
                    pool, query_texts,
                    budget=EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS,
                    config_manager=self._config_manager,
                )
            except Exception as e:
                logger.warning(
                    "[FactStore] vector+LLM rerank failed (%s: %s); "
                    "falling back to evidence_score order",
                    type(e).__name__, e,
                )

        # Fallback / legacy path: top-N by score DESC (most relevant
        # first). Reached when (a) ``new_facts`` is empty (no recall
        # query to drive the reranker), or (b) the reranker raised
        # mid-call.  This matches the pre-P2 behaviour exactly —
        # `_hard_filter` is intentionally NOT applied here because
        # the upstream consumers in the no-new_facts shape (some
        # idle-maintenance entry points) already operate on the
        # unfiltered pool.  CodeRabbit PR-956's Major was specifically
        # about the new_facts branch above silently bypassing the
        # filter when vectors weren't ready.
        pool.sort(key=lambda o: o.get('score', 0.0), reverse=True)
        return pool[:EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS]

    async def _allm_detect_signals(
        self, lanlan_name: str, new_facts: list[dict],
        existing_observations: list[dict],
    ) -> list[dict] | None:
        """Stage-2: map new facts onto existing observations with
        reinforces/negates signals. Returns validated signals (target_ids
        already filtered against existing_observations), or None on
        terminal failure.

        The cap on new_facts is enforced by the caller in
        ``aextract_facts_and_detect_signals`` via
        ``EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS`` (drain mode: the overflow
        stays signal_processed=False, handled on the next idle)."""
        if not new_facts or not existing_observations:
            return []

        # Build prompt sections.
        # 关键：先按预算累计构造 budgeted_observations 子集，prompt 和后面
        # 的 valid_ids / id_to_obs 都从同一个子集构造。否则总量截断把尾部
        # observation 砍掉后，valid_ids 还来自全集，LLM 可能 hallucinate
        # 一个被截掉的 id 通过校验落到错误条目（CodeRabbit fingerprint
        # e625b666 抓到的 race）。
        from config import (
            EVIDENCE_PER_OBSERVATION_MAX_TOKENS,
            EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS,
        )
        from utils.tokenize import truncate_to_tokens, count_tokens
        new_facts_text = "\n".join(
            f"[{f.get('id', '')}] {truncate_to_tokens(f.get('text', '') or '', EVIDENCE_PER_OBSERVATION_MAX_TOKENS)}"
            for f in new_facts
        )
        # 累计 token 直到撞到总量上限，超过的尾部 obs 直接丢出本次 prompt。
        budgeted_observations: list[tuple[dict, str]] = []  # (obs, formatted_line)
        running = 0
        for o in existing_observations:
            line = (
                f"[{o['id']}] "
                f"{truncate_to_tokens(o.get('text', '') or '', EVIDENCE_PER_OBSERVATION_MAX_TOKENS)}"
            )
            line_tokens = count_tokens(line) + 1  # +1 ≈ 一个换行符
            if budgeted_observations and running + line_tokens > EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS:
                # 至少保留一条；超过总量后丢尾部
                break
            budgeted_observations.append((o, line))
            running += line_tokens
        if not budgeted_observations:
            return []
        obs_text = "\n".join(line for _, line in budgeted_observations)
        prompt_lang = detect_prompt_language(
            f"{new_facts_text}\n{obs_text}",
            ui_language=get_global_language_full(),
        )
        prompt = get_signal_detection_prompt(prompt_lang) \
            .replace('{NEW_FACTS}', new_facts_text) \
            .replace('{EXISTING_OBSERVATIONS}', obs_text) \
            .replace('{LANLAN_NAME}', lanlan_name)

        # Phase D：Stage-2 signal detection 开 thinking——
        # 任务是 new_fact × existing_observation 的关系判断 + target_id 选择，
        # 现有 [memory/facts.py:670-708](memory/facts.py:670) 防御代码本身就是
        # 在补 LLM 幻觉，思考能减少 target_id 错位。完全后台 (signal extraction
        # loop)，无人等。timeout 拉到 90s 给 thinking 模型留余量。
        parsed = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_DETECT_SIGNALS_MODEL_TIER,
            call_type="memory_signal_detection",
            timeout=90,
            extra_body=None,
        )
        if parsed is None:
            return None
        if not isinstance(parsed, dict):
            logger.warning(
                f"[FactStore] {lanlan_name}: Stage-2 返回非 dict "
                f"{type(parsed).__name__}，丢弃"
            )
            return []
        raw_signals = parsed.get('signals', [])
        if not isinstance(raw_signals, list):
            return []

        # Defensive: drop hallucinated target_ids (§3.4.8). 校验池**必须**和
        # prompt 看到的子集一致，否则被尾部预算切掉的 obs id 仍会被当成合法。
        valid_ids = {o['id'] for o, _ in budgeted_observations}
        id_to_obs = {o['id']: o for o, _ in budgeted_observations}
        # source_fact_id 也要校验在本批 new_facts 里（CodeRabbit 1f follow-up）。
        # 否则 LLM hallucinate 一个不在本次 prompt 里的 fact id 仍会被作为合法
        # source 落到 evidence 计数器更新里。
        new_fact_ids = {f['id'] for f in new_facts if f.get('id')}
        validated: list[dict] = []
        # 单次 Stage-2 调用可能返回 N 条 signal 都对同一 reflection 报告
        # target_type 不一致——LLM 在猜命名规范（"persona.relationship"
        # vs "persona" vs "persona.relationship.prom"），看到啥前缀就抄啥。
        # 兜底逻辑一直按设计在跑，但每条一行 log 会刷屏；按 (LLM值→实际值)
        # 去重计数，循环结束后一行汇总，方便看出"哪种猜法在被反复纠"。
        target_type_fixes: dict[tuple[str | None, str], int] = {}
        for s in raw_signals:
            if not isinstance(s, dict):
                continue
            tid = s.get('target_id')
            ttype = s.get('target_type')
            signal = s.get('signal')
            if signal not in ('reinforces', 'negates'):
                continue
            sid = s.get('source_fact_id')
            if sid is not None and sid not in new_fact_ids:
                logger.warning(
                    f"[FactStore] {lanlan_name}: Stage-2 返回 source_fact_id="
                    f"{sid!r} 不在本批 new_facts 里，丢弃"
                )
                continue
            # Reconstruct full prompt-space id if LLM returned just the raw id
            candidate_full = tid
            if tid not in valid_ids:
                # Try prefixing (LLM sometimes returns just "r_xxx" instead of
                # "reflection.r_xxx"). Match by endswith on prompt ids.
                candidates = [vid for vid in valid_ids if vid.endswith(f".{tid}")]
                if len(candidates) == 1:
                    candidate_full = candidates[0]
                else:
                    logger.warning(
                        f"[FactStore] {lanlan_name}: Stage-2 返回未知 "
                        f"target_id={tid}，丢弃"
                    )
                    continue
            obs = id_to_obs[candidate_full]
            if obs['target_type'] != ttype:
                # LLM 说的 target_type 与实际不符 → 以实际为准（修正）。
                # 不在 loop 里 log，循环结束统一汇总输出。
                # ttype 来自 LLM JSON，理论上是 str/None；hallucinate 成
                # list/dict 时直接进 dict key 会 TypeError 把整个 Stage-2 拖崩
                # （codex review #1414）。用 repr 把非 hashable 值兜成 str。
                key_ttype = ttype if ttype is None or isinstance(ttype, str) else repr(ttype)
                target_type_fixes[(key_ttype, obs['target_type'])] = (
                    target_type_fixes.get((key_ttype, obs['target_type']), 0) + 1
                )
            validated.append({
                'source_fact_id': s.get('source_fact_id'),
                'target_type': obs['target_type'],
                'target_id': obs['raw_id'],
                'target_full_id': candidate_full,
                'entity_key': obs.get('entity_key'),   # 只 persona 有
                'signal': signal,
                'reason': s.get('reason', ''),
            })
        if target_type_fixes:
            summary = ", ".join(
                f"{src!r}→{dst}×{n}" if n > 1 else f"{src!r}→{dst}"
                for (src, dst), n in sorted(
                    target_type_fixes.items(), key=lambda kv: -kv[1]
                )
            )
            logger.info(
                f"[FactStore] {lanlan_name}: Stage-2 target_type 修正 {summary}"
            )
        return validated

    async def aextract_facts_and_detect_signals(
        self, lanlan_name: str, messages: list,
        reflection_engine=None, persona_manager=None,
    ) -> tuple[list[dict], list[dict], list[str]]:
        """Two-stage extraction (RFC §3.4.2) with drain semantics.

        Stage-1: pure fact extraction from user messages — no existing
        observations in prompt to avoid self-cycling.
        Stage-2: new_facts × existing_observations → reinforces/negates
        signals (with defensive target_id validation).

        Drain (PR #976):
        - Facts extracted in Stage-1 are persisted with ``signal_processed=False``
        - Stage-2 pulls **all** facts with signal_processed=False (not just this
          round's new ones, also the unfinished tail of previous rounds), takes
          the top N (=EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS) by importance DESC
          into Stage-2, and leaves the rest untouched for the next idle tick to
          drain

        Returns ``(new_facts_this_round, signals, batch_fact_ids)``:
        - ``new_facts_this_round``: facts newly extracted + persisted by this
          round's Stage-1 (for outbox and other audit purposes)
        - ``signals``: evidence signals awaiting dispatch
        - ``batch_fact_ids``: fact ids successfully processed by this Stage-2
          round. The drain queue only ever carries legacy-private facts —
          scoped (group/member) facts take the simplified pipeline and are
          written with ``signal_processed=True``; any stray non-legacy row is
          defensively dequeued before batching. The caller
          must call ``amark_signal_processed(lanlan_name, batch_fact_ids)`` only
          after every returned signal has been applied successfully via
          aapply_signal. If dispatch fails, the next idle sees those facts still
          at signal_processed=False and retries them (CodeRabbit fingerprint
          c755101c).

        Failure semantics (§3.4.2, last paragraph):
        - Stage-1 failure → abort, no fact written; caller retries later
        - Stage-2 LLM failure → batch_fact_ids still returns []; the caller
          won't mark, and the next idle retries the same batch
        - dispatch failure (caller side) → the caller doesn't call amark;
          retried next round
        """
        extracted = await self._allm_extract_facts(lanlan_name, messages)
        if extracted is None:
            # Stage-1 terminal failure — caller MUST NOT advance cursor
            # (§3.4.3 "Stage-1 失败 → 整次 abort，... 下次 idle 触发再试")
            raise FactExtractionFailed(
                f"Stage-1 LLM call exhausted retries for {lanlan_name!r}"
            )
        if not extracted:
            extracted = []

        # Persist 本轮新抽到的 facts（带 signal_processed=False 入库）。
        # 即使本轮抽到 0 条，下面仍要 drain 上轮没处理完的 unprocessed 尾部。
        persisted_this_round = await self._apersist_new_facts(lanlan_name, extracted)

        # Drain：拉所有 signal_processed=False 的 facts（含历史尾部 + 本轮新增）。
        # 老 facts 没这个字段时 default=True，避免升级后把几个月历史 fact
        # 一起重跑 Stage-2。
        #
        # ⚠️ Source filter：排除 source='ai_disclosure'——AI 自我披露的 fact
        # 不进 evidence loop（防自我强化死循环：AI 说"我喜欢 X" → 抽出 → Stage-2
        # 给 reflection "neko likes X" 涨分 → AI 更频繁说"我喜欢 X" → ...）。
        # path B 写盘时已经把 signal_processed 置 True 兜底（_apersist_new_facts），
        # 此处 source filter 做双重防御，防新加路径忘了置 signal_processed。
        # 老 fact 缺 source 字段时按 'user_observation' 回退（向后兼容）。
        all_facts = await self.aload_facts(lanlan_name)
        unprocessed = [
            f for f in all_facts
            if not f.get('signal_processed', True)
            and f.get('source', self._SOURCE_DEFAULT) != 'ai_disclosure'
        ]

        # Stage-2 evidence 只属于 legacy 私聊管线。scoped（群/成员）fact 走
        # 简化管线，写盘时就 signal_processed=True 不会出现在这里；万一出现
        # （subject 元数据损坏、旧版本代码写入的滞留数据），直接标记出队——
        # 这类 fact 没有对应的 evidence 观察池，留在队列里会永久占用 batch
        # 名额，把 legacy 主链路饿死（每 tick 空转还烧额外 LLM 调用）。
        from memory.scopes import is_legacy_private_entry

        stray = [f for f in unprocessed if not is_legacy_private_entry(f)]
        if stray:
            # 无条件把非 legacy 行滤出批次——包括没有 id、无法标记的行：
            # 它们绝不能混进纯 legacy 的 Stage-2 prompt（跨边界泄漏）。
            unprocessed = [
                f for f in unprocessed if is_legacy_private_entry(f)
            ]
            stray_ids = [f['id'] for f in stray if f.get('id')]
            logger.info(
                "[FactStore] %s: Stage-2 出队 %d 条非 legacy fact"
                "（scoped 走简化管线 / subject 损坏防御；无 id 仅过滤不标记 %d 条）",
                lanlan_name, len(stray), len(stray) - len(stray_ids),
            )
            if stray_ids:
                await self.amark_signal_processed(lanlan_name, stray_ids)
        if not unprocessed:
            return persisted_this_round, [], []

        # 按 importance DESC + 创建时间 ASC 排序，取前 N 条做这一批 batch。
        # 多余的留 signal_processed=False 给下一轮 idle tick。
        unprocessed.sort(
            key=lambda f: (
                -safe_importance(f),
                str(f.get('created_at') or ''),
            ),
        )
        batch = unprocessed[:EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS]

        # 此时 batch 只含 legacy 私聊 facts（scoped 已在上面防御性出队），
        # 保持与分区机制引入前一致的单批次 Stage-2。_aload_signal_targets
        # 内部的 scope 过滤会把观察池收敛到 legacy（defense in depth：即使
        # 未来有 scoped 观察写入 reflections/persona，也不会漏进这里）。
        try:
            existing_observations = await self._aload_signal_targets(
                lanlan_name,
                reflection_engine=reflection_engine,
                persona_manager=persona_manager,
                new_facts=batch,
            )
        except Exception as e:
            logger.warning(
                f"[FactStore] {lanlan_name}: _aload_signal_targets 失败，"
                f"跳过本轮 Stage-2（batch 保持未处理下轮重试）: {e}"
            )
            return persisted_this_round, [], []
        if not existing_observations:
            # 冷启动：还没有任何 confirmed reflection / persona 观察目标。
            # 不 mark，下轮重试（与引入 scope 之前的语义一致）。
            return persisted_this_round, [], []

        signals = await self._allm_detect_signals(
            lanlan_name, batch, existing_observations,
        )
        if signals is None:
            # Stage-2 LLM failure: 不返回 ids，caller 不 mark，下轮重试同批。
            return persisted_this_round, [], []

        # caller 仍需在所有返回 signals dispatch 成功后才 mark。
        return persisted_this_round, signals, [
            fact['id'] for fact in batch if fact.get('id')
        ]

    async def extract_facts(
        self,
        messages: list,
        lanlan_name: str,
        *,
        subject: MemorySubject | dict | None = None,
        fail_closed: bool = False,
        speaker_label: str | None = None,
        speaker_provenance: dict | None = None,
    ) -> list[dict]:
        """Stage-1-only backward-compat entry.

        Kept for callers that predate the evidence mechanism
        (memory_server's _run_post_turn_signals OFF-mode fallback transitively
        calls this, plus outbox replay). Emits only new facts and skips
        signal detection — downstream `_periodic_signal_extraction_loop`
        runs Stage-1+Stage-2 together.

        Unlike the Stage-1+2 entry, a Stage-1 terminal failure is swallowed
        here by default (returns []): the legacy per-turn call site treats
        extraction as best-effort — the next turn / the background loop will
        retry over durably stored history.

        ``fail_closed=True`` raises :class:`FactExtractionFailed` on terminal
        failure (retries exhausted or malformed payload) instead. The scoped
        history route needs the distinction: its callers checkpoint volatile
        caller-owned buffers on success, so a swallowed failure would
        permanently drop the batch (Codex P1). A genuine empty extraction
        still returns [].

        ``speaker_label`` is forwarded to the extraction prompt — see
        :meth:`_allm_extract_facts`.

        ``speaker_provenance``: stamped onto每条新建 fact（speaker_label /
        speaker_trust，信赖度阶段一只落字段）。与 ``speaker_label`` 分开传：
        后者影响 prompt 渲染，且群 digest 路由会为它填集体描述符缺省值——
        那种"无单一发言人"的调用不该在 fact 上落 provenance，由调用方
        决定是否给本参数。
        """  # noqa: DOCSTRING_CJK
        extracted = await self._allm_extract_facts(
            lanlan_name, messages,
            treat_malformed_as_failure=fail_closed,
            speaker_label=speaker_label,
        )
        if extracted is None and fail_closed:
            raise FactExtractionFailed(
                f"Stage-1 LLM call failed for {lanlan_name!r} (fail_closed caller)"
            )
        if fail_closed and isinstance(extracted, list) and extracted:
            # 任一畸形元素都判整批可重试失败：persist 会静默跳过畸形项，
            # 调用方推进游标后该元素承载的内容永久丢失；重试换一次完整
            # 提取，去重机制兜住有效项的重复（对齐 daily import 语义）。
            malformed = [
                f for f in extracted
                if not (
                    isinstance(f, dict)
                    and isinstance(f.get('text'), str)
                    and f['text'].strip()
                )
            ]
            if malformed:
                raise FactExtractionFailed(
                    f"Stage-1 returned {len(malformed)}/{len(extracted)} "
                    f"malformed fact entries for {lanlan_name!r} "
                    f"(fail_closed caller)"
                )
        if not extracted:
            return []
        return await self._apersist_new_facts(
            lanlan_name, extracted, subject=subject,
            speaker_provenance=speaker_provenance,
        )

    # ── external import state (sidecar) ──────────────────────────────

    def _external_import_state_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'external_import_state.json',
        )

    def _load_external_import_state(self, name: str) -> dict:
        """Best-effort read of the per-character external-import sidecar.

        Missing / corrupt / non-dict payloads degrade to ``{}``: the worst
        case is re-extracting already-imported days (wasted LLM calls), never
        data loss, so this read must not fail an import.
        """
        path = self._external_import_state_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                # UnicodeDecodeError（非法 UTF-8 字节，ValueError 子类，非
                # JSONDecodeError/OSError）也要降级：_acollect_day_fp_sources 在
                # per-day 隔离**之前**跑，一个损坏 sidecar 冒泡会 abort 整个导入，
                # 违背 docstring 承诺的「降级空集」（Codex P2）。
                logger.warning(
                    f"[FactStore] {name}: 读取 external_import_state 失败，降级为空: {e}"
                )
        return {}

    @staticmethod
    def _state_daily_fingerprints(state: dict) -> set[str]:
        """Processed-day fingerprint set held by the sidecar state dict."""
        daily = state.get('daily')
        fps = daily.get('imported_day_fingerprints') if isinstance(daily, dict) else None
        if not isinstance(fps, list):
            return set()
        return {str(x) for x in fps if x}

    @staticmethod
    def _facts_have_day_fingerprint(facts: list[dict], fingerprint: str) -> bool:
        """Whether any fact's ``external_import`` provenance carries this day's
        fingerprint — i.e. the day IS carried by a fact and must NOT get a
        sidecar entry.

        Covers both a newly appended fact and an existing fact whose provenance
        was upgraded in place (e.g. ai_disclosure→user_observation on a same-day
        exact-hash hit): both stamp ``day_fingerprint``, both ride facts.json's
        rollback lifecycle and self-heal, so a sidecar entry for such a day
        would outlive a rollback and suppress the re-extraction (Codex P2)."""
        for f in facts:
            meta = f.get('external_import')
            if isinstance(meta, dict) and str(meta.get('day_fingerprint') or '') == fingerprint:
                return True
        return False

    async def _acollect_day_fp_sources(self, name: str) -> tuple[set[str], set[str]]:
        """The two idempotency carriers, returned separately as
        ``(sidecar_fps, provenance_fps)``.

        provenance_fps scans active + archive (``aload_facts_full``) so a day
        whose facts were archived still counts as carried. Callers union the two
        for the skip filter, and diff them (``sidecar ∩ provenance``) to detect
        stale sidecar entries a fact now carries — see the up-front self-heal in
        ``aimport_external_daily``."""
        state = await asyncio.to_thread(self._load_external_import_state, name)
        sidecar_fps = self._state_daily_fingerprints(state)
        provenance_fps: set[str] = set()
        for f in await self.aload_facts_full(name):
            meta = f.get('external_import')
            if isinstance(meta, dict) and meta.get('day_fingerprint'):
                provenance_fps.add(str(meta['day_fingerprint']))
        return sidecar_fps, provenance_fps

    async def _aload_imported_day_fps(self, name: str) -> set[str]:
        """Union of processed-day fingerprints from two carriers, split by
        whether the day persisted any fact:

        - **Fact provenance** (``external_import.day_fingerprint`` inside
          facts.json / facts_archive.json) is authoritative for days that
          persisted at least one fact. It rides the fact data's own cloudsave
          sync / restore / rollback lifecycle, so a rolled-back day loses its
          fingerprint together with its facts and re-imports self-heal —
          exactly #2383's behavior. Days-with-facts therefore do **not** get a
          sidecar entry (see ``_arecord_unpersisted_day_fp``): a sidecar record
          that outlived a facts.json rollback would permanently suppress the
          re-extraction that restores the day.
        - **Sidecar** (``external_import_state.json``) carries **only** days the
          LLM judged to hold no fact at all (empty extraction). A day whose
          extracted facts were *all deduped away* is NOT recorded here — its
          content is already carried by the deduping facts (under a different
          fingerprint, so a rollback of those facts couldn't self-heal a sidecar
          entry), so it re-extracts instead (added=0, no data lost — chosen over
          a fact-carried multi-fingerprint scheme). Re-extracting a truly
          fact-less day is harmless, so a stale / desynced sidecar never loses
          data (Codex P2 follow-up).

        Provenance is read over active + archive (``aload_facts_full``) so a day
        whose facts were archived by ``_archive_absorbed`` (>7 days old) still
        skips instead of re-extracting. The production skip filter computes this
        same union itself from ``_acollect_day_fp_sources`` (it also needs the
        ``sidecar ∩ provenance`` intersection for the up-front self-heal), so
        this wrapper is the read-side contract exercised by the sidecar
        degradation tests. The persist-time concurrent re-check reads active
        provenance directly (a rival import that just persisted writes the
        active list, and a sidecar-only rival must not suppress this request's
        real facts).
        """
        sidecar_fps, provenance_fps = await self._acollect_day_fp_sources(name)
        return sidecar_fps | provenance_fps

    async def _arecord_unpersisted_day_fp(self, name: str, fingerprint: str) -> None:
        """Best-effort: record a day's fingerprint in the sidecar **iff no fact
        carries it**.

        Called only for days the LLM judged fact-less (empty extraction); a day
        whose extracted facts all deduped away re-extracts instead of being
        sidecar-recorded (see ``_aload_imported_day_fps``). The
        active-provenance re-check runs **inside** the persist alock (the same
        lock as fact persistence): a concurrent same-character import that
        persists this day's facts either lands before us — we see its provenance
        and skip the sidecar — or after us, blocked on the lock. So a day that
        ends up with a fact carrier never also gets a sidecar entry (which would
        outlive a facts.json rollback and suppress the self-healing
        re-extraction, Codex P2). No TOCTOU; the same critical section also
        absorbs the sidecar's own read-modify-write.

        Best-effort by design: the sidecar is a pure idempotency accelerator.
        A write failure (disk full / permission; the cloudsave fence is already
        rejected at the import entrypoint) degrades to re-extracting this day on
        the next import — the exact #2383-era cost — so it must never escalate
        a day that otherwise succeeded into a failed_day / HTTP 500. Fact
        persistence keeps its own hard ``assert_cloudsave_writable`` semantics;
        only this accelerator layer is soft.
        """
        try:
            async with self._get_persist_alock(name):
                active = await self.aload_facts(name)
                if self._facts_have_day_fingerprint(active, fingerprint):
                    return
                await asyncio.to_thread(
                    self._record_imported_day_fp_locked, name, fingerprint
                )
        except (MaintenanceModeError, OSError) as exc:
            logger.warning(
                f"[FactStore] {name}: sidecar 落盘失败（降级为下次重导重抽该天）: {exc}"
            )

    def _record_imported_day_fp_locked(self, name: str, fingerprint: str) -> None:
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/external_import_state.json",
        )
        state = self._load_external_import_state(name)
        fps = self._state_daily_fingerprints(state)
        if fingerprint in fps:
            return
        fps.add(fingerprint)
        state.setdefault('version', 1)
        daily = state.get('daily')
        if not isinstance(daily, dict):
            daily = {}
            state['daily'] = daily
        # 对偶 persona folded_fingerprints：集合语义、sorted 落盘（稳定可 diff）。
        daily['imported_day_fingerprints'] = sorted(fps)
        atomic_write_json(
            self._external_import_state_path(name), state,
            indent=2, ensure_ascii=False,
        )

    async def _aclear_day_fps(self, name: str, fingerprints: set[str]) -> None:
        """Best-effort: drop day fingerprints from the sidecar because a fact now
        carries them, or the day re-extracts — either way they must not linger as
        stale sidecar entries that a facts rollback couldn't self-heal (Codex P2).

        Fires the write only when the sidecar actually holds one of them. Used
        both for the up-front self-heal (``sidecar ∩ provenance``) and for the
        per-day clear after a day yields facts. Same per-character alock +
        best-effort contract as ``_arecord_unpersisted_day_fp``."""
        if not fingerprints:
            return
        try:
            async with self._get_persist_alock(name):
                await asyncio.to_thread(self._clear_day_fps_locked, name, fingerprints)
        except (MaintenanceModeError, OSError) as exc:
            logger.warning(
                f"[FactStore] {name}: sidecar 清理陈旧指纹失败（无害，下次导入再清）: {exc}"
            )

    def _clear_day_fps_locked(self, name: str, fingerprints: set[str]) -> None:
        path = self._external_import_state_path(name)
        if not os.path.exists(path):
            return  # 无 sidecar 文件 → 只一次 stat、不碰盘。
        state = self._load_external_import_state(name)
        fps = self._state_daily_fingerprints(state)
        to_drop = fps & fingerprints
        if not to_drop:
            return
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/external_import_state.json",
        )
        fps -= to_drop
        daily = state.get('daily')
        if not isinstance(daily, dict):
            return
        daily['imported_day_fingerprints'] = sorted(fps)
        atomic_write_json(
            self._external_import_state_path(name), state,
            indent=2, ensure_ascii=False,
        )

    async def aimport_external_daily(
        self, lanlan_name: str, candidates: list[dict], source_format: str,
        imported_at: str,
    ) -> dict:
        """LLM-extract facts from imported daily journals (Stage-1, no signals).

        Mirrors the persona side (``afuse_external_facts``): external daily files
        (``memory/`` or ``memories/YYYY-MM-DD.md``) are free-form journal prose,
        so rather than appending their raw fragments verbatim they are run through
        the conversation fact-extraction LLM. Candidates are grouped by source
        file (one file == one day); each day's fragments are joined into a single
        user turn and extracted independently so the day's ``event_date`` can be
        stamped onto every fact it yields. A day whose joined fragments exceed
        ``EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS`` is split into multiple
        extraction batches (``batch_daily_fragments``) rather than truncated —
        no journal tail is silently dropped (Greptile P1). Days run concurrently
        under ``EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY`` (a month of journals run
        sequentially would blow past the upstream 240s forwarding window);
        batches within a day run sequentially; persistence stays serialized by
        the per-character persist lock. Best-effort per day, atomic within a
        day: when any batch fails (None) or crashes the whole day persists
        nothing — no facts, no fingerprint — and is counted in ``failed_days``
        so the caller surfaces a retryable partial result; the retry re-extracts
        that day from scratch (a partially-persisted day would fingerprint-skip
        forever, Greptile P1).

        Idempotency mirrors persona ``folded_fingerprints`` via each day's
        content fingerprint, held by the fact carrier when there is one and by
        the sidecar only when there is none: days that persisted (or upgraded in
        place) a fact carry it in their ``external_import`` provenance (inside
        facts.json, so it shares the fact data's cloudsave lifecycle and
        self-heals on rollback); days the LLM judged fact-less (empty
        extraction) record it in the ``external_import_state.json`` sidecar,
        which would otherwise have no home and re-run the LLM + burn cap quota on
        every re-import (Codex P2 follow-up). A day whose extracted facts all
        deduped away is left to re-extract (its content is already carried by
        the deduping facts under a different fingerprint; recording a sidecar
        entry that a facts rollback couldn't self-heal was rejected). The read
        side unions provenance + sidecar (see ``_aload_imported_day_fps``). A
        re-imported day whose fragments are unchanged is skipped outright — zero
        LLM calls (Codex P2). Changed content re-extracts; near-identical
        re-extraction output is absorbed by the same-date FTS5 dedup in
        ``_apersist_new_facts``.

        After fingerprint filtering, more than ``EXTERNAL_IMPORT_DAILY_MAX_FILES``
        genuinely-new days raises ``ExternalMemoryImportTooLargeError`` — an
        unbounded workspace would mean hundreds of LLM calls and blow the 240s
        window even under bounded concurrency; the frontend guides splitting
        the import, and already-imported days keep skipping for free (Codex P2).

        Returns ``{'added': int, 'days': int, 'failed_days': int, 'skipped_days': int}``.
        """
        from memory.external_markdown_import import batch_daily_fragments
        from memory.persona.fusion import ExternalMemoryImportTooLargeError
        from utils.llm_client import convert_to_messages

        by_file: dict[str, list[dict]] = {}
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            by_file.setdefault(str(cand.get("source_file") or ""), []).append(cand)

        # 逐日指纹幂等（对偶 persona folded_fingerprints）：已导入且内容未变的
        # 天直接 skip，不进 LLM。双载体：有 fact 天靠 provenance（含 archive），无
        # fact 天靠 sidecar。
        sidecar_fps, provenance_fps = await self._acollect_day_fp_sources(lanlan_name)
        # skip 前自愈：sidecar 里凡 fact provenance 也持有的指纹 = 陈旧（这天已有
        # fact 载体、不该在 sidecar）→ 清掉。覆盖 exact-hash upgrade 残留、并发
        # sidecar-only 残留、以及成功天即时清理失败/进程中断——那次清理在 persist
        # 后、会被本处 skip 绕过而永不重跑（CodeRabbit + Codex）。
        stale_sidecar = sidecar_fps & provenance_fps
        if stale_sidecar:
            await self._aclear_day_fps(lanlan_name, stale_sidecar)
        imported_day_fps = sidecar_fps | provenance_fps

        day_dates = {
            source_file: next(
                (str(g["event_date"]) for g in group if g.get("event_date")), None
            )
            for source_file, group in by_file.items()
        }
        # 指纹掺 event_date：不同日期的重复例行日记（文本逐字相同）各自是新的
        # 一天，不能被对方的指纹 skip（Codex P2）——与 fact 去重键含日期同理。
        day_fps = {
            source_file: self._daily_fingerprint(
                [str(g.get("text") or "") for g in group],
                event_date=day_dates[source_file],
            )
            for source_file, group in by_file.items()
        }
        pending = {
            source_file: group
            for source_file, group in by_file.items()
            if day_fps[source_file] not in imported_day_fps
        }
        skipped_days = len(by_file) - len(pending)

        # 分批预计算 + cap 按「总抽取调用数」而非天数：单个超大日记文件能拆出
        # 几十批串行调用，len(pending) 拦不住它撞 240s 墙（Codex P2）。tiktoken
        # 编码是同步 CPU，offload 线程池。
        batches_by_file: dict[str, list[str]] = await asyncio.to_thread(
            lambda: {
                source_file: batch_daily_fragments(
                    [p for p in (str(g.get("text") or "").strip() for g in group) if p],
                    EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS,
                )
                for source_file, group in pending.items()
            }
        )
        total_batches = sum(len(b) for b in batches_by_file.values())
        if total_batches > EXTERNAL_IMPORT_DAILY_MAX_FILES:
            raise ExternalMemoryImportTooLargeError(
                f"daily import needs {total_batches} extraction calls across "
                f"{len(pending)} new journal days (cap {EXTERNAL_IMPORT_DAILY_MAX_FILES}); "
                "split the workspace"
            )

        llm_slots = asyncio.Semaphore(EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY)

        async def _extract_one_day(source_file: str, group: list[dict]) -> tuple[int, bool]:
            """One day's extraction+persist; returns (added, day_failed)."""
            event_date = day_dates[source_file]
            batches = batches_by_file[source_file]
            # 先抽完该天全部批次、**任一批失败则整天不落盘**：若早批先落盘（带
            # 全天指纹）而后批失败，重试会被指纹整天 skip、失败批内容永久丢失
            # （Greptile P1）。整天原子化后，失败天既无 fact 也无指纹，重试从头
            # 重抽；persist 自身崩溃同理由 gather 计入 failed_days 且无指纹残留。
            day_extracted: list[dict] = []
            for batch_text in batches:
                messages = convert_to_messages(
                    [{"role": "user", "content": batch_text}]
                )
                # 只有 LLM 往返占并发槽；写盘走 _apersist_new_facts 的 per-character
                # 锁自行互斥，放在槽外让别的日子的 LLM 调用尽早起跑。
                # 畸形非数组当失败天（可重试）：否则会被当空抽取天 checkpoint 进
                # sidecar、后续导入 skip LLM 而静默丢该天 facts（Codex P2）。
                async with llm_slots:
                    extracted = await self._allm_extract_facts(
                        lanlan_name, messages, treat_malformed_as_failure=True,
                    )
                if extracted is None:
                    logger.warning(
                        f"[FactStore] {lanlan_name}: 外部 daily 抽取 LLM 失败，"
                        f"放弃 {source_file}（整天重试重抽）"
                    )
                    return 0, True
                batch_facts = [f for f in extracted if isinstance(f, dict)]
                if extracted and not batch_facts:
                    # 数组非空但无 object 元素（如 ["Master likes tea"]）= schema 失败、
                    # 非确认空抽取——treat_malformed_as_failure 只挡「非数组」，挡不住
                    # 「数组套字符串」。当失败天可重试，否则被 checkpoint 成空抽取天、
                    # 后续导入 skip LLM 静默丢该天 facts（Codex P2）。
                    logger.warning(
                        f"[FactStore] {lanlan_name}: 外部 daily 抽取返回无 object 元素的"
                        f"数组，放弃 {source_file}（整天重试重抽）"
                    )
                    return 0, True
                day_extracted.extend(batch_facts)
            if not day_extracted:
                # 空抽取天：LLM 判该日无 fact，无 fact 载体存指纹，只能靠 sidecar，
                # 否则每次重导都重抽该天并占 cap 配额（Codex P2 follow-up）。
                # _arecord 锁内二次确认 active 无该天 provenance 才落（兜并发对方
                # 已 persist 真实 facts 的情况）；best-effort 写失败退回重抽、不升级
                # failed_day。
                await self._arecord_unpersisted_day_fp(lanlan_name, day_fps[source_file])
                return 0, False
            # 并发缩窗重查：两个同角色 commit 可能都在开头读过 imported_day_fps
            # 才各自跑 LLM；persist 前重读一次，若对方已把这天真实 facts 落盘则
            # 放弃本次写入（措辞不同的重复 facts 会绕过精确去重）。剩余极窄的
            # TOCTOU 窗口由同日期 FTS5 近似去重兜底；前端 in-flight 单飞锁已挡住
            # 单客户端的并发导入（Codex P2）。
            # 只认 active fact provenance、**不查 sidecar**：本请求已抽出非空
            # facts，不能被对方并发的 sidecar-only（空抽取/全去重、无 fact 载体）
            # 结果挤掉——那会让「无 fact 的 sidecar」压掉真实抽取的 facts（Codex）。
            active_now = await self.aload_facts(lanlan_name)
            if self._facts_have_day_fingerprint(active_now, day_fps[source_file]):
                logger.info(
                    f"[FactStore] {lanlan_name}: {source_file} 已被并发导入落盘，"
                    "放弃本次写入"
                )
                return 0, False
            for fact in day_extracted:
                # Stamp provenance; _apersist_new_facts_locked turns event_date
                # into event_start_at and tags the entry as external_import.
                # day_fingerprint 是重导幂等的依据（见 docstring）。
                fact["_external_import"] = {
                    "format": source_format,
                    "file": source_file,
                    "section": "daily",
                    "event_date": event_date,
                    "imported_at": imported_at,
                    "day_fingerprint": day_fps[source_file],
                }
            try:
                new_facts = await self._apersist_new_facts(
                    lanlan_name, day_extracted, semantic_dedup=True,
                )
            except Exception:
                # persist 失败（FTS/JSON 写错等）也要清该天 sidecar：本请求已抽出真实
                # facts（这天有内容），若并发空抽取先写下 sidecar，persist 失败后它会
                # 成为唯一载体、压制用户重试 skip 未变日记而永不落盘（Codex）。收窄非
                # 根除：对方空判定在本清理**之后**才落盘的序覆盖不到——失败天标识不
                # 持久化、无法跨请求围栏，与「任意后续导入 LLM 恰判空即 checkpoint」
                # 的既定接受面同构。失败天由 gather 计入 failed_days、重试从头重抽。
                await self._aclear_day_fps(lanlan_name, {day_fps[source_file]})
                raise
            # 抽出 fact 的天（成功落新 fact，或全去重命中既有）都清掉该天可能残留的
            # sidecar 指纹（并发对方空抽取先写下的）——这些天不该在 sidecar：
            #  - 成功/upgrade 天：指纹已在 fact provenance，与 facts 同处回滚单元、
            #    回滚后一起消失重导自愈；残留 sidecar 会在回滚后压制重抽 → 记忆丢失。
            #  - 全去重天：不记 sidecar，靠「每次重抽自愈」保 rollback-safe（既有 fact
            #    回滚后重抽会重新 append）；若被并发空抽取的 sidecar 挡住就破坏自愈。
            # 绝大多数天 sidecar 无此指纹 → 一次 stat 即返回（Codex）。
            await self._aclear_day_fps(lanlan_name, {day_fps[source_file]})
            return len(new_facts), False

        outcomes = await asyncio.gather(
            *(_extract_one_day(f, g) for f, g in pending.items()),
            return_exceptions=True,
        )
        added = 0
        failed_days = 0
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                failed_days += 1
                logger.error(
                    f"[FactStore] {lanlan_name}: 外部 daily 抽取单日崩溃，已跳过该日",
                    exc_info=outcome,
                )
                continue
            day_added, day_failed = outcome
            added += day_added
            if day_failed:
                failed_days += 1
        return {
            "added": added,
            "days": len(by_file),
            "failed_days": failed_days,
            "skipped_days": skipped_days,
        }

    @staticmethod
    def _daily_fingerprint(texts: list[str], *, event_date: str | None = None) -> str:
        """Whitespace/case-normalized, **order-preserving** fingerprint over one
        day's fragment texts, salted with the day's ``event_date``. Journals are
        narrative — reordering entries (e.g. "stopped medication" vs "started
        medication" swapped) changes meaning, so an edited order must re-extract
        instead of fingerprint-skipping (Greptile P1); and a routine journal
        repeated verbatim on a different date is a **new** day, not a duplicate
        (Codex P2). persona's ``_fusion_fingerprint`` stays sorted and unsalted
        by design: its candidates are an unordered, date-less set."""
        norm = [" ".join((t or "").casefold().split()) for t in texts]
        payload = f"{event_date or ''}\n" + "\n".join(norm)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def aextract_facts_with_known_pool(
        self,
        lanlan_name: str,
        messages: list,
        known_pool: list[dict],
        *,
        subject: MemorySubject | dict | None = None,
    ) -> list[dict] | None:
        """AI-aware Stage-1 (path B) extraction — input is the role-tagged full
        user+ai message set; the prompt embeds ``known_pool`` (facts path A
        already extracted in the same window) as a "do-not-repeat" list so the
        LLM dedupes proactively at the output layer.

        Differences from ``extract_facts``:
        - Uses the new prompt (``FACT_EXTRACTION_AI_AWARE_PROMPT``) instead of the basic one
        - The prompt adds a known-pool section + trust-tier guidance + a source-field output requirement
        - Persists with default_source='ai_disclosure' (a source explicitly emitted by the LLM still wins)
        - No Stage-2 (path B by design never enters the evidence loop)

        Returns:
            - ``None``: Stage-1 terminal failure — retries exhausted / LLM
              returned a non-array (e.g. wrapped as ``{"facts": [...]}``). The
              caller should keep the cursor so the next trigger retries the same
              window.
            - ``[]``: Stage-1 succeeded and the LLM judged the window to contain
              0 new facts (fully deduped). The caller may advance the cursor.
            - ``list[dict]``: succeeded with N new facts extracted and persisted.

            The None / [] distinction is critical: collapsing None into [] would
            make path B treat a failed window as "successfully extracted 0" on
            LLM transient failures / malformed payloads and advance the cursor,
            permanently skipping those messages (CodeRabbit / Codex P1 round-2 +
            Codex P1 round-9 on PR #1408).
        """
        extracted = await self._allm_extract_facts_with_known_pool(
            lanlan_name, messages, known_pool,
        )
        if extracted is None:
            return None
        if not extracted:
            return []
        return await self._apersist_new_facts(
            lanlan_name, extracted, default_source='ai_disclosure', subject=subject,
        )

    async def _allm_extract_facts_with_known_pool(
        self,
        lanlan_name: str,
        messages: list,
        known_pool: list[dict],
    ) -> list[dict] | None:
        """Stage-1 LLM call for path B: role-tagged conversation + known pool。

        Returns raw LLM-extracted list, or None on terminal failure
        (caller swallows in aextract_facts_with_known_pool)."""
        from config.prompts.prompts_memory import get_fact_extraction_ai_aware_prompt

        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        name_mapping['ai'] = lanlan_name
        conversation_text = self._format_conversation(messages, name_mapping)

        # Known pool 段渲染：按 importance DESC 排（最重要的在最前，给 LLM
        # 最强信号）。cap 已经在 caller 端做过，这里不重复。
        known_lines = []
        for f in known_pool:
            text = f.get('text', '') or ''
            if not text:
                continue
            imp = f.get('importance', 5)
            known_lines.append(f"- {text} (importance: {imp})")
        known_block = "\n".join(known_lines) if known_lines else "(none)"

        prompt = (
            get_fact_extraction_ai_aware_prompt(get_global_language_full())
            .replace('{CONVERSATION}', conversation_text)
            .replace('{KNOWN_POOL}', known_block)
            .replace('{LANLAN_NAME}', lanlan_name)
            .replace('{MASTER_NAME}', name_mapping.get('human', '主人'))
        )

        extracted = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
            call_type="memory_fact_extraction_ai_aware",
        )
        if extracted is None:
            return None
        if not isinstance(extracted, list):
            # 非数组 payload（如 `{"facts": [...]}` 包了一层、或 LLM 偶发瞎写）
            # 等同 Stage-1 terminal failure 处理——返 None 让 `_run_path_b`
            # 保留 cursor 下次 trigger 重试同窗口，而不是当成"成功 0 抽"推
            # cursor 永久 skip 这段消息（Codex P1 round-9 on PR #1408）。
            logger.warning(
                f"[FactStore] {lanlan_name}: path-B Stage-1 返回非数组 "
                f"{type(extracted).__name__}，按 terminal failure 处理 (cursor 不推进)"
            )
            return None
        return extracted

    # ── query helpers ────────────────────────────────────────────────

    def get_unabsorbed_facts(
        self,
        name: str,
        min_importance: int = 5,
        *,
        subject: MemorySubject | dict | None = None,
    ) -> list[dict]:
        """Get facts that haven't been consumed by a reflection yet."""
        facts = self.load_facts(name)
        return [
            f for f in facts
            if not f.get('absorbed')
            and f.get('importance', 0) >= min_importance
            and entry_matches_subject(f, subject)
        ]

    async def _rollback_uncommitted_facts(
        self, lanlan_name: str, new_facts: list, existing_hashes: set,
        upgraded_snapshots: list | None = None,
    ) -> None:
        """Undo in-memory effects of a batch that never reached disk.

        The fail-closed callers retry on error; if the cache and hash set
        keep the uncommitted rows, that retry deduplicates into an empty
        success and the caller advances a volatile cursor over facts that
        facts.json never received. Upgrades are left alone (field-level
        idempotent, redone by the retry)."""
        for entry, prev_source, prev_signal in (upgraded_snapshots or []):
            # in-place 升级同样要还原：留着的话重试会撞升级守卫（source
            # 已是 user_observation）→ upgraded_count=0 → 整轮跳过保存，
            # 调用方拿到"成功"推游标，磁盘上的 fact 却仍未被印证。
            entry['source'] = prev_source
            if prev_signal is None:
                entry.pop('signal_processed', None)
            else:
                entry['signal_processed'] = prev_signal
        added_ids = {
            nf.get('id') for nf in new_facts if isinstance(nf, dict)
        }
        added_hashes = {
            nf.get('hash') for nf in new_facts
            if isinstance(nf, dict) and nf.get('hash')
        }
        cache = self._facts.get(lanlan_name)
        if cache is not None:
            cache[:] = [
                f for f in cache
                if not (isinstance(f, dict) and f.get('id') in added_ids)
            ]
        for content_hash in added_hashes:
            existing_hashes.discard(content_hash)
        if self._time_indexed is not None:
            for fact_id in added_ids:
                if not fact_id:
                    continue
                try:
                    await self._time_indexed.adelete_fact_from_index(
                        lanlan_name, fact_id,
                    )
                except Exception:
                    logger.warning(
                        f"[FactStore] {lanlan_name}: 回滚 FTS 索引失败 "
                        f"({fact_id})"
                    )

    async def aget_unabsorbed_facts(
        self,
        name: str,
        min_importance: int = 5,
        *,
        subject: MemorySubject | dict | None = None,
    ) -> list[dict]:
        facts = await self.aload_facts(name)
        # load_facts preserves legacy/hand-edited non-dict rows; filter them
        # here too or one corrupted row keeps raising through every caller
        # (scoped synthesis re-enters this getter after its own guard).
        return [
            f for f in facts
            if isinstance(f, dict)
            and f.get('id')
            and not f.get('absorbed')
            # safe_importance：手改行的非数值 importance（如 "high"）在
            # 原生比较下 TypeError，会让该角色的 scoped 合成每 tick 全灭。
            and safe_importance(f, 0) >= min_importance
            and entry_matches_subject(f, subject)
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

    async def amark_absorbed(self, name: str, fact_ids: list[str]) -> None:
        await asyncio.to_thread(self.mark_absorbed, name, fact_ids)

    def mark_signal_processed(self, name: str, fact_ids: list[str]) -> None:
        """Mark facts as having gone through Stage-2 signal detection.

        Mirrors `mark_absorbed`'s shape so the drain loop in
        `aextract_facts_and_detect_signals` can checkpoint a batch after
        the LLM call returns. Old on-disk facts that lack the field are
        treated as already processed (default=True) at read time, so
        re-flipping them here is a no-op.
        """
        facts = self.load_facts(name)
        id_set = set(fact_ids)
        changed = False
        for f in facts:
            if f.get('id') in id_set and not f.get('signal_processed', False):
                f['signal_processed'] = True
                changed = True
        if changed:
            self.save_facts(name)

    async def amark_signal_processed(self, name: str, fact_ids: list[str]) -> None:
        await asyncio.to_thread(self.mark_signal_processed, name, fact_ids)

    def _recheck_mem(self) -> tuple[dict, threading.Lock]:
        """Return the recheck failure mirror and its guard, creating them once.

        Lazy rather than ``__init__``-only because several call sites build a
        ``FactStore`` through ``object.__new__``, which never runs ``__init__``;
        a plain instance attribute would make those instances raise
        ``AttributeError`` the moment the recheck path touches the mirror.
        Creation is serialised by a module-level lock — the thing that has to be
        protected here is precisely the window in which the instance has no lock
        of its own yet.
        """
        mem = self._recheck_attempts_mem
        guard = self._recheck_mem_guard
        if mem is not None and guard is not None:
            return mem, guard
        with _RECHECK_MEM_BOOTSTRAP:
            if self._recheck_attempts_mem is None:
                # 赋到实例上（不是 class 上）：镜像必须是 per-instance，否则会变成
                # 跨实例 / 跨 pytest 用例的单例。
                self._recheck_attempts_mem = {}
            if self._recheck_mem_guard is None:
                self._recheck_mem_guard = threading.Lock()
            return self._recheck_attempts_mem, self._recheck_mem_guard

    def _note_recheck_attempt(
        self, name: str, fid: str, *, base: dict | None,
    ) -> tuple[int, str]:
        """Record one recheck failure in the in-process mirror; returns (n, at).

        The mirror stores the failure timestamp as well as the count on
        purpose: ``cooldown_elapsed(None, …)`` returns True, so a count-only
        mirror would let the dead-letter self-heal branch re-admit the entry on
        the very next round — the breaker would look implemented and still
        never bite.

        On first entry for a fact the count resumes from the on-disk column so
        a restart does not reset the budget to zero.

        Concurrency contract: writers always replace an entry wholesale
        (``per_char[fid] = {...}``) instead of mutating its fields in place, so
        the lock-free reader in ``arecheck_one_legacy_fact`` can never observe a
        half-written entry. Keep it that way.

        No entry cap, unlike ``_abump_synth_backoff``'s 64: that map is keyed by
        content-derived synth keys (unbounded key space), this one by fact id
        (bounded by facts.json rows, which the archive sweep itself caps). A cap
        here would evict entries already frozen at MAX and un-freeze them.
        """
        stamp = datetime.now().isoformat()
        mem, guard = self._recheck_mem()
        with guard:
            per_char = mem.setdefault(name, {})
            prev = per_char.get(fid)
            if prev is not None:
                n = safe_int_field(prev, 'n') + 1
            else:
                n = (safe_int_field(base, 'recheck_attempts') if base else 0) + 1
            per_char[fid] = {"n": n, "at": stamp}
        return n, stamp

    def _clear_recheck_attempt(self, name: str, fid: str) -> None:
        """Drop a fact's failure record after it migrates successfully.

        Dual of ``_note_recheck_attempt``, mirroring
        ``ReflectionEngine._aclear_synth_backoff``: without it the mirror would
        only ever grow, and a migrated fact carrying a stale failure count is
        unexplainable state even though the schema filter already excludes it.
        """
        mem = self._recheck_attempts_mem
        if not mem:
            return
        _, guard = self._recheck_mem()
        with guard:
            mem.get(name, {}).pop(fid, None)

    def _recheck_budget_open(self, mem_entry: dict | None, fact: dict) -> bool:
        """Whether this legacy fact may still consume a recheck slot.

        The in-process mirror wins over the two on-disk columns. When
        facts.json cannot be written (read-only FS / permissions / maintenance
        mode) ``recheck_attempts`` stays 0 and ``last_recheck_attempt_at`` stays
        ``None`` on disk forever, and ``cooldown_elapsed(None, …)`` returns
        True — reading disk only would mean neither the breaker nor the
        self-heal gate ever bites, which is exactly the situation both were
        added for.

        ``safe_int_field`` rather than ``(… or 0)``: both columns come out of
        hand-editable JSON, and a dirty value like ``""`` / ``[]`` would raise
        inside the comparison and stall the whole drain loop.

        A mirror entry always carries both fields — ``_note_recheck_attempt``
        is its only writer and stamps ``at`` on every write — so this reads
        ``at`` straight, with no on-disk fallback that could never run.
        """
        from config import (
            MEMORY_RECHECK_MAX_ATTEMPTS,
            MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
        )
        from memory.temporal import cooldown_elapsed

        if mem_entry is not None:
            attempts = safe_int_field(mem_entry, 'n')
            last_at = mem_entry.get('at')
        else:
            attempts = safe_int_field(fact, 'recheck_attempts')
            last_at = fact.get('last_recheck_attempt_at')
        if attempts < MEMORY_RECHECK_MAX_ATTEMPTS:
            return True
        # 时间自愈：达上限的 entry 过 MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS 后放行
        # 一次 probe，让一次性写盘/网络故障恢复后自愈。
        return cooldown_elapsed(last_at, MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS)

    def _bump_fact_recheck_attempts(self, name: str, fid: str, reason: str) -> None:
        """Increment the given fact's recheck failure count.

        Once failures reach the ``MEMORY_RECHECK_MAX_ATTEMPTS`` cap, the
        candidates filter excludes the fact so the loop gives its slot to other
        v1 entries.

        The count lands in the in-process mirror first, then best-effort on
        disk. The old version only wrote facts.json — the very file whose
        failure it was supposed to count — so in the read-only FS / permission
        case named in ``arecheck_one_legacy_fact``'s comment the counter never
        moved (``save_facts`` even evicts the cache on failure, discarding the
        just-incremented value), and the same fact was re-judged, at the cost of
        one LLM call, every 30s forever.

        A failed persist logs at WARNING — "the mirror moved but the disk
        counter did not" has no other visible signal in production — except
        under maintenance mode, an expected write ban that the archive block in
        ``save_facts`` also keeps at debug.
        """
        target: dict | None = None
        try:
            for f in self.load_facts(name):
                if isinstance(f, dict) and f.get('id') == fid:
                    target = f
                    break
        except Exception as e:
            logger.debug(f"[Recheck-Fact] {name} {fid}: 读 facts 失败: {e}")
        if target is None and fid not in (self._recheck_attempts_mem or {}).get(name, {}):
            # fact 已经不在库里、镜像里也没有历史计数 → 无需记账。
            return
        # 1) 先更新进程内镜像（纯内存，不可能失败）。
        attempts, stamp = self._note_recheck_attempt(name, fid, base=target)
        if target is not None:
            # 2) 再尽力持久化。写失败只 WARN 不抛 —— 镜像已经生效，熔断不受影响；
            #    对齐 ReflectionEngine._asave_synth_backoff 的口径。
            target['recheck_attempts'] = attempts
            # 戳失败时刻供 dead-letter 时间自愈（cooldown_elapsed）
            target['last_recheck_attempt_at'] = stamp
            try:
                self.save_facts(name)
            except MaintenanceModeError as e:
                # 维护态是预期的写禁止（save_facts 顶部的闸），不是故障。对齐
                # save_facts 里的归档分支：那里也把维护态从 WARNING 降到 debug，
                # 否则维护期间每一次重判失败都在日志里报一条"落盘失败"。
                logger.debug(
                    f"[Recheck-Fact] {name} {fid}: 维护态跳过 recheck_attempts "
                    f"落盘（进程内镜像仍生效）: {e}"
                )
            except Exception as e:
                logger.warning(
                    f"[Recheck-Fact] {name} {fid}: recheck_attempts 落盘失败"
                    f"（进程内镜像仍生效，熔断不受影响）: {e}"
                )
        logger.debug(
            f"[Recheck-Fact] {name} {fid}: recheck_attempts → {attempts} ({reason})"
        )

    async def arecheck_one_legacy_fact(self, name: str) -> bool:
        """Schema v1 → v2 slow recheck (processes only 1 fact per call).

        Finds the character's oldest fact with schema_version < CURRENT (main
        facts.json only, archive shards excluded), asks the LLM to fill in
        event_when, resolves event_start_at / event_end_at against created_at
        and writes them back. Facts have no temporal_scope, so this is lighter
        than the reflection recheck.

        Returns: True when one fact was processed successfully; False when no
        candidate was found or processing failed.
        """
        from config.prompts.prompts_memory import MEMORY_RECHECK_FACT_PROMPT
        from memory.temporal import (
            normalize_event_when as _norm_when,
            compute_event_timestamps as _compute_ts,
        )

        facts = await self.aload_facts(name)
        # 无锁单次读：写侧整体替换条目 dict，读者不会看到半写状态
        # （契约见 _note_recheck_attempt）。
        recheck_mem = (self._recheck_attempts_mem or {}).get(name, {})
        candidates = [
            f for f in facts
            if (f.get('schema_version') or 1) < MEMORY_SCHEMA_VERSION_CURRENT
            # 重试预算：LLM 持续失败的 entry 累计达上限后不再阻塞队列
            # (Codex review on PR #1316 P2，对齐 reflection 同样写法)。
            and self._recheck_budget_open(
                recheck_mem.get(_readable_fact_id(f)), f,
            )
        ]
        if not candidates:
            return False
        candidates.sort(key=lambda f: (f.get('created_at', ''), f.get('id', '')))
        # Skip malformed candidates (missing id / created_at) instead of
        # aborting the whole call — otherwise a single bad legacy entry at
        # head of FIFO order would starve every later v1 fact forever
        # (Codex review on PR #1316 P2 catch).
        target: dict | None = None
        fid = ''
        created_at_iso = ''
        for c in candidates:
            cid = c.get('id')
            cts = c.get('created_at', '')
            if not cid or not cts:
                logger.debug(
                    f"[Recheck-Fact] {name}: skip malformed legacy fact "
                    f"(id={cid!r} created_at={cts!r})"
                )
                continue
            target = c
            fid = cid
            created_at_iso = cts
            break
        if target is None:
            return False

        prompt = MEMORY_RECHECK_FACT_PROMPT.format(
            FACT_TEXT=target.get('text', ''),
            CREATED_AT=created_at_iso,
        )

        failure_reason: str | None = None
        event_when_raw: dict | None = None
        try:
            from utils.llm_client import create_chat_llm_async
            set_call_type("memory_recheck_fact")
            api_config = await self._config_manager.aget_model_api_config('summary')
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
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # recheck prompt assembled from token-capped memory components.
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            result = robust_json_loads(raw)
            if not isinstance(result, dict):
                failure_reason = "non-dict response"
            else:
                event_when_raw = _norm_when(result.get('event_when'))
        except Exception as e:
            failure_reason = f"LLM call failed: {e}"

        # 失败路径统一收口：bump recheck_attempts，让连续失败的 entry 在达到
        # MAX 后被 candidates filter 排除（Codex review on PR #1316 P2，对齐
        # reflection 同样写法）。
        if failure_reason is not None:
            logger.debug(
                f"[Recheck-Fact] {name} {fid}: 跳过本轮 ({failure_reason})"
            )
            await asyncio.to_thread(self._bump_fact_recheck_attempts, name, fid, failure_reason)
            return False

        event_start_at, event_end_at = _compute_ts(
            event_when_raw,
            created_at_iso,
            fallback_start=True,
            fallback_end=False,
        )

        # 锁策略：和 mark_absorbed / mark_signal_processed (本文件 line 984
        # 附近) 一致——直接 mutate `load_facts` 返回的 cached list（CPython
        # 字段赋值是 atomic），不在外层套 _get_lock。save_facts 内部 (line 163)
        # 会自取 lock + read-merge-write 兜底并发安全。
        # 为什么不套外层锁：_get_lock 用 threading.Lock（非 reentrant），先
        # acquire 再调 save_facts 会自我死锁（Codex review on PR #1316 catch）。
        def _apply_update() -> bool:
            current = self.load_facts(name)
            found = None
            for f in current:
                if f.get('id') == fid:
                    found = f
                    break
            if found is None:
                return False
            if (found.get('schema_version') or 1) >= MEMORY_SCHEMA_VERSION_CURRENT:
                return False
            found['event_when_raw'] = event_when_raw
            found['event_start_at'] = event_start_at
            found['event_end_at'] = event_end_at
            found['schema_version'] = MEMORY_SCHEMA_VERSION_CURRENT
            self.save_facts(name)
            return True

        try:
            ok = await asyncio.to_thread(_apply_update)
        except Exception as e:
            logger.warning(f"[Recheck-Fact] {name} {fid}: save 失败: {e}")
            # 落盘失败也计入 recheck_attempts：否则 cloudsave 维护态 / 只读 FS /
            # 权限导致的持续写盘失败会让同一条 fact 每 30s 原样重判、熔断永不
            # 触发（对齐上面 LLM 失败路径的 bump）。
            await asyncio.to_thread(
                self._bump_fact_recheck_attempts, name, fid, f"save failed: {e}",
            )
            return False
        if ok:
            self._clear_recheck_attempt(name, fid)
            logger.info(
                f"[Recheck-Fact] {name} {fid}: v1→v{MEMORY_SCHEMA_VERSION_CURRENT} "
                f"when={event_when_raw}"
            )
        return ok
