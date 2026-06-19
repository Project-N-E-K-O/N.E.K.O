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
model steps on the landmine again. So extracted terms are persisted for 3 days
(``USER_DIRECTIVE_TTL_SECONDS``), and ``_build_initial_prompt`` splices a
block into the system prompt tail at startup.

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
- **TTL**: each record's ``expire_at = last_seen_at + USER_DIRECTIVE_TTL_SECONDS``.
  Filtered on read; ``purge_expired`` rewrites the file (optional; lazy is fine).
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

from config import USER_DIRECTIVE_MAX_ACTIVE, USER_DIRECTIVE_TTL_SECONDS
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
        # 历史文件可能没写 expire_at；按 last_seen + TTL 补
        try:
            expire_at = float(raw.get("expire_at") or 0) or (
                last_seen_at + USER_DIRECTIVE_TTL_SECONDS
            )
        except (TypeError, ValueError):
            expire_at = last_seen_at + USER_DIRECTIVE_TTL_SECONDS
        try:
            hit_count = int(raw.get("hit_count") or 1)
        except (TypeError, ValueError):
            hit_count = 1
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
        path = self._file_path(name)
        payload = {
            "version": _SCHEMA_VERSION,
            "directives": self._cache.get(name, []),
        }
        try:
            atomic_write_json(path, payload, indent=2, ensure_ascii=False)
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
        expire = ts + USER_DIRECTIVE_TTL_SECONDS
        key = (kind, term.casefold())
        with self._get_lock(name):
            entries = self._load_unlocked(name)
            for e in entries:
                if (e["kind"], e["term"].casefold()) == key:
                    e["last_seen_at"] = ts
                    e["expire_at"] = expire
                    e["hit_count"] = int(e.get("hit_count", 1)) + 1
                    # locale 不覆盖：首次命中的 locale 是更具诊断价值的信号
                    self._save_unlocked(name)
                    return dict(e)
            new_entry = {
                "term": term,
                "kind": kind,
                "locale": locale,
                "created_at": ts,
                "last_seen_at": ts,
                "expire_at": expire,
                "hit_count": 1,
                "source": source,
            }
            entries.append(new_entry)
            self._save_unlocked(name)
            return dict(new_entry)

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
        return out

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

    def render_prompt_block(
        self,
        name: str,
        lang: str,
        *,
        now: Optional[float] = None,
    ) -> str:
        """Render active terms into a system-prompt fragment. Returns "" when empty."""
        active = self.get_active(name, now=now)
        if not active:
            return ""
        terms = [e["term"] for e in active]
        return render_directives_block(terms, lang)

    def clear(self, name: str) -> None:
        """Entry point for tests / manual user clearing."""
        if not name:
            return
        with self._get_lock(name):
            self._cache[name] = []
            self._save_unlocked(name)


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
