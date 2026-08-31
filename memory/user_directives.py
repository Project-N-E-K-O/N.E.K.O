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
UserDirectivesManager — per-character store for explicit user ban-topic
directives ("别再提 X / stop saying X / その話はもう / ...").

Motivation
--------
The current-round LLM sees the user's original words; no intervention needed
here. But on the next session restart (archive-triggered / cold start /
reconnect), that message has long been wiped by ``compress_history`` and the
model steps on the landmine again. So extracted terms are persisted, and
``_build_initial_prompt`` splices a block into the system prompt tail at
startup.

Two things the startup injection alone does not cover, both wired on top of
this module:
- **Mid-session**: the system prompt is built once per session, so a directive
  recorded at turn N does not reach the prompt until the next rebuild (up to
  ~10 user turns away via hot-swap). ``mark_pending_injection`` /
  ``take_pending_injection`` hand the "there is something new" flag to
  ``main_logic`` (L4), which pushes the rendered block into the live session.
  This module cannot do that itself — ``memory`` (L3) may not import upward.
- **Proactive chat**: its Phase 2 prompt is assembled separately and never went
  through ``_build_initial_prompt``. ``get_active_terms`` feeds both its prompt
  section and its output-side drop gate.

Design notes
--------
- **Dispatch entry**: ``dispatch_user_utterance`` fan-out. This module
  self-registers via ``register_user_utterance_sink`` at import time, same
  style as ``plugin/core/state.py`` (dedup-on-identity; repeated registration
  doesn't re-fire).
- **Extraction**: ``config.prompts.prompts_directives.extract_directives``
  runs all locales in parallel (mixed Chinese/English speech is common); on a
  hit the term is cleaned by ``_trim_term``.
- **Dedup key**: ``(kind, term.casefold())``. Repeated hits → refresh
  ``last_seen_at`` / ``expire_at`` + ``hit_count += 1``; new entries get stored.
- **Storage**: ``memory/{name}/user_directives.json``. Schema: see ``_DEFAULT_FILE``.
  Capped at ``USER_DIRECTIVE_MAX_STORED`` rows; overflow rotates the
  least-recently-seen out (expired rows go first).
- **TTL**: each record's ``expire_at = last_seen_at + _effective_ttl(hit_count)``
  — the base window scaled by how many times the user has repeated it, capped
  at ``USER_DIRECTIVE_TTL_MAX_SECONDS``. Filtered on read; ``purge_expired``
  rewrites the file (optional; lazy is fine).
- **Prompt injection**: ``render_prompt_block(name, lang)`` returns the
  assembled string (with leading newline), "" when empty. Callers just do
  ``prompt += ...``.
- **Concurrency**: per-character ``threading.Lock``, pattern copied from
  ``memory/cursors.py``.

What is not extracted
----------
- Object-less "闭嘴/换话题/shut up": already in this round's context, the
  model sees it; persisting carries no concrete topic, and pushing such intent
  into the next round's prompt would backfire.
- Plain statements like "我不喜欢西瓜" ("I don't like watermelon"): not an
  explicit ban-topic directive; preference extraction belongs to the
  fact/persona pipeline.

False-positive policy
--------
The regex templates are lenient. Cost of a false kill = the user says an
equivalent sentence once more; cost of a miss = the user gets offended again —
so we lean toward over-killing. Terms are stored only when length ∈ [2, 40];
out-of-range ones are dropped.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional

from config import (
    USER_DIRECTIVE_MAX_ACTIVE,
    USER_DIRECTIVE_MAX_STORED,
    USER_DIRECTIVE_TTL_MAX_SECONDS,
    USER_DIRECTIVE_TTL_SECONDS,
)
from config.prompts.prompts_directives import (
    extract_directives,
    render_directives_block,
)
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


# 磁盘 schema 版本。改动 directive 字段语义时 bump，``_load_unlocked`` 拿来兼容旧文件。
_SCHEMA_VERSION = 1


def _now() -> float:
    return time.time()


def _default_payload() -> Dict[str, Any]:
    return {"version": _SCHEMA_VERSION, "directives": []}


# term 入库 / 出库共用的不变量：``str.strip()`` 后长度 ∈ [2, 40]。
# 读盘与 ``record()`` 写入两侧都走这条 helper，磁盘态始终干净——历史文件里
# 残留的过短 / 过长 / 非 str term 在下次 load 时被丢弃（CodeRabbit Minor）。
_TERM_MIN_LEN = 2
_TERM_MAX_LEN = 40


def _normalize_term(raw: Any) -> Optional[str]:
    """Normalize a term: ``str.strip()`` then enforce length ∈ [2, 40], else None."""
    if not isinstance(raw, str):
        return None
    term = raw.strip()
    if not (_TERM_MIN_LEN <= len(term) <= _TERM_MAX_LEN):
        return None
    return term


def _effective_ttl(hit_count: Any) -> float:
    """TTL for a directive repeated ``hit_count`` times, capped at the max.

    Linear in the repeat count: saying it once buys the base window, saying it
    again buys another one, and so on until the cap.
    """
    # 说一次可能是当下情绪，反复说是稳定偏好——但递增必须封顶，否则刷几十次
    # 就等于永久写进人设，而这份 list 今天还没有用户可见的删除入口。
    # ⚠️ 线性而不是指数：指数下 hit_count=5 就已经越过 30 天封顶，"说得越多
    # 记得越久"这层区分在前几次就塌成同一个值，等于没做。
    try:
        hits = int(hit_count)
    except (TypeError, ValueError):
        hits = 1
    hits = max(1, hits)
    return float(min(USER_DIRECTIVE_TTL_SECONDS * hits, USER_DIRECTIVE_TTL_MAX_SECONDS))


def _normalize_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalize one record read from disk into a dict; invalid / missing fields → None (dropped).

    Backward compat: early versions may have had only ``term`` and
    ``created_at`` — this backfills ``last_seen_at`` / ``expire_at`` /
    ``hit_count`` / ``kind`` / ``locale``.

    ⚠️ Fault tolerance: one dirty record (e.g. ``created_at: "abc"``) must not
    fail the whole file load and reset every valid directive. The whole
    function is wrapped in one try/except; this record returns None for the
    caller to drop while keeping the others (CodeRabbit Minor).
    """
    if not isinstance(raw, dict):
        return None
    try:
        term = _normalize_term(raw.get("term"))
        if term is None:
            return None
        kind = raw.get("kind") or "ban_topic"
        if not isinstance(kind, str):
            kind = "ban_topic"
        locale = raw.get("locale") if isinstance(raw.get("locale"), str) else "und"
        try:
            created_at = float(raw.get("created_at") or 0) or _now()
        except (TypeError, ValueError):
            created_at = _now()
        try:
            last_seen_at = float(raw.get("last_seen_at") or created_at)
        except (TypeError, ValueError):
            last_seen_at = created_at
        # ⚠️ hit_count 必须在 expire_at **之前**算出来：TTL 现在是 hit_count 的
        # 函数（_effective_ttl），回填历史文件时得拿到真实次数，否则一条被说过
        # 十次的指令读盘后按"说过一次"补 expire，续期长度当场退化。
        try:
            hit_count = int(raw.get("hit_count") or 1)
        except (TypeError, ValueError):
            hit_count = 1
        hit_count = max(1, hit_count)
        # 历史文件可能没写 expire_at；按 last_seen + 该条自己的 TTL 补
        try:
            expire_at = float(raw.get("expire_at") or 0) or (
                last_seen_at + _effective_ttl(hit_count)
            )
        except (TypeError, ValueError):
            expire_at = last_seen_at + _effective_ttl(hit_count)
        return {
            "term": term,
            "kind": kind,
            "locale": locale,
            "created_at": created_at,
            "last_seen_at": last_seen_at,
            "expire_at": expire_at,
            "hit_count": max(1, hit_count),
            "source": raw.get("source") or "regex",
        }
    except Exception:
        return None


class UserDirectivesManager:
    """Per-character ban-topic store (thread-safe).

    Usage:
        mgr = UserDirectivesManager()
        mgr.record_from_text(lanlan_name, raw_user_text)
        block = mgr.render_prompt_block(lanlan_name, lang='zh')
        # concat the block straight onto the system prompt tail

    A single process-wide instance ``_GLOBAL_MANAGER`` (see module tail); the
    sink is registered on it too.
    """

    def __init__(self) -> None:
        self._config_manager = get_config_manager()
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # 本进程内"哪些角色刚落盘了新指令、还没被注入当前会话"。
        # ⚠️ 只存角色名（一个待办标记），不存 term 列表：注入侧要的是**全量
        # 活跃列表**（与 _build_initial_prompt 注入的那段对偶），拿 render_
        # prompt_block 现渲染就行。存 term 的话既要处理去重与上限，又会跟
        # 全量渲染的语义打架（"注入的到底是新 term 还是所有 term"）。
        self._pending_injection: set[str] = set()
        self._pending_guard = threading.Lock()

    # ── path / lock ───────────────────────────────────────────

    def _file_path(self, name: str) -> str:
        # 延迟 import 避开 memory/__init__.py 循环依赖（同 cursors.py 风格）
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            "user_directives.json",
        )

    def _get_lock(self, name: str) -> threading.Lock:
        if name not in self._locks:
            with self._locks_guard:
                if name not in self._locks:
                    self._locks[name] = threading.Lock()
        return self._locks[name]

    # ── load / save (锁由调用方持有) ──────────────────────────

    def _load_unlocked(self, name: str) -> List[Dict[str, Any]]:
        if name in self._cache:
            return self._cache[name]
        directives: List[Dict[str, Any]] = []
        path = self._file_path(name)
        if os.path.exists(path):
            try:
                import json
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                items_raw = raw.get("directives") if isinstance(raw, dict) else None
                if isinstance(items_raw, list):
                    for r in items_raw:
                        norm = _normalize_entry(r)
                        if norm is not None:
                            directives.append(norm)
            except Exception as exc:  # 文件损坏不致命，重启从空开始
                logger.warning(
                    "[UserDirectives] load failed for %s, starting empty: %s",
                    name, exc,
                )
                directives = []
        self._cache[name] = directives
        return directives

    def _save_unlocked(self, name: str) -> None:
        # 这次落盘是同步的，跑在事件循环线程上，而且是**有意保留**的：
        #
        # 调用链是 `async def _process_stream_data_internal`
        #   → _publish_user_utterance_to_plugin_bus（同步）
        #   → dispatch_user_utterance（同步 fan-out）
        #   → _on_user_utterance → record_from_text → 这里。
        # `dispatch_user_utterance` 的契约就是同步 fan-out，链上还挂着第三方插件的
        # handler（plugin.core.state / quota.dropper）。把这一段改成 async 等于把所有
        # 插件 handler 一起挪到工作线程 —— 那是事件总线的架构取舍，不该由一条落盘
        # 顺带决定。
        #
        # 代价可接受：只有 directive 正则真命中时才写（用户说"别提 X"这种），
        # 一次会话个位数，payload 是几百字节的 JSON。
        #
        # 若要改，正确形状是 memory/anti_repeat.py 那对 stage/aflush 拆分：内存更新
        # 留在调用线程（否则同轮的读会看到旧 cache），只把 fsync 挪走，并在 turn
        # 生命周期里补一个 flush 点。
        #
        # noqa 现在其实没有对应的告警可压 —— scripts/check_async_blocking.py 只做
        # depth-1，看不到深度 6 的这里。留着是为了可 grep，以及守卫哪天加深时不用
        # 重新考古。
        path = self._file_path(name)
        payload = {
            "version": _SCHEMA_VERSION,
            "directives": self._cache.get(name, []),
        }
        try:
            atomic_write_json(path, payload, indent=2, ensure_ascii=False)  # noqa: ASYNC_BLOCK — 同步 fan-out 契约，见上
        except Exception as exc:
            logger.warning("[UserDirectives] save failed for %s: %s", name, exc)

    # ── public API ────────────────────────────────────────────

    def record(
        self,
        name: str,
        *,
        locale: str,
        kind: str,
        term: str,
        source: str = "regex",
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Register a directive; an existing ``(kind, term.casefold())`` hit refreshes it.

        Returns the final persisted dict (with merged/refreshed fields); invalid
        input (term not a str / empty after trim / length out of range) returns
        an empty dict.

        ⚠️ The write boundary also goes through ``_normalize_term`` — sharing
        the same length invariant as ``_normalize_entry``'s read validation, so
        the on-disk state always satisfies [_TERM_MIN_LEN, _TERM_MAX_LEN]
        (CodeRabbit Minor).
        """
        if not name:
            return {}
        term_norm = _normalize_term(term)
        if term_norm is None:
            return {}
        term = term_norm
        ts = float(now if now is not None else _now())
        key = (kind, term.casefold())
        with self._get_lock(name):
            entries = self._load_unlocked(name)
            for e in entries:
                if (e["kind"], e["term"].casefold()) == key:
                    hits = int(e.get("hit_count", 1)) + 1
                    e["last_seen_at"] = ts
                    # ⚠️ 续期长度用**递增之后**的次数：这一次重复本身就是
                    # "用户又说了一遍"的证据，该立刻算进本次续期，而不是等
                    # 下一次命中才生效。
                    e["expire_at"] = ts + _effective_ttl(hits)
                    e["hit_count"] = hits
                    # locale 不覆盖：首次命中的 locale 是更具诊断价值的信号
                    # ⚠️ 刷新分支也要 rotate。存量文件是在有 cap 之前长起来的
                    # （老用户可能已经几百行），而一个只会重复既有指令的用户
                    # 永远走不到新增分支——不在这里也 rotate 的话，那份文件
                    # 永远收不回 cap，而每次 record 都要全量读+全量写，还跑在
                    # 用户每条消息的同步链上。
                    self._rotate_unlocked(name, now=ts)
                    self._save_unlocked(name)
                    return self._report_if_kept(name, e)
            new_entry = {
                "term": term,
                "kind": kind,
                "locale": locale,
                "created_at": ts,
                "last_seen_at": ts,
                "expire_at": ts + _effective_ttl(1),
                "hit_count": 1,
                "source": source,
            }
            entries.append(new_entry)
            self._rotate_unlocked(name, now=ts)
            self._save_unlocked(name)
            return self._report_if_kept(name, new_entry)

    def _report_if_kept(
        self, name: str, entry: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return ``entry`` only if rotate kept it; ``{}`` when it was evicted.

        Caller holds the lock and has already rotated + persisted.
        """
        # ⚠️ rotate 有可能把**刚写的这条**挤掉，新增与刷新两条分支都会：稳定
        # 排序下并列 last_seen 的那组保持原序，而新条目 append 在末尾、切片
        # 正好从尾部切；刷新分支则是把 last_seen 改成了一个比在库条目都旧的
        # 时间。触发条件是"已有 cap 条活条目、且它们的 last_seen 都 >= ts"——
        # 系统时钟回拨（NTP 校正 / 休眠恢复 / 双系统时区，这是个 Windows 桌面
        # 应用）或同一毫秒并列都够。
        #
        # 无条件报成功的话，调用方（record_from_text → mark_pending_injection）
        # 会据此置待注入标记，去渲染一个盘上和内存里都不存在的 term ——
        # 用户重复说的这条 ban 在当前会话和下次会话都不会生效。
        #
        # ⚠️ 两条分支**共用**这一个 helper，别各写一份：刷新分支原先没有这道
        # 检查，正是因为它的 rotate 是后加的（修"存量超限收不回 cap"那条），
        # 而写的时候没回头看它的返回值 —— greptile P1 抓到。
        if any(item is entry for item in self._cache.get(name, ())):
            return dict(entry)
        # ⚠️ **不打印 term 原文**。被 ban 的话题按定义就是用户明说不想再听的东西
        # （前任 / 病名 / 逝者姓名），而这条 logger 是落盘的持久化日志。同一条判据
        # 在 proactive 那道闸上已经立过一次（那里把 terms=%s 改成了条数）——写这个
        # helper 时又把 term 塞了回来，greptile P1 抓到。定位靠角色名 + 这条消息
        # 本身就够，不需要知道是哪个 term。
        logger.warning(
            "[UserDirectives] %s: a directive was rotated out immediately "
            "(store at cap with newer timestamps); not reporting it as recorded",
            name,
        )
        return {}

    def _rotate_unlocked(self, name: str, *, now: float) -> int:
        """Cap the stored list at ``USER_DIRECTIVE_MAX_STORED``, oldest first.

        Returns the number of entries dropped. Caller holds the lock.
        """
        entries = self._cache.get(name)
        if not entries or len(entries) <= USER_DIRECTIVE_MAX_STORED:
            return 0
        # ⚠️ 先按过期清一轮再按 last_seen 淘汰。过期条目本来就已经对
        # ``get_active`` 不可见，让它们占着名额去挤掉一条**活的**指令，等于
        # 用死数据换掉用户真说过的话。
        alive = [e for e in entries if float(e.get("expire_at", 0)) > now]
        expired_dropped = len(entries) - len(alive)
        if len(alive) > USER_DIRECTIVE_MAX_STORED:
            # last_seen_at 降序取前 N —— 与 ``get_active`` 的排序同一个键，
            # 一个取最新 N 条注入，一个丢最旧的。最久没被提起的先走。
            alive.sort(key=lambda e: float(e.get("last_seen_at", 0)), reverse=True)
            alive = alive[:USER_DIRECTIVE_MAX_STORED]
        dropped = len(entries) - len(alive)
        self._cache[name] = alive
        if dropped > expired_dropped:
            logger.info(
                "[UserDirectives] %s: rotated out %d directive(s) over the "
                "%d cap (%d of them already expired)",
                name, dropped, USER_DIRECTIVE_MAX_STORED, expired_dropped,
            )
        return dropped

    def record_from_text(
        self,
        name: str,
        text: str,
        *,
        now: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Run the full extract → store pipeline over a user text.

        Returns the list of entries written/refreshed this time (empty = no pattern hit).
        """
        if not name or not text:
            return []
        hits = extract_directives(text)
        if not hits:
            return []
        ts = float(now if now is not None else _now())
        out: List[Dict[str, Any]] = []
        for locale, kind, term in hits:
            out.append(
                self.record(
                    name,
                    locale=locale,
                    kind=kind,
                    term=term,
                    source="regex",
                    now=ts,
                )
            )
        # ``record`` 对越界 term 返回 {}；全被丢掉时不该置待注入标记。
        if any(out):
            self.mark_pending_injection(name)
        return out

    # ── 会话中途注入的待办标记 ────────────────────────────────────

    def mark_pending_injection(self, name: str) -> None:
        """Flag that ``name`` has fresh directives not yet pushed into a live session."""
        if not name:
            return
        with self._pending_guard:
            self._pending_injection.add(name)

    def take_pending_injection(self, name: str) -> bool:
        """Consume the pending flag for ``name``; True when there was one.

        Take-once: the flag is cleared even if the caller then fails to inject.
        A dropped injection costs at most the pre-existing behaviour (the block
        still lands at the next session rebuild), whereas a flag that survives
        a failure would re-inject the same block on every later turn.
        """
        if not name:
            return False
        with self._pending_guard:
            if name not in self._pending_injection:
                return False
            self._pending_injection.discard(name)
            return True

    def get_active(
        self,
        name: str,
        *,
        now: Optional[float] = None,
        limit: int = USER_DIRECTIVE_MAX_ACTIVE,
    ) -> List[Dict[str, Any]]:
        """Return up to ``limit`` unexpired records, sorted by last_seen_at descending."""
        if not name:
            return []
        ts = float(now if now is not None else _now())
        with self._get_lock(name):
            entries = self._load_unlocked(name)
            alive = [dict(e) for e in entries if float(e.get("expire_at", 0)) > ts]
        alive.sort(key=lambda e: float(e.get("last_seen_at", 0)), reverse=True)
        if limit and limit > 0:
            alive = alive[:limit]
        return alive

    def purge_expired(self, name: str, *, now: Optional[float] = None) -> int:
        """Lazy cleanup: delete expired entries and persist; returns the number deleted."""
        if not name:
            return 0
        ts = float(now if now is not None else _now())
        with self._get_lock(name):
            entries = self._load_unlocked(name)
            before = len(entries)
            kept = [e for e in entries if float(e.get("expire_at", 0)) > ts]
            removed = before - len(kept)
            if removed:
                self._cache[name] = kept
                self._save_unlocked(name)
            return removed

    def get_active_terms(
        self,
        name: str,
        *,
        now: Optional[float] = None,
    ) -> List[str]:
        """Active terms only, newest-first — the shape output-side filters want."""
        return [e["term"] for e in self.get_active(name, now=now) if e.get("term")]

    def render_prompt_block(
        self,
        name: str,
        lang: str,
        *,
        now: Optional[float] = None,
    ) -> str:
        """Render active terms into a system-prompt fragment. Returns "" when empty."""
        terms = self.get_active_terms(name, now=now)
        if not terms:
            return ""
        return render_directives_block(terms, lang)

    def clear(self, name: str) -> None:
        """Entry point for tests / manual user clearing."""
        if not name:
            return
        with self._get_lock(name):
            self._cache[name] = []
            self._save_unlocked(name)
        # 清空之后没有任何可注入内容，待办标记也要一起撤，否则下一轮会白跑
        # 一次注入（render 出空串），并把一次 take 的语义浪费掉。
        with self._pending_guard:
            self._pending_injection.discard(name)


# ── 进程级单例 + 自注册 ──────────────────────────────────────
_GLOBAL_MANAGER: Optional[UserDirectivesManager] = None
_GLOBAL_MANAGER_LOCK = threading.Lock()


def get_user_directives_manager() -> UserDirectivesManager:
    global _GLOBAL_MANAGER
    if _GLOBAL_MANAGER is None:
        with _GLOBAL_MANAGER_LOCK:
            if _GLOBAL_MANAGER is None:
                _GLOBAL_MANAGER = UserDirectivesManager()
    return _GLOBAL_MANAGER


def _on_user_utterance(bucket: str, event: Dict[str, Any]) -> None:
    """user_utterance sink: extract and persist. Errors are swallowed (main_logic
    already does a per-sink try/except inside dispatch; this is one more layer
    of defense).

    Dedup rule: a single ``dispatch_user_utterance`` dispatch fans out to both
    the ``"default"`` bucket and the character-name bucket (see the
    ``dict.fromkeys(("default", self.lanlan_name))`` loop in
    ``main_logic/core.py``). Rules:
      - event["lanlan"] non-empty and not "default" → a real character; the
        "default" bucket counts as the duplicate, skip; only store when
        bucket == event["lanlan"]
      - event["lanlan"] empty / "default" → the dispatch only sent the
        "default" copy (character unconfigured / character literally named
        "default"); bucket=="default" goes through normal processing, so the
        whole message isn't missed (codex P1)
    """
    if not isinstance(event, dict):
        return
    canonical = event.get("lanlan")
    if not isinstance(canonical, str):
        canonical = ""
    if canonical and canonical != "default":
        # 真角色：跳过 default 的重复分发，只处理角色 bucket
        if bucket != canonical:
            return
        record_key = canonical
    else:
        # 无 character 或 character literal == "default"：dispatch 只发了
        # "default"，必须处理这一份
        if not bucket:
            return
        record_key = bucket  # 当 lanlan_name 为空时只能落到 bucket（即 "default"）
    text = ""
    raw = event.get("content")
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, list):
        # multimodal content list：拼 text 片段
        parts: List[str] = []
        for p in raw:
            if isinstance(p, dict):
                t = p.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(p, str):
                parts.append(p)
        text = " ".join(parts)
    if not text or not text.strip():
        return
    try:
        get_user_directives_manager().record_from_text(record_key, text)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[UserDirectives] sink failed: %s", exc)


# 注意：sink 不能在这里 self-register。``memory`` 层在 ``main_logic`` 之下
# （scripts/check_module_layering.py），向上 import ``main_logic.agent_event_bus``
# 会触发 LAYER_CYCLE。所以本模块只导出 ``_on_user_utterance``；真正把它接到
# event bus 的工作放到 ``app/runtime_bindings.py``（L6 app 层有权碰 L4
# main_logic + L3 memory，是合法的接线点）。
#
# 副作用：直接 import 本模块的测试 / 临时脚本不会自动起 sink；测试通过
# ``_on_user_utterance(bucket, event)`` 手动驱动验证抽取+落盘合同（见
# ``tests/unit/test_user_directives.py::test_user_utterance_sink_records``）；
# 集成路径靠 ``app.__init__`` → ``install_runtime_bindings`` 完成挂载。
